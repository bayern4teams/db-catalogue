# Technical implementation contract

Status: binding design baseline, with all five package roles implemented and tested on synthetic inputs and, in CI, on live PostgreSQL, ClickHouse and MongoDB containers. This file records decisions with their dates and the code map. Open items and live verification are in [TODO.md](TODO.md). The research behind the decisions is in [ARCHITECTURE_RESEARCH.md](docs/ARCHITECTURE_RESEARCH.md). Terms are defined in [GLOSSARY.md](GLOSSARY.md).

## Package map and dependencies

<!-- verified: 6e43d33 -->

```mermaid
flowchart LR
  COL[dbig-collectors] --> CON[dbig-contracts]
  BLD[dbig-catalog-builder] --> CON
  REQ[dbig-catalogue-requester] --> CON
  PLN[dbig-lineage-planner] --> CON
  COL -.-> DRV["pg8000, clickhouse-connect, pymongo (extras)"]
  BLD --> RF[rapidfuzz]
  BLD --> LG[langgraph]
  BLD --> OAI[openai client]
  REQ --> OAI
  BLD --> LF[langfuse]
  REQ --> LF
  PLN --> SG[sqlglot]
  PLN --> NX[networkx]
```

No role imports another role. Every internal import goes to `dbig_contracts`, checked in all five packages at 6e43d33. `scripts/package_isolation.py` and the `package-isolation` CI job prove from the built wheels that the requester and planner install and import without each other. For the other roles the rule rests on import review. The versions and the reason for each library are in §4 and §17.1.

## Module map

<!-- verified: 6e43d33 -->

| Package | Module | Owns | Entry point |
| --- | --- | --- | --- |
| `packages/contracts/src/dbig_contracts` | `models.py` | IR record types, `Manifest` | `models.py::Record` |
| | `artifact.py` | Canonical JSON, signing, atomic write, read with checks | `artifact.py::write_artifact`, `artifact.py::read_artifact` |
| | `scan.py` | Scan request, signature, receipt | `scan.py::verify_scan` |
| | `security.py`, `errors.py`, `ids.py` | Secret resolver, loopback rule, operator errors, stable IDs | `security.py::secret_from_env` |
| `packages/collectors/src/dbig_collectors` | `runtime.py` | Discovery for the three engines, budget, bundle write | `runtime.py::collect` |
| | `scan.py`, `api.py` | Scan intake queue (SQLite) and its private HTTP route | `scan.py::accept_scan` |
| `packages/catalog-builder/src/dbig_builder` | `builder.py` | Candidates, evidence, snapshot | `builder.py::assemble` |
| | `reviewer.py` | Bounded LangGraph review | `reviewer.py::review_candidates` |
| | `state.py` | SQLite receipts, incremental runs | `state.py::assemble_incremental` |
| | `render.py` | Static HTML page | `render.py::render_catalog` |
| `packages/requester/src/dbig_requester` | `search.py` | FTS5 index and ranked search | `search.py::search` |
| | `answer.py` | Grounded answer | `answer.py::answer_question` |
| | `scan.py` | Scan dispatch, file or HTTP | `scan.py::dispatch_scan` |
| `packages/planner/src/dbig_planner` | `graph.py` | Path graph from snapshot and observations | `graph.py::build_graph` |
| | `observations.py` | Observation and OpenLineage models, SQL relations | `observations.py::sql_relations` |
| | `paths.py`, `plans.py` | Path search, SQL and MongoDB rendering | `paths.py::find_paths`, `plans.py::render_plan` |

Each role package (not `contracts`) has `cli.py` and `config.py`; collectors, requester and planner also have `api.py`. Tests are in `tests/`, helper scripts in `scripts/`, published JSON Schemas in `schemas/ir-v1/`, example configurations in `examples/local/`.

## Workflows

<!-- verified: 6e43d33 -->

**Collect.** One run reads one source and writes one bundle or nothing.

```mermaid
sequenceDiagram
  participant CLI as dbig collect
  participant RT as runtime.collect
  participant SRC as source
  participant ART as write_artifact
  CLI->>RT: config and output directory
  RT->>RT: resolve secrets, refuse plaintext off loopback
  RT->>SRC: discover within Budget
  SRC-->>RT: objects, fields, constraints, profiles
  RT->>ART: records and signing key
  ART-->>CLI: bundle, manifest written last
```

A reached limit or a missing namespace raises an operator error and writes no bundle (`runtime.py::collect`, `runtime.py::_refuse_missing_namespaces`).

**Assemble.** The builder turns signed bundles into a snapshot.

```mermaid
flowchart LR
  A["_read_signed: verify and read"] --> B[nodes and declared edges]
  B --> C["candidates by blocking rules"]
  C --> D["_overlap evidence"]
  D --> E["_composite_candidates"]
  E --> F["review_candidates"]
  F --> G["write_artifact: snapshot"]
```

`assemble` marks the snapshot `partial` for a partial bundle, a foreign key whose target is outside the snapshot, a reused earlier bundle, or a failed review (`builder.py::assemble`). The manifest field `partial_reasons` lists the causes. A failed review still publishes the deterministic snapshot.

**Ask.** The requester refuses before the model when it can.

```mermaid
sequenceDiagram
  participant U as caller
  participant A as answer_question
  participant S as search
  participant M as private model
  U->>A: question
  A->>A: refuse empty or over-long question
  A->>S: scope by named source, match any, top retrieval_limit
  S-->>A: entries
  A->>M: entries as data, strict JSON schema
  M-->>A: answer and citations
  A->>A: citations must be retrieved IDs
  A-->>U: answer or typed error
```

Typed errors: `INVALID_QUESTION`, `NO_CATALOGUE_MATCH` (no model call), `STALE_INDEX` and the `MODEL_*` codes, such as `MODEL_UNAVAILABLE_OR_INVALID`, `MODEL_UNREACHABLE`, `MODEL_TIMEOUT` and `MODEL_INVALID_CITATION` (`answer.py::answer_question`). A question that names lineage is allowed; only a model answer that claims lineage is rejected, as `MODEL_UNAVAILABLE_OR_INVALID` (`answer.py::answer_question`).

**Plan.** The planner never touches a source.

```mermaid
flowchart LR
  SN[snapshot] --> G["build_graph"]
  OB[observations] --> G
  G --> F["find_paths: join or lineage"]
  F --> R["render_plan"]
  R --> OUT["SQL, MongoDB pipeline, logical steps"]
```

Limits: `PlannerConfig.max_hops` (default 4) and `max_paths` (default 20) in `config.py`.

**Scan request.** The requester asks, the collector decides.

```mermaid
sequenceDiagram
  participant R as requester dispatch_scan
  participant K as collector accept_scan
  participant Q as SQLite queue
  participant W as process_next
  R->>K: signed request, file or HTTP
  K->>K: verify key, TTL, requester, scope, capability, replay
  K->>Q: insert as queued
  K-->>R: receipt
  W->>Q: take queued, set running
  W->>W: collect
  W->>Q: complete or failed
```

## Status life cycles

<!-- verified: 6e43d33 -->

```mermaid
stateDiagram-v2
  [*] --> submitted: requester dispatch
  submitted --> queued: accept_scan
  queued --> running: process_next
  running --> complete
  running --> failed
  queued --> failed: request expired before processing
```

Scan status values are `ScanReceipt.status` in `packages/contracts/src/dbig_contracts/scan.py`. The `Edge` model allows `declared`, `strong`, `tentative`, `rejected` and `observed`. The builder sets `declared` and `tentative`, and `rejected` for a candidate the owner rejected in `decisions_file`; the planner sets `observed` on observed joins; `strong` is reserved. Planner lineage links are `executed` or `defined`. A path result is `found` or `unavailable`; a path itself is `declared`, `observed`, `defined`, `partial` or `tentative` (`paths.py::find_paths`). A plan status is `ready_for_review`, `review_required` or `unresolved` (`plans.py::render_plan`).

## Key types

<!-- verified: 6e43d33 -->

```mermaid
classDiagram
  class Record
  Record <|-- Source
  Record <|-- Object
  Record <|-- Field
  Record <|-- Constraint
  Record <|-- Profile
  Record <|-- ValueTokens
  Record <|-- Evidence
  Record <|-- Edge
  Record <|-- Diagnostic
  Record <|-- ValueRanges
  class Manifest
  Manifest *-- FileEntry
  Manifest *-- Signature
```

All in `packages/contracts/src/dbig_contracts/models.py`. `Record` allows extra fields, so an older reader tolerates an added field. `SnapshotNode` is the strict union `Source | Object | Field` for `nodes.jsonl`.

## 1. Product boundary and non-negotiable behavior

<!-- verified: bce5fee -->

The product has four independently deployable roles: source collectors, a catalog builder, a catalogue requester, and a lineage and path planner. The requester and planner are independently installable Python packages with CLIs and optional HTTP JSON REST services. Collectors discover PostgreSQL, ClickHouse, and MongoDB metadata; the builder profiles within explicit budgets and infers *candidates* for same-source and cross-source references. The requester searches an existing snapshot, answers grounded natural-language questions through the private local model, and requests new scans. The planner returns observed transformation lineage, candidate join paths, and reviewable SQL/MongoDB plans without executing them. Similar values alone are never lineage. No central source-database login belongs in v1.

Each source has a separate collector process and network/credential boundary. One trusted assembler receives collector bundles. Cross-source sampled values may be compared **in memory**; raw values are discarded after feature extraction. Production requires an approved secure bundle transfer and common tokenization key distribution. The assembler's local LLM gets only minimized, redacted candidate context; it never gets credentials, raw sampled values, SQL literals, or live DB tools.

The initial scale assumption is about 10 sources, 200 objects, and 1,000 fields. All budgets, freshness windows, and limits are configuration, not hardcoded policy. Defaults below are proposals until measured and approved.

## 2. Execution topology

<!-- verified: bce5fee -->

The system context, deployment and data flow diagrams are in [ARCHITECTURE.md](ARCHITECTURE.md). Each source is its own security domain with its own collector.

Collector responsibilities: scope authorization, source discovery, bounded profiling, source-local feature extraction, permitted SQL/pipeline observation, redaction, bundle schema validation, manifest hashes, and atomic publication. Builder responsibilities: validation and identity resolution, run reconciliation, cross-source feature comparison, candidate blocking and ranking, evidence policy, local semantic review, QA, and deterministic catalog publication. The requester owns search, grounded answers, and authorized scan dispatch. The planner owns lineage parsing/import, typed path search, and query-plan rendering. Both consume versioned artifacts without importing each other or source drivers.

No collector waits for another collector. A run can mark a missing source as `partial`, preserve prior known state with age metadata, and avoid silently deleting unseen objects. Source-local exceptions become typed diagnostics.

## 3. Repository and deployable boundaries

<!-- verified: bce5fee -->

Package boundaries:

```text
packages/contracts/         # IR models, JSON Schema export, artifact validation, signatures
packages/collectors/        # source-site CLI, source drivers, budgets, scan intake
packages/catalog-builder/   # candidates, evidence, reviewer, snapshot, HTML page
packages/requester/         # search, grounded questions, scan dispatch, CLI and REST
packages/planner/           # lineage, paths, SQL and MongoDB plans, CLI and REST
tests/                      # pytest suite; tests/golden/ir-v1 holds the committed golden artifacts
schemas/ir-v1/              # published JSON Schemas per IR version
examples/local/             # safe example TOML; local runs use their own copies
scripts/                    # developer graph, CI and benchmark helpers, not runtime modules
.githooks/                  # pre-commit and post-commit graph refresh
.github/workflows/          # ci, docs, rules, release
.gitlab-ci.yml              # GitLab mirror of the GitHub workflows
```

Use a `uv` workspace for development, but publish separate wheels: contracts, collectors, catalog builder, catalogue requester, and lineage planner. Each role depends only on contracts and its own justified libraries; requester and planner never import one another. Collector driver extras install only the matching driver. The builder, requester, and planner have no source drivers or source credentials. REST serving is an optional extra on requester/planner. A shared workspace lock does not prove package isolation: CI must install each wheel in a clean environment and test CLI, API, and file-contract compatibility independently. `uv` is not required at runtime.

## 4. Module and third-party decisions

<!-- verified: bce5fee -->

