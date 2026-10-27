# Phase 7 handoff — the state that survives a session

Overwrite at every commit. A fresh session reads this first, then [the plan](../PHASE7_LOCAL_PLAN.md).

**Branch:** `fix/phase7` (from `verify/live-20261020` at `30eb6ce`, DEC-28) · local only, never merged
**Order (plan §5):** P7-0 → P7-8 → P7-1 → P7-4 → P7-2 → P7-3 → P7-5 → P7-6 → P7-7

## Items

States: `todo` · `in-progress` · `blocked` · `done` (acceptance tests pass and the regression gate is green).

| item | finding | owner files | state | commit | lead verdict |
|---|---|---|---|---|---|
| P7-0 research record | F11-b, benchmark | `artifacts/run-20261020-pgch/research-f14-f17.md` | done | 30eb6ce | setup session |
| P7-8 value-free failure `reason` | F18 | requester `answer.py` | done | 3d8a033 | lead: `finish_reason` restricted to the OpenAI set (else `other`) after the attack showed server text passing through; old exact `error == {"code"}` pins changed to `error["code"]` |
| P7-1 "at least" wording + Wilson CI, `basis` | F14 | builder `builder.py`, `config.py`; requester `search.py` index | done | 3d8a033 | lead: REJECT fixed (floor/ceil, "<1%"/">99%", count when floor is 0); dead branch removed; 29 acceptance + 7 attack pass; ponytail-review ship |
| P7-4 `checked` field, `MODEL_ANSWER_UNEXPLAINED`, retry | F17 | requester `answer.py`, `config.py`, test stubs | done | 7807328 | lead: attack ACCEPT; fixed a `checked` of blank strings counting as explained, and the attempt count now reaches the span on success too (one span update in `finally`); DEBUG-level SDK logging of the re-sent reply is pre-existing (the question was already logged there) |
| P7-2 measured evidence ranks above unmeasured | F15 | builder `builder.py` | done | 7807328 | lead: offline replay (no database): the 2 cut owner-true `-> pg.location` pairs restored; 9 owner-false pairs to an unmeasured CH weak key replaced by 7 unmarked PG -> Mongo pairs at ~0.6% and the 2 restored; PG+CH sets unchanged (48/48, 56/56); `attack6_f10` same-source pin updated to DEC-32 (plus a forbidden-source count) |
| P7-3 bridge-table paths (BFS) | F16 | requester `search.py`, `answer.py`, `config.py` | done | 6c00681 | lead: attack ACCEPT, D1 (strongest path first) and D2 (lines through kept tables at a full cap) fixed, 35 attack + 28 acceptance pass; real snapshot N-S4/N-S8/N-I10 3/3 expected tables in context (2/3 search off); P7-5 A must filter cross-source path edges too | | |
| P7-5 cross-source control A + B (+ E via DEC-3, DEC-34) | F11-b | builder, requester config, planner | done | fefe6ef | lead: attack ACCEPT; 4 defects fixed (qualified-name ambiguity, unrounded tier, `decisions_file_max_bytes`, BOM); DEC-39 B trusts only random/hash samples (dormant today); offline replay PG+CH: 0 cross-source candidates reach the model or planner (13 and 17, all `unverified_cross_source`), 0 owner-true lost (35/35, 39/39) | | |
| P7-5 option C value-free profiles | F11-b | collectors, builder | done | a371f35 | lead: attack REJECT fixed (F1 privacy whitelist, F2 identifiers skip, F3 budget never costs the bundle, F5 `formats_truncated`, F6 `values_complete`, F4 arrays, `ranges.jsonl` only with rows); real SQL unverified until the databases are up | | |
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
| DEC-38 | P7-5 A: does a lower bound (partly read parent) that already reaches c_min count as `verified`? (asked 2026-10-24) | Yes: a lower bound >= c_min is `verified`; below it `plausible` (same source) or `unverified_cross_source` (owner 2026-10-24). |
| DEC-39 | P7-5 B assumes a random parent sample, but partly read parents are read as `first_rows_limit`; which samples may B trust? (asked 2026-10-25) | Random or hash-coordinated samples only, never first-rows/first-documents reads (owner 2026-10-25). Today no collector emits such a sample, so B is dormant until option D; A's tier still filters. |
| DEC-40 | Start P7-5 option C while the databases are down? (asked 2026-10-25) | Yes: fixtures now, live checks with P7-6 (owner 2026-10-25). |
| DEC-41 | Push `fix/phase7` and run CI? (asked 2026-10-25) | Yes (owner 2026-10-25): pushed at `fefe6ef`, CI run 40171751091. |
| DEC-42 | Option C rule and default (asked 2026-10-25) | Reject when the child's dominant format covers >= `format_reject_share` (0.95) of its values and is absent from the parent's formats, or the length ranges don't overlap, or the numeric ranges don't overlap (owner 2026-10-25). |
| DEC-43 | Option C source: from the existing sample, or aggregate SQL per column? (asked 2026-10-25) | Aggregate SQL per column on PostgreSQL and ClickHouse; MongoDB from its sample (owner 2026-10-25). Lead: collection behind a source config flag, off by default (as sampling is); numeric min/max stay in a local-only bundle file like the value tokens; the snapshot keeps only a ranges-overlap boolean. |
| DEC-44 | CI `application-quality`: pip-audit found 5 new CVEs (certifi, filelock, opentelemetry-api); upgrade them in uv.lock? (asked 2026-10-25) | Yes, only those three (owner 2026-10-25). uv also moved the opentelemetry family to 1.45.1/0.66b1 and added two transitive packages (`opentelemetry-exporter-http-transport`, `opentelemetry-exporter-otlp-common`, Apache-2.0); license inventory refreshed. |
| DEC-45 | CI `source-integration-latest`: `mongo:9.0.2-noble` exits on runner kernels 6.19+ (fix SERVER-125742 released in 8.0.30 and 8.3.9, not yet in 9.0.x). Which pin? (asked 2026-10-25) | `mongo:8.3.13-noble` until a fixed 9.0.x exists (owner 2026-10-25). It also refused (CI 40173973962): kernels 6.19-7.0.13 are incompatible for every 8.x/9.x; the fix only lifts the guard on kernel 7.0.14+. Owner 2026-10-25: newest 7.0.x, `mongo:7.0.45-jammy`, until runners have 7.0.14+. |
| DEC-33 | P7-2: overlap now runs before the top-k cut, so `dropped_zero_overlap_candidates` can rise (kept set unchanged). Which count? (asked 2026-10-24) | Count all zeros, including those below the cut (owner 2026-10-24). |

