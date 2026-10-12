# Lane D report: C3 points (1)-(4)

Files edited: docs/LOCAL_RUN.md, docs/LIVE_TEST.md, .env.example (comments only). No branch change, no commit.
Note: the lead's commit c34cdc9 already swept my docs/LOCAL_RUN.md edits in; LIVE_TEST.md and .env.example are uncommitted.

## Done
1. ClickHouse SELECT: LOCAL_RUN step 2 now says SHOW is enough for collect, `GRANT SELECT ON mydb.* TO dbig_reader` is needed for scripts/run_plan.py (code 497 otherwise) and why; LIVE_TEST sections 2 and 9 point to it. The text says the GRANT is not covered by the step's verification claim.
2. Malformed .env line: LOCAL_RUN step 4 has an awk command (no script added) that prints "line N: NAME: reason", never a value. Verified by extracting it from the md and running it on: unclosed quote (shell reports the last line; command reports the right one), unquoted space (shell names the word after the space; command names the variable), CRLF, a valid `'\''` escape (no false positive), and `.env.example` (no output). LIVE_TEST section 3 and a .env.example comment point to it. Tested with mawk 1.3.4 only.
3. .env.example: DBIG_SIGNING_KEY comment says REQUIRED and quotes the real messages (verified: unset -> "is not set", empty -> "is empty", by running `dbig collect`). Note: a key under 32 bytes ends in a ValueError traceback, not a one-line message (observed; outside my files; lead may want a ticket).
4. SQL checks: LIVE_TEST section 0 says psql/clickhouse-client/mongosh are optional; section 3 defines helpers pgq / chq / mongoq / mongoprobe using pg8000, clickhouse_connect, pymongo (same options as scripts/run_plan.py, env var CHECK_CA_FILE chosen to avoid the DBIG_<SETTING> override namespace). Sections 2 and 7 reference them. LOCAL_RUN step 7 says scripts/run_plan.py needs none of the clients.

## Not written (by instruction)
Point (5), the llama.cpp compatibility note: left out until B1/C1 are accepted.

## Checks run
- uv run --no-sync pytest tests/test_examples.py -q: 6 passed
- python scripts/ci_check.py: "Repository policy checks passed."
- Doc helper block extracted from the md and run: all four functions reach the driver connect call (connection refused to 127.0.0.1:1, no secrets printed).

## Could not verify (needs LOCAL_VERIFY.md)
- `GRANT SELECT ON mydb.* TO dbig_reader;` and that run_plan.py then succeeds / fails with 497 without it: no ClickHouse server, docker daemon not running.
- pgq / chq / mongoq / mongoprobe against a real server (rows returned, TLS with a company CA, the refusal messages for writes). Only the code path up to connecting was run.
- The pre-existing psql/mongosh statements in LIVE_TEST sections 2 and 7 were not changed and not run (no server).
- awk command under gawk / BSD awk (only mawk available).
