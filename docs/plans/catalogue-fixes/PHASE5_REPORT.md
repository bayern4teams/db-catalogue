# Phase 5 report (cloud hardening)

Live-only checks are in [LOCAL_VERIFY.md](LOCAL_VERIFY.md). Counts and names only; no values.

## H0 coverage of `artifacts/engineer-fixes.md`

Every proposal and check sentence mapped to a commit and a test, or to why it is not built. Test paths are under `tests/` (`cf/` = `tests/catalogue_fixes/`).

| id | proposal / check | built in | test | state |
|---|---|---|---|---|
| A1(1) | say on every candidate what was not checked, on the page | 783e70e, af63301 | cf/test_a1_overlap.py | built |
| A1(2) | measured zero overlap lowers the status | 783e70e (user 2026-10-12: measured 0-of-N at or above `min_token_support` is **dropped**, not lowered) | cf/test_a1_overlap.py | built as decided |
| A1(3) | planner warns when keys have no value evidence | 9e669f7 (DEC-4) | test_planner.py `test_dec4_*` | built; warns on unverified cross-source hops only (see gap G1) |
| A1 check | disjoint columns show overlap 0 / not measured; plan warning names it | as above | as above | built (a disjoint pair with sampling on is dropped; with sampling off the page says "values not compared") |
| A2 | prefer the parent column whose name matches the child's; none if none resembles | 0abead0 (DEC-1) | cf/test_dec1_parent_column.py | built |
| A3 | same as A1 | 783e70e | cf/test_a1_overlap.py | built |
| B1 | no length bound in the sent schema; limit enforced after parsing | 42ff1de | cf/test_b1_schema.py | built; live check pending |
| B2 | document; batches; thinking switch optional | 9dff5a6 (DEC-10), fa8a580 (note) | cf/test_dec10_batches.py | batches built; the "thinking off" switch **not built** (DEC-10 answer: unverified for this model) |
| B3 | split camelCase/PascalCase at index time | 42ff1de | cf/test_search_split_stem_stop.py | built |
| B4 | schema words as stopwords | 42ff1de | cf/test_search_split_stem_stop.py | built; the "minimum share of words" alternative is deferred (`ponytail:` ceiling) |
| B5(1) | ClickHouse key as weak, labelled parent claim; threshold visible in evidence | 5f00c21 (DEC-2); `threshold` in `measured_uniqueness` evidence | cf/test_dec2_weak_key.py | built for one-column keys; composite keys and MongoDB parents not built (gap G2) |
| B5(2) | suffix of a lowercase name when the stem is a known object | be1bfd6 | cf/test_d3_lowercase_refs.py | built |
| B5 check | `classificationid` to `industryclassification.classificationid` | 4524942 (DEC-12) | test_inference_decisions.py, cf/test_d3_lowercase_refs.py | built |
| C1 | distinct model error codes | 42ff1de, 09e9bd5 | cf/test_c1_error_codes.py | built (DEC-11 moot) |
| C2 | CLI summary and manifest carry the page diagnostics | dd99df3 (DEC-11b a) | cf/test_c2_diagnostics.py | built |
| C3(1)-(5) | ClickHouse `SELECT`, `.env` line, `DBIG_SIGNING_KEY`, drivers, llama.cpp note | d1af940, fa8a580 | tests/test_examples.py | built; command runs listed in LOCAL_VERIFY.md |
| C4 | per-source exclude list for schema-description collections | 7c865a9 (DEC-8) | cf/test_dec7_dec8_retrieval.py | built |
| D1(1) | store key column names | 5f00c21 | cf/test_dec2_weak_key.py | built (one-column keys) |
| D1(2) | page says why a cell is empty | d69cd83 | cf/test_d1b_empty_cells.py | built |
| D1(3) | optional bounded cardinality probe for ClickHouse and MongoDB | | | **not built: optional, changes collector queries and budgets** -> DEC-14 |
| D2(1) | label cross-source candidates "values not compared" | 783e70e | cf/test_a1_overlap.py | built |
| D2(2) | drop on 0% when sampling is on | 783e70e | cf/test_a1_overlap.py | built |
| D2(3) | dataset groups / forbidden pairs | bc2615c (DEC-3) | cf/test_dec3_source_groups.py | built |
| D2(4) | partial overlap is a ranking, not a threshold | 783e70e | cf/test_a1_overlap.py | built |
| D3 | lowercase names | be1bfd6 | cf/test_d3_lowercase_refs.py | built |
| D4 | prefer same-source path; warn on unverified cross-source | 9e669f7 | test_planner.py | built |
| D5 steps | relationships and evidence status in context | 8e1fe49 (DEC-5) | cf/test_dec7_dec8_retrieval.py | built |
| D5 comprehensive | grouped retrieval or two-stage answer | partly 7c865a9 (object retrieval for list questions) | | two-stage answer **not built: a design choice** -> DEC-15 |
| D5 inference | uncited "not established" outcome | 6e5a63d (DEC-6) | cf/test_dec7_dec8_retrieval.py | built |
| D5 direct | needs B3 | 42ff1de | | built |
| D5 refusal on `derived from` | claim, not user's wording | 6e5a63d | cf/test_dec7_dec8_retrieval.py | built |
| D6(1)-(2) | stemming, camelCase | 42ff1de | cf/test_search_split_stem_stop.py | built (plural fold, not porter; recorded) |
| D6(3)-(5) | source/engine/namespace words, list-by-object, object ranking | 7c865a9 (DEC-7) | cf/test_dec7_dec8_retrieval.py | built |
| D7 | timeout code; document queueing | 42ff1de, fa8a580 | cf/test_c1_error_codes.py | built |
| D8 | exclude noise; filter by named source; relationships in context; lineage rule; resolve cited IDs | 7c865a9, 8e1fe49, 6e5a63d, faa26f1 | cf/test_dec7_dec8_retrieval.py | built |
| D9 | overlap number and sample size on the candidate; zero in a large sample is evidence against | 783e70e | cf/test_a1_overlap.py | built |
| E1 | short `DBIG_SIGNING_KEY` is a one-line error | 9dff5a6 | tests/test_security.py | built |
| DEC-13 | pymongo 4.18.2 | c343957 | pip-audit | built |

