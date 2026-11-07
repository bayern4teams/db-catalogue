# Glossary

One fixed term per concept. Documents use these words and no synonyms.

## Roles

<!-- verified: bce5fee -->

| Term | Definition | Code |
| --- | --- | --- |
| collector | Process that runs beside one source with read-only credentials and writes a signed bundle. | `packages/collectors/src/dbig_collectors/runtime.py::collect` |
| builder | Central process that validates bundles and publishes a snapshot. | `packages/catalog-builder/src/dbig_builder/builder.py::assemble` |
| requester | Module that searches a snapshot, answers questions with a private model, and dispatches scan requests. | `packages/requester/src/dbig_requester/search.py::search` |
| planner | Module that builds a path graph from a snapshot and observations, and renders plans. It never runs a plan. | `packages/planner/src/dbig_planner/graph.py::build_graph` |
| reviewer | Bounded LangGraph workflow in the builder that asks a private model about ambiguous candidates. | `packages/catalog-builder/src/dbig_builder/reviewer.py::review_candidates` |
| source | One configured database (PostgreSQL, ClickHouse or MongoDB) with its own collector. | `packages/contracts/src/dbig_contracts/models.py::Source` |

## Artifacts

<!-- verified: bce5fee -->

| Term | Definition | Code |
| --- | --- | --- |
| bundle | Directory of JSONL files plus a signed manifest, written by a collector. | `packages/contracts/src/dbig_contracts/artifact.py::write_artifact` |
| snapshot | Catalogue the builder publishes (`nodes`, `edges`, `evidence`, `runs`, manifest). The only interchange contract between builder, requester and planner. | `packages/contracts/src/dbig_contracts/models.py::Manifest` |
| manifest | List of files, digests, counts and completeness of a bundle (signed) or a snapshot (unsigned). | `packages/contracts/src/dbig_contracts/models.py::Manifest` |
| complete / partial | Manifest completeness. `partial` means a source, capability or review did not finish. | `packages/contracts/src/dbig_contracts/models.py::Manifest` |
| diagnostic | Typed, value-free record of something that did not work, such as `IDENTITY_UNAVAILABLE`. | `packages/contracts/src/dbig_contracts/models.py::Diagnostic` |
| stable ID | ID derived from source, kind and a server-assigned native identifier, never from a display name. | `packages/contracts/src/dbig_contracts/ids.py::stable_id` |
| canonical JSON | UTF-8 JSON with sorted keys and no whitespace. The bytes that are signed. | `packages/contracts/src/dbig_contracts/artifact.py::canonical` |
| profile | Per-field statistics a source provides (null fraction, distinct estimate, width). | `packages/contracts/src/dbig_contracts/models.py::Profile` |
| value token | HMAC-SHA256 of a normalised sampled value. Opt-in. Lives only in a bundle. | `packages/contracts/src/dbig_contracts/models.py::ValueTokens` |
| graph (planner) | Derived planner index built from a snapshot and observations. A cache, never a contract. | `packages/planner/src/dbig_planner/graph.py::build_graph` |
| search index | Derived requester SQLite FTS5 index built from a snapshot. A cache, never a contract. | `packages/requester/src/dbig_requester/search.py::index_snapshot` |

## Relationships and evidence

<!-- verified: bce5fee -->

| Term | Definition | Code |
| --- | --- | --- |
| edge | Typed link between two nodes, with a status and evidence IDs. | `packages/contracts/src/dbig_contracts/models.py::Edge` |
| edge status | One of `declared`, `strong`, `tentative`, `rejected`, `observed`. | `packages/contracts/src/dbig_contracts/models.py::Edge` |
| declared key | Constraint the source itself enforces (primary, unique, foreign). | `packages/contracts/src/dbig_contracts/models.py::Constraint` |
| candidate | A `REFERENCE_CANDIDATE` edge: a guessed reference, never lineage. | `packages/catalog-builder/src/dbig_builder/builder.py::assemble` |
| evidence | Immutable, typed record that supports or opposes an edge. | `packages/contracts/src/dbig_contracts/models.py::Evidence` |
| containment | Share of a child field's distinct values found in a parent key. One-directional. | `packages/catalog-builder/src/dbig_builder/builder.py::_containment` |
| blocking rule | Rule that admits a pair for comparison (suffix, exact bare name, key name, measured uniqueness). | `packages/catalog-builder/src/dbig_builder/builder.py::assemble` |
| lineage | Observed or defined data movement from input to output. Distinct from a candidate and from an observed join. | `packages/planner/src/dbig_planner/paths.py::find_paths` |
| observed join | A join seen in a query (`JOINS_OBSERVED`). Proves use, not derivation. | `packages/planner/src/dbig_planner/graph.py::build_graph` |
| observation | Input record to the planner: SQL text, a pipeline run, or an OpenLineage event. | `packages/planner/src/dbig_planner/observations.py::SQLObservation` |
| join path | Ranked chain of reference edges between two objects. | `packages/planner/src/dbig_planner/paths.py::find_paths` |
| plan | Reviewable SQL or MongoDB read steps for a path. Never executed by this project. | `packages/planner/src/dbig_planner/plans.py::render_plan` |

## Operations

<!-- verified: bce5fee -->

| Term | Definition | Code |
| --- | --- | --- |
| budget | Per-scan limit on queries, objects and fields. Exhaustion stops the run with no bundle. | `packages/collectors/src/dbig_collectors/runtime.py::Budget` |
| scan request | Signed, expiring request for a collector to scan a source. Carries no credentials. | `packages/contracts/src/dbig_contracts/scan.py::ScanRequest` |
| scan receipt | Collector's status answer for a scan request. | `packages/contracts/src/dbig_contracts/scan.py::ScanReceipt` |
| operator error | Failure the person running a command can fix from its message alone. | `packages/contracts/src/dbig_contracts/errors.py::OperatorError` |
| secret reference | Name of an environment variable that holds a secret. Configuration never holds the value. | `packages/contracts/src/dbig_contracts/security.py::secret_from_env` |
| golden artifact | Committed bundle and snapshot that pin the IR contract across versions. | `tests/test_golden.py` |
| graph tools | Graphify, Graft and Codebase Memory. They index this repository for developers and are not part of the product. | `scripts/graph_sync.py` |
