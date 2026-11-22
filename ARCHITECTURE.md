# Architecture

How the system fits together, runs, stores data and what it guarantees. For what it is and how to start, see [README.md](README.md). For the code, see [TECHNICAL_IMPLEMENTATION.md](TECHNICAL_IMPLEMENTATION.md). Terms are defined in [GLOSSARY.md](GLOSSARY.md).

## System context

<!-- verified: 6e43d33 -->

```mermaid
flowchart LR
  PG[(PostgreSQL)] --> C1[collector]
  CH[(ClickHouse)] --> C2[collector]
  MG[(MongoDB)] --> C3[collector]
  C1 -->|signed bundle| B[builder]
  C2 -->|signed bundle| B
  C3 -->|signed bundle| B
  B -->|review calls| V[private vLLM]
  B -->|metadata-only spans| L[Langfuse]
  R -->|metadata-only spans| L
  B -->|snapshot| R[requester]
  B -->|snapshot| P[planner]
  O[observations and OpenLineage events] --> P
  R -->|grounded answers| V
  R -->|signed scan request| C1
  P --> X[paths and plans]
```

The only things that cross a trust boundary are signed bundles, a snapshot and signed scan requests. The planner makes no model call. Source credentials never leave a collector.

## Deployment

<!-- verified: e304a88 -->

Two ways to run it. Each role can run as a Python process started by its own command, as before. Or the five packages run as five containers on one LAN host, from `deploy/compose.yaml` (report Part 3; owner decisions D1, D2 and D6 of 2026-11-10).

```mermaid
flowchart LR
  caller[program on the LAN] -->|HTTPS, caller key| requester
  caller -->|HTTPS, caller key| planner
  caller -->|HTTPS, caller key| builder
  caller -->|HTTPS, caller key| contracts
  requester -->|signed scan request| collectors
  requester --> vllm[private vLLM]
  collectors -->|verified TLS| dbs[(databases)]
  collectors --> bundles[(bundles volume)]
  bundles --> builder
  builder --> snapshots[(snapshots volume)]
  snapshots --> requester
  snapshots --> planner
```

| Container | Command | State | Outbound |
| --- | --- | --- | --- |
| `collectors` | `dbig serve --bundle-root ...`: scan intake and the worker in one process | queue SQLite, `bundles` read-write | its databases, over verified TLS |
| `builder` | `dbig-builder serve`: assembly jobs | `bundles` read-only, `snapshots` read-write, job SQLite | vLLM and Langfuse when review is on |
| `requester` | `dbig-requester serve` | `snapshots` read-only, search index | vLLM, the collector, Langfuse |
| `planner` | `dbig-planner serve` | `snapshots` read-only, graph | none |
| `contracts` | `dbig-contracts serve`: the IR schemas and a record check | none | none |

The caller runs the pipeline (lead ruling D3 (a)): it asks the requester for a scan, starts an assembly on the builder, then calls `POST /v1/catalogue/reindex` and `POST /v1/planner/graph`. The builder points `snapshots/current` at each new snapshot. Until the reindex, search answers `STALE_INDEX`.

**The LAN boundary.** No inbound traffic from outside the LAN and no outbound traffic beyond it, held by five layers:

| Layer | Mechanism | Where |
| --- | --- | --- |
| L1 host bind | each published port has `host_ip` set to the LAN address, never all interfaces | `deploy/compose.yaml`, checked by `scripts/ci_check.py::compose_problems` in the tests (T1) |
| L2 inbound firewall | `DOCKER-USER` drops LAN-interface traffic from outside the LAN CIDR; kept but not applied for now (owner decision 2, 2026-11-21: LAN, no firewall for now) | `deploy/lan_firewall.py`, `deploy/dbig-firewall.service` |
| L3 egress firewall | the compose subnet reaches only the listed database, model and Langfuse addresses; IPv6 is dropped on the compose bridge only (`DBIG_BRIDGE`), so other containers on the host are untouched; kept but not applied for now (decision 2) | same |
| L4 application | every model, Langfuse and collector URL passes `private_base_url`; a database host must be, or resolve only to, private addresses; a `serve` binds only loopback, a private IP literal with TLS, or a private IP literal with plain HTTP when `DBIG_ALLOW_PLAIN_LAN=1` (decision 14, ADR-001) | `packages/contracts/src/dbig_contracts/security.py::bind_refusal`, `::private_host_refusal` |
| L5 caller identity | a key per caller, stored as SHA-256 with scopes and an expiry; two hashes per caller let a key rotate | `packages/contracts/src/dbig_contracts/service.py::ServiceGuard` |

