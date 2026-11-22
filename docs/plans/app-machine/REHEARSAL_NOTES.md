# Rehearsal notes (laptop, 2026-11-22)

All numbers below are measured on this laptop (docker stats / du / wall-clock), not copied.
The rehearsal stack ran with LAN_IP=127.0.0.1 (loopback only), the same rule as the 2026-11-11 run.

## Services

- Images built from the clone: collectors 275 MB, catalog-builder 351 MB, requester 267 MB,
  planner 241 MB, contracts 208 MB (docker images sizes).
- 7 of 7 containers healthy; every /healthz answered 200 (ports 8441 to 8447).
- OpenAPI: 401 without a key, 200 with a key, on all five services (decision 16 holds).
- One real route per service: search 200 with hits; contracts validate/field {"valid":true};
  planner graph 200 with the snapshot digest; collector and builder answered their real
  NOT_FOUND codes; a builder-scoped key on the search route gave 401 (scope check).

## Model answers (the live vLLM machine, one HTTP client call to check it, then via the stack)

Catalogue: the 2026-10-28 pg+ch snapshot loaded as the current snapshot (fresh collect not
run tonight: the bench databases' TLS is off and the box refused my SSH; owner's skip ruling
stands). Grading like artifacts/model-questions.md: right = names the expected columns and
cites IDs that exist in the index (every citation was checked against the search index).

| Q | Question (from model-questions.md) | Result | Time |
| --- | --- | --- | --- |
| D1 | person's role in PostgreSQL | right (person.role, cited ids verified) | 13 s |
| D2 | company ticker + type | right (Company.companyTicker) | ~20 s |
| D3 | postal code of a location | right (location.postalcode) | 12 s |
| D4 | ClickHouse revenue columns | right (companyRevenue, companyRevenueIn000s, ...) | ~30 s |
| D5 | industry names stored where | right after timeout fix (first tries: MODEL_TIMEOUT) | 60 s |
| S1 | company -> industry names join path | right after retry (company, companyindustry, industryclassification, join columns named) | 27 s |
| C3 | list ClickHouse tables | right (the seven tables) | ~40 s |

- 7 of 7 answered right with every citation verified; 0 wrong, 0 refused.
- 3 of 7 first tries hit MODEL_TIMEOUT with the 20 s default -> deploy/config/requester.toml
  now sets timeout_seconds=60 (b48fd25).
- The earlier Oct-2026 run of the same questions could not reach a model at all
  (MODEL_UNAVAILABLE_OR_INVALID in live-test-report.md), so this is the first measured pass.

## Langfuse (4.83.0, compose.langfuse.yaml)

- All six containers healthy; web answers /api/public/health {"status":"OK"}.
- Memory in use (docker stats): web 754 MiB, worker 582 MiB, clickhouse 841 MiB,
  postgres 33 MiB, minio 83 MiB, redis 9 MiB - total about 2.3 GiB.
- Disk after first boot (du of its volumes): postgres 68 MB, clickhouse 81 MB + 2 MB logs,
  minio 0.2 MB, redis 32 KB - total about 151 MiB.
- Images: langfuse 1.06 GB + worker 821 MB + clickhouse 1.13 GB + postgres 645 MB +
  minio 236 MB + redis 170 MB.
- Failure found and fixed: LANGFUSE_*_PASSWORD values go inside DATABASE_URL; base64 (the
  usual advice) carries + and / and Prisma refuses the URL (P1013, web crash-looped). Hex
  passwords work; rotating one also needs ALTER USER inside postgres (P1000 otherwise).
  .env.example now says hex (b48fd25).

## Not checked

- collect against the live bench databases (TLS off on the box; owner adds logins).
- Fresh-collect answer quality (the answers above replay the October snapshot).
- app-machine itself, the (unused) LAN firewall, GitHub/GitLab release runs on a real tag
  (no tag is allowed tonight, decision 7).
