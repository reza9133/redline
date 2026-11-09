# Testing

Four layers, from cheapest to most authoritative. The live run on Studionet is the final authority; the other three
exist so that it finds as little as possible.

```
npm run check              layer 1   215 unit tests          ~10 s    no network, no dependencies
npm run mutation           layer 2   257 injected bugs       ~15 min  (resumable)
npm run rehearsal          layer 3   the live script, offline ~2 min
npm run rehearsal:mutants  layer 3b  28 broken contracts vs the live script
npm run e2e                layer 4   Studionet             15-25 min
```

## Layer 1: unit tests against a strict SDK stand-in

`tests/standin/genlayer/__init__.py` is a local stand-in for the GenVM Python SDK, and `tests/standin/harness.py`
wraps it in a tiny chain (clock, accounts, mocked web and model, rollback on revert, validator replay).
It is deliberately **stricter than the real SDK** where contract bugs usually hide:

* storage (contract fields, `TreeMap`, `DynArray`, storage dataclasses) cannot be read or written inside a
  leader / validator block;
* `view` methods cannot write;
* storage integers must be explicit `u256(...)`; negative or oversized values raise instead of wrapping;
* assigning a dataclass into storage **copies** it (mutating the local afterwards changes nothing);
* a reverted transaction is rolled back completely (state and events);
* values returned by views and by leaders must be calldata-encodable (no floats, sets, tuples);
* web and model access are only possible inside non-deterministic blocks;
* a contract may only assign declared storage fields;
* the real **Keccak-256** (not SHA3) is implemented and verified against published vectors and, for multi-block
  inputs, against `hashlib.sha3_256` with the SHA3 domain byte.

The harness can replay the captured validator against a different world (another page, another model answer, a
forged leader result), which is how `test_validators.py` and `test_defense.py` work.

| File | Covers |
| --- | --- |
| `test_standin.py` | the stand-in really is strict (otherwise nothing else proves anything) |
| `test_urls.py` | URL validation vectors, helpers, canonicalisation, same-site rule |
| `test_engine.py` | sentence splitting, diff, topics, plan kinds, ratios, severity table, quotes, hygiene |
| `test_registration.py` | registration, normalisation, views, pagination, limits, timestamps |
| `test_revisions.py` | the append pipeline, policy, guards, cooldown, rewrites, failure modes, adversarial pages |
| `test_validators.py` | consensus: tolerance matrix, forged leaders, failure reports, cheap rejections |
| `test_appeal.py` | confirmed / revised appeals, counters, floors, failures |
| `test_defense.py` | the contract refuses forged results even if consensus accepted them |
| `test_fixtures.py` | deterministic facts of every e2e fixture + a replay of the live script's story |
| `test_invariants.py` | randomised operation sequences, every counter / hash / flag recomputed after each step |

Run one file: `python3 -m unittest discover -s tests/contract -p "test_engine.py"`.

## Layer 2: mutation check

A green suite proves little if it would stay green with bugs in the code. `scripts/mutation_check.py` applies 257
deliberate, exact-text mutations (flip `<` to `<=`, drop a rule, swap a counter, break the chain formula, widen a
mask, ...) to a copy of the contract and requires the suite to fail for each one. A mutant that does not even compile is
reported as invalid.

```
python3 scripts/mutation_check.py                             # everything
python3 scripts/mutation_check.py -k cooldown                 # a subset
python3 scripts/mutation_check.py --resume /tmp/m.tsv --budget 240   # time-boxed, resumable
```

Exit code 0 only if every mutation is killed (2 = not finished). A surviving mutant is either a hole in the tests
(add a test) or an *equivalent mutant* (the change cannot alter behaviour). The known equivalent mutants are listed
in the script's docstring, with the reason for each.

## Layer 3: offline rehearsal of the live script

`npm run rehearsal` runs the **unmodified** `e2e/live-e2e.mjs` against a local stand-in:

* `genlayer-js` and `viem` are replaced by shims through a module-resolution hook (`tests/rehearsal/hooks.mjs`);
  the shim's Keccak is an independent JavaScript implementation, so the contract's hashes are cross-checked by a
  second implementation;
* the contract runs in the strict SDK stand-in behind a JSON-lines Python process;
* simulation (`gen_call`) behaves like Studionet's: leader only, nothing committed, a revert reported only as
  "execution failed";
* the revision cooldown is shortened to 3 s in a temporary copy of the contract (the script checks the chain's limits
  against that same file), fixtures are served from `e2e/fixtures`, reports go to `reports/rehearsal/`.

The model is fake and selectable with `REHEARSAL_MODEL`:

| Mode | Behaviour | Expected outcome |
| --- | --- | --- |
| `truthful` (default) | rates by clearly adverse / favourable phrases and quotes them verbatim | 39 passed |
| `obedient` | does what a hostile page says | N01 `WARN`, every HARD step still passes (the guard holds) |
| `lazy` | right direction, never a quote | `UNCLEAR` verdicts, HARD steps pass ("no quote, no verdict") |
| `flipping` | after two calls on a prompt, the opposite verdict | A01 sees `REVISED`; counters still recount correctly |

`REHEARSAL_CLOCK_SKEW=-30` makes the chain clock run behind this machine's, which exercises the script's
cooldown retry logic. `--quick` and `--strict` work as in the live run.

`npm run rehearsal:mutants` goes one step further: it feeds deliberately broken contracts (chosen from the mutation
list) to the live script and requires at least one FAILED step for each. A live script that stays green against
a broken contract would be worthless. Cases that only show with a particular model name it (for example the
silent-rewrite guard needs `obedient`, the appeal counters need `flipping`).

The rehearsal found real problems in the script (a wrong expectation about the previous revision, state that was not
tracked correctly, a cooldown retry that missed on-chain rejections, a rejection test that a missing URL rule could
hide behind a failed fetch).

## Layer 4: live end-to-end on Studionet

See the README. Notes on interpreting a run:

* **HARD** steps must pass whatever the models say. **MODEL** steps may `WARN`.
* Rejections that depend on state or time are proven on-chain: one real transaction is sent, the receipt's reason
  is compared when the network exposes it, and the contract state must be unchanged.
* Pure input validation is simulated and paired with a **control call** (same method, valid input) so a typo cannot pass
  as a rejection. Simulation reports reverts only as "execution failed", so the exact reason of every rule is asserted
  in layer 1.
* Everything an immutable revision carries (text, redline) is read once and cached, because the public RPC allows
  about 30 requests a minute.

## What is not verified

* The real GenVM SDK, the real `genlayer-js`, and Studionet. Differences between the stand-in and the real thing are
  possible (for instance the shape of failed receipts, simulation timestamps, or how large a calldata payload the
  network accepts). The live script is written to degrade gracefully where those are unknown.
* The behaviour of real LLM validators. MODEL steps record it; they cannot assert it.
