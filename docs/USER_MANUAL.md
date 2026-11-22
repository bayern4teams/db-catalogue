# User manual

For anyone who uses the catalogue from another app on the LAN and has never seen it: how you get
a caller key, what each service answers, and what changes day to day. The app has **no screens**:
everything is the HTTP API. Setting up the machine is another document:
[DEPLOY.md](DEPLOY.md). Every route and field: [API.md](API.md).

Text in `<angle brackets>` is yours to fill in. `<app-machine address>` is the address of the
app machine, for example `10.10.9.50`; ask the person who runs it.

## 1. What the app does

<!-- verified: feat/app-machine, 2026-11-22 -->

The app keeps a **catalogue** of your PostgreSQL, ClickHouse and MongoDB databases: their tables,
fields, types and **proposed** relationships. It never reads row values. Another app on the LAN
asks it questions through plain HTTP: "which table holds invoices," "what does this column feed,"
"is this record valid for schema X." A private model answers questions with citations.

| Service | Port (default) | What it is for | Scopes its routes need |
| --- | --- | --- | --- |
| collectors | 8441 (`COLLECTOR_PORT`) | Runs next to each database; re-scans on request | `scan` |
| catalog-builder | 8442 (`BUILDER_PORT`) | Joins scan bundles into one catalogue snapshot | `assemble` |
| requester | 8443 (`REQUESTER_PORT`) | Searches the catalogue, answers questions, reindexes | `search`, `ask`, `scan`, `reindex` |
| planner | 8444 (`PLANNER_PORT`) | Join paths, lineage, reviewable read plans | `plan`, `graph` |
| contracts | 8445 (`CONTRACTS_PORT`) | Validates a record against a stored schema | `validate` |

Plain HTTP on the LAN only (owner decision 2026-11-21, C5 review 2027-05-21). The services refuse
a public bind address. `GET /healthz` needs no key; **every other route, including
`/openapi.json`, answers 401 without one.**

## 2. Before you start

1. **Be on the private network.** The services listen on the app machine's LAN address only; a
   caller outside that network cannot reach the ports at all.
2. **Ask the person who runs the app for a caller key.** What they do, and what you get, is
   section 3. One key per calling app, with only the scopes that app needs.
3. **Keep the key text secret.** It is a bearer token: anyone who has it can use your scopes. It
   is not stored on the server—only its SHA-256 hash is—so there is no way to show it again;
   lose it and you get a new one.
4. **Keys expire.** Each entry carries an `expires_at`; an expired key gives 401 like a wrong one.

## 3. Getting a caller key (worked procedure)

The person who runs the app adds one entry to the key file of each service you will call —
`deploy/secrets/caller-keys-<service>.json` on the app machine, git-ignored. Nothing else changes;
there is no database of keys.

1. **[laptop] Make a key.** The caller generates it themselves and sends only the *hash* to be
   listed; the key text stays with the caller:

   ```bash
   KEY=*** rand -hex 32)
   echo -n "$KEY" | sha256sum        # send this hash, keep $KEY secret
   ```

2. **[app-machine] List the hash.** Append an entry to the key file of each service. The file is
   JSON: a `keys` array, each entry with `caller` (a name for the calling app), `sha256` (the 64
   hex characters of step 1), `scopes` (from the table in section 1) and `expires_at` (with a
   time zone):

   ```json
   {
     "keys": [
       {
         "caller": "billing-app",
         "sha256": "<64 hex characters>",
         "scopes": ["search", "ask"],
         "expires_at": "2027-06-01T00:00:00+00:00"
       }
     ]
   }
   ```

   Rotating a key: list the new hash beside the old one for the same `caller`, switch the caller,
   then remove the old entry.

