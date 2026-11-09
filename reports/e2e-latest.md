# Redline live end-to-end report

- Run: `mw9epl1u`
- Network: GenLayer Studionet
- Contract: `0xD27E9FdddFe71C2abCeafDAf56d28962be175dAE` ([explorer](https://explorer-studio.genlayer.com/contracts/0xD27E9FdddFe71C2abCeafDAf56d28962be175dAE))
- Fixtures: https://raw.githubusercontent.com/reza9133/redline/main/e2e/fixtures
- Date: 2026-11-09T15:33:00.933Z
- Result: **PASS** — 39 passed, 0 warnings, 0 skipped, 0 failed in 13.9 min

HARD steps are deterministic contract behaviour; MODEL steps depend on real LLM validators or on the web renderer (a WARN there is an observation, not a contract fault).

| Step | Kind | Result | Detail | Time |
| --- | --- | --- | --- | --- |
| S01 Accounts created and funded | HARD | PASS | alice 0x549603566F861D10C70601Ac2077eEA308cabc54, bob 0x29e1707e016dec94ECbB08EFF4f8e0581c38DD5a | 0.4s |
| S02 Fixtures are reachable and match the local files | HARD | PASS | base https://raw.githubusercontent.com/reza9133/redline/main/e2e/fixtures | 1.5s |
| S03 Contract deployed (or an existing one reused) | HARD | PASS | deployed 0xD27E9FdddFe71C2abCeafDAf56d28962be175dAE | 4.7s |
| S04 get_version is 1.x and get_limits matches the source constants | HARD | PASS | version 1.0.0 | 2.3s |
| R01 Rejected: private IPv4 literal | HARD | PASS | rejected (control call passes; node hides the reason) | 4.5s |
| R02 Rejected: cloud metadata address (on-chain: the reason is visible in the receipt) | HARD | PASS | on-chain: state unchanged; reason "url must be a public https:// URL (no private IPs, odd ports, or useri" | 47.0s |
| R03 Rejected: carrier-grade NAT | HARD | PASS | rejected (control call passes; node hides the reason) | 1.4s |
| R04 Rejected: hex-spelled loopback | HARD | PASS | rejected (control call passes; node hides the reason) | 1.3s |
| R05 Rejected: non-443 port | HARD | PASS | rejected (control call passes; node hides the reason) | 1.2s |
| R06 Rejected: userinfo in a fetchable URL | HARD | PASS | rejected (control call passes; node hides the reason) | 1.2s |
| R07 Rejected: plain http on a fetchable host | HARD | PASS | rejected (control call passes; node hides the reason) | 1.2s |
| R08 Rejected: localhost | HARD | PASS | rejected (control call passes; node hides the reason) | 1.2s |
| R09 Rejected: name too short | HARD | PASS | rejected (control call passes; node hides the reason) | 1.3s |
| R10 Rejected: unknown kind | HARD | PASS | rejected (control call passes; node hides the reason) | 1.2s |
| R11 Rejected: blank publisher | HARD | PASS | rejected (control call passes; node hides the reason) | 1.2s |
| R12 Rejected: prompt delimiters in the name | HARD | PASS | rejected (control call passes; node hides the reason) | 1.2s |
| H01 D0 registered: baseline revision exists | HARD | PASS | document #1 | 9.9s |
| C01 Immediate second revision is blocked by the cooldown | HARD | PASS | on-chain: state unchanged; reason "revision cooldown still active; try again in a minute" | 48.3s |
| H02 D2 and D3 registered | HARD | PASS | documents #2, #3 | 50.9s |
| H03 D1 registered: baseline hash chain recomputes | HARD | PASS | document #4, head fd4ae8a5e92f... | 20.4s |
| H04 Baseline text equals the fixture as rendered by the validators | MODEL | PASS |  | 0.0s |
| H05 Duplicate registrations are refused whatever the spelling | HARD | PASS | rejected (control call passes; node hides the reason) | 2.2s |
| H06 Lookup views: find_document, list_owner_documents, list_documents | HARD | PASS | 4 documents on the contract | 35.9s |
| G01 A page with too little text cannot be tracked | HARD | PASS | on-chain: state unchanged; reason "the page has too little readable text to track (at least 120 character" | 17.1s |
| I01 A page identical to the latest revision is refused | HARD | PASS | on-chain: state unchanged; reason "the page is identical to the latest recorded revision" | 43.0s |
| F01 A capitalisation-only change is recorded without a model verdict | HARD | PASS | revision #5 | 29.1s |
| M01 Adverse amendment is rated worse, with a verbatim quote, and flagged HIGH | MODEL | PASS | MUCH_WORSE/HIGH, quote "All disputes must be settled by binding arbitratio" | 54.1s |
| A01 Appeal by another account runs a fresh consensus round | MODEL | PASS | CONFIRMED | 19.6s |
| A02 A second appeal, the baseline and a format-only revision cannot be appealed | HARD | PASS | 3 refusals confirmed | 90.4s |
| M02 Reverting the amendment is rated better; the hash chain still recomputes | MODEL | PASS | MUCH_BETTER/MEDIUM | 35.8s |
| M03 Cosmetic edit (from the original text) is judged and stays quiet | MODEL | PASS | NEUTRAL/LOW | 65.1s |
| N01 A hostile page is flagged; the deterministic guard holds whatever the model does | MODEL | PASS | MUCH_WORSE/HIGH, silent false | 46.2s |
| P01 A non-owner cannot point the document at another site | HARD | PASS | on-chain: state unchanged; reason "only the document owner may point it at a different site (web.archive." | 21.5s |
| P02 A non-owner cannot commit a full rewrite | HARD | PASS | on-chain: state unchanged; reason "too little of the previous text survives in this page; only the docume" | 42.7s |
| P03 The owner may commit a quiet rewrite; it is flagged and never LOW | HARD | PASS | NEUTRAL/MEDIUM, retained 1208bp, churn 697bp | 59.9s |
| V01 Pagination and unknown ids behave | HARD | PASS | 4 revisions on D1 | 16.2s |
| C02 Contract-wide counters equal a recount from the stored records | HARD | PASS | 4 documents, 10 revisions | 31.6s |
| C03 Every document's hash chain recomputes | HARD | PASS | 10 revisions verified | 17.3s |
| C04 This run changed the totals as expected | HARD | PASS | +4 documents, +10 revisions | 1.2s |

## Transactions

| Label | Seconds | Result | Link |
| --- | --- | --- | --- |
| deploy | 0 | accepted | [0xfb896ca53c...](https://explorer-studio.genlayer.com/tx/0xfb896ca53c114169eaba0e72eeff9cfe3637833de1bd726aeedeb9f056d04e5b) |
| expected rejection: register_document | 5 | accepted | [0xc4623288d1...](https://explorer-studio.genlayer.com/tx/0xc4623288d13216885763c6d5038b18978cbdf0d6d8d42431081308a57b0f9358) |
| register: D0 cooldown and format-only | 5 | accepted | [0x44b9aadd9f...](https://explorer-studio.genlayer.com/tx/0x44b9aadd9f3c7a214a365f909cb8fa904408c4d7065b2f6ce5ee06c44ef70583) |
| expected rejection: append_revision (cooldown) | 0 | accepted | [0x77e3795540...](https://explorer-studio.genlayer.com/tx/0x77e3795540ec89d423915ee2f7e230654c74509d6a897fe7a3a0a10a8e05d149) |
| register: D2 injection attempt | 10 | accepted | [0x3213c5d65e...](https://explorer-studio.genlayer.com/tx/0x3213c5d65e4d595ea711997fb6cb93fa3ecb25fbf28499ea409b6ade5762a3f3) |
| register: D3 site and rewrite guards | 34 | accepted | [0x4dd054f18a...](https://explorer-studio.genlayer.com/tx/0x4dd054f18a6c26404f25c8fe8a2e41e6b7adf79101fe31ef08e7d555a8336676) |
| register: D1 main amendment chain | 6 | accepted | [0x56683ac7b0...](https://explorer-studio.genlayer.com/tx/0x56683ac7b0eea49089797b6302f157b8224f79d8a53956a3ea432938161a1c35) |
| expected rejection: register_document | 6 | accepted | [0x2fc2d3686a...](https://explorer-studio.genlayer.com/tx/0x2fc2d3686a35a40294febc5375754e99610e0b69b7a4256b5b33a35dd5d17609) |
| expected rejection: append_revision | 12 | accepted | [0x61d1e62c08...](https://explorer-studio.genlayer.com/tx/0x61d1e62c0893d48005d5d3f136b6d1eff9bfdd8b9d6516914f985d503351cf58) |
| D0: case-only change | 10 | accepted | [0x49afa482b4...](https://explorer-studio.genlayer.com/tx/0x49afa482b4111f8af225cf57ea25aca088aa3d4a88e5458575e03d6b1be81dd5) |
| D1: adverse amendment | 17 | accepted | [0x0ccf0f2e26...](https://explorer-studio.genlayer.com/tx/0x0ccf0f2e261e6e8524afeb98c314e8b46810c1fb6bd83eb2c4c364d0a14d8baf) |
| appeal D1 adverse revision | 12 | accepted | [0x9805cd9a35...](https://explorer-studio.genlayer.com/tx/0x9805cd9a353139ca46342eb96d0ff5da9fb40c9adcdaf510207b72375339e291) |
| expected rejection: appeal_revision | 6 | accepted | [0x1ad357d142...](https://explorer-studio.genlayer.com/tx/0x1ad357d142a2e111ef4e76ba7cd8297a3c5e8601e910fc299ea0b0deca43e359) |
| expected rejection: appeal_revision | 5 | accepted | [0x54ca241bc9...](https://explorer-studio.genlayer.com/tx/0x54ca241bc985e98cd8ef07abd89cb08f473db1a66d923f1a0560c0aaf80f6254) |
| expected rejection: appeal_revision | 5 | accepted | [0x6fdd41d714...](https://explorer-studio.genlayer.com/tx/0x6fdd41d71418b38f0fcc2f54f7621a61b1c14a6523ba8bba48eb7e75bc89f2de) |
| D1: revert to the original (by bob) | 17 | accepted | [0xd564a148ba...](https://explorer-studio.genlayer.com/tx/0xd564a148ba120fd9f9b75d8f365d2d390afa1b9bbf5ff4a0cf3d49cd5f8ba7d7) |
| D1: cosmetic edit | 16 | accepted | [0x740a753181...](https://explorer-studio.genlayer.com/tx/0x740a753181d0f3e622771581717f67499d7a5c7f8463ff1c99853658f8277044) |
| D2: injection attempt | 34 | accepted | [0x581af68365...](https://explorer-studio.genlayer.com/tx/0x581af68365a5ac1e9f224856e89dff450a55860261ad0a2a73f9179e32bb2409) |
| expected rejection: append_revision | 6 | accepted | [0xe006babdd8...](https://explorer-studio.genlayer.com/tx/0xe006babdd83d7b7521e4084a85a26575b0ba8cdc1a26de26117fa34e8cae7897) |
| expected rejection: append_revision | 6 | accepted | [0x95154bc804...](https://explorer-studio.genlayer.com/tx/0x95154bc8048e052f48d32ce56c41634d30ec3e591e61bc058d18f352ea6aac90) |
| D3: owner rewrite (every sentence reworded) | 16 | accepted | [0xe2a42be63f...](https://explorer-studio.genlayer.com/tx/0xe2a42be63f161fb4d86f98c042c7015581a32efc0f9c09acc78a4d7a3967bba8) |
