# Lessons

What was tried and did not work, so nobody tries it again. Rule J14 in [AGENTS.md](../AGENTS.md). Each row links its source; the source holds the detail. "Reason not recorded" means the source does not say why.

## Test and CI guards

<!-- verified: f30780c -->

| Date | Tried | Measured result and why dropped | Replaced by | Source |
| --- | --- | --- | --- | --- |
| 2026-11-08 | Ruff `RUF100` run with only `PGH004,RUF100,PT011` selected | 82 `noqa` lines reported unused, because rules such as `S603` were not selected in that run. Only 21 were truly unused. | Run it with the project's own rule selection | `pyproject.toml` `[tool.ruff.lint]` |
| 2026-11-08 | `actionlint` run locally without `shellcheck` installed | It passed here, but CI (which has `shellcheck`) failed on 6 unquoted `$DOCS` in `docs.yml`. | Install `shellcheck` before running `actionlint`, and fix the findings | [.github/workflows/docs.yml](../.github/workflows/docs.yml) |
| 2026-11-08 | A `# zizmor: ignore[...]` comment inside a `run:` block | zizmor still reported the finding: the comment sits in a shell script, not in the workflow YAML. | An ignore list in `.github/zizmor.yml` | [.github/zizmor.yml](../.github/zizmor.yml) |
| 2026-11-08 | Installing the zizmor binary from its GitHub release with a hash check | The download returned an error page, so the hash did not match and the install stopped. | The pinned PyPI package in `uv.lock`, whose hashes `uv` checks | `pyproject.toml` dev group |

## Relationship inference

<!-- verified: bce5fee -->

| Date | Tried | Measured result and why dropped | Replaced by | Source |
| --- | --- | --- | --- | --- |
| 2026-09-28 | Admit a pair only if the child name ends in `_id` and the parent has a declared single-column unique key | Very few pairs were ever compared, so a column such as `owner_ref` or an undeclared but unique key could not become a candidate. No count recorded. | Three admission rules, later six | [TECHNICAL_IMPLEMENTATION.md](../TECHNICAL_IMPLEMENTATION.md) §8b |
| 2026-10-04 | Type family = type name with parameters cut off | ClickHouse `String`, PostgreSQL `text` and MongoDB `str` were three families, and `Nullable(Int32)` was the family `nullable`. No text key and no nullable ClickHouse key became a cross-engine candidate. Found by `tests/test_live_shapes.py`. | Unwrap `Nullable` and `LowCardinality`; one family per engine spelling of integer, text and UUID | TECHNICAL_IMPLEMENTATION.md §8b |
| found before 2026-10-05 | Measured uniqueness proposes any unique parent column | On a two-row lookup table every column is unique, and `regions.name` was proposed beside the declared key `regions.code`. | Measured uniqueness only for tables with no declared key (rule 4, user decision 2026-10-05) | TECHNICAL_IMPLEMENTATION.md §8b |
| found before 2026-10-05 | Suffix rule that needs an underscore (`_id`, `_code`) | `camelCase` keys (`AccountID`, `customerId`) were never compared. | Split at case boundaries; `key_name` rule (rules 5 and 6) | TECHNICAL_IMPLEMENTATION.md §8b |
| 2026-09-30 | One edge and evidence ID per pair of tables for composite keys | Two composite keys on one parent that one child matched produced one candidate, because the second overwrote the first. Found by review before tests existed. | IDs that include the key | [TODO.md](../TODO.md), "Finished composite-key candidates" |
| 2026-10-12 | Name-based candidate rules on real databases | 5 of 21 proposed relationships were true and 16 false; 40 confirmed relationships were missing. | Fixes planned (items A1 to D9), not merged | [artifacts/live-test-report.md](../artifacts/live-test-report.md), section 8; [artifacts/candidate-trace.md](../artifacts/candidate-trace.md) |
| date not recorded | A fixed weighted similarity sum shown as "confidence" | Rejected in research: a similarity is not a probability. Reason for rejection is in the source; no measurement. | `confidence` stays null until calibrated | [ARCHITECTURE_RESEARCH.md](ARCHITECTURE_RESEARCH.md) |

## Collectors and values

<!-- verified: bce5fee -->

