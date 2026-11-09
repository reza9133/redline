# Redline

**A tamper-evident amendment ledger for the fine print, as a GenLayer intelligent contract.**

Companies change their terms of service, privacy policies and pricing pages quietly. Redline turns
that into an append-only, verifiable history: anyone can start tracking a public page, anyone can
record its next version, and the validators answer the only question a reader cares about:

> *What did this change do to **me**?*

Redline does not ask a language model "what does this page say?". It does the boring, checkable part
itself, in plain Python, and shows the model **only the sentences that changed**:

```
 page URL ──► every validator fetches it independently ──► must read byte-identical text
                                   │
        deterministic redline (pure Python, no model)
        removed sentences · added sentences · how much old text survives · word-level churn
        sensitive topics touched (disputes, data, money, liability, IP, termination, notice)
                                   │
        model sees ONLY the changed sentences ──► rates the effect on the reader
        MUCH_BETTER · BETTER · NEUTRAL · WORSE · MUCH_WORSE   + a verbatim quote
                                   │
        consensus = "at most one step apart"   (bounded disagreement, not strict equality)
                                   │
        deterministic rules the model never decides:
        no quote → no verdict · silent-rewrite guard · severity · rewrite lock · hash chain
```

Why that shape? Models are good at judging a handful of sentences and unreliable at diffing a
30-page document. Python is the opposite. Splitting the work this way makes the model's job small,
makes its inputs checkable, and lets the contract overrule it with arithmetic.

## What is different from a typical "AI oracle" contract

| Idea | What Redline does |
| --- | --- |
| Input to the model | Only the removed / added sentences (capped; sentences that touch sensitive topics are shown first, so a flood of trivial edits cannot hide the real change). |
| Consensus | **Bounded disagreement**: a validator accepts the leader when its own rating is at most one step away on the five-point scale. `NEUTRAL` vs `WORSE` agree, `BETTER` vs `WORSE` never do. |
| Evidence | A quote must be a verbatim piece of **one changed sentence**, checked in plain Python by every validator. No grounded quote, no verdict: it is stored as `UNCLEAR`. |
| Distrusting the model | **Silent-rewrite guard**: a `NEUTRAL` rating on a redline that touches sensitive topics (or churns ≥ 25 % of the words) is flagged and raised to `MEDIUM`. Severity is computed, never asked. |
| Formatting-only edits | If no sentence differs once case is ignored, the revision is recorded as `NEUTRAL` **without calling the model at all**. |
| History | Every revision extends a Keccak hash chain over the texts. Reordering or editing the past breaks the chain. |
| Spam | 60 s cooldown per document, 25 revisions per document, 20 documents per owner, identical pages refused, and a page that shares almost nothing with the previous text is a *rewrite* that only the document's owner may commit. |
| Second opinion | `appeal_revision` runs a fresh consensus round over the **stored** redline (no web fetch) and replaces the verdict if it differs. |

## What the model decides, and what it cannot

| The model decides | The contract decides (deterministic, tested) |
| --- | --- |
| The five-point rating | Which sentences changed, how much text survives, churn, topics |
| A one-line summary | Whether the rating counts (`UNCLEAR` without a grounded quote) |
| A quote from a changed sentence | Severity `LOW / MEDIUM / HIGH`, the silent-rewrite flag, the rewrite flag |
| | Who may append what (same site, owner-only rewrites), cooldown, caps, the hash chain |

Severity table (`heavy` = the redline touches a sensitive topic **or** word churn ≥ 25 %):

| Rating | quiet redline | heavy redline |
| --- | --- | --- |
| `MUCH_WORSE` | HIGH | HIGH |
| `WORSE` | MEDIUM | HIGH |
| `NEUTRAL` | LOW | MEDIUM **+ `silent_flag`** |
| `BETTER` | LOW | MEDIUM |
| `MUCH_BETTER` | MEDIUM | MEDIUM |
| `UNCLEAR` | LOW | MEDIUM |

A rewrite never stays `LOW` (it is lifted to `MEDIUM`).

## Project layout

```
contracts/redline.py        the intelligent contract (single file, ~1,400 lines)
e2e/deploy.mjs              deploy to Studionet
e2e/live-e2e.mjs            live end-to-end run against Studionet (39 steps)
e2e/fixtures/*.txt          the pages the live run tracks (serve them from a public repo)
tests/contract/             215 unit tests (Python stdlib unittest, no downloads)
tests/standin/              a strict local stand-in for the GenVM SDK + a tiny local chain
tests/rehearsal/            offline rehearsal: runs the UNMODIFIED live script without Studionet
scripts/mutation_check.py   injects 257 deliberate bugs; the tests must catch every one
scripts/rehearsal_mutants.py  feeds broken contracts to the live script; it must catch them
docs/                       ARCHITECTURE.md · SECURITY.md · TESTING.md
```

