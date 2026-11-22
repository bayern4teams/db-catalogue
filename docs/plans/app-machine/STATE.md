# STATE - move db-catalogue to app-machine (branch feat/app-machine)

Ledger. Newest first. Commits carry their verification; D1/D4 per AGENTS.md.

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
- NEXT: USER_MANUAL.md + DEPLOY.md (in progress now, docs agents); normalization branch
  (its own repo, not merged); J13 stamps; STATE/REPORT finish; section 5 run; push both
  remotes; watch CI.
- BLOCKED for the owner: bench logins + TLS, live collect, tag v0.1.0 (only after
  app-machine passes, decision 7), model key rotation (it appeared in a pasted brief).
