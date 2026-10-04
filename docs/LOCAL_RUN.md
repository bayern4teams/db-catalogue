# Running the catalogue against your own databases

This builds one readable catalogue page of your PostgreSQL, ClickHouse and MongoDB databases:
every table, view and collection, every column with type, nullability, keys and statistics,
declared foreign keys, proposed relationships with the evidence behind each, and a list of
anything that could not be catalogued and why. Nothing is written to your databases, no data
values leave the collector, and nothing is sent anywhere except to the databases you configure
and, if you enable it, your own vLLM.

The flow is three commands per run, then as many questions as you like:

```
dbig collect            one signed bundle per database     (reads catalog metadata)
dbig-builder assemble   one snapshot from all bundles      (infers relationships)
dbig-builder render     one HTML page                      (what you open)

dbig-requester search   find tables and columns by name, table or comment
dbig-planner join-paths how to get from one table to another, across databases
dbig-planner plan       the read-only query for a path (never executed for you)
dbig-requester ask      a question in plain language, answered by your own vLLM
```

## What has been verified, and what has not

Verified, against real servers in CI and in the development workspace, at PostgreSQL 16.15,
ClickHouse 25.8.33.6 and MongoDB 7.0.41:

- `collect` over verified TLS on all three engines, with the CA supplied through `tls_ca_file`
  alone, and refusal of a certificate from an untrusted CA on all three;
- the read-only accounts in step 2, including that they cannot write;
- collect → assemble → render with all three engines in one catalogue.

Not verified: other database versions (they will probably work; nothing has checked them), your
network, your certificates, and the model review against a real vLLM. The review's request
format is checked against vLLM's own source, but whether a given model follows the schema is
only known once you run it.

## 1. Install