| Concern | Decision | Specific use and boundary |
| --- | --- | --- |
| Language and build | Python 3.12+, `pyproject.toml`, `uv`, `uv.lock`, hatchling or equivalent build backend after packaging spike | Reproducible environment; verify Windows/Linux wheel availability and lock all extras. |
| CLI | Click | Four command groups: `dbig`, `dbig-builder`, `dbig-requester`, `dbig-planner`. Commands are listed in §12. Exit codes and operator errors as in §12a. |
| Config and IR | `tomllib`, Pydantic v2, standard `typing.Protocol` | TOML config, typed records, strict validation, secret references; no arbitrary YAML. |
| Serialization | standard `json`, `hashlib`, `pathlib`, `tempfile`; `jsonschema` as a dev dependency only | Canonical UTF-8 JSONL, digests, atomic replace. Pydantic validates records at run time; `jsonschema` checks the published schemas in tests and `scripts/export_schemas.py`. |
| PostgreSQL | `pg8000` | Read-only direct catalog SQL, timeouts and TLS. No ORM and no Psycopg in permissive core. |
| ClickHouse | `clickhouse-connect` | `system.*`, engine metadata, bounded queries and permitted query log. |
| MongoDB | `pymongo` bundled `bson` | Collection/index/validator metadata, sampled documents; never install unrelated `bson`. |
| SQL lineage | SQLGlot | Parse supported PostgreSQL/ClickHouse SQL; our resolver binds identifiers to catalog objects. Unsupported SQL is diagnostic, never fabricated lineage. |
| Matching | RapidFuzz plus our deterministic policies | Name similarity is one feature, never sufficient alone. |
| Graph algorithms | NetworkX `MultiDiGraph`, replaceable planner-local view | Parallel typed edges and bounded path search; canonical persisted IR is independent of NetworkX. |
| Pipeline interchange | OpenLineage spec; `openlineage-python` extra only if needed | Import/export observed job/run/dataset facets, preserve provenance. |
| Local LLM | vLLM OpenAI-compatible server on Linux or WSL2 GPU; `openai` Python client against its private base URL; trial Qwen3 `8B` | One bounded structured-output call per ambiguous candidate batch; no cloud fallback. Record model digest, prompt/schema version, latency. Confirm GPU support and JSON conformance on the pinned versions. |
| Review workflow | LangGraph `StateGraph` | Explicit nodes for redaction, model call, validation and evidence conversion. Our deterministic assembler owns scheduling and publication. No autonomous tools, DB access, dynamic handoffs or model-selected SQL. Top-level `langchain` and `langgraph-swarm` are excluded; `langchain-core` may enter transitively with LangGraph. |
| Runtime state | SQLite stdlib and FTS5 where available | Local builder run index; requester search index; planner path index. Each package owns its own rebuildable state. Check FTS5 availability at startup and fail with a typed capability error. |
| Requester API | FastAPI and Uvicorn in the requester `api` extra | Private HTTP JSON REST over the same application functions used by the CLI; no source drivers or planner dependency. |
| Planner API | FastAPI and Uvicorn in the planner `api` extra | Private HTTP JSON REST over the same deterministic planner functions used by the CLI; no source credentials or query execution. |
| Telemetry | stdlib structured logging plus self-hosted Langfuse Python SDK | Trace reviewer nodes, local model latency/usage, validation outcomes and redacted error codes with approved metadata only. Never export raw prompts, outputs, SQL, values or credentials. Langfuse is observability, not source catalog state. |
| Test/tooling | pytest, Ruff, mypy, pip-audit, `scripts/supply_chain.py` (CycloneDX SBOM and license report from `uv export`) | CI runs lint, types, tests, vulnerability audit, supply chain checks and live-source tests (§15). |
| Source-code intelligence | Graphify, Graft, Codebase Memory MCP | Developer-only structural graphs. Graphify first, then Graft, then CBM. Git hooks/CI refresh them without LLM/deep modes. |
| Ponytail | Ponytail plugin skills | Agent design/review discipline; use all named skills when available, record results and do not equate a slash command with a CI test. |

Application dependencies are ranges in each package `pyproject.toml`, resolved and hashed in `uv.lock`. The developer graph tools are pinned in `pyproject.toml` `[tool.dbig.graph-tools]` (§15).

## 5. Configuration and secrets

Configuration is explicit TOML plus `DBIG_*` environment overrides, with a strict precedence contract: CLI explicit flag > process environment > TOML > documented safe default. `.env` is a developer-local input to a launcher, never read implicitly in production. `.env` is ignored; `.env.example` has harmless placeholders. Secret values are resolved at runtime from process environment or an approved secret manager reference. Never put passwords, DSNs with embedded credentials, HMAC keys, tokens, or private SQL in source, examples, artifacts, logs, test snapshots, or graph tool indexes.

Source config fields: source ID, kind, connection reference, allowed databases/schemas/collections, TLS policy, read timeout, query budget, sample rows/documents, byte budget, concurrency, history access flag, permitted lineage event path, sensitivity policy. Assembler config fields: bundle roots, output path, freshness threshold, matching thresholds, private vLLM base URL/auth reference/model/digest, request timeout, batching, GPU concurrency, self-hosted Langfuse URL/key references, telemetry enablement, logging, bundle verification policy. Every user-supplied path is canonicalized and constrained to an approved root. The vLLM and Langfuse services have separate deployment secrets and network policies.

Secrets are never passed on command lines. Collector has least-privilege read permissions and a query cancellation timeout. The cross-source comparison key has run scope, a key ID only in manifests, and secure distribution to authorized collectors. If no shared key is available, disable cross-source value evidence and record that capability as unavailable rather than comparing incompatible hashes. Decide retention and cryptographic signing before production rollout.

## 6. IR and artifact contract

Use append-style JSONL files in a bundle directory plus a manifest written last. Files: `sources.jsonl`, `objects.jsonl`, `fields.jsonl`, `constraints.jsonl`, `profiles.jsonl`, `observations.jsonl`, `diagnostics.jsonl`, `tokens.jsonl` and an optional `ranges.jsonl`. The assembler output adds `nodes.jsonl`, `edges.jsonl`, `evidence.jsonl`, `runs.jsonl`, and `manifest.json`. Canonical JSON uses stable key ordering and UTF-8; IDs are deterministic from normalized source identity and source-native stable identifiers where available. Do not base IDs on display names alone. Every record has `ir_version`, `record_type`, `id`, `source_id`, `observed_at`, `collector_version`, and provenance where applicable.

Manifest fields: format/IR version, source/run ID, creation window, collector build, scope hash, file paths, sizes, SHA-256 digests, counts, content classification, key ID if tokenized, optional signature metadata, diagnostics summary, optional `partial_reasons` (code names saying why a snapshot is `partial`; absent when complete, ignored by older readers), and completion status. Readers reject unknown major versions, duplicate IDs, unsafe paths, missing files, digest mismatches, invalid JSON, impossible timestamps, missing references, and record counts that disagree with the manifest. Minor additions must be backward compatible. The producer writes temp files, fsyncs, validates, and atomically renames the manifest last; the consumer never reads an incomplete directory.

Object kinds: database, schema, table, view, materialized view, ClickHouse dictionary/engine-bearing table, MongoDB collection, job, and run. Field kinds include relational columns and normalized MongoDB document paths with type unions and array cardinality. Graph edge types: `CONTAINS`, `DECLARES_FK`, `REFERENCE_CANDIDATE`, `JOINS_OBSERVED`, `WRITES`, `READS`, `DERIVES_FROM`, `RUNS_JOB`. Each edge has status (`declared`, `strong`, `tentative`, `rejected`, `observed`), direction, evidence IDs, explanation, source/run provenance, first/last seen, and limitations. A field-name match can create only a *candidate*.

Evidence records are immutable and typed: catalog key, uniqueness/index, compatible type, name similarity, value containment/overlap, join observation, query-plan observation, SQL AST mapping, OpenLineage facet, LLM suggestion, and counterevidence. Preserve metrics and denominators; never publish an uncalibrated similarity as a probability. `confidence` remains null until calibrated on labeled data. Every relationship cites at least one non-LLM evidence record; lineage cites execution/definition metadata and parse resolution.

### 6a. Identity, rename, canonical encoding and migration (settled 2026-09-27)

**Identity.** `stable_id(source_id, kind, native_id)` hashes those three parts and prefixes the
kind, so an ID is reproducible from source facts and never from a display name. Each connector
supplies a source-native identifier: PostgreSQL uses `pg_class.oid` for an object and
`oid:attnum` for a field; ClickHouse uses the table UUID and `uuid:position`; MongoDB uses the
`listCollections` `info.uuid` and `uuid:path`. All three are assigned by the server and are
independent of the name, which is what makes a rename survivable.

**An object with no native identity is not catalogued.** ClickHouse reports the all-zeros UUID
for a table on a non-Atomic database engine, and a MongoDB view has no `info.uuid`. In both
cases the collector emits an `IDENTITY_UNAVAILABLE` diagnostic and drops the object and its
fields, rather than minting a name-derived ID. User decision on 2026-09-27: keep dropping and
make it loud. A catalogue entry whose identity cannot survive a rename would attach history and
edges to something that silently becomes a different object, which is the opposite of the
precision-over-recall rule. The cost is explicit: those objects never appear.

The drop is now visible without reading `diagnostics.jsonl`. The manifest carries
`diagnostics`, a count per diagnostic code, derived by the writer from the validated rows so a
producer cannot under-report, and checked by the reader against the actual rows so a manifest
cannot disagree with its own file. Any diagnostic already makes a bundle `partial`.
`diagnostics.jsonl` validates against a strict `Diagnostic` model whose `code` is required and
constrained, because per-code counts can only total the row count if every row has exactly one
well-formed code. Snapshots carry no diagnostics file and so report no summary at all, which is
different from reporting an empty one.

**Rename semantics.** Where native identity exists, a rename keeps the ID and changes `name`;
the object is the same object and its history and edges follow it. Where native identity does
not exist the object was never catalogued, so the delete-plus-add case does not arise in
practice. This is verified rather than asserted: the engines' own documentation is unreachable
from the build environment, so `tests/test_live_collectors.py` renames a real table, column and
collection on the pinned `postgres:16.15`, `clickhouse-server:25.8.33.6` and `mongo:7.0.41`
service containers and asserts the ID is unchanged while the name changed. Those tests run only
in CI, under `DBIG_RUN_INTEGRATION=1`; they are skipped locally and their result is claimed only
from an actual run.

**Canonical encoding.** `canonical()` emits UTF-8 JSON with sorted keys, no inserted whitespace,
`NaN` and infinities refused, and `None` fields omitted. Three properties were measured rather
than assumed. A round trip through JSON reproduces the bytes exactly. Input key order does not
affect the output. A timestamp must be UTC: the same instant written at a different offset
serialises differently, so a non-UTC offset is refused at validation with a message naming the
cause, rather than normalised, because a normalised record would fail its signature check later
and report the wrong cause. Unicode names are deliberately **not** normalised, so an NFC and an
NFD spelling of the same word produce different bytes; they are different identifiers in the
source, and folding them would merge objects the source keeps apart. Floats use Python's
shortest round-tripping representation.

**The contract is pinned by a committed artifact, not only by tests.** Every other artifact test
writes and reads inside one process, so it can only prove this version agrees with itself. The
requester and planner install as separate wheels and are expected in the field to be older than
the snapshot they read, so `tests/golden/ir-v1/` holds a collector bundle and a catalog snapshot
produced once and committed, and `tests/test_golden.py` reads them back on every run: signature
verification, the published JSON Schemas, manifest digests, a byte-identical read-and-rewrite,
tolerance of an added unknown field, and refusal of a hand-edited row.
`scripts/make_golden.py` regenerates them from a frozen clock, so a regeneration with no
contract change produces no diff and any diff is the signal to review. Adding a manifest field
with a container default is caught there mechanically, which is how the rule below is enforced
rather than merely written down.

**Migration policy.** A reader refuses an unknown major version and accepts additive minor
fields. One rule follows from the signature construction and is easy to get wrong: the signature
is taken over the manifest with `None` fields excluded, so **a new optional manifest field must
default to `None`, never to an empty container.** A field defaulting to `{}` would change the
signed bytes of every manifest, including those written before the field existed, and silently
invalidate their signatures. `diagnostics` therefore defaults to `None`, absent means unreported
rather than zero, and a bundle from a producer that predates the field still verifies. A
compatibility test signs an older-shaped manifest and reads it back to hold that guarantee.

## 7. Collector implementation

Candidate review (2026-10-13, DEC-10): `[builder.review] batch_size` (default 5) candidates per model call, `max_candidates` overall; the first failing batch ends the review, keeps the decisions already made and reports that batch's failure code. Keys (2026-10-13, E1): `dbig_contracts.security.key_from_env` requires 32 bytes and turns a shorter key into a one-line command error naming the variable.

Each engine has a `discover_<engine>` function in `runtime.py`; `collect` runs it under a `Budget` and writes the bundle. Budget accounting covers queries, wall time, rows, bytes and source-specific concurrency. Cancel on expiry, emit partial diagnostics, and respect source read-only transactions where supported. No unrestricted `SELECT *` on large tables.

PostgreSQL: query `pg_catalog` for namespaces, relations, attributes, keys, FK constraints, indexes, view/materialized-view definitions, statistics and comments where allowed. Use quoted identifiers and parameterized dynamic values. Prefer catalog row estimates and existing statistics; use bounded sample queries only when permitted. Record partitioning and composite key order. Capture observed SQL only from configured logs/audit/job metadata, never assume `pg_stat_statements` includes result data or execution time history sufficient for lineage.

ClickHouse: query `system.databases`, `system.tables`, `system.columns`, `system.data_skipping_indices`, relevant constraints/projections/parts where available. Preserve engine, partition/sort/primary expressions and distributed/shard semantics. Capture `system.query_log` only with explicit access, retention bounds and redaction. Mutations and materialized views require engine-aware parsing; do not convert a same-name table relation into a FK.

