# Catalogue fixes — final report (2026-10-13)

Branch `feat/catalogue-fixes`. Never merged by the agents. Every item of `artifacts/engineer-fixes.md` (A1 to D9) plus E1 is built; the user's Decisions DEC-1 to DEC-13 (DEC-11 moot) were built exactly as answered.

## Evidence
- Full check green locally on the final code: ruff, mypy, 663 passed / 68 skipped, `pip-audit` clean (pymongo 4.18.2), `supply_chain.py`, `uv build --all-packages`, package isolation (under Python 3.12), graph sync.
- CI run 38364040378 (dispatched on the Phase 3 head) green in all 8 jobs, including `source-integration` and `source-integration-latest` (live PostgreSQL, ClickHouse, MongoDB with synthetic schemas).
- No corpus literal from the user's databases in `packages/*/src` (grep: 0 hits).

## Items
| group | items | state |
|---|---|---|
| Phase 1 | B1, B3/D6a, D6b, B4, C1/D7, C3, D1b | unverified-live |
| Phase 1 | C2 (manifest `partial_reasons`, DEC-11b a) | accepted-cloud |
| Phase 2 | D3, A1/D2/D9 | unverified-live |
| Phase 3 | DEC-12 (key_name for measured-unique parents), DEC-1 (parent column resembles child), DEC-3 (source groups), DEC-4 (planner), E1 | accepted-cloud |
| Phase 3 | DEC-2 (weak ClickHouse key claim), DEC-5/6/7/8/9 (answering and retrieval), DEC-10 (review batches) | unverified-live |
`LOCAL_VERIFY.md` lists each live check with the report's "before" value.

## Behaviour changes the user should know
- The requester index is version 6: run `dbig-requester index` again (old indexes answer `STALE_INDEX`; the question route returns 503, search 409).
- DEC-12 reverses the 2026-10-05 "declared key only" limit of the `key_name` rule, by user decision; two tests were flipped on purpose.
- The word `lineage` no longer refuses a question or answer; an answer claiming `derived from`, `flows into` or `writes to` is still rejected.
- `ask` replies gain `cited` (names) and may carry `code: NOT_ESTABLISHED_BY_CATALOGUE`.
- New optional config, all default none or documented: `[builder] source_groups`, `forbidden_source_pairs`, `review.batch_size` (5); `[requester] exclude_objects`, `answer.relationship_limit` (20); `[planner] cross_source_candidate_weight` (4).

## Escalations carried over (from the lead's verdicts)
- A1: the 0-of-N drop on a first-rows sample could drop a real foreign key whose parent ids lie outside the sampled rows (a capped parent never drops); needs the user's sampling setup to judge.
- The plural fold is not a stemmer (`boxes` becomes `boxe`); the stopword list is the ceiling for B4.
- Lineage-claim detection is phrase-based; the requester may not import the planner.
- ClickHouse key claims skip expression and multi-column keys.
- The builder review schema was checked for bounds only by a non-strict test; the report says llama.cpp accepted it. Verify live.
- Verifier "attack" passes were run by the lead's own tests for Phase 3 items (cost); the Phase 0 to 2 items had independent verifier attacks.

## Ponytail
- Review: Phase 1 to 3 diffs reviewed, nothing mandatory to cut. Audit: no unreferenced code found by the graphs in the touched files; the only cosmetic shrink is the `named` set in `question_scope`.
- Debt ledger (every `ponytail:` comment and its trigger): httpx class-name match (reviewer.py, answer.py; upgrade when httpx is a declared dependency); ClickHouse expression keys (runtime.py; parse them when a real schema needs it); lineage-claim phrases (answer.py; check against planner output); stopword list (search.py; require a minimum share of matching words); plural fold (search.py; a real stemmer if recall demands).
- Gain: not measured; no scoreboard claimed.

