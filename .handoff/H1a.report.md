# H1 group (a) attack report: DEC-12, DEC-2, DEC-1, DEC-3, DEC-4, C2

Branch feat/catalogue-fixes at fa87e05. Checks were derived from artifacts/engineer-fixes.md and the
HANDOFF Decisions text before the implementation was opened (only test helpers, the Phase 0 kit and
existing public test APIs were read). Attack files (not collected by default, python 3.12):
`tests/catalogue_fixes/attack3_a_{dec12,dec12_noise,dec2,dec1,dec3,dec4,c2}.py`. Run by path with
`DBIG_BASE_SRC=<git archive of 2e87fa7> uv run pytest <file> -q -p no:cacheprovider`
(only c2 needs DBIG_BASE_SRC: it reads the new partial snapshot with the BASE commit's packages).
Mutation harness: `.tmp/h1a/mutate.py <name>` (scratch copy of HEAD, one patch, PYTHONPATH import
verified to come from the scratch tree; the working tree src is untouched).

## Results on the real tree
| file | result |
|---|---|
| dec12 | 24 passed |
| dec2 | 25 passed, 1 failed (RISK, see D-3) |
| dec1 | 14 passed, 1 failed (tie, see D-4) |
| dec3 | 15 passed, 1 failed (DEFECT D-1) |
| dec4 | 20 passed, 2 failed (DEFECT D-2, RISK D-5) |
| c2 | 13 passed (incl. old-reader read of the new partial snapshot) |
| dec12_noise | 12 failed (RISK D-6, characterisation of an accepted Decision, not a pass/fail gate) |

## Mutation proof (tests failing ONLY with the item reverted; baseline failures above excluded)
- DEC-12: restore `parent.id in declared_unique and` -> 5 fail (positive link, evidence says measured, case-insensitive, cross-source label, sampled overlap keeps).
- DEC-2 builder: `weak_key_ids.add` -> pass: 6 fail (proposed + labelled weak, primary_key variant, overlap keeps label, case). Collector: skip-all -> 4 collector tests fail.
- DEC-1: gate (`_resembles`) off -> 8 fail (report case, wrong-column-alone, unrelated names, separators); "named beats generic" filter off -> `generic_unique_column_loses_to_a_named_one` fails.
- DEC-3: drop `pair_allowed` in the single-field generator -> 4 fail (forbidden pair, symmetry, groups, isolate-all).
- DEC-4: weight -> 2 fails only `test_weight_decides_when_max_paths_cuts_the_list` (the same-source filter masks the weight otherwise); same-source filter off -> `longer_chain_beats_shorter_detour` fails; warning off -> 5 fail incl. plan warning.
- C2: `partial_reasons` not written -> 6 fail; render count `+ len(partial_reasons)` removed -> `cli_count_equals...` and `old_code_would_have_said_zero` fail.

## Verdicts
- DEC-12: ACCEPT (with RISK D-6). Report check satisfied; below 0.99, unmeasured, generic id/key/code/uuid/ref/num/no/fk, type mismatch, self-table, case: all hold; sampling on drops 0-overlap and keeps real overlap. Evidence says "measured", never "declared".
- DEC-2: ACCEPT (RISK D-3). Collector emits a names-only `index` constraint (one plain known column; expressions, tuples, unknown column, injection-shaped strings skipped and never stored; hashed attrs kept); builder labels "weak key claim ... uniqueness unproven", status not above a declared key, composite/other-index/other-column propose nothing, zero sampled overlap still drops. No schema change (kind exists).
- DEC-1: ACCEPT. Report case (0.9826 vs 1.0 other column) names the right column or nothing; `customer_id -> customers.id` and declared-pk-with-other-name still link; deterministic under field order; fuzzy gate cannot be passed by a role sibling because the table-name rule still blocks.
- DEC-3: REJECT until D-1 is fixed (one-line gap, rest holds: default none unchanged, symmetric, same-source untouched, malformed rejected, TOML `[builder]` loads).
- DEC-4: REJECT until D-2 is fixed (tie case); D-5 is a policy question for the lead. Everything else holds: same-source beats detour, only-cross path still returned with the warning, value evidence silences, declared never warned, plan carries the prose warning, old graph.json without `value_evidence` loads, order deterministic.
- C2: ACCEPT. Reasons are sorted, unique, codes only; page, render count and CLI count agree and are non-zero for 400/500/invalid JSON/truncated; canary text, key and URL never reach manifest/page/CLI; complete snapshot has none and count 0; `diagnostics` untouched; schema field optional; the BASE reader reads the new signed partial snapshot.

## Real defects
- D-1 `packages/catalog-builder/src/dbig_builder/builder.py` `_composite_candidates` (def at ~line 168, loop ~200-273): no `config.pair_allowed(...)` check. `forbidden_source_pairs` / `source_groups` do not remove cross-source COMPOSITE candidates (test `test_forbidden_pair_also_removes_cross_source_composite_candidates`; control proves the composite candidate exists without the config). Violates DEC-3 for that generator.
- D-2 `packages/planner/src/dbig_planner/paths.py` ~lines 106-113 and 173-184: the same-source preference holds only for paths already in the first `max_paths` results. Four same-source candidate hops cost 8, equal to the two-hop cross detour (2 x default 4); with `max_paths=1` the detour is returned (`test_tie_between_same_source_chain_and_detour_with_max_paths_one`, both edge orders). Fix idea for the lead: filter before the cut, or make the cross weight strictly exceed max_hops x 2.

## Risks and notes (not defects against the letter of the Decisions)
- D-3 `builder.py` ~565: a weak key claim wins over a contradicting measured uniqueness (0.2 unique still becomes a parent). Latent: ClickHouse has no measured uniqueness today; matters if the D1 cardinality probe lands.
- D-4 `builder.py` DEC-1 tie: two near-identical resembling unique columns of one parent (`depotid`, `depot_id`) both become candidates (bounded by top_k_per_field).
- D-5 `paths.py` ~138: the warning needs EVERY hop to be an unverified cross-source candidate, as the Decision says. A 3-hop path ch -> pg -> pg -> ch (two false cross hops around one same-source candidate) is silent (`test_three_hop_path_whose_middle_hop_is_same_source_still_warns` fails). The D4 report shape is 2-hop and does warn. The user may prefer "any".
- D-6 DEC-12 precision: any measured-unique column whose name equals a column of another table links (created_at, email, name, title, slug, url, amount, description, label, value, type, status all do; the generic list is only id/_id/uuid/key/code/num/ref/fk/no). Unsampled these are published as tentative "values not compared"; this is the A1 problem class (16 of 21 false). Also two measured-unique identical columns yield candidates in both directions. Suggest a Decision: restrict the identity rule to key-like types/names, or require sampling.
- Same-source filter hides a value-verified cross-source route when an unverified same-source one exists (printed by `test_value_verified_cross_path_is_not_hidden...`: only [a, b] returned). Consistent with DEC-4 text, noted.
- Plan carries the warning as prose, not the code `CROSS_SOURCE_CANDIDATES_UNVERIFIED` (the path object has the code).

## Plan 2.4 grep and constants
`grep -rniE 'zoominfo|diffbot|...|industryclassification' packages/*/src` : 0 hits (only a stale .pyc matched earlier for the pattern `sorting_key`, not the corpus grep). New constants: `WEAK_KEY_SUFFIXES` (a contract string shared with the collector, structural), `cross_source_candidate_weight` (config, default 4, ge=1), `min_token_support` (config), `batch_size` n/a. `GENERIC_KEY_NAMES` is pre-existing. No literal thresholds added in the diff of builder/planner/collector.

## Implemented, unverified (needs live data)
Real ClickHouse `system.tables.sorting_key` / `primary_key` strings (only the fake client was exercised: plain column, backticks, spaces; CI source-integration must confirm). Whether D-6 noise appears on the real PostgreSQL planner statistics (n_distinct = -1 on timestamp/text columns). Planner behaviour on the real 21-candidate snapshot. 0-of-N drop under real sampling interplay with DEC-12 (synthetic tokens only).
