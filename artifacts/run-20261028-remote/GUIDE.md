# db-catalogue — how to run everything yourself

This guide covers the whole system end to end. Every command below was run on 2026-10-28 against the two remote databases. The real logs are in this folder: `pg-commands.txt`, `ch-commands.txt`, `both-commands.txt` and `planner-commands.txt`.

## 0. What the system is

| Part | Command | What it does | Talks to |
|---|---|---|---|
| **Collector** | `dbig` | Reads **metadata only** from one database: tables, columns, keys and value-free statistics. It writes a signed *bundle*. | the database (read-only) |
| **Builder** | `dbig-builder` | Checks the bundle signatures and merges bundles into one *catalogue snapshot*. It proposes join candidates, each with an evidence tier, and renders an HTML catalogue. | files only |
| **Requester** | `dbig-requester` | Search over the catalogue, plus natural-language questions answered by the local LLM with citations. It can also send scan requests to collectors. | the snapshot and the vLLM model |
| **Planner** | `dbig-planner` | Finds join paths and lineage paths, and writes SQL/Mongo read plans for them. **It never runs them.** | files only |

The parts exchange only versioned files (`ir_version 1.0`), never direct imports. Each part is a separate installable package with its own CLI and an optional HTTP JSON service.

```
 PostgreSQL ─┐                                                     ┌─► catalogue.html
 ClickHouse ─┼─► dbig collect ─► bundle ─► dbig-builder assemble ─► snapshot ─┼─► dbig-requester ask/search ◄─► vLLM model
 MongoDB  ───┘   (signed, value-free)        (verify + merge)                  └─► dbig-planner join-paths / plan
```

## 1. Install (once)

```bash
# Python 3.12 and uv 0.12.19
cd db-catalogue
uv sync --locked --all-packages --all-extras
```

## 2. Secrets: only in `.env`, never in config files

Config files name **environment variables**; the programs read the values at run time. Nothing secret is written to a file or log.

| Variable | Used by | Format |
|---|---|---|
| `PG_CONNECTION` | collector (PostgreSQL) | JSON `{"host","port","database","user","password"}` |
| `CLICKHOUSE_CONNECTION` | collector (ClickHouse) | JSON `{"host","port","username","password"}` |
| `MONGODB_CONNECTION` | collector (MongoDB) | connection JSON (see `examples/local/mongodb.toml`) |
| `DBIG_SIGNING_KEY` | collector signs, builder verifies | random secret; the same value for every bundle in one run |
| `DBIG_TOKEN_KEY` | collector | key for value digests (overlap measurement without values) |
| `VLLM_API_KEY` | requester → model | the token given to `vllm serve --api-key` |
| `REQUESTER_TOKEN` | requester HTTP service | bearer token clients must send |

Load the file into your shell first: `set -a; . ./.env; set +a`. Our scripts do the same without printing anything.

## 3. Network: how we reach the databases and the model

- **Databases.** The remote host runs the databases in Docker. Its firewall admits only one approved machine.
  - The collector refuses **unencrypted** connections to any non-local address (`tls = false is allowed only for loopback`), so it does not connect directly to the remote IP.
  - Either enable TLS on the database servers, or use an SSH tunnel, which is what we did:
  ```bash
  ssh -N -L 127.0.0.1:15401:127.0.0.1:5401 -L 127.0.0.1:18101:127.0.0.1:8101 <user>@<db-host>
  # PG_CONNECTION then uses host 127.0.0.1 port 15401; CLICKHOUSE_CONNECTION 127.0.0.1 port 18101
  ```
  Our `run.sh` points the connection at the tunnel in memory only; `.env` is not changed.
- **Model.** vLLM serves `Qwen/Qwen3.6-35B-A3B-FP8` (revision `95a723d08a9490559dae23d0cff1d9466213d989`) on the GPU host. It is reached through a tunnel:
  ```bash
  ssh -N -L 127.0.0.1:8000:<gpu-host>:8000 <user>@<ssh-host>
  curl -H "Authorization: Bearer $VLLM_API_KEY" http://127.0.0.1:8000/v1/models   # check it is up
  ```
  The requester calls the OpenAI-compatible `POST /v1/chat/completions` there.

