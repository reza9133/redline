// Shared helpers for the live Studionet scripts (deploy + end-to-end).
// Plain ESM: runs with `node` and the project's own dependencies, no extra tooling.
import "./throttle.mjs"; // first import: wraps fetch before genlayer-js / viem load
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createAccount, createClient, generatePrivateKey } from "genlayer-js";
import { studionet } from "genlayer-js/chains";
import { TransactionStatus } from "genlayer-js/types";

export const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
// REDLINE_CONTRACT_PATH lets you deploy / check another file (used by the offline rehearsal).
export const CONTRACT_PATH = resolve(process.env.REDLINE_CONTRACT_PATH || resolve(ROOT, "contracts", "redline.py"));
export const EXPLORER = "https://explorer-studio.genlayer.com";
export { createAccount, createClient, generatePrivateKey, studionet, TransactionStatus };

export const sleep = (ms) => new Promise((resolveSleep) => setTimeout(resolveSleep, ms));

/** genlayer-js returns Map / bigint / Uint8Array(20): turn them into plain JSON-friendly values. */
export function normalize(value, depth = 0) {
  if (depth > 12) return null;
  if (typeof value === "bigint") return Number(value) <= Number.MAX_SAFE_INTEGER ? Number(value) : value.toString();
  if (value instanceof Uint8Array) return value.length === 20 ? "0x" + Buffer.from(value).toString("hex") : Array.from(value);
  if (value instanceof Map) return Object.fromEntries([...value.entries()].map(([k, v]) => [String(k), normalize(v, depth + 1)]));
  if (Array.isArray(value)) return value.map((v) => normalize(v, depth + 1));
  if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, normalize(v, depth + 1)]));
  return value;
}

export function makeClient(account) {
  return createClient({ chain: studionet, account });
}

/** Studionet faucet: gives the account simulated GEN. Returns true, or the error text. */
export async function fund(client, address, amount = 10 ** 20) {
  try {
    await client.request({ method: "sim_fundAccount", params: [address, amount] });
    return true;
  } catch (err) {
    return String(err?.message ?? err);
  }
}

export function receiptFailed(receipt) {
  return receipt?.txExecutionResultName === "FINISHED_WITH_ERROR";
}

const safeJson = (v) => {
  try {
    return JSON.stringify(v, (_k, x) => (typeof x === "bigint" ? x.toString() : x)) ?? "";
  } catch {
    return String(v);
  }
};

/** Every string an error exposes: message, details, viem metaMessages, data and the whole cause chain. */
export function errorText(err, depth = 0) {
  if (err == null || depth > 5) return "";
  if (typeof err === "string") return err;
  const parts = [];
  for (const k of ["shortMessage", "message", "details", "reason"]) if (typeof err[k] === "string") parts.push(err[k]);
  if (Array.isArray(err.metaMessages)) parts.push(...err.metaMessages.map(String));
  if (err.data != null) parts.push(typeof err.data === "string" ? err.data : safeJson(err.data));
  if (err.cause) parts.push(errorText(err.cause, depth + 1));
  return parts.join("\n");
}

/** Digs the contract's own "[EXPECTED] ..." / "[EXTERNAL] ..." text out of any receipt or error shape. */
export function taggedMessage(value, depth = 0) {
  if (depth > 7 || value == null) return null;
  if (typeof value === "string") {
    const m = value.match(/\[(EXPECTED|EXTERNAL)\]\s*([^"\\]*)/);
    return m ? m[2].trim() : null;
  }
  if (Array.isArray(value)) {
    for (const v of value) {
      const f = taggedMessage(v, depth + 1);
      if (f) return f;
    }
    return null;
  }
  if (typeof value === "object") {
    for (const v of Object.values(value)) {
      const f = taggedMessage(v, depth + 1);
      if (f) return f;
    }
  }
  return null;
}

export function findAddress(value, depth = 0) {
  if (depth > 7 || value == null) return null;
  if (typeof value === "string") return /^0x[0-9a-fA-F]{40}$/.test(value) ? value : null;
  if (typeof value === "object") {
    for (const key of ["contract_address", "contractAddress", "recipient", "to_address"]) {
      const hit = typeof value[key] === "string" && /^0x[0-9a-fA-F]{40}$/.test(value[key]) ? value[key] : null;
      if (hit) return hit;
    }
    for (const v of Object.values(value)) {
      const f = findAddress(v, depth + 1);
      if (f) return f;
    }
  }
  return null;
}

export async function deployRedline(client, log = console.log) {
  const code = readFileSync(CONTRACT_PATH, "utf8");
  log(`deploying ${CONTRACT_PATH.replace(ROOT + "/", "")} to Studionet ...`);
  const hash = await client.deployContract({ code, args: [], leaderOnly: false });
  log(`deploy tx ${hash}`);
  const receipt = await client.waitForTransactionReceipt({ hash, status: TransactionStatus.ACCEPTED, interval: 3000, retries: 120 });
  if (receiptFailed(receipt)) throw new Error("deployment failed: " + (taggedMessage(receipt) ?? "see the explorer"));
  const address = findAddress(receipt?.data) ?? findAddress(receipt?.txDataDecoded) ?? findAddress(receipt);
  if (!address) throw new Error(`deployed (tx ${hash}) but the address could not be read from the receipt; copy it from ${EXPLORER}/tx/${hash}`);
  return { address, hash };
}

/**
 * Where e2e/fixtures are served from. The contract fetches fixtures over the public internet,
 * so they must be pushed to a public repository first.
 *   1. E2E_FIXTURE_BASE                         explicit, always wins
 *   2. GITHUB_REPOSITORY (+ E2E_FIXTURE_BRANCH) set automatically in Codespaces / Actions
 *   3. `git remote origin` of this checkout     github.com/<owner>/<repo>
 */
export function resolveFixtureBase() {
  const explicit = process.env.E2E_FIXTURE_BASE;
  if (explicit) return explicit.replace(/\/$/, "");
  const git = (...args) => {
    try {
      return execFileSync("git", args, { cwd: ROOT, encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim();
    } catch {
      return "";
    }
  };
  let branch = process.env.E2E_FIXTURE_BRANCH || "";
  if (!branch) {
    const head = git("rev-parse", "--abbrev-ref", "HEAD");
    branch = head && head !== "HEAD" ? head : "main";
  }
  let repo = process.env.GITHUB_REPOSITORY || "";
  if (!repo) {
    const remote = git("config", "--get", "remote.origin.url");
    const m = remote.match(/github\.com[:/]+([^/]+\/[^/]+?)(?:\.git)?\/?$/i);
    if (m) repo = m[1];
  }
  if (!repo) {
    throw new Error(
      "cannot tell where the fixtures are hosted. Push this project to a public GitHub repository, then either run inside " +
        "a Codespace / a clone with an `origin` remote, or set E2E_FIXTURE_BASE=https://raw.githubusercontent.com/<owner>/<repo>/<branch>/e2e/fixtures",
    );
  }
  return `https://raw.githubusercontent.com/${repo}/${branch}/e2e/fixtures`;
}
