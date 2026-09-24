# Database Intelligence Graph Generator

Evidence-first discovery of database objects, likely relationships, and **observed** data lineage across PostgreSQL, ClickHouse, and MongoDB, with two independent user-facing modules. All five package roles now have a synthetic-tested implementation; live source, model and deployment verification remains in [TODO.md](TODO.md).

## What the system will do

Collectors run beside each configured source with read-only credentials and produce versioned bundles. A central builder validates them, compares permitted features across sources, ranks relationship candidates, and publishes a versioned catalog snapshot. The **catalogue requester** independently searches that snapshot, answers grounded natural-language questions with a private local LLM, and dispatches new scan requests. The **lineage and path planner** independently imports observed SQL/pipeline metadata and OpenLineage events, returns observed lineage or possible join paths, and generates reviewable SQL/MongoDB plans without executing them. Both modules will ship as separate installable Python packages with CLIs and optional HTTP JSON REST services; they share only the artifact contract.

```mermaid
flowchart LR
  PG[PostgreSQL collector] --> B1[Validated bundle]
  CH[ClickHouse collector] --> B2[Validated bundle]
  MO[MongoDB collector] --> B3[Validated bundle]
  B1 --> A[Central assembler]
  B2 --> A
  B3 --> A
  A --> L[Bounded LangGraph reviewer]
  L --> V[vLLM on Linux or WSL2 GPU]
  V --> L
  L --> F[Self-hosted Langfuse traces]
  L --> A
  A --> IR[Versioned catalog snapshot]
  IR --> R[Catalogue requester CLI and REST]
  IR --> P[Lineage and path planner CLI and REST]
  O[Observed SQL, pipeline and OpenLineage events] --> P
  R -->|signed bundle or private HTTP| C[Site collector scan adapter]
  R -->|grounded questions| V
  P --> X[Lineage paths, join paths, SQL/Mongo plans]
```

## Project documents

- [TECHNICAL_IMPLEMENTATION.md](TECHNICAL_IMPLEMENTATION.md): binding implementation contract, module ownership, algorithms, data formats, gates, and acceptance criteria.
- [TODO.md](TODO.md): live, ordered implementation ledger with verification and decision gates.
- [ARCHITECTURE_RESEARCH.md](ARCHITECTURE_RESEARCH.md): 30-section research record and upstream source links.
- [AGENTS.md](AGENTS.md) and [CLAUDE.md](CLAUDE.md): working rules for coding agents. `AGENTS.md` is the canonical policy.

## Current state

The `dbig-contracts` package validates signed JSONL collector bundles and catalog snapshots. The `dbig-collectors` package has PostgreSQL, ClickHouse, and MongoDB metadata discovery with source-local credentials and defensive limits. It publishes a digest of PostgreSQL view definitions, digests of ClickHouse key expressions, and MongoDB validator/index fields; optional MongoDB sampling publishes only path types within row and byte limits. The `dbig-catalog-builder` package validates signed source bundles, publishes a sealed snapshot, distinguishes declared foreign keys from tentative references, and can review ambiguous candidates through a configured private vLLM endpoint with metadata-only Langfuse spans. Its model output is cited, validated weak evidence; model failure still publishes the deterministic snapshot as partial. The requester indexes and searches snapshots, asks a private model for cited catalogue answers, and dispatches signed scan requests through a scheduler directory or private collector HTTP. The planner consumes the same snapshot plus observation files, separates observed lineage from candidate references, and returns bounded paths and reviewable SQL/MongoDB or unresolved cross-source plans. Current tests use synthetic drivers and a fake model; live database, GPU/model, scheduler, TLS proxy and telemetry deployment tests are still required.

For local development, run `uv sync --locked --dev`, `uv run pytest`, and `uv build --all-packages`. At a source site, install only the matching collector extra (`postgresql`, `clickhouse`, or `mongodb`), set the configured connection JSON and signing key in the process environment, then run `dbig collect --config <source.toml> --out <new-bundle-directory>`. The config's `[source]` section requires `source_id`, `kind`, `connection_env`, `signing_key_env`, `signing_key_id`, and an allowed `namespaces` list. The collector refuses plaintext transport and records a partial bundle when stable source identity is unavailable. Never put credentials in TOML or shell arguments.

Run `python scripts/supply_chain.py` to generate the CycloneDX SBOM and Markdown/JSON license reports under ignored `.data/supply-chain/`. The checked-in `supply-chain/licenses.json` records metadata for every pinned PyPI release in `uv.lock`; `python scripts/supply_chain.py --refresh` refreshes it when the lock changes. CI rejects missing or stale entries. Declarations reported by upstream packages are inventory evidence, not a legal conclusion; the MIT license for this repository and its five wheels is in `LICENSE` and each wheel's metadata.