3. **[app-machine] Restart that service** so it re-reads the file:
   `docker compose up -d --force-recreate requester` (the service's own name).
4. **[laptop] Prove it.** Send the key as a bearer header; a scope test matters too—a
   builder-scoped key used on the `search` route gives `401`, measured:

   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' \
     -H "Authorization: Bearer $KEY" http://<app-machine address>:8443/openapi.json
   ```

   Pass: `200` with the scopes you asked for.

## 4. The API, one call per service

```bash
export APP=http://<app-machine address>
export KEY=<your key text>
```

Every call carries `-H "Authorization: Bearer $KEY"`. JSON in and out. The body fields are in
[API.md](API.md); below is one worked call per service with the real response shape.

### 4.1 Requester: search the catalogue (`search`)

```bash
curl -s -X POST $APP:8443/v1/catalogue/search -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' -d '{"query": "<word>", "limit": 5}'
```

The answer holds `results`, one entry per hit with `id`, `kind`, `name` and `object_id`. Use the
`id` as `from_id`/`to_id` in planner calls.

### 4.2 Requester: ask a question (`ask`)

```bash
curl -s -X POST $APP:8443/v1/catalogue/questions -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' -d '{"question": "<your question>"}'
```

The answer holds `answer` (the model's text—not a catalogue fact), `citations` (the retrieved
ids it cites) and `relationships` (the only catalogue facts about links, written by code from
stored edges). **The reasoning model takes 12 to 60 s**; the requester itself gives up at
`timeout_seconds = 60` (`deploy/config/requester.toml`) with 503 `MODEL_TIMEOUT`. A question
containing *lineage*, *derived from*, *flows into* or *writes to* is refused
`LINEAGE_REQUIRES_PLANNER`—those questions belong to the planner.

### 4.3 Requester: ask for a fresh scan (`scan`)

`POST $APP:8443/v1/scan-requests` with a `ScanRequest` and `"adapter": "http"`; then poll
`GET /v1/scan-requests/<request_id>` until `status` is `complete`. See [API.md](API.md) for the
request body; the collector side is section 4.6.

### 4.4 Builder: rebuild the snapshot (`assemble`)

```bash
curl -s -X POST $APP:8442/v1/builder/assemblies -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' -d '{"bundles": ["<bundle path>", "..."]}'
```

The answer is 202 with a `job_id`; poll `GET /v1/builder/assemblies/<job_id>` for its status.
After a finished assembly the requester still serves the old index until you reindex
(section 4.1 of [API.md](API.md) or section 5, "rebuild catalogue").

### 4.5 Planner: the graph (`graph`), and plans (`plan`)

```bash
curl -s -X POST $APP:8444/v1/planner/graph -H "Authorization: Bearer $KEY"
```

The answer holds `snapshot_digest`—the version of the catalogue the planner is reading; compare
it with what the requester reports when the two seem to disagree. `POST /v1/join-paths` and
`POST /v1/lineage/paths` take `from_id` and `to_id` (the ids from 4.1); `POST /v1/plans` turns a
`path_id` into a reviewable SQL or MongoDB read. The planner **never runs** what it writes.

### 4.6 Contracts: validate a record (`validate`)

```bash
curl -s -X POST $APP:8445/v1/contracts/validate/field -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' -d '{ ...a field record... }'
```

The answer holds `valid: true` for a good record. An unknown record type is refused with
`UNKNOWN_SCHEMA`; the list of names is `GET /v1/contracts/schemas`.

### 4.7 Collectors: scan directly (`scan`)

`POST $APP:8441/v1/collector/scan-requests` takes a **signed** scan request (`request`, `key_id`,
`signature`)—signing uses `deploy/secrets/scan-signing.key` and is for the requester or an
operator, not an ordinary caller. Prefer 4.3.

### 4.8 Errors

Bare codes in the body: `NOT_FOUND` (no such id or job), `UNKNOWN_SCHEMA` (contracts),
`MODEL_TIMEOUT` (503, the model is slow or down), `STALE_INDEX` (the index is older than the
snapshot). `401` covers a missing, wrong, expired or out-of-scope key—the service does not say
which, so check the scopes first.

## 5. Daily work

| You want to | Do (where) |
| --- | --- |
| Add a caller key | [app-machine] Append the hash entry to the service's `deploy/secrets/caller-keys-<service>.json`, restart that service |
| Change a setting | [app-machine] Edit `deploy/.env`, then `docker compose up -d` |
| Change the model | [app-machine] Update `VLLM_MODEL` and `VLLM_MODEL_DIGEST` in `deploy/.env` (the digest must change with the model), `docker compose up -d` |
| Rebuild the catalogue | [app-machine/laptop] Collect (or `POST /v1/scan-requests`, 4.3), then `POST /v1/builder/assemblies` (4.4), then `POST /v1/catalogue/reindex` |
| Update the images | [app-machine] Pull from `DBIG_REGISTRY` at `DBIG_VERSION`, or build from a clone (see [DEPLOY.md](DEPLOY.md)) |

## 6. When something goes wrong

<!-- verified: measured on the branch, 2026-11-22 -->

| You see | Cause | Fix |
| --- | --- | --- |
| `401` even on `/openapi.json` | No key, wrong key, expired key, or the key lacks that route's scope. | Check the header is `Authorization: Bearer <key text>`; ask for the scope; check `expires_at`. |
| `503` with `MODEL_TIMEOUT` | The model is slow or down. Answers took 12 to 60 s in measurement. | Retry; ask the operator to check vLLM, or raise `timeout_seconds` in `deploy/config/requester.toml`. |
| `STALE_INDEX` | The search index of the requester is older than the catalogue snapshot. | Reindex: `dbig-requester index` on the app machine or `POST /v1/catalogue/reindex`. |
| A question about lineage is refused `LINEAGE_REQUIRES_PLANNER` | By design: link questions go to the planner. | Use `POST /v1/lineage/paths` or `/v1/join-paths` (4.5). |
| A service refuses to start, message about the bind address | `LAN_IP` is not a private address, or the flag is missing. | `LAN_IP` must be the private LAN address; `DBIG_ALLOW_PLAIN_LAN=1` in `deploy/.env` allows a private bind with plain HTTP, nothing public. |
| Langfuse web crash-loops with `P1013` or `P1000` | A `LANGFUSE_*` password is not hex: the passwords sit inside `DATABASE_URL` and base64 breaks Prisma. | Set them with `openssl rand -hex 24`; after rotating a password also run `ALTER USER` in the Langfuse Postgres container. |

## 7. Limits you should know

- No screens. The catalogue web page of earlier releases is not part of this deployment; the API
  is the only door.
- Plain HTTP only, by decision, on the LAN. A key crosses the wire in clear text: trust the
  network, and keep the key file out of it.
- Relationships are proposals. A proposal stays a guess until it is confirmed; the answer text of
  4.2 is the model's own words, and only `relationships` entries are catalogue facts.
- The planner never executes what it writes; a human runs the SQL or read, or a staging target is
  configured for cross-source plans.
- Scans are signed requests; an ordinary caller asks through the requester (4.3), not the
  collector (4.7).
- Keys are files on the app machine. There is no key UI, no self-service, no audit log of calls.

Not checked by this manual's author: a live collect against the bench databases (their TLS is
off; the owner adds the logins); answer quality on a fresh collect; the LAN firewall (kept in the
repo but unused for now, owner decision 2).
