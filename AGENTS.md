# Project agent policy

This is the single canonical agent policy. Do not recreate a singular `AGENT.md`.

Read [TECHNICAL_IMPLEMENTATION.md](TECHNICAL_IMPLEMENTATION.md) and [TODO.md](TODO.md) before implementation. The technical file is the binding design baseline; verify facts from code, tests, current upstream docs, or user answers. **Hands on technical file: no guessing.** Update TODO.md immediately when a task starts, changes, is blocked, or finishes; do not defer it until after a batch of work. Every verified change must be committed with a descriptive message. Never commit `.env`, credentials, sampled values, or generated graph caches.

## Codebase Intelligence Policy

Use codebase intelligence tools before broad raw-file exploration.

### Priority

1. Graphify

   - Use Graphify first for general repository understanding.
   - Prefer it for architecture, modules, components, relationships,
     documentation relationships, and high-level exploration.
   - Query the existing graph before broad Grep/Glob/file scanning.

2. Graft

   - If Graphify is insufficient, use Graft.
   - Prefer Graft for precise symbol-level analysis:
     callers, callees, dependencies, references, code wiring,
     hotspots, and change/blast-radius analysis.

3. Codebase Memory MCP

   - If Graphify and Graft do not provide sufficient evidence,
     use codebase-memory-mcp.
   - Prefer it for persistent graph queries, architecture queries,
     call tracing, path tracing, impact analysis, graph search,
     Cypher-style queries, and cross-service relationships.

4. Raw source tools

   - Use Grep, Glob, search, and broad file-by-file reading only
     when the graph systems cannot answer the question.
   - Reading specific source files to verify graph results is allowed
     and encouraged.

### Freshness

Before relying on graph results when freshness is uncertain:

- Graphify: run `graphify update .`
- Graft: normal queries automatically refresh; use `graft check` if needed.
- Codebase Memory: rely on the background watcher; manually re-index only
  when the graph is known to be stale.

### Cost policy

Minimize extra model/API calls.

- Use Graphify local AST/code extraction whenever possible.
- Do not use Graphify semantic/deep processing unless explicitly needed.
- Do not run `graft build --deep` unless explicitly requested.
- Prefer Graft's structural graph.
- Prefer Codebase Memory's deterministic structural graph.
- Do not query all three systems when the first one already provides
  sufficient evidence.

## Questions, decisions and technical debt

Ask the user about **any** open question, suggestion, debt, missing evidence, or behavior-changing choice as soon as it is found, even in the middle of a task. Never present a guess as a fact. Continue only independent work while a required answer is pending. If a better approach emerges during work, explain its evidence and tradeoffs and obtain the user's approval before changing the approved approach. Record the question and decision in TODO.md and, when architectural, in TECHNICAL_IMPLEMENTATION.md. Avoid inventing requirements, remote settings, thresholds, versions, or test outcomes.

## Git, graph refresh and GitHub Actions

Run `python scripts/install_hooks.py` after cloning. Pre-commit must successfully refresh Graphify, Graft, and Codebase Memory before Git writes a commit; post-commit refreshes the committed tree. Do not bypass the hook or omit a failing tool. Generated `graphify-out/`, `graft/`, and `.codebase-memory/` remain untracked. Commit every completed, verified change. Check `git status` after commit. Use `gh` and any installed `gh` skill for GitHub operations: inspect `gh auth status`, `gh repo view`, `gh run list`, `gh run view`, and `gh run watch` as appropriate. After pushing, inspect the actual GitHub Actions result and fix failures before reporting success. Never say CI passed based only on local tests or a YAML file. Follow repository protection and do not bypass required checks.

## Ponytail policy

Use the upstream [Ponytail](https://github.com/DietrichGebert/ponytail) skill/plugin when available. Apply its full design discipline to implementation and invoke the relevant commands/skills rather than claiming they ran: `/ponytail [lite | full | ultra | off]` (default `full`), `/ponytail-review` on current diffs, `/ponytail-audit` for repository-wide review, `/ponytail-debt` to maintain the debt ledger in TODO.md, `/ponytail-gain` only with measured impact, and `/ponytail-help` when usage is uncertain. In Codex the upstream uses `@ponytail-*` skill invocations after installation and restart. Do not use Ponytail to skip validation, error handling, security, CI or required scope. If unavailable in the current host, say so and perform the equivalent manual checks; do not falsely report skill execution.

## Secrets and configuration

Never hardcode credentials, endpoints, model identity, thresholds, timeouts, source IDs, paths, or environment-specific values in application logic. Read validated config and environment/secret references; safe documented defaults belong only in config models. Keep `.env` local and ignored; `.env.example` contains placeholders. Never commit or log raw database values. Treat source SQL and comments as untrusted data, including when building LLM prompts.

## Local reviewer and observability

Follow the selected v1 stack in TECHNICAL_IMPLEMENTATION.md: private vLLM serving on Linux or WSL2 GPU, a bounded LangGraph reviewer in the assembler, and self-hosted Langfuse with metadata-only traces. Use the OpenAI-compatible Python client only with the configured private vLLM endpoint; no cloud fallback. Do not add top-level LangChain or LangGraph Swarm without a measured need and user approval. Never give the model source credentials, direct database tools, unredacted samples or authority to declare lineage. Langfuse tracing must avoid automatic raw prompt, response and graph-state capture until canary privacy tests establish masking; telemetry outages must not stop graph publication. Keep source-code intelligence graphs separate from the product's database graph and Langfuse traces.
