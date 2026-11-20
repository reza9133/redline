# Redline live end-to-end report

- Run: `mwors4j0`
- Network: GenLayer Studionet
- Contract: `0x9a879A8EB92b7BB3b3f4514E845d24E62e3177E7` ([explorer](https://explorer-studio.genlayer.com/contracts/0x9a879A8EB92b7BB3b3f4514E845d24E62e3177E7))
- Fixtures: https://raw.githubusercontent.com/reza9133/redline/main/e2e/fixtures
- Date: 2026-11-20T09:35:25.086Z
- Result: **PASS** — 39 passed, 0 warnings, 0 skipped, 0 failed in 13.8 min

HARD steps are deterministic contract behaviour; MODEL steps depend on real LLM validators or on the web renderer (a WARN there is an observation, not a contract fault).

| Step | Kind | Result | Detail | Time |
| --- | --- | --- | --- | --- |
| S01 Accounts created and funded | HARD | PASS | alice 0x8dd420E04daCB9BD2A249150e4A742d431928C11, bob 0x22cbeD207397C960D6B8314824f83DeB5d8e0c96 | 0.5s |
| S02 Fixtures are reachable and match the local files | HARD | PASS | base https://raw.githubusercontent.com/reza9133/redline/main/e2e/fixtures | 1.9s |
| S03 Contract deployed (or an existing one reused) | HARD | PASS | deployed 0x9a879A8EB92b7BB3b3f4514E845d24E62e3177E7 | 7.9s |
| S04 get_version is 1.x and get_limits matches the source constants | HARD | PASS | version 1.0.1 | 2.3s |
| R01 Rejected: private IPv4 literal | HARD | PASS | rejected (control call passes; node hides the reason) | 3.5s |
| R02 Rejected: cloud metadata address (on-chain: the reason is visible in the receipt) | HARD | PASS | on-chain: state unchanged; reason "url must be a public https:// URL (no private IPs, odd ports, or useri" | 44.3s |
| R03 Rejected: carrier-grade NAT | HARD | PASS | rejected (control call passes; node hides the reason) | 2.2s |
| R04 Rejected: hex-spelled loopback | HARD | PASS | rejected (control call passes; node hides the reason) | 1.2s |
| R05 Rejected: non-443 port | HARD | PASS | rejected (control call passes; node hides the reason) | 1.2s |
| R06 Rejected: userinfo in a fetchable URL | HARD | PASS | rejected (control call passes; node hides the reason) | 1.2s |
| R07 Rejected: plain http on a fetchable host | HARD | PASS | rejected (control call passes; node hides the reason) | 1.2s |
| R08 Rejected: localhost | HARD | PASS | rejected (control call passes; node hides the reason) | 1.2s |
| R09 Rejected: name too short | HARD | PASS | rejected (control call passes; node hides the reason) | 1.1s |
| R10 Rejected: unknown kind | HARD | PASS | rejected (control call passes; node hides the reason) | 1.2s |
| R11 Rejected: blank publisher | HARD | PASS | rejected (control call passes; node hides the reason) | 1.2s |
| R12 Rejected: prompt delimiters in the name | HARD | PASS | rejected (control call passes; node hides the reason) | 1.1s |
| H01 D0 registered: baseline revision exists | HARD | PASS | document #1 | 12.3s |
| C01 Immediate second revision is blocked by the cooldown | HARD | PASS | on-chain: state unchanged; reason "revision cooldown still active; try again in a minute" | 46.1s |
| H02 D2 and D3 registered | HARD | PASS | documents #2, #3 | 49.1s |
| H03 D1 registered: baseline hash chain recomputes | HARD | PASS | document #4, head fd4ae8a5e92f... | 26.0s |
| H04 Baseline text equals the fixture as rendered by the validators | MODEL | PASS |  | 0.0s |
| H05 Duplicate registrations are refused whatever the spelling | HARD | PASS | rejected (control call passes; node hides the reason) | 1.2s |
| H06 Lookup views: find_document, list_owner_documents, list_documents | HARD | PASS | 4 documents on the contract | 33.7s |
| G01 A page with too little text cannot be tracked | HARD | PASS | on-chain: state unchanged; reason "the page has too little readable text to track (at least 120 character" | 20.5s |
| I01 A page identical to the latest revision is refused | HARD | PASS | on-chain: state unchanged; reason "the page is identical to the latest recorded revision" | 38.4s |
| F01 A capitalisation-only change is recorded without a model verdict | HARD | PASS | revision #5 | 28.6s |
| M01 Adverse amendment is rated worse, with a verbatim quote, and flagged HIGH | MODEL | PASS | MUCH_WORSE/HIGH, quote "We may share your files and usage data with advert" | 52.8s |
| A01 Appeal by another account runs a fresh consensus round | MODEL | PASS | CONFIRMED | 18.8s |
| A02 A second appeal, the baseline and a format-only revision cannot be appealed | HARD | PASS | 3 refusals confirmed | 92.4s |
| M02 Reverting the amendment is rated better; the hash chain still recomputes | MODEL | PASS | MUCH_BETTER/MEDIUM | 31.7s |
| M03 Cosmetic edit (from the original text) is judged and stays quiet | MODEL | PASS | NEUTRAL/LOW | 60.3s |
| N01 A hostile page is flagged; the deterministic guard holds whatever the model does | MODEL | PASS | MUCH_WORSE/HIGH, silent false | 51.2s |
| P01 A non-owner cannot point the document at another site | HARD | PASS | on-chain: state unchanged; reason "only the document owner may point it at a different site (web.archive." | 17.7s |
| P02 A non-owner cannot commit a full rewrite | HARD | PASS | on-chain: state unchanged; reason "too little of the previous text survives in this page; only the docume" | 51.7s |
| P03 The owner may commit a quiet rewrite; it is flagged and never LOW | HARD | PASS | NEUTRAL/MEDIUM, retained 1208bp, churn 697bp | 59.8s |
| V01 Pagination and unknown ids behave | HARD | PASS | 4 revisions on D1 | 10.4s |
| C02 Contract-wide counters equal a recount from the stored records | HARD | PASS | 4 documents, 10 revisions | 33.5s |
| C03 Every document's hash chain recomputes | HARD | PASS | 10 revisions verified | 18.4s |
| C04 This run changed the totals as expected | HARD | PASS | +4 documents, +10 revisions | 1.2s |

## Transactions

| Label | Seconds | Result | Link |
| --- | --- | --- | --- |
| deploy | 0 | accepted | [0xc2c6f58378...](https://explorer-studio.genlayer.com/tx/0xc2c6f58378b6eeb03ccc7fc7908c40a7ebf0c6027414982de3d0f1b74a6ae760) |
| expected rejection: register_document | 41 | accepted | [0x16771fa306...](https://explorer-studio.genlayer.com/tx/0x16771fa30631c358fdd291e085ef4231faf769c9f85fda8bbcaa3bba48379eb9) |
| register: D0 cooldown and format-only | 5 | accepted | [0x2af3276bc1...](https://explorer-studio.genlayer.com/tx/0x2af3276bc132e632123ff62d9335801358a0c034969ee0905be274bb37e20bf7) |
| expected rejection: append_revision (cooldown) | 0 | accepted | [0xe67763e3ff...](https://explorer-studio.genlayer.com/tx/0xe67763e3ff8d52d0632b03011b82426a4aa388a97a5dea95483512a3dc134c0c) |
| register: D2 injection attempt | 5 | accepted | [0x5214910dac...](https://explorer-studio.genlayer.com/tx/0x5214910dac8734dc712af2f143ca176f67c1ffd624bff6ada30e40094dc33836) |
| register: D3 site and rewrite guards | 35 | accepted | [0x8b09133ca3...](https://explorer-studio.genlayer.com/tx/0x8b09133ca3de7cfff65c01bba1d452450ebbc80b5eced0973f850c874e9eec30) |
| register: D1 main amendment chain | 10 | accepted | [0xc789e81f06...](https://explorer-studio.genlayer.com/tx/0xc789e81f068e53b41976e8488ed33b54cef3462dcc13684b95e10cc3cd0999ee) |
| expected rejection: register_document | 10 | accepted | [0x9fc5c3afc9...](https://explorer-studio.genlayer.com/tx/0x9fc5c3afc912dac816ca1ac0af52e93cd335c8b29ecb466f06e08e3e35de9d7c) |
| expected rejection: append_revision | 6 | accepted | [0x790602b4f2...](https://explorer-studio.genlayer.com/tx/0x790602b4f2539696a8768764a6c4e2eb00faee578f81ffb35d626ed47184aad9) |
| D0: case-only change | 10 | accepted | [0xe58c73754a...](https://explorer-studio.genlayer.com/tx/0xe58c73754a8c3a36d43d46fbe39e686b053c8daeb0a99c42ce58e781d49a58c4) |
| D1: adverse amendment | 11 | accepted | [0x54c0ee2dd5...](https://explorer-studio.genlayer.com/tx/0x54c0ee2dd5e39fa66718d12555dadc10a51d22e06b333cbc3075d1dfa96b1497) |
| appeal D1 adverse revision | 12 | accepted | [0xc388f594d1...](https://explorer-studio.genlayer.com/tx/0xc388f594d1903a6fc6b229d2fdc0d4b115226e93d5d4b469f98e45a58ec277fc) |
| expected rejection: appeal_revision | 6 | accepted | [0xf298aae903...](https://explorer-studio.genlayer.com/tx/0xf298aae903092895b91ec38e51e07f5cfd148d8aaae9823b499b9b927b97afa8) |
| expected rejection: appeal_revision | 5 | accepted | [0xb6375e596d...](https://explorer-studio.genlayer.com/tx/0xb6375e596df52c16ec30cd6d9949012589e777809b932067184ec533a4987088) |
| expected rejection: appeal_revision | 6 | accepted | [0x3a9b4d6e1c...](https://explorer-studio.genlayer.com/tx/0x3a9b4d6e1c8efab51c48efead464b1f0daaa88b887c50af93562107a3ac3f031) |
| D1: revert to the original (by bob) | 16 | accepted | [0x614cca2dad...](https://explorer-studio.genlayer.com/tx/0x614cca2dad2f75f40d342a11a6f888634fa20f901c769142cff32f5d7cc0b953) |
| D1: cosmetic edit | 10 | accepted | [0x1875464fb5...](https://explorer-studio.genlayer.com/tx/0x1875464fb53e0c7e4eab6765c1ad8e227046ca65a54f66a7badcad0f756b7002) |
| D2: injection attempt | 39 | accepted | [0x44c6a27550...](https://explorer-studio.genlayer.com/tx/0x44c6a275502c6eaa6caa9ba111941a0f688fbc45cf1c01c09b1d192955125063) |
| expected rejection: append_revision | 6 | accepted | [0xc96278f037...](https://explorer-studio.genlayer.com/tx/0xc96278f037b2969222bc53ddefc85d7520d2b5105ea64c4547e08f3e42a0611e) |
| expected rejection: append_revision | 12 | accepted | [0x7f42e6af6a...](https://explorer-studio.genlayer.com/tx/0x7f42e6af6a40faf26536ee0e51e9be6cd3497b26cd6badd5587587423cdcd555) |
| D3: owner rewrite (every sentence reworded) | 16 | accepted | [0xc5e495a7a7...](https://explorer-studio.genlayer.com/tx/0xc5e495a7a7f0d5280021b8e38db3f073408cf9a7125a6eaa0dc9e89e3f114084) |