Build a snapshot with `dbig-builder assemble --config <builder.toml> --bundle <bundle-dir> --out <new-snapshot-dir>`; run `dbig-requester index --config <requester.toml>` and `dbig-requester search --config <requester.toml> --query orders`. The requester configuration supplies the snapshot and local state directories and an API key environment reference; `[requester.answer]` and `[requester.scan]` enable private answers and scan dispatch. `dbig-requester serve --config <requester.toml> --host 127.0.0.1 --port <port>` serves the private REST API. At a source site, `dbig accept-scan` or `dbig serve` accepts signed scan requests and `dbig process-scans` runs queued work. The signed scheduler bundle consists of a `request.json` inside a new request-ID directory at the configured handoff root.

Create a planner graph with `dbig-planner build --snapshot <snapshot-dir> --observations <jsonl-dir> --out <graph-dir> [--mapping <dataset-map.json>]`; query it with `dbig-planner lineage-paths` or `join-paths --graph <graph-dir> --from <object-id> --to <object-id> --json`, then `dbig-planner plan --graph <graph-dir> --path-id <returned-id> --json`. Observation JSONL records have `kind: sql` with a source ID, dialect, SQL text, timezone-aware timestamp and executed/defined status; or `kind: pipeline` with catalog input/output object IDs and status. OpenLineage RunEvents with `COMPLETE` and JobEvents require explicit dataset-to-catalog mapping in `dataset_to_object_id`; ambiguous outputs and unresolved SQL produce diagnostics. Raw SQL is parsed but never stored in the derived graph. `dbig-planner serve --config <planner.toml> --host 127.0.0.1 --port <port>` provides its separate REST API. Both APIs bind only to loopback and require a private TLS proxy for remote access.

The selected LLM stack is vLLM for private GPU serving, LangGraph for the builder's fixed review workflow, and self-hosted Langfuse for metadata-only visibility. The requester also calls the private vLLM endpoint for grounded catalogue questions. The planner is deterministic and makes no LLM call. Top-level LangChain and LangGraph Swarm remain deferred. The exact GPU, model revision, service versions and telemetry infrastructure must be validated before deployment.

## Developer setup

1. Install Python 3.12+, Git, [Graphify](https://github.com/Graphify-Labs/graphify), [Graft](https://github.com/trailhq/Graft), and [Codebase Memory MCP](https://github.com/DeusData/codebase-memory-mcp). The supported local graph versions used when creating this repository were Graphify 0.9.65, Graft 0.18.0, and Codebase Memory MCP 0.11.0.
2. Clone the repository and run `python scripts/install_hooks.py` from its root. Git does not propagate local hook configuration through a clone.
3. Copy `.env.example` to `.env` and fill local values. `.env` is ignored by Git. Source credentials must be supplied through the approved secret store or process environment, never committed.
4. Run `python scripts/graph_sync.py --check-tools` to verify graph commands are on `PATH`.
5. Before implementation, read `TECHNICAL_IMPLEMENTATION.md` and keep `TODO.md` open and updated after each meaningful change.

## Commit and CI policy

The **pre-commit** hook refreshes Graphify, Graft, and Codebase Memory and aborts on any failure. The **post-commit** hook refreshes them again against the committed tree. This ordering matters: a post-commit hook alone cannot reject a commit already written. Graph caches are local regeneration products; CI rebuilds them and enforces freshness. GitHub currently denies branch protection/rulesets for this private repository on the account plan, so CI is visible but cannot be made a mandatory remote merge gate. The owner chose to keep the repository private and rely on local hooks plus inspected CI results.

## Licenses and upstream tools

Apache-2.0 and MIT are free for small and commercial uses. [DataHub](https://github.com/datahub-project/datahub) is Apache-2.0 and usable as a reference or integration target; its complete platform is outside this project's chosen scope. [OpenMetadata](https://github.com/open-metadata/OpenMetadata) has different terms for its `ingestion/` subtree, so the project does not embed that code under its permissive-core policy. Full reasoning and citations are in the research document.

[Amundsen](https://github.com/amundsen-io/amundsen) is Apache-2.0 but its repository was archived in September 2026; use its ingestion/search architecture as a reference, not a v1 runtime dependency. [OpenLineage](https://github.com/OpenLineage/OpenLineage) is Apache-2.0 and supplies the run/job/dataset/facet event standard for planner import/export. Neither repository requires deploying its complete stack. The binding module boundaries and exact package decisions are in [TECHNICAL_IMPLEMENTATION.md](TECHNICAL_IMPLEMENTATION.md#17-independent-requester-and-planner-contract-approved-2026-09-23).

The graph tools named above index **this source repository** for developers. They are not runtime dependencies of the Database Intelligence Graph Generator and are not the graph artifact delivered to its users.
