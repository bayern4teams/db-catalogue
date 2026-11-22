# Report — move db-catalogue to the app machine (2026-11-22)

Branch `feat/app-machine` on both remotes (`github` = GitHub, `origin` = GitLab at
git.personal.com). Not merged into `main`, no tag pushed — waiting for the owner's `merge it`
and for the first release tag after the app-machine pass (decision 7).

## What changed

- Plain HTTP on the private LAN is an opt-in: `DBIG_ALLOW_PLAIN_LAN=1` with a private bind
  and no TLS; the default still refuses it, and a public or all-interfaces bind is always
  refused (`packages/contracts/src/dbig_contracts/security.py`, `docs/adr/ADR-001-app-machine-plain-http.md`).
- Committed settings may name an environment variable `${NAME}`; the shared
  `load_toml_section` expands it and a missing name fails with the setting and the variable
  (`packages/contracts/src/dbig_contracts/errors.py`). Real values live only in git-ignored
  `deploy/.env`.
- OpenAPI (`/openapi.json`, `/docs`) is on on all five services; every route including those
  two stays behind the caller key and its scope.
- Deployment files: `deploy/compose.yaml` (the five services), `compose.sources.yaml`
  (ClickHouse and MongoDB collector replicas), `compose.langfuse.yaml` (self-hosted
  Langfuse 4.83.0 + ClickHouse/Postgres/Redis/MinIO, pinned), `deploy/.env.example`
  (TEST-NET placeholders), six committed TOML configs with slots, `secrets/` convention.
- Releases: tag `vX.Y.Z` on either remote runs the same gate
  (`scripts/check_release_version.py`), builds five images to GHCR + the GitLab registry,
  and publishes wheels + `SHA256SUMS` to the GitLab package registry.
- Docs: `docs/API.md` (generated from the live OpenAPI documents by
  `scripts/export_api_docs.py`; 17 routes), `docs/USER_MANUAL.md`, `docs/DEPLOY.md`, the
  bind rule in README/ARCHITECTURE/TECHNICAL_IMPLEMENTATION §17.6, and a shared-machine
  note in the normalization repo's `docs/DEPLOY.md` (branch `feat/app-machine-shared`,
  commit `fafb36c`, not merged).

## Measured tonight (laptop rehearsal, `docker stats`/`du`, 2026-11-22)

- `deploy/.env.example` loads through the real loader into valid configs for all five
  services and both replicas.
- Full suite: 1433 passed, 16 skipped.
- 7/7 services `healthy`; seven `/healthz` return 200; `openapi.json` is 401 without a key
  and 200 with one on all five; a builder key is refused on the requester route.
- A catalogue snapshot (656 fields, 10 tables) served real questions D1–D5, S1, C3 through
  `POST /v1/ask` against the live vLLM; citations re-checked against the index.
- Langfuse stack healthy at one point; `GET /api/public/health` returns
  `{"status":"OK","version":"4.83.0"}`. Measured: services 2.3 GiB (web 754, worker 582,
  ClickHouse 841, Redis 92, Postgres 33, MinIO 24 MiB); volumes 151 MiB; images ~1.5 GB.
  Five services under test load: 33–84 MiB each, capped at `DBIG_MEM_LIMIT`=1 GiB.
- Sizing for the app machine: 8 GiB memory, 40 GiB free disk for both projects (was 6/20).
- GitHub CI observed for the pushed branch: Rule checks and Docs checks green on the final
  commits (an earlier red run was markdownlint MD060 in the generated table; fixed at
  `e361fe3`). GitLab CI could not be observed — this laptop has no glab/CLI auth for
  git.personal.com (push itself works over SSH).

## Not checked

- Real collect from the bench databases (collect → assemble → reindex): the owner's login is
  still a laptop-only SSH key, the box's TLS is off while the collector requires it, and the
  owner rule was hands-off the other machines. The rehearsal used the October snapshot.
- Nothing ran on the app machine itself — it does not exist yet.
- The GitLab pipeline for this branch (see above).

## For the owner

- Rotate the model API key: it appeared in a pasted brief; the rehearsal uses it only in
  git-ignored `deploy/.env` / `secrets/`.
- Bench logins + TLS for the live collect (the first real run on the app machine).
- `merge it` when you have read the branch; tag `v0.1.0` after the app-machine pass.
- Open questions: should README/ARCHITECTURE link to `docs/USER_MANUAL.md` /
  `docs/DEPLOY.md` from the front page (J13 says the files own the topic; not linked yet);
  the Langfuse web UI currently binds `LAN_IP:3000` — confirm that is who you want to see it.