MongoDB: list databases/collections, indexes, validator JSON Schema, collection options and safe stats; sample documents using bounded strategy, flatten nested paths with array markers, type counts, null/missing distinction and path depth limit. Preserve `_id` and index information. A path ending in `Id` is only a lexical candidate. Aggregation pipelines or change-stream/job records count as lineage only when observed and resolvable.

Optional value-free column profiles (`format_profiles`): one whole-column read-only aggregate per text, integer or UUID column on PostgreSQL and ClickHouse, the same profile from the existing sample on MongoDB, and the integer minimum and maximum in the local-only `ranges.jsonl` (written only when it has rows); see 8b.

Collector problems become diagnostic codes such as `IDENTITY_UNAVAILABLE`, `STATS_UNAVAILABLE`, `FORMAT_PROFILES_SKIPPED`, `SAMPLE_BYTE_LIMIT` and `OUT_OF_SCOPE_REFERENCE`, and an exhausted budget stops the run with `BUDGET_EXHAUSTED`; the design names `PERMISSION_DENIED`, `TIMEOUT`, `UNSUPPORTED_VERSION` and `PARTIAL_SCAN` were never implemented; never silently substitute empty results for unavailable metadata.

### 7a. Transport security and the readable catalogue (settled 2026-09-30)

User decision on 2026-09-30: the user's databases are on other servers on their network, so
their runs use verified TLS; a loopback-only opt-out is allowed for local development. Source
config therefore has `tls` (default `true`) and `tls_ca_file` (a PEM of CA certificates, unset
means the operating system store, refused when `tls = false` or when the file is missing). The
CA file is passed explicitly because, verified from the installed driver source, the drivers
find trust roots differently: pg8000 uses `ssl.create_default_context()`, ClickHouse Connect
0.15.1 takes `ca_cert=` and otherwise uses the OS store, and PyMongo 4.18.1 takes `tlsCAFile`
and handles `SSL_CERT_FILE` differently on Windows. `tls = false` requires every host to be a
loopback IP literal, the same rule as plain HTTP to a private model endpoint. The bundle's
`Source.transport` records which applied; it is an optional additive field, absent from older
bundles and excluded from canonical bytes when absent.

`dbig-builder render --snapshot S [--bundle B ...] --config C --out catalog.html` writes one
self-contained HTML page. By user decision it reads the snapshot plus the bundles it was built
from rather than extending the snapshot contract: statistics, keys, indexes and diagnostics
stay in bundles, so the requester and planner contracts are unchanged. A bundle is accepted only
if its signature verifies with the builder's keys and `bundle_run_id` (the same derivation the
assembler uses) is among the snapshot's runs. A source with no bundle is rendered from the
snapshot with a visible notice. Output is deterministic for the same inputs and written
atomically.

### 7b. Comments, search and plans on real catalogues (settled 2026-09-30)

User decisions on 2026-09-30, after running the requester and planner on a real
three-database snapshot:

- **Comments.** `Object.description` and `Field.description` carry the owner-written comment,
  optional and additive (absent from canonical bytes when `None`). PostgreSQL reads
  `obj_description`/`col_description` and ClickHouse `system.tables.comment`/
  `system.columns.comment` (verified on 25.8.33.6), both inside queries already made, so the
  query budget is unchanged. MongoDB has no comments. Control characters are removed and text
  over 1,000 characters is cut with an ellipsis. Shown on the page, searchable, and given to
  `ask`; `include_comments = false` turns collection off.
- **Search.** The index holds each entry's name, its owning table and schema, and its comment.
  Terms match as prefixes and results are ranked by bm25 with weights 10/4/1 over those three.
  `match = "all"` (default, the CLI and API) requires every term; `match = "any"` (used by
  `ask`) drops common words and ranks by any term. `ask` no longer falls back to unfiltered
  entries when nothing matches; it returns `NO_CATALOGUE_MATCH` without calling the model.
- **Plans.** A cross-source path is split into runs of consecutive objects in one source; each
  run is one read (a joined SELECT or a `$lookup` pipeline), and `logical_join` steps remain
  only between runs. A path that leaves a source and returns is separate reads.
- **Columns and key-filtered reads (settled 2026-10-04).** A SQL read selects every catalogued
  column by name with a unique output name `<table>__<column>` (`<table>_<position>__<column>`
  when a table repeats), instead of `SELECT *`, which returned one `id` per joined table. An
  object with no catalogued fields falls back to its `*` with a warning. In a cross-source plan
  the first read is a bounded sample; every later read is restricted to the join keys the read
  before it returned, so the final join covers every match for that sample. Before this, each
  read took an independent `LIMIT` sample, and the final join could be empty although matches
  existed. Keys are never written into a plan: each later step lists `parameters` (`name`,
  `filters_field`, `values_from_step`, `values_from_column`) and its `parameter_binding`:
  PostgreSQL `= ANY(%(name)s)` bound to an array (`psycopg`, or pg8000 with `pyformat`),
  ClickHouse server-side `IN {name: Array(T)}`, MongoDB `$expr`/`$in` on `$$name` bound through
  the aggregate command's `let`. A composite key filters each column separately, a superset that
  the final join narrows exactly. Each read keeps the row limit, and the plan warns that a later
  read returning exactly that many rows may be incomplete. Verified by executing plans on the
  three pinned servers against ground truth (`tests/test_live_plans.py`). The planner still
  executes nothing, and cross-source plans stay `unresolved` until a staging target is chosen.
- **Limits.** Reaching `max_queries`, `max_objects` or `max_fields` keeps stopping with no
  bundle and an operator message.

- **Model and index error codes (catalogue fixes).** Derived from exception types and structure, never from content: `MODEL_UNREACHABLE`, `MODEL_REJECTED_REQUEST`, `MODEL_TIMEOUT`, `MODEL_TRUNCATED` (`finish_reason: length`), `MODEL_INVALID_CITATION`, `MODEL_ANSWER_UNEXPLAINED` (a bare verdict after `answer_retries` retries, section 12a), `MODEL_ANSWER_TOO_LONG` (answer text over the optional `[requester.answer] max_answer_chars`, unset = no limit; `max_tokens` is likewise optional, unset = none sent, for `ask` and since P6-5 for the builder review `[builder.review] max_tokens` too; user decisions 2026-10-17/18 and 2026-10-19, DEC-18); the builder keeps `MODEL_REVIEW_FAILED` as the partial reason beside the specific code. `STALE_INDEX` (index version or snapshot changed since `dbig-requester index`): `search` raises it, `ask` returns it, REST answers 409 on search and 503 on questions. `NOT_ESTABLISHED_BY_CATALOGUE` is an answer outcome, not an error. A model failure of `ask` also carries `error.reason` (P7-8, F18), structure only so causes that share one code can be told apart: `exception` (class name), `schema_errors` (Pydantic error types, for example `missing`, `extra_forbidden`, `json_invalid`, `literal_error`), `finish_reason`, `reply_length` (int), `citation_check` (`unknown_id` | `none`); a key that does not apply is absent. Never reply text, an ID from the reply, a field name or a value. The code and reason are also set on the Langfuse span metadata; the CLI prints and the REST app returns `error` whole.
- **Manifest counts (catalogue fixes).** Optional, written only when non-zero so other snapshots' bytes are unchanged: `partial_reasons` (codes), `dropped_zero_overlap_candidates` (measured 0-of-N against a complete parent, A1/D2, DEC-19) and `dropped_ordinary_name_candidates` (DEC-20: a same-name pair whose parent is not a declared key and whose name is not reference-style, counted per child-parent pair; renamed in P6-4 from `dropped_unmeasured_key_name_candidates`, which counted DEC-16's unmeasured pairs). Counts only, never values. A `key_name` candidate on a weak ClickHouse key carries `weak_key_claim` evidence.
- **Cardinality probe (DEC-14, 2026-10-14).** Optional, default off: `cardinality_probe`, `cardinality_probe_max_columns` (20), `cardinality_probe_max_rows` (100000). ClickHouse: one `SELECT count(), uniq(col)... FROM (SELECT cols FROM db.table LIMIT n)` per table with `max_threads = 1` (F9, 2026-10-19: the count covers exactly n rows and the server reads about one block more, measured about 2x on 26.8 against 7.8x for the earlier `max_rows_to_read`/`break` query); MongoDB: one `$limit` + `$facet` of `$group`/`$count` per collection. Charged to `max_queries`, needs the `sample` scan capability, emits `sample` profiles with `distinct_estimate`, `uniqueness` and `sample_count` only; a failure is a `STATS_UNAVAILABLE` diagnostic.
- **Same-name keys and ties (DEC-20, F10, 2026-10-19).** A child whose name equals a parent column's links by `key_name` when the parent is a declared key, or when the parent is only measured-unique or a weak ClickHouse key claim and its name is reference-style (stem plus `id`/`_ref`/... suffix). An ordinary name (`email`, `description`, `freeMails`) on an undeclared parent never links, whatever the measured overlap (replaces DEC-16's overlap allowance). Candidates with equal scores are cut to `top_k_per_field` by evidence: same source first, then declared key, weak key claim, measured uniqueness, then id.
- **MongoDB collections (corrected 2026-10-04).** `sample_rows` is per collection, as it is per
  table for PostgreSQL; it was a total for the source, so after one collection that large every
  later collection was left unsampled and, having no validator, had no fields, with no
  diagnostic. `max_sample_bytes` still bounds the whole source and now says which collections it
  left unsampled. Collections named `system.*` are MongoDB's own (view definitions, profiler,
  time-series buckets) and are no longer catalogued. A view has no collection UUID and is still
  left out under §6a; by user decision on 2026-10-05 it is reported as `VIEW_NOT_CATALOGUED`, an
  informational diagnostic that is shown but does not make the bundle `partial`
  (`INFORMATIONAL_DIAGNOSTICS` in the collector). The collection it reads is catalogued.
- **Observed joins are join paths (user decision 2026-10-05).** Each `ON a.x = b.y [AND ...]`
  in an observed SQL statement, with every column qualified by a table or alias in the same
  SELECT, becomes a `JOINS_OBSERVED` reference with status `observed`, its column pairs, and the
  IDs of the observations that showed it (`observed_join_columns`). The tables come from the
  columns, not from the order in `FROM`. An `ON` with an OR, a non-equality, an unqualified
  column or `USING` is skipped rather than guessed, and a column that does not resolve to exactly
  one catalogued field reports `UNRESOLVED_SQL`. Between the same two objects, a declared key is
  preferred to an observed join, which is preferred to a candidate; a path through an observed
  hop has status `observed`, and its plan warns that the hop came from query logs. A SELECT join
  is still never lineage.

## 8. Profiling and cross-source value evidence

Per field, compute bounded null/non-null counts, approximate or exact distinct count with method label, min/max for allowed types, length histogram, top-frequency sketches only if safe, and type/nullability distribution. Use source statistics where trustworthy, annotate freshness. Sample selection and size are recorded. Sensitive fields can be metadata-only. Value normalization is type-aware; preserve case/locale policy, leading zeros, time zone, binary encoding, and null distinction. Candidate pair comparison requires compatible normalized types and direction checks.

Raw sample values exist only in collector memory and, for allowed cross-source comparison, controlled assembler memory. Persist HMAC tokens or aggregate overlap metrics only according to the data policy; plain hashes are insufficient against dictionary attacks for low-entropy values. Encrypt bundle transport/storage if token sets are exported. Clear buffers on process exit; Python cannot guarantee memory zeroization, so deployment isolation and short retention are required. Never put raw values in LLM context, logs, graph artifacts, or developer code graphs.

Initial candidate blocking: compare only permitted source pairs; require compatible value domains or explicit manual relationship hint; then generate top-K per field by normalized lexical similarity plus key/uniqueness cues. Compute evidence on blocked pairs, not all 1,000² pairs. Support composite keys by ordered tuple comparison only when component mapping is justified. Direction asks whether child values are contained in a candidate unique parent key, plus null/duplication rates, join evidence and counterexamples. Abstain when sample support or uniqueness is insufficient. Cross-source matches are `REFERENCE_CANDIDATE` unless observed joins or stronger evidence warrants an approved status tier.

### 8a. Cross-source value evidence as implemented (2026-09-27)

Tokens are opt-in twice over: a collector produces them only when sampling is configured *and* a
`value_token_key_env` reference with a matching `value_token_key_id` is given. A key without an ID
is refused, because an ID is what makes two sides comparable, and an ID without a key would claim
a comparison that was never computed.

A token is `HMAC-SHA256(run key, normalised text)`. Keyed, not hashed: an unkeyed digest of a
low-entropy value such as a small integer or a country code is recovered by trying every
candidate. `exact_text` normalisation does not fold case or trim, because §8 requires case, locale
and leading zeros preserved; it does mean integer `123` and string `"123"` agree, which is the
cross-source case that matters most, a MongoDB string identifier pointing at a relational integer
key. Booleans, bytes and structured values are counted as unusable rather than coerced, since no
normalisation for them would be agreed by two different engines.

Tokens ride in their own `tokens.jsonl` inside the bundle, so an operator can see from the
manifest whether a bundle carries them at all and a deployment can choose not to transport that
file. A bundle is a controlled transfer; the published graph is not, and no token crosses into a
snapshot.