## Quick start

Requirements: Python 3 for the tests (nothing to install; developed and tested on 3.12) and Node 20.6+ for the scripts (tested on 22).

```bash
npm install          # only needed for deploy / live e2e (genlayer-js, viem)
npm run check        # 215 unit tests + syntax check of the scripts (~10 s)
npm run rehearsal    # the live script against a local stand-in, no network (~2 min)
```

## Deploy to Studionet

1. **Push the repository to a public GitHub repo.** The validators fetch the fixtures over the
   internet, exactly like any other page, so `e2e/fixtures/` must be reachable at
   `https://raw.githubusercontent.com/<owner>/<repo>/<branch>/e2e/fixtures/terms_v1.txt`.
   (Inside a Codespace, or in any clone with an `origin` remote, the scripts work this out themselves.
   Otherwise set `E2E_FIXTURE_BASE`, see `.env.example`.)
2. Deploy the contract:
   ```bash
   npm run deploy
   ```
   It prints the address and writes `deployments/studionet.json`. (Alternative: open GenLayer Studio,
   paste `contracts/redline.py`, deploy with no constructor arguments.)
3. Run the live end-to-end test:
   ```bash
   export REDLINE_CONTRACT_ADDRESS=0x...      # optional: without it a fresh contract is deployed
   npm run e2e                                # ~15-25 minutes
   npm run e2e:quick                          # ~5 minutes, a smaller pass
   node e2e/live-e2e.mjs --strict             # model observations that miss expectations also fail
   ```
   The report is written to `reports/e2e-latest.md` (and `.json`), with an explorer link per transaction.

### What the live run proves

Every step is tagged:

* **HARD**: deterministic contract behaviour. It must hold whatever the validators' models answer. A
  failure fails the run (exit code 1). Examples: the URL rules, the cooldown, "identical page refused",
  case-only edits recorded without a model verdict, the hash chain recomputed with `viem`, the quote rule,
  the severity policy re-computed in JavaScript for *every* recorded revision, and a full recount of every
  contract-wide and per-document counter from the stored records.
