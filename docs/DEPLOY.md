# Move the catalogue to the app machine

This guide runs the five catalogue services, and their optional Langfuse telemetry, on the
**app machine** — the same host the normalization app runs its API on (a separate repository).
The databases live on the benchmark box and stay read-only to the collector. Why the machine
is built this way: [ADR-001](adr/ADR-001-app-machine-plain-http.md).

| Machine | Runs |
|---|---|
| App machine | Five services (collector with its two source replicas, builder, requester, planner, contracts) and, when enabled, the Langfuse stack |
| Benchmark box (`<bench box address>`) | The PostgreSQL, ClickHouse and MongoDB databases the collector reads, over TLS |
| vLLM machine (`<vLLM machine address>`) | The model behind `ask` and the relationship review |
| Laptop | Nothing of the stack. You call the API with curl. |

Every command says where it runs: **[app machine]**, **[owner]** (your own admin page or
shell), **[box]**, **[vLLM machine]**, **[laptop]** or **[GitHub]** / **[GitLab]** (the web
page). Text in `<angle brackets>` is yours to fill in. No real address appears in this
repository: the committed files carry TEST-NET placeholders (owner decision 1).

## 1. Prerequisites

1. **[app machine]** Docker Engine with the Compose plugin (2.24 or later), and git.
   Check: `docker compose version`.
2. **[box]** PostgreSQL, ClickHouse and MongoDB listening on their TLS ports, each with a
   certificate your CA bundle verifies. Database TLS stays required; the CA is
   `source-ca-bundle.pem` from step 7.
3. **[vLLM machine]** vLLM serving one pinned model with a bearer key, as in
   `docs/LOCAL_RUN.md` step 9, bound to the LAN instead of 127.0.0.1.
4. Check from the **[app machine]**: `openssl s_client -connect <bench box address>:5432
   -starttls postgres` completes a TLS handshake, and
   `curl -s -H "Authorization: Bearer <vllm key>" http://<vLLM machine address>:8000/v1/models`
   lists the model.

## 2. What the app machine needs

| Part | Measured on the laptop rehearsal, 2026-11-22 (laptop-measured) |
|---|---|
| The five services under load | 33–84 MiB each (`docker stats`); `DBIG_MEM_LIMIT` caps each container at 1 GiB |
| Langfuse stack, in use | 2.3 GiB total: web 754, worker 582, ClickHouse 841, Postgres 33, MinIO 83, Redis 9 (MiB) |
| Langfuse volumes, after first boot | 151 MiB (Postgres 68 MB, ClickHouse 81 MB) |
| The five images | 208–351 MB each, about 1.3 GB |

Give the machine **8 GiB of memory and 40 GiB of free disk** for db-catalogue together with
normalization. normalization's own numbers are in its deploy guide, "Move the app to the app
machine" — a separate repository; do not add the two guides' sizes independently.
Check: `df -h /` and `free -g` show the headroom.

## 3. Let the app machine reach the box and vLLM

1. **[box]** Add `<app-machine address>` to the rules of `bench-docker-firewall.service` for
   the database TLS ports (5432, 8443, 9440) and restart the service. This is the same machine
   and the same service as step 2 of the normalization guide, so if that step already allowed
   the address, only the database ports may still need adding.
2. **[vLLM machine]** Allow `<app-machine address>` to reach port 8000, and nothing else.
3. Check: the two checks of step 1.4 pass.

## 4. Read-only logins and source files (owner)

1. **[owner]** Create the read-only account on each bench database exactly as
   `docs/LOCAL_RUN.md` step 2 says (`dbig_reader`; ClickHouse with `readonly = 2`).
2. **[owner]** Fill `deploy/secrets/source-connection-slot1.json` through `slot3.json`
   yourself, one login each, in the URL form the committed placeholder shows, e.g.
   `{"url": "postgresql://dbig_reader:<password>@<bench box address>:5432/<database>"}`.
   Owner decision 5: the owner adds these files; they never go into git, a chat log or a
   ticket.
3. Check: each file parses as JSON with only a `url` key.

## 5. Get the stack on the app machine

Either build from a clone or run tagged release images (owner decision 8).

1. **[app machine]**

   ```bash
   git clone <repository URL> ~/db-catalogue
   ```

   A later update on the same checkout: `git pull`. Leave `DBIG_REGISTRY` empty to build the
   images from this clone.
2. To run release images: **[app machine]** `docker login ghcr.io -u <github user>` (or the
   GitLab registry with a deploy token, as the normalization guide step 4 describes), then in
   `deploy/.env` set `DBIG_REGISTRY` to the registry path ending in `/` (for example
   `ghcr.io/<github user>/db-catalogue/`) and `DBIG_VERSION` to the release version (step 12).
