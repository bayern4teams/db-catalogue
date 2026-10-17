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