PostgreSQL sampling is one bounded read per table, with the discovered columns named explicitly
rather than `SELECT *`, and the row limit bound rather than interpolated. The sample is the first
rows the server returns, which is biased, and `sample_method` records exactly that. §20 rules out
the alternatives that would look fairer: `ORDER BY random()` and unbounded scans are the reads
that hurt a production source, and `TABLESAMPLE` plus `LIMIT` guarantees neither a cheap nor an
unbiased fixed-size sample.

ClickHouse sampling (F7, 2026-10-19) is the same: one `SELECT <columns> FROM db.table LIMIT n` per
table, plain non-nested columns only, the limit a bound parameter, `max_execution_time` set and the
read charged to `max_queries`. A materialized view is read (stored rows, as PostgreSQL reads
matviews); a View or LiveView is not, since reading one runs its query. A read the server refuses
costs that table its tokens and leaves a code-only `STATS_UNAVAILABLE` diagnostic, never the
server's text.

Every token set records `complete` (P6-1, owner decision 2026-10-19, DEC-19): true when the read
returned fewer rows than `sample_rows` (and, for MongoDB, no byte budget and no `max_array_elements`
cut it), so the set holds every value of the column. An older bundle without the field counts as a
sample.

The assembler measures **containment**, not similarity: a reference means the child's values are
drawn from the parent's key, so the question is one-directional and the denominator is the child's
distinct count. Jaccard overlap is reported alongside it, because a high containment on a tiny
child sample against a huge parent is weak and the two numbers together show that. The evidence
carries both metrics, both sample counts, both sample methods, the key ID, the minimum support,
and `lower_bound_only` when either token set was capped.

It **abstains** rather than reporting a meaningless number in four cases: either side has no
tokens, the two were keyed under different key IDs, the two were normalised differently, or either
distinct count is below `MIN_TOKEN_SUPPORT`.

A capped token set keeps the smallest keyed hashes, so two capped sets are coordinated bottom-k
sketches. When either side is capped, both are compared only at or below the smaller of the
capped sides' largest kept hash, where each side holds every hash it saw; the child's support is
what that range holds (DEC-19). A measured zero drops a candidate only when the parent is
`complete`; a zero against a sampled parent is kept as `value_containment` with reason
`parent_sampled` and the sentence `parent sampled, a zero is not a measurement`, and the planner
does not count it as value evidence. A ClickHouse parent larger than `sample_rows` is therefore
never a source of a trusted zero; a full-column sketch read is the upgrade path. The different-key case matters most: those tokens are
unrelated byte strings, so their zero overlap would read as a counterexample rather than as
ignorance.

**What the number means (P7-1, F14).** A `value_containment` with shared values adds `basis`,
`estimate`, `lower` and `upper`. `basis` is `lower_bound` when the parent was not read whole
(`parent_complete` false), or a capped side was not compared inside the common hash range: then
`estimate` = `lower` = the observed fraction, `upper` is null, and the candidate sentence reads
`value overlap at least X% of N sampled child values (parent only partly read; the true overlap
may be much higher)`. Otherwise `basis` is `measured`: `lower`/`upper` are the Wilson score
interval on shared/N (N = the child's distinct sampled values in the compared range), the
two-sided level is `[builder] overlap_confidence_level` (default 0.95, strictly between 0 and 1,
z from `statistics.NormalDist`), and the sentence reads `value overlap ≈X% (95% CI a-b%) of N
sampled child values`. The zero path (`parent_sampled`) and `value_overlap_not_measured` keep
their wording and carry no basis. The requester copies the four fields unchanged into each
relationship body (index version 8).

Containment never promotes an edge. It is cited by the `REFERENCE_CANDIDATE` edge and changes
neither its status nor its `confidence`, which stays null. Promotion needs the calibration that
§15 requires and that does not exist yet.

### 8b. Which pairs become candidates (widened 2026-09-28)

Evidence is worthless on a pair that never gets compared, and the original blocking admitted very
few. A child field had to end in `_id`, and a parent had to carry a declared single-column unique
constraint, so a column called `owner_ref` or a genuinely unique key on a table that never declared
one could not become a candidate however well its values contained.

Three rules now decide admission, each deliberately narrow:

1. **Suffix rule.** A child whose name ends in `_uuid`, `_code`, `_num`, `_ref`, `_key`, `_id`,
   `_fk` or `_no` announces itself as a reference, so the stem is matched fuzzily against the
   parent object's singularised name at the configured threshold. The longest suffix wins, and a
   name that is nothing but a suffix is not stripped to an empty stem that would match everything.
2. **Exact bare-name rule.** A child with no such suffix announces nothing, so only an exact match
   against the singularised parent object name counts. This catches a column called `customer`
   pointing at `customers` while refusing `custom`, which any fuzzy threshold would have accepted.
   Fuzzy matching here would trade precision away for a flood of coincidental spellings.
3. **Measured uniqueness.** A parent field whose profile reports uniqueness at or above
   `MEASURED_UNIQUE` (0.99) may be a key with nothing declared. The evidence kind is
   `measured_uniqueness`, never `catalog_key`: a measurement and a catalog assertion are different
   claims and must not be read as the same strength. The threshold is pinned by a test, because a
   number that decides which relationships get proposed is a contract rather than a tunable.

A child measured entirely null is not compared at all. It references nothing, so name evidence for
it would describe a relationship with nothing behind it. Sparse is not empty: an optional foreign
key is mostly null by design and is still compared.

The `name_similarity` evidence records which rule admitted the pair in `blocking_rule`, so a reader
can tell a fuzzy suffix match from an exact bare-name match without re-deriving it.

**Rules decided 2026-10-05** (user, after `tests/test_live_shapes.py` exposed both gaps):

- Rule 4: **Declared key first.** Measured uniqueness (rule 3) only proposes a column of a table that
   declares no key at all, single or composite. On a two-row lookup table every column measures
   unique, and `regions.name` was proposed beside the declared `regions.code`.
- Rule 5: **Case boundaries.** A name is split at case boundaries before the suffix rule
   (`AccountID` -> `account_id`, `customerId` -> `customer_id`), so camelCase and PascalCase keys
   reach rule 1; snake_case is unchanged.
- Rule 6: **Key name.** A child whose name equals, case-insensitively, a parent's declared
   or measured-unique single-column key name (measured-unique added 2026-10-13, DEC-12; with
   sampling on the overlap check guards it) is admitted with `blocking_rule = "key_name"`, whatever the parent
   table is called; generic names (`id`, `_id`, `uuid`, `key`, `code`, `num`, `ref`, `fk`, `no`)
   are excluded, since every table has an `id`. This links `AccountID` in ClickHouse and MongoDB
   to PostgreSQL's `"Customer Accounts"."AccountID"`, which rule 1 cannot (`account` is too far
   from `customer account`).
- Rule 8: **Parent column must resemble the child (2026-10-13, DEC-1).** A parent column that is only
   measured unique or a weak claim is accepted when its name equals the child's (ignoring case
   and separators), is within the name-similarity threshold, or is a generic key name; and a
   generic one is dropped when another column of the same parent is named like the child. A
   declared key is exempt. This removes `location.companyid` as the parent of a `locationid`.
- Rule 10: **Planner path choice (2026-10-13, DEC-4).** In a join path an unverified candidate between
   two sources costs `cross_source_candidate_weight` (default 4, planner config) against 2 for a
   same-source candidate and 1 for a declared or observed link, and when both ends are in one
   source only same-source paths are returned if any exist. A path whose every hop is a
   cross-source candidate with no value-overlap evidence carries
   `warnings: ["CROSS_SOURCE_CANDIDATES_UNVERIFIED"]`, repeated in the plan's warnings.
- Rule 9: **Source groups (2026-10-13, DEC-3).** `[builder]` accepts `source_groups` (lists of source
   IDs that hold one dataset) and `forbidden_source_pairs` (pairs never linked). Both default to
   none, which changes nothing. With groups set, two sources are paired only when they share a
   group; a source in no group is unrestricted.
- Rule 7: **Weak key claim (2026-10-13, DEC-2).** The ClickHouse collector reports a table's
   one-column plain sorting or primary key as an `index` constraint whose `native_id` ends in
   `:primary_key` or `:sorting_key` (column names only; expressions and multi-column keys are
   skipped). The builder admits that column as a parent with evidence kind `weak_key_claim` and
   the explanation "uniqueness unproven"; it is never a declared key, and with sampling on the
   overlap check still applies.

**Candidate order and the cut (P7-2, F15, DEC-32/DEC-33).** The value overlap is computed for every
name-filtered candidate before the cut at `top_k_per_field`. A measured zero (a complete parent with
no shared token) is dropped first; `dropped_zero_overlap_candidates` counts every such zero, also
one the cut would have skipped, and the kept set is what dropping first and then cutting gives. The
rest are ordered by: name score; a declared key; evidence tier (0 a measured containment, 1 a lower
bound or a zero against a partly read parent, 2 not measured); higher contained fraction; same
source; weak key claim before measured uniqueness; then the field ID. A weak, capped key with too
few values therefore no longer takes the slot of a measured-unique parent that contains the child.

**Evidence tiers, owner decisions and a conclusive zero (Phase 7, P7-5, F11-b; owner decisions DEC-23, DEC-34..38).** Every `REFERENCE_CANDIDATE` edge carries the optional, additive field `evidence_tier` (`DECLARES_FK` edges carry `verified`, other kinds none, and an older snapshot has none):

- `verified`: the contained fraction of the child's compared distinct values is at least `[builder] c_min` (default 0.25, `0 < c_min <= 1`; the point estimate, not the interval), on a complete parent or already as a lower bound on a partly read one.
- `plausible`: both ends are in one source and the above does not hold (no tokens, a sample too small, a lower share than `c_min`, a zero against a partly read parent).
- `unverified_cross_source`: the same, with the ends in two sources. A composite-key candidate has no tuple measurement and is `plausible` or `unverified_cross_source`.
- The catalogue page lists every candidate with its tier. The requester and the planner leave `unverified_cross_source` out unless their flag is true (17.3, 17.4); a `rejected` edge is always left out.
- `[builder] decisions_file` names an optional JSON list of `{"child", "parent", "decision"}`, with `decision` `confirmed` or `rejected` and a column named `<source_id>.<namespace>.<table>.<column>` (namespace omitted when the object has none, DEC-36). `confirmed` sets the tier `verified`; `rejected` sets the edge status `rejected` (the edge stays on the page). The file is read and validated with the config (bad JSON, a non-list, an unknown decision or a missing field is refused without echoing names). The file is read with a byte order mark accepted and at most `decisions_file_max_bytes` (default 1,048,576) bytes; a larger file is refused. A qualified name shared by two proposed candidates (table `a.b` against table `b` in namespace `a`) is ambiguous and applied to neither. An entry that matches no proposed candidate (swapped, unknown, ambiguous, or cut earlier) is a warning logged as a count, and the builder run carries `unmatched_decisions` when non-zero; names are never logged.

**Conclusive zero by expected overlap (option B).** A zero against a parent that was only partly read used to stay a candidate. If the link held at the weakest containment the catalogue promises to find (`c_min`), the child values compared would share `E = n_child x f_parent x c_min` values with the parent's tokens: `n_child` is the child's distinct values compared (inside the common hash range when a side is capped), `f_parent = min(1, parent distinct values read / the parent profile's distinct_estimate)`. When `E >= [builder] min_expected_shared` (default 5, so P(0 | link) is about e^-5 < 1%) and none is shared, the zero is a measurement: the candidate drops and `dropped_expected_overlap_candidates` counts it (non-zero only). Without a distinct estimate (ClickHouse with the probe off, DEC-35) `f_parent` is unknown, B does not apply, and the tier alone separates the candidate. A zero against a complete parent still drops under `dropped_zero_overlap_candidates`. **Only a trusted parent read (DEC-39, owner 2026-10-25).** The formula assumes the parent's values read are a random subset, so B drops only when the parent token set's `sample_method` starts with one of `dbig_builder.config.B_TRUSTED_SAMPLE_METHODS` (random or hash-coordinated reads); never `first_rows_limit:*` or `first_documents_limit:*`, where the child's values may sit in the unread rows. No collector emits a trusted sample yet (option D, coordinated hash sampling, will), so B is dormant on today's collections and the tier alone separates the candidates.

**Allowed source pairs (option E, DEC-34).** There is no `allowed_source_pairs` key. The DEC-3 keys already say which pairs may link: with `source_groups = [["a", "b"], ["c"]]` only `a`-`b` can be paired (a source in no group is unrestricted), and `forbidden_source_pairs = [["a", "c"]]` blocks one pair in both directions. Candidates inside one source are never affected.

**Value-free format profiles (Phase 7, P7-5 option C, F11-b; owner decisions DEC-40, DEC-42, DEC-43).** A candidate whose two columns cannot hold the same values is dropped without reading a value into the artifact. The collector side is off by default: source flag `format_profiles` (also `DBIG_FORMAT_PROFILES`), and a scan request needs the `sample` capability for it, as for the cardinality probe.

