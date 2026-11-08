# Re-run without MongoDB: PostgreSQL + ClickHouse only (2026-10-20)

**Owner decisions 2026-10-20:**
- U01-U44 are all false: the MongoDB data itself is wrong, not the app.
- Re-run all three steps without MongoDB. Config only; one yes covers all three steps.
- Scoring rule (b): the two databases hold unrelated data, so every PostgreSQL<->ClickHouse candidate counts as false. MongoDB marks are dropped. Score only, with no app change.
- Same 40 questions and expected answers (none claims a PG<->CH link is true). These results are the new baseline.

**Code:** `verify/live-20261020` at `483e223` (Phase 6). **Configs:** `config/`, copied from `run-20261020` (DEC-19 settings, key id `run-20261020`).

## Step 1: collect, assemble, render, index (`step1-commands.txt`, `step1-candidates.json`)
- **Pipeline:** all commands exit 0 (collect PG 1.74 s, CH 2.75 s); 2 sources, 20 objects, 170 fields, 0 diagnostics.
- **Candidates:** 48 in total, of which 35 true and 13 false. All 13 false are PG->CH pairs (W-A 10 to `ch.Company.companyID`; W-B 3). 13 are cross-source; 17 carry `lower_bound_only`.
- **Not proposed:**
  - `ch.Location.companyID -> ch.Company.companyID`: type family, by design.
  - 4 `P4-ch` person links: these need the probe.
- **Ordinary-name links:** none.
- **Unchanged:** the same 48 pairs as the PG+CH subset of this morning's step 1.

## Step 2: ClickHouse probe (`step2-commands.txt`, `step2-candidates.json`)
- **Pipeline:** collect exit 0 in 2.67 s; assemble exit 0.
- **Candidates:** 56 in total, of which 39 true and 17 false. All 17 false are cross-source PG<->CH.
- **True pairs missing:** only the type-family pair above. The 4 `P4-ch` links are restored.
- **Unchanged:** the same 56 pairs as the PG+CH subset of this morning's probe snapshot.
- **F15:** no owner-true pair is cut, so F15 does not occur without MongoDB.

## Step 3: the 40 questions (`step3-answers.json`, `step3-context.json`, `step3-grading-sheet.md`)
- **Context sent to the model** (stub, no model call): identical to this morning's. 104 of 134 expected names; no `max_tokens`; `outcome` required; 638/638 relationships with source and namespace.
- **Run:** 59 min; every exit code 0; stderr empty; median 81 s, maximum 208 s.
- **Result codes:** answered 28, NOT_ESTABLISHED 9, NO_CATALOGUE_MATCH 2 (N-C4, N-C7, as expected), `MODEL_UNAVAILABLE_OR_INVALID` 1 (N-S8; it was NOT_ESTABLISHED this morning).
- **Changed since this morning:** N-S2 MODEL_INVALID_CITATION -> answered; N-S4 NOT_ESTABLISHED -> answered; N-S8 NOT_ESTABLISHED -> MODEL_UNAVAILABLE_OR_INVALID.
- **Bare verdicts (F17):** N-S5, N-I2, N-I4, N-I8 have empty text or only the outcome word (2 this morning).
- **Grades:** given by the owner in `step3-grading-sheet.md`.

## N-S2 recorded (owner yes; reply text kept local in `.tmp/`, not committed)
- One call, 75 s, `answered`: 6 citations, all allowed by the app; 737 characters.
- The morning's `MODEL_INVALID_CITATION` did not recur, so the cause is still unknown. The same input gives different citations from run to run.
