# STATE - move db-catalogue to app-machine (branch feat/app-machine)

Ledger. Newest first. Commits carry their verification; D1/D4 per AGENTS.md.

- 2026-11-22 97f1955 green docs gate: em-dash/quote/terms fixes, planner route summary
  (Vale.Terms flagged the auto-title), API.md regenerated live; markdownlint 0, Vale 0
  errors, lychee 0 on all 13 docs; planner rebuilt + healthy, openapi tests pass.
  REPORT.md written.
- 2026-11-22 bece6fc USER_MANUAL.md + DEPLOY.md committed (docs agents; 401/403 and the
  7-service health facts cross-checked against the code).
- 2026-11-22 447be71 J13 doc updates (bind rule, build_images/release text, verified stamps).
- 2026-11-22 pushed to both remotes (github + origin/GitLab; branch only, no merge, no tag).
  GitHub: Rule checks green on every push; Docs checks observed. CI full suite dispatched
  on the branch (run 44997692714). GitLab CI not observable from this laptop (no glab auth
  for git.personal.com; push over SSH works).
- 2026-11-22 normalization repo: branch feat/app-machine-shared, commit fafb36c
  (DEPLOY.md shared-machine note: 8 GiB / 40 GiB for both projects), not merged.
- 2026-11-22 5faee2f docs/API.md generated from the five live OpenAPI documents
  (scripts/export_api_docs.py; 17 route sections; ruff clean).
- 2026-11-22 b48fd25 rehearsal fixes: hex Langfuse passwords in .env.example (P1013 was
  measured), requester timeout_seconds=60 (MODEL_TIMEOUT measured; 60 s answered all).
- 2026-11-22 561d80f release pipelines both remotes (five images, wheels, checksums;
  check_release_version gate; actionlint + zizmor clean).
- 2026-11-22 7fb39bb deployment files: .env.example, compose.yaml + compose.sources.yaml +
  compose.langfuse.yaml, config slots; compose config OK for all three; T1 kept, new
  companion-file test added (none changed).
- 2026-11-22 ea2f60c plain-HTTP LAN opt-in + ${NAME} settings + OpenAPI on all five;
  42 targeted tests, mutation-checked; ADR-001.
- BLOCKED for the owner: bench logins + TLS, live collect, tag v0.1.0 (only after
  app-machine passes, decision 7), model key rotation (it appeared in a pasted brief).