- *Collection (7).* For every text, integer and UUID column (ClickHouse `Nullable` and `LowCardinality` wrappers looked through; not boolean, timestamp, numeric, JSON, float, array or map) PostgreSQL and ClickHouse run **one read-only aggregate statement over the whole column** (DEC-43; no `sample_rows` bound, no `TABLESAMPLE` or `SAMPLE`), quoted as the other collector reads are, charged to `max_queries`, under `statement_timeout` or `max_execution_time` (`read_timeout_seconds`). The statements run after every catalogue read and stop at the budget limit instead of raising (`BudgetExhausted` would discard the whole bundle): the profiles left unread are counted in one `FORMAT_PROFILES_SKIPPED` diagnostic, and the catalogue is written. A column the server refuses or times out, or whose schema, table or column name is not a plain identifier, leaves the code-only diagnostic `STATS_UNAVAILABLE` ("format profile unavailable") and the scan goes on. MongoDB computes the same profile from the documents it already samples: no extra `find`, no budget charge, nothing when `sample_rows` is 0.
- *The profile* is a `Profile` (`method` `sample`, `sample_count` the non-null values) with `length_min`, `length_max`, `length_avg`, the shares of values that are all digits, all hexadecimal, a UUID or all letters (`digits_share`, `hex_share`, `uuid_share`, `alpha_share`) and `formats`, a map from a shape token to its share. A token turns a decimal-digit run into `9{n}`, a letter run into `a{n}` (any script, by Unicode category on MongoDB and ClickHouse; the engine's letter class on PostgreSQL), keeps only the ASCII punctuation in a fixed whitelist (`!#$&()+,-./:<=>@[]^_` and the backtick, `|`, `~`) and turns every other character (space, control, brace, quote, symbol, emoji, mark) into one `*`, so no character of a value outside that list survives. It is made from the first `format_token_max_length` (default 64) characters, so `QK-4821` is `a{2}-9{4}`. `formats` holds the `format_profile_max_formats` (default 8) most common shapes, so a shape missing from it is `not among the common ones`, not `never seen`; `formats_truncated` says the list was cut, and `values_complete` is false when the numbers come from a MongoDB sample that did not reach the end of the collection. Array types (`varchar(n)[]`) are not profiled. An empty or all-null column has no profile; ClickHouse returns 0 for `min`, `max` and `length` over an empty non-nullable column, but the statement groups by shape and so returns no row for it, and a column is never read as a [0, 0] range.
- *Numeric ranges (local only).* For integer columns the smallest and largest value go into the collector-bundle file **`ranges.jsonl`** (model `ValueRanges`: `field_id`, `object_id`, exact integers `minimum` and `maximum`, and `complete`, true when the bounds cover the whole column). It is local-only like `tokens.jsonl`: ignored in `.gitignore` wherever `tokens.jsonl` is, never in a snapshot (`SNAPSHOT_FILES`), a log, a trace or a model context. A bundle written before the file existed has no `ranges.jsonl` in its manifest and is still read, with no ranges. MongoDB ranges come from the sampled documents, so `complete` is true only when the read reached the end of the collection; otherwise they may mark an overlap and never drop.
- *Builder (DEC-42).* `[builder] format_reject_share` (default 0.95, `0 < x <= 1`; `>=` compared, so 1.0 drops only a child whose values all share one shape). Before the `top_k_per_field` cut, a `REFERENCE_CANDIDATE` is dropped when (a) the child's most common format covers at least that share of its values and the token is not among the parent's `formats`; or (b) both columns are text and their `[length_min, length_max]` ranges do not overlap (touching at one length overlaps); or (c) both have a complete `ranges.jsonl` row and the integer ranges do not overlap (touching overlaps). A missing profile, a profile with no `formats`, a half-known range, a parent `formats` list that was cut (`formats_truncated`, or, when it is not said, an unlisted mass at least as large as the lightest listed shape) or a profile with `values_complete` false (rules a and b) makes that rule not apply. A declared foreign key is never dropped, and neither is a candidate with a measured shared value (a shared value was observed; a list of common shapes is a summary). A dropped candidate takes no slot in the cut and is counted under the builder run's `dropped_format_mismatch_candidates` (non-zero only; a count, never a value). A kept candidate whose two columns have a range row carries the boolean `numeric_ranges_overlap` on its edge; the bounds themselves never leave the bundle.
- *Limits.* A shape is only as good as its sample on MongoDB, and the SQL statements read the whole column: the cost on a very large table is a full scan and a regular-expression pass per value, which `read_timeout_seconds` bounds. The shape grammar sees ASCII digits and the engine's letter class, so non-ASCII letters may differ between engines. Live cost and the effect on the 0-PG<->CH-candidates acceptance are checked with P7-6, not by the fixture tests.

**Type families (corrected 2026-10-04).** A pair is compared only within one type family. The
families used to be the type name with its parameters cut off, so ClickHouse `String`,
PostgreSQL `text` and MongoDB `str` were three families and `Nullable(Int32)` was the family
`nullable`: no text key and no nullable ClickHouse key could become a cross-engine candidate.
`Nullable(...)` and `LowCardinality(...)` are now unwrapped, and the integer, text and UUID
spellings of the three engines are each one family (`dbig_builder.builder.INTEGER_TYPES`,
`TEXT_TYPES`, `UUID_TYPES`). Found by `tests/test_live_shapes.py`, where
`region_code LowCardinality(String)` in ClickHouse now meets `regions.code text`.

## 9. Query history and lineage

Inputs are observed SQL text/normalized fingerprints with provenance and times, view definitions, job/run records, and OpenLineage events. A view definition is *defined* transformation lineage, distinguished from an executed job. SQLGlot parses PostgreSQL/ClickHouse dialects; our resolver qualifies tables/columns against a snapshot, handles aliases/CTEs/stars where resolvable, and records unresolved expressions. Capture `INSERT ... SELECT`, `CREATE TABLE AS`, materialized view definitions, configured transformation jobs, and reads/writes. Column lineage is emitted only when expressions map to known fields; otherwise table-level edge plus diagnostic. SQL text stored only under policy after literal/comment redaction; default artifact stores fingerprint, parser version and redacted snippet.

The planner now maps explicit OpenLineage `job.facets.lineage` entries before event input/output arrays. For output datasets, a `facets.lineage` facet takes precedence over `facets.columnLineage`; mapped field inputs create separate field lineage edges and can be queried through the lineage paths API with field IDs. RunEvent `COMPLETE` paths are observed; JobEvent paths are defined. Unmapped fields, unsupported job-typed entries and outputs without explicit lineage produce diagnostics instead of fabricated paths. Facet transformation descriptions are discarded from the derived graph. The observed SQL resolver expands read-only CTE/subquery sources and resolves schema-qualified table names; it abstains on nested writes, ambiguous names, three-part names and ClickHouse materialized views with a `TO` target until their target/output semantics are modeled. SQL-derived column lineage remains open.

OpenLineage import maps dataset namespace/name to source/object IDs and run/job facets to observed edges. Preserve RunEvent versus JobEvent provenance. Use an explicit target/source mapping from a lineage facet or resolved transformation SQL before asserting a particular input→output edge; a run listing several inputs and outputs alone does not justify their Cartesian product. Import column lineage only when the facet and field mapping are both available. Unmatched datasets remain unresolved observations. Distinguish lineage from observed joins: a SELECT join proves a query used two datasets, not that one was derived from the other. No LLM may invent lineage.

## 10. Local LLM review in version 1

After deterministic scoring, a bounded LangGraph `StateGraph` runs `redact -> request -> validate -> record` for ambiguous candidate batches within configured token, request and time budgets. The trial model is Qwen3 `8B`; final model, quantization and digest depend on GPU VRAM and measured latency/accuracy. vLLM runs on a Linux or WSL2 GPU host; the assembler calls its private OpenAI-compatible endpoint through the `openai` Python client. This API compatibility does not authorize an OpenAI cloud call. Prompt includes normalized object/field labels, types, structural metrics, sanitized descriptions, source context and evidence IDs. It excludes raw rows, token sets, SQL literals and connection details. Require schema-constrained output: candidate ID, decision (`supports`, `opposes`, `unknown`), short reason, cited evidence IDs, and any suggested follow-up. Validate again with Pydantic and check all IDs; reject malformed, uncited or invented evidence. Persist model/digest, prompt/schema versions and response hash for audit; redact response text as necessary. Verify vLLM's pinned structured-output behavior against the schema before deployment. As implemented until 2026-10-04 the prompt carried only the two object names and the evidence; it now also carries each candidate's column pairs (left referencing, right referenced) with names and types, which this paragraph always required. Sanitized descriptions and source context are still not sent to the reviewer.

The reviewer adds only `MODEL_SUGGESTION` evidence and can reprioritize review; it cannot convert a candidate into a declared FK or an observed lineage edge. A timeout, local server outage or invalid output produces a typed diagnostic and the deterministic graph still publishes. The v1 deployment enables review for ambiguous cases, then compares precision/recall and latency with the no-LLM baseline. LangGraph is a fixed workflow, not a free-running agent: no model tool calls, autonomous query planning, peer-agent handoffs or cloud API. Top-level LangChain and LangGraph Swarm add no needed v1 capability; reconsider only if a measured workflow requires their features.

Self-hosted Langfuse receives spans for run/candidate IDs, workflow node names, model artifact identity, prompt/schema versions, token counts, timing, validation status and redacted error codes. Start with manual SDK spans so model inputs/outputs and LangGraph state are not captured automatically. A LangGraph callback/agent visualization is allowed only after tests prove it transmits approved metadata alone. Langfuse requires its own web/worker, PostgreSQL, ClickHouse, Redis/Valkey and S3-compatible storage; these are telemetry infrastructure, not data sources or assembler dependencies. Restrict network access and retention. Langfuse unavailability must not block graph publication; bound any telemetry queue and emit local diagnostics. Test that canary secrets, sample values and SQL literals never appear in traces.

## 11. Assembler, state and incremental runs

Assembler validates all manifests before reading records, builds a normalized in-memory index and uses SQLite for durable run receipts and IDs. Process bundles idempotently by manifest digest. Reconcile object lifecycle across complete source scans only; a missing/failed source does not imply deletions. Edge first/last seen and evidence history survive reruns. Cache matching on object profile hashes, normalization version, algorithm version, and relevant config hash. Invalidate on source key changes, schema changes, consent changes or tokenization key rotation. Deterministic sorting means same inputs/config/tool versions produce byte-identical graph data except declared run metadata.

The initial SQLite receipt implementation stores one verified active bundle path and digest per source and a receipt for each accepted signed manifest. A partial rescan preserves the last complete active bundle for that source, records the partial receipt, and publishes a snapshot marked partial with the affected source/digest in builder run metadata. The next complete scan replaces the active bundle. Replayed receipts are idempotent. Bundle files must remain available for rebuilds; source-level receipt state currently rebuilds all graph data and does not yet cache field-level matching, historical edge first/last-seen timestamps, or profile-based invalidation.

Partial runs identify which sources and capabilities were unavailable. A graph can be published with partial status if its manifest says so; downstream can enforce full-only. No permanent central raw sample store.

## 12. Command and error contract

<!-- verified: 6e43d33 -->

Commands as implemented (`packages/*/src/*/cli.py`). Option names come from the Click declarations on this commit.

```text
dbig collect --config <source.toml> --out <bundle-dir>
dbig validate-bundle <bundle-dir> --key-env <ENV>
dbig accept-scan --config <source.toml> --request <request.json> --queue-db <db>
dbig process-scans --config <source.toml> --queue-db <db> --out-root <dir>
dbig serve --config <source.toml> --queue-db <db> --host <loopback-ip> --port <port>
dbig-builder assemble --config <builder.toml> --bundle <dir> [--bundle <dir> ...] --out <snapshot-dir>
dbig-builder assemble-incremental --config <builder.toml> --state <sqlite> [--bundle <dir> ...] --out <snapshot-dir>
dbig-builder validate-graph <snapshot-dir>
dbig-builder explain-edge <edge-id> --graph <snapshot-dir>
dbig-builder render --snapshot <dir> [--bundle <dir> ...] [--config <builder.toml>] --out <catalog.html>
dbig-requester index --config <requester.toml>
dbig-requester search --config <requester.toml> [--query <text>] [--match <all|any> --source-id --kind --data-type --tag --capability --evidence-status --max-age-seconds --limit --offset]
dbig-requester ask --config <requester.toml> --question <text>
dbig-requester request-scan --config <requester.toml> --request <request.json> --dispatch <bundle|http>
dbig-requester serve --config <requester.toml> --host <loopback-ip> --port <port> [--no-access-log]
dbig-planner build --snapshot <dir> --observations <dir> --out <graph-dir> [--mapping <file>]
dbig-planner lineage-paths --graph <dir> --from <id> --to <id>
dbig-planner join-paths --graph <dir> --from <id> --to <id> [--include-unverified-cross-source]
dbig-planner plan --graph <dir> --path-id <id> [--max-rows <1-100000>]
dbig-planner serve --config <planner.toml> --host <loopback-ip> --port <port>
```

Exit codes, measured on this commit: 0 on success; 1 for an `OperatorError` (shown as a one-line command error) or an unhandled exception (traceback); 2 for a Click usage error such as a missing file option. The finer scheme once planned here (codes 3 to 6, `--allow-partial`, a JSON diagnostic envelope) is not implemented. A bundle or snapshot reports `partial` in its manifest instead of through an exit code. A command never prints a secret or a raw value.

Snapshot node rows validate strictly. `nodes.jsonl` carries exactly three record types, so it validates against the discriminated union `dbig_contracts.models.SnapshotNode` (`Source | Object | Field`, discriminated on `record_type`) rather than the permissive base `Record`, and `schemas/ir-v1/node.schema.json` publishes that contract as a `oneOf` with a `record_type` discriminator. `dbig_contracts.artifact.validate_row` is the single gate both the write and read paths use, so an unknown `record_type` or a row missing its own type's required fields is refused on publication and on read. `runs.jsonl` deliberately keeps no strict model and stays a base `Record`. The earlier `invalid snapshot node type` check in the referential pass was removed because the union makes it unreachable.

### 12a. Operator errors at the command surface (settled 2026-09-27)

`dbig_contracts.errors.OperatorError` is the one exception type each package's Click group
converts into a clean command error. It means a failure the person running the command can fix
from the message alone: a missing secret reference, an unknown identifier, state that has not
been built yet. `SecretReferenceError` subclasses it, so the single conversion added earlier for
secrets now covers the whole class. Catching a bare `ValueError` stays rejected, because it
would dress a Pydantic validation failure or a genuine bug as something an operator can fix; a
test pins that `validate-graph`'s ordinary `signature key unavailable` failure keeps its full
traceback, which is what keeps the distinction honest.

Two defects were found by adding command coverage rather than by review. `join-paths` and
`lineage-paths` raised `ValueError("UNKNOWN_OBJECT")`, which reached the operator as a traceback
naming neither the identifier nor the remedy; and `search` before `index` raised the `sqlite3`
driver message `unable to open database file`. Both now name the cause and the next step.

The HTTP surface follows the private-network decision: the collector, planner and requester
`serve` commands each refuse a non-loopback bind address and refuse a hostname outright rather
than resolving it, since a name could resolve off-host. That behaviour already existed and had
no test; it now has coverage for each command across several non-loopback addresses.

Extended on 2026-10-04, after an independent run of the runbook still ended in tracebacks:

- **Configuration files** are written by the operator, so a TOML syntax error, a wrong table or a
  setting that fails validation is an `OperatorError` for all four commands
  (`dbig_contracts.errors.load_toml_section` and `validate_configuration`). The message names the
  setting and the rule, never the value, since a value can be a host or a path. A malformed
  `DBIG_<SETTING>` override is reported the same way. Records inside artifacts are unchanged:
  their validation failures keep the traceback, as above.
- **Observation files and the dataset mapping** given to `dbig-planner build` are also operator
  input. A record that is not JSON or fails validation is reported as `file:line`, never with its
  content, which may be SQL text.
- **Collector connections**: a refused login, an unknown PostgreSQL database and an unreachable
  server become one line naming the `connection_env` variable to check. Recognised from each
  driver's own types and codes as observed on the pinned servers (pg8000 SQLSTATE `28P01`, `28000`,
  `3D000` and wrapped socket errors; ClickHouse code 516 in the text of ClickHouse Connect; PyMongo
  `OperationFailure` code 18 and `ServerSelectionTimeoutError`). Driver text is never repeated,
  because it can carry host and user names. Anything else keeps its traceback.
