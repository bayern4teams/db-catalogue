# H1 group (c) attack report: B2 / DEC-10, E1, C1 + D7

Branch feat/catalogue-fixes at fd583ee (working tree src untouched by this review). Checks for B2 and C1/D7 were
derived from artifacts/engineer-fixes.md (B2, C1, D7), plan section 9 and the HANDOFF Decisions text before the
implementation was opened. For E1 (not in engineer-fixes.md) the checks come from the HANDOFF row; I had seen the
`key_from_env` helper in a grep before writing the E1 file, so E1 is derived from the row plus the CLIs, not blind.

Attack files (not collected by default; Python 3.12; run by path with `uv run --python 3.12 pytest <file> -q -p no:cacheprovider`):
- `tests/catalogue_fixes/attack3_c_b2_batches.py` (61 tests, all pass)
- `tests/catalogue_fixes/attack3_c_b2_telemetry.py` (1 test, passes; Langfuse down does not lose batches)
- `tests/catalogue_fixes/attack3_c_c1_codes.py` (39 tests, all pass)
- `tests/catalogue_fixes/attack3_c_e1_key.py` (46 tests, 37 pass, 9 fail = defects D-1, D-2, D-3 below)
Repo suites run too: tests/test_reviewer.py, test_reviewer_http.py, test_operator_errors.py, catalogue_fixes/test_dec10_batches.py, test_c1_error_codes.py: 96 passed.

## Verdicts
- **B2 / DEC-10: ACCEPT.** Counts 0,1,4,5,6,9,10,11,15,16,20 with default 5: requests = ceil(N/5), every candidate exactly once, full batches first;
  batch_size 1, 3, 20 and larger than N behave; `max_candidates` caps before batching (13 cand, cap 7 -> [5,2]; 25 cand default cap -> 4x5);
  config rejects 0 and negatives; TOML `[builder.review] batch_size = 3` is honoured through `assemble`. Failure kinds (400, length, garbage, invented citation,
  timeout) at batch 1, 2, 3 of 12 and batch 2 of 4 (size 1): exactly that many requests are sent (no later batch), earlier decisions kept (suggestion count = earlier batch
  sizes), snapshot `partial`, reasons contain `MODEL_REVIEW_FAILED` plus the failing batch's own code, failing batch adds nothing. Timeout is per call
  (3 x 0.8 s with timeout 1 s completes). Same input gives the same batch order; batches are strictly sequential. An answer naming a candidate outside its batch is
  `MODEL_INVALID_CITATION` (partial, earlier 5 kept). Old behaviour negative: default now 3 calls for 12 candidates (old: 1).
- **C1 + D7: ACCEPT.** In both `ask` and the builder review: closed port -> MODEL_UNREACHABLE, 400 -> MODEL_REJECTED_REQUEST, delay > timeout_seconds -> MODEL_TIMEOUT,
  finish_reason length (empty or half JSON) -> MODEL_TRUNCATED, unknown cited id -> MODEL_INVALID_CITATION; the five are five distinct codes in each caller; the old umbrella
  is not emitted for any of them; a timeout is not unreachable and vice versa; a server that accepts and drops the connection is not TIMEOUT. Builder `partial_reasons` is sorted, unique, holds
  `MODEL_REVIEW_FAILED` and the specific code. Error dict is `{code}` only; canary in question, response body, invented ids, key, endpoint URL never appears in the result, run record,
  manifest, stdout or this project's own loggers.
- **E1: REJECT until D-1 is fixed** (the scan-signing half of the stated scope). Collect, token key and builder hold: 31/32/33 bytes ASCII, 2-byte (16 chars = 32 bytes ok, 15+1 = 31 bytes refused),
  3-byte (11 chars = 33 ok, 10+1 = 31 refused), 4-byte (8 chars = 32 ok); one-line `Error: ...` output naming the variable (DBIG signing var for collect/builder, the token var for the token key), "at least 32 bytes",
  never the key; no traceback; no half-written bundle on a short token key; unset/empty/whitespace key still names the variable.

## Defects (file:line)
- **D-1 (E1, blocks ACCEPT).** Scan signing error does not name the variable: output is `Error: scan signing key must hold at least 32 bytes; generate a longer one`.
  `packages/contracts/src/dbig_contracts/scan.py:71-72` (`sign_scan` raises with a fixed text and a literal `32`, not `MIN_KEY_BYTES`); reached from `packages/requester/src/dbig_requester/scan.py:64-66`
  (`request-scan`) and `packages/collectors/src/dbig_collectors/scan.py:40-42` (`accept-scan`, `process-scans`, `serve`), which use `secret_from_env(...).encode()` instead of `key_from_env`.
  Tests: `request_scan_signing_key_boundary[ascii31 / two_byte_15_plus_1]`, `collector_accept_scan_with_a_short_key_names_the_variable[x2]`. Fix: call `key_from_env` in both callers (also removes the second literal 32).