Services serve plain HTTP on the LAN when the deployment sets `DBIG_ALLOW_PLAIN_LAN=1` (owner decision 2026-11-21, "we don't need https for now", ADR-001 with the C5 note, review 2027-05-21); without the flag the old rule holds — a private address binds only with a TLS certificate from the private CA (`scripts/make_ci_certs.py --ip`), and a public or all-interfaces bind is refused either way. Plain settings live in `deploy/.env`, `${NAME}` placeholders in `deploy/config/*.toml` read them at load (owner decision 11). Self-hosted Langfuse runs next to the five services (`deploy/compose.langfuse.yaml`, pinned 4.83.0, its own ports bound to `LAN_IP`; traces stay metadata-only). Docker's `iptables` backend is required, because the `nftables` backend has no `DOCKER-USER` chain. Containers run read-only as UID 10001 with every capability dropped. Secrets are files under `deploy/secrets/`, read through `<NAME>_FILE`. Images come from a registry tag (`DBIG_REGISTRY` + `DBIG_VERSION`, published by the tag release on both remotes) or build from the clone; `scripts/build_images.py` still makes the one `docker save` tarball for a host with no outbound access. `scripts/lan_boundary_check.py` checks the boundary on the host (T4, T5, T7); the LAN firewall itself is kept but not applied for now (owner decision 2, 2026-11-21). On 2026-11-22 the full stack with Langfuse ran on one laptop with ports on loopback: 7 of 7 healthchecks, one authenticated route per service, and the OpenAPI of all five (see `docs/plans/app-machine/REHEARSAL_NOTES.md`). The project databases on the remote host get TLS from the same private CA through `deploy/remote_tls.py`, with plaintext kept (owner, 2026-11-11).

| Setting | Where | Example |
| --- | --- | --- |
| LAN address, LAN CIDR, interface | `deploy/.env` | `LAN_IP`, `LAN_CIDR`, `LAN_INTERFACE` |
| Compose subnet and one address per service | `deploy/.env` | `DBIG_SUBNET`, `REQUESTER_IP` |
| Allowed outbound targets | `deploy/.env` | `DBIG_EGRESS=ip:port,...` |
| Caller keys, one file per service | `deploy/secrets/<service>-keys.json` | `{"keys": [{"caller", "sha256", "scopes", "expires_at"}]}` |
| Service configuration | `deploy/config/<service>.toml` | the same TOML as the CLIs |

Without Compose, each `serve` command still binds loopback by default, and a LAN bind needs `--tls-cert` and `--tls-key`. A public exposure is unsupported. See [TECHNICAL_IMPLEMENTATION.md](TECHNICAL_IMPLEMENTATION.md) §17.3 for the routes and §5 for configuration precedence.

## Main data flow

<!-- verified: 6e43d33 -->

```mermaid
flowchart LR
  S[(source catalog)] --> BUN["bundle: catalog files, profiles, tokens, ranges, diagnostics"]
  BUN --> CAND[candidate engine]
  CAND --> SNAP["snapshot: nodes, edges, evidence, runs"]
  SNAP --> IDX[search index]
  SNAP --> GRF[path graph]
  OBS[observations] --> GRF
  GRF --> PATH[join paths and lineage paths]
  PATH --> PLAN[plans]
  IDX --> ANS[search results and answers]
```

The snapshot is the interchange contract. The search index and the path graph are caches that anyone can rebuild from it.

## Data contract

<!-- verified: 6e43d33 -->

```mermaid
erDiagram
  SOURCE ||--o{ OBJECT : "source_id"
  OBJECT ||--o{ FIELD : "object_id"
  OBJECT ||--o{ CONSTRAINT : "object_id"
  FIELD ||--o| PROFILE : "field_id"
  NODE ||--o{ EDGE : "from_id, to_id"
  EDGE }o--|{ EVIDENCE : "evidence_ids"
  SOURCE ||--o{ DIAGNOSTIC : "source_id"
  OBJECT { string native_id string kind string name }
  FIELD { string native_id string data_type }
  EDGE { string kind string status float confidence string evidence_tier }
  EVIDENCE { string kind string subject_id }
```

`NODE` stands for a source, object or field row. A snapshot has no diagnostics file. `confidence` stays null until calibrated; `evidence_tier` carries the strength of a candidate.

## Main workflow

<!-- verified: 6e43d33 -->

