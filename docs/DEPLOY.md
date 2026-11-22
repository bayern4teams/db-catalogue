# Move the catalogue to the app machine

This guide installs the five catalogue services, and optionally their Langfuse telemetry, on
the **app machine**. The normalization app runs on the same host from a separate repository.
Follow the steps in order. Every step says where it runs: **[app machine]**, **[box]** (the
database host), **[vLLM machine]**, **[laptop]**, or **[GitHub]** / **[GitLab]** (the web
page). Text in `<angle brackets>` is yours to fill in. No real address is in this repository:
the committed files carry TEST-NET placeholders (`192.0.2.x`). Why it is built this way:
[ADR-001](adr/ADR-001-app-machine-plain-http.md). How other apps call it:
[USER_MANUAL.md](USER_MANUAL.md).

| Machine | Runs |
| --- | --- |
| App machine | The five services (collectors, builder, requester, planner, contracts) and, when enabled, Langfuse |
| Box (`<box address>`) | The PostgreSQL, ClickHouse and MongoDB databases the collector reads |
| vLLM machine (`<vLLM machine address>`) | The model behind questions and the relationship review |
| Laptop | Nothing of the stack. You call the API with curl. |

## 1. Prerequisites

<!-- verified: 24eb0b6 -->

1. **[app machine]** Linux with Docker Engine, the Compose plugin 2.24 or later, git and
   python3. Check: `docker compose version` and `python3 --version` print versions.
2. **[app machine]** At least **8 GiB of memory and 40 GiB of free disk** for db-catalogue
   and normalization together. Check: `free -g` and `df -h /`. The measured parts:

   | Part | Laptop rehearsal, 2026-11-22 |
   | --- | --- |
   | The five services | 33–84 MiB each; `DBIG_MEM_LIMIT` caps each at 1 GiB |
   | Langfuse, in use | 2.3 GiB in total |
   | Langfuse volumes after the first start | 151 MiB |
   | The five images | 208–351 MB each, about 1.3 GB |

3. **[app machine]** Write down its private LAN address and network, for example
   `10.10.9.50` and `10.10.9.0/24`. This guide calls the address `<app-machine address>`.
4. **[vLLM machine]** vLLM serves one model behind a bearer key on a private address
   (`docs/LOCAL_RUN.md` step 9, bound to the LAN instead of 127.0.0.1). Write down its URL,
   the model name, the model's digest (the revision you pinned) and the key.

## 2. Let the app machine reach the databases and vLLM

<!-- verified: 24eb0b6 -->

1. **[box]** Allow `<app-machine address>` on the port of every database the catalogue will
   read, in the box's firewall (`bench-docker-firewall.service`), then restart that service.
   normalization's guide, step 2, changes the same service; add only the ports it lacks.
2. **[vLLM machine]** Allow `<app-machine address>` on the vLLM port (8000 unless you changed
   it), nothing else.
3. Check, **[app machine]**: `nc -zv <box address> <port>` connects for each database, and
   `curl -s -H "Authorization: Bearer <vLLM key>" http://<vLLM machine address>:8000/v1/models`
   lists the model.

## 3. Get the code

<!-- verified: 24eb0b6 -->

1. **[GitLab]** The project, Settings, Repository, Deploy tokens: create a token with
   `read_repository` (add `read_registry` if you will pull release images, step 13).
2. **[app machine]** Clone into your home folder; git asks for the token user and the token:

   ```bash
   git clone <repository URL> ~/db-catalogue
   ```

3. Check: `ls ~/db-catalogue/deploy` lists `compose.yaml`, `.env.example`,
   `sources.example.toml` and `config`.

From here on every **[app machine]** command runs in `~/db-catalogue/deploy`.

## 4. Fill deploy/.env

<!-- verified: 24eb0b6 -->

`deploy/.env` holds every plain setting. Each line of `.env.example` says what it is.

