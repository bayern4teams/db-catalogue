# Gate 1 (metadata only, sampling off), run 2026-10-17 on 3d1505f

Counts and names only. Raw: gate1-commands.txt, direct-counts.json, pg-stats-id-columns.txt, candidates-off.json.

| check | result | evidence |
|---|---|---|
| collect pg / ch / mongo | PASS, exit 0, 0.25 / 0.29 / 0.71 s | gate1-commands.txt |
| assemble | PASS, `complete`, `diagnostics` null, `partial_reasons` null, 1.56 s | manifest in gate1-commands.txt |
| render | PASS, 3 sources, 48 objects, 1035 fields, 0 declared FKs, 71 candidates, diagnostics 0, 1.17 s | |
| index (version 6) | PASS, exit 0, 0.58 s | |
| counts vs direct driver queries | PASS: PG 13 tables / 124 columns, per-table equal; CH 7 / 46, per-table equal; Mongo 28 collections, names equal, 0 views; 0 FK/PK/UNIQUE, 0 comments in PG | direct-counts.json |
| "values not compared" on every candidate (A1/D2) | PASS, 71 of 71 (`value_overlap_not_measured`, reason `no_value_tokens`) | candidates-off.json |
| D1b empty statistic cells | PASS, 0 blank of 4140 cells: 2715 "not provided by this source", 24 "not computed", 10 "all values null" (5 all-NULL PG columns) | catalog-off.html |
| C2 | complete snapshot: `partial_reasons` absent, render diagnostics 0 = page; the partial path not exercised here | |
| D3 / DEC-12 | PASS for marked pairs: all 12 true PG pairs proposed (J1, J2, X1 edges exist), M9/M10 not proposed | |
| DEC-1 | PASS: #9, #20 (`locationID` -> `location.companyid`) gone; no `locationid` candidate (`location.locationid` n_distinct -0.9826) | pg-stats-id-columns.txt |
| L2 / DEC-16 | PASS: no ordinary shared name linked (rules: key_name 69, suffix 2); `dropped_unmeasured_key_name_candidates` 2 | runs.jsonl |
| **DEC-2 weak ClickHouse key** | **FAIL**: 0 constraints in the ClickHouse bundle, 0 `weak_key_claim` evidence. ClickHouse 26.8.2.7 reports single keys with parentheses (`(companyID)`); `runtime.py:675` strips spaces and backticks only, so `_PLAIN_COLUMN` never matches | direct-counts.json `ch_keys`, bundle-ch-off/constraints.jsonl (0 lines) |
| consequence of DEC-2 | the 6 CH-CH pairs and Z1-Z11 cannot be proposed (no CH parent); the 10 Mongo-Mongo pairs are also missing (Mongo has no measured parents without the probe) | candidates-off.json `missing_marked` |
| candidates vs marks | 71 = 18 marked true (5 of #2/#7/#8/#11/#12, 12 PG, M26), 14 marked false (#1, #3-#6, #10, #13-#19, #21), **39 unmarked**; 27 marked-true pairs missing | candidates-off.json |
| unmarked 39 | 19 with parent `pg.location.companyid`, 19 with parent `pg.companyindustry.companyid` (both n_distinct -1 in pg_stats, so DEC-12 key_name makes them parents of every `companyid`), 1 `mongo.diffbot_CompanyInvestmentRound.investmentID` -> `pg.companyinvestmentround.investmentid` | |

## After the DEC-2 fix (commit 8ed4c87), same metadata-only scope

Raw: gate1-fix-commands.txt, candidates-off-fix.json (marks include the user's 2026-10-17 batch answers).

| check | result |
|---|---|
| pipeline | PASS: collect 0.18 / 0.51 / 0.76 s, assemble `complete` 0.97 s, render 106 candidates, 0 diagnostics, index 0.6 s |
| DEC-2 weak key claims | PASS: 5 claims (`Company.companyID`, `Industry.industryID`, `Location.locationID`, `Person.personID`, `PersonContact.freeMails`, all `primary_key`); composite keys (CompanyIndustry, Experience) skipped as designed; 35 candidates cite `weak_key_claim` |
| "values not compared" | PASS: 106 of 106 |
| Z1-Z11 | PASS: 11 of 11 proposed (0 before the fix) |
| CH-CH (6 true) | 5 of 6; `Location.companyID` (`Nullable(Int64)`) -> `Company.companyID` (`String`) not proposed: different type families, by design |
| Mongo-Mongo (10 true) | 0 of 10: no MongoDB parent without the cardinality probe (L1, Gate 4) |
| M9, M10, #9, #20 (false) | not proposed |
| marked candidates | 61 true, 26 false proposed; 19 new unmarked (ClickHouse weak parents with PostgreSQL / diffbot children, `freeMails`) |
| marks complete (user batches 2026-10-17) | before fix: 71 = 45 true, 26 false; after fix: 106 = 61 true, 45 false, 0 unmarked |
| **L2 / DEC-16 on weak-key parents: FAIL** | `mongo.zoominfo_contact_information.freeMails` -> `ch.PersonContact.freeMails` (user: false) links by `key_name` on an ordinary name: DEC-16 requires overlap only for *measured*-unique parents, not for a weak ClickHouse key claim |
| new false links from the fix | 19 (user: all false): 14 diffbot/PostgreSQL `companyid` -> `ch.Company.companyID`, 3 `personid` -> `ch.Person.personID`, `pg.location.locationid` -> `ch.Location.locationID`, `freeMails`; with sampling off only "values not compared" separates them |
| after the L2 weak-key fix (re-assembled, no database access) | 105 candidates = 61 true, 44 false; `freeMails` dropped; `dropped_unmeasured_key_name_candidates` 3 (gate1-l2-assemble.txt, candidates-off-l2.json) |
