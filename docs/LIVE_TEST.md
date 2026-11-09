# Testing the catalogue live on your own databases

This is the checklist for the first real run on your machine, against your PostgreSQL,
ClickHouse and MongoDB servers and your vLLM. Everything here has already passed in CI against
real servers (PostgreSQL 16.15 and 18.6, ClickHouse 25.8.33.6 and 26.9.10.4, MongoDB 7.0.41 and
9.0.2). What CI cannot know is your network, your certificates, your accounts, your data's
shapes, your model, and whether the relationships it proposes are right for your data. Those
are what this run checks.

Work through the sections in order. Each step says what to run, what a pass looks like, and what
to write down. Section 13 lists what to send back, and the end of this file has a prompt you can
give a local agent to do the whole run for you.

**Never** paste a password, a key, a `.env` file or row values into a report, a chat or an
issue. Counts, names of tables and columns, error messages and timings are what is needed.

## 0. What you need

- Linux, `git`, and [uv](https://docs.astral.sh/uv/) 0.10 or newer (`uv --version`).
- Python 3.12. If `uv python find 3.12` finds nothing, run `uv python install 3.12`.
- `openssl` (for the TLS checks) and network access from this machine to each database.
- `psql`, `clickhouse-client` and `mongosh` are optional: section 3 defines the same checks with
  the Python drivers the collector already installed (`pg8000`, `clickhouse_connect`, `pymongo`).
- The PEM file of the CA that issued your database certificates, if it is a company CA.
- An account on each database that can create a read-only user, or a DBA who will.
- For sections 10-11: a Linux or WSL2 machine with a GPU running vLLM.

## 1. Install and run the test suite

```bash
git clone https://github.com/bayern4teams/db-catalogue.git && cd db-catalogue
git checkout feat/catalogue-fixes     # until it is merged into main
uv sync --locked --all-packages --all-extras --python 3.12
source .venv/bin/activate
python --version                                # 3.12.x
pytest -q
```

Pass: about 480 passed, about 70 skipped, **0 failed** (478 and 68 when this was written). The skipped tests are the live ones that
need the CI database containers; they ran in CI. Write down the summary line.

## 2. Read-only accounts

Create them exactly as `docs/LOCAL_RUN.md` step 2 says, for every schema (PostgreSQL) or
database (ClickHouse, MongoDB) you will catalogue. If you will run plans in section 9, the
ClickHouse account also needs `GRANT SELECT ON <db>.* TO dbig_reader`, which that step explains.
Then prove each one cannot write, with `psql`, `clickhouse-client` and `mongosh` as below, or
with the helpers of section 3 once `.env` is loaded (`pgq "INSERT ..."`, `chq "INSERT ..."`,
`mongoprobe <db> <collection>`); the refusal message is the last line they print:

```sql
-- PostgreSQL, connected as dbig_reader: expect "permission denied"
INSERT INTO <schema>.<any_table> DEFAULT VALUES;
-- ClickHouse, as dbig_reader: expect code 497 "Not enough privileges" (or 164 "readonly")
INSERT INTO <db>.<any_table> SELECT * FROM <db>.<any_table> LIMIT 0;
```

```javascript
// MongoDB, as dbig_reader: expect "Unauthorized" (only if your server has access control on)
db.getSiblingDB("<db>").<collection>.insertOne({probe: 1})
```

Pass: every write refused. Write down the three refusal messages.

## 3. Secrets: the `.env` file

The connection strings go in a `.env` file in the repository root. Git ignores it; never commit
it. The commands read environment variables only, so you load the file into each new shell:

```bash
cp .env.example .env && chmod 600 .env
$EDITOR .env          # fill DBIG_SIGNING_KEY and one JSON object per database
set -a; . ./.env; set +a
```

- `DBIG_SIGNING_KEY`: generate with `python -c 'import secrets; print(secrets.token_urlsafe(48))'`.
- `PG_CONNECTION`, `CLICKHOUSE_CONNECTION`, `MONGODB_CONNECTION`: one JSON object each, in single
  quotes, exactly as the example shows. Use the **host name on the certificate**, not an IP.
  ClickHouse uses its HTTPS port (usually 8443). MongoDB needs `authSource=<db>` in the URI.
- A password containing `'` is written as `'\''` inside the quotes.

Check: `python -c 'import json,os; [json.loads(os.environ[v]) for v in ("PG_CONNECTION","CLICKHOUSE_CONNECTION","MONGODB_CONNECTION")]; print("ok")'`
prints `ok`.

If `. ./.env` fails, or the check above does, the shell's message does not name the variable
(an unclosed quote is reported at the last line of the file). `docs/LOCAL_RUN.md` step 4 has a
command that lists each malformed line by number and variable name without printing a value.

**Checks without `psql` or `mongosh`.** Sections 2 and 7 give statements for those clients. They
are optional: with `.env` loaded, these helpers run the same statements through the collector's
drivers, with the account in your connection variables (the one `collect` uses), over verified
TLS. They are for your servers, which use TLS; uncomment the first line below and point `CHECK_CA_FILE` at your CA file, or leave it
unset if the CA is in the system trust store. A refused statement or an unreachable server ends
in a traceback whose last line is the message.

```bash
# export CHECK_CA_FILE=/path/to/company-ca.pem   # only if your CA is not in the system trust store

pgq() { python -c '
import json, os, ssl, sys
from pg8000.dbapi import connect
c = json.loads(os.environ["PG_CONNECTION"])
keys = ("host", "port", "database", "user", "password")
tls = ssl.create_default_context(cafile=os.environ.get("CHECK_CA_FILE"))
con = connect(**{k: c[k] for k in keys if k in c}, ssl_context=tls, timeout=30)
cur = con.cursor()
cur.execute(sys.argv[1])
if cur.description:
    print(*cur.fetchall(), sep="\n")
con.rollback()
' "$1"; }

chq() { python -c '
import json, os, sys
import clickhouse_connect
c = json.loads(os.environ["CLICKHOUSE_CONNECTION"])
keys = ("host", "port", "username", "password")
client = clickhouse_connect.get_client(
    **{k: c[k] for k in keys if k in c},
    secure=True, verify=True, ca_cert=os.environ.get("CHECK_CA_FILE"),
)
result = client.query(sys.argv[1])
print(*result.result_set, sep="\n")
' "$1"; }

mongoq() { python -c '
import json, os, sys
from pymongo import MongoClient
c = json.loads(os.environ["MONGODB_CONNECTION"])
client = MongoClient(c["uri"], tls=True, tlsCAFile=os.environ.get("CHECK_CA_FILE"), serverSelectionTimeoutMS=30000)
db = client[sys.argv[1]]
for info in db.list_collections():
    print(info["name"], info["type"])
' "$1"; }

mongoprobe() { python -c '
import json, os, sys
from pymongo import MongoClient
c = json.loads(os.environ["MONGODB_CONNECTION"])
client = MongoClient(c["uri"], tls=True, tlsCAFile=os.environ.get("CHECK_CA_FILE"), serverSelectionTimeoutMS=30000)
client[sys.argv[1]][sys.argv[2]].insert_one({"probe": 1})
print("WRITE WAS ACCEPTED: this account is not read-only")
' "$1" "$2"; }
```

Use them as `pgq "SELECT count(*) FROM information_schema.tables WHERE table_schema = '<schema>'"`,
`chq "SELECT count() FROM system.tables WHERE database = '<db>'"` and `mongoq <db>`, which prints
each collection and its type (`collection` or `view`).

## 4. Source configuration files

```bash
mkdir -p artifacts/config && cp examples/local/*.toml artifacts/config/
```

In each of `postgresql.toml`, `clickhouse.toml`, `mongodb.toml` set `source_id` (any stable
name), `namespaces` (your schemas or databases), and `tls_ca_file = "/path/to/company-ca.pem"`
if your CA is not in the operating system's trust store. Leave `builder.toml` as it is.

## 5. TLS reaches each server

```bash
openssl s_client -connect <pg-host>:5432 -starttls postgres -CAfile <ca.pem> -verify_hostname <pg-host> </dev/null 2>/dev/null | grep "Verify return code"
openssl s_client -connect <ch-host>:8443 -CAfile <ca.pem> -verify_hostname <ch-host> </dev/null 2>/dev/null | grep "Verify return code"
openssl s_client -connect <mongo-host>:27017 -CAfile <ca.pem> -verify_hostname <mongo-host> </dev/null 2>/dev/null | grep "Verify return code"
```

Pass: `Verify return code: 0 (ok)` three times. A failure here is the network or the certificate,
not the catalogue: fix it before going on (wrong CA file, or connecting by a name the
certificate does not carry).

## 6. Collect, assemble, render

Run `docs/LOCAL_RUN.md` step 5 as written. Pass:

- each `collect` prints its bundle directory and no error. If you see a one-line message, the
  troubleshooting table in `docs/LOCAL_RUN.md` says what to change; write the message down;
- `assemble` prints a manifest with `"completeness":"complete"`. If `partial`, open the page and
  write down every line of its Diagnostics section;
- `catalog.html` opens in a browser.

Write down the time each command took (`time dbig collect ...`).

## 7. The catalogue matches your databases

Compare counts the database reports with the page's counts (run each statement with `psql` or
`clickhouse-client`, or as the argument of `pgq` / `chq` from section 3; for MongoDB use
`mongosh` as shown or `mongoq <db>`):

