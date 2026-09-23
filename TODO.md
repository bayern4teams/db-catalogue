# Live implementation ledger

Update this file when work starts, a fact changes, a decision is needed, a check runs, or work finishes. Do not batch updates at the end. Use `- [ ]`, `- [x]`, or `- [~]` (in progress); add a short evidence link/command with every completion. Ask the user about any question, suggestion, or debt as soon as it is discovered. All verified changes are committed. This ledger is a plan, not a claim of implemented runtime capability.

## 0. Repository and workflow foundation

- [x] Create Git repository on `main` in this folder. Evidence: `git init -b main` on 2026-09-23.
- [x] Record architecture research, local-LLM decision, and source links. Evidence: `ARCHITECTURE_RESEARCH.md`.
- [x] Ask whether to commit source-code graph caches. Decision: regenerate locally/CI; do not commit.
- [x] Ask whether to make first commit. Decision: yes.
- [x] Ask for GitHub remote. Decision: `https://github.com/Reza-Sh83/db-catalogue.git`.
- [x] Write README, binding implementation contract, agent instructions, local `.env` scaffold, hooks and CI baseline. Evidence: first commit `f165266` after local policy and three-tool graph checks.
- [x] Configure `origin` and inspect GitHub auth/repository. Evidence: `git remote -v`, `gh auth status`, `gh repo view`; `Reza-Sh83/db-catalogue` is private and Actions are enabled.
- [x] Push `main` after first local commit and checks. Evidence: `git push -u origin main` at `9db735a`.
- [x] Inspect GitHub Actions result with `gh`. Evidence: [CI run 35871915777](https://github.com/Reza-Sh83/db-catalogue/actions/runs/35871915777) completed successfully: repository-policy, graph-index, application-quality (planning-stage gate), and ci-success all passed.
- [x] Check branch protection/ruleset availability. GitHub returned HTTP 403: current plan requires Pro or a public repository. User chose to keep this repository private; enforcement is local hooks plus visible CI results, not a server-side merge block.
- [~] GitHub warned that older workflow actions use deprecated Node 20. User approved an update; pinned reviewed Node 24 action releases by commit SHA. Rerun CI after committing/pushing.
- [x] Install Ponytail 4.10.0 from its official Codex marketplace. Evidence: `codex plugin marketplace add DietrichGebert/ponytail` and `codex plugin add ponytail@ponytail`; a new Codex session/restart is required to expose its skills here.
- [ ] Verify Ponytail skill availability in a new agent session. This running session read all six installed SKILL.md files and applies their checks manually; it has not invoked newly loaded slash skills.
- [x] No `gh` skill is installed in this Codex session; user confirmed there is no specific skill to install. Use authenticated `gh` CLI directly.

## 1. Critical product decisions still open

- [ ] Obtain exact assembler GPU model, VRAM and RAM. Current answer only says “gpu model”; choose Qwen size/quantization after benchmark.
- [ ] Agree supported PostgreSQL, ClickHouse and MongoDB server versions and authentication modes.
- [ ] Agree per-source sample/query/byte/time limits and permissible metadata/history views.
- [ ] Define collector-to-assembler bundle transport, authentication, signing and retention.
- [ ] Define shared HMAC key distribution and rotation for permitted cross-source value comparisons.
- [ ] Define classification of SQL text, descriptions and sampled values; redaction and logging policy.
- [ ] Identify actual observed SQL and pipeline event sources and OpenLineage input availability.
- [ ] Approve labeled evaluation corpus and numerical precision/recall/coverage/latency thresholds after baseline measurement.

## 2. Package skeleton and dependency governance

- [ ] Create `pyproject.toml` with Python 3.12+ and extras for PostgreSQL, ClickHouse, MongoDB, LLM and OpenLineage.
- [ ] Pin exact app dependencies with `uv.lock`; inspect all transitive licenses, vulnerabilities and platform wheels.
- [ ] Set Ruff, type checker, pytest and coverage configuration with strict settings justified by code.
- [ ] Produce SBOM and a repeatable license report from the lockfile.
- [ ] Add reproducible wheel build and command smoke test for Windows and Linux.

## 3. IR, validation and bundles

- [ ] Finalize IR v1 JSON Schemas and typed Pydantic models, including source/object/field/evidence/edge/run/diagnostic.
- [ ] Define stable IDs, rename semantics, canonical JSON encoding and migration policy.
- [ ] Implement atomic JSONL bundle writer and strict manifest digest/count/path validation.
- [ ] Add optional bundle signature contract and security tests for malformed/tampered/oversized input.
- [ ] Define graph output schema, evidence references and `explain-edge` command.
- [ ] Create synthetic golden bundles and compatibility contract tests.

## 4. Collector common runtime

- [ ] Implement validated TOML config, environment override precedence and runtime secret references.
- [ ] Implement collector Protocol, scopes, budgets, timeouts, retries and typed diagnostics.
- [ ] Implement safe sample handling and no-raw-value artifact/logging tests.
- [ ] Implement source snapshot identity and partial-scan semantics.

## 5. PostgreSQL collector

- [ ] Discover databases/schemas/relations/columns/types, nullability, indexes, constraints and composite FKs.
- [ ] Capture view/materialized-view definitions with provenance and permissions diagnostics.
- [ ] Add bounded statistics/sampling with TLS and read-only integration tests.
- [ ] Test supported versions, partitions, quoted identifiers, row-level permissions and failed scans.

## 6. ClickHouse collector

- [ ] Discover `system.*` metadata, engines, distributed/partition/sort semantics and materialized views.
- [ ] Capture permitted query-log observations with redaction and retention limits.
- [ ] Add bounded statistics/sampling and ClickHouse integration tests.
- [ ] Test schema changes, cluster/shard cases and insufficient permissions.

## 7. MongoDB collector

- [ ] Discover databases/collections/indexes/validators/options and safe stats.
- [ ] Flatten bounded document paths with arrays, missing/null and type unions.
- [ ] Capture observed aggregation/job metadata where configured.
- [ ] Test nested/path-depth cases, BSON types, permissions and sample budgets.

## 8. Profiles and relationship inference

- [ ] Implement type-aware normalization and approved HMAC tokenization; never persist raw values.
- [ ] Implement candidate blocking, top-K budget, lexical/type/key/uniqueness evidence and counterevidence.
- [ ] Implement same-source and cross-source containment/overlap metrics with exact denominators.
- [ ] Implement composite candidate support, edge direction rules and conservative abstention.
- [ ] Add labeled positive/negative fixtures and compare recall/precision/abstention by source type.
- [ ] Add calibrated probabilities only after sufficient held-out labeled data; otherwise keep `confidence = null`.

## 9. Observed lineage

- [ ] Implement SQLGlot parser wrapper and catalog-aware resolver for configured dialects.
- [ ] Distinguish defined view lineage, executed writes, observed joins and unresolved parse cases.
- [ ] Import OpenLineage run/job/dataset/column facets with strict source mapping.
- [ ] Test CTEs, aliases, stars, INSERT SELECT, CTAS, materialized views, unsupported SQL and Mongo pipelines.
- [ ] Prove no inferred relationship is mislabeled as lineage.

## 10. Central assembler and local LLM

- [ ] Implement SQLite state, idempotent bundle receipts, incremental invalidation and partial-source semantics.
- [ ] Implement deterministic graph writer with stable sort, evidence integrity and validation.
- [ ] Add Ollama structured-output reviewer for ambiguous candidates only, enabled in v1 deployment.
- [ ] Validate/redact model input and output; ensure timeout or server failure does not prevent graph publication.
- [ ] Pin evaluated Qwen model artifact/digest after GPU benchmark; record prompt/schema versions.
- [ ] Compare deterministic-only baseline against LLM reviewer on held-out examples and report cost/latency.

## 11. Security, operations and release

- [ ] Threat-model collector privileges, bundle transfer, prompt injection, unsafe SQL, secret leakage and replay.
- [ ] Add secret scanning and dependency/license gates to CI once lockfile exists.
- [ ] Add integration services in CI or an authorized test environment; run least-privilege tests.
- [ ] Benchmark 10 sources/200 objects/1,000 fields, source load, memory, bundle size and LLM p95.
- [ ] Define deployment target and approval path; create CD only after package and target are real.
- [ ] Publish release notes, checksums, SBOM and provenance for a tested release.

## 12. Debt and suggestions ledger

- [ ] No accepted implementation debt yet. Add each debt item here with owner, reason, risk, due milestone and user decision; ask immediately when discovered.
- [ ] Assess whether Graft/Graphify/CBM tool versions should be pinned exactly in CI and developer bootstrap after first working run.
- [ ] Revisit optional Valentine, embeddings, vLLM, and agent framework only against measured v1 gaps; request approval before changing the chosen architecture.

## 13. Verification record

- [x] Hook path installed (`git config --local --get core.hooksPath` -> `.githooks`); `python scripts/graph_sync.py --check-tools` found all tools.
- [x] Manual three-tool graph refresh passed outside filesystem sandbox on 2026-09-23. Graphify 114 nodes/125 edges, Graft check OK, CBM 152 nodes/206 edges. CBM CLI cannot create its coordination endpoint inside the restricted sandbox; actual local hooks/CI run with normal local permissions.
- [x] `python scripts/ci_check.py`, `python -m compileall -q scripts`, and `git diff --check` passed locally.
- [x] Pre-commit and post-commit graph refresh both passed on first commit `f165266`: Graphify 114 nodes/125 edges; Graft check OK; CBM 154 nodes/208 edges.
- [x] CI repository-policy and graph-index passed at [run 35871915777](https://github.com/Reza-Sh83/db-catalogue/actions/runs/35871915777). Application quality job passed its planning-stage path because `src/` and `pyproject.toml` do not exist yet; this is not a runtime test result.
- [ ] Git status clean after final verification update/commit/push, excluding ignored `.env` and caches.
