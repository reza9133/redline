# Architecture

## Data model

```
Document                                   Revision
  id, owner, name, publisher, kind           id, doc_id, seq (0 = baseline), url, submitter
  url            registered URL (immutable)  text_hash, chain_hash, text_len
  latest_url     URL of the latest revision  added_units, removed_units, added_chars, removed_chars
  site_url       the site non-owners must    retained_bp, churn_bp          (basis points, 0..10000)
                 stay on (moves only when    topics ("DISPUTES,DATA,...")
                 the OWNER appends a         raw_effect  what the model said
                 non-archive URL)            effect      what is recorded (UNCLEAR if no grounded quote)
  latest_rev_id, latest_hash, chain_head     severity, silent_flag, rewrite, judged, injection_flagged
  created_at, last_revision_at               summary, quote, created_at
  revision_count (includes the baseline)     appealed, appeal_outcome, appeal_by, appeal_at, prev_effect
  better, neutral, worse, unclear,
  high, silent, rewrites   (counters)
```

Side tables: `rev_texts[rev] -> text`, `rev_redlines[rev] -> JSON`, `doc_revs[doc] -> [rev ids]`,
`owner_docs[address] -> [doc ids]`, `fingerprints[keccak(canonical url)] -> doc id`, and two
`TreeMap[str, u256]` counter maps (per effect, per severity).

All judged-revision counters move in exactly one function, `_account(doc, effect, severity, silent, delta)`;
an appeal that revises a verdict calls it twice (`-1` for the old state, `+1` for the new one). The randomised
invariant test recomputes every counter from the stored records after every operation.

## The append pipeline

`append_revision(doc_id, url)`:

1. **Cheap, state-only checks** (before the clock and the network): URL rules, document exists, same-site rule for
   non-owners, revision cap.
2. **Cooldown** against the transaction timestamp.
3. Copy everything the non-deterministic blocks need out of storage into plain locals
   (`prev_text`, `name`, `kind`, `publisher`, `is_owner`, `url`). GenVM forbids storage access inside
   leader / validator code; the closures call **only module-level pure functions**, never `self`.
4. `run_nondet_unsafe(leader_fn, validator_fn)`:

   **Leader**
   ```
   snapshot = fetch(url)                         # None -> {"ok": false, "reason": "fetch"}
   plan     = _plan(prev_text, snapshot, is_owner)
   kind in { unchanged, blocked, format_only, judge }        # all deterministic
   unchanged / blocked      -> return {kind, hash}
   format_only              -> return {kind, hash, snapshot}  # no model
   judge                    -> judged = model(changed sentences only)
                                None -> {"ok": false, "reason": "model"}
                                else -> {kind, hash, snapshot, effect, summary, quote}
   ```
   **Validator** (`leaders_res` -> bool)
   ```
   fetch the page myself
   ok == false:   accept only if reason == "fetch" AND I cannot read the page either
                  (a model failure is never rubber-stamped: the round is retried)
   recompute the plan myself; kind, hash and snapshot must match the leader's
   unchanged / blocked / format_only: accept (nothing else to judge)
   judge: leader's effect must be on the scale
          leader's quote must be verbatim in ONE changed sentence of MY plan     (plain Python)
          my own model rating must be within one step of the leader's            (bounded disagreement)
   ```
5. **After consensus** the contract re-derives the plan itself and re-checks the hash, the kind, the rating and
   the quote (defence in depth, see `tests/contract/test_defense.py`), then applies the policy:
   `unchanged` / `blocked` revert with an `[EXPECTED]` reason; `format_only` is recorded as a plain `NEUTRAL`;
   `judge` goes through `_finalize` (no quote -> `UNCLEAR`) and `_severity`.
6. Persist the revision, the text, the redline; extend the hash chain; update the document; move counters;
   emit `RevisionRecorded`.

### Why "bounded disagreement"

Strict equality across independent LLMs makes a five-point scale unusable: honest validators would disagree on
`WORSE` versus `MUCH_WORSE` all day and rounds would never converge. Accepting "at most one step apart" keeps
liveness for honest models while keeping the extremes apart: `NEUTRAL` can never be pushed through against a
`MUCH_WORSE` validator, and `BETTER` can never pass against `WORSE`. The leader's rating is the one recorded, so
the worst a leader can do is shift a rating by one step, and the deterministic policy (quote requirement,
silent-rewrite guard, severity) limits what even that can achieve.

### Why the redline is computed in Python

* Models are unreliable at diffing long documents; plain code is exact.
* Every validator computes the identical redline from identical inputs, so the redline needs no trust.
* The model's context is tiny and fully described, which cuts both cost and the prompt-injection surface.
* Things like "how much of the old text survived" and "does this touch arbitration" become arithmetic the model
  cannot argue with.

### Sentence units and the diff

* Text is whitespace-collapsed, non-printable characters removed, truncated to `MAX_TEXT_CHARS`.
* Units end at `. ! ? ;` when a space (or the end of text) follows; a dot after a short clause label (`1.`, `12.`,
  `a.`) does not end a unit, so inserting a clause does not turn every later label into a change; units longer than
  400 characters are chopped at spaces.
* The diff is a **multiset difference ignoring case**: a pure reordering is not a change, a case-only change leaves
  an empty redline (recorded as `format_only`, no model), duplicates are counted.
* `retained_bp` = share of the previous text (in sentence characters) that survives. `churn_bp` is **word level**
  (bag-of-words symmetric difference over the changed sentences), so a three-word edit inside long sentences does not
  count as three rewritten sentences. The two are different on purpose: sentence-level retention is the right test
  for "is this still the same document" (common words are shared between *any* two English texts), word-level churn
  is the right measure of "how much changed".

### Topics

Seven tags (`DISPUTES, DATA, MONEY, LIABILITY, IP, TERMINATION, NOTICE`) matched against whole words/phrases of the
**changed** sentences only (removed and added). They feed the severity policy and the silent-rewrite guard, and are
stored on the revision.

### Plan kinds

| kind | meaning | model? | outcome |
| --- | --- | --- | --- |
| `unchanged` | byte-identical to the latest revision | no | refused |
| `blocked` | < 20 % of the previous sentences survive and the sender is not the owner | no | refused |
| `format_only` | no sentence differs (case / reordering / whitespace only) | no | recorded as `NEUTRAL`, `judged = false` |
| `judge` | anything else | yes | recorded with the model's rating, then the policy |

## Appeals

`appeal_revision(rev_id)` re-judges exactly the stored redline (the units the first round saw), with no web fetch,
so the two rounds differ only by leader and model sampling. If the final effect differs, the revision is *revised*:
`prev_effect` keeps the old one, severity and the silent flag are recomputed from the stored topics / churn /
rewrite facts, and counters move `-1 / +1`. One appeal per revision; baseline and format-only revisions have no
verdict to appeal.

## Events

`DocumentRegistered(doc_id; chain_head)`, `RevisionRecorded(rev_id, doc_id; effect, severity)`,
`RevisionAppealed(rev_id; outcome)`.