## Phase 5 (2026-10-13 to 2026-10-14): cloud hardening and hand-over
- Coverage of `engineer-fixes.md`, independent attacks with mutation proof, regression, the report-shaped end-to-end scenario and the privacy canaries: `PHASE5_REPORT.md` (H0 table, H2, H3 counts, H4).
- Built on the user's answers of 2026-10-14: L1 optional cardinality probe for ClickHouse and MongoDB (DEC-14, default off), L2 measured-overlap requirement for measured-unique parents with non-reference names (DEC-16), L3 `POST /v1/catalogue/search` and `--no-access-log` (DEC-17). DEC-15 (two-stage answer) deferred by the user until the live rerun.
- Final gate: full check green locally (751 passed, 68 skipped, 1 xfailed; ruff, mypy, pip-audit `--skip-editable`, supply chain, package isolation under 3.12); CI run 38524824216 green in all 8 jobs on `4aaa2a9`. The first run (38524290097) failed in `application-quality` only because the H4 canary test imported `pg8000`, which that job does not install; fixed in the test with a stub fallback, no assertion changed.
- `/ponytail-review` of the Phase 5 diff: nothing mandatory to cut; one cosmetic shrink left (`distinct < 0` guard in `_probe_profile` cannot trigger). New `ponytail:` ceilings: `_is_reference_style` (a lowercase name with a three-character stem passes, e.g. `valid`; upgrade: require `known_objects`), `_probe_profile` (uniqueness is within the first `cardinality_probe_max_rows` rows; upgrade: table-wide `uniqExact` behind its own budget).
- Open for the user: nothing blocks. Policy points listed under "Open escalations" in `HANDOFF.md` remain as recorded.

## Phase 6 (2026-10-19): the live findings F5 to F13 on `fix/live-findings`
- Source of truth `artifacts/run-20261014/leader/LEAD_REVIEW.md`; owner decisions DEC-18 to DEC-21. Every item: checks derived by a verifier before the change, mutation proof, attack by a second verifier, lead verdict (rows in `HANDOFF.md`).
- Built: P6-1 `ValueTokens.complete` (additive schema field); P6-2 a measured zero drops only against a complete parent, a zero against a sampled parent is kept as `parent_sampled`, capped sets compared inside their common bottom-k hash range; P6-3 ClickHouse value hashes (materialized views read, a refused read is a code-only diagnostic); P6-4 ordinary names link only to a declared key (DEC-20) and top-k ties broken by evidence (manifest count renamed to `dropped_ordinary_name_candidates`); P6-5 review `max_tokens` unset by default, example timeout 900; P6-6 probe counts through a `LIMIT` subquery on one thread; P6-7 source and namespace on every relationship, index version 7, cross-source sentence; P6-8 `outcome` required; P6-9 retrieved tables expanded to all their columns, uncapped.
- Attack rounds: P6-2 rejected once (a `parent_sampled` zero satisfied DEC-16, the edge text hid it, the planner counted it as value evidence; an empty capped token set raised IndexError), fixed by the lead with the verifier's tests. P6-3 findings acted on (materialized views, refused reads). All others accepted first round.
- Intended test flips, each named in its commit: `test_dec16_name_only_keys::test_measured_overlap_admits_the_pair`, `test_l1_cardinality_probe::test_clickhouse_probe_yields_uniqueness_from_counts_only`, `test_h4_privacy_canaries::test_the_relationship_context_is_names_status_and_the_builders_sentence`, three DEC-7 list-question probes in `attack3_b_answering_retrieval.py` (retrieval still objects only; their columns now follow), and every well-formed test stub reply gained `"outcome": "answered"`.
- Gate P6-10: full check green (904 passed, 69 skipped, 1 xfailed; ruff, mypy, ci_check, pip-audit clean, supply chain, package isolation under 3.12); attack files by path fail only the known 37 (the new `attack6_*` files pass); H4 canaries 49 passed 1 xfailed, extended to ClickHouse hashes (`attack6_f7_f9.py`), relationship source fields and expanded columns (`attack6_f11_f13.py`); §2.4 grep 0 hits; CI on the final head recorded in `HANDOFF.md`.
- `/ponytail-review` of the Phase 6 source diff: nothing mandatory; optional shrinks left on purpose (a shared token-append helper for PostgreSQL and ClickHouse, about 15 lines, not done because it would touch the accepted PostgreSQL path just before hand-over; two small shrinks in `search.py`). `/ponytail-debt`: 3 new ceilings, each with its trigger: a never-complete ClickHouse parent gives no trusted zero (builder and collector; upgrade: full-column sketch, LEAD_REVIEW F5 option C); uncapped table expansion can exceed the model context (requester; upgrade: a cap behind config or type-text indexing, F13 option C).
- Open for the owner: DEC-22 (UUID and date normalisation for tokens; blocks nothing). The live numbers are `unverified-live`: run "Phase 6 live re-check" at the top of `LOCAL_VERIFY.md`.
