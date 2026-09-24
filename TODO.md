# Live implementation ledger

Update this file when work starts, a fact changes, a decision is needed, a check runs, or work finishes. Do not batch updates at the end. Use `- [ ]`, `- [x]`, or `- [~]` (in progress); add a short evidence link/command with every completion. Ask the user about any question, suggestion, or debt as soon as it is discovered. All verified changes are committed. This ledger is a plan, not a claim of implemented runtime capability.

## Current change: v1 implementation

- [~] Implement the approved v1 in dependency order, beginning with package boundaries and versioned artifact contracts. Evidence: user request on 2026-09-23; existing repository is design-only (`git status --short --branch`, Graphify query).
- [x] User delegated previously open implementation choices on 2026-09-23. Conservative local profile and measurement boundaries are recorded in `TECHNICAL_IMPLEMENTATION.md` §16; no live source or GPU facts are assumed.
- [x] Build the first `dbig-contracts` increment: IR boundary models, stable IDs, signed atomic JSONL artifacts, published JSON Schemas, and malformed/compatibility tests. Evidence: `uv lock`, `uv run pytest` (4 passed), `uv run mypy packages` (passed), `uv build --package dbig-contracts` (wheel and sdist built), `uv run ruff check .` (passed), and `uv run pip-audit` (no known vulnerabilities after upgrading pytest from 8.4.2 to 9.1.1). CI inspection follows commit/push.
- [x] Contract commit `b21489f` passed all pre-commit graphs; its immediate post-commit Codebase Memory pass met a transient concurrent index, then an explicit full `python scripts/graph_sync.py` passed. Pushed to `main`; [CI run 35903235369](https://github.com/Reza-Sh83/db-catalogue/actions/runs/35903235369) completed with repository-policy, graph-index, application-quality, and ci-success all successful.
- [~] Collector package now has signed source-local bundle publication, validated config, read-only PostgreSQL catalog discovery with PK/unique/FK metadata, ClickHouse UUID/column/engine discovery, and MongoDB UUID/index discovery with optional type-only sampling. Evidence: `uv run pytest` (9 passed), `uv run ruff check .`, `uv run mypy packages`, `uv build --package dbig-collectors`, and no known vulnerabilities from `pip-audit`. Driver APIs were checked against official pg8000, ClickHouse Connect, and PyMongo documentation. Live database integration, profiles, observations, richer schema cases, and version matrix remain open.
- [x] Harden required graph hook for a transient Codebase Memory concurrent-index race: retry the same fast index up to three times, then fail closed. Evidence: the collector pre-commit hook retried once and passed all three tools; an explicit committed-tree `python scripts/graph_sync.py` passed.
- [x] Collector increment `e0e4a46` pushed; [CI run 35904351409](https://github.com/Reza-Sh83/db-catalogue/actions/runs/35904351409) passed repository-policy, application-quality, graph-index and ci-success. This does not establish live DB compatibility.
- [x] Implement central builder increment: signed intake, typed constraint resolution, separate declared and tentative edges with evidence, and sealed snapshot publication. Evidence: `uv run pytest` (16 passed), `uv run mypy packages`, `uv run ruff check .`, `uv build --package dbig-catalog-builder`, and `pip-audit` (no known vulnerabilities). Live connector and incremental-state tests remain open.
- [x] Add bounded LangGraph review of ambiguous candidates through a private vLLM URL with cited-ID validation, metadata-only Langfuse spans, and deterministic publication on model outage. Evidence: synthetic fake-client, bad-citation, outage, URL, and trace tests included in the 16-test suite. Actual GPU/model JSON conformance and self-hosted Langfuse deployment remain unverified.
- [x] Implement catalogue requester search, grounded private-model answers, signed scheduler/HTTP scan dispatch, CLI and REST; add collector-side queue, scope/load/replay checks. Evidence: `uv run pytest -q` (23 passed), Ruff, mypy, both wheel builds, policy check and `pip-audit` passed on 2026-09-23. Live scheduler, TLS proxy, model and source operations still need deployment verification.
- [~] Implement separate lineage/path planner with observed SQL, pipeline and OpenLineage intake, candidate paths, query rendering, CLI and REST. Evidence: `uv run pytest -q` (29 passed), Ruff/mypy, and Windows clean-wheel/API/CLI isolation passed. Exact OpenLineage facet mapping, full SQL resolution, Linux wheel result, and live input sources remain open.
- [x] Requester and scan increment `7947435` pushed; [CI run 35908200102](https://github.com/Reza-Sh83/db-catalogue/actions/runs/35908200102) completed with repository-policy, graph-index, application-quality and ci-success successful. This verifies synthetic behavior, not live deployment.
- [x] Enrich source-local metadata: PostgreSQL view-definition digest, ClickHouse engine/key-expression digests and dependencies, MongoDB validator/index fields and type-only per-source row/byte bounded sampling. Evidence: official PostgreSQL/ClickHouse/MongoDB catalog docs, `uv run pytest -q` (32 passed), Ruff, mypy, collector wheel build, `pip-audit` and repository policy check. Raw SQL literals, key expressions and sample values are absent from bundles in synthetic privacy tests. Docker Desktop daemon is unavailable locally (`docker version` cannot connect); local GPU is GTX 1650 with 4 GiB VRAM, and WSL distro enumeration returned access denied. Live integration/model deployment remain unverified.
- [x] Planner increment `874e97d` pushed; [CI run 35910217065](https://github.com/Reza-Sh83/db-catalogue/actions/runs/35910217065) passed repository-policy, application-quality, graph-index, Linux and Windows clean-wheel isolation, and ci-success. It verifies package boundaries and synthetic behavior, not live source/model deployment.
- [x] Extend deterministic builder to use MongoDB named unique constraints as candidate-key evidence and verify cross-source candidate inference with negative fixtures. Evidence: `uv run pytest -q` (33 passed), Ruff, mypy, builder wheel, dependency audit and policy check. Cross-source links remain tentative, with no value comparison or false lineage claim.
- [x] Collector metadata increment `d76d5ab` pushed; [CI run 35910935940](https://github.com/Reza-Sh83/db-catalogue/actions/runs/35910935940) passed repository-policy, application-quality, graph-index, both package-isolation jobs and ci-success. No live DB test ran.
- [x] Add Linux CI service integration for PostgreSQL 16.15, ClickHouse 25.8.33.6 and MongoDB 7.0.41 metadata discovery using synthetic schemas. [CI run 35911699728](https://github.com/Reza-Sh83/db-catalogue/actions/runs/35911699728) completed successfully; source-integration log shows `3 passed in 1.01s`, and all other jobs plus ci-success passed. Local `uv run pytest -q` passed 33 synthetic tests and skipped the 3 service tests because Docker is unavailable. The CI services use ephemeral fixture auth, so production TLS/auth and version matrix beyond these versions remain open.
- [x] User decision on 2026-09-23: configure private vLLM interfaces now, retain synthetic answer/reviewer/privacy tests, and defer live GPU/model and Langfuse deployment tests until the user reports an accessible GPU host. Do not treat skipped live LLM tests as passed or select a model digest before benchmarking.
- [x] Documentation decision commit `29b0169` pushed; [CI run 35912165912](https://github.com/Reza-Sh83/db-catalogue/actions/runs/35912165912) passed all jobs, including the three live source metadata fixtures.
- [x] Strengthen snapshot referential validation for node types, field ownership, source IDs, evidence targets, edge field pairs and run references. Six negative publication fixtures plus builder tests pass; full local suite: 39 passed, 3 service tests skipped without Docker. Ruff and mypy passed. Commit and CI inspection follow.
- [x] Snapshot validation commit `625e671` pushed; [CI run 35912590146](https://github.com/Reza-Sh83/db-catalogue/actions/runs/35912590146) passed repository policy, graph indexing, application quality, source integration, Linux/Windows wheel isolation and ci-success.
- [x] Cross-source candidate increment `c785adb` pushed; [CI run 35911329411](https://github.com/Reza-Sh83/db-catalogue/actions/runs/35911329411) passed repository-policy, application-quality, graph-index, both clean-wheel jobs and ci-success. Live source integration was not yet in that run.
- [x] Builder increment `e3d2f51` pushed; [CI run 35905976696](https://github.com/Reza-Sh83/db-catalogue/actions/runs/35905976696) passed repository-policy, graph-index, application-quality, and ci-success. Live vLLM and Langfuse deployment remain untested.

## Current change: local agent framework decision

- [x] Remove duplicate `AGENT.md` and keep the complete policy in `AGENTS.md`; update README/CLAUDE.md and repository check. Evidence: `AGENT.md` deleted, `scripts/ci_check.py` rejects its return, local policy check passed.
- [x] Research vLLM, LangGraph, LangChain, LangGraph Swarm and Langfuse from official documentation; update README, implementation contract, research, CLAUDE.md, AGENTS.md and this ledger consistently. Evidence: upstream links and decision analysis in `ARCHITECTURE_RESEARCH.md` §§19, 26, 29–30.
- [x] Use only justified v1 modules: vLLM serving, LangGraph bounded reviewer, Langfuse self-hosted telemetry; defer top-level LangChain and LangGraph Swarm. Evidence: user decision on 2026-09-23; upstream project documentation is linked in `ARCHITECTURE_RESEARCH.md`.
- [x] Keep agent telemetry inside the environment and serve the GPU model on Linux or WSL2. Evidence: user decisions on 2026-09-23.
- [x] Run repository policy and graph checks, commit all changes, push, and inspect the actual GitHub Actions result. Evidence: local `python scripts/ci_check.py`, `git diff --check`, Python compile check and both three-tool hooks passed; commit `5d46f51` pushed; [CI run 35875953671](https://github.com/Reza-Sh83/db-catalogue/actions/runs/35875953671) passed repository-policy, graph-index, application-quality planning gate and ci-success.

## Current change: Amundsen/OpenLineage and independent module boundaries

- [x] Review Amundsen and OpenLineage architecture, packages, maintenance and licenses. Evidence: official upstream links and comparison in `ARCHITECTURE_RESEARCH.md` §31; Amundsen repository says archived 2026-09-10, OpenLineage remains the event contract reference.
- [x] Define independent requester/planner behavior and deployment form from user answers, without substituting guessed behavior. Evidence: `TECHNICAL_IMPLEMENTATION.md` §17 records both package/CLI and HTTP REST boundaries, search and local-LLM Q&A, scan dispatch adapters, observed lineage and candidate joins, SQL/Mongo plan rendering, no execution, and logical cross-source steps with unresolved staging. The user chose architecture and TODO before package skeletons.
- [x] Update technical, research, README, agent policy and this ledger; run local checks, commit, push and inspect GitHub Actions. Evidence: `python scripts/ci_check.py` and `git diff --check` passed; Ponytail full/review/audit found no application-code complexity to cut and debt scan found zero `ponytail:` comment markers; both three-tool Git hooks passed; commit `d42ff6c` pushed; [CI run 35899250817](https://github.com/Reza-Sh83/db-catalogue/actions/runs/35899250817) passed repository-policy, graph-index, application-quality planning gate and ci-success. This ledger completion is a separate follow-up commit.

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
- [x] Replace deprecated Node 20 workflow actions with reviewed Node 24 releases pinned by commit SHA. User approved. Evidence: [CI run 35872465691](https://github.com/Reza-Sh83/db-catalogue/actions/runs/35872465691) passed all four jobs after commit `b634042`.
- [x] Install Ponytail 4.10.0 from its official Codex marketplace. Evidence: `codex plugin marketplace add DietrichGebert/ponytail` and `codex plugin add ponytail@ponytail`; the resumed session exposes its six skills.
- [x] Verify Ponytail skill availability in a new agent session. Evidence: all six Ponytail skills are listed in the resumed session on 2026-09-23; full-mode design, diff review, repository audit and debt scan were applied to this planning repository.
- [x] Apply Ponytail full/review/audit/debt checks to this planning repository: no speculative runtime code, no added app dependency, no `ponytail:` code markers and no justified complexity cuts. Gain/help guidance was read; no per-repository gain is claimed without a baseline.
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

- [ ] Create a `uv` workspace and separately installable `dbig-contracts`, `dbig-collectors`, `dbig-catalog-builder`, `dbig-catalogue-requester`, and `dbig-lineage-planner` distributions. Verify final names against package indexes. Keep source-driver and HTTP API extras scoped to owners.
- [x] Build all five wheels and install requester/planner separately on Windows and Linux; prove no mutual imports or source-driver dependencies. Evidence: local Windows `python scripts/package_isolation.py`; [CI run 35910217065](https://github.com/Reza-Sh83/db-catalogue/actions/runs/35910217065) package-isolation-windows-latest and package-isolation-ubuntu-latest passed.
- [x] Verify the five intended `dbig-*` distribution names against PyPI JSON endpoints on 2026-09-23; all returned HTTP 404 (unoccupied at check time). Recheck before any publication.
- [ ] Pin exact app dependencies with `uv.lock`; inspect all transitive licenses, vulnerabilities and platform wheels.
- [ ] Set Ruff, type checker, pytest and coverage configuration with strict settings justified by code.
- [x] Produce CycloneDX SBOM and repeatable license reports for all 113 lockfile components. `python scripts/supply_chain.py --refresh` fetched pinned PyPI release metadata and verified the sole missing declaration, `mypy-extensions==1.1.0`, against its tagged upstream LICENSE; `python scripts/supply_chain.py` reports zero unresolved declarations and rejects stale inventory. CI gate added; actual CI result pending. This is metadata inventory, not a legal review of every license term.
- [x] User chose MIT for the repository and five dbig packages on 2026-09-23. Root and package LICENSE files and PEP 639 metadata are added; all five built wheels contain `License-Expression: MIT` and the license text. Local full suite: 39 passed, 3 service tests skipped without Docker; Ruff, mypy, repository policy, SBOM/license check, all-wheel build, and clean requester/planner wheel isolation passed. CI pending.
- [x] MIT/SBOM increment `7f61168` pushed; [CI run 35975753471](https://github.com/Reza-Sh83/db-catalogue/actions/runs/35975753471) passed repository-policy, graph-index, application-quality including the supply-chain gate, source-integration, Linux/Windows wheel isolation and ci-success.
- [x] Add SQLite signed-bundle receipts and source-level incremental publication. Replays leave receipt count unchanged; partial rescans preserve the last complete source graph and mark the snapshot partial; a later complete scan reconciles removals. CLI can rebuild from recorded bundles; mixed-source bundles are rejected. Evidence: 43 local tests passed, 3 service tests skipped without Docker; Ruff, mypy, builder wheel and policy check passed. Commit/CI pending.
- [x] Incremental builder commit `16d0bce` pushed; [CI run 35976508859](https://github.com/Reza-Sh83/db-catalogue/actions/runs/35976508859) passed all jobs, including live source fixtures and both platform wheel checks.
- [x] Map exact OpenLineage Job, Dataset and Column facet input/output and field edges; expose bounded field lineage paths in CLI/API, distinguish defined JobEvent paths from observed completed RunEvents, and refuse plans from field lineage. Evidence: 48 local tests passed, 3 live-source tests skipped without Docker; Ruff, mypy, planner wheel and policy checks passed. Facet precedence and private transformation-description omission are covered by synthetic fixtures. Commit/CI pending.
- [x] OpenLineage facet commit `859a249` pushed; [CI run 35978051058](https://github.com/Reza-Sh83/db-catalogue/actions/runs/35978051058) passed all jobs, including Linux/Windows clean-wheel isolation and live source metadata tests.
- [x] Expand conservative SQL observation parsing to read-only CTE/subquery sources, schema-qualified names, aliases, CTAS and materialized views without `TO`; reject nested writes and ClickHouse `TO` views until output semantics are modeled. Evidence: SQLGlot AST behavior inspected locally; 54 local tests passed, 3 service tests skipped without Docker; Ruff, mypy, planner wheel and policy checks passed. CI pending.
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

- [~] Implement SQLGlot parser wrapper and catalog-aware resolver for configured dialects. PostgreSQL/ClickHouse direct writes, read-only CTE/subquery sources, qualified names and direct alias joins pass synthetic tests; SQL-derived columns, ClickHouse `TO` materialized-view semantics and three-part names remain unresolved.
- [ ] Distinguish defined view lineage, executed writes, observed joins and unresolved parse cases.
- [ ] Import OpenLineage run/job/dataset/column facets with strict source mapping.
- [~] Test CTEs, aliases, stars, INSERT SELECT, CTAS, materialized views, unsupported SQL and Mongo pipelines. Synthetic CTE, nested-read, qualified-name, alias, CTAS, PostgreSQL/ClickHouse materialized-view and nested-write fixtures pass; richer dialect/column and live source cases remain open.
- [ ] Prove no inferred relationship is mislabeled as lineage.
- [~] Map OpenLineage RunEvent/JobEvent and lineage/column facets conservatively; test multi-input/multi-output ambiguity and unresolved dataset names. Explicit job, dataset and column facets now map exact object/field edges with official precedence and field-path API support; JobEvent paths are `defined`, completed RunEvent paths `observed`, and unsupported/missing mappings abstain. SQL-derived columns, job-to-job facets and complete real producer compatibility remain open.

## 9a. Independent catalogue requester

- [x] Build snapshot validation and SQLite FTS5 index with digest/freshness checks, structured filters, pagination and stable ordering. Evidence: requester search tests in 23-test increment `7947435`.
- [x] Implement grounded natural-language catalogue Q&A against private vLLM with validated cited object IDs, redaction, budgets and model-outage behavior. Evidence: fake-client citation/outage/telemetry tests in increment `7947435`; live vLLM remains open.
- [x] Define scan-request schema and authorization, expiry, idempotency, signature, replay and receipt/status contracts. Evidence: schemas and signature/replay tests in increment `7947435`.
- [x] Implement signed scheduler-bundle and private collector-HTTP scan dispatch adapters with collector-side scope/load checks. Evidence: synthetic adapter and queue tests in increment `7947435`; production handoff/TLS remains open.
- [x] Provide requester CLI and optional FastAPI/Uvicorn HTTP JSON REST endpoints on the same application functions. Evidence: API tests and wheel CLI smoke in increment `7947435`.
- [ ] Determine the actual scheduler interface, HTTP authentication, key management and retention with deployment owners before production implementation.

## 9b. Independent lineage and path planner

- [~] Build separate observed-lineage and reference-candidate graph views with evidence/freshness and configured path limits. Initial derived graph and bounded paths implemented; complete column facets and contrary evidence remain open.
- [~] Implement lineage-path and candidate join-path CLI/API contracts with clear unresolved and partial statuses. Synthetic paths pass; richer ambiguity and ranking checks remain open.
- [~] Generate reviewed, read-only, dialect-specific SQL segments using SQLGlot ASTs and validated catalog identifiers; never execute generated queries. Initial PostgreSQL/ClickHouse SELECT AST renderer passes synthetic tests; user predicates and dialect matrix remain open.
- [~] Generate validated MongoDB aggregation pipeline JSON for Mongo segments. Initial bounded `$lookup` and `$limit` planner passes synthetic tests; full pipeline schema and live server remain open.
- [x] Return ordered cross-source source-side reads and logical transfer/join steps; mark staging/final execution unresolved until an approved target is configured. Evidence: synthetic mixed PG/Mongo test in planner suite.
- [~] Provide optional FastAPI/Uvicorn HTTP JSON REST endpoints and clean-wheel/CLI/API/contract-isolation tests. Windows clean wheel, CLI, API and no-import check passed; Linux CI pending.

## 10. Central assembler and local LLM

- [~] Implement SQLite state, idempotent bundle receipts, incremental invalidation and partial-source semantics. Source-level receipts, replay handling and conservative fallback to the last complete bundle pass synthetic tests; field/profile-level cache invalidation, historical edge timestamps and retention remain open.
- [~] Implement deterministic graph writer with stable sort, evidence integrity and validation. Referential checks were strengthened and six negative contract fixtures pass; deterministic serialization across repeated runs and further evidence integrity checks remain open.
- [ ] Add bounded LangGraph reviewer for ambiguous candidates only: redaction, vLLM request, response validation and weak-evidence conversion; enable in v1 deployment.
- [ ] Validate/redact model input and output; ensure timeout or server failure does not prevent graph publication.
- [ ] Deploy vLLM on a Linux or WSL2 GPU host behind a protected private endpoint; validate GPU support, model fit, structured-output contract and no cloud fallback. Explicitly deferred by user until GPU host access is provided; synthetic model tests continue.
- [ ] Deploy self-hosted Langfuse with isolated web/worker, PostgreSQL, ClickHouse, Redis/Valkey and object storage; configure retention, access controls and private network path. Live deployment verification deferred with unavailable local Docker/GPU host; metadata-only SDK tests continue.
- [ ] Instrument manual Langfuse metadata-only spans; prove canary secrets, raw values, SQL literals, prompt text and model outputs never enter traces. Test telemetry outage and bounded queue behavior.
- [ ] Consider LangGraph callback/agent graph view only after masking/privacy tests pass; do not auto-capture state or messages by default.
- [ ] Pin evaluated Qwen model artifact/digest after GPU benchmark; record prompt/schema versions.
- [ ] Compare deterministic-only baseline against LLM reviewer on held-out examples and report cost/latency.

## 11. Security, operations and release

- [ ] Threat-model collector privileges, bundle transfer, prompt injection, unsafe SQL, secret leakage and replay.
- [ ] Add secret scanning and dependency/license gates to CI once lockfile exists.
- [ ] Add integration services in CI or an authorized test environment; run least-privilege tests.
- [ ] Add requester/planner API contract, wheel-isolation, authorization, prompt-privacy and no-query-execution gates as code appears.
- [ ] Benchmark 10 sources/200 objects/1,000 fields, source load, memory, bundle size and LLM p95.
- [ ] Define deployment target and approval path; create CD only after package and target are real.
- [ ] Publish release notes, checksums, SBOM and provenance for a tested release.

## 12. Debt and suggestions ledger

- [ ] No accepted implementation debt yet. Add each debt item here with owner, reason, risk, due milestone and user decision; ask immediately when discovered.
- [ ] Assess whether Graft/Graphify/CBM tool versions should be pinned exactly in CI and developer bootstrap after first working run.
- [ ] Revisit optional Valentine, embeddings, top-level LangChain and LangGraph Swarm only against measured v1 gaps; request approval before changing the chosen architecture.

## 13. Verification record

- [x] Hook path installed (`git config --local --get core.hooksPath` -> `.githooks`); `python scripts/graph_sync.py --check-tools` found all tools.
- [x] Manual three-tool graph refresh passed outside filesystem sandbox on 2026-09-23. Graphify 114 nodes/125 edges, Graft check OK, CBM 152 nodes/206 edges. CBM CLI cannot create its coordination endpoint inside the restricted sandbox; actual local hooks/CI run with normal local permissions.
- [x] `python scripts/ci_check.py`, `python -m compileall -q scripts`, and `git diff --check` passed locally.
- [x] Pre-commit and post-commit graph refresh both passed on first commit `f165266`: Graphify 114 nodes/125 edges; Graft check OK; CBM 154 nodes/208 edges.
- [x] CI repository-policy and graph-index passed at [run 35871915777](https://github.com/Reza-Sh83/db-catalogue/actions/runs/35871915777). Application quality job passed its planning-stage path because `src/` and `pyproject.toml` do not exist yet; this is not a runtime test result.
- [x] `git status --short --branch` showed a clean tracked tree after commit `b634042`, before this final verification entry; `git check-ignore` confirmed `.env`, Graphify and Graft caches are ignored. Recheck after the final commit/push.
- [x] Architecture update verification: [CI run 35875953671](https://github.com/Reza-Sh83/db-catalogue/actions/runs/35875953671) passed all four jobs at commit `5d46f51`; `gh run view` inspected job conclusions. Ponytail diff review and whole-repo audit found no justified complexity cuts in this planning-stage tree; `rg -n '(#|//)\s*ponytail:'` found zero debt markers. `/ponytail-gain` has no per-repository baseline to claim.
