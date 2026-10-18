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

Verified against real servers, in CI on every run and in the development workspace, at two
sets of versions: PostgreSQL 16.15, ClickHouse 25.8.33.6 and MongoDB 7.0.41, and the latest
releases as of 2026-10-04, PostgreSQL 18.6, ClickHouse 26.9.10.4 and MongoDB 9.0.2:

- `collect` over verified TLS on all three engines, with the CA supplied through `tls_ca_file`
  alone, and refusal of a certificate from an untrusted CA on all three;
- the read-only accounts in step 2: on the latest versions each collected exactly what an
  administrator collects, and each write was refused (MongoDB checked with access control on);
- collect → assemble → render with all three engines in one catalogue, including quoted and
  mixed-case names, composite keys, partitions, views and nested MongoDB documents;
- search, join paths, and plans executed on the servers and compared with the true result,
  across databases as well;
- scan requests through both routes in step 10;
- the one-line messages in the troubleshooting table for a refused login, an unreachable server
  and a misspelt namespace.

Not verified: versions between and outside those two sets (they will probably work), your
network, your certificates, your data's shapes, and anything involving a real model: the review
and `ask` against your vLLM. The review's request format is checked against vLLM's own source,
but whether a given model follows the schema is only known once you run it.

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

`SHOW TABLES, SHOW COLUMNS` is all `collect` needs. If you also want to run query plans with
`scripts/run_plan.py` (`docs/LIVE_TEST.md` section 9), that script runs the plan's `SELECT`
against your tables, so the account needs `SELECT` as well, or ClickHouse refuses with code 497
("Not enough privileges"):

```sql
GRANT SELECT ON mydb.* TO dbig_reader;
```

Reading rows is the only thing this adds; `readonly = 2` still refuses every write. Leave the
grant out if you never execute a plan. The statement above is not covered by the verification
claim at the top of this step.

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

Put the secrets in a `.env` file in the repository root, which git ignores, and load it into the
shell you run from. The commands read environment variables only; `set -a` exports everything
the file sets:

```bash
cp .env.example .env && chmod 600 .env    # then edit .env: one JSON object per database
set -a; . ./.env; set +a                  # in every new shell, before running any command
```

Keep the single quotes around each JSON value; a password containing a single quote is written
as `'\''` inside them. `DBIG_SIGNING_KEY` must be filled in too: unset or empty, `collect`
stops with a one-line message naming it, and a key shorter than 32 bytes is refused.

If `. ./.env` fails, the shell's message points at the wrong place: for an unclosed quote it
names the last line of the file, and for an unquoted space it names the word after the space
as the missing command. This command lists each suspect line by number and variable name, and
never prints a value (no output means every line looks well formed):

```bash
awk '
  /^[[:space:]]*(#|$)/ { next }
  { line = $0; sub(/^[[:space:]]*(export[[:space:]]+)?/, "", line)
    name = line; sub(/=.*/, "", name)
    plain = line; gsub(/\\\047/, "", plain); quotes = gsub(/\047/, "&", plain)
    if (line !~ /^[A-Za-z_][A-Za-z0-9_]*=/) print "line " NR ": not NAME=value"
    else if (quotes % 2) print "line " NR ": " name ": unbalanced single quote"
    else if (line ~ /^[A-Za-z_][A-Za-z0-9_]*=[^\047"]*[[:space:]]/) print "line " NR ": " name ": space in an unquoted value"
    else if (line ~ /\r$/) print "line " NR ": " name ": Windows line ending" }
' .env
```

It checks the shapes that break the file (a line that is not `NAME=value`, an odd number of
single quotes once `\'` is set aside, a space in an unquoted value, a Windows line ending); it
does not parse the JSON, which the check in `docs/LIVE_TEST.md` section 3 does.

Or set the variables directly:

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
  A MongoDB view is listed as `VIEW_NOT_CATALOGUED`: it is a saved query with no stable
  identity, so it is left out, but the collection it reads is catalogued and the catalogue stays
  `complete`.
