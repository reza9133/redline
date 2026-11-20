#!/usr/bin/env node
/**
 * Redline live end-to-end test against GenLayer Studionet.
 *
 *   npm run e2e                    # deploy a fresh contract, run everything (~15-25 min)
 *   npm run e2e:quick              # a smaller pass (~5 min)
 *   node e2e/live-e2e.mjs --strict # model observations that differ from expectations also fail the run
 *
 *   REDLINE_CONTRACT_ADDRESS=0x...  reuse a deployment instead of deploying
 *   REDLINE_PRIVATE_KEY / REDLINE_PRIVATE_KEY_2   fixed keys for the two actors (random otherwise)
 *   E2E_FIXTURE_BASE=...            where e2e/fixtures is hosted (see .env.example)
 *
 * Prerequisite: push e2e/fixtures to a PUBLIC repository first; the validators fetch the
 * fixtures from the internet like any other page.
 *
 * Step kinds
 *   HARD   deterministic contract behaviour. It must hold whatever the validators' models
 *          answer. A failure fails the run (exit 1).
 *   MODEL  depends on what real LLM validators decide, or on how the web renderer presents a
 *          page. The observation is recorded as PASS or WARN; WARN only fails the run with --strict.
 *
 * Actors: alice owns every document and sends most transactions; bob is "somebody else"
 * (a watcher): he may append revisions on the same site, appeal, and is refused everything else.
 *
 * Documents: D0 (cooldown / identical / format-only), D1 (the main amendment chain: original -> adverse -> revert -> cosmetic),
 * D2 (prompt-injection attempt), D3 (site and rewrite guards).
 *
 * Expected duration is dominated by the 60 s revision cooldown and consensus rounds; the script
 * interleaves work so that cooldowns elapse while other documents are being handled.
 */
import "./throttle.mjs"; // must stay first: wraps fetch before genlayer-js / viem load
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { keccak256, toHex } from "viem";
import {
  CONTRACT_PATH, createAccount, deployRedline, errorText, EXPLORER, fund, generatePrivateKey, makeClient, normalize, receiptFailed,
  resolveFixtureBase, ROOT, sleep, taggedMessage, TransactionStatus,
} from "./lib.mjs";

const args = new Set(process.argv.slice(2));
const QUICK = args.has("--quick");
const STRICT = args.has("--strict");
const RUN_ID = Date.now().toString(36);
const REPORT_DIR = resolve(ROOT, process.env.E2E_REPORT_DIR || "reports");
// ~7.5 minutes per transaction at the default 5 s poll. A transaction that never reaches ACCEPTED (validators that
// cannot agree end UNDETERMINED) fails its own step instead of stalling the whole run for an hour.
const RECEIPT_RETRIES = Number(process.env.E2E_RECEIPT_RETRIES) || 90;
const NETWORK = process.env.E2E_NETWORK_LABEL || "GenLayer Studionet";
const FIXTURE_NAMES = ["terms_v1", "terms_v1_caps", "terms_v2_cosmetic", "terms_v3_adverse", "terms_v5_injection", "terms_plain", "terms_plain_reworded", "unrelated", "tiny"];
const SCALE = ["MUCH_BETTER", "BETTER", "NEUTRAL", "WORSE", "MUCH_WORSE"];

let FIXTURE_BASE = "";
const fixtureUrl = (name, tag) => `${FIXTURE_BASE}/${name}.txt?run=${RUN_ID}&doc=${tag}`;
const localFixture = (name) => readFileSync(resolve(ROOT, "e2e", "fixtures", `${name}.txt`), "utf8");
const collapse = (s) => s.split(/\s+/).filter(Boolean).join(" ");
const keccak = (s) => keccak256(toHex(s)).slice(2); // matches the contract's Keccak256().hexdigest()
const lower = (s) => String(s).toLowerCase();

// ---------------------------------------------------------------- recorder
const steps = [];
const txs = [];
class Observation extends Error {} // a MODEL expectation that did not hold
class Inconclusive extends Error {} // the environment (not the contract) prevented a verdict
class Skipped extends Error {}

async function step(id, title, kind, fn) {
  const started = Date.now();
  const rec = { id, title, kind, status: "PASS", detail: "", ms: 0 };
  process.stdout.write(`[${id}] ${title} ... `);
  try {
    const detail = await fn();
    rec.detail = typeof detail === "string" ? detail : "";
  } catch (err) {
    if (err instanceof Skipped) rec.status = "SKIP";
    else if (err instanceof Inconclusive) rec.status = "WARN";
    else if (err instanceof Observation) rec.status = kind === "MODEL" ? "WARN" : "FAIL";
    else rec.status = "FAIL";
    rec.detail = `${err?.message ?? err}`.slice(0, 700);
  }
  rec.ms = Date.now() - started;
  steps.push(rec);
  console.log(`${rec.status}${rec.detail ? ` (${rec.detail.slice(0, 160)})` : ""} [${(rec.ms / 1000).toFixed(1)}s]`);
  return rec.status;
}

const expect = (cond, message) => {
  if (!cond) throw new Error(message);
};
const observe = (cond, message) => {
  if (!cond) throw new Observation(message);
};
const need = (value, why) => {
  if (value === undefined || value === null || value === false) throw new Skipped(why);
  return value;
};

// ---------------------------------------------------------------- chain I/O
let address;
let alice, bob, aliceClient, bobClient, reader;
let limits;

const TRANSIENT = /\b50[234]\b|<!DOCTYPE|Unexpected token '<'|ECONNRESET|ETIMEDOUT|fetch failed|timed? ?out/i;
const read = async (fn, a = []) => {
  for (let i = 0; ; i++) {
    try {
      return normalize(await reader.readContract({ address, functionName: fn, args: a }));
    } catch (err) {
      if (i >= 4 || !TRANSIENT.test(errorText(err))) throw err;
      await sleep(1500 * (i + 1)); // public RPC hiccup (502 / HTML error page): back off and retry
    }
  }
};

async function write(client, fn, a, label) {
  const hash = await client.writeContract({ address, functionName: fn, args: a, value: 0n });
  const t0 = Date.now();
  const receipt = await client.waitForTransactionReceipt({ hash, status: TransactionStatus.ACCEPTED, interval: 5000, retries: RECEIPT_RETRIES });
  txs.push({ label: label ?? fn, hash, explorer: `${EXPLORER}/tx/${hash}`, seconds: Math.round((Date.now() - t0) / 1000), failed: receiptFailed(receipt) });
  if (receiptFailed(receipt)) throw new Error(`${fn} failed on-chain: ${taggedMessage(receipt) ?? "no reason in the receipt (see the explorer)"}`);
  return { hash, receipt };
}