* **MODEL**: depends on real LLM validators or on how the web renderer presents a page (for example "the
  validators rate forced arbitration `WORSE` or `MUCH_WORSE`"). Recorded as `PASS` or `WARN`. A `WARN` is an
  observation about the models, not a contract fault; `--strict` turns it into a failure.

The scenario, in the order it runs:

| Step | What happens |
| --- | --- |
| S01–S04 | accounts funded, fixtures reachable and identical to the local files, contract deployed, `get_limits` equals the source constants |
| R01–R12 | twelve inputs the contract must refuse (private/metadata/CGNAT/hex IPs, odd ports, userinfo, http, …) |
| H01–H06 | register D0 / D2 / D3 / D1, baseline hash chain recomputed, duplicate spellings refused, lookup views |
| C01, G01, I01, F01 | cooldown, too-little-text page, identical page, **case-only change recorded with no model** |
| M01 | **adverse amendment** (forced arbitration + data sharing + price changes) → rated worse, quoted, `HIGH` |
| A01–A02 | **appeal** by a second account; second appeal / baseline / format-only revision refused |
| M02, M03 | revert to the original (by a non-owner), then a cosmetic edit judged quiet |
| N01 | **hostile page** that tries to instruct the judge: flagged; if the model obeys, the guard still fires |
| P01–P03 | non-owner cannot change site or commit a rewrite; owner commits a **quiet rewrite** (flagged, never `LOW`) |
| V01, C02–C04 | pagination, unknown ids, full counter recount, every hash chain recomputed |

## Contract API

Writes

| Method | Purpose |
| --- | --- |
| `register_document(name, publisher, kind, url) -> u256` | Start tracking a public page. `kind` ∈ `TERMS, PRIVACY, PRICING, LICENSE, POLICY, OTHER`. Records the baseline (revision 0). |
| `append_revision(doc_id, url) -> u256` | Record the page's next version. Anyone may, on the document's site (or a `web.archive.org` permalink). |
| `appeal_revision(rev_id)` | One fresh consensus round over the stored redline; replaces the verdict if the final rating differs. |

Reads

| Method | Returns |
| --- | --- |
| `get_document(id)` · `list_documents(offset, limit)` · `find_document(url)` · `list_owner_documents(address)` | documents (counters per verdict, `chain_head`, `site_url`, …) |
| `get_revision(id)` · `list_revisions(doc, offset, limit)` | revisions: rating, severity, flags, quote, topics, ratios, hashes, appeal state |
| `get_revision_text(id)` · `get_redline(id)` | the stored text · the shown redline as JSON `{added, removed, added_omitted, removed_omitted}` |
| `get_stats()` · `get_limits()` · `get_version()` | contract-wide counters · every limit · version |

Hash chain, for independent verification:
`chain_0 = keccak(":" + keccak(text_0))`, `chain_i = keccak(chain_{i-1} + ":" + keccak(text_i))` (hex digests,
UTF-8 input). `e2e/live-e2e.mjs` recomputes it with `viem`.

Revision `effect` values: `BASELINE` (revision 0), `MUCH_BETTER`, `BETTER`, `NEUTRAL`, `WORSE`, `MUCH_WORSE`,
`UNCLEAR`. `raw_effect` is what the model said; they differ only when `effect` is `UNCLEAR`.

## Limits

| Limit | Value |
| --- | --- |
| Tracked text | first **8,000** characters of the rendered page (whitespace collapsed) |
| Sentence unit | split at `. ! ? ;` before a space, clause labels like `1.` are not sentence ends; chopped at 400 chars |
| Shown to the model | ≤ 24 sentences and ≤ 3,000 chars per side |
| Cooldown / revisions | 60 s per document · 25 per document · 20 documents per owner |
| Rewrite lock | < 20 % of the previous sentences survive ⇒ only the owner may commit |
| Quote | 8 – 240 chars, verbatim in one changed sentence |

## Honest limitations

* **Only the first 8,000 characters** of a page are tracked, because the text travels through consensus and is
  stored per revision. A change later in a long document is invisible. Raise `MAX_TEXT_CHARS` only after testing
  your network's calldata limits.
* Topic tagging is a transparent **keyword heuristic** over whole words and phrases (English). It can miss a
  clause worded unusually and can flag an innocent one; it only ever raises severity or sets a flag, never lowers
  a verdict. The model, not the keywords, rates direction.
* Sentence splitting is heuristic (abbreviations such as "Inc. Foo" split). It is deterministic, so every validator
  agrees, but it is not a linguistic parser.
* Ownership is first-come: whoever registers a URL first owns it and is the only one who may commit rewrites or
  move the tracked site. Anyone can still append revisions on the same site.
* The contract cannot prove a page was served to *you*; it records what the validators saw.

## What has been verified, and how

This project was built and debugged **without access to Studionet or the real GenVM SDK**, so be precise about
what that means:

| Layer | Result |
| --- | --- |
| 215 unit tests against a *strict* SDK stand-in (storage forbidden inside leader/validator blocks, explicit `u256`, copy-on-store, full rollback on revert, calldata-encodable results only, …) | pass |
| Mutation check: 257 deliberate bugs injected into the contract | **257 / 257 caught** |
| Offline rehearsal of the unmodified live script, 4 fake-model behaviours (truthful, obedient, lazy, flipping) + quick + strict | pass (obedient / lazy / flipping only produce `WARN` on MODEL steps, as designed) |
| Broken contracts fed to the live script | **28 / 28 caught** |
| Live run on Studionet | **not run by the author. You run it (`npm run e2e`).** |

The stand-in is stricter than the real SDK where bugs usually hide, but it is not the real GenVM. API usage is
limited to calls that an already-deployed Studionet project uses (`gl.vm.run_nondet_unsafe`,
`gl.nondet.web.render`, `gl.nondet.exec_prompt`, storage dataclasses, `TreeMap`, `DynArray`, events), and the
`Depends` pin on line 1 is copied from that project. If Studionet has moved to a newer SDK, update that hash.

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| `S02 ... HTTP 404` | `e2e/fixtures` not pushed to the branch / repository the script resolved. Push, or set `E2E_FIXTURE_BASE`. |
| `Rate limit exceeded: 30 requests per minute` | The public RPC limit. The scripts throttle to 22/min; lower `E2E_RPC_PER_MINUTE` further if needed. |
| `H04 WARN` (text differs from the raw fixture) | The renderer adds or drops page chrome. Deterministic checks that depend on exact text may then fail; use plain-text hosting for fixtures. |
| A MODEL step `WARN`s | The validators' models disagree with the expectation. Inspect the transaction in the explorer. |
| A transaction never reaches `ACCEPTED` | Validators could not agree (`UNDETERMINED`). Bounded by `E2E_RECEIPT_RETRIES` (default 90 polls). Re-run the step. |
| `C01 WARN (inconclusive)` | The network took longer than the cooldown between two transactions; not a contract fault. |
| Deploy rejected | Check the `Depends` pin on line 1 against the current Studionet SDK. |

See `docs/ARCHITECTURE.md`, `docs/SECURITY.md` and `docs/TESTING.md` for details.
