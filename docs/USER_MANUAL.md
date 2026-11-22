# User manual

For anyone who uses the catalogue from another app on the LAN and has never seen it: how you get
a caller key, what each service answers, and what to do when a call fails. The app has **no
screens**: everything is the HTTP API. Installing it is another document:
[DEPLOY.md](DEPLOY.md). Every route and field: [API.md](API.md).

Text in `<angle brackets>` is yours to fill in. `<app-machine address>` is the address of the
app machine, for example `10.10.9.50`; ask the person who runs it. Every call and answer below
was measured against the running services on 2026-11-22 (laptop rehearsal).

## 1. What the app does

<!-- verified: 24eb0b6 -->

The app keeps a **catalogue** of your PostgreSQL, ClickHouse and MongoDB databases: their tables,
fields, types and **proposed** relationships. It reads structure, never row values. Another app
on the LAN asks it questions over plain HTTP: "which table holds invoices," "how do these two
tables join," "is this record valid." A private model answers questions in words, with citations.

| Service | Port (default) | What it is for | Scopes its routes need |
| --- | --- | --- | --- |
| collectors | 8441 (`COLLECTOR_PORT`) | Reads every database the operator listed, when a scan is requested | `scan` |
| catalog-builder | 8442 (`BUILDER_PORT`) | Joins finished scans into one catalogue snapshot | `assemble` |
| requester | 8443 (`REQUESTER_PORT`) | Searches the catalogue, answers questions, asks for scans, reindexes | `search`, `ask`, `scan`, `reindex` |
| planner | 8444 (`PLANNER_PORT`) | Join paths, lineage, reviewable read plans | `plan`, `graph` |
| contracts | 8445 (`CONTRACTS_PORT`) | Validates a record against the catalogue's schemas | `validate` |

Plain HTTP on the LAN only (owner decision 2026-11-21, review date 2027-05-21). `GET /healthz`
needs no key; **every other route, `/openapi.json` included, needs a caller key.**

## 2. Before you start

<!-- verified: 24eb0b6 -->

1. **Be on the private network.** The services listen on the app machine's LAN address only.
2. **Get a caller key** for your app (section 3): one key per calling app, with only the scopes
   it needs, listed on every service it calls.
3. **Keep the key text secret.** It is a bearer token: anyone who has it can use your scopes.
   The server stores only its SHA-256 hash, so it cannot show the key again; lose it and make a
   new one.
4. **Keys expire.** Each entry carries an `expires_at`; an expired key gets `401`.

## 3. Getting a caller key

<!-- verified: 24eb0b6 -->

1. **[your machine]** Make the key and its hash. Keep the key; send only the hash, the scopes
   you need and the services you will call to the person who runs the app:

   ```bash
   KEY=$(openssl rand -hex 32)
   printf '%s' "$KEY" | sha256sum      # send the 64 hex characters, keep $KEY secret
   ```

2. **[app machine]** The person who runs the app adds one entry to the key file of each service
   you will call, `deploy/secrets/caller-keys-<service>.json` (git-ignored, edited with `sudo`):

   ```json
   {
     "caller": "billing-app",
     "sha256": "<64 hex characters>",
     "scopes": ["search", "ask"],
     "expires_at": "2027-06-01T00:00:00+00:00"
   }
   ```

   The entry goes into the file's `keys` list, next to the others. To rotate a key, list the new
   hash beside the old one for the same `caller`, switch the app over, then remove the old entry.
