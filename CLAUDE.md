# Claude Code project instructions

Read [AGENTS.md](AGENTS.md), [TECHNICAL_IMPLEMENTATION.md](TECHNICAL_IMPLEMENTATION.md), and [TODO.md](TODO.md) at task start. All rules in AGENTS.md apply here, including the full **Codebase Intelligence Policy** in this priority: **Graphify → Graft → Codebase Memory MCP → raw source tools**. Query an existing graph before broad search, refresh only if stale, prefer structural modes, and do not query later tools when an earlier one suffices. Read specific files to verify graph claims.

`AGENTS.md` is the only canonical agent policy; do not create a second singular `AGENT.md`.

Keep TODO.md live: mark the item in progress before changing files and update it at each discovery, decision, block, and completion, including mid-task. Hands on TECHNICAL_IMPLEMENTATION.md: verify commands, APIs, versions, package behavior and decisions; never guess. Ask the user whenever there is a question, suggestion, debt, missing evidence or a behavior-changing choice, even mid-task. If you discover a better approach, explain it and get approval before switching. Continue unrelated work while waiting. Every completed verified change must be committed.

Install and run the versioned Git hooks with `python scripts/install_hooks.py`. Pre-commit must refresh **all three** source graphs before commit; post-commit refreshes again. Do not bypass failed hooks. Do not commit generated graph caches or `.env`. Never hardcode credentials, endpoints, paths, models, limits or thresholds in application logic; use validated config and secret references.

Use `gh` and an installed `gh` skill when available. Before remote work, inspect auth and repository with `gh auth status` and `gh repo view`; after pushing, inspect actual Actions runs with `gh run list`, `gh run view`, and `gh run watch`. Fix a failed run before calling CI green. Do not assume a workflow file proves that CI ran.

Use the [Ponytail](https://github.com/DietrichGebert/ponytail) plugin's complete workflow when installed: `/ponytail [lite | full | ultra | off]`, `/ponytail-review`, `/ponytail-audit`, `/ponytail-debt`, `/ponytail-gain`, and `/ponytail-help`. Default to `full`; use `gain` only with measurements and keep debt in TODO.md. If a command is unavailable, state that and apply its review criteria manually. Ponytail must never reduce required safety, tests, error handling, or security.

Implement the approved v1 reviewer with private vLLM on Linux or WSL2 GPU, a fixed LangGraph workflow, and self-hosted Langfuse metadata-only spans. Do not install top-level LangChain or LangGraph Swarm without demonstrated need and user approval. Never send raw prompts, responses, sample values, SQL literals or secrets to traces. Test masking before enabling automatic LangGraph callback visualization; deterministic graph publication must survive model or telemetry failure. Keep `.env` local and ignored.
