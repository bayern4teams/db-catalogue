# Glossary

One fixed term per concept. Documents use these words and no synonyms.

## Roles

<!-- verified: 6e43d33 -->

| Term | Definition | Code |
| --- | --- | --- |
| collector | Process that reads one or more sources with read-only credentials and writes one signed bundle per scan. | `packages/collectors/src/dbig_collectors/runtime.py::collect` |
| builder | Central process that validates bundles and publishes a snapshot. | `packages/catalog-builder/src/dbig_builder/builder.py::assemble` |
| requester | Module that searches a snapshot, answers questions with a private model, and dispatches scan requests. | `packages/requester/src/dbig_requester/search.py::search` |
| planner | Module that builds a path graph from a snapshot and observations, and renders plans. It never runs a plan. | `packages/planner/src/dbig_planner/graph.py::build_graph` |
| reviewer | Bounded LangGraph workflow in the builder that asks a private model about ambiguous candidates. | `packages/catalog-builder/src/dbig_builder/reviewer.py::review_candidates` |
| source | One configured database (PostgreSQL, ClickHouse or MongoDB) with its own collector. | `packages/contracts/src/dbig_contracts/models.py::Source` |

## Artifacts

<!-- verified: 6e43d33 -->

| Term | Definition | Code |
| --- | --- | --- |
| bundle | Directory of JSONL files plus a signed manifest, written by a collector. | `packages/contracts/src/dbig_contracts/artifact.py::write_artifact` |
| snapshot | Catalogue the builder publishes (`nodes`, `edges`, `evidence`, `runs`, manifest). The only interchange contract between builder, requester and planner. | `packages/contracts/src/dbig_contracts/models.py::Manifest` |
| manifest | List of files, digests, counts and completeness of a bundle (signed) or a snapshot (unsigned). | `packages/contracts/src/dbig_contracts/models.py::Manifest` |
| complete / partial | Manifest completeness. `partial` means a source, capability or review did not finish; `Manifest.partial_reasons` names why. | `packages/contracts/src/dbig_contracts/models.py::Manifest` |
| diagnostic | Typed, value-free record of something that did not work, such as `IDENTITY_UNAVAILABLE`. | `packages/contracts/src/dbig_contracts/models.py::Diagnostic` |
| stable ID | ID derived from source, kind and a server-assigned native identifier, never from a display name. | `packages/contracts/src/dbig_contracts/ids.py::stable_id` |
| canonical JSON | UTF-8 JSON with sorted keys and no whitespace. The bytes that are signed. | `packages/contracts/src/dbig_contracts/artifact.py::canonical` |
| profile | Per-field statistics a source provides (null fraction, distinct estimate, width) and, when enabled, the shape of its values (see format profile). | `packages/contracts/src/dbig_contracts/models.py::Profile` |
| value token | HMAC-SHA256 of a normalised sampled value. Opt-in. Lives only in a bundle, as does a value range. | `packages/contracts/src/dbig_contracts/models.py::ValueTokens` |
| value range | Smallest and largest integer of a field. Real values, so they live only in a bundle (`ranges.jsonl`); the snapshot records only whether two ranges overlap. | `packages/contracts/src/dbig_contracts/models.py::ValueRanges` |
| graph (planner) | Derived planner index built from a snapshot and observations. A cache, never a contract. | `packages/planner/src/dbig_planner/graph.py::build_graph` |
| search index | Derived requester SQLite FTS5 index built from a snapshot. A cache, never a contract. | `packages/requester/src/dbig_requester/search.py::index_snapshot` |

## Relationships and evidence

<!-- verified: 6e43d33 -->