### Gaps and what became of them
- G1 (A1(3)): the planner warns only when no edge carries value evidence. A candidate whose sample was below the drop threshold is kept and labelled "sample too small" and the plan does not name it. No choice needed to review; recorded, not changed.
- G2 (B5(1)): MongoDB collections still never become parents and composite ClickHouse keys give no claim. Needs the DEC-14 choice (statistics) to be useful.
- DEC-14 and DEC-15 added to HANDOFF.md as open Decisions; they block nothing else.

## H2 regression (final code of 2026-10-13)
- Full suite: 663 passed, 68 skipped. `ruff`, `mypy packages` clean. `pip-audit --skip-editable`: no known vulnerabilities; `supply_chain.py`: 113 components, 0 unresolved licences. Phase 0 fixtures: no leftover strict `xfail` (the only mentions are docstrings).
- Attack files run by path (all pass unless listed). Failures are known and each has a cause, not a regression:
  - `attack2_d3_lowercase.py` (27): its fixture parent column `rowkey` does not resemble the child name; DEC-1 (user: "propose nothing if none resembles") now refuses it on purpose. The same cases with a resembling parent column pass in `test_d3_lowercase_refs.py`.
  - `attack3_a_dec12_noise.py` (12): ordinary shared names link on a measured-unique parent -> DEC-16 (open).
  - `attack3_a_dec1.py` (1, two near-identical unique parent columns both become candidates), `attack3_a_dec2.py` (1, a weak ClickHouse key beats a measured 0.2 uniqueness), `attack3_a_dec4.py` (1, a path with a same-source middle hop does not warn; the Decision says "every hop"): open policy points in HANDOFF.md.
  - `attack3_b_answering_retrieval.py` (6): five paraphrased lineage claims (documented `ponytail:` ceiling) and a source id that is also a word prefix; `attack_search.py` (1): `pos` is a prefix of the engine word `postgresql`, now searchable by DEC-7.