```sql
-- PostgreSQL, per catalogued schema
SELECT count(*) FROM information_schema.tables WHERE table_schema = '<schema>';
SELECT count(*) FROM information_schema.columns WHERE table_schema = '<schema>';
SELECT count(*) FROM information_schema.table_constraints
 WHERE table_schema = '<schema>' AND constraint_type = 'FOREIGN KEY';
-- ClickHouse, per database
SELECT count() FROM system.tables WHERE database = '<db>';
SELECT count() FROM system.columns WHERE database = '<db>';
```

```javascript
// MongoDB, per database: collections (views listed separately)
db.getSiblingDB("<db>").getCollectionInfos().map(c => c.type)
```

Pass: tables and columns equal (PostgreSQL also counts materialized views and partitions as
objects, so allow for those); declared foreign keys equal the page's `DECLARES_FK` count; every
MongoDB collection appears; each MongoDB view appears once under Diagnostics as
`VIEW_NOT_CATALOGUED`. Also open three tables you know well and check their columns, types,
keys and comments.

## 8. Are the proposed relationships right?

This is the measurement only you can make. On the page, take 20 `REFERENCE_CANDIDATE`
relationships (all of them if there are fewer) and mark each:

- **true**: the child column really holds the parent's key;
- **false**: it does not;
- **unsure**.