const GENERIC_REVERT = /execution failed/i;
const controls = new Map();

/**
 * Asserts that `fn(...a)` is rejected by the contract.
 *
 * Studionet's gen_call (simulation) reports a contract revert only as a generic "execution failed";
 * the contract's own "[EXPECTED] ..." text usually does not reach the client. So:
 *  - if the node does expose a reason, it must contain `needle`; a different reason fails the step;
 *  - a generic "execution failed" counts as a rejection ONLY IF a `control` call (the same method with
 *    valid input) simulates successfully, so a typo'd argument or a broken calldata cannot pass as a rejection;
 *    any other error (502, HTML, timeouts) is a transport problem and fails;
 *  - rules that depend on the transaction timestamp or on consensus (cooldown, identical page, rewrite,
 *    appeal state) use { onchain: true }: one real transaction is sent, and the rejection is proven by the
 *    receipt's reason when it has one AND by state (`probe()` must be unchanged).
 * The exact reason of every rule is asserted in tests/contract; the live run confirms the DEPLOYED contract
 * refuses the input.
 */
async function expectRevert(client, fn, a, needle, { control, onchain = false, probe } = {}) {
  const wants = (m) => lower(m).includes(lower(needle));
  if (!onchain) {
    try {
      await client.simulateWriteContract({ address, functionName: fn, args: a });
    } catch (err) {
      const msg = errorText(err);
      if (wants(msg)) return `reason seen: "${needle}"`;
      const tagged = taggedMessage(msg);
      expect(!tagged, `reverted for another reason: "${tagged}" (wanted one containing "${needle}")`);
      expect(GENERIC_REVERT.test(msg), `simulation failed for a reason that is not a contract revert: ${msg.slice(0, 200)}`);
      if (control) {
        const key = `${fn}:${JSON.stringify(control)}`;
        if (!controls.has(key)) {
          let last = "";
          for (let i = 0; i < 4; i++) {
            try {
              await client.simulateWriteContract({ address, functionName: fn, args: control });
              last = null;
              break;
            } catch (e) {
              last = errorText(e).slice(0, 200);
              if (!/internal error|STATUS_NOT_OK/i.test(last)) break; // only a node hiccup is worth retrying
              await sleep(3000);
            }
          }
          controls.set(key, last);
        }
        expect(controls.get(key) === null, `control call (same method, valid input) also failed: ${controls.get(key)}`);
      }
      return control ? "rejected (control call passes; node hides the reason)" : "rejected (node hides the reason)";
    }
    throw new Error(`expected a revert containing "${needle}", but the simulation succeeded`);
  }

  const snapshot = probe ?? (async () => JSON.stringify(await read("get_stats")));
  const before = await snapshot();
  const wantsCooldown = /cooldown/i.test(needle);
  for (let attempt = 0; ; attempt++) {
    const hash = await client.writeContract({ address, functionName: fn, args: a, value: 0n });
    const t0 = Date.now();
    const receipt = await client.waitForTransactionReceipt({ hash, status: TransactionStatus.ACCEPTED, interval: 5000, retries: RECEIPT_RETRIES });
    txs.push({ label: `expected rejection: ${fn}`, hash, explorer: `${EXPLORER}/tx/${hash}`, seconds: Math.round((Date.now() - t0) / 1000), failed: receiptFailed(receipt) });
    const reason = taggedMessage(receipt);
    // The rule under test is evaluated AFTER the cooldown. If the cooldown answered first, the chain clock is behind ours
    // (our wait was too short): the rejection proves nothing about the rule, so wait and try again instead of failing.
    if (reason && !wantsCooldown && /cooldown/i.test(reason) && !wants(reason)) {
      if (attempt >= 2) throw new Inconclusive(`the revision cooldown still answered first after ${attempt + 1} attempts: the chain clock and this machine's clock disagree`);
      expect((await snapshot()) === before, "state changed during a cooldown rejection");
      process.stdout.write(`\n    (cooldown answered before the rule under test; the chain clock is behind ours, retrying in 30s) `);
      await sleep(30_000);
      continue;
    }
    if (reason) expect(wants(reason), `rejected on-chain, but with "${reason.slice(0, 200)}" instead of one containing "${needle}"`);
    expect((await snapshot()) === before, `the transaction changed state although "${needle}" should have rejected it (${hash})`);
    return `on-chain: state unchanged; ${reason ? `reason "${reason.slice(0, 70)}"` : `receipt gives no reason (result ${receipt?.txExecutionResultName ?? "?"})`}`;
  }
}

// ---------------------------------------------------------------- domain helpers
/** The same function as the contract's _severity(): lets us verify the DEPLOYED policy for ANY model output. */
function policy(effect, topics, churn, rewrite) {
  const heavy = topics.length > 0 || churn >= limits.heavy_churn_bp;
  let severity;
  let silent = false;
  if (effect === "MUCH_WORSE") severity = "HIGH";
  else if (effect === "WORSE") severity = heavy ? "HIGH" : "MEDIUM";
  else if (effect === "MUCH_BETTER") severity = "MEDIUM";
  else if (effect === "BETTER") severity = heavy ? "MEDIUM" : "LOW";
  else if (effect === "NEUTRAL") {
    silent = heavy;
    severity = silent ? "MEDIUM" : "LOW";
  } else severity = heavy ? "MEDIUM" : "LOW";
  if (rewrite && severity === "LOW") severity = "MEDIUM";
  return { severity, silent };
}

const topicsOf = (rev) => (rev.topics ? rev.topics.split(",") : []);

async function revisionsOf(docId) {
  const out = [];
  for (let offset = 0; ; offset += 50) {
    const page = await read("list_revisions", [docId, offset, 50]);
    out.push(...page);
    if (page.length < 50) return out;
  }
}

async function allDocuments() {
  const out = [];
  for (let offset = 0; ; offset += 50) {
    const page = await read("list_documents", [offset, 50]);
    out.push(...page);
    if (page.length < 50) return out;
  }
}

