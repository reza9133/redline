# Security model

Redline holds no funds and no secrets: everything it stores is public page text. The risks are
integrity of the record, abuse of the fetch capability, and manipulation of the model.

| Threat | Defence | Where it is tested |
| --- | --- | --- |
| **SSRF**: make validators fetch internal addresses | Only `https://` on port 443; every private, loopback, link-local, CGNAT, reserved and test IPv4 range blocked; every non-canonical IPv4 spelling (hex, octal, integer, short, leading zero) rejected; IPv6 literals, userinfo, backslashes, control characters, non-ASCII hosts, percent signs in the authority, `.local` / `.internal` / `.lan` / … names and wildcard-DNS services rejected | `test_urls.py` (shared vector file with 130+ rows), live rows R01–R12 |
| **Prompt injection** from page text | (1) the model sees only changed sentences; (2) `<<<` / `>>>` in page text are neutralised so it cannot close a prompt section; (3) the prompt tells the model the text is untrusted; (4) a rating needs a verbatim quote from a changed sentence; (5) the **silent-rewrite guard**: if a hostile page talks the model into `NEUTRAL`, a redline that touches sensitive topics is still flagged and raised to `MEDIUM`; (6) injection markers are recorded in `injection_flagged` | `test_revisions.py::Adversarial`, `test_engine.py`, live step N01 |
| **Hiding the real change in noise** | Sentences touching sensitive topics are shown to the model first; the cap is on count and characters; topics are computed over *all* changed sentences, not only the shown ones | `test_engine.py::Plan`, `test_revisions.py::Adversarial` |
| **A lying leader** | Validators recompute the plan, the hash, the snapshot and the kind from their own fetch; the quote is verified in Python; the rating must be within one step of the validator's own; a reported fetch failure is believed only if the validator cannot read the page either; a model failure is never accepted | `test_validators.py` |
| **A bug or malicious majority on the validator side** | The contract re-derives the plan after consensus and re-checks hash, kind, rating and quote | `test_defense.py` |
| **Leader whose model is down but whose validators' are not** | The leader reports `reason: "model"`; validators reject it unconditionally; the round is retried | `test_validators.py`, `test_appeal.py` |
| **Chain pollution / spam** | 60 s cooldown per document; 25 revisions per document; 20 documents per owner; identical pages refused; non-owners restricted to the document's site (plus archive permalinks); pages that share < 20 % of the previous sentences can only be committed by the owner | `test_revisions.py` |
| **Rewriting history** | Keccak hash chain over all revision texts; every view exposes the texts so anyone can recompute it | `test_revisions.py::HappyPath`, live C03 |
| **Duplicate / squatted documents** | One document per canonical URL (host lower-cased, fragment and default port removed, trailing slash removed; path and query case-sensitive) | `test_registration.py`, live H05 |
| **Counter drift** | One accounting function; randomised recount after every operation | `test_invariants.py`, live C02 |
| **Timestamp trouble** | A missing or malformed transaction timestamp is a clean `[EXPECTED]` revert, never a crash | `test_registration.py` |

## Not defended (by design)

* **Squatting**: the first registrant of a URL owns it. Ownership gates rewrites and site changes only; anyone can
  still append revisions on the same site.
* **The blind tail**: only the first 8,000 characters of a page are tracked. A change after that is not seen.
* **Truthfulness of the page**: Redline records what the validators fetched. It cannot tell whether a site served
  different content to someone else.
* **Model quality**: a rating is as good as the validators' models. The design bounds what a wrong rating can do
  (one step, quote required, severity computed) but does not make the models right.
* **Keyword gaps**: topic tagging is English keyword matching. A clause worded unusually may not set a topic (the
  model still rates it; the guard and the severity floor just have less to work with).

## Reporting

This is an example project. If you find a bug, add a failing test to `tests/contract` first (the mutation check
shows how little a green suite proves on its own) and fix the contract.