1. **[app machine]** `cp .env.example .env && chmod 600 .env`
2. Replace every `192.0.2.x` placeholder: `LAN_IP` (the app machine's address), `LAN_CIDR`
   (its network), `VLLM_BASE_URL` (`http://<vLLM machine address>:8000/v1`), `LANGFUSE_URL`
   (`http://<app-machine address>:3000`) and `DBIG_EGRESS` (the database and vLLM
   `address:port` pairs; it is only read by the unused firewall, owner decision 2).
3. Set `VLLM_MODEL` to the name `/v1/models` listed in step 2, and `VLLM_MODEL_DIGEST` to the
   model's digest. Change both together whenever the model changes.
4. Keep `DBIG_ALLOW_PLAIN_LAN=1`. It allows plain HTTP for the services and the model on the
   LAN, and plain connections to databases marked `tls = false` (owner decisions of
   2026-11-21 and 2026-11-22; review date 2027-05-21). A public address stays refused.
5. Generate the Langfuse values now, even if you start Langfuse later. The four passwords
   and the encryption key: `openssl rand -hex 24` each (they sit inside a database URL, where
   base64 characters break it). `LANGFUSE_NEXTAUTH_SECRET` and `LANGFUSE_SALT`:
   `openssl rand -base64 32` each.
6. Check: `grep -c '192\.0\.2' .env` prints `0`, and `grep -c '<' .env` prints `0`.

## 5. List the databases

<!-- verified: 24eb0b6 -->

One collector reads any number of databases of the three engines, each with any number of
namespaces (owner 2026-11-22, answer B1). They are listed in `deploy/sources.toml`.

1. **[box]** Create a read-only account on each database (`docs/LOCAL_RUN.md` step 2:
   `dbig_reader`; on ClickHouse with `readonly = 2`).
2. **[app machine]** `cp sources.example.toml sources.toml && chmod 644 sources.toml`. Then
   write one `[[source]]` table per database:

   ```toml
   [[source]]
   source_id = "shop-pg"          # your name for it; letters, digits, - and _
   kind = "postgresql"            # postgresql, clickhouse or mongodb
   connection_file = "/run/secrets/sources/shop-pg.json"
   namespaces = ["public", "sales"]   # PostgreSQL schemas, ClickHouse databases, MongoDB databases
   ```

   The `[defaults]` table at the top applies to every source; leave it as it is.
3. **[app machine]** Write each login into its own file named in `connection_file`, under
   `deploy/secrets/sources/`. Type the password into the file in an editor, not on the
   command line, so it stays out of your shell history:

   | kind | The file holds |
   | --- | --- |
   | `postgresql` | `{"host": "<box address>", "port": 5432, "database": "<db>", "user": "dbig_reader", "password": "<password>"}` |
   | `clickhouse` | `{"host": "<box address>", "port": 8123, "username": "dbig_reader", "password": "<password>"}` |
   | `mongodb` | `{"uri": "mongodb://dbig_reader:<password>@<box address>:27017/?authSource=admin"}` |

4. TLS per database: `tls = false` (the default in `[defaults]`) sends the login unencrypted
   over the LAN (owner 2026-11-22, answer A1). For a database that has TLS, add
   `tls = true` and `tls_ca_file = "/run/secrets/source_ca_bundle"` to its own table, and put
   its CA in `secrets/source-ca-bundle.pem` (step 6).
5. Check: `python3 -c 'import json,sys; [json.load(open(f)) for f in sys.argv[1:]]'
   secrets/sources/*.json` prints nothing, and each `connection_file` names one of those files.

## 6. Create the secrets

<!-- verified: 24eb0b6 -->

Every key is a file under `deploy/secrets/`, never a line in `.env`.

1. **[app machine]** Create the service keys:

   ```bash
   cd ~/db-catalogue/deploy/secrets
   python3 -c 'import secrets; print(secrets.token_urlsafe(48))' > scan-signing.key
   python3 -c 'import secrets; print(secrets.token_urlsafe(48))' > token.key
   python3 -c 'import secrets; print(secrets.token_urlsafe(32))' > requester-collector-token
   touch source-ca-bundle.pem langfuse-public.key langfuse-secret.key
   ```

   `source-ca-bundle.pem` stays empty unless a database uses TLS. The two Langfuse key files
   stay empty until step 11; while they are empty, tracing is off and nothing else changes.
2. **[app machine]** Put the vLLM key into `model.key` with an editor (one line, the key only).
3. **[app machine]** Create the five caller-key files, still in `deploy/secrets/`. This makes
   one `ops` key that may call every route, and lists the token of the requester as a caller of the
   collector, which every scan needs:

   ```bash
   python3 - <<'EOF'
   import hashlib, json, secrets
   UNTIL = "2027-11-22T00:00:00+00:00"  # when these keys stop working; pick your own
   def sha(text): return hashlib.sha256(text.encode()).hexdigest()
   key = secrets.token_urlsafe(32)
   ops = {"caller": "ops", "sha256": sha(key), "scopes": ["*"], "expires_at": UNTIL}
   token = open("requester-collector-token").read().strip()
   requester = {"caller": "requester", "sha256": sha(token), "scopes": ["scan"], "expires_at": UNTIL}
   for service in ["collectors", "builder", "requester", "planner", "contracts"]:
       keys = [ops, requester] if service == "collectors" else [ops]
       with open(f"caller-keys-{service}.json", "w") as out:
           json.dump({"keys": keys}, out, indent=2)
   print("ops key, shown once; keep it in your password manager:", key)
   EOF
   ```

   Keys for other apps come later, one per app with only the scopes it needs
   ([USER_MANUAL.md](USER_MANUAL.md), section 3).
4. **[app machine]** The services run as user 10001 and must read these files; nobody else
   should:

   ```bash
   cd ~/db-catalogue/deploy
   sudo chown -R 10001:10001 secrets && sudo chmod -R u=rX,go= secrets
   ```

   From now on, edit a file under `secrets/` with `sudo`.
5. Check: `sudo ls secrets secrets/sources` lists `caller-keys-builder.json`,
   `caller-keys-collectors.json`, `caller-keys-contracts.json`, `caller-keys-planner.json`,
   `caller-keys-requester.json`, `langfuse-public.key`, `langfuse-secret.key`, `model.key`,
   `requester-collector-token`, `scan-signing.key`, `source-ca-bundle.pem`, `token.key` and
   your login files. Then, in `deploy/`, `docker compose --env-file .env config --quiet` prints
   nothing; it checks that every required setting of `.env` is set, not the secret files.

## 7. Build and start

<!-- verified: 24eb0b6 -->

1. **[app machine]** Build the five images from the clone (a few minutes the first time),
   then start them:

   ```bash
   docker compose --env-file .env build
   docker compose --env-file .env up -d --wait
   ```

2. Check: `docker compose ps` lists `builder`, `collectors`, `contracts`, `planner` and
   `requester`, each `healthy`.

Langfuse is a separate Compose project (step 11). Never add `-f compose.langfuse.yaml` to these
commands: Compose would merge the two project names and start the five services a second time.

## 8. Check the services

<!-- verified: 24eb0b6 -->

**[laptop]**, with the `ops` key of step 6:

```bash
export APP=http://<app-machine address>
export KEY=<ops key>
for p in 8441 8442 8443 8444 8445; do
  curl -s -o /dev/null -w "%{http_code} " $APP:$p/healthz
done; echo
curl -s -o /dev/null -w '%{http_code}\n' $APP:8443/openapi.json
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $KEY" $APP:8443/openapi.json
```

Check: five `200`, then `401` (no key), then `200` (with the key). The ports are the defaults;
use yours if you changed `*_PORT` in `.env`.

## 9. First data

<!-- verified: 24eb0b6 -->

The stack starts empty (owner decision 17). Do this once now, and again whenever you want a
fresh catalogue. Measured end to end on the laptop on 2026-11-22.

1. **[laptop]** Ask for a scan of each database in `sources.toml`, one request each. `id` and
   `idempotency_key` must be new for every request; `issued_at` and `expires_at` are UTC
   times, for example `date -u +%Y-%m-%dT%H:%M:%SZ`; `requester_id` stays `catalogue`:

   ```bash
   curl -s -X POST $APP:8443/v1/scan-requests -H "Authorization: Bearer $KEY" \
     -H 'Content-Type: application/json' -d '{"request": {"id": "shop-pg-1",
       "source_id": "shop-pg", "scopes": ["public", "sales"], "capabilities": ["metadata"],
       "reason": "first collect", "requester_id": "catalogue", "idempotency_key": "shop-pg-1",
       "issued_at": "<now>", "expires_at": "<one hour later>",
       "reply_location": "local-receipt"}, "adapter": "http"}'
   ```

   The answer is `"status": "queued"`.
2. **[laptop]** Ask the collector until the scan is `complete` (a few seconds for a small
   database). The status route of the requester keeps the first `queued` receipt, so use this:

   ```bash
   curl -s -H "Authorization: Bearer $KEY" $APP:8441/v1/collector/scan-requests/shop-pg-1
   ```

   `"status": "failed"` comes with an `error` that names the login file or the setting to fix.
3. **[laptop]** Build one snapshot from all the finished scans, named by their request IDs
   (not by their `bundle_path`):

   ```bash
   curl -s -X POST $APP:8442/v1/builder/assemblies -H "Authorization: Bearer $KEY" \
     -H 'Content-Type: application/json' -d '{"bundles": ["shop-pg-1", "<other request id>"]}'
   ```

   The answer holds the job `id`. Repeat
   `curl -s -H "Authorization: Bearer $KEY" $APP:8442/v1/builder/assemblies/<id>` until
   `"status": "complete"`.
4. **[laptop]** Reindex the requester and rebuild the planner's graph, so search, questions
   and join paths read the new snapshot. Until you do, they keep answering from the previous one:

   ```bash
   curl -s -X POST -H "Authorization: Bearer $KEY" $APP:8443/v1/catalogue/reindex
   curl -s -X POST -H "Authorization: Bearer $KEY" $APP:8444/v1/planner/graph
   ```

   Both answer the same `snapshot_digest`.

5. Check: `curl -s -H "Authorization: Bearer $KEY" "$APP:8443/v1/catalogue/search?query=<a table name>"`
   lists that table, and a question answers with citations (10–60 s):

   ```bash
   curl -s -X POST $APP:8443/v1/catalogue/questions -H "Authorization: Bearer $KEY" \
     -H 'Content-Type: application/json' -d '{"question": "Which table holds <something>?"}'
   ```

## 10. Give other apps their keys

<!-- verified: 24eb0b6 -->

For each app that will call the catalogue, follow [USER_MANUAL.md](USER_MANUAL.md), section 3:
the app makes its own key and sends you only its hash; you add one entry to the key file of each
service it calls and recreate that service.

## 11. Enable Langfuse (optional)

<!-- verified: 24eb0b6 -->

1. **[app machine]** Start it as its own project (the first start runs migrations for a few
   minutes):

   ```bash
   docker compose -f compose.langfuse.yaml --env-file .env up -d
   ```

   Check: `curl -s -o /dev/null -w '%{http_code}\n' http://<app-machine address>:3000` prints
   `200`.
2. **[laptop]** Open `http://<app-machine address>:3000`, sign up, create an organization and
   one project, and copy the project's public and secret keys.
3. **[app machine]** Put the two keys into `secrets/langfuse-public.key` and
   `secrets/langfuse-secret.key` with `sudo` and an editor, then recreate the two services that
   send traces:

   ```bash
   docker compose --env-file .env up -d --force-recreate builder requester
   ```

   Check: after the next question, the project shows a trace. Traces hold metadata only, never
   prompts, replies, SQL or values. If Langfuse is down, the services keep working without it.

## 12. Daily work

<!-- verified: 24eb0b6 -->

All commands run **[app machine]** in `~/db-catalogue/deploy`.

| Task | Do |
| --- | --- |
| Change a setting | Edit `.env`, then `docker compose --env-file .env up -d` |
| Change the model | Set `VLLM_MODEL` and `VLLM_MODEL_DIGEST` (and `model.key` if the key changed), then `docker compose --env-file .env up -d --force-recreate builder requester` |
| Add, change or remove a database | Edit `sources.toml` and its login file (step 5), then `docker compose --env-file .env up -d --force-recreate collectors requester`, then step 9 |
| Add or remove a caller key | Edit `secrets/caller-keys-<service>.json` with `sudo`, then `docker compose --env-file .env up -d --force-recreate <service>` |
| Refresh the catalogue | Step 9 |
| Update the code | `git pull`, then `docker compose --env-file .env up -d --build --wait` |
| Update to a release | Step 13 |
| See what a service does | `docker compose logs -f <service>` |
| Stop everything | `docker compose down` (the data volumes stay) |
| Back up | `.env`, `sources.toml` and `secrets/` cannot be rebuilt: copy them somewhere safe. The catalogue itself is rebuilt by step 9. |

## 13. Releases

<!-- verified: 24eb0b6 -->

Tag `v0.1.0` only after the app machine has passed steps 7 to 9 (owner decision 7).

1. **[laptop]**, in the repository on `main`, check that the tag equals every package version:
   `python3 scripts/check_release_version.py v0.1.0`. A later release changes every version,
   commits, then tags.
2. **[laptop]** `git tag -a v0.1.0 -m "v0.1.0"`, then `git push origin v0.1.0`. `origin`
   pushes to GitLab and GitHub both.
3. **[GitHub]** Actions, "Release package": wait for green. **[GitLab]** CI/CD, Pipelines, the
   tag's pipeline: wait for the release stage. Images are tagged with the version without the
   `v` (`0.1.0`): on GitHub `ghcr.io/<github user>/db-catalogue/dbig-*`, on GitLab under
   Deploy, Container Registry. Wheels are in GitLab's Deploy, Package Registry.
4. **[app machine]** To run release images instead of building: log in once
   (`docker login ghcr.io -u <github user>` with a token that has `read:packages`, or
   `docker login <GitLab registry address>` with the deploy token of step 3), set
   `DBIG_REGISTRY` in `.env` to the registry path ending in `/`, for example
   `ghcr.io/<github user>/db-catalogue/`, and `DBIG_VERSION=0.1.0`, then:

   ```bash
   docker compose --env-file .env pull
   docker compose --env-file .env up -d --wait
   ```

   Roll back: set the old `DBIG_VERSION` and run the same two commands. With `DBIG_REGISTRY`
   empty, `pull` has nothing to fetch; build instead (step 7).

## When something goes wrong

<!-- verified: 24eb0b6 -->

Measured on the laptop rehearsal of 2026-11-22.

| You see | Cause and fix |
| --- | --- |
| A service exits at start with `PermissionError` or "cannot be read" on `/run/secrets/...` | The file is not readable by user 10001. Repeat step 6.4. |
| Scan request answers `502` `COLLECTOR_REFUSED_401` | The token of the requester is not in `caller-keys-collectors.json`. Repeat step 6.3, or add its entry, and recreate `collectors`. |
| Scan request answers `403` `UNAUTHORIZED_SCOPE` | The `source_id` or a namespace is not in `sources.toml`. Fix the request or the file. |
| Scan request answers `502` `UNAUTHORIZED_REQUESTER_OR_SOURCE` | `requester_id` is not `catalogue`, or the source is missing from the collector's file. |
| Scan `failed`, error "could not reach the server" | The host or port in the named login file is wrong, or the box's firewall blocks the app machine (step 2). |
| Assembly answers `404` `BUNDLE_NOT_FOUND` | Name the bundles by their scan request IDs, not by `bundle_path`. |
| Search or join paths find nothing new after a scan | You did not reindex and rebuild the graph (step 9.4). |
| `401` `UNAUTHORIZED` / `403` `FORBIDDEN_SCOPE` | A wrong or expired key / a key without that route's scope. |
| Question answers `503` `MODEL_TIMEOUT` | The model took longer than 60 s or is down. Check vLLM; `timeout_seconds` is in `config/requester.toml`. |
| `langfuse-web` restarts with Prisma `P1013` | A Langfuse password is not hex. Regenerate it with `openssl rand -hex 24`. |
| `langfuse-web` restarts with Prisma `P1000` | A password changed in `.env` after the first start. Set it back, or run `ALTER USER` in `langfuse-postgres`. |
| "Pool overlaps with other one on this address space" | `-f compose.langfuse.yaml` was added to a stack command (step 7), or `DBIG_SUBNET` clashes with another Docker network: pick a free `172.x.0.0/24`. |

## Not checked

- A real app machine: every number above is from the laptop.
- A scan of the box's databases: the laptop run used a throwaway PostgreSQL with a small schema.
- The tag-triggered release pipelines on both remotes.
- The LAN firewall files (`deploy/lan_firewall.py`, `deploy/dbig-firewall.service`), kept but
  not used for now (owner decision 2).