## H3 report-shaped end-to-end scenario (2026-10-14, final code incl. L1 to L3)
`tests/catalogue_fixes/test_h3_end_to_end.py`, four invented sources, planted truth: 10 true links, 7 false links (same names, disjoint value domains), 2 decoy columns (`valid`, `paid`). Counts only. The *before* column is **not re-measured**: the baseline runner from the earlier session was not kept, so the per-item before-behaviour in `PHASE0_REPORT.md` stays the reference.

| measure | sampling on | sampling off |
|---|---|---|
| true links proposed / missed | 10 / 0 | 10 / 0 |
| false links proposed | 0 (each dropped on 0 of 12 sampled values) | 7, each labelled "values not compared" (17 candidate rows, 51 page mentions) |
| candidates with an overlap number | 10 | 0 |
| a 25% overlap is kept and shows "25% of 8 sampled" | yes | n/a |
| decoys / unplanned candidates | 0 / 0 | 0 / 0 |
| join questions J1 to J4, X1 with a true path | 5 of 5 (0 through a false link) | same set (the false links exist but are labelled) |
| questions answered with a citation | 8 of 8 | 8 of 8 |
| retrieval recall in the stub's context | 15 of 15 | 15 of 15 |
| relationship rows sent to the model | 37 | 70 |
| unanswerable question | `NO_CATALOGUE_MATCH`, 0 model calls | same |
| schema-description collections reaching the model | 0 (with `exclude_objects`) | 0 |
| `partial` reasons shown (manifest = page) | `PREVIOUS_COMPLETE_BUNDLE_USED`, `SOURCE_BUNDLE_PARTIAL`, `MODEL_REJECTED_REQUEST` + `MODEL_REVIEW_FAILED`; complete = none | same |

Plans: J1 `review_required`, J4 (cross-source) `unresolved` with `approved_staging_or_federated_target` and `final_cross_source_join` listed, Mongo plan `ready_for_review`. Not proved by this scenario: real PostgreSQL statistics, real ClickHouse key strings, a real model (`LOCAL_VERIFY.md`).

## H4 privacy canaries (2026-10-14)
`tests/catalogue_fixes/test_h4_privacy_canaries.py`: 49 passed, 1 xfailed (the documented lineage-claim paraphrase ceiling). A canary is planted as a sampled value, a SQL literal, a prompt and a secret, then searched for in: bundles, snapshot, search index, `partial_reasons`, diagnostics, every review batch request, the `ask` request and relationship context, the weak ClickHouse key claim, resolved `cited` names, the error codes of every model failure mode, the CLI output, the REST bodies, a log capture of the whole build and of every REST call, traces, and short-key errors. Result: no canary in any of them; the question is not echoed in a 422 body.
- Real finding, fixed: `GET /v1/catalogue/search?query=...` carries the question in the URL, so a default uvicorn access log records it (DEC-17). Now: `POST /v1/catalogue/search` takes the query in the body (canary test added) and `dbig-requester serve --no-access-log` turns the request log off; documented in `LOCAL_RUN.md`, `LIVE_TEST.md`, `TECHNICAL_IMPLEMENTATION.md`.
- Test-side corrections (five, applied by the user's session, none removed a privacy assertion) are in `HANDOFF.md` Notifications.
- Left open on purpose: the `openai` library logs prompts at DEBUG if an operator turns that level on (the application sets no logging; a test asserts this).

## L1 to L3 (the user's answers of 2026-10-14)
- L1 DEC-14: `cardinality_probe` (default off) for ClickHouse and MongoDB; 9 tests, 5 fail without the change. Live behaviour: `LOCAL_VERIFY.md`.
- L2 DEC-16: measured-unique parents with non-reference names need measured overlap; 7 tests, 5 fail without the change; the earlier noise probe `attack3_a_dec12_noise.py` now passes (12 of 12).
- L3 DEC-17: POST search route and `--no-access-log`; 4 tests plus 1 canary test.