3. Check: the clone holds `deploy/compose.yaml`, `compose.sources.yaml` and `deploy/.env.example`.

## 6. Fill deploy/.env

1. **[app machine]**

   ```bash
   cd ~/db-catalogue/deploy && cp .env.example .env && chmod 600 .env
   ```

2. Replace **every** TEST-NET `192.0.2.x` placeholder with the real address or network:
   `LAN_IP`, `LAN_CIDR`, `VLLM_BASE_URL`, `BENCH_PG_HOST`, `BENCH_CH_HOST`,
   `BENCH_MONGO_HOST`, `LANGFUSE_URL`, `DBIG_EGRESS`.
3. Set `VLLM_MODEL` to the name `/v1/models` lists and `VLLM_MODEL_DIGEST` to the pinned
   revision's digest. Generate the `LANGFUSE_*` values now — the three passwords and the
   encryption key with `openssl rand -hex 24` (they go inside `DATABASE_URL`, where base64's
   `+/=` break Prisma), `LANGFUSE_NEXTAUTH_SECRET` and `LANGFUSE_SALT` with
   `openssl rand -base64 32`.
4. Check: `grep -c '192\.0\.2' .env` prints 0 and no line still says `<generated>`.

## 7. Secrets

Values live as files under `deploy/secrets/`, never in `.env`.

1. **[app machine]** Generate the service keys:

   ```bash
   cd ~/db-catalogue/deploy/secrets
   python3 -c 'import secrets; print(secrets.token_urlsafe(48))' > scan-signing.key
   python3 -c 'import secrets; print(secrets.token_urlsafe(48))' > token.key
   python3 -c 'import secrets; print(secrets.token_urlsafe(32))' > requester-collector-token
   ```

2. **[owner]** Put the CA that signed the database certificates in `source-ca-bundle.pem`,
   paste the vLLM bearer key into `model.key`, and keep the slot files from step 4.
3. Caller keys. Each `caller-keys-<service>.json` holds a `keys` array; an entry has `caller`,
   `sha256`, `scopes` and `expires_at`. Make a random key per caller, hash it, and write the
   hash into the file (the key itself lives only on the caller):

   ```bash
   printf '%s' '<the caller key>' | sha256sum
   ```

   Replace every rehearsal `sha256` and `expires_at`. The scopes per service:

   | Service | Scopes |
   |---|---|
   | collectors | `scan` |
   | requester | `search`, `ask`, `scan`, `reindex` |
   | planner | `plan`, `graph` |
   | builder | `assemble` |
   | contracts | `validate` |

4. Check: `chmod 600 deploy/secrets/*`, and `docker compose -f compose.yaml
   -f compose.sources.yaml --env-file .env config` (run in `deploy/`) names no missing file.

## 8. Start

**[app machine]**, in `~/db-catalogue/deploy`:

```bash
docker compose -f compose.yaml -f compose.sources.yaml --env-file .env up -d
```

To run Langfuse from the first start, add `-f compose.langfuse.yaml` (its secrets and keys
come in step 11). Check: `docker compose ps` shows seven services `healthy` — the five plus
the ClickHouse and MongoDB collector replicas.

## 9. Health and scope checks

```bash
for p in 8441 8442 8443 8444 8445 8446 8447; do
  curl -s -o /dev/null -w "%{http_code} " http://<app-machine address>:$p/healthz
done; echo
```

Check: seven `200`s. Every other route, `/openapi.json` included, needs a caller key (owner
decision 16):

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://<app-machine address>:8443/openapi.json
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer <requester key>" \
  http://<app-machine address>:8443/openapi.json