## 4. Configuration files used in this run (`config/`)

| File | Purpose | Key settings |
|---|---|---|
| `postgresql.toml` | collector for Diffbot | `source_id="pg-diffbot"`, `namespaces=["diffbot"]`, `sample_rows=25000`, `format_profiles=true`, `max_queries=200` |
| `clickhouse.toml` | collector for ZoomInfo | `source_id="ch-zoominfo"`, `namespaces=["testzoominfo"]`, same sampling, `format_profiles=true` |
| `builder.toml` | builder | `signing_keys = { local = "DBIG_SIGNING_KEY" }` |
| `requester-pg.toml` / `requester-ch.toml` | search over one database | `snapshot_dir`, `state_dir`, `api_key_env` |
| `requester-both.toml` | search + questions over **both** databases | adds `[requester.answer]`: `base_url`, `model`, `model_digest`, `api_key_env`, `timeout_seconds=900`, `retrieval_limit=10` |

`max_queries` is the collector's query budget per run. With the default of 50, Diffbot's optional format profiles were cut short, so it was raised to 200, and Diffbot then completed.

## 5. Step by step (CLI)

Run from the repository root. Every file below lives in `artifacts/run-20261028-remote/`; prefix the paths with that folder, e.g. `artifacts/run-20261028-remote/config/postgresql.toml`. The scripts `run.sh` and `both.sh` already do this.

Run each database separately first.

```bash
# 5.1 Collect (read-only, value-free), then verify the signature
uv run dbig collect --config config/postgresql.toml --out bundle-pg
uv run dbig validate-bundle --key-env DBIG_SIGNING_KEY bundle-pg

# 5.2 Build one catalogue snapshot and its HTML page
uv run dbig-builder assemble --config config/builder.toml --bundle bundle-pg --out snapshot-pg
uv run dbig-builder render  --config config/builder.toml --snapshot snapshot-pg --bundle bundle-pg --out catalog-pg.html

# 5.3 Index it for search
uv run dbig-requester index --config config/requester-pg.toml
```

The same steps with `clickhouse.toml` produce `bundle-ch`, `snapshot-ch` and `catalog-ch.html`. Scripted form: `./run.sh pg` and `./run.sh ch`.

**Both databases together.** This is what questions should use.

```bash
uv run dbig-builder assemble --config config/builder.toml --bundle bundle-pg --bundle bundle-ch --out snapshot-both
uv run dbig-builder render  --config config/builder.toml --snapshot snapshot-both --bundle bundle-pg --bundle bundle-ch --out catalog-both.html
uv run dbig-requester index --config config/requester-both.toml
```

**Search (no model needed).**

```bash
uv run dbig-requester search --config config/requester-both.toml --query "company revenue" --limit 10
# filters: --source-id, --kind, --data-type, --tag, --capability, --evidence-status, --max-age-seconds, --offset, --match all|any
```

**Ask a question (uses the model).**

```bash
uv run dbig-requester ask --config config/requester-both.toml --question "Which table links people to companies?"
```

- The output is JSON with:
  - `answer`;
  - `cited`, the catalogue items used;
  - an `error` code when it could not answer.
- Answers cite only catalogue metadata; the model never sees row values.
- Scripted run of the 40 test questions: `python3 ask.py`. It runs 6 in parallel and writes `answers-both.json`.

**Join paths and read plans (planner; never executes).**

```bash
mkdir -p observations          # optional lineage observations (none in this run)
uv run dbig-planner build --snapshot snapshot-both --observations observations --out planner-graph
uv run dbig-planner join-paths --graph planner-graph --from <object-id> --to <object-id>
uv run dbig-planner join-paths --graph planner-graph --from <id> --to <id> --include-unverified-cross-source
uv run dbig-planner plan --graph planner-graph --path-id <path_id> --max-rows 100
uv run dbig-planner lineage-paths --graph planner-graph --from <id> --to <id>
```

- Object IDs are in `snapshot-both/nodes.jsonl`, and they are also shown in the catalogue page.
- By default, cross-source links with no measured value overlap are **hidden**. Add `--include-unverified-cross-source` to see them; they come back marked `CROSS_SOURCE_CANDIDATES_UNVERIFIED`.