3. **[app machine]** Recreate each changed service so it reads the file again, in
   `~/db-catalogue/deploy`: `docker compose --env-file .env up -d --force-recreate requester`
   (the service's own name).
4. **[your machine]** Check, for each service:

   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' \
     -H "Authorization: Bearer $KEY" http://<app-machine address>:8443/openapi.json
   ```

   `200` means the key works there. `401` means the key is not listed on that service (or is
   expired or mistyped).

## 4. The API, one call per service

<!-- verified: 24eb0b6 -->

```bash
export APP=http://<app-machine address>
export KEY=<your key text>
```

Every call carries `-H "Authorization: Bearer $KEY"`; JSON in and out. Field lists are in
[API.md](API.md).

### 4.1 Requester: search (`search`)

```bash
curl -s "$APP:8443/v1/catalogue/search?query=orders" -H "Authorization: Bearer $KEY"
curl -s -X POST $APP:8443/v1/catalogue/search -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' -d '{"query": "orders", "limit": 5}'
```

The answer holds `snapshot_digest` (which catalogue version answered), `completeness` and
`results`. Each result has `id`, `kind` (`table`, `field`, …), `name`, `namespace`,
`source_id`, `object_id`, `data_type` and `description`. A table's `id` (`object:…`) is what the
planner takes in 4.5.

### 4.2 Requester: ask a question (`ask`)

```bash
curl -s -X POST $APP:8443/v1/catalogue/questions -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' -d '{"question": "Which table holds customer orders?"}'
```

The answer holds `answer` (the model's words, not a catalogue fact), `citations` (the catalogue
ids it used), `relationships` (the only catalogue facts about links, written by code from stored
edges), `model_digest` and `snapshot_digest`. **An answer takes about 10 to 60 seconds**; after
60 seconds the requester gives up with `503` `MODEL_TIMEOUT`. A question about *lineage*,
*derived from*, *flows into* or *writes to* is refused with `LINEAGE_REQUIRES_PLANNER`: use 4.5.

### 4.3 Requester: ask for a fresh scan (`scan`)

For the operator or an app that refreshes the catalogue. `source_id` and the namespaces must be
in the operator's database list; `id` and `idempotency_key` must be new for every request;
times are UTC:

```bash
curl -s -X POST $APP:8443/v1/scan-requests -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' -d '{"request": {"id": "shop-pg-2",
    "source_id": "shop-pg", "scopes": ["public", "sales"], "capabilities": ["metadata"],
    "reason": "refresh", "requester_id": "catalogue", "idempotency_key": "shop-pg-2",
    "issued_at": "<now>", "expires_at": "<one hour later>",
    "reply_location": "local-receipt"}, "adapter": "http"}'
```

The answer is `"status": "queued"`. Follow the scan on the collector, with a key that has `scan`
there: `curl -s -H "Authorization: Bearer $KEY" $APP:8441/v1/collector/scan-requests/shop-pg-2`
until `status` is `complete` or `failed` (a failed scan names the cause in `error`). The
requester route `GET /v1/scan-requests/<id>` keeps the first `queued` receipt and does not
follow the scan.

### 4.4 Builder: build a snapshot (`assemble`)

Name the finished scans by their request ids:

```bash
curl -s -X POST $APP:8442/v1/builder/assemblies -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' -d '{"bundles": ["shop-pg-2"]}'
```

The answer holds the job `id`; repeat `GET $APP:8442/v1/builder/assemblies/<id>` until `status`
is `complete`. Then reindex the requester (`reindex`) and rebuild the planner's graph
(`graph`), or search, questions and join paths keep answering from the previous snapshot:

```bash
curl -s -X POST -H "Authorization: Bearer $KEY" $APP:8443/v1/catalogue/reindex
curl -s -X POST -H "Authorization: Bearer $KEY" $APP:8444/v1/planner/graph
```

Both answer the new `snapshot_digest`; searches and paths carry it from then on.

### 4.5 Planner: join paths and read plans (`plan`), the graph (`graph`)

```bash
curl -s -X POST $APP:8444/v1/join-paths -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' -d '{"from_id": "<object id>", "to_id": "<object id>"}'
```

The answer has `status` (`found` or not) and `paths`; each path has a `path_id`, the
`object_ids` and `edge_ids` it crosses and its own `status` (for example `declared` for a
foreign key). `POST /v1/lineage/paths` takes the same body for data-flow paths. Turn a path into
a read you can review:

```bash
curl -s -X POST $APP:8444/v1/plans -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' -d '{"path_id": "<path_id>", "max_rows": 10}'
```

The plan has `status: "ready_for_review"` and `steps`, each with its `sql` (or MongoDB read).
The planner **never runs** what it writes; a person reviews and runs it.
`POST $APP:8444/v1/planner/graph` rebuilds the planner's graph from the newest snapshot (4.4).

### 4.6 Contracts: validate a record (`validate`)

```bash
curl -s $APP:8445/v1/contracts/schemas -H "Authorization: Bearer $KEY"
curl -s -X POST $APP:8445/v1/contracts/validate/field -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' -d '<one field record as JSON>'
```

The first call lists the record types (`field`, `object`, `edge`, …). A good record answers
`{"valid": true}`; a bad one answers `422` `INVALID_RECORD` with `problems` naming every
missing or wrong field; an unknown type answers `404` `UNKNOWN_SCHEMA`.

### 4.7 Collectors directly

`POST $APP:8441/v1/collector/scan-requests` takes a **signed** scan request, made with the
deployment's signing key; it is for the requester. Use 4.3 instead. `GET` on the same path with a
request id (4.3) is the way to follow a scan.

## 5. When something goes wrong

<!-- verified: 24eb0b6 -->

An error from the key check has the body `{"error": {"code": …}}`; an error from a route has
`{"detail": {"code": …}}`.

| You see | Cause | Fix |
| --- | --- | --- |
| `401` `UNAUTHORIZED` | No key, a wrong or expired key, or a key not listed on that service. | Check the header is `Authorization: Bearer <key text>` and ask whether the key is listed there (section 3). |
| `403` `FORBIDDEN_SCOPE` | The key is listed but lacks this route's scope. | Ask for the scope (section 3). |
| `503` `MODEL_TIMEOUT` | The model is slow or down. | Retry; ask the operator to check the vLLM machine. |
| Search, questions or join paths miss what a new scan found | The snapshot was built but the requester was not reindexed or the planner graph not rebuilt. | Both calls of 4.4. |
| `STALE_INDEX` | The index was written by an older version of the app. | `POST /v1/catalogue/reindex`. |
| `LINEAGE_REQUIRES_PLANNER` | Link questions belong to the planner. | `POST /v1/lineage/paths` or `/v1/join-paths` (4.5). |
| Scan request `403` `UNAUTHORIZED_SCOPE` | The `source_id` or a namespace is not in the operator's database list. | Use a listed one, or ask the operator to add it. |
| Scan request `502` `COLLECTOR_REFUSED_…` or `COLLECTOR_UNREACHABLE` | The requester could not hand the scan to the collector. | Ask the operator ([DEPLOY.md](DEPLOY.md), "When something goes wrong"). |
| Scan `failed` with "could not reach the server" | The collector cannot reach that database. | Ask the operator to check the login file the error names. |
| Assembly `404` `BUNDLE_NOT_FOUND` | A bundle was named by its path or a wrong id. | Use the scan request ids (4.4). |

## 6. Limits you should know

<!-- verified: 24eb0b6 -->

- No screens: the API is the only door.
- Plain HTTP on the LAN, by decision: a key crosses the network in clear text. Use the app only
  on a network you trust.
- Relationships are proposals until confirmed; the answer text of 4.2 is the model's own words,
  and only `relationships` entries are catalogue facts.
- The planner never runs what it writes.
- Scans are signed; an app asks through the requester (4.3), not the collector.
- Keys are files on the app machine: no key page, no self-service, no log of who called.

Not checked by this manual's author: the box's real databases (the rehearsal used a throwaway
PostgreSQL); answer quality on them; the app machine itself.
