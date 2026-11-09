# Improvement report: research progress

Updated 2026-11-09. All parts of `docs/improvement-report.md` are written and pushed. Next: answer implementer questions and keep the report current; D1-D8, H4 and H5 await the owner. The sections below are the evidence record and are superseded by the report where they differ.

Stopped on 2026-11-09 because the 5-hour usage reached 83% (limit 80%). Branch `docs/improvement-report`, cut from `main` at 3ff51f3 (`git ls-remote origin refs/heads/main` showed 3ff51f3).

## Done

- Base confirmed and branch cut.
- Format example outline read (`/home/bayern/Projects/normalization/docs/improvement-report.md`: status header, table of contents, §0 constraints table, Part 1 defects, Part 2 research, Part 3 architecture, Part 4 appendices, recommended sequence).
- Part 1 evidence gathered by two subagents; both finished, none running. Their findings are copied below in full, so neither needs a re-run.

## Rechecked directly by the lead (I4)

- `.github/workflows/release.yml`: `test -d src` and plain `uv build` are present.
- `.github/workflows/docs.yml`: the `files=` list leaves out the other `docs/*.md` files.
- `packages/requester/src/dbig_requester/answer.py`: the correction retry covers only `_unexplained`; `INVALID_CITATION, "none"` raises with no retry (R2).
- No `getLogger("openai|httpx|httpcore")`, `logging.basicConfig` or `logging.DEBUG` in `packages` or `scripts` (T20).
- `scripts/ci_check.py`: the M1 check only looks at names without `/`, so `.handoff/` cannot fail it.
- `.gitlab-ci.yml` uses `docker:29.9.2-dind` and its header asks for a privileged runner.