- **Relationships**: `DECLARES_FK` / `declared` comes from the database's own foreign keys.
  `REFERENCE_CANDIDATE` / `tentative` is inferred from names (`customer_id`, `customerId`, or a
  column named exactly like another table's key, such as `AccountID`), types, keys and, if
  sampling is on, measured value overlap. Every candidate lists the evidence it rests on. Confidence reads "not
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
- Over HTTP (`dbig-requester serve`), send a question with `POST /v1/catalogue/search` and a JSON body
  (`{"query": "...", "match": "any"}`): the `GET` route puts the query in the URL, which a server
  access log records. `dbig-requester serve ... --no-access-log` turns the request log off.
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
  statement is checked to be a single read, and it carries a row limit. Every column is named
  `<table>__<column>`, so `refunds__id`, `orders__id` and `customers__id` stay apart.
- **Across databases**: `tickets` (MongoDB) -> `customers` -> `orders` -> `refunds`
  (PostgreSQL) becomes one read per database, with consecutive tables in the same database
  joined in that one read, plus a `logical_join` step naming the keys that connect them. The
  first read returns up to `max_rows` rows; each later read fetches only the rows matching the
  keys the read before it returned, so the final join finds every match for those rows. The
  status is `unresolved` because moving rows between databases needs a staging area or a
  federated engine you choose; the planner does not pick one for you.
- Nothing is ever executed by the planner. You review a plan and run it yourself.

Running a cross-database plan by hand: run the steps in order. A later step's `parameters`
say which column of which earlier step supplies its keys (`values_from_step`,
`values_from_column`); collect those values and bind them under the parameter's `name`:

```bash
# PostgreSQL step, from Python with psycopg: the placeholder is %(keys_1_1)s, bound to a list
#   cur.execute(step_sql, {"keys_1_1": [150, 157]})
# ClickHouse step: server-side parameter
clickhouse-client --param_keys_1_1="[150,157]" --query "<step sql>"
# MongoDB step: pass the keys as `let` variables
mongosh ... --eval 'db.tickets.aggregate(<step pipeline>, {let: {keys_1_1: [150, 157]}})'
```

`scripts/run_plan.py` does all of this for you with the same drivers the collector uses, so none
of `psql`, `clickhouse-client` or `mongosh` is needed (`docs/LIVE_TEST.md` section 9); the
lines above show what it does.

Each read keeps the row limit; if a later read returns exactly `max_rows` rows the plan's
warning applies: re-plan with a larger limit, `dbig-planner plan ... --max-rows 5000`.

Joins your applications run count too: a SELECT in the `observations` directory whose `ON`
clause equates qualified columns (`ON v.account_ref = a.acct_no`) becomes a join path with
status `observed`, even where no key or candidate links the tables. Each line of an
observation file is one JSON object:

```json
{"kind": "sql", "id": "q1", "source_id": "pg-main", "dialect": "postgres", "status": "executed", "observed_at": "2026-10-01T10:00:00Z", "sql": "SELECT ... FROM public.visits v JOIN public.accounts a ON v.account_ref = a.acct_no"}
```

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

### Optional: measure uniqueness on ClickHouse and MongoDB

Those two keep no per-column statistics, so with the metadata alone none of their columns can be a
measured-unique parent. Setting `cardinality_probe = true` in a source file (default off) adds one
bounded query per table or collection (charged to `max_queries`) that returns only counts; the
profile then carries a distinct count and a uniqueness over the first `cardinality_probe_max_rows`
rows (default 100000) of up to `cardinality_probe_max_columns` columns (default 20). On a larger table
that is uniqueness within those rows, and `sample_count` says how many. A failed probe is a
`STATS_UNAVAILABLE` diagnostic, not a failed scan. Because it reads data, a scan request needs the
`sample` capability for it. For MongoDB also set `sample_rows`, otherwise only validator paths are
known. See the commented keys in `examples/local/clickhouse.toml` and `mongodb.toml`.

### What the overlap does to candidates

With sampling on, a cross-source candidate whose child values were compared and found **0 of N** (N at least `min_token_support`, default 4, in the `[builder]` file) is dropped. A candidate with some overlap, even 21 % to 27 %, stays and shows its number and sample size: overlap ranks and warns, it never decides alone. A sample smaller than `min_token_support` keeps the candidate, marked "sample too small". With sampling off every candidate says "values not compared". Two optional `[builder]` lists restrict candidates by source: `source_groups` (sources in different groups are not linked) and `forbidden_source_pairs`; both default to empty. See `examples/local/builder.toml`.

Other settings added after the live test, all optional, with defaults in the example files: `[builder.review] batch_size = 5` (candidates per model call); `[requester] exclude_objects` (per source glob list, hides schema-description collections; re-run `dbig-requester index`); `[requester.answer] relationship_limit = 20` (relationships given to the model, 0 turns them off); planner `cross_source_candidate_weight = 4` (how much longer a cross-source path must be to lose against a same-source one). `ask` can reply `NOT_ESTABLISHED_BY_CATALOGUE`: that is an answer ("the catalogue does not show this"), not an error.

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

### llama.cpp and other OpenAI-compatible servers

The JSON schema sent to the server no longer carries length or item bounds (`maxLength`,
`minLength`, `maxItems`, `minItems`): llama.cpp refuses them ("failed to parse grammar"), and the
limits are checked after the reply is parsed instead. Failures now have their own code:
`MODEL_UNREACHABLE` (host down or firewalled), `MODEL_TIMEOUT`, `MODEL_REJECTED_REQUEST` (an HTTP 4xx),
`MODEL_TRUNCATED` (the server stopped on `max_tokens`), `MODEL_INVALID_CITATION` and `MODEL_ANSWER_TOO_LONG` (longer than
`max_answer_chars`, unset by default). Other causes keep
`MODEL_UNAVAILABLE_OR_INVALID`.

A reasoning model spends tokens thinking before it answers. In one test with the default
`max_tokens = 512` it used all 512 and returned an empty reply (`MODEL_TRUNCATED`); 20 review
candidates did not finish in 8192 tokens (72 s), while 5 candidates with `max_tokens = 24000` and
`timeout_seconds = 900` completed in 14.9 s using 1858 tokens. Raise `max_tokens` and
`timeout_seconds` for such a model, and review fewer candidates. `[requester.answer] max_tokens`
is unset by default: `ask` then sends no limit and the server's own maximum applies. A shared server queues
requests: a reply that took 19 s idle took over 60 s when the server was busy, so the timeout must
cover the queue (`MODEL_TIMEOUT` says this, not "unavailable"). These numbers come from one
model on one shared server; measure your own.

## 10. Optional: request a new scan from the requester

The requester can ask a collector site to re-scan, signed with the same key the site signs
bundles with. Add to the requester file:

```toml
[requester.scan]
signing_key_env = "DBIG_SIGNING_KEY"
key_id = "local"                                   # the site's signing_key_id
allowed_sources = { "pg-main" = ["public"] }       # sources and namespaces it may ask for
handoff_root = "artifacts/handoff"                 # route 1: a directory both sides can reach
collector_urls = { "pg-main" = "http://127.0.0.1:8601" }  # route 2: the site's private API
http_token_env = "COLLECTOR_TOKEN"
```

and to the source file `authorized_requesters = ["<requester_id>"]` (and `http_token_env` for
route 2). A request is a JSON file with `id`, `source_id`, `scopes`, `capabilities`
(`["metadata"]`), `reason`, `requester_id`, `idempotency_key`, `issued_at`, `expires_at` and
`reply_location`. Then:

```bash
# Route 1: signed file in the handoff directory, accepted at the site
dbig-requester request-scan --config "$out/requester.toml" --request scan.json --dispatch bundle
dbig accept-scan --config artifacts/config/postgresql.toml \
  --request artifacts/handoff/<id>/request.json --queue-db artifacts/scan-queue.sqlite3
# Route 2: the site runs its API, and the requester posts to it
dbig serve --config artifacts/config/postgresql.toml --queue-db artifacts/scan-queue.sqlite3 \
  --host 127.0.0.1 --port 8601 &
dbig-requester request-scan --config "$out/requester.toml" --request scan.json --dispatch http
# Either way, the site runs what is queued; the receipt names the new bundle
dbig process-scans --config artifacts/config/postgresql.toml \
  --queue-db artifacts/scan-queue.sqlite3 --out-root artifacts/scans
```

A failed scan's receipt carries `error`, the same one-line message `collect` would print.

## 11. Troubleshooting

| Message | Meaning and fix |
|---|---|
| `the server's TLS certificate did not verify` | Set `tls_ca_file` to your company CA, or check it is the right CA; connect with the host name the certificate names. |
| `the server refused the login` | Wrong user or password in the connection variable it names, or the account does not exist on that server. |
| `the database named in ... does not exist` | The PostgreSQL `database` in the connection variable is misspelt. |
| `could not reach the server` | Wrong host or port, a firewall in between, or a slow network: raise `read_timeout_seconds`. |
| `... listed in namespaces does not exist or is not visible to this account` | A misspelt schema or database in `namespaces`, or the account has no grant on it. No bundle was written. |
| `configuration ... is invalid` / `is not valid TOML` | The message names the setting and line; fix the file. |
| `observation <file>:<line> is invalid` | That line of an observation file does not match the format in `TECHNICAL_IMPLEMENTATION.md`. |
| `the scan reached a configured limit` | Raise `max_queries` (MongoDB needs about 3 × collections + databases), `max_objects` or `max_fields`. No bundle was written. |
| `tls = false is allowed only for loopback IP literals` | Plaintext is refused off this machine; enable TLS on the server. |
| `Setting max_execution_time is unknown or readonly` | The ClickHouse account has `readonly = 1`; use `readonly = 2`. |
| `signature key unavailable` / `invalid artifact signature` | `DBIG_SIGNING_KEY` differs between `collect` and `assemble`; use one key per run. |
| `bundle ... was not assembled into this snapshot` | Pass `render` the same bundles you passed `assemble`. |
| `no search index at ...` | Run `dbig-requester index` for this run first. |
| `DBIG_SIGNING_KEY ... must hold at least 32 bytes` | Generate a longer key, for example `python -c 'import secrets; print(secrets.token_urlsafe(48))'`. The message never prints the key. |
| `STALE_INDEX` | The snapshot changed after indexing; run `dbig-requester index` again. |
| `STALE_PATH` | The path came from another graph; run `join-paths` again against this one. |

A traceback means something not listed here; please report it with the command you ran.
