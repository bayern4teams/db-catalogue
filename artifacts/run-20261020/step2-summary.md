# Step 2: the cardinality probe snapshot (2026-10-20)

**Run.** Owner yes 2026-10-20. ClickHouse and MongoDB were collected with `cardinality_probe = true` plus the step 1 settings (`config/*-probe.toml`), then assembled at the default `top_k_per_field = 5`, with MongoDB (`snapshot-probe`) and without it (`snapshot-probe-nomongo`).

**Evidence:**
- `step2-commands.txt`
- `step2-rowcap.json`: my read-only re-run of the app's probe query shape
- `step2-candidates-probe.json`, `step2-candidates-probe-nomongo.json`
- `step2-unmarked.md`

## Results

| Check | Before | After |
|---|---|---|
| Pipeline | complete | PASS: all four commands exit 0; 0 diagnostics |
| Probe `sample_count` | up to 229,376 against a cap of 100,000 | **PASS**: exactly `min(100000, table rows)` for all 46 ClickHouse and 295 MongoDB probe profiles |
| Rows ClickHouse reads per probe | 7.8× the cap (2026-10-19) | **PASS, 1.10× to 1.31×**. `system.query_log` is closed to `dbig_reader` (code 497), so this was measured by re-running the app's query shape, counts only |
| The 3 pairs cut on hash order | lost | **PASS**: `ch.Experience.companyID` and `ch.CompanyIndustry.companyID -> ch.Company.companyID` present in both snapshots; Z4 present with MongoDB |
| Ordinary-name links (30 MongoDB pairs, `freeMails`) | linked | **PASS**: none |
| The original 21 | n/a | PASS: no known-wrong present; all 5 known-right present (with MongoDB) |
| MongoDB-MongoDB true pairs | 5 of 9 | 5 of 9 (same five), no change |
| Candidates with MongoDB | 162 (84 unmarked) | 134: 68 true, 22 false, **44 unmarked** (`step2-unmarked.md`) |
| Candidates without MongoDB | 67 | 56: 39 true, 17 false, 0 unmarked |

## Missing owner-true pairs with MongoDB (7)
- **By design (5):**
  - `ch.Location.companyID` and `mongo.zoominfo_location.companyID` are integers against string parents (type family).
  - `zoominfo_contracts.company_id` and `zoominfo_evidence.company_id -> zoominfo_Company.companyID` fail the name rule. Only `ch.Company` matches, by table name.
  - `zoominfo_industry.industryID` is not unique (41 distinct values in 261 documents).
- **New finding F15 (2):** `mongo.diffbot_CompanyExperience.companyID -> pg.location.companyid` and `mongo.diffbot_CompanyInvestmentRound.companyID -> pg.location.companyid`.
  - Both were present in step 1 and both have a **measured containment of 100%**, yet they are cut at `top_k_per_field = 5`.
  - The slot goes to `ch.Company.companyID`, an unmeasured weak key (`sample_too_small`) whose real overlap is 0.
  - **Cause:** the P6-4 tie-break orders declared key, then weak key claim, then measured uniqueness, and never looks at measured containment.
  - Shown with a local rebuild at `top_k_per_field = 100`; no database access.