You need Python 3.12 and [uv](https://docs.astral.sh/uv/) 0.10 or newer (CI uses 0.12.19).
`.python-version` pins 3.12, so `uv sync` uses it even when a newer Python is installed; if
3.12 is missing, `uv python install 3.12` provides it. Python 3.13 and later verify
certificates more strictly (a CA without a key-usage extension is refused), which is not
what the supported matrix was checked with.

```bash
git clone https://github.com/bayern4teams/db-catalogue.git
cd db-catalogue
uv sync --locked --all-packages --all-extras
source .venv/bin/activate            # Windows PowerShell: .venv\Scripts\Activate.ps1
dbig --help && dbig-builder --help
```

Install once, then work inside the activated environment. `uv run` re-syncs to the default
dependency set and can remove the database drivers that `--all-extras` installed.

## 2. Create a read-only account on each database

Each statement below was run against the pinned server version, followed by a real `collect`
with that account and an attempted write that was refused.

**PostgreSQL**, once per schema you list in `namespaces`:

```sql
CREATE ROLE dbig_reader LOGIN PASSWORD 'choose-a-password';
GRANT USAGE ON SCHEMA public TO dbig_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO dbig_reader;
```

`SELECT` is what lets PostgreSQL show the account its own planner statistics (`pg_stats`). The
collector reads no table rows unless you turn sampling on.

**ClickHouse**, once per database:

```sql
CREATE USER dbig_reader IDENTIFIED WITH sha256_password BY 'choose-a-password'
    SETTINGS readonly = 2;
GRANT SHOW TABLES, SHOW COLUMNS ON mydb.* TO dbig_reader;
```

Use `readonly = 2`, not `1`. The collector sets a per-query time limit (`max_execution_time`)
and `readonly = 1` forbids changing any setting, so the scan fails. `2` still refuses every
write.

**MongoDB**, in the database being catalogued:

```javascript
db.getSiblingDB("appdb").runCommand({
  createUser: "dbig_reader",
  pwd: "choose-a-password",
  roles: [{ role: "read", db: "appdb" }]
})
```

Then connect with `authSource=appdb` in the URI.

## 3. TLS

Your databases are on other servers, so the collector connects with verified TLS: the server's
certificate must chain to a CA the collector trusts, and must name the host you connect to.

- If the certificates come from a public CA, or your company CA is installed in the operating
  system's trust store, there is nothing to configure.
- Otherwise ask your DBA for the CA certificate as a PEM file and set `tls_ca_file` in each
  source file. It is passed explicitly to all three drivers, which otherwise look for trust roots
  in different places.
- The host in the connection must match the certificate: if the certificate says
  `db1.corp.example`, connect to that name, not to its IP address.

`tls = false` exists only for databases on the same machine and is refused for any host other
than a loopback IP literal such as `127.0.0.1`. It is not an option for your servers.

## 4. Secrets and configuration

Copy the examples into `artifacts/`, which git ignores, and edit `source_id` and `namespaces`:

```bash
mkdir -p artifacts/config
cp examples/local/*.toml artifacts/config/
```

Set the secrets in the shell you run from. Nothing reads a `.env` file; the variables must be in
the environment.

```bash
# One key signs every bundle in a run; at least 32 bytes.
export DBIG_SIGNING_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
export PG_CONNECTION='{"host": "db1.corp.example", "port": 5432, "database": "app", "user": "dbig_reader", "password": "..."}'
export CLICKHOUSE_CONNECTION='{"host": "ch1.corp.example", "port": 8443, "username": "dbig_reader", "password": "..."}'
export MONGODB_CONNECTION='{"uri": "mongodb://dbig_reader:...@mongo1.corp.example:27017/?authSource=appdb"}'
```

PowerShell:

```powershell
$env:DBIG_SIGNING_KEY = python -c "import secrets; print(secrets.token_urlsafe(48))"
$env:PG_CONNECTION = '{"host": "db1.corp.example", "port": 5432, "database": "app", "user": "dbig_reader", "password": "..."}'
$env:CLICKHOUSE_CONNECTION = '{"host": "ch1.corp.example", "port": 8443, "username": "dbig_reader", "password": "..."}'
$env:MONGODB_CONNECTION = '{"uri": "mongodb://dbig_reader:...@mongo1.corp.example:27017/?authSource=appdb"}'
```

Any source setting can also be overridden for one run with `DBIG_<SETTING>`, for example
`DBIG_MAX_QUERIES=800`. Be aware that such a variable left set in your shell applies to every
source you collect.

## 5. Run

```bash
out=artifacts/run-$(date +%Y%m%d-%H%M%S)
dbig collect --config artifacts/config/postgresql.toml --out "$out/bundle-pg"
dbig collect --config artifacts/config/clickhouse.toml --out "$out/bundle-ch"
dbig collect --config artifacts/config/mongodb.toml    --out "$out/bundle-mongo"

dbig-builder assemble --config artifacts/config/builder.toml \
  --bundle "$out/bundle-pg" --bundle "$out/bundle-ch" --bundle "$out/bundle-mongo" \
  --out "$out/snapshot"

dbig-builder render --config artifacts/config/builder.toml --snapshot "$out/snapshot" \
  --bundle "$out/bundle-pg" --bundle "$out/bundle-ch" --bundle "$out/bundle-mongo" \
  --out "$out/catalog.html"
```

PowerShell uses the same commands with `$out = "artifacts/run-$(Get-Date -Format yyyyMMdd-HHmmss)"`
and a backtick instead of a backslash for line continuation.

Open `catalog.html` in a browser. Each output directory is new: `collect` and `assemble` refuse
to overwrite an existing one, so a run never half-replaces an earlier one.

You can catalogue fewer than three databases: collect only those, and pass only their bundles.

## 6. Reading the catalogue

- **Completeness** at the top is `complete` or `partial`. `partial` means a source could not be
  fully read or the model review failed; the Diagnostics section says which.
- **Diagnostics** lists what could not be catalogued and why, for example an object with no
  stable identifier (`IDENTITY_UNAVAILABLE`), which is left out rather than risk a wrong identity.
- **Relationships**: `DECLARES_FK` / `declared` comes from the database's own foreign keys.
  `REFERENCE_CANDIDATE` / `tentative` is inferred from names, types, keys and, if sampling is on,
  measured value overlap. Every candidate lists the evidence it rests on. Confidence reads "not
  calibrated" on purpose: no probability is shown until one has been measured on labelled data.
- **Statistics** come from the database's own planner statistics, with the time they were last
  analyzed; they are estimates and can be stale.
- Each source shows how it was reached: `tls-verified`, or `plaintext-loopback` in warning colour.
- Table and column **comments** appear under each table and in a Description column. Turn them
  off per source with `include_comments = false`.

## 7. Search, join paths and query plans

Point the requester at the run's snapshot, build its search index once per run, then search:

```bash
printf '[requester]\nsnapshot_dir = "%s"\nstate_dir = "%s"\napi_key_env = "REQUESTER_TOKEN"\n' \
  "$out/snapshot" "$out/requester" > "$out/requester.toml"
dbig-requester index  --config "$out/requester.toml"
dbig-requester search --config "$out/requester.toml" --query customer
dbig-requester search --config "$out/requester.toml" --query "where is the customer email" --match any
```

- A word matches as a prefix: `customer` finds `customers`, `customer_id` and every column of
  the `customers` table. Entries whose own name matches come first.
- By default every word must match. `--match any` ranks entries by how well they match any
  meaningful word, which is what you want for a question in plain language.
- Comments count: a column is found by what its comment says.
- Narrow with `--source-id`, `--kind table|view|collection|field`, `--data-type`, `--limit`.

Each result has an `id` such as `object:e970e074...`. Paths are asked between two object IDs:

```bash
mkdir -p "$out/observations"
dbig-planner build --snapshot "$out/snapshot" --observations "$out/observations" --out "$out/graph"
dbig-planner join-paths --graph "$out/graph" --from "object:<from-id>" --to "object:<to-id>"
dbig-planner plan --graph "$out/graph" --path-id "<path_id from the join-paths answer>"
```

What the planner gives back:

- **Multi-hop in one database**: `refunds -> orders -> customers` in PostgreSQL becomes one
  `SELECT ... JOIN ... JOIN ...` with status `ready_for_review` (`review_required` when a hop
  is an inferred candidate rather than a declared foreign key). Identifiers are quoted, the
  statement is checked to be a single read, and it carries a row limit.
- **Across databases**: `tickets` (MongoDB) -> `customers` -> `orders` -> `refunds`
  (PostgreSQL) becomes one read per database, with consecutive tables in the same database
  joined in that one read, plus a `logical_join` step naming the keys that connect them. The
  status is `unresolved` because moving rows between databases needs a staging area or a
  federated engine you choose; the planner does not pick one for you.
- Nothing is ever executed by the planner. You review a plan and run it yourself.

`lineage-paths` answers "where did this data come from", which needs a record of what actually
ran: SQL logs or pipeline events placed as JSON lines in the `observations` directory. With the
directory empty, lineage paths are empty; relationships between tables still come from
`join-paths`. The observation format is in `TECHNICAL_IMPLEMENTATION.md`.

## 8. Optional: measure value overlap across databases

Name matching alone proposes candidates; measured overlap tells you whether a child column's
values really appear in the parent key. It needs sampling, which reads rows, so it is off by
default. To enable it on PostgreSQL and MongoDB sources (ClickHouse does not produce tokens):

```bash
export DBIG_TOKEN_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
```

and in each of those source files:

```toml
sample_rows = 100
value_token_key_env = "DBIG_TOKEN_KEY"
value_token_key_id = "run-1"
```

Use the same key and ID for every source in a run and a new key for each run. Only keyed digests
leave the collector; the key never enters any file, and digests from different runs cannot be
compared.

## 9. Optional: your own vLLM, for review and for questions

Start vLLM bound to loopback with an API key. `--host` defaults to all interfaces, so set it:

```bash
export VLLM_API_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
vllm serve <model> --revision <commit> --host 127.0.0.1 --port 8000 --api-key "$VLLM_API_KEY"
```

Then use `artifacts/config/builder-with-vllm.toml` for `assemble` (and `render`), with `model`
set to the exact name vLLM serves and `model_digest` to the revision you pinned. Rules enforced:

- plain `http://` only to `127.0.0.1` or `::1`; a vLLM on another machine needs `https://` on a
  private IP literal; hostnames are refused;
- the model only sees object names and evidence metrics, never values or credentials;
- its answers are validated against a schema and may only cite evidence that exists; they are
  recorded as weak evidence and never change a declared fact;
- if vLLM is down, slow or answers badly, the catalogue is still published, marked `partial`.

To ask questions in plain language, add a `[requester.answer]` table to the requester file
(see `examples/local/requester-with-vllm.toml`), then:

```bash
dbig-requester ask --config "$out/requester.toml" --question "Which column holds the customer's email?"
```

The requester finds the most relevant catalogue entries for the question (up to
`retrieval_limit`) and gives the model their names, types, table and comments. The answer must
cite only those entries, and is refused if it cites anything else or claims lineage. If nothing
in the catalogue matches the question, you get `NO_CATALOGUE_MATCH` and the model is not asked.
Comments are free text written by your database owners and are given to the model by your
choice; the model is told to treat them as data, not instructions.

## 10. Troubleshooting

| Message | Meaning and fix |
|---|---|
| `the server's TLS certificate did not verify` | Set `tls_ca_file` to your company CA, or check it is the right CA; connect with the host name the certificate names. |
| `the scan reached a configured limit` | Raise `max_queries` (MongoDB needs about 3 × collections + databases), `max_objects` or `max_fields`. No bundle was written. |
| `tls = false is allowed only for loopback IP literals` | Plaintext is refused off this machine; enable TLS on the server. |
| `Setting max_execution_time is unknown or readonly` | The ClickHouse account has `readonly = 1`; use `readonly = 2`. |
| `signature key unavailable` / `invalid artifact signature` | `DBIG_SIGNING_KEY` differs between `collect` and `assemble`; use one key per run. |
| `bundle ... was not assembled into this snapshot` | Pass `render` the same bundles you passed `assemble`. |
| `no search index at ...` | Run `dbig-requester index` for this run first. |
| `STALE_INDEX` | The snapshot changed after indexing; run `dbig-requester index` again. |
| `STALE_PATH` | The path came from another graph; run `join-paths` again against this one. |

Other connection failures (wrong password, unreachable host) currently appear as the driver's
own error message with a traceback.