/** Facts that hold for every recorded revision, whatever the model said. */
function checkRules(rev, redline, text, prevText) {
  const where = `revision #${rev.id}`;
  const exp = policy(rev.effect, topicsOf(rev), rev.churn_bp, rev.rewrite);
  expect(rev.severity === exp.severity, `${where}: severity ${rev.severity}, the policy says ${exp.severity} for ${rev.effect}/${rev.topics || "no topics"}/churn ${rev.churn_bp}`);
  expect(rev.silent_flag === exp.silent, `${where}: silent_flag ${rev.silent_flag}, the policy says ${exp.silent}`);
  if (rev.judged) {
    expect(SCALE.includes(rev.raw_effect), `${where}: raw_effect "${rev.raw_effect}" is not on the scale`);
    expect(rev.effect === rev.raw_effect || rev.effect === "UNCLEAR", `${where}: effect ${rev.effect} differs from raw ${rev.raw_effect} without being UNCLEAR`);
    const units = [...redline.added, ...redline.removed].map(lower);
    if (rev.effect !== "NEUTRAL" && rev.effect !== "UNCLEAR") {
      expect(rev.quote.length >= limits.min_quote_chars, `${where}: ${rev.effect} without a usable quote`);
      expect(units.some((u) => u.includes(lower(rev.quote))), `${where}: quote "${rev.quote.slice(0, 60)}" is not a piece of a changed sentence`);
    }
    if (rev.effect === "UNCLEAR") expect(rev.quote === "", `${where}: UNCLEAR must carry no quote`);
  } else {
    expect(rev.effect === "NEUTRAL" && rev.raw_effect === "" && rev.quote === "", `${where}: a revision without a model verdict must be a plain NEUTRAL`);
  }
  for (const u of redline.added) expect(lower(text).includes(lower(u)), `${where}: added sentence not found in the new text: "${u.slice(0, 50)}"`);
  for (const u of redline.removed) expect(lower(prevText).includes(lower(u)), `${where}: removed sentence not found in the previous text: "${u.slice(0, 50)}"`);
  expect(rev.retained_bp >= 0 && rev.retained_bp <= 10000 && rev.churn_bp >= 0 && rev.churn_bp <= 10000, `${where}: ratios out of range`);
}

// A revision's text and redline never change after it is recorded (an appeal only edits verdict fields),
// so they are read once. This matters: the public RPC allows only ~30 requests a minute.
const textCache = new Map();
const redlineCache = new Map();
const textOf = async (id) => (textCache.has(id) ? textCache.get(id) : (textCache.set(id, await read("get_revision_text", [id])), textCache.get(id)));
const redlineOf = async (id) => (redlineCache.has(id) ? redlineCache.get(id) : (redlineCache.set(id, JSON.parse(await read("get_redline", [id]))), redlineCache.get(id)));

/** Recomputes the whole hash chain of a document from the stored texts. */
async function verifyChain(docId) {
  const doc = await read("get_document", [docId]);
  const revs = await revisionsOf(docId);
  let head = "";
  const texts = [];
  const redlines = [];
  for (const [i, r] of revs.entries()) {
    expect(r.seq === i, `revision ids out of order in document ${docId}`);
    const text = await textOf(r.id);
    expect(keccak(text) === r.text_hash, `revision #${r.id}: stored text does not hash to text_hash`);
    expect(text.length === r.text_len, `revision #${r.id}: text_len ${r.text_len} != ${text.length}`);
    head = keccak(head + ":" + r.text_hash);
    expect(head === r.chain_hash, `revision #${r.id}: chain_hash does not recompute`);
    texts.push(text);
    redlines.push(await redlineOf(r.id));
  }
  const last = revs[revs.length - 1];
  expect(doc.chain_head === head && doc.latest_rev_id === last.id && doc.latest_hash === last.text_hash, "document head does not match its last revision");
  expect(doc.revision_count === revs.length, `revision_count ${doc.revision_count} != ${revs.length}`);
  return { doc, revs, texts, redlines };
}

async function waitForCooldown(docId) {
  const doc = await read("get_document", [docId]);
  const readyAt = (doc.last_revision_at + limits.min_append_interval_seconds + 6) * 1000;
  let ms = readyAt - Date.now();
  if (ms > 180_000) ms = 90_000; // chain clock far ahead of this machine: do not wait forever, the retry below covers the rest
  if (ms > 0) {
    process.stdout.write(`\n    (waiting ${Math.ceil(ms / 1000)}s for the revision cooldown of document #${docId}) `);
    await sleep(ms);
  }
}

/**
 * Appends a revision, waiting out the cooldown first.
 * If the transaction is rejected on-chain while the revision count did not move and the reason is not one of the
 * contract's other rules, the most likely cause is a chain clock that runs behind ours (the cooldown has not elapsed
 * there yet): wait and try again. A timeout is never retried (the first transaction might still be accepted later,
 * and a retry would then record a duplicate).
 */
async function appendRevision(client, docId, url, label) {
  const otherRule = /identical|different site|full rewrite|maximum number|public https|not found/i;
  let lastError;
  for (let attempt = 0; attempt < 3; attempt++) {
    await waitForCooldown(docId);
    const countBefore = (await read("get_document", [docId])).revision_count;
    try {
      await write(client, "append_revision", [docId, url], label);
      return (await read("get_document", [docId])).latest_rev_id;
    } catch (err) {
      lastError = err;
      const message = String(err?.message);
      if (!/failed on-chain/i.test(message) || otherRule.test(message)) throw err;
      if ((await read("get_document", [docId])).revision_count !== countBefore) throw err;
      process.stdout.write(`\n    (rejected on-chain without a clear reason; assuming the cooldown has not elapsed on the chain clock, retrying in 30s) `);
      await sleep(30_000);
    }
  }
  throw lastError;
}

async function registerDoc(client, name, fixture, tag, { publisher = "Northwind Cloud (e2e)", kind = "TERMS" } = {}) {
  const url = fixtureUrl(fixture, tag);
  await write(client, "register_document", [`${name} [${RUN_ID}]`, publisher, kind, url], `register: ${name}`);
  const id = await read("find_document", [url]);
  expect(id > 0, "find_document did not return the new document");
  created.push(id);
  return id;
}

const okRegisterArgs = (over = {}) => {
  const a = { name: `Rejected document [${RUN_ID}]`, publisher: "Northwind", kind: "TERMS", url: fixtureUrl("terms_v1", "control"), ...over };
  return [a.name, a.publisher, a.kind, a.url];
};