- **A namespace that is not there stops the run with no bundle.** Before, a mistyped namespace
  produced an empty bundle marked `complete`. A namespace that listed nothing is checked against
  `pg_namespace` or `system.databases` (one extra query, only in that case), so an existing but
  empty schema or database still collects. MongoDB has no empty database, so a database with no
  collections is reported without a further query. The message says "does not exist or is not
  visible to this account", because the catalogs cannot tell those apart for every engine.

## 13. Security and operations

Secret references resolve through one function, `dbig_contracts.security.secret_from_env`. Components never read a credential directly: validated configuration holds the *name* of an environment variable, and this resolver refuses a missing or blank reference, an unset variable, and a variable set to whitespace, so a wrong reference can never degrade into an unsigned bundle, an unverified scan request, or an unauthenticated API. The raised `ValueError` names the variable and the purpose so an operator can fix it, and never contains the secret's value; the resolved value is returned byte-for-byte because signing keys are byte-sensitive. Every secret call site uses it: builder config and reviewer, collector runtime, scan intake and `validate-bundle`, requester answer, scan dispatch and API, and planner API. The resolver raises `SecretReferenceError`, which subclasses `ValueError` so existing callers keep working. Each package's Click group is a `SecretAwareGroup` that converts `OperatorError`, the parent type, into a command error (§12a), so any command in any of the four CLIs reports a named message rather than a traceback when a secret reference is missing, blank, unset or empty. Only that type is converted: an ordinary `ValueError`, such as `validate-graph`'s `signature key unavailable`, keeps its full traceback, so validation and programming errors are not hidden behind a tidy message. The converter is duplicated once per package rather than shared, because §17 forbids the packages importing each other and `dbig_contracts` must not depend on Click.

Use read-only accounts, TLS verification, per-query timeouts, connection pooling limits, process separation by source, and explicit allowlists. Bundle receiver enforces size limits, path traversal rejection, digest verification and trusted transport; optional signatures become mandatory if transfer is not otherwise authenticated. Require workload identity or equivalent secret delivery in production. Scrub logs and crash reports. Keep source-side raw sample in RAM only. Retention schedules apply to manifests and profiler tokens. Provide key rotation and rerun semantics. Run each collector on a schedule agreed with source owners; source load metrics and budgets are visible in diagnostics.

[THREAT_MODEL.md](docs/THREAT_MODEL.md) records the trust boundaries, the eleven modelled threats and their controls, and the known gaps. Each mitigation there names the test that holds it, a mitigation without a test is listed as a gap rather than as a control, and `test_the_threat_model_cites_only_tests_that_exist` fails if a cited test is renamed or removed. Threat tests: malicious bundle path/JSON, oversized record, duplicate ID, HMAC mismatch, prompt injection in comments/SQL, model hallucinated IDs, timeout, collector privilege failure, and stale manifest replay. The LLM prompt treats source metadata as data, never instructions.

## 14. Test and acceptance plan

<!-- verified: f30780c -->

Unit/contract: IR schema round-trips, versioning, stable IDs, atomic bundle write/read, digest tamper rejection, bounded budget accounting, normalization and scoring, SQL resolver known/unknown cases, LLM schema rejection and timeout, deterministic graph diff. Integration: disposable PostgreSQL, ClickHouse, MongoDB with representative keys, composite joins, arrays/nested paths, views, materialized views, and pipeline events; test least-privilege roles and source failures. Golden fixtures include true/false relationships, ambiguous names, sparse/low-cardinality fields and observed transformations. Measure candidate precision/recall at fixed review budgets; report coverage and abstention separately. Measure no-LLM and local-LLM accuracy and latency on the same held-out corpus. Acceptance thresholds require user approval after a labeled corpus exists; do not invent success percentages.

Performance: benchmark at the stated 10/200/1,000 scale with per-source load and bundle bytes, assembler wall time/RAM, LLM p50/p95, cache hit rate. Security: dependency vulnerability/license report, secrets scan, least-privilege integration checks, malformed bundle fuzz/property tests. CI cannot test live private databases; those run in a separate authorized environment.

The last recorded full run, 2026-11-09, was 1,282 passed, 80 skipped and 1 expected failure ([TODO.md](TODO.md)). Tests in `tests/test_live_*.py` run only with `DBIG_RUN_INTEGRATION=1` against real servers, which the `source-integration` CI job starts. Scale was measured on 2026-10-04 with `scripts/scale_benchmark.py` at 10 sources, 200 objects and 1,000 fields on the latest server releases: every command finished in under 3 s; assemble peaked at 110 MiB and each collector at most 44 MiB; in-process search is 87 ms median and 129 ms p95, while a CLI invocation adds about 0.8 s of interpreter start-up. Figures and caveats are in TODO.md (verification fixes 1-10, item 9). Model latency remains unmeasured until a GPU host exists.