| Term | Definition | Code |
| --- | --- | --- |
| edge | Typed link between two nodes, with a status and evidence IDs. | `packages/contracts/src/dbig_contracts/models.py::Edge` |
| edge status | One of `declared`, `strong`, `tentative`, `rejected`, `observed`. | `packages/contracts/src/dbig_contracts/models.py::Edge` |
| declared key | Constraint the source itself enforces (primary, unique, foreign). | `packages/contracts/src/dbig_contracts/models.py::Constraint` |
| candidate | A `REFERENCE_CANDIDATE` edge: a guessed reference, never lineage. | `packages/catalog-builder/src/dbig_builder/builder.py::assemble` |
| evidence | Immutable, typed record that supports or opposes an edge. | `packages/contracts/src/dbig_contracts/models.py::Evidence` |
| containment | Share of a child field's distinct values found in a parent key. One-directional. | `packages/catalog-builder/src/dbig_builder/builder.py::_overlap` |
| blocking rule | Rule that admits a pair for comparison (suffix, exact bare name, key name, measured uniqueness). | `packages/catalog-builder/src/dbig_builder/builder.py::assemble` |
| evidence tier | Strength of the measured evidence for a candidate: `verified`, `plausible` or `unverified_cross_source`. Used instead of a confidence score. | `packages/contracts/src/dbig_contracts/models.py::Edge` |
| unverified cross-source candidate | Candidate between two sources with no positive value measurement. Join paths leave it out unless the planner is told to include it; a path made only of such candidates carries the warning `CROSS_SOURCE_CANDIDATES_UNVERIFIED`. | `packages/planner/src/dbig_planner/paths.py::find_paths` |
| value overlap | Measured share of sampled value tokens that two fields have in common, with a Wilson interval. Zero shared tokens drop a candidate only when the sample is conclusive. Complements containment. | `packages/catalog-builder/src/dbig_builder/builder.py::_overlap` |
| format profile | Value-free shape of a column (length range, digit, hex, UUID and letter shares). A candidate whose child format the parent lacks is dropped. Opt-in per source. | `packages/catalog-builder/src/dbig_builder/builder.py::_format_conflict` |
| cardinality probe | Opt-in bounded count of distinct values per column, so a unique column can be measured as a parent key. | `packages/collectors/src/dbig_collectors/config.py::SourceConfig` |
| owner decision | Operator verdict (`confirmed` or `rejected`) on a candidate, read from `decisions_file`. | `packages/catalog-builder/src/dbig_builder/config.py::Decision` |
| source group | Operator rule that says which sources a candidate may pair. `forbidden_source_pairs` is the opposite rule. | `packages/catalog-builder/src/dbig_builder/config.py::BuilderConfig` |
| model failure code | Typed reviewer outcome such as `MODEL_UNREACHABLE` or `MODEL_TIMEOUT`. | `packages/catalog-builder/src/dbig_builder/reviewer.py::MODEL_FAILURE_CODES` |
| lineage | Observed or defined data movement from input to output. Distinct from a candidate and from an observed join. | `packages/planner/src/dbig_planner/paths.py::find_paths` |
| observed join | A join seen in a query (`JOINS_OBSERVED`). Proves use, not derivation. | `packages/planner/src/dbig_planner/graph.py::build_graph` |
| observation | Input record to the planner: SQL text, a pipeline run, or an OpenLineage event. | `packages/planner/src/dbig_planner/observations.py::SQLObservation` |
| join path | Ranked chain of reference edges between two objects. | `packages/planner/src/dbig_planner/paths.py::find_paths` |
| plan | Reviewable SQL or MongoDB read steps for a path. Never executed by this project. | `packages/planner/src/dbig_planner/plans.py::render_plan` |

## Operations

<!-- verified: 6e43d33 -->

| Term | Definition | Code |
| --- | --- | --- |
| budget | Per-scan limit on queries, objects and fields. Exhaustion stops the run with no bundle. | `packages/collectors/src/dbig_collectors/runtime.py::Budget` |
| scan request | Signed, expiring request for a collector to scan a source. Carries no credentials. | `packages/contracts/src/dbig_contracts/scan.py::ScanRequest` |
| scan receipt | Collector's status answer for a scan request. | `packages/contracts/src/dbig_contracts/scan.py::ScanReceipt` |
| operator error | Failure the person running a command can fix from its message alone. | `packages/contracts/src/dbig_contracts/errors.py::OperatorError` |
| secret reference | Name of an environment variable that holds a secret. Configuration never holds the value. | `packages/contracts/src/dbig_contracts/security.py::secret_from_env` |
| golden artifact | Committed bundle and snapshot that pin the IR contract across versions. | `tests/test_golden.py` |
| graph tools | Graphify, Graft and Codebase Memory. They index this repository for developers and are not part of the product. | `scripts/graph_sync.py` |