```mermaid
sequenceDiagram
  participant C as collector
  participant B as builder
  participant R as requester
  participant P as planner
  C->>C: read catalog within budget
  C->>B: signed bundle
  B->>B: verify, build candidates, publish snapshot
  R->>R: index snapshot
  R->>R: search or answer from retrieved entries
  P->>P: build graph from snapshot and observations
  P->>P: find paths, render plan
  R->>C: signed scan request
  C->>C: verify, queue, scan later
```

A question is answered only from retrieved entries, and a plan is returned, never run.

## Guarantees and limits

<!-- verified: 6e43d33 -->

| Guarantee | Held by |
| --- | --- |
| A collector reads metadata only by default. Sampling, history, cardinality probes, format profiles and value tokens are opt-in per source. | `packages/collectors/src/dbig_collectors/config.py::SourceConfig` |
| A consumer rejects an unknown major version, a bad signature, an unsafe path or a digest mismatch. | `packages/contracts/src/dbig_contracts/artifact.py::read_artifact` |
| An object with no server-assigned identity is not catalogued, and a diagnostic says so. | `packages/collectors/src/dbig_collectors/runtime.py::discover_clickhouse` |
| An incremental run that lacks a complete scan keeps the last complete bundle of that source and marks the snapshot `partial`. | `packages/catalog-builder/src/dbig_builder/state.py::assemble_incremental` |
| A candidate is never lineage, and a model suggestion is weak evidence only. | `packages/catalog-builder/src/dbig_builder/reviewer.py::review_candidates` |
| The planner opens no source database connection and runs no plan. | `packages/planner/src/dbig_planner/plans.py::render_plan` |
| A scan request that is unsigned, expired, replayed or out of scope is refused. | `packages/collectors/src/dbig_collectors/scan.py::accept_scan` |

Measured quality is limited. On the one live run, 5 of 21 proposed relationships were true and 16 were false ([artifacts/live-test-report.md](artifacts/live-test-report.md), section 8). Phase 1 to 7 fixes for that run are merged to `main`. A later PostgreSQL and ClickHouse run, scored with the owner's rule that every cross-source pair is false, found 35 true and 13 false of 48 candidates ([summary](artifacts/run-20261020-pgch/summary.md), step 1). The two runs use different data and scoring, so do not compare them. See [README.md](README.md#limits).

## Security and personal data

<!-- verified: 6e43d33 -->

The catalogue holds names, types, statistics and optional owner comments. It holds no row values. Value tokens and value ranges exist only in bundles, are keyed per run, and never enter a snapshot. Source text, such as comments and SQL, is untrusted data: pages escape it and models see it marked as data. Every secret is a named environment variable that `dbig_contracts.security.secret_from_env` resolves. [THREAT_MODEL.md](docs/THREAT_MODEL.md) lists the eleven modelled threats, each control and its test, and the known gaps. Details are in [TECHNICAL_IMPLEMENTATION.md](TECHNICAL_IMPLEMENTATION.md) §13.

## Performance

<!-- verified: 6e43d33 -->

At 10 sources, 200 objects and 1,000 fields, every command finished in under 3 s, with assemble peaking at 110 MiB and each collector at most 44 MiB, and an in-process search had a 87 ms median. These come from `scripts/scale_benchmark.py` run on 2026-10-04 and recorded in [TODO.md](TODO.md) (change: verification fixes 1-10, item 9). The benchmark predates the Phase 1 to 7 changes and was not repeated. Model latency is unmeasured on a GPU host. Method and caveats: [TECHNICAL_IMPLEMENTATION.md](TECHNICAL_IMPLEMENTATION.md) §14.

## Libraries, standards and external services

<!-- verified: 6e43d33 -->

| Item | Role | Where it is confined |
| --- | --- | --- |
| PostgreSQL, ClickHouse, MongoDB drivers | Read source catalogs | Collector extras only |
| vLLM, OpenAI-compatible client | Private model calls | Builder and requester, configured private URL only |
| LangGraph | Fixed review workflow | Builder |
| Langfuse | Metadata-only spans | Builder and requester; a required install, used only when configured |
| SQLGlot, NetworkX | SQL parsing, bounded path search | Planner |
| OpenLineage | Event standard for planner input | Planner |
| FastAPI, Uvicorn | Private REST | `api` extra of the collectors, requester and planner |

Versions and the reason for each choice: [TECHNICAL_IMPLEMENTATION.md](TECHNICAL_IMPLEMENTATION.md) §4. Design decisions are recorded with their dates in TECHNICAL_IMPLEMENTATION.md §6a, §7a, §7b, §8b and §16. Research background: [ARCHITECTURE_RESEARCH.md](docs/ARCHITECTURE_RESEARCH.md).