Added in Phase 7 (P7-4, F17): **`MODEL_ANSWER_UNEXPLAINED`**, a model failure of `ask` for a bare verdict. The reply schema now starts with `checked` (a list of strings, every field required, still no `minItems`/`minLength` in the schema, B1). The app refuses an empty `checked`, a blank `answer`, or an `answer` that is only an outcome word (case, space, underscore and punctuation ignored). The requester then re-sends the same conversation, the bare reply as an assistant message and one fixed correction message that holds nothing from the question, the catalogue or the reply, up to `[requester.answer] answer_retries` times (default 1, `ge=0`; 0 makes one attempt). After the retries the code is returned with the F18 `reason`. A reply without `checked` is a schema failure (`MODEL_UNAVAILABLE_OR_INVALID`) and is not retried. The Langfuse span metadata gets the code and the attempt count only. An `answered` reply with no citation takes the same bounded retry with its own fixed text (`answer.py::CITATION_CORRECTION`, report item B2), and ends as `MODEL_INVALID_CITATION`, check `none`. `dbig_contracts.security.cap_model_client_logs` holds the `openai`, `httpx` and `httpcore` loggers at WARNING wherever a model client is used, because their DEBUG log carries the question and the reply (B1; owner's option H1 a).

### 14.1 Test guards

Cheap checks keep the tests honest; each runs in CI on every push and pull request. A rule that needs judgement (L2, L3, L5) has no check.

| Rule | Check | Where |
| --- | --- | --- |
| C5 | Ruff `PGH004` and `RUF100`; an added `noqa`, `type: ignore`, skip or xfail must carry `C5: reason=...; approved=...; review=...` on the same line | `pyproject.toml` `[tool.ruff.lint]`; `scripts/rule_check.py::check_exceptions` |
| L1 | A removed test function needs a `C5:` line in a commit message | `scripts/rule_check.py::check_removed_tests` |
| L4 | Ruff `PT` rules (`PT018` left out, see the comment in `pyproject.toml`); an added or edited test needs an assertion | `scripts/rule_check.py::check_assertions` |
| B6 | gitleaks over all history; the allowlist in `.github/gitleaks.toml` names two false-positive families by path and line pattern | `.github/workflows/rules.yml` |
| M1, M2 | Only files in `ROOT_FILES` sit in the root; no tracked file is git-ignored | `scripts/ci_check.py::ROOT_FILES` |
| M4 | `deptry` per package, plus the import check in `ci_check.py` | `ci.yml` job `application-quality` |
| CI files | `actionlint`, and `zizmor` with `.github/zizmor.yml` | `rules.yml`, `ci.yml` |

`scripts/install_check_tools.py` installs gitleaks and actionlint at pinned versions and verifies each SHA-256. `deptry`, `vulture` and `zizmor` are pinned dev dependencies. `actionlint` also runs `shellcheck` on `run:` scripts when `shellcheck` is on the path, so install it locally to see what CI sees. `vulture` (dead code) is for audits only and never blocks, because it reports false positives. `tests/test_rule_check.py` runs the C5, L1 and L4 checks against a throwaway git repository. `pytest` runs strict: `xfail_strict`, `--strict-markers`, `--strict-config`.

## 15. Git, graph tooling, Ponytail and CI/CD contract

<!-- verified: bce5fee -->

The repository hook path is `.githooks`, installed with `python scripts/install_hooks.py`. Pre-commit runs `scripts/graph_sync.py --pre-commit`, which refuses a commit with staged files that also have unstaged edits, then refreshes Graphify (`graphify update .`), Graft (`graft build .` and `graft check .`) and Codebase Memory (fast index) and fails the commit on any failure. Post-commit and session start run the same refresh in warn-only mode (`--post-commit`, `--session-start`). All steps are structural and make no LLM call. Graph caches are git-ignored local caches. `--no-verify` and a changed `core.hooksPath` bypass local hooks, so CI repeats the refresh in the `graph-index` job.

The three developer graph tools are pinned in `pyproject.toml` `[tool.dbig.graph-tools]`, the single source of truth: Graphify 0.9.104 (`graphifyy` on PyPI), Graft 0.29.0 (`@nanonets/graft` on npm) and Codebase Memory MCP 0.12.1 (PyPI). By user decision on 2026-11-07 ("we should always run on latest version"), a newer release is installed and every pin is bumped together; the pin sites are listed in AGENTS.md section 4. `scripts/graph_sync.py` reads each tool's `--version` and fails closed on a mismatch, naming the install command. `scripts/ci_check.py` refuses a workflow that installs another version. `scripts/ci_check.py::_check_import_dependencies` also fails when a package imports a distribution it does not declare, or declares one its source never imports (M4); under `uv run` it maps import names to distributions through installed metadata, and outside it matches the distribution with the same name. Tests stub the version reporter, because the `application-quality` job does not install the tools.

Tool version pinning is part of the gate, not an environment detail. `pyproject.toml` declares `[tool.uv] required-version = ">=0.10.0"`, and every `astral-sh/setup-uv` step pins one exact uv version (`0.12.19`) instead of defaulting to latest. The floor is empirical rather than from release notes: uv's changelog does not record the introduction of the preview `cyclonedx1.5` export format, so installed binaries were tested directly on 2026-09-27. `uv export --help` first offers `cyclonedx1.5` at uv 0.10.0 and omits it at 0.9.0 and 0.8.17, and `scripts/supply_chain.py` reproduces the same `113 components; unresolved license declarations: 0` result on uv 0.10.0, 0.11.0 and 0.12.19. Because uv validates command arguments before reading `required-version`, an older uv reports an unknown-format error rather than a version error, so `scripts/supply_chain.py` reports uv's own message together with the running and required versions. `scripts/ci_check.py` rejects an unpinned or floating `setup-uv` step, a pin below the declared floor, and pins that disagree across workflows, so a supply chain gate can never silently depend on whichever uv is latest.

`.github/workflows/ci.yml` runs on pull requests, pushes to `main` and manual dispatch. Its jobs: `repository-policy` (`scripts/ci_check.py`), `graph-index`, `application-quality` (locked sync, Ruff, mypy, pytest, pip-audit), `package-isolation` (Ubuntu and Windows, clean wheels), `source-integration` (pinned server versions) and a second leg with the latest server versions, then `ci-success`, which requires all of them. The separate `docs.yml` runs markdownlint, Vale, offline link check and Mermaid rendering; `rules.yml` runs `scripts/rule_check.py`; `release.yml` runs on a `v*` tag. `.gitlab-ci.yml` mirrors them for the GitLab origin, except Windows package isolation, `ci-success` and release. The live-source job pins PostgreSQL 16.15, ClickHouse 25.8.33.6 and MongoDB 7.0.41; its latest leg uses PostgreSQL 18.6, ClickHouse 26.9.10.4 and MongoDB 7.0.45, an approved exception documented next to the image line in `ci.yml`. GitHub denies branch protection for this private repository on the account plan, so CI results are not a mandatory merge gate; the owner relies on local hooks and inspected runs.

Ponytail is a coding-agent methodology, not a runtime dependency. On every meaningful change, apply `/ponytail [lite | full | ultra | off]` with `full` as intended baseline when the plugin is installed; run review for current diff, audit for repository-wide design, debt to keep TODO debt visible, gain only with measurements, and help when command behavior is uncertain. In Codex, upstream documents these as `@ponytail-*` skills after plugin installation/restart. If unavailable, explicitly report that status and manually apply the same minimal-design checks; never pretend a skill ran. Agent instructions in AGENTS.md and CLAUDE.md are the controlling project policy.

### 15.1 Developer setup

1. Install Python 3.12 (`.python-version`), Git and uv 0.10.0 or later (`pyproject.toml` `[tool.uv] required-version`). Install the three graph tools at the pinned versions with `python -m pip install graphifyy==0.9.104 codebase-memory-mcp==0.12.1` and `npm install --global @nanonets/graft@0.29.0`.
2. Clone, then run `python scripts/install_hooks.py`. Git does not carry hook configuration through a clone.
3. Copy `.env.example` to `.env` and fill local values. `.env` is ignored by Git and nothing loads it automatically.
4. Run `python scripts/graph_sync.py --check-tools`, then the checks in AGENTS.md section 5.
5. `python scripts/supply_chain.py` writes the CycloneDX SBOM and license reports under the ignored `.data/supply-chain/`. `supply-chain/licenses.json` records every pinned release in `uv.lock`; `--refresh` updates it when the lock changes, and CI rejects a missing or stale entry. A declaration reported by an upstream package is inventory, not a legal conclusion.

## 16. Decisions requiring user input or empirical evidence

1. Resolved: generated developer graphs are local/CI caches and are never committed.
2. Resolved: make the first commit and record the remote (the origin is now the GitLab repository; `github` is the GitHub mirror, see AGENTS.md section 4).
3. **Open:** exact GPU model/VRAM and RAM; model size and latency remain provisional.
4. Resolved 2026-09-27: source read budgets and value classification. Metadata-only stays the default; sampling, query history and cross-source token comparison are opt-in per source under mandatory row, byte and time budgets with a sensitive-field denylist.
5. Resolved 2026-09-27: bundle transfer and tokenization key. A per-run HMAC key reaches authorized collectors by environment reference, the manifest records only its `key_id`, bundles move as signed files on a filesystem or object store, and tokens exist only in assembler memory inside one run boundary.
6. Resolved 2026-09-27: the supported version matrix is exactly the server versions the CI integration fixtures run. Untested versions are unsupported rather than assumed working.
7. **Open:** source of observed SQL/job metadata and its privacy policy. Until it is chosen, executed lineage stays unavailable and no edge may claim it.
8. Resolved 2026-09-27 as a method, not as numbers: a labeled synthetic corpus plus an evaluation command reports precision-recall and calibration. Thresholds stay unset and the engine keeps abstaining with `confidence = null` until the user approves measured values.
9. Resolved 2026-09-27: consumer scope. The independent requester and planner are the only machine consumers of a published snapshot, and their CLIs plus optional HTTP services are the product surface. No third-party catalog mapping is owed in v1, and `openlineage.jsonl` stays an optional export.
10. Resolved 2026-09-27: object-rename semantics. Use a source-native stable identifier where one exists, emit a rename hint where none does, and treat an unverified rename as delete plus add.
11. Resolved 2026-09-27: HTTP exposure. The adapters target a trusted private network with a loopback bind default and a bearer token from a secret reference; public exposure is documented as unsupported rather than partially hardened.
12. Resolved 2026-11-10 by the owner (relayed by the lead session; improvement report Part 3 and section H). D1 "TLS, private CA (Recommended)": services on the LAN serve Uvicorn TLS with IP-based certificates from a private CA. D2 "TLS on each DB (Recommended)": the collector container reaches each database over verified TLS, never an SSH tunnel or plain TCP. D6 "Build here, copy tar (Recommended)": images are built on a machine with internet and moved with `docker save`, so the LAN boundary has no build exception. D8 "Code writes it (Recommended)": relationships in an answer are written by code from stored edges (report option O4), and the five paraphrase attack tests no longer assert a refusal. H4 "Map type words (Recommended)": type words in a question map to the `data_type` filter. H5 "List mode (Recommended)": a list question returns every field of the matched tables. Tests: "Allow that one (Recommended)" (the logging test exempts only `cap_model_client_logs`); "Retries off in those 3 (Recommended)" (`answer_retries=0` in the three no-citation tests, plus new retry tests); "Retire as stale (Recommended)" (the 27 `attack2_d3_lowercase` tests); "Accept both (Recommended)" (DEC-2: admit a weak key only when its uniqueness is unmeasured or at least `MEASURED_UNIQUE`; DEC-1: keep one deterministic best candidate per child column and parent table). Earlier lead rulings D3, D4, D5 and D7 (2026-11-09) stand: the caller runs each step, one secrets file per service, the contracts container runs, the collector's SQLite queue is the job engine.

These are tracked in TODO.md. Work that does not depend on them may proceed; behavior-changing assumptions must be surfaced to the user, including in the middle of a task.

On 2026-09-23 the user delegated these choices to implementation. The initial local profile is conservative: collectors require read-only credentials and verified TLS; sampling, history access, cross-source token comparison, and free-text export are disabled unless explicitly configured. Signed filesystem bundles are the first scheduler handoff; an HTTP adapter must authenticate its caller and use the same signed request envelope. Signing and token keys are separate environment references, and unsigned scan requests are rejected. Unknown event sources yield no observed lineage rather than invented edges. Model identity and acceptance thresholds remain measured outputs, not defaults; a GPU cannot be selected or declared supported without host evidence. The version and driver matrix will be declared only after integration tests against those versions. Production connection details and retention are deployment configuration, not repository constants.

The metadata-only profile publishes SHA-256 digests of PostgreSQL view definitions and ClickHouse key expressions instead of their raw text. MongoDB validators and indexes contribute field names/types without sampling; optional samples contribute only path/type unions, missing/null flags and bounded counts, never values. This preserves change detection while full definition parsing and lineage await an approved observed-SQL source and classification. The available local GPU reports GTX 1650 with 4 GiB VRAM; Docker Desktop's Linux daemon is unavailable and WSL enumeration returned access denied on 2026-09-23. No supported model size or live server version is inferred from this evidence.

The collector also publishes `row_count_estimate` and `row_count_method` object attributes when source metadata makes a count available. PostgreSQL reads [`pg_class.reltuples`](https://www.postgresql.org/docs/16/catalog-pg-class.html) for tables/materialized views and omits its unknown `-1` value. ClickHouse reads [`system.tables.total_rows`](https://clickhouse.com/docs/reference/system-tables/tables), which can be NULL; its present value is the server's quickly determined count at discovery time. MongoDB uses PyMongo's [metadata-based `estimated_document_count`](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/crud/query/count/) for collections only, within the existing query and server-time budgets. It omits the count if the query budget is exhausted and emits a value-free diagnostic if that optional query fails. These counts are snapshots or estimates, not evidence of key uniqueness or record-level relationships; no row values are read for them.

PostgreSQL partition membership uses [`pg_class.relispartition`](https://www.postgresql.org/docs/16/catalog-pg-class.html) and its direct parent from [`pg_inherits`](https://www.postgresql.org/docs/16/catalog-pg-inherits.html). The collector labels a visible partition with `is_partition` and sets `parent_id` only when the parent is also in the authorized scan result. Pending detachments and out-of-scope parents do not create a parent link. It does not export partition bound expressions or mistake ordinary inheritance for declarative partitioning.

Artifact publication validates each object parent against another object in the same source and rejects missing, cross-source or cyclic parent references before the manifest can be published.

On 2026-09-27 the user decided items 4, 5, 6, 8, 9, 10 and 11 above, and the conservative 2026-09-23 profile stands unchanged where they overlap. Three of those decisions widen no behaviour and only make an existing default binding: metadata-only collection, per-run keyed tokens and the private-network HTTP posture are what the code already does, and they are now a contract rather than a starting point. Four of them add scope. The supported version matrix may name only versions an integration fixture actually exercised, so a version the fixtures do not run is reported as unsupported instead of untested. Rename handling gains a source-native identity path: PostgreSQL `pg_class.oid` and a ClickHouse table UUID prove that a renamed object is the same object, MongoDB offers no such identifier, so a collection rename yields a hint from structural similarity and, while unverified, a delete plus an add rather than a silent merge. Calibration gains an instrument before it gains numbers: a labeled synthetic corpus of declared-FK positives, deliberately hard negatives, ambiguous names and sparse low-cardinality fields, plus an evaluation command reporting precision-recall and calibration, with no threshold and no published probability until the user approves measured values. The consumer decision bounds what compatibility means: because the requester and planner are the only machine readers, golden-bundle and compatibility tests exist for an internal reason, namely that an independently installed older consumer must behave predictably against a newer snapshot, and not to satisfy an external product's schema.

Two decisions remain open and are not implied by these. The GPU host and model identity stay unmeasured, so live vLLM and Langfuse behaviour was untested by user decision at that date. Later, a private OpenAI-compatible model served by llama.cpp ran the live questions (results in `artifacts/run-20261014/` and `artifacts/run-20261020-pgch/`); Langfuse has not run live. The source of observed SQL and job metadata is still unchosen, which is why executed lineage remains unavailable; defined lineage from view definitions is unaffected, and no edge asserts execution without that evidence.

The first live metadata compatibility matrix was established by CI run 35911699728: PostgreSQL 16.15 (catalog read through a role granted schema usage and table SELECT), ClickHouse 25.8.33.6 (`system.tables` and `system.columns`), and MongoDB 7.0.41 (`listCollections` and indexes). The three tests passed with synthetic schemas. The CI `source-integration` job now also runs `tests/test_live_tls.py` against a throwaway CA; production authentication is untested. Only these exact tested versions have empirical metadata compatibility evidence; this is not a claim that adjacent versions or secure production connections have passed.

On 2026-09-23 the user requested that private vLLM integration be configured in code now while live GPU/model tests wait for an accessible GPU host they will identify later. Synthetic reviewer, answer-grounding and metadata-only telemetry tests remain active. No model artifact, digest, latency, live privacy result, or Langfuse deployment is claimed until it is actually tested. This deferral does not remove the live v1 acceptance gate.

## 17. Independent requester and planner contract (approved 2026-09-23)

The user selected **both** separately installable packages/CLIs exchanging versioned files **and** separately deployed HTTP JSON REST services. This section supersedes earlier single-package, headless-only, and planner-in-assembler descriptions in the research record. The requester, scan adapters, and initial planner have synthetic-tested implementations. The architecture and TODO record the binding scope and remaining work.

### 17.1 Dependency and ownership rules

<!-- verified: bce5fee -->

| Distribution | Owns | Required application dependencies | Optional dependencies |
| --- | --- | --- | --- |
| `dbig-contracts` | IR v1, JSON Schema, manifests, validation, stable IDs, signatures | Pydantic v2, Python standard library | None |
| `dbig-collectors` | Site-specific discovery, profiles, signed bundles, scan intake | `dbig-contracts`, Click, Pydantic v2, standard library | `pg8000`, `clickhouse-connect` or `pymongo` by source extra; FastAPI and Uvicorn `api` extra |
| `dbig-catalog-builder` | Cross-source evidence, reference candidates, deterministic snapshot, bounded semantic review | `dbig-contracts`, Click, Pydantic v2, RapidFuzz, LangGraph, OpenAI-compatible Python client, Langfuse SDK, SQLite stdlib | NetworkX only if builder-side traversal proves necessary |
| `dbig-catalogue-requester` | Structured catalogue search, grounded local-LLM answers, scan dispatch | `dbig-contracts`, Click, Pydantic v2, SQLite/FTS5 stdlib, OpenAI-compatible Python client, Langfuse SDK | FastAPI and Uvicorn `api` extra |
| `dbig-lineage-planner` | Observed lineage import, lineage and join paths, SQL/Mongo/query-plan rendering | `dbig-contracts`, SQLGlot, NetworkX, Click, Pydantic v2 | FastAPI/Uvicorn `api` extra; `openlineage-python` only for typed event emission |

The names are intended distribution names, subject to package-index availability and final lock audit. Do not introduce imports from requester to planner or vice versa. Source credentials and drivers are confined to collectors. The planner makes no LLM call; path selection and rendering use validated IR and deterministic policies. The requester may call the same private vLLM server used by the builder, but the packages share no process state or direct Python dependency. No top-level LangChain, LangGraph Swarm, Amundsen, Elasticsearch, Neo4j, Marquez, OpenLineage SQL parser, or second SQL parser is required. Graphify/Graft/Codebase Memory remain developer-only graphs.

### 17.2 Versioned artifact handoff

The builder publishes a sealed **catalog snapshot** using the section 6 `nodes.jsonl`, `edges.jsonl`, `evidence.jsonl`, `runs.jsonl`, and `manifest.json` convention. The snapshot includes object/field definitions, declared constraints, `REFERENCE_CANDIDATE` edges and their evidence, plus approved observation references. Planner-local lineage and path indexes and requester-local SQLite FTS indexes are derived caches, never the interchange contract. Observed SQL/pipeline metadata and OpenLineage event files enter the planner through validated observation records; raw values and source credentials never enter either package. A planner can operate on a compatible catalog snapshot without a requester. A requester can operate on that snapshot without a planner. OpenLineage-only input can yield lineage paths after dataset mapping, but join paths require compatible field/reference evidence; missing evidence produces an explicit unavailable result.

Every artifact declares schema major/minor version, content type, producer, source run IDs, creation/as-of time, completeness, SHA-256 file digests, counts, and provenance. A reader rejects unknown major versions, invalid references, excessive size, unsafe paths, bad signatures/digests, or ambiguous source identities; a compatible minor version adds fields without changing meaning. Create canonical fixtures and clean-wheel consumer tests before promising compatibility. The research document's earlier `entities.jsonl`/`relationships.jsonl` wording is historical; the section 6 filenames above are the binding design.

### 17.3 Catalogue requester

Structured requests filter by object/field kind, source, name, type, tags/capabilities, freshness, and evidence status, with pagination and stable sort. At the initial scale, build a local SQLite FTS5 index from a validated snapshot, retaining only approved metadata and object IDs. Store a snapshot digest with the index and refuse stale or mismatched requests until reindexed. Natural-language questions use bounded retrieval followed by a private vLLM call against **only retrieved and redacted metadata**; the model returns cited catalog IDs and an answer, validated against those results. It cannot query sources, invent fields, issue scans, assert lineage, or run SQL. If the model is unavailable, structured search remains usable and the natural-language route returns a typed unavailability response. Manual metadata-only Langfuse traces cover this call under the same privacy rules as the builder.

**Retrieval rules (2026-10-13, DEC-7 and DEC-8; index version 7 since 2026-10-19, P6-7).** Each entry's searchable context also holds its source ID, engine kind and source name, so "ClickHouse tables" matches. `ask` reads the question's scope from the index: a word equal to a source ID, kind or name filters the search to that source (and need not itself match), and a plural `tables`/`collections`/`views` makes it a list question that retrieves objects only, which for a named source lists its objects. An object whose name is one of the question's words ranks before columns that merely mention it. `[requester] exclude_objects = { source_id = ["glob", ...] }` (default none) keeps matching objects, and their fields, out of the index; re-run `dbig-requester index` after changing it.

**Type and list questions (2026-11-10, report items B6 and B7; owner: "Map type words (Recommended)", "List mode (Recommended)").** A question that holds `type` and a type word (`timestamp`, `date`, `numeric`, `integer`, `text`, `boolean`, `uuid` and a few spellings, `search.py::TYPE_WORDS`) retrieves the fields whose type falls in that category (`search.py::type_categories`, the SQL standard's datetime and numeric groups over `dbig_contracts.types::type_family`), in the named sources; its other words do not filter. Such a question, and one holding `all`, `every` or `list`, retrieves up to `[requester.answer] list_limit` entries (default 100, the search route's maximum) instead of `retrieval_limit`. Table expansion (DEC-18) still applies, so a type question's context holds every field of each matching table; on the 2026-10-28 snapshot N-C4 retrieves 20 datetime fields and its context is 169 of 169 fields. Nullability (N-C7) is not a type category and still finds nothing.

**Relationships in the answer context (2026-10-13, DEC-5).** The index also stores each declared and candidate relationship (names, status, the builder's evidence sentence; no values). `ask` passes the relationships of the retrieved objects, declared first, at most `[requester.answer] relationship_limit` (default 20, 0 turns it off), and a relationship ID is a valid citation. Since 2026-10-19 (F11, P6-7) each relationship also carries `from_source`, `from_namespace`, `to_source` and `to_namespace` (identifiers only), and the system prompt says a relationship between objects of two different sources cannot be joined in one query.

**Table expansion (2026-10-19, F13, P6-9; owner decision DEC-18).** After retrieval, every field of each retrieved object, and of the object of each retrieved field, joins the answer context with the same keys, once each, and becomes citable. `retrieval_limit` and `relationship_limit` still rank and cut; the expansion has no per-table cap and no model-token limit (ponytail ceiling: a very wide table can exceed the model's context; upgrade path a cap behind config, or indexing type text, LEAD_REVIEW F13 option C, deferred).

**Bridge tables on the path (Phase 7, P7-3, F16; owner decision DEC-24).** After retrieval, `ask` runs a breadth-first search over the index `relationships` table between the retrieved tables (an edge is one hop, any status, either direction). A table that lies on a shortest path of at most `[requester.answer] path_max_hops` edges (default 4, `ge=1`, the planner's limit) and is not itself retrieved joins `items` with `role: "path_only"` and only its key columns (the columns the path's relationship lines name); the path's lines join `relationships` and become citable. At most `path_max_bridge_tables` such tables are added (default 4, `ge=0`, 0 turns the search off); a path is kept whole or not at all, in order of status rank then ID. A line keeps its stored status, so a path over a candidate edge is still `tentative`. Path lines are not cut by `relationship_limit`, but `relationship_limit = 0` also turns the search off. Edges the answer may not show (below) are not walked. The index version is 9 since P7-5. Ceiling: every shortest path is walked, so a dense hub of equal-length paths is slow; the upgrade is a visit cap behind config. `search.py::chained_paths` also writes each path between two retrieved tables that stays inside the context as one `paths` entry of table names and relationship IDs, because a model given the separate lines did not connect them (N-I10, report item B4; the chain is replayed on the real snapshot, the live answer is `not checked`).

**Unverified cross-source candidates (Phase 7, P7-5, F11-b).** The index body of a relationship carries `evidence_tier` (`INDEX_VERSION` 9). At answer time a candidate whose tier is `unverified_cross_source`, and any edge with status `rejected`, is left out of the relationship lines (in SQL, so a left-out line never takes a slot of `relationship_limit`) and out of the bridge-table path search (a path is whole or nothing, so one such edge removes the path). `[requester.answer] include_unverified_cross_source = true` (default false) brings the first kind back with its status still `tentative`; it never brings back a rejected edge. The index state is unchanged by the flag.

**Answer rules (2026-10-13, DEC-6).** The word `lineage` no longer refuses a question (the `LINEAGE_REQUIRES_PLANNER` code is gone) or an answer; an answer that claims one (`derived from`, `flows into`, `writes to`) is still rejected, because the requester holds no lineage data. The model's reply carries `outcome`, required in the schema since 2026-10-19 (F12, P6-8; a reply without it is `MODEL_UNAVAILABLE_OR_INVALID`, and the prompt says to put the verdict only there): `answered` must cite retrieved IDs; `not_established` may cite nothing and is returned as a normal reply with `code: NOT_ESTABLISHED_BY_CATALOGUE`, not an error. Any citation, in either outcome, must be a retrieved ID. The reply also carries `cited` (2026-10-13, DEC-9): each cited ID resolved app-side to its kind and name, with owning object, namespace and source for a column, or `from -> to (columns)` and status for a relationship; taken from the retrieved metadata, never from the model's text.

The scan-request contract names an authorized source ID, requested scope/capabilities, reason, requester identity, idempotency key, expiry, and reply location; it contains no source credentials. Two adapters use the same signed, versioned request model: (1) write a signed bundle into the existing scheduler's approved handoff, or (2) POST it over private authenticated HTTP to a collector-side adapter. The collector verifies signature, replay/expiry, caller authorization, allowed scope and local load budget, then queues a scan locally and returns a receipt/status. The requester never bypasses collector policy or directly connects to a database. Exact scheduler interface, HTTP authentication mechanism, key management and retention require deployment evidence and remain open TODO items.

REST v1 routes: `GET /v1/catalogue/search`, `POST /v1/catalogue/search` (same search, query in the JSON body so it is not in the URL or an access log; `dbig-requester serve --no-access-log` turns the request log off), `POST /v1/catalogue/questions`, `POST /v1/scan-requests`, and `GET /v1/scan-requests/{id}`. CLI and REST call the same application functions. Responses include contract version, snapshot digest, IDs, evidence/freshness, completeness, and typed errors. API authentication, authorization, payload limits, rate limits and TLS are required before deployment; do not invent their concrete configuration.

Both requester and planner HTTP services require a nonempty API bearer secret from their configured environment reference before startup. Their request body ceiling is the validated `max_request_bytes` config field (default 65,536), enforced while streaming; per-minute request limits remain separately configurable. Production TLS termination and client identity policy are deployment obligations.

### 17.4 Path planner and lineage

Maintain separate edge sets for observed/defined `DERIVES_FROM`/`READS`/`WRITES` lineage and declared or inferred reference candidates. `JOINS_OBSERVED` records that a query used a join, not data movement. A lineage-path request traverses only lineage edges with execution/definition provenance and timestamps. A join-path request traverses approved declared/reference-candidate edges, marks tentative links, applies configured hop/result limits and returns ranked paths with every supporting and contrary evidence ID. Missing mappings, stale snapshots and partial observations are visible; never silently complete a path with an LLM guess. OpenLineage RunEvents, JobEvents and facets are mapped conservatively as described in section 9.

Planner observation intake applies `max_observation_bytes` to the aggregate bytes across all JSONL files, bounds both record and file counts by `max_observations`, and rejects linked input directories/files. It reads no more than the remaining byte budget from each file before parsing.

For a selected path, the planner constructs a typed query plan from catalog IDs and approved predicates. For a SQL-capable single-engine segment, render dialect-specific SQL through SQLGlot ASTs with quoted identifiers and bound **values**; validate the resulting AST against the selected objects, joins and read-only policy. Identifiers are allowlisted from the snapshot, never concatenated from user text. For MongoDB segments, emit a JSON aggregation pipeline with validated collection/path references and bounded stages. No generated query is executed by this product. For cross-database paths, return executable **source-side** read queries where possible plus ordered logical extract/transfer/join steps, schemas, expected keys and prerequisites. Until an approved staging/federated target is configured, mark staging and final join execution `unresolved`; do not claim the complete cross-source plan is executable. If a target is later configured, add its dialect/capability checks and explicit approval before rendering target SQL.

REST v1 routes: `POST /v1/lineage/paths`, `POST /v1/join-paths`, and `POST /v1/plans`. CLI and REST use the same validated functions. A plan response carries plan/version ID, snapshot digest, path/evidence IDs, source dialects, rendered SQL with parameter metadata and/or Mongo pipeline JSON, ordered logical steps, unresolved prerequisites, and warnings. Do not include source credentials or raw sample values. The API is read-only with respect to all source databases.

**Unverified cross-source candidates (P7-5, F11-b).** A join-path request leaves out a `REFERENCE_CANDIDATE` whose `evidence_tier` is `unverified_cross_source`, and every `rejected` edge, unless `[planner] include_unverified_cross_source = true` (default false; a rejected edge is never restored). With the flag on, the cross-source candidate weight and the `CROSS_SOURCE_CANDIDATES_UNVERIFIED` warning apply as before. The graph file still holds every edge, so the flag takes effect at query time.

### 17.5 Verification gates

Before calling the two packages independent, build and install each wheel in separate clean Windows and Linux environments and prove neither imports the other or a source driver. Contract fixtures must round-trip through both CLIs and REST APIs using the same sealed snapshot; test unknown major and additive minor versions. Test requester search/answer grounding, stale-index rejection, authorization/replay of both scan adapters, and model outage. Test planner lineage versus join separation, multi-input/multi-output OpenLineage ambiguity, SQL dialect and Mongo pipeline rendering, cross-source unresolved staging, prompt injection, read-only AST policy, and that no execution path exists. CI should start these gates only as the packages are implemented; CI now runs these gates as described in section 15; a gate not named there is not yet automated.