// ---------------------------------------------------------------- scenario
const D = {}; // document ids: D.d0 ... D.d3
const S = {}; // other state shared between steps
const created = []; // ids of the documents this run registered
let before; // stats at the start of the run

async function main() {
  console.log(`Redline live E2E  run=${RUN_ID}  network=${NETWORK}${QUICK ? "  (quick)" : ""}${STRICT ? "  (strict)" : ""}\n`);

  await step("S01", "Accounts created and funded", "HARD", async () => {
    alice = createAccount(process.env.REDLINE_PRIVATE_KEY || generatePrivateKey());
    bob = createAccount(process.env.REDLINE_PRIVATE_KEY_2 || generatePrivateKey());
    aliceClient = makeClient(alice);
    bobClient = makeClient(bob);
    reader = makeClient(undefined);
    const fa = await fund(aliceClient, alice.address);
    const fb = await fund(bobClient, bob.address);
    expect(fa === true && fb === true, `sim_fundAccount failed: ${fa === true ? "" : fa} ${fb === true ? "" : fb}`);
    return `alice ${alice.address}, bob ${bob.address}`;
  });
  if (steps.at(-1).status === "FAIL") return;

  await step("S02", "Fixtures are reachable and match the local files", "HARD", async () => {
    FIXTURE_BASE = resolveFixtureBase();
    for (const name of FIXTURE_NAMES) {
      const url = `${FIXTURE_BASE}/${name}.txt`;
      const res = await fetch(url);
      expect(res.ok, `${url} -> HTTP ${res.status}. Push e2e/fixtures to that branch of a public repository first, or set E2E_FIXTURE_BASE.`);
      expect(collapse(await res.text()) === collapse(localFixture(name)), `${name}.txt on the server differs from the local copy; push your latest e2e/fixtures.`);
    }
    return `base ${FIXTURE_BASE}`;
  });
  if (steps.at(-1).status === "FAIL") return;

  await step("S03", "Contract deployed (or an existing one reused)", "HARD", async () => {
    if (process.env.REDLINE_CONTRACT_ADDRESS) {
      address = process.env.REDLINE_CONTRACT_ADDRESS;
      return `reusing ${address}`;
    }
    const d = await deployRedline(aliceClient, (m) => process.stdout.write(`\n    ${m}`));
    address = d.address;
    txs.push({ label: "deploy", hash: d.hash, explorer: `${EXPLORER}/tx/${d.hash}`, seconds: 0, failed: false });
    return `deployed ${address}`;
  });
  if (!address) return;

  await step("S04", "get_version is 1.x and get_limits matches the source constants", "HARD", async () => {
    const v = await read("get_version");
    expect(String(v).startsWith("1."), `version ${v}`);
    limits = await read("get_limits");
    const src = readFileSync(CONTRACT_PATH, "utf8");
    const pairs = [
      ["max_name_chars", "MAX_NAME_CHARS"], ["min_name_chars", "MIN_NAME_CHARS"], ["max_publisher_chars", "MAX_PUBLISHER_CHARS"],
      ["min_text_chars", "MIN_TEXT_CHARS"], ["max_text_chars", "MAX_TEXT_CHARS"], ["max_docs_per_owner", "MAX_DOCS_PER_OWNER"],
      ["max_revisions_per_doc", "MAX_REVISIONS_PER_DOC"], ["min_append_interval_seconds", "MIN_APPEND_INTERVAL_SECONDS"],
      ["min_retained_bp", "MIN_RETAINED_BP"], ["heavy_churn_bp", "HEAVY_CHURN_BP"], ["min_quote_chars", "MIN_QUOTE_CHARS"],
      ["max_quote_chars", "MAX_QUOTE_CHARS"], ["prompt_max_units", "PROMPT_MAX_UNITS"], ["max_page_size", "MAX_PAGE_SIZE"],
    ];
    for (const [key, constant] of pairs) {
      const m = src.match(new RegExp(`^${constant}\\s*=\\s*(\\d+)`, "m"));
      expect(m && Number(m[1]) === limits[key], `${key}: chain ${limits[key]} vs source ${m?.[1]}`);
    }
    return `version ${v}`;
  });
  if (!limits) return;

  before = await read("get_stats").catch(() => null);

  // ---- 1. rule enforcement on registration
  // Pure validation is simulated, with a control call. A URL that is private AND unfetchable would be refused
  // by the fetch alone even if the contract's own URL rule were missing, so the rows marked "fetchable" use a
  // URL the validators could really read (the fixture host with userinfo / over http): only the rule can refuse them.
  const fixtureHost = () => new URL(fixtureUrl("terms_v1", "x"));
  const rejections = [
    ["private IPv4 literal", () => ({ url: "https://10.0.0.5/x" }), "public https"],
    ["cloud metadata address (on-chain: the reason is visible in the receipt)", () => ({ url: "https://169.254.169.254/latest/meta-data" }), "public https", true],
    ["carrier-grade NAT", () => ({ url: "https://100.64.0.1/x" }), "public https"],
    ["hex-spelled loopback", () => ({ url: "https://0x7f.0.0.1/x" }), "public https"],
    ["non-443 port", () => ({ url: "https://example.com:8443/x" }), "public https"],
    ["userinfo in a fetchable URL", () => { const u = fixtureHost(); u.username = "anyone"; return { url: u.toString() }; }, "public https"],
    ["plain http on a fetchable host", () => ({ url: fixtureUrl("terms_v1", "http").replace(/^https:/, "http:") }), "public https"],
    ["localhost", () => ({ url: "https://localhost/x" }), "public https"],
    ["name too short", () => ({ name: "ab" }), "name must be at least 3"],
    ["unknown kind", () => ({ kind: "CONTRACT" }), "kind must be one of"],
    ["blank publisher", () => ({ publisher: "   " }), "publisher must name"],
    ["prompt delimiters in the name", () => ({ name: "Bad <<< name" }), "<<<"],
  ];
  const quickRows = new Set(["private IPv4 literal", "name too short", "unknown kind", "userinfo in a fetchable URL"]);
  for (const [i, [title, over, needle, onchain]] of rejections.entries()) {
    if (QUICK && !quickRows.has(title)) continue;
    await step(`R${String(i + 1).padStart(2, "0")}`, `Rejected: ${title}`, "HARD", () =>
      expectRevert(aliceClient, "register_document", okRegisterArgs(over()), needle, { control: okRegisterArgs(), onchain: Boolean(onchain) }));
  }

  // ---- 2. D0: register, then try to append immediately (cooldown)
  await step("H01", "D0 registered: baseline revision exists", "HARD", async () => {
    D.d0 = await registerDoc(aliceClient, "D0 cooldown and format-only", "terms_v1", "d0");
    const doc = await read("get_document", [D.d0]);
    S.d0Created = doc.created_at;
    expect(doc.revision_count === 1 && doc.owner === lower(alice.address), "unexpected document state");
    return `document #${D.d0}`;
  });

  await step("C01", "Immediate second revision is blocked by the cooldown", "HARD", async () => {
    need(D.d0, "D0 was not registered");
    const countBefore = (await read("get_document", [D.d0])).revision_count;
    const hash = await aliceClient.writeContract({ address, functionName: "append_revision", args: [D.d0, fixtureUrl("terms_v1_caps", "d0")], value: 0n });
    const receipt = await aliceClient.waitForTransactionReceipt({ hash, status: TransactionStatus.ACCEPTED, interval: 5000, retries: RECEIPT_RETRIES });
    txs.push({ label: "expected rejection: append_revision (cooldown)", hash, explorer: `${EXPLORER}/tx/${hash}`, seconds: 0, failed: receiptFailed(receipt) });
    const doc = await read("get_document", [D.d0]);
    if (doc.revision_count === countBefore + 1) {
      const gap = doc.last_revision_at - S.d0Created;
      if (gap >= limits.min_append_interval_seconds) {
        S.d0FormatDone = true; // the network was slower than the cooldown, so this append was legitimately accepted
        throw new Inconclusive(`the chain took ${gap}s between the two transactions (cooldown ${limits.min_append_interval_seconds}s): not a contract fault`);
      }
      throw new Error(`accepted only ${gap}s after the baseline although the cooldown is ${limits.min_append_interval_seconds}s`);
    }
    const reason = taggedMessage(receipt);
    if (reason) expect(/cooldown/i.test(reason), `rejected for another reason: ${reason}`);
    expect(doc.revision_count === countBefore, "revision count changed");
    return `on-chain: state unchanged; ${reason ? `reason "${reason.slice(0, 60)}"` : "receipt gives no reason"}`;
  });

  // ---- 3. the other documents are registered now so their cooldowns run while we test D0
  if (!QUICK) {
    await step("H02", "D2 and D3 registered", "HARD", async () => {
      D.d2 = await registerDoc(aliceClient, "D2 injection attempt", "terms_v1", "d2");
      D.d3 = await registerDoc(aliceClient, "D3 site and rewrite guards", "terms_plain", "d3", { kind: "POLICY", publisher: "Harbor Notes Club" });
      return `documents #${D.d2}, #${D.d3}`;
    });
  }
  await step("H03", "D1 registered: baseline hash chain recomputes", "HARD", async () => {
    D.d1 = await registerDoc(aliceClient, "D1 main amendment chain", "terms_v1", "d1");
    const { doc, revs, texts } = await verifyChain(D.d1);
    expect(revs.length === 1 && revs[0].effect === "BASELINE" && revs[0].severity === "NONE" && revs[0].judged === false, "unexpected baseline");
    expect(doc.chain_head === keccak(":" + revs[0].text_hash), "chain_head is not keccak(':' + text_hash)");
    S.baselineText = texts[0];
    return `document #${D.d1}, head ${doc.chain_head.slice(0, 12)}...`;
  });

  await step("H04", "Baseline text equals the fixture as rendered by the validators", "MODEL", async () => {
    need(S.baselineText, "D1 baseline missing");
    observe(S.baselineText === collapse(localFixture("terms_v1")), "the web renderer returned different text than the raw fixture (it may add or drop page chrome)");
  });

  await step("H05", "Duplicate registrations are refused whatever the spelling", "HARD", async () => {
    need(D.d1, "D1 missing");
    const url = fixtureUrl("terms_v1", "d1");
    const spelled = url.replace("raw.githubusercontent.com", "RAW.githubusercontent.com") + "#frag";
    return expectRevert(bobClient, "register_document", [`Copycat [${RUN_ID}]`, "Copycat", "TERMS", spelled], "already tracked", { control: okRegisterArgs() });
  });

  await step("H06", "Lookup views: find_document, list_owner_documents, list_documents", "HARD", async () => {
    need(D.d1, "D1 missing");
    expect((await read("find_document", [fixtureUrl("terms_v1", "d1") + "#x"])) === D.d1, "find_document ignores canonicalisation");
    expect((await read("find_document", [fixtureUrl("terms_v1", "nobody")])) === 0, "find_document should return 0 for an unknown page");
    expect((await read("find_document", ["http://example.com/x"])) === 0, "find_document should return 0 for an invalid URL");
    const mine = await read("list_owner_documents", [alice.address]);
    expect(mine.includes(D.d1) && mine.includes(D.d0), "list_owner_documents misses a document");
    expect((await read("list_owner_documents", [bob.address])).length === 0, "bob owns nothing");
    const all = await allDocuments();
    expect(all.some((d) => d.id === D.d1), "list_documents misses D1");
    expect((await read("list_documents", [0, 2])).length === 2 || all.length < 2, "list_documents(0, 2) should return two documents");
    expect((await read("list_documents", [all.length + 10, 5])).length === 0, "an offset past the end must return an empty page");
    return `${all.length} documents on the contract`;
  });

  await step("G01", "A page with too little text cannot be tracked", "HARD", () =>
    expectRevert(aliceClient, "register_document", [`Tiny [${RUN_ID}]`, "Northwind", "TERMS", fixtureUrl("tiny", "tiny")], "too little readable text", { onchain: true }));

  // ---- 4. D0 continued: identical page, then a formatting-only revision (no model involved)
  await step("I01", "A page identical to the latest revision is refused", "HARD", async () => {
    need(D.d0, "D0 missing");
    if (S.d0FormatDone) throw new Skipped("D0 already advanced during C01");
    await waitForCooldown(D.d0);
    const probe = async () => JSON.stringify([await read("get_stats"), (await read("get_document", [D.d0])).revision_count]);
    return expectRevert(aliceClient, "append_revision", [D.d0, fixtureUrl("terms_v1", "d0")], "identical", { onchain: true, probe });
  });

  await step("F01", "A capitalisation-only change is recorded without a model verdict", "HARD", async () => {
    need(D.d0, "D0 missing");
    if (!S.d0FormatDone) await appendRevision(aliceClient, D.d0, fixtureUrl("terms_v1_caps", "d0"), "D0: case-only change");
    const { revs, texts, redlines } = await verifyChain(D.d0);
    const r = revs[1];
    expect(r.judged === false && r.effect === "NEUTRAL" && r.raw_effect === "" && r.quote === "", `expected a plain NEUTRAL, got ${r.effect}/${r.judged}`);
    expect(r.severity === "LOW" && r.silent_flag === false && r.churn_bp === 0 && r.retained_bp === 10000 && r.topics === "", "format-only metrics are wrong");
    expect(r.added_units === 0 && r.removed_units === 0 && redlines[1].added.length === 0 && redlines[1].removed.length === 0, "a format-only revision must have an empty redline");
    expect(texts[1] !== texts[0] && r.text_hash !== revs[0].text_hash, "the new bytes must be recorded even though no sentence changed");
    checkRules(r, redlines[1], texts[1], texts[0]);
    return `revision #${r.id}`;
  });

  // ---- 5. D1: the main amendment chain
  await step("M01", "Adverse amendment is rated worse, with a verbatim quote, and flagged HIGH", "MODEL", async () => {
    need(D.d1, "D1 missing");
    const id = await appendRevision(aliceClient, D.d1, fixtureUrl("terms_v3_adverse", "d1"), "D1: adverse amendment");
    const { revs, texts, redlines } = await verifyChain(D.d1);
    const r = revs.at(-1);
    S.m02 = r;
    expect(r.id === id && r.judged === true, "the revision was not judged");
    expect(r.topics === "DISPUTES,DATA,MONEY,NOTICE", `topics "${r.topics}" (deterministic: derived from the changed sentences)`);
    expect(r.added_units === 5 && r.removed_units === 4 && r.retained_bp >= limits.min_retained_bp && r.rewrite === false, "redline metrics are off");
    expect(redlines.at(-1).added.some((u) => /binding arbitration/i.test(u)) && redlines.at(-1).removed.some((u) => /do not sell your data/i.test(u)), "the redline does not contain the expected sentences");
    checkRules(r, redlines.at(-1), texts.at(-1), texts.at(-2));
    observe(r.effect === "WORSE" || r.effect === "MUCH_WORSE", `the validators rated forced arbitration plus data sharing ${r.effect}`);
    observe(r.severity === "HIGH", `severity ${r.severity}`);
    observe(r.quote.length >= limits.min_quote_chars, "no quote was recorded");
    return `${r.effect}/${r.severity}, quote "${r.quote.slice(0, 50)}"`;
  });

  if (!QUICK) {
    await step("A01", "Appeal by another account runs a fresh consensus round", "MODEL", async () => {
      const m02 = need(S.m02, "M01 did not produce a revision");
      await write(bobClient, "appeal_revision", [m02.id], "appeal D1 adverse revision");
      const r = await read("get_revision", [m02.id]);
      expect(r.appealed === true && r.appeal_by === lower(bob.address) && r.appeal_at > 0, "appeal fields are not recorded");
      expect(r.appeal_outcome === "CONFIRMED" || r.appeal_outcome === "REVISED", `appeal_outcome "${r.appeal_outcome}"`);
      if (r.appeal_outcome === "REVISED") expect(r.prev_effect === m02.effect && r.effect !== m02.effect, "a REVISED appeal must record the previous effect");
      else expect(r.effect === m02.effect && r.prev_effect === "", "a CONFIRMED appeal must not change the verdict");
      const { texts, redlines, revs } = await verifyChain(D.d1);
      const at = revs.findIndex((x) => x.id === m02.id);
      expect(at > 0, "the appealed revision disappeared from the chain");
      checkRules(revs[at], redlines[at], texts[at], texts[at - 1]);
      observe(r.appeal_outcome === "CONFIRMED", `the appeal changed the verdict from ${m02.effect} to ${r.effect}`);
      return `${r.appeal_outcome}`;
    });

    await step("A02", "A second appeal, the baseline and a format-only revision cannot be appealed", "HARD", async () => {
      const m02 = need(S.m02, "M01 did not produce a revision");
      need(D.d0, "D0 missing");
      const d0revs = await revisionsOf(D.d0);
      const format = d0revs.find((r) => r.seq > 0 && !r.judged);
      const rows = [
        [m02.id, "already been appealed"],
        [(await revisionsOf(D.d1))[0].id, "baseline revision has no verdict"],
      ];
      if (format) rows.push([format.id, "nothing to appeal"]);
      for (const [revId, needle] of rows) await expectRevert(bobClient, "appeal_revision", [revId], needle, { onchain: true });
      return `${rows.length} refusals confirmed`;
    });
  }

  await step("M02", "Reverting the amendment is rated better; the hash chain still recomputes", "MODEL", async () => {
    need(D.d1, "D1 missing");
    const id = await appendRevision(bobClient, D.d1, fixtureUrl("terms_v1", "d1"), "D1: revert to the original (by bob)");
    const { revs, texts, redlines } = await verifyChain(D.d1);
    const r = revs.at(-1);
    expect(r.id === id && r.submitter === lower(bob.address), "bob's revision was not recorded under bob");
    expect(texts.at(-1) === texts[0] && r.text_hash === revs[0].text_hash && r.chain_hash !== revs[0].chain_hash, "a revert has the old text but a new chain link");
    checkRules(r, redlines.at(-1), texts.at(-1), texts.at(-2));
    observe(r.effect === "BETTER" || r.effect === "MUCH_BETTER", `the validators rated the restoration of user protections ${r.effect}`);
    return `${r.effect}/${r.severity}`;
  });

  await step("M03", "Cosmetic edit (from the original text) is judged and stays quiet", "MODEL", async () => {
    need(D.d1, "D1 missing");
    const id = await appendRevision(aliceClient, D.d1, fixtureUrl("terms_v2_cosmetic", "d1"), "D1: cosmetic edit");
    const { revs, texts, redlines } = await verifyChain(D.d1);
    const r = revs.at(-1);
    expect(r.id === id && r.judged === true, "the revision was not judged by the validators");
    expect(r.topics === "" && r.injection_flagged === false && r.rewrite === false, `deterministic facts are off: topics "${r.topics}"`);
    expect(r.churn_bp < limits.heavy_churn_bp, `churn ${r.churn_bp} should be tiny for a three-word edit`);
    checkRules(r, redlines.at(-1), texts.at(-1), texts.at(-2));
    S.m01 = r;
    observe(r.effect === "NEUTRAL", `the validators rated a wording-only edit ${r.effect}${r.quote ? ` ("${r.quote.slice(0, 50)}")` : ""}`);
    observe(r.severity === "LOW" && r.silent_flag === false, `severity ${r.severity}, silent ${r.silent_flag}`);
    return `${r.effect}/${r.severity}`;
  });

  // ---- 6. D2: prompt-injection attempt
  if (!QUICK) {
    await step("N01", "A hostile page is flagged; the deterministic guard holds whatever the model does", "MODEL", async () => {
      need(D.d2, "D2 missing");
      const id = await appendRevision(aliceClient, D.d2, fixtureUrl("terms_v5_injection", "d2"), "D2: injection attempt");
      const { revs, texts, redlines } = await verifyChain(D.d2);
      const r = revs.at(-1);
      expect(r.id === id && r.judged === true, "the revision was not judged");
      expect(r.injection_flagged === true, "the page tried to instruct the judge but injection_flagged is false");
      expect(r.topics === "MONEY,TERMINATION,NOTICE", `topics "${r.topics}"`);
      checkRules(r, redlines.at(-1), texts.at(-1), texts.at(-2));
      // the guard: obeying the page (NEUTRAL) must still end up flagged and at least MEDIUM
      expect(r.effect !== "NEUTRAL" || (r.silent_flag === true && r.severity !== "LOW"), "a NEUTRAL rating on a hostile, sensitive redline slipped through unflagged");
      observe(r.effect === "WORSE" || r.effect === "MUCH_WORSE", `the validators rated an injected instruction plus a unilateral termination clause ${r.effect}`);
      return `${r.effect}/${r.severity}, silent ${r.silent_flag}`;
    });

    // ---- 7. D3: site and rewrite guards
    await step("P01", "A non-owner cannot point the document at another site", "HARD", async () => {
      need(D.d3, "D3 missing");
      const probe = async () => JSON.stringify([await read("get_stats"), (await read("get_document", [D.d3])).revision_count]);
      return expectRevert(bobClient, "append_revision", [D.d3, "https://example.org/terms"], "different site", { onchain: true, probe });
    });

    await step("P02", "A non-owner cannot commit a full rewrite", "HARD", async () => {
      need(D.d3, "D3 missing");
      await waitForCooldown(D.d3);
      const probe = async () => JSON.stringify([await read("get_stats"), (await read("get_document", [D.d3])).revision_count]);
      return expectRevert(bobClient, "append_revision", [D.d3, fixtureUrl("unrelated", "d3")], "full rewrite", { onchain: true, probe });
    });

    await step("P03", "The owner may commit a quiet rewrite; it is flagged and never LOW", "HARD", async () => {
      need(D.d3, "D3 missing");
      const statsBefore = await read("get_stats");
      const id = await appendRevision(aliceClient, D.d3, fixtureUrl("terms_plain_reworded", "d3"), "D3: owner rewrite (every sentence reworded)");
      const { revs, texts, redlines } = await verifyChain(D.d3);
      const r = revs.at(-1);
      expect(r.id === id && r.rewrite === true && r.retained_bp < limits.min_retained_bp, `rewrite flag ${r.rewrite}, retained ${r.retained_bp}`);
      // the fixture touches no sensitive topic and changes few words, so nothing but the rewrite flag can lift the severity
      expect(r.topics === "" && r.churn_bp < limits.heavy_churn_bp && r.injection_flagged === false, `topics "${r.topics}", churn ${r.churn_bp}`);
      expect(r.severity !== "LOW", `a rewrite must never be LOW (got ${r.severity} for ${r.effect})`);
      checkRules(r, redlines.at(-1), texts.at(-1), texts.at(-2));
      const statsAfter = await read("get_stats");
      expect(statsAfter.rewrites === statsBefore.rewrites + 1, "stats.rewrites did not move by one");
      expect((await read("get_document", [D.d3])).rewrites === 1, "document.rewrites should be 1");
      return `${r.effect}/${r.severity}, retained ${r.retained_bp}bp, churn ${r.churn_bp}bp`;
    });
  }

  // ---- 8. views and consistency
  await step("V01", "Pagination and unknown ids behave", "HARD", async () => {
    need(D.d1, "D1 missing");
    const total = (await read("get_document", [D.d1])).revision_count;
    expect((await read("list_revisions", [D.d1, 0, 2])).length === Math.min(2, total), "list_revisions(0, 2)");
    expect((await read("list_revisions", [D.d1, total, 5])).length === 0, "an offset at the end must give an empty page");
    expect((await read("list_revisions", [D.d1, 0, 1000])).length === total, "an oversized limit is capped, not an error");
    expect((await read("list_revisions", [999999, 0, 5])).length === 0, "an unknown document has no revisions");
    for (const fn of ["get_document", "get_revision", "get_revision_text", "get_redline"]) {
      let threw = false;
      try {
        await reader.readContract({ address, functionName: fn, args: [999999999] });
      } catch {
        threw = true;
      }
      expect(threw, `${fn}(unknown id) should fail`);
    }
    return `${total} revisions on D1`;
  });

  await step("C02", "Contract-wide counters equal a recount from the stored records", "HARD", async () => {
    const stats = await read("get_stats");
    const docs = await allDocuments();
    const tally = { much_better: 0, better: 0, neutral: 0, worse: 0, much_worse: 0, unclear: 0, low: 0, medium: 0, high: 0, silent: 0, rewrites: 0, format_only: 0, appeals: 0, revised: 0 };
    let revisions = 0;
    for (const d of docs) {
      const revs = await revisionsOf(d.id);
      revisions += revs.length;
      const body = revs.filter((r) => r.seq > 0);
      const per = { better: 0, worse: 0, neutral: 0, unclear: 0, high: 0, silent: 0, rewrites: 0 };
      for (const r of body) {
        tally[r.effect.toLowerCase()] += 1;
        tally[r.severity.toLowerCase()] += 1;
        if (r.silent_flag) { tally.silent += 1; per.silent += 1; }
        if (r.rewrite) { tally.rewrites += 1; per.rewrites += 1; }
        if (!r.judged) tally.format_only += 1;
        if (r.appealed) tally.appeals += 1;
        if (r.appeal_outcome === "REVISED") tally.revised += 1;
        if (r.effect === "BETTER" || r.effect === "MUCH_BETTER") per.better += 1;
        else if (r.effect === "WORSE" || r.effect === "MUCH_WORSE") per.worse += 1;
        else if (r.effect === "NEUTRAL") per.neutral += 1;
        else per.unclear += 1;
        if (r.severity === "HIGH") per.high += 1;
      }
      for (const key of Object.keys(per)) expect(d[key] === per[key], `document #${d.id}: ${key} is ${d[key]}, recount says ${per[key]}`);
      expect(d.revision_count === revs.length, `document #${d.id}: revision_count`);
    }
    expect(stats.total_documents === docs.length && stats.total_revisions === revisions, `totals: chain ${stats.total_documents}/${stats.total_revisions}, recount ${docs.length}/${revisions}`);
    const pairs = [["much_better", "much_better"], ["better", "better"], ["neutral", "neutral"], ["worse", "worse"], ["much_worse", "much_worse"], ["unclear", "unclear"],
      ["severity_low", "low"], ["severity_medium", "medium"], ["severity_high", "high"], ["silent_flags", "silent"], ["rewrites", "rewrites"],
      ["format_only", "format_only"], ["appeals", "appeals"], ["appeals_revised", "revised"]];
    for (const [statKey, tallyKey] of pairs) expect(stats[statKey] === tally[tallyKey], `stats.${statKey} is ${stats[statKey]}, recount says ${tally[tallyKey]}`);
    return `${docs.length} documents, ${revisions} revisions`;
  });

  await step("C03", "Every document's hash chain recomputes", "HARD", async () => {
    const docs = await allDocuments();
    let n = 0;
    for (const d of docs.filter((x) => created.includes(x.id))) {
      const { revs, texts, redlines } = await verifyChain(d.id);
      for (const [i, r] of revs.entries()) if (i > 0) checkRules(r, redlines[i], texts[i], texts[i - 1]);
      n += revs.length;
    }
    expect(n > 0, "nothing to verify");
    return `${n} revisions verified`;
  });

  await step("C04", "This run changed the totals as expected", "HARD", async () => {
    if (!before) throw new Skipped("no stats before the run");
    const after = await read("get_stats");
    expect(after.total_documents - before.total_documents >= created.length, `total_documents grew by ${after.total_documents - before.total_documents}, expected at least ${created.length}`);
    expect(after.total_revisions > before.total_revisions, "total_revisions did not grow");
    return `+${after.total_documents - before.total_documents} documents, +${after.total_revisions - before.total_revisions} revisions`;
  });
}