Not rechecked by the lead: the rest of the subagent tables below, including the line 75 "28" in `artifacts/live-test-report.md` (the lead's grep output was cut before that word).

## Corrections to the task brief

- `TECHNICAL_IMPLEMENTATION.md`: the brief says §4-11, 13-16 lack a current stamp. Found: §5-11, 13, 16 have no stamp; §4, 15 have bce5fee; §14 has f30780c. Whether any stamp is current was not checked.
- The 92 Vale errors and their four files are not recorded in `TODO.md`. Vale was not run (`vale sync` needs network). Not checked.
- The GitLab `source-integration` dind failure is not recorded in the repo. Runner state: not checked.
- The 23 attack-test ids are not in the repo, and `TODO.md` also says 37. Not reconciled; needs a capped batch run.

## Next, in order

1. Write `docs/improvement-report.md`: status header, table of contents, §0 confirmed constraints (from the task brief and `service-research-plan` owner decisions of 2026-11-08), then Part 1 from the findings below. It does not exist yet.
2. Recheck the remaining pass/fail claims while writing.
3. Commit, push the branch, tell the lead "Part 1 complete", and stop until the lead answers.
4. Parts 2-4 only when the lead says so. No research for them has started.

## Open for the lead when Part 1 ends

Owner decisions already visible: T20 (accept the DEBUG text, or cap the three loggers where the client is built; recommend capping), B4 (sync the ponytail copies, or record the drift), B5 (keep `.handoff/`, or archive finished files; M1 today covers root files only), F13 (type-word filter; deferred earlier by the owner), R3 (retrieval limit, or a list mode).

---

# Subagent findings: TODO and prompt reports

# Part 1 audit at 3ff51f3 (read-only; grep and narrow reads only; tests not run)

481 TODO.md lines; 163 unticked/in-progress boxes (grep `^\s*- \[( |~)\]`). Grouped below.

## Specific items

| id | problem | status | evidence | smallest fix | verify |
|---|---|---|---|---|---|
| T20 | OpenAI SDK DEBUG log holds reply and question text when a bare reply is re-sent | OPEN, OWNER-DECISION | `packages/requester/src/dbig_requester/answer.py` lines ~336-343: on `_unexplained(parsed)` it appends `{"role":"assistant","content": choice.message.content}` plus `CORRECTION` to `messages` and calls `model_client.chat.completions.create` again (line ~319). Client built at line ~286 `OpenAI(...)`. `grep -rE 'getLogger\(.(openai\|httpx\|httpcore)\|setLevel' packages scripts` finds nothing: no openai/httpx logger level is set anywhere; only `logging.getLogger(__name__).warning` for telemetry (lines 364, 368). Test `tests/phase7/attack7_p7_4.py::test_debug_logs_bare_reply` exists on main, is informational (asserts only re-send). | Owner picks: accept (document in `docs/THREAT_MODEL.md`), or set `logging.getLogger("openai").setLevel(logging.WARNING)` and `httpx`, `httpcore` once in the one place that builds the client (`answer.py` client construction, ~line 286) and make the test assert the canaries are absent. | `pytest tests/phase7/attack7_p7_4.py::test_debug_logs_bare_reply -s` prints `False False` (run on a small batch only). |
| T21 | 23 attack tests fail on fix/phase7 | OPEN, unclassified | Attack files exist on main: `tests/phase7/attack7_p7_{1,2,3,4,5,5c,8}.py` and 16 more `tests/catalogue_fixes/attack*.py`. TODO.md never lists the 23 ids: it says "ids in the P7 ledger below" but the ledger lines (45-52, 63) only say "attack files the same 37 ids"; the ids were in `.tmp/phase7/attack-baseline.txt` (ignored, not in repo). Line 21 says 23, lines 45-102 say 37: the two numbers are not reconciled. Not checked: the actual failing ids (running them is a test run). | Re-run the attack files once in small batches, write the failing ids and a reason each into the TODO ledger (or `docs/plans/catalogue-fixes/`); reconcile 23 vs 37; fix or mark each per C5 (reason, approval, review date). | List of ids in TODO.md equals the batch output; count matches. |
| T32 | release.yml has `test -d src` and plain `uv build` | OPEN | `.github/workflows/release.yml` line 34 `test -d src`; line 49 `run: uv build`. Repo has no root `src/` (sources in `packages/*/src`). AGENTS.md section 5 uses `uv build --all-packages`. Job has never run. | Delete line 34; change line 49 to `uv build --all-packages`; add `python scripts/package_isolation.py` as in ci.yml if wanted. | `actionlint .github/workflows/release.yml`; dry run `uv build --all-packages` locally (heavy: ask first). |
| T34 | GitLab pipeline for 3941e8c not checked | OWNER-ACTION / NOT-CHECKED | `.gitlab-ci.yml` is tracked now (TODO line 55 still says "kept untracked": stale). Job `source-integration` (lines 74-135) uses service `docker:29.9.2-dind` with `DOCKER_HOST: tcp://docker:2375`; header says it needs "a privileged Linux Docker runner"; runner is tag-gated `tags: [bayern]`. TODO.md has no note on a dind privilege failure and I found no log of one: the "runner failure" is not recorded in the repo. Not checked: runner config (privileged = true), actual pipeline status. | Owner: set `privileged = true` for the runner in its config.toml (runner side, not repo), or use a socket-bind runner. Fix TODO line 55 wording (file is tracked). | `glab ci status` / pipeline shows `source-integration` green for both matrix legs. |
| B1 | docs.yml does not cover docs/*.md | OPEN | `.github/workflows/docs.yml` line 45: `files=$(ls README.md ARCHITECTURE.md TECHNICAL_IMPLEMENTATION.md GLOSSARY.md docs/LESSONS.md AGENTS.md ...)`. Not covered: `docs/LIVE_TEST.md`, `docs/LOCAL_RUN.md`, `docs/THREAT_MODEL.md`, `docs/ARCHITECTURE_RESEARCH.md`, `docs/plans/**`. The "92 Vale errors in four files" is not recorded in TODO.md (grep: the only "92" is the C5 noqa count, unrelated); the four files are probably the four docs above: not checked. `vale` is installed but `vale sync` (network) has not run here (error "Style 'Google' isn't on the StylesPath"), so I did not run it. | Change the `files=` line to a glob for `docs/*.md` after fixing the errors (or exclude research with a reason per C5); one list in `docs.yml`. | After `vale sync`: `vale docs/*.md` gives 0 errors; docs workflow green. |
| B2 | TECHNICAL_IMPLEMENTATION.md sections lack a current `verified` stamp | OPEN | Stamps found (`awk` on H2 + `verified:`): Package map 6e43d33; Module map 6e43d33; Workflows 6e43d33; Status life cycles 6e43d33; Key types 6e43d33; 1 bce5fee; 2 bce5fee; 3 bce5fee; 4 bce5fee; 12 6e43d33; 14 f30780c; 15 bce5fee; 17 bce5fee. NO stamp: 5, 6, 7, 8, 9, 10, 11, 13, 16 (9 H2s). TODO line 30 already lists 5-11, 13, 16 (and README License/Links, THREAT_MODEL, LIVE_TEST, LOCAL_RUN). No stamp is at HEAD 3ff51f3; "current" depends on per-section diff: not checked. | Verify each section against code, add `<!-- verified: <sha> -->` under each H2. Optional K4 check in `scripts/rule_check.py` (owner skipped it, commit 3ff51f3). | `awk '/^## /{h=$0} /verified:/{...}'` lists all 17 numbered + 5 unnumbered H2s. |
| B3 | live-test-report says 28 and 40 missing | OPEN (typo-level) | `artifacts/live-test-report.md` line 16 "40 confirmed relationships missing"; line 75 "also misses 28 relationships you confirmed as true"; lines 165, 307, 324 say 40. Line 165 breaks down 12 PG + 6 CH + 10 Mongo + 1 cross + 11 Mongo-CH = 40, and line 363 says 42 pairs, 40 true. So 40 is right; the 28 on line 75 matches no breakdown (40 minus the 12 PG = 28 is a guess, not checked). | Edit line 75 "28" to "40". | `grep -nE '\b(28\|40) relationships' artifacts/live-test-report.md` shows only 40. |
| B4 | tracked .claude/skills/ponytail* differ from v5.3.3 | OPEN, OWNER-DECISION | TODO.md lines 7 and 25: checkout `~/Tools/ponytail` is v5.3.3 plus one docs commit; project-local copies deliberately "left alone" (user: "3. leave them alone"). Checked: `diff -rq` shows `SKILL.md` differs in `ponytail`, `ponytail-audit`, `ponytail-review` (other 3 skills not compared; content of the differences not read). | Owner decision to sync: copy `~/Tools/ponytail/skills/<name>` over `.claude/skills/<name>` and update `.claude/skills/PONYTAIL_LICENSE` if changed; otherwise record as a known drift. | `diff -r` of each skill dir is empty. |
| B5 | `.handoff/` has 37 tracked files outside M1 | OPEN, OWNER-DECISION | `git ls-files .handoff \| wc -l` = 37. `scripts/ci_check.py` line 236: `stray = ... if "/" not in name and name not in ROOT_FILES`: only files directly in the root are checked, so any folder passes (dotfolders included); `.handoff` is not in ROOT_FILES and does not need to be. So CI cannot fail; M1 as written constrains root files, not folders. agent `catalogue-fixes-verifier` is told it may write `.handoff/`. | Owner decides: keep (document in TECHNICAL_IMPLEMENTATION.md layout) or `git mv` finished reports to `docs/archive/` (M3). If M1 should cover folders, add an allowed top-level-dir list next to `ROOT_FILES` in `ci_check.py`. | `python scripts/ci_check.py`; `git ls-files .handoff`. |

## Stale tickboxes (implemented in code; box still open), grouped

Checked by grep for existence only, not for full coverage of each sentence.
- T332 collector Protocol, scopes, budgets, timeouts, diagnostics: `packages/collectors/src/dbig_collectors/runtime.py::Budget`, `::BudgetExhausted`; `packages/contracts/src/dbig_contracts/models.py::Diagnostic`. No class named `Collector` Protocol found by grep: partly, not checked.
- T331 TOML config + secret refs: `packages/collectors/src/dbig_collectors/config.py`, `packages/*/src/*/config.py` exist; env precedence not checked.
- T338-T341, T345-T348, T352-T355 (PG, ClickHouse, Mongo discovery, sampling): `packages/collectors/src/dbig_collectors/runtime.py`; TODO line 204 and `tests/test_collectors.py`, `tests/test_live_*.py` exist. Sub-items (partitions, row-level permissions, cluster/shard) not checked.
- T313 IR v1 schemas/models: `packages/contracts/src/dbig_contracts/models.py`, `artifact.py`; `tests/test_artifact.py` validates published schema.
- T286/T287 uv.lock pinned, Ruff/mypy/pytest strict: `uv.lock` tracked, CI runs ruff, mypy, pytest (`.github/workflows/ci.yml`); L7 strict markers per AGENTS.md, not re-read.
- T383-T387 lineage/OpenLineage: `packages/planner/src/dbig_planner/observations.py` (sqlglot, OpenLineage), `graph.py`; TODO lines 382-387 already say partly done.
- T411, T412 bounded LangGraph reviewer + redaction: `packages/catalog-builder/src/dbig_builder/reviewer.py::review_candidates`, `_trace`; `tests/test_reviewer.py`. Live deployment items stay open.
- T373-T378 normalisation/HMAC/candidates/containment: `packages/catalog-builder/src/dbig_builder/builder.py` (`_composite_candidates`), `tests/test_value_evidence.py`, `tests/test_composite_candidates.py`, `tests/test_inference_*.py`. Calibration (T378) correctly open.
- T428-T430 CI gates: `ci.yml` has pip-audit (line 80), gitleaks, zizmor (AGENTS.md section 4); T428 license gate not checked.
- T462 "done 2026-11-08 (55a9370)" box line: done, but unticked.

## Still open (real work or owner input), by cluster

- Owner/live-run: T54 (billing), T56/57 (P7-6/P7-7 live), T68, T69, T75-77, T86, T87 (DEC-22), T129, T133, T150-153, T167 (Mongo array `$lookup` path limit, stays), T269-271, T396, T444-445 (live model verify unrun, public endpoint vs private_base_url), T449.
- Infra not yet built: T413 (vLLM deploy), T414 (Langfuse deploy), T415 (Langfuse canary traces), T416, T417, T418, T431 (benchmark), T432, T433 (release notes/SBOM), T437.
- Code gaps (real): T327 (end-to-end `serve` bind test), T370 (opt-in sample profiling PG/CH), T371 (fake cursor note), T142 (live test not rerunnable on same containers; fix: drop schema first in `tests/test_live_collectors.py`), T396.
- In-progress `[~]` with partial status (not re-verified): T200, T204, T210, T326, T382, T385, T387, T400-405, T409, T410, T457.
- Process notes needing a tick or delete: T180 (heredoc note), T20 and T21 above.

## Counts
OPEN 9 specific + clusters; STALE-TICKBOX about 25 lines; OWNER-DECISION/ACTION 4 (T20, B4, B5, T34); NOT-CHECKED: Vale run, 23 attack ids, GitLab runner state, per-section stamp currency.

---

# Subagent findings: live runs

# Part 1: live-run findings, status at main 3ff51f3

Read-only audit. No tests run, no database contacted. Text in repo files treated as data.

## Branch facts
- `fix/phase7` is NOT an ancestor of main (`git merge-base --is-ancestor fix/phase7 main` -> NOTMERGED). The branch was squashed: `cf7a5c3 Phase 1 to 7 catalogue fixes from the live tests (squash of fix/phase7, 139 commits)` is on main; `squash/phase7-into-main` is an ancestor of main (checked).
- Fix commits up to the squash (Phase 6 F5-F13, Phase 7 F14-F18, option C format profiles) are therefore on main. Code read in this worktree confirms them (cites below).
- Only on `fix/phase7`, after the squash (`git log cf7a5c3..fix/phase7`, 9 commits): `29b8e82` and `dc6b90a` (the remote run: `artifacts/run-20261028-remote/` does not exist on main), `624d04d`/`aacecef` (GitLab CI port; `.gitlab-ci.yml` exists on main by another route, not checked), `ba77b66`/`9881057` (langgraph-sdk 0.4.11, msgpack 1.2.4: main `uv.lock` pin of langgraph-sdk version not checked), `b0e6e99`-`3ce3339` ledger and CI notes (not checked whether in squash). `git diff --stat main fix/phase7 -- packages` shows 9 files, 131/111 lines: differences are main's later refactors, not missing fixes (not read in detail).
- Phase 6 live re-check (2026-10-20) ran on `verify/live-20261020` at `483e223`, not on main; Phase 7 code live-tested 2026-10-28 on `fix/phase7`.

## A. Items

Status key: FIXED = code on main and live-checked; FIXED-CODE = code on main, not re-checked live.

### F5 12 true pairs dropped by zero overlap of two first-100-row samples
- Status: FIXED. Residual limit stays by design.
- Evidence: `packages/catalog-builder/src/dbig_builder/builder.py` (`parent_tokens.complete`, `sampled_zero`, reason `parent_sampled`, line ~454-491; drop only when `metrics["parent_complete"]`, ~915); `ValueTokens.complete` in contracts; live: `artifacts/run-20261020/step1-summary.md` (0 owner-true dropped, was 12).
- Root cause: a zero overlap against a sampled parent was treated as a measurement.
- Fix done: record completeness per hash set; drop only against a complete parent; range comparison of capped sets; run settings 25,000 rows / 4,096 hashes (DEC-19).
- Residual: a ClickHouse parent is never read completely, so 18 false PG-to-CH candidates stay (labelled "parent sampled"). Upgrade path (LEAD_REVIEW): full ClickHouse column read for a sketch. Needs owner decision (cost).
- Verify: rerun step 1 on the same data, compare to `step1-candidates.json`.

### F6 7 of 16 wrong remain, 2 of 5 right dropped
- Status: FIXED by F5+F7 (no own fix, as set). PARTIAL for the 18 residual false pairs (see F5).
- Evidence: `step1-summary.md` 16 of 16 gone, 5 of 5 kept (1st live check: Gate 2 `run-20261014/gate2-summary.md` 9 of 16, 3 of 5).

### F7 ClickHouse produced no value hashes
- Status: FIXED. Evidence: `packages/collectors/src/dbig_collectors/runtime.py::_tokenize_clickhouse`; live 38 ClickHouse hash sets, 29 pairs with a number (`step1-summary.md`).
- Residual: only 3 of 38 sets complete (37-row `Industry` etc.); the rest sampled at 25,000 rows.

### F8 shipped review config times out
- Status: FIXED-CODE (review not re-run live after fix: not checked).
- Evidence: `packages/catalog-builder/src/dbig_builder/config.py::ReviewConfig.max_tokens` is `int | None = None`; `reviewer.py:248` sends `omit` when unset.
- Verify: run the review once against the private model with the example config; expect `complete`, review time near the 457 s of `LIVE_VERIFY_REPORT.md` Step 8.

### F9 probe reads up to 7.8x its row cap on ClickHouse
- Status: FIXED. Evidence: `runtime.py:1416` settings `max_threads: 1` with a `LIMIT` subquery; live 1.10x to 1.31x (`run-20261020/step2-summary.md`, measured by re-running the query shape; `system.query_log` closed to the reader).
- Note: still "about", not exact; `max_block_size` deliberately not set (LEAD_REVIEW).

### F10 probe makes false parents / ordinary names link; 3 true pairs lost on hash-order tie
- Status: FIXED. Evidence: `builder.py` `dropped_ordinary_name` (~801-862, DEC-20: an ordinary name links only to a declared key); tie-break at the top-k cut (P6-4); step 2: 3 pairs restored, no ordinary-name links.
- Residual: F15 (below) was the follow-on.

### F11 model joins PostgreSQL and ClickHouse tables
- Status: FIXED live. Evidence: step 3 summary ("No answer writes a PG-CH join as one query"); 638 of 638 relationships carry source and namespace; remote run N-C8, N-S3, N-S7 say not joinable in one query.
- Not: the F11-b rules (c_min 0.25, `min_expected_shared` 5, `catalog-builder/config.py` ~81-85) are code, effect not measured live beyond 2026-10-28 (3 cross-source candidates `unverified_cross_source`, 30 verified).

### F12 text says not_established while field says answered
- Status: FIXED. Evidence: `requester/answer.py` Answer `outcome` required; step 3: 0 mismatches (was 4).

### F13 search misses needed columns (44% of expected names reach the model)
- Status: PARTIAL. Retrieval 78% (104 of 134) after table expansion (`step3-summary.md`). Remaining: type questions (N-C4, N-C7 return `NO_CATALOGUE_MATCH` in both 2026-10-20 and 2026-10-28 runs, 0.56 s and 0.54 s) because FTS indexes only `name, context, description` (`requester/search.py` line ~175: `fts5(id UNINDEXED, name, context, description)`); option C (index type text) deferred by owner/lead because "text" would match 94 of 170 fields and N-C7 would return the nullable columns.
- Smallest correct general fix: not a tuned keyword list; map a question's type words to a `data_type` filter (`SearchRequest.data_type` exists, search.py ~131, ~402) before the FTS leg, and ask the owner first (decision exists: deferred).
- Verify: N-C4 and N-C7 return columns of the right type; recall replay with a stub model.

### F14 lower bound shown as a measurement ("1%" where real 25%)
- Status: FIXED-CODE (P7-1: "at least" wording, Wilson CI, `basis`, INDEX_VERSION 8). Live evidence 2026-10-28: N-I10 shows "value overlap at least 5 of 4038 sampled child values" (wording now a floor). Re-check against the true 25% not done.

### F15 tie-break ranks unmeasured weak key over measured 100% parent
- Status: FIXED-CODE (P7-2 offline replay restored 2 pairs; not re-run live: not checked). Cost noted in TODO: `_overlap` for all candidates, 1.0 s -> 4.5 s on 120 parents x 2000 tokens.

### F16 multi-hop path missing from context (N-S4, N-S8, N-I10)
- Status: PARTIAL. Code: `requester/search.py::bridge_paths` (path_max_hops 4, path_max_bridge_tables 4, `config.py:35`). Live 2026-10-28: N-S4 answered with a 4-table route; N-I10 (PersonContact -> Person -> Experience -> Company) still "not in catalogue", although the three edges were in the cited list. Cause not checked (retrieval vs. model not chaining). Note `bridge_paths` skips a path that passes through another retrieved table, relying on the two short edges alone.
- Fix direction: not checked; first read the recorded prompt for N-I10 (`answers-both.json` on fix/phase7) to see whether the Person-Experience edge was in the context. TODO P7-3 lead doubt: "needs 3 hops via ch.Person".

### F17 bare verdicts
- Status: FIXED-CODE. Evidence: `answer.py::_unexplained`, `ANSWER_UNEXPLAINED`, correction retry (answer.py ~335-343). Live 2026-10-28: N-I4, N-I5 verdicts carry an explanation (text read in REPORT); N-I2 explains. Run-20261020-pgch had 4 bare answers before the fix.

### F18 failure reason lost
- Status: FIXED-CODE. Evidence: `answer.py::_failure_reason` (keys `exception`, `schema_errors`, `finish_reason`, `reply_length`, `citation_check`).

### Remote run 2026-10-28 (source: `git show fix/phase7:artifacts/run-20261028-remote/REPORT.md`)

| id | problem | status | evidence | root cause | smallest general fix | verify |
|---|---|---|---|---|---|---|
| R1 | Diffbot bundle `partial`: 78 format profiles not read at default `max_queries = 50` | OPEN (workaround: 200 in run config) | `collectors/config.py:31` default 50; `runtime.py:709-710` message "format profiles not read: query budget reached" | fixed query budget does not scale with 235 column profiles; message gives no remedy (J11) | add the needed count and the config key to the diagnostic text; leave the default, or derive the default charge per profile in config docs (owner choice) | collect Diffbot with default; diagnostic names `max_queries` and a number |
| R2 | 4 answers refused `MODEL_INVALID_CITATION`, check `none` (N-D9, N-S8, N-S9, N-I3) | OPEN | `answer.py:349-351`: no citations and outcome not `not_established` raises with no retry; only `_unexplained` retries | model gives an `answered` reply with no citation; the one correction retry is not used for this case | route "answered with no citation" through the same correction retry (`CORRECTION` in `answer.py`) | unit test with a stub model returning no citations first, valid second |
| R3 | "List all" answer incomplete (N-C6 named 2 of 4 numeric columns) | OPEN, known limit | report 4a; `config.py:29 retrieval_limit = 10` | top-10 hits cut; table expansion helps only for retrieved tables | none without owner: raise limit or add a list mode (owner decision 1 keeps 10 as ranking cut) | recall replay with stub model |
| R4 | N-C4 and N-C7 `NO_CATALOGUE_MATCH` | OPEN, deferred by owner | see F13 | type words not indexed | see F13 | see F13 |
| R5 | N-C1 answer says metadata has one database though both were indexed | NOT-CHECKED (cause) / OPEN | REPORT answer N-C1 | likely top-10 hits all from pg-diffbot; not checked | a "both databases" question must retrieve per source (not checked how `answer.py` source scoping works) | replay retrieval for N-C1 |
| R6 | N-I10 not found; N-S4 took 62.71 s | PARTIAL | see F16 | see F16 | see F16 | see F16 |
| R7 | N-I2 `investorcompanyid` -> `company.companyid` not proposed | OPEN, limitation | answer N-I2; DEC-20 ordinary-name rule (name differs, no declared key) | name rule cannot link differing names | leave; owner may add an explicit decision file entry (F11-b mechanism, DEC-36) | not checked whether such a decision exists for this pair |
| R8 | `ch.Location.companyID` (`Nullable(Int64)`) -> `Company.companyID` (`String`) not proposed | OPEN, by design | not in REPORT candidate list; `run-20261020/step1-summary.md` | type-family rule | none without owner | n/a |
| R9 | Cross-source (PG<->CH) candidates stay `unverified_cross_source` (3), planner shows none by default | OPEN, by design | REPORT sections 2, 4 item 3, 5 | the two collections used different/unshared token keys, no overlapping hashes | collect both with one `DBIG_TOKEN_KEY` (config only, report section 5) | collect both with same key, check candidates carry a number |
| R10 | Questions written for older databases; 3 repeats and owner grading not done; P7-7 benchmark not done | OPEN (process) | REPORT section 5, TODO P7-6, P7-7 | run plan | owner grades `answers-both.json`; run `ask.py` 2 more times | n/a |
| R11 | Remote host password readable in a README | OUT OF SCOPE (environment) | REPORT 4d | credential in plain text on the host | owner rotates it | n/a |
| R12 | `unverified` semantic questions (N-I4, N-I5, N-I7, N-D6) answered "not in catalogue" | OPEN, by design | value-free catalogue has no descriptions; answers say so honestly | no source comments in these databases | none (C1) | n/a |

Tally (F5-F18 = 14 items, R1-R12 = 12 items; R6 repeats F16 and R4 repeats F13, so 24 distinct):
- FIXED live: F5, F7, F9, F10, F11, F12 (6)
- FIXED-CODE, not re-run live: F8, F14, F15, F17, F18 (5)
- PARTIAL: F6 (18 residual false pairs), F13, F16 (3)
- OPEN: R1, R2, R3, R7, R8, R9, R10, R12 (8; R3, R7, R8, R9, R12 are limits or by design)
- NOT-CHECKED cause: R5 (1); OUT OF SCOPE: R11 (1)

## B. Measurements

All numbers copied from the named file. "REPORT" = `fix/phase7:artifacts/run-20261028-remote/REPORT.md`. "not checked" = not in the sources read. Model and GPU: 2026-10-28 `Qwen/Qwen3.6-35B-A3B-FP8` served by vLLM (REPORT); 2026-10-14/20 `Qwen3.8-Flash-Next` on a llama.cpp server, "an RTX card (model not given)" (`artifacts/live-test-report.md` line 66); GPU model, throughput tokens/s: not measured in any source.

### Wall clock per stage
| Stage | Run | Value | Source |
|---|---|---|---|
| collect PostgreSQL (remote, tunnel, sampling + format profiles) | 2026-10-28 | 7.9 s | REPORT s2 |
| collect ClickHouse | 2026-10-28 | 16.04 s | REPORT s2 |
| validate bundle pg / ch | 2026-10-28 | 0.32 s / 0.31 s | REPORT s2 |
| assemble pg / ch | 2026-10-28 | 1.34 s / 1.17 s | REPORT s2 |
| render pg / ch | 2026-10-28 | 1.13 s / 1.06 s | REPORT s2 |
| index pg / ch | 2026-10-28 | 0.58 s / 0.55 s | REPORT s2 |
| assemble/render/index both | 2026-10-28 | exit 0, seconds not copied (in `both-commands.txt` summary lines not shown) | not checked |
| answer per question (40 questions, 6 parallel) | 2026-10-28 | min 0.54 s, median 29.21 s, max 65.0 s | REPORT s2 |
| collect pg / ch / mongo (local DBs, 100 rows) | 2026-10-17 | 0.25 / 0.29 / 0.71 s; assemble 1.56 s; render 1.17 s; index 0.58 s | `run-20261014/gate1-summary.md` |
| collect pg / ch / mongo (25,000 rows, 4,096 hashes) | 2026-10-20 | 1.76 / 2.70 / 1.28 s; assemble 1.94 s; render 1.52 s; index 0.84 s | `run-20261020/step1-commands.txt` |
| collect ch probe | 2026-10-18 / 2026-10-20 | 0.99 s (ClickHouse), 0.72 s (MongoDB retry) / ch 2.67 s (PG+CH rerun) | `gate4-summary.md`; `run-20261020-pgch/summary.md` |
| planner plans J1 J2 J3 X1 J4 | 2026-10-17 | 0.28, 0.18, 1.91, 0.17, 0.30 s (one run each, not a benchmark) | `gate3-summary.md` |
| LLM review (4 batches of 5 candidates, 20 suggestions) | 2026-10-14 | 457 s; prompt tokens 1976/1674/2105/2104; completion 2508/6966/10322/8755 = 28,551 | `LIVE_VERIFY_REPORT.md` Step 8 |
| LLM review with shipped example config | 2026-10-14 | fails at 61.6 s, 0 suggestions (`MODEL_TIMEOUT`) | same |
| LLM review (largest batch) | 2026-10-19 | 243 s | `LEAD_REVIEW.md` F8 |
| LLM review in the 2026-10-28 run | | not run (`config/builder.toml` has no review section) | `git show fix/phase7:.../config/builder.toml` |
| 40 questions total | 2026-10-20 (PG+CH+Mongo) | 51 min, median 59 s, max 291 s | `run-20261020/step3-summary.md` |
| 40 questions total | 2026-10-20 (PG+CH only) | 59 min, median 81 s, max 208 s | `run-20261020-pgch/summary.md` |
| ask, 20 questions | 2026-10-14 | min 4.6 / median 43 / max 582 s | `LIVE_VERIFY_REPORT.md` send-back |
| ask, 148 to 9294 completion tokens per answer | 2026-10-12 | 2 to 101 s on a shared server | `artifacts/live-test-report.md` line 279 |
| ClickHouse probe read amplification | 2026-10-19 / 2026-10-20 | 7.8x (778,240 rows, cap 100,000) / 1.10x to 1.31x | `LEAD_REVIEW.md` F9; `step2-summary.md` |
| ClickHouse probe row cap (soft) | 2026-10-18 | 163,696 to 229,376 rows read, cap 100000 (2.3x) | `gate4-summary.md` |
| builder cost of F15 ranking | 2026-10-24 | `_overlap` for all candidates: 1.0 s -> 4.5 s on 120 parents x 2000 tokens | TODO.md P7-2 |

### Scale scanned
| Item | Value | Source |
|---|---|---|
| Diffbot (PostgreSQL) tables / columns | 13 / 124 | REPORT s3 |
| ZoomInfo (ClickHouse) tables / columns | 7 / 45 (46 in 2026-10-17 run) | REPORT s3; gate1-summary |
| Declared keys/constraints | 0 / 5 | REPORT s3 |
| Column profiles / with format profile | pg 235 / 111; ch 79 / 38 | REPORT s3 |
| Catalogue nodes | 138 / 53 / 191 both | REPORT s3 |
| Format profiles not read at max_queries 50 | 78 (then 200 -> complete, 111 profiles) | REPORT s4 item 1 |
| Largest PG table / MongoDB collection (rows) | 20,092 / 13,518 | LEAD_REVIEW F5 |
| ClickHouse parent size | 1.27 M rows (LEAD_REVIEW); 12,338,392 `Experience` rows; 1,266,268 ch ids | LEAD_REVIEW F5; gate3-summary |
| Local 3-source catalogue | 3 sources, 48 objects, 1035 fields | `run-20261014/gate1-summary.md` |
| PG+CH only catalogue | 2 sources, 20 objects, 170 fields | `run-20261020-pgch/summary.md` |
| Value-hash sets | PG 106, MongoDB 450 (449 complete), ClickHouse 38 (3 complete) | `step1-summary.md` |
| Largest prompt | 30,454 bytes (112 items), 38 requests | `step3-summary.md` |

### Candidates and quality
| Measure | Value | Source |
|---|---|---|
| Candidates 2026-10-17: off / on (100 rows) | 106 / 82 | gate1-summary; gate2-summary |
| Zero-overlap drops (100 rows) | 25 (23 candidates vanished) | gate2-summary |
| 16 known-wrong gone / 5 known-right kept (100 rows) | 9 of 16; 3 of 5 | gate2-summary |
| Same (25,000 rows, with ClickHouse hashes) | 16 of 16; 5 of 5; 0 owner-true dropped (was 12) | step1-summary |
| Candidates step 1 | 79 (61 true, 18 false, 0 unmarked) | step1-summary |
| 18 false remain, all with ClickHouse parent | 14 to `ch.Company.companyID`, 4 to `ch.Person/Location` | step1-summary |
| Counters step 1 | `dropped_zero_overlap_candidates` 26, `dropped_ordinary_name_candidates` 3 | step1-summary |
| Candidates probe: with Mongo / without | 134 (68 true, 22 false, 44 unmarked) / 56 (39 true, 17 false) | step2-summary |
| Probe before fix, 3 sources | 162 (84 unmarked) | gate4-summary |
| MongoDB-MongoDB true pairs found | 5 of 9 (was 0) | step2-summary |
| Owner-true pairs missing with Mongo | 7 (5 by design, 2 F15) | step2-summary |
| PG+CH only, no MongoDB baseline | 48 (35 true, 13 false) step 1; 56 (39 true, 17 false) step 2 | `run-20261020-pgch/summary.md` |
| Remote 2026-10-28 candidates | pg 30 verified; ch 5 plausible; both 5 plausible + 3 unverified_cross_source + 30 verified; planner 38 reference edges | REPORT s2-s3 |
| Overlap measured examples | `Experience.companyID` -> `Company` real 25%; `PersonContact`->`Person` 25% (1,082,567 of 4,320,757); displayed "1% of 2702" and "0% of 4042" before F14 fix | step3-summary |
| `freeMails` real overlap | 35% (1,313 of 3,776) | LEAD_REVIEW F2 |
| Ranking 9-27% real containment, true ch pairs | 9-27% | LEAD_REVIEW F7 |
| LLM review vs owner marks (20 suggestions) | 11 true: supports 10, unknown 1; 9 false: supports 4, unknown 4, opposes 1 (4 of 14 "supports" on false) | LIVE_VERIFY_REPORT |
| Planner full joins | J1 1230 of 1230; J2 1229 of 1230; J3 3,465,441 of 12,338,392; X1 3939 of 3940; J4 0 shared distinct ids (pg 3940, ch 1,266,268) | gate3-summary |

### Requester answer quality
| Run | Result | Source |
|---|---|---|
| 2026-10-18, 40 questions | Right 17, Partial 13, Wrong 6, NOT_ESTABLISHED 2, NO_CATALOGUE_MATCH 2 | LEAD_REVIEW "Not verified" (recounted by lead) |
| 2026-10-20 | Right 19, Partial 13, Wrong 0, NOT_ESTABLISHED 5, NO_CATALOGUE_MATCH 2, error 1 (MODEL_INVALID_CITATION) | step3-summary |
| 2026-10-20 retrieval: expected names in context | 59 of 134 (44%) -> 104 of 134 (78%); at 10 hits 59, 30 hits 81 (60%), with table expansion 94 (70%) (replay) | LEAD_REVIEW F13; step3-summary |
| 2026-10-20 PG+CH only | answered 28, NOT_ESTABLISHED 9, NO_CATALOGUE_MATCH 2, MODEL_UNAVAILABLE_OR_INVALID 1; owner grades in `step3-grading-sheet.md` (not summed here: not checked) | `run-20261020-pgch/summary.md` |
| 2026-10-28, 40 questions (1 repeat) | 24 answered, 10 not in catalogue, 6 refused (4 MODEL_INVALID_CITATION none, 2 NO_CATALOGUE_MATCH) | REPORT s1, s4 |
| 2026-10-28 spot check (every 5th answered: 5) | 4 correct, 1 incomplete; not a full grading | REPORT s4a |
| 2026-10-28 | grading by owner not done; 1 of 3 repeats | REPORT s5 |

### Dominant stage
Model calls. 2026-10-28: collect 7.9 s (pg) + 16.04 s (ch) and every other build step 0.3-1.3 s each, while one answer took median 29.21 s (max 65.0 s) and the 40 questions at 6 in parallel. Arithmetic: non-model stages for Diffbot = 7.9 + 0.32 + 1.34 + 1.13 + 0.58 = 11.27 s; for ZoomInfo = 16.04 + 0.31 + 1.17 + 1.06 + 0.55 = 19.13 s; one median answer 29.21 s exceeds each. Earlier runs: LLM review 457 s versus assemble 0.97-1.94 s; 40 questions 51 min and 59 min versus collect 1.28-2.70 s. Within non-model stages, collect (network reads and format profiles) dominates. Total 40-question wall time in 2026-10-28: not recorded in REPORT (not checked in `both-commands.txt`).

## Not checked
- Whether the later fix/phase7 commits (GitLab CI, langgraph bump, option C ledger) all reached main; live re-run of F8, F14, F15, F17, F18 on main.
- N-I10 context content; N-C1 retrieval; `both-commands.txt` timings for assemble/render/index of both; GPU model and token throughput (not measured anywhere); owner grades sum of the PG+CH 2026-10-20 run.
