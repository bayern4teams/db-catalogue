# API

Every route and field of the five services, generated from their live OpenAPI
documents by `python scripts/export_api_docs.py`—do not hand-edit. All routes,
including `/openapi.json` and `/docs`, need a caller key in the `Authorization:
Bearer <key>` header (owner decision 16, 2026-11-21); scopes per route are listed
under each service. Replace `<app-machine address>` with the host address; the
ports are the `deploy/.env` settings named in the headings.

## collectors (`COLLECTOR_PORT`, default 8441)

Health: `GET /healthz` (no key).

### `POST /v1/collector/scan-requests`

Post Scan

Request body (`SignedScanRequest`):

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `request` | ScanRequest | yes | — |
| `key_id` | string | yes | — |
| `signature` | string | yes | — |

Response body (`ScanReceipt`):

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `ir_version` | string | no | — |
| `request_id` | string | yes | — |
| `status` | `submitted` / `queued` / `running` / `complete` / `failed` | yes | — |
| `received_at` | string(date-time) | yes | — |
| `bundle_path` | string or null | no | — |
| `error` | string or null | no | — |

Status codes: 200, 422.

### `GET /v1/collector/scan-requests/{request_id}`

Get Scan

| Path/query parameter | Type | Required |
| --- | --- | --- |
| `request_id` | string | yes |

Response body (`ScanReceipt`):

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `ir_version` | string | no | — |
| `request_id` | string | yes | — |
| `status` | `submitted` / `queued` / `running` / `complete` / `failed` | yes | — |
| `received_at` | string(date-time) | yes | — |
| `bundle_path` | string or null | no | — |
| `error` | string or null | no | — |

Status codes: 200, 422.

## catalog-builder (`BUILDER_PORT`, default 8442)

Health: `GET /healthz` (no key).

### `POST /v1/builder/assemblies`

Start

Request body (`AssemblyBody`):

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `bundles` | list[string] | yes | — |

Status codes: 202, 422.

### `GET /v1/builder/assemblies/{job_id}`

Status

| Path/query parameter | Type | Required |
| --- | --- | --- |
| `job_id` | string | yes |

Status codes: 200, 422.

## requester (`REQUESTER_PORT`, default 8443)

Health: `GET /healthz` (no key).

### `POST /v1/catalogue/questions`

Question Route

Request body (`QuestionBody`):

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `question` | string | yes | — |

Response body (`QuestionResponse`):

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `answer` | string | yes | The model's own text about the retrieved metadata. Not a catalogue fact: a link it describes is established only by an entry in relationships. |
| `citations` | list[string] | yes | The retrieved IDs the answer cites. |
| `relationships` | list[object] | yes | The only catalogue facts about links: one entry per cited stored edge, written by code from that edge (names, columns, status, metadata-only evidence). |

Status codes: 200, 422.

### `POST /v1/catalogue/reindex`

Reindex Route

Status codes: 200.

### `GET /v1/catalogue/search`

Search Route

| Path/query parameter | Type | Required |
| --- | --- | --- |
| `query` | string | no |
| `source_id` | string or null | no |
| `kind` | string or null | no |
| `data_type` | string or null | no |
| `tag` | string or null | no |
| `capability` | string or null | no |
| `evidence_status` | string or null | no |
| `max_age_seconds` | integer or null | no |
| `limit` | integer | no |
| `offset` | integer | no |
| `match` | `all` / `any` | no |

Status codes: 200, 422.

### `POST /v1/catalogue/search`

Search Post Route

Request body (`SearchRequest`):

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `query` | string | no | — |
| `source_id` | string or null | no | — |
| `source_ids` | list[string] or null | no | — |
| `objects_only` | boolean | no | — |
| `kind` | string or null | no | — |
| `data_type` | string or null | no | — |
| `type_category` | string or null | no | — |
| `tag` | string or null | no | — |
| `capability` | string or null | no | — |
| `evidence_status` | string or null | no | — |
| `max_age_seconds` | integer or null | no | — |
| `limit` | integer | no | — |
| `offset` | integer | no | — |
| `match` | `all` / `any` | no | — |

Status codes: 200, 422.

### `POST /v1/scan-requests`

Scan Route

Request body (`ScanBody`):

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `request` | ScanRequest | yes | — |
| `adapter` | `bundle` / `http` | yes | — |

Status codes: 200, 422.

### `GET /v1/scan-requests/{request_id}`

Status Route

| Path/query parameter | Type | Required |
| --- | --- | --- |
| `request_id` | string | yes |

Status codes: 200, 422.

## planner (`PLANNER_PORT`, default 8444)

Health: `GET /healthz` (no key).

### `POST /v1/join-paths`

Join Paths

Request body (`PathRequest`):

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `from_id` | string | yes | — |
| `to_id` | string | yes | — |
| `max_hops` | integer or null | no | — |
| `max_paths` | integer or null | no | — |

Status codes: 200, 422.

### `POST /v1/lineage/paths`

Find lineage paths

Request body (`PathRequest`):

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `from_id` | string | yes | — |
| `to_id` | string | yes | — |
| `max_hops` | integer or null | no | — |
| `max_paths` | integer or null | no | — |

Status codes: 200, 422.

### `POST /v1/planner/graph`

Graph

Status codes: 200.

### `POST /v1/plans`

Plans

Request body (`PlanRequest`):

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `path_id` | string | yes | — |
| `max_rows` | integer or null | no | — |

Status codes: 200, 422.

## contracts (`CONTRACTS_PORT`, default 8445)

Health: `GET /healthz` (no key).

### `GET /v1/contracts/schemas`

Schema Names

Status codes: 200.

### `GET /v1/contracts/schemas/{name}`

Schema

| Path/query parameter | Type | Required |
| --- | --- | --- |
| `name` | string | yes |

Status codes: 200, 422.

### `POST /v1/contracts/validate/{record_type}`

Validate

| Path/query parameter | Type | Required |
| --- | --- | --- |
| `record_type` | string | yes |

Status codes: 200, 422.