## Open owner questions

- GitHub Actions billing blocks CI (2026-10-25); CI for `a371f35` (option C) not run on GitHub; local CI green on 2026-10-27. Last green GitHub CI: 40174608116 on `b0e6e99`.
- GitLab (`gitlab` remote) paused until the owner says it is available (API certificate from an unknown CA).
- Databases down (2026-10-24): P7-6 and P7-7 wait.

## Next action

Owner: fix GitHub billing (CI 40177198288 on `a371f35` was refused before any job started: "recent account payments have failed or your spending limit needs to be increased"), then the lead re-runs CI on `fix/phase7`. Then P7-6 (live re-check, 3 repeats, includes the first real run of option C SQL with `format_profiles = true`) and P7-7 (benchmark): both wait for the owner to say the databases are up; P7-7 also needs a yes per compose file.

## Log

- 2026-10-27: owner: GitHub is `origin` again (GitLab `gitlab` paused until the owner says it is available); CI runs locally. Local CI: repository-policy, application-quality (1266 passed, pip-audit clean), package-isolation Linux, graph-index, source-integration pinned set (91 passed, isolated, containers removed) all green. Not run: -latest set, Windows.

- 2026-10-25: CI 40177198288 on `a371f35`: no job started (GitHub billing / spending limit). Owner action needed.

- 2026-10-25: P7-5 option C done. Gate (lead alone): 1276 passed, 70 skipped, 1 xfailed; attack files the same 37 ids; all phase-7 attack files pass; ruff, mypy, ci_check, package_isolation clean; 0 corpus names; offline replay unchanged.

- 2026-10-25: CI 40174608116 on `b0e6e99` green in all 8 jobs (P7-1..P7-5 A+B+E, CI fixes).

- 2026-10-25: CI 40171751091 on `fefe6ef`: 5 of 8 green; `application-quality` red on pip-audit only (1160 tests passed); `source-integration` red: 6 live tests expected cross-source paths that P7-5 A now leaves out by default (intended); `source-integration-latest` red: MongoDB 9.0.2 refuses kernel 6.19+. Fixes: DEC-44 lock upgrade, DEC-45 pin, planner CLI `join-paths --include-unverified-cross-source` (the CLI had no way to set the P7-5 flag), live tests pass the flag. Local gate 1169 passed.

- 2026-10-25: P7-5 A + B + E done. Gate (lead alone, capped): 1169 passed, 70 skipped, 1 xfailed; attack files the same 37 ids; phase-7 attack files all pass; ruff, mypy, ci_check, package_isolation clean; 0 corpus names; schema `edge.schema.json` additive; goldens unchanged.

- 2026-10-24: P7-3 done. Gate (lead alone, capped, temp on disk): 1089 passed, 70 skipped, 1 xfailed; attack files the same 37 ids; ruff, mypy, ci_check, package_isolation clean; 0 corpus names. Real snapshot check 0.6 s for six asks.

- 2026-10-24: `/home` filled to 100% while the P7-3 worker ran; a write truncated `search.py`, which the worker rebuilt from HEAD plus its edit (lead re-reviewed the full diff). The worker deleted `.mypy_cache` and ran `uv cache prune` (~35 GB of unused cache; both regenerable). `/tmp` is a RAM-backed tmpfs: pytest now uses `--basetemp=.tmp/pt`.

- 2026-10-24: P7-2 and P7-4 done. Gate (memory-capped, lead alone): 1061 passed, 70 skipped, 1 xfailed; attack files the same 37 ids; ruff, mypy, ci_check, package_isolation clean; 0 corpus names.

- 2026-10-24: the machine ran out of memory with two workers running full suites in parallel; owner: one at a time, leave RAM free. Agents now run targeted tests only, under a 3 GB cap; the lead runs the full suite alone.

- 2026-10-24: P7-8 and P7-1 done. Gate: 1011 passed, 70 skipped, 1 xfailed; attack files the same 37 ids; ruff, mypy, ci_check, package_isolation clean; 0 corpus names.
- 2026-10-24: `fix/phase7` created from `verify/live-20261020` at `30eb6ce`; this file written from plan §4 and §8.
