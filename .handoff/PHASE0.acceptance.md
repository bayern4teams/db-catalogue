# Phase 0 acceptance checks (derived from the report and plan section 9; implementation not read)

Run: `uv run --python 3.12 pytest tests/catalogue_fixes -q` (green now: 80 passed, 49 xfailed).
Each item's xfail(strict) tests flip to a hard failure (XPASS) the moment the item lands; the lead
then deletes the marker on that item's tests. `--runxfail` shows the failure reasons. Controls (no
marker) must stay green throughout. Fixtures: `catalogue_fixture_kit.py` (signed synthetic
bundles; `ModelStub` = recording HTTP server: 400 on `maxLength/minLength/maxItems/minItems`,
`finish_reason` length, delay, forced status; `closed_port_url()` for unreachable).

| file | item | negative case (fails on old code) | controls |
|---|---|---|---|
| test_b1_schema.py | B1 | stub rejecting bounds -> `ask` returns cited answer; recursive walk finds no bound keyword | schema still sent; over-limit answer refused, 2000 accepted |
| test_search_split_stem_stop.py | B3/D6a, D6b, B4 | `revenue` finds `accountRevenue`; `url`/`server`/`id`; old-format index not misread; plural = singular; schema-word-only question -> NO_CATALOGUE_MATCH, 0 model calls (7 words) | whole-name rank, snake_case, over-split guard (`rl`), exact-name rank, `invoice`/`invoke` unmerged, `number` column found with `invoice`, zebra guard |
| test_c1_error_codes.py | C1/D7 | six causes x {ask, builder review} each its own code; five distinct codes | still an error never an answer, no canary in codes/logs/console, review stays partial with MODEL_REVIEW_FAILED and publishes candidates |
| test_c2_diagnostics.py | C2 | manifest lists codes; render count == manifest sum; CLI summary | complete snapshot shows none/0 |
| test_d1b_empty_cells.py | D1b | no blank stat cell; all-null / not provided / not computed; reason follows values not names | populated cells keep numbers |
| test_d3_lowercase_refs.py | D3 | `vendorid`->`vendor`, `buyerid`->`buyer`, plural parent, exactly those two | `xid valid paid codeid labelid` nothing, snake/camel, declared key, same-table, type-family, declared-first, DEC-12 stays none |
| test_a1_overlap.py | A1/D2/D9 | sampling off labelled "value overlap not measured" + page "values not compared"; different keys labelled; N-1 sample "sample too small"; 0 of N (cross and same source) dropped; only the disjoint sibling dropped | candidate kept when sampling off, below N, 25% overlap with numbers, 1 shared value; declared FK never dropped; no token in snapshot |
| test_no_corpus_literals.py | plan 2.4 | - | no corpus name in `packages/*/src` or in the fixtures |

Plan 2.4 commands (reviewer runs after each item):
- names: `grep -rniE 'zoominfo|diffbot|enrichment_engine|testzoominfo|companyrevenue|companyticker|companyemployees|companyexperience|industryclassification' packages/*/src` must print nothing (baseline: nothing).
- numbers (diff review, no mechanical test possible): `git diff 780e964 -- 'packages/*/src' | grep -E '^\+' | grep -E '[^A-Za-z_0-9"\x27.][0-9]{2,}\b|[0-9]+\.[0-9]+'` and for every hit ask "would a count over the catalogue or the config produce it?". The A1 drop threshold must be a validated builder config field defaulting to `MIN_TOKEN_SUPPORT`.

Not testable here (stay "implemented, unverified"): real llama.cpp grammar behaviour, busy-server
timeout, retrieval recall on the real catalogue, live overlap numbers (21 -> 5 candidates).
Assumptions the attacker should challenge: "value overlap not measured", "values not compared",
"sample too small", "all values null", "not provided by this source", "not computed" are the plan's
phrases; reasons are matched by substring ("sampling"/"no value tokens", "key"). Review-code location
accepted in `review_status` or `partial_reasons`.