- **D-2 (E1, low).** A key value that is not valid UTF-8 (Linux env bytes reach Python as lone surrogates) makes `.encode("utf-8")` raise `UnicodeEncodeError`, a traceback, not the one-line error:
  `security.py:87` (collect, token key, builder via `config.py:86`), `requester/scan.py:66`, `collectors/scan.py:42`. Tests: `*_undecodable_*` (4). Fix: catch `UnicodeEncodeError` in `key_from_env` and raise the same `SecretReferenceError` naming the variable.
- **D-3 (outside the E1 list, low).** `dbig-collector validate-bundle --key-env VAR` with a short key uses `secret_from_env` (`collectors/cli.py:53-54`): ValueError traceback `invalid artifact signature`, not the key-length message. Test: `validate_bundle_with_a_short_key`.

## Risks (not blocking; not part of the written acceptance)
- R-1 (C1/D7): HTTP 408 and 429 map to MODEL_REJECTED_REQUEST (`reviewer.py:158`, `answer.py:80`: any status < 500); 503 (llama.cpp "loading" or "all slots busy") maps to the old umbrella MODEL_UNAVAILABLE_OR_INVALID.
  On a busy shared server the operator can be told to "fix the request" for a queue-full 429. The report names only the client timeout for D7, so this is as specified.
- R-2 (B2): a batch reply that omits a candidate (or answers fewer than asked) is accepted: status OK, completeness complete, that candidate silently unreviewed (`reviewer.py:254-263` only rejects too many, duplicates and foreign ids). Probable pre-existing; a duplicate decision gives the umbrella code, not INVALID_CITATION.
- R-3 (B2/docs): `batch_size` is not in `examples/local/builder-with-vllm.toml` or `docs/LOCAL_RUN.md` (only TECHNICAL_IMPLEMENTATION.md line 191); C3 promises a "smaller batches" note for llama.cpp operators.
- R-4: with the root logger at DEBUG the `openai`/`httpcore` libraries log the full request body (prompt, question) themselves (seen in pytest caplog). Outside this project's loggers (`dbig*` loggers are clean), but a DEBUG-level operator would get prompts in a log.
- R-5: batch loop builds a new client, LangGraph and a Langfuse span per batch (`reviewer.py:191-310`); behaviour is correct (a down Langfuse did not lose decisions, 12 candidates reviewed in 3 s), cost is per-batch overhead only.
- R-6 (plan 2.4): `contracts/scan.py:71` has a second literal `32` next to the named `MIN_KEY_BYTES`. §2.4 corpus grep over `packages/*/src`: 0 hits. New numbers: `batch_size` default 5 in the validated `ReviewConfig` (allowed), `MIN_KEY_BYTES` named.

## Mutation proof (scratch copy of HEAD in `.tmp/h1c/mut`, PYTHONPATH import verified to come from the scratch tree; working tree untouched). Harness: `.tmp/h1c/mutate.py <name>`, results `.tmp/h1c/mutation-results.txt`. "New failures" = failures beyond the unmutated baseline (E1 baseline = the 9 defect tests).
| mutation | new failures |
|---|---|
| b2: do not stop after a failing batch | 18 |
| b2: discard earlier decisions on failure | 15 |
| b2: single call (old behaviour) | 34 |
| b2: overlapping slice (+1) | 32 |
| b2: default 4 | 9 |
| b2: ignore config, hardcode 5 | 10 |
| c1: no timeout branch, builder / ask | 2 / 3 |
| c1: no truncation check, builder / ask | 3 / 5 |
| c1: any 4xx to umbrella, builder / ask | 2 / 7 |
| c1: invalid citation to umbrella, builder / ask | 2 / 3 |
| c1: no unreachable branch, builder | 2 |
| c1: builder drops MODEL_REVIEW_FAILED / drops specific code / everything umbrella | 6 / 6 / 7 |
| e1: no length check | 12 |
| e1: characters instead of bytes | 6 |
| e1: message without variable name | 9 |
| e1: collect back to old path | 5 |
| e1: builder back to old path | 3 |
| e1: PostgreSQL token key back to old path | 4 |
Negative on old code: against the `2e87fa7` packages b2 fails 39/61, c1 20/39, e1 20/46; against `9dff5a6^` (before B2 and E1, after C1) b2 fails 39, e1 20, c1 passes 39 (C1 predates it).

## Implemented, unverified (needs live data)
- A real reasoning model on vLLM or llama.cpp: that batch 5 with `max_tokens` 512 finishes (the report's 5 candidates / 24000 tokens / 14.9 s was with a larger budget), i.e. B2's live check "21 candidates complete with the shipped example config".
- `MODEL_TIMEOUT` against a genuinely busy shared server (stub delay only); a connect timeout on a black-holed host (classified by `ConnectTimeout` class name; not reproducible offline).
- Mongo token key (`runtime.py:900`) not re-attacked here; PostgreSQL path (`runtime.py:195`) was. 429/503 behaviour of real servers.