**Inspect and validate.**

```bash
uv run dbig-builder explain-edge --graph snapshot-both <edge-id>      # why a join candidate exists
uv run dbig-builder validate-graph snapshot-both
```

## 6. HTTP JSON APIs (optional services)

Every service:
- requires `Authorization: Bearer <token>`, where the token comes from the env var named in its config;
- limits request size and rate;
- returns errors as `{"error": {"code": "..."}}` or `{"detail": {"code": "..."}}`.

Bind them to `127.0.0.1` unless they are behind TLS.

### 6.1 Requester

```bash
uv run dbig-requester serve --config config/requester-both.toml --host 127.0.0.1 --port 8200 --no-access-log
```

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/v1/catalogue/search` | `?query=&source_id=&kind=&data_type=&tag=&capability=&evidence_status=&max_age_seconds=&limit=20&offset=0` | ranked catalogue items |
| POST | `/v1/catalogue/search` | `{"query": "...", "source_id": null, "limit": 20, "offset": 0, ...}` | same; keeps the query out of URLs and logs |
| POST | `/v1/catalogue/questions` | `{"question": "..."}` (1–1024 chars) | `answer`, `cited`; `503 MODEL_NOT_CONFIGURED` if `[requester.answer]` is missing |
| POST | `/v1/scan-requests` | `{"request": <ScanRequest>, "adapter": "bundle" \| "http"}` | receipt; asks a collector for an authorized rescan |
| GET | `/v1/scan-requests/{request_id}` | — | scan status |

```bash
curl -s -X POST http://127.0.0.1:8200/v1/catalogue/questions \
  -H "Authorization: Bearer $REQUESTER_TOKEN" -H "Content-Type: application/json" \
  -d '{"question":"Which ClickHouse table holds person contact details?"}'
```

### 6.2 Planner

Its config has a `[planner]` section with `graph_dir` and `api_key_env`.

```bash
uv run dbig-planner serve --config planner.toml --host 127.0.0.1 --port 8300
```

| Method | Path | Body |
|---|---|---|
| POST | `/v1/join-paths` | `{"from_id": "object:…", "to_id": "object:…", "max_hops": 4, "max_paths": 20}` |
| POST | `/v1/lineage/paths` | same shape |
| POST | `/v1/plans` | `{"path_id": "<64 hex>", "max_rows": 1000}` |

### 6.3 Collector

The collector is the scan-request receiver. Its source config needs `http_token_env` and the scan settings; see the comments in `examples/local/*.toml`.

```bash
uv run dbig serve --config config/postgresql.toml --queue-db scans.sqlite3 --host 127.0.0.1 --port 8100
uv run dbig process-scans --config config/postgresql.toml --queue-db scans.sqlite3 --out-root scans/
```

| Method | Path | Body |
|---|---|---|
| POST | `/v1/collector/scan-requests` | a **signed** scan request; unsigned or wrongly signed requests are refused |
| GET | `/v1/collector/scan-requests/{request_id}` | — |

CLI alternative without HTTP:
- `dbig-requester request-scan --config … --request req.json --dispatch bundle|http`
- `dbig accept-scan --config … --request req.json --queue-db scans.sqlite3`

## 7. Safety properties you can check yourself

- **No row values leave the collector.** Bundles hold names, types, counts, shares and keyed digests only. `tokens.jsonl` (digests) and `ranges.jsonl` (min/max) stay local and are git-ignored.
- **Signatures.** A bundle with a wrong `DBIG_SIGNING_KEY` fails `validate-bundle` and `assemble`.
- **Plain-text guard.** Unencrypted database connections work only on loopback.
- **Planner.** It only *writes* plans. It reports unresolved prerequisites, such as `approved_staging_or_federated_target`, instead of guessing.
- **Model.** It sees catalogue metadata only. Every citation must be an item it was given; an unsupported or uncited reply is refused with an error code (`MODEL_INVALID_CITATION`, `MODEL_ANSWER_UNEXPLAINED`) instead of being shown as fact.
