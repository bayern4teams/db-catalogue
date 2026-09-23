# Database Intelligence Graph Generator

Headless, evidence-first discovery of database objects, likely relationships, and **observed** data lineage across PostgreSQL, ClickHouse, and MongoDB. The project is in the architecture and implementation-planning stage; no collector or assembler runtime has been implemented yet.

## What the system will do

Collectors run beside each configured source with read-only credentials. They discover schema and bounded profiles, then produce signed or digest-verified, versioned bundles. A central assembler validates the bundles, compares safe features across sources, ranks relationship candidates, imports observed SQL/pipeline lineage, and emits a machine-readable graph. A local GPU-hosted Ollama/Qwen reviewer examines ambiguous candidates in version 1. Its suggestions cannot, by themselves, establish a foreign key or lineage edge.

```mermaid
flowchart LR
  PG[PostgreSQL collector] --> B1[Validated bundle]
  CH[ClickHouse collector] --> B2[Validated bundle]
  MO[MongoDB collector] --> B3[Validated bundle]
  B1 --> A[Central assembler]
  B2 --> A
  B3 --> A
  P[Observed SQL and pipeline events] --> A
  A --> L[Local Ollama reviewer]
  L --> A
  A --> IR[Versioned graph JSONL and manifest]
```

## Project documents

- [TECHNICAL_IMPLEMENTATION.md](TECHNICAL_IMPLEMENTATION.md): binding implementation contract, module ownership, algorithms, data formats, gates, and acceptance criteria.
- [TODO.md](TODO.md): live, ordered implementation ledger with verification and decision gates.
- [ARCHITECTURE_RESEARCH.md](ARCHITECTURE_RESEARCH.md): 30-section research record and upstream source links.
- [AGENTS.md](AGENTS.md), [CLAUDE.md](CLAUDE.md), and [AGENT.md](AGENT.md): working rules for coding agents.

## Current state

This repository contains plans, workflow policy, Git hooks, and CI scaffolding. It does **not** yet contain production code. The tools and versions listed in the technical plan are choices to validate in an implementation lockfile, not claims that the application is deployable now.

## Developer setup

1. Install Python 3.12+, Git, [Graphify](https://github.com/Graphify-Labs/graphify), [Graft](https://github.com/trailhq/Graft), and [Codebase Memory MCP](https://github.com/DeusData/codebase-memory-mcp). The supported local graph versions used when creating this repository were Graphify 0.9.65, Graft 0.18.0, and Codebase Memory MCP 0.11.0.
2. Clone the repository and run `python scripts/install_hooks.py` from its root. Git does not propagate local hook configuration through a clone.
3. Copy `.env.example` to `.env` and fill local values. `.env` is ignored by Git. Source credentials must be supplied through the approved secret store or process environment, never committed.
4. Run `python scripts/graph_sync.py --check-tools` to verify graph commands are on `PATH`.
5. Before implementation, read `TECHNICAL_IMPLEMENTATION.md` and keep `TODO.md` open and updated after each meaningful change.

## Commit and CI policy

The **pre-commit** hook refreshes Graphify, Graft, and Codebase Memory and aborts on any failure. The **post-commit** hook refreshes them again against the committed tree. This ordering matters: a post-commit hook alone cannot reject a commit already written. Graph caches are local regeneration products by default; CI rebuilds them and enforces freshness. The check is also required in GitHub Actions. A developer can bypass local hooks with Git options, so a protected branch must require the CI job before merge.

## Licenses and upstream tools

Apache-2.0 and MIT are free for small and commercial uses. [DataHub](https://github.com/datahub-project/datahub) is Apache-2.0 and usable as a reference or integration target; the complete platform is outside this headless v1. [OpenMetadata](https://github.com/open-metadata/OpenMetadata) has different terms for its `ingestion/` subtree, so the project does not embed that code under its permissive-core policy. Full reasoning and citations are in the research document.

The graph tools named above index **this source repository** for developers. They are not runtime dependencies of the Database Intelligence Graph Generator and are not the graph artifact delivered to its users.
