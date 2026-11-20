# Fixtures

Plain-text pages the live end-to-end run asks the validators to fetch. They must be **ASCII** (so JavaScript
and Python hash identical bytes) and served as raw text from a public URL, for example
`https://raw.githubusercontent.com/<owner>/<repo>/<branch>/e2e/fixtures/terms_v1.txt`.

| File | Role | Deterministic facts (asserted in `tests/contract/test_fixtures.py`) |
| --- | --- | --- |
| `terms_v1.txt` | the original terms of a fictional cloud service | baseline of D0, D1, D2 |
| `terms_v1_caps.txt` | the same text in capitals | `format_only`: no sentence differs, so no model is called |
| `terms_v2_cosmetic.txt` | three small wording edits | no topics, word churn ~2 %, not a rewrite |
| `terms_v3_adverse.txt` | forced arbitration, data sharing, 7-day refunds, price changes without notice | topics `DISPUTES, DATA, MONEY, NOTICE`; 5 sentences added, 4 removed; 53 % of the text survives |
| `terms_v5_injection.txt` | v1 plus an instruction aimed at the judge and a unilateral termination clause | injection marker present; topics `MONEY, TERMINATION, NOTICE` |
| `terms_plain.txt` | a reading-club guideline text with no sensitive word | baseline of D3 |
| `terms_plain_reworded.txt` | every sentence reworded a little | a *quiet rewrite*: ~12 % of the sentences survive, word churn ~7 %, no topics |
| `unrelated.txt` | a bakery blog post | shares nothing with the other texts (a rewrite only the owner may commit) |
| `tiny.txt` | one short line | below `MIN_TEXT_CHARS`: cannot be tracked |

Every fixture URL gets a unique `?run=...&doc=...` query in the live run, so repeated runs never collide with the
"one document per page" rule; the host ignores the query string.