Also write down any relationship you know exists that is **missing**. Pass is not a number here:
the counts are the result. Note which `blocking_rule` the wrong ones came from (it is listed in
each relationship's evidence).

## 9. Search and plans

```bash
printf '[requester]\nsnapshot_dir = "%s"\nstate_dir = "%s"\napi_key_env = "REQUESTER_TOKEN"\n' \
  "$out/snapshot" "$out/requester" > "$out/requester.toml"
dbig-requester index --config "$out/requester.toml"
dbig-requester search --config "$out/requester.toml" --query "<a word from your schema>"
dbig-requester search --config "$out/requester.toml" --query "<a real question>" --match any
```

Ask five questions you would really ask. Pass: the right table or column is in the first five
results. Write down each question and whether it was.

Then plans for joins you already know:

```bash
mkdir -p "$out/observations"
dbig-planner build --snapshot "$out/snapshot" --observations "$out/observations" --out "$out/graph"
dbig-planner join-paths --graph "$out/graph" --from "object:<from>" --to "object:<to>"
dbig-planner plan --graph "$out/graph" --path-id "<path_id>" > "$out/plan.json"
python scripts/run_plan.py --plan "$out/plan.json" \
  --config artifacts/config/postgresql.toml --config artifacts/config/clickhouse.toml \
  --config artifacts/config/mongodb.toml
```

Take three joins inside one database and, if you have one, one across databases. The ClickHouse
account needs `SELECT` for this (step 2 above); without it the script ends with code 497.
`scripts/run_plan.py` runs every step read-only, binds each later step to the keys the earlier
one returned, and reports rows per step and the joined row count. Pass: for a same-database
plan, the row count equals your own hand-written query with the same joins and `LIMIT 1000`;
for a cross-database plan, `joined_rows` equals what you would get joining the same first-step
rows by hand, and `left_rows_without_a_match` counts first-step rows with nothing on the other
side; a later step is an inner join, so a row whose key has no partner there is expected to be
unmatched.

Optional, if you have query logs: export some SELECT statements with joins as observation lines
(format in `docs/LOCAL_RUN.md` step 7), rebuild the graph, and check that `join-paths` now finds
those joins with status `observed`. For PostgreSQL, `pg_stat_statements` holds normalised queries.

## 10. Your vLLM: questions

Start vLLM as `docs/LOCAL_RUN.md` step 9 says (bound to `127.0.0.1`, with `--api-key`). Then:

```bash
curl -s -H "Authorization: Bearer $VLLM_API_KEY" http://127.0.0.1:8000/v1/models
```

lists your model. Copy `examples/local/requester-with-vllm.toml` next to the run, set
`snapshot_dir`, `state_dir`, `model` (exactly as `/v1/models` lists it) and `model_digest`, and
ask ten questions:

```bash
time dbig-requester ask --config "$out/requester-vllm.toml" --question "<question>"
```

Pass, for each: the answer cites IDs that exist (search for them) and the cited column is the
right one. Write down: right / wrong / refused, and the time each took. A question about
something not in the catalogue should return `NO_CATALOGUE_MATCH`.

## 11. Your vLLM: relationship review

```bash
dbig-builder assemble --config artifacts/config/builder-with-vllm.toml \
  --bundle "$out/bundle-pg" --bundle "$out/bundle-ch" --bundle "$out/bundle-mongo" \
  --out "$out/snapshot-reviewed"
```

with `model` and `model_digest` set in that file. Pass: `"completeness":"complete"`, and the page
rendered from it shows a model suggestion on reviewed candidates. Look at five: does the
model's `supports`/`opposes` agree with your marks from section 8? Then stop vLLM and run the
same command with a new `--out`: it must still publish, marked `partial` with
`MODEL_REVIEW_FAILED`.

## 12. Optional

- Value overlap (`docs/LOCAL_RUN.md` step 8): repeat sections 6 and 8 with sampling on, and see
  whether the relationship marks improve.
- HTTP services: `dbig-requester serve` / `dbig-planner serve` on `127.0.0.1` with a token; a
  request without the token must get 401. Send a question to search with `POST /v1/catalogue/search`
  (JSON body, same fields as the query string of the `GET` route): the `GET` route puts the query in
  the URL, and Uvicorn's access log would record it. Start the service with `--no-access-log` to
  keep request lines out of the log altogether.
- Scan requests (`docs/LOCAL_RUN.md` step 10).

## 13. What to send back

One report, with no secrets and no row values:

1. The `pytest -q` summary line (section 1).
2. Your server versions, and the three write refusals (section 2).
3. Any `collect` / `assemble` message, the completeness, every Diagnostics line, and timings
   (section 6).
4. The count comparison table (section 7) and any table whose columns, types or keys were wrong.
5. Relationship marks: true / false / unsure counts, the `blocking_rule` of each wrong one, and
   missing relationships (section 8).
6. Search: each question and whether the right result was in the first five; plans: each
   `run_plan.py` summary next to your own query's row count (section 9).
7. vLLM: model name and revision, GPU, each question's result and time, and the review
   comparison (sections 10-11).
8. Every traceback, in full, with the command that produced it. A traceback is a bug: every
   operator mistake is meant to end in a one-line message.

## Prompt for a local agent

Give this to an agent running on your machine (for example Claude Code in the cloned
repository). Fill in the angle-bracket parts first. It works inside the repository and never
needs your passwords: they are already in `.env`, which you fill in yourself.

```text
You are testing the db-catalogue project live on this machine, in this repository, on branch
feat/catalogue-fixes. Follow docs/LIVE_TEST.md sections 1 to 11 in order, and
docs/LOCAL_RUN.md where it points there. Read CLAUDE.md and AGENTS.md first and follow them.

The databases to catalogue:
- PostgreSQL <version> at <host>, schemas <schemas>
- ClickHouse <version> at <host>, databases <databases>
- MongoDB <version> at <host>, databases <databases>
The company CA is at <path or "in the system trust store">.
The read-only accounts <already exist | must be created: ask me to run the statements>.
vLLM <runs at http://127.0.0.1:8000/v1 serving <model> at revision <revision> | is not
available: skip sections 10 and 11>.
I have filled in .env myself. Load it with `set -a; . ./.env; set +a` in every shell.

Rules:
- Never print, log, copy or commit .env, passwords, keys, tokens or connection strings, and
  never put row values in the report; use --show only if I ask.
- Never write to any database. Use only the read-only accounts, except where I run statements.
- Do not change source code, configuration in examples/, or tests. If something fails, stop at
  that step, record the exact command and its full output, find the cause, and tell me what you
  think is wrong before changing anything.
- Ask me whenever a step needs knowledge you do not have: which joins are real, whether a
  proposed relationship is true (section 8), which questions to ask (sections 9-10).
- Keep the run's files under artifacts/ (git ignores it).

Write the report described in docs/LIVE_TEST.md section 13 to artifacts/live-test-report.md,
with one subsection per section of the guide, and mark every step PASS, FAIL or SKIPPED with
the reason. At the end, list every failure and every traceback first.
```
