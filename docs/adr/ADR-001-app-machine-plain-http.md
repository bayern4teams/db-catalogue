# ADR: run db-catalogue on app-machine as plain-HTTP Docker services

Date: 2026-11-21. Source: the owner's chat, relayed in the night-run brief. Status: accepted.
All decisions below are the owner's; the words column is quoted from the brief.

| # | Decision | Owner's words (from the brief) |
| --- | --- | --- |
| 1 | Run on app-machine; its IP stays a placeholder `<app-machine address>` in every file. | quoted in brief |
| 2 | No LAN firewall for now; `deploy/lan_firewall.py` and `deploy/dbig-firewall.service` stay, docs say unused. | "for now we don't want these. because we run it in our local network" |
| 3 | Answers use the existing private vLLM machine; address, model and key are settings. | "app should be dynamic about its address and model and api key not static" |
| 4 | Self-hosted Langfuse on app-machine in the deployment; traces stay metadata only. | brief |
| 5 | The owner adds read-only bench logins to `.env`; placeholders in `.env.example` only. | brief |
| 6 | OpenAPI on for all five services; a caller guide too. | brief |
| 7 | Release v0.1.0 only after the app-machine deployment passes its checks. | brief |
| 8 | Clone on app-machine; versions by git tag; pull tagged images or clone and build. | brief |
| 9 | GitLab is inside the LAN. | brief |
| 10 | Release on both remotes: wheels, checksums, five images; GitLab Python package registry; both private. | brief |
| 11 | Plain settings in one file `deploy/.env`; keys and secrets stay files in `deploy/secrets/`. | brief |
| 12 | Plain HTTP to the vLLM machine. | "we don't need https for now" |
| 13 | A changed setting applies with `docker compose up -d` (restart), no live reload. | brief |
| 14 | Plain HTTP for the five services on the LAN too. | brief |
| 15 | Both plain-HTTP exceptions carry the C5 note: reason, owner approval 2026-11-21, review date 2027-05-21. | brief |
| 16 | Keep the per-caller keys and scopes (ServiceGuard). | brief |
| 17 | Start empty on app-machine and collect again; laptop `dbig_*` volumes are not moved and not deleted. | brief |
| 18 | Write `docs/USER_MANUAL.md`, `docs/DEPLOY.md`, `docs/API.md`. | brief |
| 19 | Update normalization's `docs/DEPLOY.md` too: combined memory and disk needs in both guides. | brief |

## What changes in the code, and what does not

- Plain HTTP becomes an explicit setting (`DBIG_ALLOW_PLAIN_LAN`, see `docs/plans/app-machine/STATE.md`):
  the compose sets it; nothing else does. `bind_refusal`, `serve_refusal` and `private_base_url`
  keep refusing plain HTTP by default, so every existing test passes unchanged (L1).
- Database TLS policy is untouched (owner decision D2 of 2026-11-10 stays in force).
- The LAN firewall files stay in the repo, unused for now (decision 2; deleting needs a yes, B2).
- Per-caller keys and scopes stay required on every route, OpenAPI endpoints included (decision 16).

## Consequences

- Two C5 exceptions (the plain-HTTP bind and the plain-HTTP vLLM call) carry
  `reason=LAN-only deployment; approved=owner, 2026-11-21; review=2027-05-21` next to the code.
- Moving settings from `deploy/config/*.toml` to `deploy/.env` keeps the Pydantic validators;
  only where the values are read changes (decision 11).
- Langfuse runs in its own compose file (`deploy/compose.langfuse.yaml`), because
  `tests/test_repository_policy.py::test_the_shipped_compose_file_keeps_the_lan_policy` pins
  `deploy/compose.yaml` to exactly the five product services (T1, unchanged).