// ---------------------------------------------------------------- report
function writeReport(totalSeconds) {
  const fails = steps.filter((s) => s.status === "FAIL");
  const warns = steps.filter((s) => s.status === "WARN");
  const skips = steps.filter((s) => s.status === "SKIP");
  const lines = [];
  lines.push("# Redline live end-to-end report", "");
  lines.push(`- Run: \`${RUN_ID}\`${QUICK ? " (quick)" : ""}${STRICT ? " (strict)" : ""}`);
  lines.push(`- Network: ${NETWORK}`);
  lines.push(`- Contract: ${address ? `\`${address}\` ([explorer](${EXPLORER}/contracts/${address}))` : "not deployed"}`);
  lines.push(`- Fixtures: ${FIXTURE_BASE || "n/a"}`);
  lines.push(`- Date: ${new Date().toISOString()}`);
  lines.push(`- Result: **${fails.length ? "FAIL" : STRICT && warns.length ? "FAIL (strict)" : "PASS"}** — ${steps.filter((s) => s.status === "PASS").length} passed, ${warns.length} warnings, ${skips.length} skipped, ${fails.length} failed in ${(totalSeconds / 60).toFixed(1)} min`, "");
  lines.push("HARD steps are deterministic contract behaviour; MODEL steps depend on real LLM validators or on the web renderer (a WARN there is an observation, not a contract fault).", "");
  lines.push("| Step | Kind | Result | Detail | Time |", "| --- | --- | --- | --- | --- |");
  for (const s of steps) lines.push(`| ${s.id} ${s.title} | ${s.kind} | ${s.status} | ${s.detail.replace(/\|/g, "\\|").replace(/\n/g, " ").slice(0, 220)} | ${(s.ms / 1000).toFixed(1)}s |`);
  lines.push("", "## Transactions", "", "| Label | Seconds | Result | Link |", "| --- | --- | --- | --- |");
  for (const t of txs) lines.push(`| ${t.label} | ${t.seconds} | ${t.failed ? "reverted" : "accepted"} | [${t.hash.slice(0, 12)}...](${t.explorer}) |`);
  lines.push("");
  mkdirSync(REPORT_DIR, { recursive: true });
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const md = lines.join("\n");
  const json = JSON.stringify({ runId: RUN_ID, address, fixtureBase: FIXTURE_BASE, quick: QUICK, strict: STRICT, steps, txs }, null, 2);
  writeFileSync(resolve(REPORT_DIR, "e2e-latest.md"), md);
  writeFileSync(resolve(REPORT_DIR, "e2e-latest.json"), json);
  writeFileSync(resolve(REPORT_DIR, `e2e-${stamp}.md`), md);
  writeFileSync(resolve(REPORT_DIR, `e2e-${stamp}.json`), json);
  return { fails, warns };
}

const startedAt = Date.now();
try {
  await main();
} catch (err) {
  steps.push({ id: "XX", title: "Unexpected error", kind: "HARD", status: "FAIL", detail: `${err?.message ?? err}`.slice(0, 600), ms: 0 });
  console.log(`\nunexpected error: ${err?.stack ?? err}`);
}
const { fails, warns } = writeReport((Date.now() - startedAt) / 1000);
console.log(`\n${steps.filter((s) => s.status === "PASS").length} passed, ${warns.length} warnings, ${steps.filter((s) => s.status === "SKIP").length} skipped, ${fails.length} failed`);
console.log(`report: ${resolve(REPORT_DIR, "e2e-latest.md").replace(ROOT + "/", "")}`);
process.exit(fails.length || (STRICT && warns.length) ? 1 : 0);