```

Check: `401` without the key, `200` with it. A key missing the route's scope gets `403`.

## 10. First data

The stack starts empty: the volumes hold nothing until you collect.

1. **[app machine]** Collect each source through the requester (`scan` scope), one request per
   slot:

   ```bash
   curl -s -X POST -H "Authorization: Bearer <requester key>" -H 'content-type: application/json' -d '{
     "request": {"id": "first-pg", "source_id": "bench-pg", "scopes": ["public"],
       "capabilities": ["metadata"], "reason": "first collect", "requester_id": "ops",
       "idempotency_key": "first-pg-1", "issued_at": "<now, UTC>", "expires_at": "<one hour later>"},
     "adapter": "bundle"}' \
     http://<app-machine address>:8443/v1/scan-requests
   ```

   Poll `GET /v1/scan-requests/<request_id>` until the receipt says `complete` and names the
   `bundle_path`. Repeat for `bench-ch` (namespace `default`) and `bench-mongo` (`appdb`).
2. Assemble one snapshot from all three bundles (`assemble` scope):

   ```bash
   curl -s -X POST -H "Authorization: Bearer <builder key>" -H 'content-type: application/json' \
     -d '{"bundles": ["<bundle_path_1>", "<bundle_path_2>", "<bundle_path_3>"]}' \
     http://<app-machine address>:8442/v1/builder/assemblies
   ```

   Poll `GET /v1/builder/assemblies/<job_id>` until the job completes.
3. Reindex so search sees the snapshot (`reindex` scope; before it, search answers `STALE_INDEX`):

   ```bash
   curl -s -X POST -H "Authorization: Bearer <requester key>" \
     http://<app-machine address>:8443/v1/catalogue/reindex
   ```

Check: `GET /v1/catalogue/search?query=<a word from your schema>` returns hits, and a question
to `POST /v1/catalogue/questions` comes back with citations that exist in the catalogue.

## 11. Enable Langfuse (optional)

1. **[app machine]** If the stack started without Langfuse, set the `LANGFUSE_*` values from
   step 6 and start everything together:

   ```bash
   docker compose -f compose.yaml -f compose.sources.yaml -f compose.langfuse.yaml --env-file .env up -d
   ```

   The first boot runs migrations for a few minutes. Check: `curl -s -o /dev/null
   -w '%{http_code}\n' http://<app-machine address>:3000` answers.
2. **[owner]** Open `http://<app-machine address>:3000`, create the organisation and one
   project, and copy its public and secret keys.
3. **[app machine]**

   ```bash
   printf '%s' '<pk...>' > secrets/langfuse-public.key
   printf '%s' '<sk...>' > secrets/langfuse-secret.key
   ```

4. Restart the two services that trace:

   ```bash
   docker compose -f compose.yaml -f compose.sources.yaml --env-file .env up -d builder requester
   ```

   Check: the project page shows traces arriving. They are metadata-only — never prompts,
   replies, SQL or values (enforced in code) — Langfuse never phones home
   (`TELEMETRY_ENABLED=false`), and if it is down the services keep working and drop
   telemetry.

## 12. Releases

Tag `v0.1.0` only after the app machine has passed steps 8–10 end to end (owner decision 7).

1. **[laptop]** The gate: the tag must equal every package version:

   ```bash
   python3 scripts/check_release_version.py v0.1.0
   ```

   Both CI files run the same check before publishing anything.
2. **[laptop]** `git tag -a v0.1.0 -m "v0.1.0"`, then push the tag; `origin` pushes to GitHub
   and GitLab both.
3. **[GitHub]** and **[GitLab]**: the release pipelines publish to both remotes — images to
   `ghcr.io` and the GitLab container registry, wheels to the GitLab package registry.
4. **[app machine]** Adopt a release: set `DBIG_VERSION` (and `DBIG_REGISTRY`, step 5),
   `docker compose -f compose.yaml -f compose.sources.yaml --env-file .env pull`, then the
   step 8 start command. Roll back by setting the old version and repeating.

## When something goes wrong

Every line below was measured in the laptop rehearsal of 2026-11-22:

| Symptom | Cause and fix |
|---|---|
| `langfuse-web` crash-loops: Prisma `P1013` | A base64 password inside `DATABASE_URL` (`+/=` are not valid there). Regenerate `LANGFUSE_DB_PASSWORD` (and the other two) with `openssl rand -hex 24`. |
| `langfuse-web` crash-loops: Prisma `P1000` | The password rotated in `.env` but Postgres kept the old one. Run `ALTER USER langfuse WITH PASSWORD '<new>'` inside the langfuse-postgres container, or recreate the volumes. |
| `ask` answers 503 `MODEL_TIMEOUT` | The default 20 s timeout is too short; answers take 12–60 s. `deploy/config/requester.toml` sets `timeout_seconds = 60` — keep it. |
| `PermissionError` after copying files into a volume from the host | Copied files carry the host uid's ownership. Fix it inside the service's container: `docker compose exec -u 0 <service> chown -R 10001:10001 <path>`. |
| A service restart-loops with exit code 127 | An old or wrong image tag. `docker compose ... pull` and check `DBIG_VERSION`. |

## Not checked

- A real app machine. Every number above is laptop-measured (rehearsal 2026-11-22).
- A live collect from this stack against the bench databases (TLS handshakes, the reader logins).
- The LAN firewall files (`deploy/dbig-firewall.service`, `deploy/lan_firewall.py`): kept in
  the repository but unused for now (owner decision 2, 2026-11-21).
- The answer quality of a fresh collect against real data.
