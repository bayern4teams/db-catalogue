# Live verification of the catalogue fixes: brief for the lead

**What was tested.** The cloud routine's fixes (`feat/catalogue-fixes`), run against the real PostgreSQL 16.15, ClickHouse 26.8.2.7, MongoDB 7.0.41 and the private model server (`Qwen3.8-Flash-Next`), 2026-10-14 to 2026-10-18.

**How.** Every check was proved by an independent route (direct queries, file inspection, a wrapper around the model client), not by the app's own messages. Every true/false judgement on a relationship or an answer is the owner's, or was graded against expected answers the owner approved.

**Where things are.**
- **Branch:** `verify/live-20261014` (pushed). CI passed: run 39128510898, all 8 jobs. Nothing is merged and no PR is open.
- **Full result:** [`../LIVE_VERIFY_REPORT.md`](../LIVE_VERIFY_REPORT.md), covering every step, before against after, limitations, and the send-back template.
- **Proposed fixes for what is still open:** [`FIX_PROPOSALS.md`](FIX_PROPOSALS.md), with evidence, root cause, options, pros and cons, recommendation, and sources.
- **Raw evidence:** `artifacts/run-20261014/` (counts and names only):
  - Gate summaries: `gate1-summary.md`, `gate2-summary.md`, `gate3-summary.md`, `gate4-summary.md`.
  - Candidate tables with the owner's marks: `candidates-*.json`.
  - Model answers and grades: `ask-*.json`, `q40-*.json`.
  - Review measurements: `review-lv-*.json`.

## Key findings
1. **The planner and search fixes work.**
   - J1, J2 and X1 went from "no path" to a direct path; J3 now offers the direct ClickHouse join; J4 carries the new warning.
   - Planner row counts equal hand-written queries.
   - Search finds camelCase parts and plurals (revenue 0 → 25 hits), and the REST POST route and `--no-access-log` work.
2. **`ask` works with this model only after two limits were made optional.**
   - **Before:** 0 of 20 answered.
   - **After:** Right 8, Partial 9, NOT_ESTABLISHED 1, Wrong 2.
   - **40 new owner-approved questions:** Right 17, Partial 13, Wrong 6. All 10 direct questions are Right; the comprehensive ones are weakest.
3. **Sampling-based overlap drops real relationships.**
   - 12 true relationships were dropped as "zero overlap", although their real overlap is 95-100%.
   - The cause is structural, not a threshold: both sides are 100-row first-row samples, and a partial parent is treated as complete.
   - Calculated: a true reference with 17 child values is dropped about 65% of the time.
4. **The model mixes the two databases.** Relationship entries reach the model with bare table names and no source. The model then built joins between PostgreSQL and ClickHouse, which share 0 ids. This explains 4 of the 6 Wrong answers in the new set (N-S5, N-C10, N-I1, N-I3), plus I1 of the first 20.
5. **ClickHouse produces no value tokens.** No ClickHouse relationship ever gets an overlap number; LOCAL_VERIFY assumed it would.
6. **The cardinality probe helps on PostgreSQL + ClickHouse** (+8 candidates, all judged by the owner, 0 lost). On MongoDB it creates false parents and pushes out true ones.

## Fixed on the verify branch (owner-approved, each with a test that fails without it)
| # | Defect found live | Fix | Commit |
|---|---|---|---|
| F1 | ClickHouse 26.8 writes a single key as `(companyID)`; 0 keys were read | strip one outer pair of parentheses | `8ed4c87` |
| F2 | an ordinary name (`freeMails`) linked to a weak ClickHouse key without any overlap | the overlap rule also covers weak-key parents | `444ff9c` |
| F3 | fixed `max_tokens`: 14 of 20 answers were cut off (`MODEL_TRUNCATED`) | `max_tokens` optional; unset sends none | `309e6bc`, CI type fix `0785ad1` |
| F4 | answers over 2000 characters were rejected and misreported as a server error | `max_answer_chars` optional (unset = no limit), new code `MODEL_ANSWER_TOO_LONG` | `7704a97` |

Tests: 757 passed, 69 skipped, 1 xfailed; the attack files still show only the known 37 failures.

## Still open (owner decides; details and options in FIX_PROPOSALS.md)
| # | Problem | Recommended fix | Why that one |
|---|---|---|---|
| F5 | true relationships dropped when sampling is on | read small parents completely; never drop against a partial parent | keeps true pairs **and** still drops false ones; small change |
| F6 | known-wrong candidates remain, known-right ones dropped | none of its own | follows from F5 and F7 |
| F7 | no ClickHouse value tokens | add ClickHouse tokenisation, after F5 | reuses tested code; after F5, or ClickHouse inherits the false drops |
| F8 | the shipped example review config times out | make the review's `max_tokens` optional, like F3; example timeout 900 | same rule as the owner chose for `ask` |
| F9 | the probe reads up to 2.3× its row cap on ClickHouse | correct the comment and docs | counts only, nothing leaks; cheapest honest fix |
| F10 | with MongoDB the probe makes false parents and pushes out true ones | whole-table uniqueness for parents, stricter rule for ordinary names, same-source parents ranked first | three small rules, each aimed at one measured failure |
| F11 | the model joins PostgreSQL and ClickHouse | put the source and namespace on each relationship entry, plus one prompt rule | gives the model the missing fact at the root |
| F12 | the answer text says "not established" while the outcome says answered | make `outcome` required in the schema | the server enforces the schema |
| F13 | search misses needed columns; type questions find nothing | include a table's columns with it; index types as text | targets 8 of the misses directly |

**Suggested order:** F5 → F7 (they belong together), F11, F13, F10, F8, F12, F9.

## Limits of this verification
- **Sampling** is first rows, not random.
- **Model answers vary between runs;** each grade is of one run.
- **The model server was shared** and dropped three times, so seconds include queueing and are not a benchmark.
- **MongoDB was left out** of the probe step and the 40 new questions, by owner decision.
- **No query logs** were available, so lineage observation was not tested.
- **Never proven:** overlap for any ClickHouse pair, and uniqueness across a whole large table.

**Incident:** on 2026-10-14 three `.env` values were echoed into a session transcript, because unquoted lines failed under zsh. The owner regenerated them, and `.env` has since been loaded only under bash with output discarded.
