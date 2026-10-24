# Phase 7 handoff — the state that survives a session

Overwrite at every commit. A fresh session reads this first, then [the plan](../PHASE7_LOCAL_PLAN.md).

**Branch:** `fix/phase7` (from `verify/live-20261020` at `30eb6ce`, DEC-28) · local only, never merged
**Order (plan §5):** P7-0 → P7-8 → P7-1 → P7-4 → P7-2 → P7-3 → P7-5 → P7-6 → P7-7

## Items

States: `todo` · `in-progress` · `blocked` · `done` (acceptance tests pass and the regression gate is green).

| item | finding | owner files | state | commit | lead verdict |
|---|---|---|---|---|---|
| P7-0 research record | F11-b, benchmark | `artifacts/run-20261020-pgch/research-f14-f17.md` | done | 30eb6ce | setup session |
| P7-8 value-free failure `reason` | F18 | requester `answer.py` | done | (this commit) | lead: `finish_reason` restricted to the OpenAI set (else `other`) after the attack showed server text passing through; old exact `error == {"code"}` pins changed to `error["code"]` |
| P7-1 "at least" wording + Wilson CI, `basis` | F14 | builder `builder.py`, `config.py`; requester `search.py` index | done | (this commit) | lead: REJECT fixed (floor/ceil, "<1%"/">99%", count when floor is 0); dead branch removed; 29 acceptance + 7 attack pass; ponytail-review ship |
| P7-4 `checked` field, `MODEL_ANSWER_UNEXPLAINED`, retry | F17 | requester `answer.py`, `config.py`, test stubs | done | (this commit) | lead: attack ACCEPT; fixed a `checked` of blank strings counting as explained, and the attempt count now reaches the span on success too (one span update in `finally`); DEBUG-level SDK logging of the re-sent reply is pre-existing (the question was already logged there) |
| P7-2 measured evidence ranks above unmeasured | F15 | builder `builder.py` | done | (this commit) | lead: offline replay (no database): the 2 cut owner-true `-> pg.location` pairs restored; 9 owner-false pairs to an unmeasured CH weak key replaced by 7 unmarked PG -> Mongo pairs at ~0.6% and the 2 restored; PG+CH sets unchanged (48/48, 56/56); `attack6_f10` same-source pin updated to DEC-32 (plus a forbidden-source count) | | |
| P7-3 bridge-table paths (BFS) | F16 | requester `search.py`, `answer.py` | tests ready (28; 20 fail on old code); worker after P7-4 | | |
| P7-5 cross-source control A + B (+ E via DEC-3, DEC-34) | F11-b | builder, requester config, planner | todo (DEC-34..36 answered) | | |
| P7-5 option C value-free profiles | F11-b | collectors, builder | todo (after A+B+E) | | |
| P7-5 option D coordinated hash sampling | F11-b | collectors | deferred (DEC-23) | | |
| P7-6 regression gate + live re-check, 3 repeats | all | evidence only | blocked: databases down (owner 2026-10-24); owner yes per run | | |
| P7-7 general benchmark | all | `/home/bayern/Projects/benchmark/dbcat-bench/` | todo (owner yes per compose file) | | |

## Decisions (owner answers 2026-10-20, plan §8)

| id | question | answer |
|---|---|---|
| DEC-23 | F11-b: which options? | A + B + E now; C next; D later. B: `c_min` 0.25, `min_expected_shared` 5 (config, documented defaults). |
| DEC-24 | F16 limits | `path_max_hops` 4 (same as the planner), `path_max_bridge_tables` 4 (config). |
| DEC-25 | Benchmark | Yes. One compose project per dataset in `/home/bayern/Projects/benchmark/dbcat-bench/<dataset>/compose.yaml`, own container and named volume, local images only, ports on 127.0.0.1, read-only `dbig_reader`. Layout: pagila, chinook -> PG; tpch-sf1 split (customer, part, supplier, nation, region -> PG; orders, lineitem, partsupp -> CH); gleif -> CH; sirene -> PG; enhetsregisteret -> MongoDB; negatives nyc-taxi -> CH, raha-hospital -> PG, raha-beers -> MongoDB. Each compose file shown to the owner before it is written or started. |
| DEC-26 | Other running containers | Not included. |
| DEC-27 | F17 thinking | Stays on. |
| DEC-28 | Base of `fix/phase7` | `verify/live-20261020`. |
| DEC-29 | Repeats in P7-6 | 3 per question. |
| DEC-30 | Push `4ae2e85` | Pushed. |
| DEC-31 | N-S8 | Recorded once: NOT_ESTABLISHED with 6 allowed citations; the earlier `MODEL_UNAVAILABLE_OR_INVALID` did not recur and its cause is not recorded (-> F18, P7-8). |
| DEC-32 | F15 tie order | Declared keys first, then evidence tier. |
| DEC-34 | P7-5 E: add `allowed_source_pairs`, or reuse DEC-3 `source_groups` / `forbidden_source_pairs` which already express it? (asked 2026-10-24) | Reuse DEC-3: no new key; document it and add one test that blocks PG<->CH by config (owner 2026-10-24). |
| DEC-35 | P7-5 B on ClickHouse with the probe off: add one `uniqCombined` per key column in the collector? (asked 2026-10-24) | No: probe only. B applies where a distinct estimate exists; A's tier covers the rest. Owner also noted: the databases are not up for now (owner 2026-10-24). |
| DEC-36 | P7-5 A: how does the decisions file name a column? (asked 2026-10-24) | Qualified names `<source_id>.<namespace>.<table>.<column>` (namespace omitted when absent); an unmatched entry is a warning, count only (owner 2026-10-24). |
| DEC-37 | P7-5 A: does `verified` need a minimum containment? The P7-2 replay shows 7 PG -> MongoDB pairs at ~0.6% on a complete parent (asked 2026-10-24) | Yes: `verified` needs measured containment >= `c_min` (0.25) on a complete parent; below it cross-source is `unverified_cross_source`, same-source `plausible`. No new setting (owner 2026-10-24). |
| DEC-33 | P7-2: overlap now runs before the top-k cut, so `dropped_zero_overlap_candidates` can rise (kept set unchanged). Which count? (asked 2026-10-24) | Count all zeros, including those below the cut (owner 2026-10-24). |

## Open owner questions

(none yet)

## Next action

P7-3 worker (requester `search.py`, `answer.py`), one agent at a time; then P7-5 A + B (DEC-34..37).

## Log

- 2026-10-24: P7-2 and P7-4 done. Gate (memory-capped, lead alone): 1061 passed, 70 skipped, 1 xfailed; attack files the same 37 ids; ruff, mypy, ci_check, package_isolation clean; 0 corpus names.

- 2026-10-24: the machine ran out of memory with two workers running full suites in parallel; owner: one at a time, leave RAM free. Agents now run targeted tests only, under a 3 GB cap; the lead runs the full suite alone.

- 2026-10-24: P7-8 and P7-1 done. Gate: 1011 passed, 70 skipped, 1 xfailed; attack files the same 37 ids; ruff, mypy, ci_check, package_isolation clean; 0 corpus names.
- 2026-10-24: `fix/phase7` created from `verify/live-20261020` at `30eb6ce`; this file written from plan §4 and §8.