| Date | Tried | Measured result and why dropped | Replaced by | Source |
| --- | --- | --- | --- | --- |
| 2026-09-27 | A name-derived ID for an object with no server-assigned identity | Rejected by user decision: an entry whose identity cannot survive a rename would attach history to a different object. The cost is that such objects never appear. | `IDENTITY_UNAVAILABLE` diagnostic, loud and visible in the manifest | TECHNICAL_IMPLEMENTATION.md §6a |
| 2026-09-27 | Plain hash of sampled values | Rejected: an unkeyed digest of a low-entropy value is recovered by trying every candidate. Not measured. | HMAC-SHA256 with a per-run key | TECHNICAL_IMPLEMENTATION.md §8a |
| 2026-09-27 | `ORDER BY random()`, `TABLESAMPLE` plus `LIMIT` or unbounded scans for samples | Rejected: they hurt a production source or give neither a cheap nor an unbiased sample. Not measured. | First rows the server returns, labelled in `sample_method` | TECHNICAL_IMPLEMENTATION.md §8a |
| 2026-10-04 | MongoDB `sample_rows` as a total per source | After one large collection, every later collection was left unsampled and, having no validator, had no fields and no diagnostic. | `sample_rows` per collection; `max_sample_bytes` names the collections it left out | TECHNICAL_IMPLEMENTATION.md §7b |

## Search, answers and plans

<!-- verified: bce5fee -->

| Date | Tried | Measured result and why dropped | Replaced by | Source |
| --- | --- | --- | --- | --- |
| 2026-09-30 | Whole-token search over entry names; `ask` needing every question word and falling back to unfiltered entries | `customer` missed `customers`; a natural question matched 0 entries and the model got the first entries alphabetically. | Prefix terms, name, table and comment index with bm25, `match = "any"` for `ask`, no fallback | TECHNICAL_IMPLEMENTATION.md §7b; TODO.md "requester, planner and comments on real data" |
| 2026-10-12 | Answer schema with `maxLength: 2000` | 17 of 20 questions returned `MODEL_UNAVAILABLE_OR_INVALID`, most likely the llama.cpp server rejecting the schema (`failed to parse grammar`); two of the 17 were 60 s timeouts. Retrieval also gave the model only 25% of the needed entries. | Fix planned (item B1), not merged | [artifacts/model-questions-results.md](../artifacts/model-questions-results.md) |
| 2026-10-04 | `SELECT *` in a rendered join | The result had one `id` per joined table. | Every catalogued column by name as `<table>__<column>` | TECHNICAL_IMPLEMENTATION.md §7b |
| 2026-10-04 | An independent `LIMIT` sample per read in a cross-source plan | The final join could be empty although matches existed. | Each later read filtered by the keys of the read before it | TECHNICAL_IMPLEMENTATION.md §7b |
| 2026-09-27 | Catch a bare `ValueError` at the command surface | Rejected: it would dress a validation failure or a bug as an operator error. | `OperatorError`, with a test that `validate-graph` keeps its traceback | TECHNICAL_IMPLEMENTATION.md §12a |

## Tooling, CI and process

<!-- verified: bce5fee -->

| Date | Tried | Measured result and why dropped | Replaced by | Source |
| --- | --- | --- | --- | --- |
| 2026-09-27 | An unpinned uv for the supply chain gate | `uv export --format cyclonedx1.5` failed on uv 0.8.17 while CI passed. | uv pinned in every workflow, floor 0.10.0 | TODO.md ("Pin the uv resolver" entry) |
| 2026-10-04 | Default Python (3.13) for development | `tests/test_ci_certificates.py` failed with `CA cert does not include key usage extension`, because 3.13 verifies certificates strictly. | `.python-version` = 3.12; the CI test CA now carries the key usage extension | TODO.md, verification fixes 1-10, item 1 |
| 2026-09-27 | Read a gate's result from piped output | `ruff` piped through `tail` hid a failure; a commit was pushed red. | Check the exit code | TODO.md, "Self-correction recorded 2026-09-27" |
| 2026-10-04 | Driver exception types imported in tests of an install without driver extras | CI run 37192243412 failed in `application-quality` with `ModuleNotFoundError`. | `importorskip`; `source-integration` still runs them | TODO.md, verification fixes 1-10 |
| date not recorded | Index-based fake PostgreSQL cursor in tests | It answered by call order and mis-answered when a query was added between two others. | A fake that answers by the catalog asked about | TODO.md |
| date not recorded | A threshold test whose input moved with the constant | Dropping `MEASURED_UNIQUE` from 0.99 to 0.5 left every test passing. | Absolute values plus a test that pins the constant | TODO.md, "Mutation testing found a flaw" |
| 2026-09-27 | Stay on Graphify 0.9.65 and Graft 0.18.0 | Reversed by the user on 2026-11-07: "we should always run on latest version" | Pins follow the latest release | TODO.md (2026-09-27 pin decision); AGENTS.md section 4 |
| 2026-11-07 | MongoDB 8.x or 9.x in the CI latest leg | They refuse to start on runner kernels 6.19 to 7.0.13 (SERVER-121912, SERVER-125742). | MongoDB 7.0.45, an approved exception reviewed when runners reach kernel 7.0.14 | `.github/workflows/ci.yml`, comment beside the `mongo:` line in the matrix |
