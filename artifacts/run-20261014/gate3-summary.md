# Step 6 and Gate 3: planner (2026-10-17)

Snapshot `snapshot-on` (sampling on, after the DEC-2 and L2 fixes). Graph `graph-on`: 82 reference edges (equal to the 82 candidates in `candidates-on.json`), 0 lineage edges (no observations given).

## join-paths (`join-paths-on.json`; marks from `candidates-on.json`)

| Join | Before (2026-10-06) | After | First path | Verdict |
|---|---|---|---|---|
| J1 pg `companyexperience` to `company` | `unavailable` | `found`, 20 paths | direct `companyid` to `companyid`, marked true | PASS (D3) |
| J2 pg `companyexperience` to `person` | `unavailable` | `found`, 1 path | direct `personid` to `personid`, marked true | PASS (D3) |
| J3 ch `Experience` to `Company` | 3 roundabout paths via pg on false #3, #13 | `found`, 1 path | direct ClickHouse join, marked true | PASS (DEC-4) |
| J4 ch `Company` to pg `company` | 1 path on false #13, no warning | `found`, 20 paths | false #13, carries `CROSS_SOURCE_CANDIDATES_UNVERIFIED` | PASS (DEC-4 warning); every J4 path uses at least one user-false edge |
| X1 pg `companyindustry` to `industryclassification` | `unavailable` | `found`, 1 path | direct `classificationid`, marked true | PASS (D3) |

J1's other 19 paths detour through `companyindustry.companyid` or `location.companyid` as parents (user marks U-ci, U-loc true). J4 paths 2-20 mix one false cross-source edge with true PostgreSQL edges; the warning is on path 1 only, because DEC-4 warns only when every hop is an unverified cross-source candidate.

## Plans and execution (Gate 3, user yes 2026-10-17)

`dbig-planner plan` for the first path of each join; `scripts/run_plan.py` read-only, no `--show` (`gate3-run-plan.json`). Hand counts by direct query (`gate3-handcount.json`).

| Plan | Status | run_plan rows | Hand count (same join, LIMIT 1000) | Full join (no limit) | Verdict |
|---|---|---|---|---|---|
| J1 | review_required, 1 sql_read | 1000 | 1000 | 1230 of 1230 child rows | PASS |
| J2 | review_required, 1 sql_read | 1000 | 1000 | 1229 of 1230 | PASS |
| J3 | review_required, 1 sql_read | 1000 | 1000 | 3,465,441 of 12,338,392 | PASS |
| X1 | review_required, 1 sql_read | 1000 | 1000 | 3939 of 3940 | PASS |
| J4 | unresolved, 2 source_read + logical_join | step 1: 1000; step 2: 0 (1000 keys bound); joined 0, unmatched 1000 | 0 of the first 1000 ClickHouse keys are in pg `company` | 0 shared distinct ids (pg 3940, ch 1,266,268) | PASS (count matches); the join itself is false, as marked |

Seconds (one run each, not a benchmark): J1 0.28, J2 0.18, J3 1.91, X1 0.17, J4 0.30. No errors, empty stderr; the ClickHouse code 497 of the last run did not recur.

Limitation: every same-database plan stops at LIMIT 1000, so run_plan alone cannot show the full join size. The hand count without a limit shows it.
