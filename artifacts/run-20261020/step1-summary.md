# Step 1: Gate 2 with ClickHouse value hashes and the DEC-19 settings (2026-10-20)

**Run.** Branch `verify/live-20261020` from `fix/live-findings` at `d06dc25`; owner yes 2026-10-20.

**Settings** (`config/`): `sample_rows = 25000`, `max_value_tokens = 4096`, MongoDB `max_sample_bytes = 16777216`; the existing `DBIG_TOKEN_KEY` (owner choice) with key id `run-20261020`.

**Evidence** (counts and names only):
- `step1-commands.txt`
- `step1-candidates.json`: the snapshot's candidates against the owner's marks
- `step1-recount.json`: my own read-only whole-column re-count of 39 marked pairs

## Results

| Check | Before (2026-10-17, 100 rows) | After |
|---|---|---|
| Pipeline | complete | PASS: all six commands exit 0; 0 diagnostics in all three bundles; `complete`, `partial_reasons` none; collect 1.76 / 2.70 / 1.28 s |
| Value-hash sets | PostgreSQL 106, MongoDB 450, **ClickHouse 0** | PASS: PostgreSQL 106 (all complete), MongoDB 450 (449 complete), **ClickHouse 38** (3 complete: `Industry`, 37 rows) |
| The 16 known-wrong (#1-#21 false) | 9 of 16 gone | **PASS: 16 of 16 gone** |
| The 5 known-right | 3 of 5 kept | **PASS: 5 of 5 kept** (#2, #7, #8, #11, #12) |
| Owner-true pairs dropped by a measured zero | 12 | **PASS: 0** |
| Re-count vs snapshot | n/a | PASS: for all 39 marked pairs, real containment ≥ 50% ⇔ present in the snapshot; 0 numbers changed since 2026-10-19 |
| `freeMails` and ordinary-name links | `freeMails` fixed by F2 | PASS: none (`dropped_ordinary_name_candidates` 3) |
| Counters | `dropped_zero_overlap` 25 | `dropped_zero_overlap_candidates` 26, `dropped_ordinary_name_candidates` 3 |
| Candidates | 82 (49 true, 33 false) | **79 (61 true, 18 false, 0 unmarked)** |
| ClickHouse pairs with a number | none | PASS: 29 pairs carry `value_containment` |

## Expected limits seen
- **18 false candidates remain**, all with a ClickHouse parent: 14 `-> ch.Company.companyID`, 4 `-> ch.Person/Location`. Real overlap is 0 (`lead-review-overlap.json`). The ClickHouse parent is read for 25,000 of up to 11.8 M rows, so the zero is labelled "parent sampled, a zero is not a measurement" and does not drop (DEC-19 by design; `LEAD_REVIEW.md` F5 "Limits that remain").
- **14 owner-true pairs are not proposed.** None of them was dropped by the zero rule:
  - 9 MongoDB-MongoDB and 4 `P4-ch` pairs need probe uniqueness (step 2);
  - `ch.Location.companyID -> ch.Company.companyID` crosses type families (`Nullable(Int64)` vs `String`), by design since Gate 1.

## New finding F14: a lower bound is shown as a measurement
- **What it says.** `ch.Experience.companyID -> ch.Company.companyID` reads "value overlap 1% of 2702 sampled child values".
- **What is true.** The real containment is 25% (`lead-review-overlap.json`). The evidence metrics say `lower_bound_only: true, parent_complete: false`, but the sentence shown on the page and passed to the answering model does not.
- **Scope.** Every ClickHouse-parent pair with a non-zero overlap shows a low number this way.
- **Consequence.** A reader, or the model, may conclude that the two columns barely overlap.
