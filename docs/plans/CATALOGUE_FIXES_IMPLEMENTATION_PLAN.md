# Catalogue fixes implementation plan — cloud, multi-agent, lead-approved

**Source of truth for *what* to build:** [`artifacts/engineer-fixes.md`](../../artifacts/engineer-fixes.md)
(items A1 to D9, each with a code location and a check), read together with
[`artifacts/live-test-report.md`](../../artifacts/live-test-report.md),
[`artifacts/candidate-trace.md`](../../artifacts/candidate-trace.md),
[`artifacts/statistics-analysis.md`](../../artifacts/statistics-analysis.md) and
[`artifacts/model-questions-results.md`](../../artifacts/model-questions-results.md). The report's line
numbers refer to commit `2e87fa7`; verify each against the code before editing. This plan says *how* to build it. Where the two disagree on substance,
the report wins; where the code contradicts either, the code wins and the discrepancy goes in the
handoff file.

**Scope:** fix every defect in `engineer-fixes.md` (A1 to D9) on the code the live test ran. An item or sub-step
that needs a user decision is **`blocked`** in `HANDOFF.md` with its question in the Decisions table, and is built only
after the user writes an answer there (§2.9). Independent work continues meanwhile. Out of scope: anything not in
`engineer-fixes.md`; merging to `main`; creating the routine; the live re-test on the user's databases and model
(`LOCAL_VERIFY.md`); changing the example configs' sampling or model settings for the user's setup.

**Branch:** `feat/catalogue-fixes`, created from `claude/gracious-bardeen-x1y6he` (commit `2e87fa7`, the code the live test ran;
`main` is 14 commits behind it) plus the report commit. Every agent works there and nowhere else (§2).

**Phase 6 branch (2026-10-19, owner decision):** `fix/live-findings`, created from `verify/live-20261014` at `ec62d7b`. It carries everything above plus the live-verification fixes F1 to F4 and the evidence in `artifacts/run-20261014/`. Phase 6 builds the open live findings F5 to F13 there (§8, §9).

**Live state lives in two files, not in any session:**
[catalogue-fixes/HANDOFF.md](catalogue-fixes/HANDOFF.md) (status table, next action, escalations) and
[catalogue-fixes/LOCAL_VERIFY.md](catalogue-fixes/LOCAL_VERIFY.md) (what the cloud could not prove).

---

## 1. Launch (the user does this once; exact strings are in `catalogue-fixes/LAUNCH.md`)

The system runs unattended through an hourly **supervisor routine** (§7). The user: puts the kit on
`main`, creates a cloud environment with the kit's setup script, creates the routine in the
claude.ai Routines UI (repo + environment + GitHub connector + the short pointer prompt), runs one
`dryrun` tick, then sets `Autonomy: unattended-through-phase-N`. The user never creates worker
sessions: the lead creates every subagent itself.

## 2. Operating rules — every agent, every phase

These are CLAUDE.md's rules restated as the contract. The lead copies the relevant
ones into every worker brief; a worker that was not told a rule is the lead's failure.

2.1 **One branch.** `fix/live-findings` only (Phase 6; Phases 0 to 5 ran on `feat/catalogue-fixes`, which is now history and never touched). Never `git checkout` another branch,
merge, rebase, delete or force-push. Worktree isolation is not used: lanes own
disjoint files (§3.3), so one working tree is safe.

2.2 **Tool order, stop at the first tier that answers.** graphify
(`query` / `path` / `explain` / `affected` / `god-nodes`) → graft (`ask`,
`skeleton`) → codebase-memory-mcp (`search_graph`, `trace_path`, `get_code_snippet`,
`check_index_coverage` on every file relied on) → raw source. Missing graphs:
the project's graph refresh script (`python scripts/graph_sync.py`; it already fails closed on a missing or mis-versioned tool and CI's `graph-index` job asserts it), never a silent drop to grep. **Never** the
LLM paths: `graphify extract --mode deep`, `graphify label`, `graphify cluster-only`
without `--no-label`, `graft build --deep`. A graph that contradicts the code loses;
rebuild it. Cloud exception, authorised by the user on 2026-10-12: graft is **best effort** there (its install has hung). If
`graft` is not on PATH, do not install the git hooks; refresh graphify (`graphify update .`) and codebase-memory-mcp
(`codebase-memory-mcp cli index_repository --repo-path . --mode fast --persistence false`) by hand before each commit, note the
missing graft once in `HANDOFF.md`, and rely on CI's `graph-index` job for all three. Never edit `scripts/graph_sync.py`, the
hooks or the pins to make this easier. `affected "X"` returning nothing means the *graph* sees no caller —
confirm with grep before deleting anything.

2.3 **Ponytail, all six commands, used for real.**
- `/ponytail` full — the ladder: does it need to exist → already in this codebase →
  stdlib → native → installed dependency → one line → minimum code.
- Every non-trivial branch leaves **one** runnable check; deliberate corners carry a
  `ponytail:` comment naming the ceiling and the upgrade path (the report's own
  "Ceiling" / "Deferred alternative" text is the source for those comments).
- `/ponytail-review` on every phase's diff **before** the phase closes.
- `/ponytail-audit` once, in Phase 4, paired with graph tiers (graph finds the
  unreferenced, the audit decides whether it should exist).
- `/ponytail-debt` in Phase 4: the ledger of every deferral and its trigger.
- `/ponytail-gain` in Phase 4: measured effect, for the final report.
- `/ponytail-help` when unsure.

2.4 **No hardcoded values, no corpus literals.** AGENTS.md: never hardcode credentials, endpoints, model identity, thresholds,
timeouts, source IDs, paths or environment-specific values in application logic; a new number lives in a validated config model with a
documented default. And no name from the user's databases may appear in `packages/*/src` (the fix must work on any catalogue). Phase 0
records the baseline of
`grep -rniE 'zoominfo|diffbot|enrichment_engine|testzoominfo|companyrevenue|companyticker|companyemployees|companyexperience|industryclassification' packages/*/src`
and every item's acceptance requires no new hit. Fixtures use invented generic names. The question for every constant: would a count over
the catalogue or the config produce it? Existing named constants (`MEASURED_UNIQUE`, `MIN_TOKEN_SUPPORT`) stay as they are unless an item says to move one.

2.5 **Mechanically enforced rules stay enforced.** `ruff check .`, `mypy packages`, `scripts/ci_check.py` and
`tests/test_repository_policy.py` (required files, uv and graph-tool pins, `TODO.md` keeps a live verification record and open tasks);
requester and planner are separate wheels that share only `dbig_contracts` (`scripts/package_isolation.py`: no import between
them, no source-database credentials); `pip-audit` and `scripts/supply_chain.py` (no new dependency without the user's approval:
ask via a Decision); generated JSON schemas under `schemas/` are regenerated with `scripts/export_schemas.py` and golden artifacts
under `tests/golden` with `scripts/make_golden.py` only when the item says the output changes, and the diff is reviewed by the lead and
named in the commit. Never commit `.env`, credentials, sampled values, `graphify-out/`, `graft/`, `.codebase-memory/` or search indexes. No raw prompt,
response, sample value, SQL literal or secret in a log, error, trace or test output; error codes come from exception types and structure,
not from content. `TECHNICAL_IMPLEMENTATION.md` is the binding design: verify a fact against code before relying on it, and update the
section a behaviour change touches (§8b rules, §12a errors, §17). Keep `TODO.md` live (mark an item in progress before changing files,
record each discovery, decision and completion).

2.6 **Known traps.**
- **CI does not run on a push to this branch** (`ci.yml` triggers on pull requests, pushes to `main` and `workflow_dispatch`). Start it with
  `gh workflow run ci.yml --ref fix/live-findings`, then `gh run list --workflow ci.yml --branch fix/live-findings` and `gh run watch`.
  The live suites (`tests/test_live_*.py`) skip locally and run only in CI's `source-integration` and `source-integration-latest` jobs, so a
  local green is not enough for any item that touches collectors, plans or scans: read the CI result per job.
- **Python 3.12 is pinned** (`.python-version`); 3.13 fails `tests/test_ci_certificates.py`. Use `uv ... --python 3.12`.
- **The default is sampling off**, so value overlap is measured only when a source config sets `sample_rows` and `value_token_key_env`. Tests that
  need tokens build them as `tests/test_value_evidence.py` does.
- **`artifacts/` is evidence, read-only.** Do not edit it; write scratch to `.tmp/` (ignored) and handoff files to `.handoff/`.
- **requester and planner may not import each other or the builder.** A helper both need goes in `dbig_contracts` only if `package_isolation.py`
  still passes; otherwise duplicate the few lines with a `ponytail:` comment.
- **The report's numbers come from the user's databases**, which the cloud cannot see (§6).

2.7 **Completed is not evidence.** A run finishing `completed` proves nothing; the
only proof is an independent score. The scorer/acceptance check is written *before*
the implementation, by the verifier (§4), never fitted afterwards.

2.8 **Environment.** `bash scripts/cloud_setup.sh` (the supervisor runs it when a tool is missing) installs the three graph tools at
the project's pins (graphify 0.9.65, graft 0.18.0, codebase-memory-mcp 0.12.1) and bootstraps with
`uv sync --locked --all-packages --all-extras --dev --python 3.12` (uv 0.12.19, the CI pin). Whether the cloud image has `uv` and Python 3.12
was not verified when this plan was written; the dry-run tick records it. No `.env`, no database credentials, no model key exist in the cloud
and none may be created: tests use throwaway test values from `tests/` and `.env.example` placeholders only. Network access is Full so `uv`, `pip-audit`
and the graph tools can reach their indexes.

2.9 **Decisions are blocked, not guessed.** When an item or sub-step needs a choice that is the user's (a behaviour change the user has
not approved, a threshold default the report does not give, a new dependency, a schema change that is not additive, a better approach
than the plan), the lead adds or updates a row in the Decisions table of `HANDOFF.md` (question, options, recommendation, evidence), sets the
dependent items to `blocked`, records it in `TODO.md`, and **continues independent work**. Only the user writes the `Answer` column. At every
tick and phase end the lead re-reads that table: an answered row turns its items back to `todo` and the lead builds exactly the answer. When only
blocked items remain, the Next action becomes `waiting for the user: <DEC ids>` and the tick exits. The Decisions pre-filled below (DEC-1 to DEC-12) are the ones
already known.

## 3. Roles, routing, concurrency

### 3.1 Roles

| role | model / effort | does |
|---|---|---|
| **Lead** (the launched session) | Opus 5.5, high | designs, does the complex and cross-cutting items itself, writes worker briefs, reviews and approves/rejects *every* result, owns commits, `HANDOFF.md`, phase gates, notifications |
| **catalogue-fixes-worker** (`.claude/agents/catalogue-fixes-worker.md`) | Sonnet 5.5, high | implements one item inside named files; reports to `.handoff/<id>.report.md` |
| **catalogue-fixes-verifier** (`.claude/agents/catalogue-fixes-verifier.md`) | Sonnet 5.5, high | *derive*: writes acceptance checks from the report before implementation; *attack*: tries to break the result afterwards; read-only on `src/` |

Measured in an earlier cloud project that used this method (phase-kit): the frontmatter `model` key is honoured but a worker's `effort` could not
be confirmed as high (one worker reported low). So: the lead **always passes `model: "sonnet"`
at spawn**, treats worker effort as unverified, and keeps judgement-heavy work (§3.2 lead column) itself.

### 3.2 Routing table — who gets what  

The lead may deviate **only** with a one-line reason in `HANDOFF.md`.

| goes to **lead (Opus)** | goes to **Sonnet worker** |
|---|---|
| anything inside a shared large file (`catalog-builder/.../builder.py`, `render.py`, `collectors/.../runtime.py`) | work in one small file with a fully specified fix |
| D3, A1/D2/D9, C2, D1b, the builder and reviewer half of C1, every Decision item touching the builder, collectors, planner, contracts or schemas | B1, B3/D6a, D6b, B4 and the requester half of C1 (one worker, one serial lane in `requester/`), C3 documentation |
| gate/orchestration wiring, algorithmic work with subtle correctness | tests, fixtures, docs, report plumbing, applying a known pattern |
| items a worker fails review twice | first attempt at medium-risk items (lead reviews the diff line-by-line) |
| every approval decision | never an approval decision |

**Escalation rule:** a worker whose result is rejected twice is taken over by the lead.

### 3.3 Concurrency and conflict avoidance

- **At most 2 concurrent subagents.** The cloud usage window is small and parallel agents burn it
  fast; the cap is raised only by the user.
- **A lane = a set of files.** Two lanes never share a file. Phase 0 locks the real ownership table
  with `graphify affected` per item and writes it into `HANDOFF.md`; files the report does not name
  are inferred until then. A lane that needs a file it does not own asks the lead; the lead
  serialises it behind the owner.
- Everything touching a shared large file is **one serial chain** run by the lead. Independent
  work runs beside it, never inside it.

## 4. The lead approval protocol (nothing advances without it)

For every item, in this order:

1. **Derive.** Lead briefs a `catalogue-fixes-verifier` (*derive*): write acceptance checks from
   the report's Evidence + Fix text only — including a negative case that fails on the
   *old* behaviour and the §2.4 grep. Output: tests under `tests/` +
   `.handoff/<id>.acceptance.md`. The implementer has not influenced these.
2. **Implement.** Lead (or a worker, per §3.2) implements against the brief. The brief
   states: report sections, owned files, acceptance file, rules from §2, what "done"
   means (§5), and "report what you could NOT verify".
3. **Attack.** A `catalogue-fixes-verifier` (*attack*) runs the checks and tries to break the
   result. Verdict file: ACCEPT / REJECT with evidence, plus the unverified list.
4. **Lead decides.** The lead reads the worker report, the verdict *and the diff*.
   - **Reject** if it passes only the written tests, passes only the report's one
     example, contains a corpus literal, widens scope, or leaves any old failure
     mode reproducible. Send *specific* corrections to the same worker with
     `SendMessage` (it keeps its context). Max two rounds, then the lead takes over.
   - **Accept** only when it would survive the lead's own attempt to break it.
5. **Record.** Lead commits (`<id>: <what> — <evidence>`), updates `HANDOFF.md`
   (`accepted-cloud` or `unverified-live`), and adds a `LOCAL_VERIFY.md` entry for
   anything the cloud could not prove. If a result is wrong or evidence-thin, it goes
   under "Open escalations" in `HANDOFF.md` instead of being waved through.

Workers write full reports to files and return ≤10 lines, so the lead's context holds
verdicts, not essays.

## 5. Definition of done — per item

- [ ] Acceptance checks (derived by the verifier) pass, **and** the negative case fails
  on the pre-change code (shown by running it against `git stash` or the parent commit).
- [ ] The check stages (§8 "Full check") that the touched files affect are green; at
  phase end the project's full check command is green (it is the one verification command).
- [ ] §2.4 literal grep clean; §2.5 guards clean; no lint/type baseline growth.
- [ ] The report's "Ceiling" for the item is recorded as a `ponytail:` comment or an
  item note; the "Deferred alternative" and its trigger are listed for `/ponytail-debt`.
- [ ] Graphs refreshed (`python scripts/graph_sync.py`, or the §2.2 cloud exception) after the commit.
- [ ] `HANDOFF.md` row updated; `LOCAL_VERIFY.md` entry added where applicable.
- [ ] Lead's explicit verdict recorded. No self-certification by a worker.

## 6. What the cloud can and cannot verify

**Can:** the full check (§8), every unit and regression test, `tests/test_value_evidence.py`-style fixtures with planted ground truth,
a stub model server that records its calls and can imitate a llama.cpp server (HTTP 400 on a schema carrying `maxLength`, `minLength`,
`maxItems` or `minItems`; a reply cut off with `finish_reason: length`; a slow reply; an unreachable port), the package-isolation wheel build,
and, through CI started with `gh workflow run`, the live suites against throwaway PostgreSQL, ClickHouse and MongoDB containers with
**synthetic** schemas (`source-integration` and `source-integration-latest`).

**Cannot:** reach the user's PostgreSQL 16.15, ClickHouse 26.8.2.7 and MongoDB 7.0.41 (loopback on their machine), the user's llama.cpp or vLLM
server (behind an SSH tunnel), TLS to those servers, the 42 labelled relationship pairs, the 20 harder questions' real answers, the user's `.env`,
or any secret. The user rebuilds that environment with [`docs/LIVE_TEST.md`](../LIVE_TEST.md) and the configs in `artifacts/config/`
(no secrets in them), as written in [`catalogue-fixes/LOCAL_VERIFY.md`](catalogue-fixes/LOCAL_VERIFY.md).

**Rules for the split:**
- An item is fully implemented even when its live check is deferred; it is marked `unverified-live`, never `accepted-cloud`, and never silently "done".
- Synthetic tests are *evidence of mechanism*, not proof the live numbers moved. The report's live "before" figures go into `LOCAL_VERIFY.md`
  as the values the user must see change.
- Anything depending on a model is exercised with the recording stub; the user's server is `unverified-live`.
- Report counts, not rates, where the base rate is tiny; stratify where an aggregate hides a failure. Overlap is a ranking and a warning, never a
  pass/fail threshold on its own, except the measured 0-of-N rule the user decided (A1/D2): the report shows true relationships at 21% to 27%.

## 7. Sessions, limits, supervisor, notifications

**Target: unattended cloud runs, resumed by an hourly supervisor routine** (proven in that earlier project; if a
dry-run tick fails, fall back to an attended session with the same protocol).

- **State is in git.** After every accepted item (and every `WIP <id>` step): commit, update
  `HANDOFF.md`, push. A new session needs only the plan + `HANDOFF.md`. Sessions are short-lived
  *slices*; nothing depends on a session's memory, so no compaction is needed.
- **Usage limit.** The cloud shows no usage percentage, so there is no 90 % rule there: work
  until the limit stops the session. Nothing is lost because everything is committed per
  step. If a usage percentage *is* readable (local `get_usage`), stop at ≥ 90 %: finish the
  commit, write `HANDOFF.md`, push.
- **Supervisor.** A scheduled cloud routine (cron `0 * * * *`, branch `fix/live-findings`)
  (created in the claude.ai Routines UI with the repo and GitHub connector — a routine made from
  inside a session has no repo) starts a fresh session whose prompt is
  [catalogue-fixes/SUPERVISOR.md](catalogue-fixes/SUPERVISOR.md). It exits
  at once when: the rate limit is a hard stop (`allowed_warning` is not one); `HANDOFF.md` says `Autonomy: off`; the next
  action is "done" or "waiting for the user"; or `Lock` is younger than 75 minutes (another
  session is alive). Otherwise it takes the lock and acts as **lead** for as long as it can.
  An idle tick costs almost nothing, so hourly resumes soon after each window reset.
- **Phases chain automatically** once the user sets `Autonomy: unattended-through-phase-5`:
  the phase gate (§8) must pass, including a green CI run on the pushed head, before
  `HANDOFF.md` advances to the next phase. After the last authorised phase the supervisor stops and never merges.
- **Never lose an item.** Commit `WIP <id>` before any long operation. A session that finds
  uncommitted work or a stale lock inspects `git status`, finishes or discards it deliberately,
  and records which in `HANDOFF.md`.
- **Notifications.** Cloud push does not reach the user (confirmed). The record is
  `HANDOFF.md` + commits: every phase end, blocker and the final completion are written to its
  "Notifications" section, and a `[PHASE n DONE]` / `[BLOCKED]` / `[Catalogue fixes COMPLETE]` commit
  message prefix makes them visible in GitHub. Try the push tool anyway; do not rely on it.
- **Cost discipline:** Sonnet for the mechanical work in §3.2; batch independent
  lookups; keep worker reports in files; do not re-read what the graphs already answered.

## 8. Phases

**Full check** (the one verification command; each line is a CI job):

```bash
uv sync --locked --all-packages --all-extras --dev --python 3.12
python -m compileall -q scripts && python scripts/ci_check.py
uv run ruff check . && uv run mypy packages && uv run pytest -q
uv run pip-audit && python scripts/supply_chain.py
uv build --all-packages && python scripts/package_isolation.py
python scripts/graph_sync.py          # or the §2.2 cloud exception
```

Then push and start CI: `gh workflow run ci.yml --ref fix/live-findings`; the phase gate needs every job green on the pushed head.

**Hard ordering from the report and the code (the DAG):**
- `builder.py` is one serial lead chain: D3, then A1/D2/D9, then (after answers) DEC-12, DEC-2, DEC-1, DEC-3. `render.py` is shared by C2, D1b and A1's page text: serial, lead.
- `search.py`: B3/D6a, then D6b, then B4, then (after DEC-7) D6c and DEC-8. `answer.py`: B1, then the requester half of C1, then (after answers) DEC-5, DEC-6, DEC-9.
- C3 documents behaviour, so its llama.cpp note is written after B1 and C1 are accepted; its other points have no dependency.
- Changes to collector output (DEC-2) regenerate schemas and golden artifacts and must come after the builder chain items that read them.
- The first-listed Phase 1 items change no inference output and need no decision.

- **Phase 0 — baseline and ownership (lead only; no feature code).** Run the full check and record per-stage result and time (expected on the
  tested code: `pytest` 478 passed, 68 skipped in the cloud (about 80 s) and 477 passed, 69 skipped on the user's machine, which has no system CA bundle for the one test that needs it; CI green on `2e87fa7`). Start CI on the branch head and record it. Record the §2.4 grep
  baseline. Lock the file-ownership table with `graphify affected` per item and write it into `HANDOFF.md`. A verifier builds synthetic fixtures under
  `tests/catalogue_fixes/` reproducing each failure *shape* with planted ground truth: lowercase no-separator names (`xid` style), camelCase
  columns, plural words, two sources whose key columns share no values, a source whose key columns share some, a stub model reproducing each B1/C1
  failure. Record **before** behaviour on the current code. Note where the report's own example already passes or over-claims (for example: the
  decided D3 rule recognises `companyid` and `personid` but not `classificationid`, whose stem is not a known object: DEC-12). Write
  `catalogue-fixes/PHASE0_REPORT.md`, commit, continue.
- **Phase 1 — independent bugs (no decision needed).**

| lane | owner | items | files |
|---|---|---|---|
| R | worker (serial inside the lane) | B1, B3/D6a, D6b, B4, C1 requester half | `packages/requester/src/dbig_requester/{answer,search,config}.py`, `tests/test_requester.py` and new tests |
| D | worker | C3 (docs; the llama.cpp note last) | `docs/LOCAL_RUN.md`, `docs/LIVE_TEST.md`, `.env.example` comments only |
| lead | lead | C1 builder half, C2, D1b | `reviewer.py`, `builder.py` (the status line only), `render.py`, `cli.py`, `state.py`, manifest schema if needed |

- **Phase 2 — relationship inference, decided parts (lead chain).** D3, then A1/D2/D9 (labels, overlap numbers on the page, the measured-zero drop).
- **Phase 3 — decision-gated items.** One block per Decision in §9. Each starts only when its `Answer` is written; if the answer is "no" or "later" the
  item becomes `deferred-by-user` and counts as finished. The lead takes answered items in DAG order. If none is answered, set the Next action to
  `waiting for the user: <DEC ids>` and exit.
- **Phase 4 — integration and hand-over.** Starts when every item is accepted, `unverified-live` or `deferred-by-user`. Full check green; `/ponytail-review` over the
  whole branch diff, `/ponytail-audit`, `/ponytail-debt`, `/ponytail-gain`; every `HANDOFF.md` row has a lead verdict; finalise `LOCAL_VERIFY.md`;
  update `TECHNICAL_IMPLEMENTATION.md`, `docs/` and `TODO.md` for changed behaviour; purge `.handoff/` after folding its verdicts into a short final
  report; `[Catalogue fixes COMPLETE]`. **Stop. Never merge.**

- **Phase 5 — cloud hardening and hand-over to local (added 2026-10-13 at the user's request).** Everything the cloud can still test or implement, then a clear hand-over.
  Items H0 to H8 (§9). Starts when Phase 4 is done and the user has set `Autonomy: unattended-through-phase-5`. It ends in `READY FOR LOCAL VERIFY`, not in
  "complete": the Next action becomes `READY FOR LOCAL VERIFY - waiting for the user`, and the commit and the Notifications entry start with
  `[READY FOR LOCAL VERIFY]`. Never merge.

- **Phase 6 — the open live findings F5 to F13 (added 2026-10-19 at the owner's request).**
  - **Source of truth:** [`artifacts/run-20261014/leader/LEAD_REVIEW.md`](../../artifacts/run-20261014/leader/LEAD_REVIEW.md). Its "Verdicts" table and its "Changed or new recommendations" are what to build; `FIX_PROPOSALS.md` gives background only, and where the two differ, `LEAD_REVIEW.md` wins.
  - **Decisions:** answered by the owner on 2026-10-19 (DEC-18 to DEC-21 in `HANDOFF.md`).
  - **Items:** P6-0 to P6-11 (§9).
  - **Start:** when the owner sets `Autonomy: unattended-through-phase-6`.
  - **Order (the DAG):**
    - `packages/collectors/src/dbig_collectors/runtime.py` is one serial chain: P6-1 (F5 collector), then P6-3 (F7), then P6-6 (F9).
    - `builder.py` is one serial chain: P6-2 (F5 builder), then P6-4 (F10).
    - The requester is one serial chain: P6-7 (F11), then P6-8 (F12), then P6-9 (F13), because all three touch `answer.py` or `search.py`.
    - P6-5 (F8) is independent.
  - **End:** the regression gate P6-10, then the hand-over P6-11 in `READY FOR LOCAL VERIFY`, exactly like H8. Never merge.

### Phase gate (end of every phase)

All items accepted or explicitly `unverified-live`, `blocked` with a Decision, or escalated → full check green → CI dispatched on the pushed head
and green in every job → `/ponytail-review` of the phase diff addressed → graphs refreshed → `HANDOFF.md` and `TODO.md` updated → push →
`[PHASE n DONE]` commit.

## 9. Per-item specification

Report ids refer to `artifacts/engineer-fixes.md`. *Accept* goes beyond "tests pass"; *Live* is the value the user must see change
(full list in `LOCAL_VERIFY.md`).

### Phase 1

**B1 — `ask` schema rejected by llama.cpp** (`requester/.../answer.py` lines 22, 118-126). Remove `maxLength` from the schema sent to the server and enforce the same
limit after parsing in the existing `model_validate_json` path, from one named constant. *Accept:* the recording stub rejects any schema containing a length or item
bound and `ask` now returns a cited answer; an answer over the limit is rejected after parsing; a recursive walk of the sent schema finds none of
`maxLength`, `minLength`, `maxItems`, `minItems`; the same stub made the pre-change code fail. *Live:* `ask` answers "Which column holds a company's website?" on llama.cpp (before: 0 of 30 answered).

**B3 + D6a — camelCase and PascalCase are one word in search** (`requester/.../search.py` lines 68, 166). Index the split words (`companyRevenue`, `companyID`,
`companyRevenueIn000s`) beside the exact lower-cased name, as the builder's `_snake` does for inference (§2.6 on sharing). *Accept:* a fixture catalogue where
`search --query revenue` returns `accountRevenue`; snake_case and whole-name queries still work; an upper-case run (`ID`, `URL`) is one word; index version or digest changes are
handled so an old index is rebuilt, not misread. *Live:* `revenue` returns `Company.companyRevenue`; D2, D4, I4 retrieval recall rises.

**D6b — no stemming** (same file). Add a stemmed form next to the exact one (FTS5 `porter` tokenizer or a plural fold; the lead picks the smaller one that passes the check and
records why). *Accept:* `industries` returns what `industry` returns, likewise `tables`/`table`, `companies`/`company`; two unrelated words are not merged; exact-name queries keep their rank.
*Live:* the three questions that got `NO_CATALOGUE_MATCH` for plurals (D5, I5, C3's `tables`) retrieve entries (C3 still needs DEC-7 for the word "ClickHouse").

**B4 — the "nothing matches" guard is defeated by schema words** (`search.py` line 23). Add `column`, `table`, `field`, `number`, `holds`, `store`, `stored` to `STOPWORDS`. *Accept:* a question whose
only matching words are these returns `NO_CATALOGUE_MATCH` and the stub records zero model calls; a column literally named `number` is still found by its own name together with a table word.
*ponytail:* the stopword list is the ceiling; deferred alternative: require a minimum share of meaningful words to match. *Live:* the credit-card question returns `NO_CATALOGUE_MATCH`.

**C1 + D7 — one error code hides the cause** (`answer.py` lines 129-135, `reviewer.py` lines 125 and 229, `builder.py` line 616). Distinct codes from exception types and structure, never from
content: `MODEL_UNREACHABLE`, `MODEL_REJECTED_REQUEST`, `MODEL_TIMEOUT`, `MODEL_TRUNCATED` (`finish_reason: length`), `MODEL_INVALID_CITATION`. Keep `MODEL_UNAVAILABLE_OR_INVALID` only if some
schema, document or consumer names it in a closed list; if so that is **DEC-11** (replace or keep as umbrella) before changing it. The builder keeps `MODEL_REVIEW_FAILED` as the partial reason and
adds the specific code. *Accept:* one test per cause (stub: closed port, 400, delay beyond `timeout_seconds`, length cut-off, unknown cited ID), each its own code; no code or message contains prompt, response or
catalogue text. *Live:* a busy shared server shows `MODEL_TIMEOUT`, not "unavailable".

**C2 — `partial` is not explained in machine-readable places** (`render.py` line 228, manifest, `cli.py`). The CLI summary and the manifest carry the same diagnostics (builder partial reasons included) as the page. *Accept:* with the failing stub, `assemble`'s
manifest lists the codes, `render`'s `diagnostics` equals the count on the page; a complete snapshot still shows 0/none. If the manifest schema change is not additive: **DEC-11b** before changing it.

**C3 — guide gaps** (`docs/LOCAL_RUN.md` step 2, `.env.example`, `docs/LIVE_TEST.md`). (1) Say the ClickHouse account needs `SELECT` for `scripts/run_plan.py` (code 497 otherwise) and say why; (2) name the variable when `. ./.env` fails: document how to find the malformed line,
with a command verified by running it; do not add a checker script; (3) `.env.example` comments say `DBIG_SIGNING_KEY` must be set; (4) the SQL checks name the drivers the collector uses and give a `psql`/`mongosh`-free way or say they are optional; (5) after B1 and C1 are accepted, a llama.cpp compatibility note:
what B1 fixed, that a reasoning model needs a larger `max_tokens` and a longer `timeout_seconds` (with the measured numbers from the report), and that a shared server queues. *Accept:* every command written or changed is run in the cloud where it can be, otherwise listed in `LOCAL_VERIFY.md`; `tests/test_examples.py` and `ci_check.py` pass.

**D1b — empty statistic cells have no explanation** (`render.py` lines 303-315, 387-388; `artifacts/statistics-analysis.md`). A blank cell says why: "not provided by this source", "all values null" or "not computed", chosen from the profile's method and values, never from a source or column name.
*Accept:* fixtures for the three source kinds show the right reason in each empty cell and no blank cell remains; golden render changes are reviewed and named.

### Phase 2

**D3 + B5(2) — lowercase names without a separator are not references** (`builder.py` `_reference_stem`, lines 108-132 and 519-546). *User decision 2026-10-12:* read `companyid` as `company` + `id` **only when the stem is a known object** (a table or collection name in the catalogue, normalised exactly as the existing parent-name comparison does). *Accept:* a fixture where `x.companyid` links to `company.companyid` and `x.personid` to `person.personid`; a name whose stem is not a known object (`xid`, `valid`, `paid`) proposes nothing; the same-table exclusion, the type-family rule, the declared-key-first rule and the case-boundary rule behave as before (the existing inference tests pass unchanged); golden artifacts unchanged unless the fixture they use has such names. Note: `classificationid` is *not* fixed by this rule (stem `classification`, table `industryclassification`): that is DEC-12. *Live:* J1 and J2 return a path; the 21 candidates grow by the PostgreSQL pairs.

**A1 + D2 + D9 — a relationship is proposed without proof that its values link** (`builder.py` lines 503-600, `_containment` line 244, `render.py`). *User decisions 2026-10-12:* (1) every candidate says what was **not** checked: evidence "value overlap not measured" with the reason (sampling off, no tokens, below minimum support, different key IDs), shown on the page as "values not compared"; (2) with sampling on, a candidate whose measured containment is **0 of N** with N at or above the drop threshold is **dropped**; (3) a non-zero overlap, even 21% to 27%, is shown with its number and sample size and ranks and warns but never drops. The drop threshold is a validated builder config value whose default is the existing `MIN_TOKEN_SUPPORT` (4); if the lead finds a reason for another default, or that the drop needs a new diagnostic or schema field that is not additive, that is a Decision. The lead chooses the smallest auditable record of what was dropped (counts only, no values). *Accept:* with sampling off every candidate shows "values not compared" and none is dropped; with sampling on, disjoint columns give no candidate, overlapping columns give a candidate with the numbers, a 25% overlap keeps its candidate, a sample below the threshold keeps its candidate marked "sample too small"; mutation check: removing the drop makes the disjoint test fail. *Live:* of the 21 candidates, the 16 with 0% overlap disappear and the 5 with 100% stay (needs the user to turn sampling on, `LOCAL_VERIFY.md`). A3 is this item.

### Phase 3 (each starts only when its Decision has an answer; "Recommendation" is what the lead proposes, not a decision)

| Decision | Items | What would be built if answered yes | Files | Recommendation |
|---|---|---|---|---|
| DEC-1 | A2 | When several unique columns of a parent table qualify, or the best is under the 0.99 threshold, prefer the parent column whose name matches the child's; never accept a parent column whose name does not resemble it | `builder.py` | yes |
| DEC-2 | D1a, B5(1) | Keep ClickHouse primary/sorting key column names (schema metadata, not values) in the bundle and treat them as a weak, labelled parent claim; regenerates schemas and golden | `collectors/.../runtime.py` (~lines 278, 610), contracts, `builder.py` | yes, labelled weak |
| DEC-3 | 2.5 / D2(3) | Optional operator config: dataset groups or forbidden source pairs; default none (no behaviour change unless set) | builder config, `builder.py` | yes |
| DEC-4 | D4, A1(3) | The planner prefers a same-source path over a roundabout cross-source one when both ends are in one source, and warns when every edge of a path is a cross-source candidate with no value evidence | `planner/.../paths.py`, `plans.py` | yes |
| DEC-5 | D5a | Give the model the relationship edges and their evidence status for the retrieved objects (bounded count, metadata only) | `answer.py`, `search.py` | yes |
| DEC-6 | D5b | Validate claims, not words: stop refusing the word `lineage` in a question or answer; allow an uncited "not established by the catalogue" outcome with its own code | `answer.py` | yes, with its own code |
| DEC-7 | D6c, D8(wrong source) | Index `source_id`, engine kind and namespace as searchable words; for list questions retrieve by object; rank object hits above generic column hits when the question names a table or topic; filter or boost by source named in the question | `search.py` | yes |
| DEC-8 | C4, D8 | An optional per-source exclude list (or tag) so schema-description collections (`__schemas`, `*_source_profiles`) are skipped by search; default none | collector or builder config, `search.py` | per-source exclude list |
| DEC-9 | D8 | Resolve each cited ID to its object and column name in the reply, app-side | `answer.py` | app-side |
| DEC-10 | B2 | Review candidates in batches of a configured size; no "thinking off" switch (not verified for this model) | `reviewer.py`, builder config | batches, default 5 |
| DEC-11 / 11b | C1, C2 | See the Phase 1 blocks | | |
| DEC-12 | B5, X1 | Extend the `key_name` rule to a **measured-unique** parent column (identical, non-generic column name), so `companyindustry.classificationid` links to `industryclassification.classificationid`; the user's 2026-10-05 decision admitted it only for **declared** keys. With sampling on, the A1 overlap check guards it | `builder.py` | yes |

*Accept for every Phase 3 item:* the Decision's check from `engineer-fixes.md` as a synthetic test that fails on the old code; the 20 harder questions re-run is `unverified-live`.

### Phase 5 (cloud hardening; lead chain, verifiers in parallel up to the cap)

**H0 — coverage audit.** Map every sentence of the *Proposal* and *Check* of each item A1 to D9 in `artifacts/engineer-fixes.md`, and each Decision's built scope, to a
commit and a test, or to an explicit "not built because <Decision answer / ceiling / needs live>". Write the table to `PHASE5_REPORT.md`. Any gap that the cloud can implement and
that needs no choice from the user is implemented now (with its test); a gap that needs a choice becomes a Decision row and `blocked` (plan §2.9) while the rest continues.

**H1 — independent attack on every Phase 3 item** (C2, DEC-1, DEC-2, DEC-3, DEC-4, DEC-5, DEC-6, DEC-7, DEC-8, DEC-9, DEC-10, DEC-12, E1; they had only the lead's own tests). One
`catalogue-fixes-verifier` per group, at most two at a time: (a) inference and planner: DEC-12, DEC-2, DEC-1, DEC-3, DEC-4, C2; (b) answering and retrieval: DEC-5, DEC-6, DEC-7, DEC-8, DEC-9;
(c) DEC-10, E1 and the error codes. The verifier derives its checks **from `engineer-fixes.md` and the Decision text only, before opening the implementation**, writes them under
`tests/catalogue_fixes/` (attack files stay not collected by default; the lead runs them by path), then attacks the result. For every item it also shows, by reverting the item's
change on a scratch copy, that its negative case fails without the change (mutation proof). Lead decides ACCEPT or REJECT; a rejected item is corrected and re-attacked, at most two rounds, then the lead takes over.

**H2 — regression of everything earlier.** Run all attack files of Phases 0 to 2 and 5 by path, the Phase 0 fixtures (no leftover strict `xfail` that now passes, none removed without a note),
the full suite, and `pip-audit`; all green on the final code.

**H3 — report-shaped synthetic end-to-end scenario with planted ground truth** (counts only). Three synthetic sources mirroring the report's failure shapes: two datasets whose key columns share no values; a
PostgreSQL-like schema with no declared keys and lowercase names without separators; a ClickHouse-like schema with camelCase columns and a declared sorting key; a MongoDB-like schema with a
schema-description collection. Run it through assemble, render, index, search, `ask` against the recording stub, `join-paths` and `plan`, with value sampling on and off. Report in `PHASE5_REPORT.md`: candidates true/false/missed against the planted
truth (before = the behaviour recorded in `PHASE0_REPORT.md`), join paths found for the analogues of J1 to J4 and X1, retrieval recall in the top 10 for planted analogues of the 20 questions, and every `partial` reason shown. If it needs
real containers, say so and use the CI `source-integration` jobs only through an existing test file; changing `ci.yml` is a Decision.

**H4 — privacy canaries.** Plant a canary string as a sampled value, SQL literal, secret and prompt text. Assert it appears in no artifact, log, diagnostic, error message, `partial_reasons` entry, trace,
`cited` field, or request body sent to the stub model (only names, types and evidence numbers are allowed there), across the review batches (DEC-10), the relationship context (DEC-5), resolved cited names
(DEC-9), the weak key claim (DEC-2) and the new error codes.

**H5 — documentation and example audit.** Every changed or new config key, error code (`NOT_ESTABLISHED_BY_CATALOGUE`, the C1 codes, `STALE_INDEX`), CLI behaviour and schema field appears, with its default, in `docs/LOCAL_RUN.md`,
`docs/LIVE_TEST.md`, `examples/local/*.toml` (commented) and `TECHNICAL_IMPLEMENTATION.md`. Run every documented command that can run in the cloud against fixtures; list the rest in `LOCAL_VERIFY.md`. Fix the "Final integration" row and any other stale text in `HANDOFF.md` and `TODO.md`.

**H6 — `LOCAL_VERIFY.md` as one ordered run for the user.** Top section "Start here": pull, `uv sync` with the pinned graph tools note, `dbig-requester index`, the exact sampling config snippet to turn on value overlap in all three source configs, the recommended `max_tokens` and
`timeout_seconds` for the reasoning model from the report's measurements, then one numbered list of live checks, each with its command, the report's *before* value and the expected *after*. End with a "send back" template (counts and names only, no values, no secrets) shaped like the live report's section 13.

**H7 — final gate.** Full check, CI dispatched on the final head and green in every job, `/ponytail-review` of the Phase 5 diff, `/ponytail-debt`, graphs refreshed, `FINAL_REPORT.md` updated with Phase 5 results and the coverage table.

**H8 — hand-over.** Next action `READY FOR LOCAL VERIFY - waiting for the user`; the Notifications entry and the commit start with `[READY FOR LOCAL VERIFY]` and say, in at most ten lines: what the cloud proved, what is `unverified-live`, any Decision still open, and the first three commands to run locally. Clear the lock, push, try the push-notification tool (it may not reach the user). Stop.

### Phase 6 (live findings; owner decisions of 2026-10-19 in `HANDOFF.md` DEC-18 to DEC-21)

Evidence for every item is in `artifacts/run-20261014/` (read-only); the numbers below come from `LEAD_REVIEW.md` and its `lead-review-*.json`. Every item follows the usual rules:
- the verifier writes the check before the change;
- a mutation proof shows the check fails without the change;
- no corpus literal appears in `packages/*/src` (§2.4);
- a new value lives in a validated config field with a documented default.

**P6-0 — baseline.** Run the full check and the attack files by path. The known failures are 37, explained in `PHASE5_REPORT.md` H2; record any other, with its cause. Then:
- run the H4 canaries;
- dispatch CI on the branch head (`gh workflow run ci.yml --ref fix/live-findings`) and record the run;
- record the §2.4 grep baseline;
- lock the file-ownership table for Phase 6 in `HANDOFF.md`.

**P6-1 — F5 collector: say whether a sample is complete** (`runtime.py` `_tokenize_postgresql` line ~211, the MongoDB token path line ~1188; `dbig_contracts/models.py` `ValueTokens` line 178).
- **Field:** each `ValueTokens` gains `complete: bool`, true when the read returned fewer rows than `sample_rows`. For MongoDB it must also be false when `max_sample_bytes` cut the read.
- **Schema:** the field is additive (`extra=allow`; an old reader ignores it). Regenerate the schemas; the golden files change only if a fixture samples.
- **Accept:**
  - a table with fewer rows than `sample_rows` gives `complete: true`;
  - a larger table gives `false`;
  - a MongoDB source stopped by `max_sample_bytes` gives `false`.

**P6-2 — F5 builder: a zero is a measurement only against a complete parent; compare capped sets within their common hash range** (`builder.py` `_overlap` line 296, the drop at lines 688-698).
- **(a) The drop:** a `value_containment` with 0 shared drops the candidate only when the parent's `complete` is true. Otherwise the evidence keeps the zero and says "parent sampled, a zero is not a measurement", under its own reason code.
- **(b) Capped sets:** when either side is `truncated`, compare only hashes at or below the smaller of the two sides' largest kept hash (each side's own largest if truncated, no bound if not). Support is counted inside that range, against `min_token_support`. The kept hashes are the smallest of a keyed hash (`_capped_tokens` sorts), so they form a coordinated bottom-k sketch.
- **(c) What does not change:** the decision is built on the parent's `complete` flag, not on `truncated`; the existing "a capped parent never drops" guard is replaced by (a) and (b) together. A missing `complete` (an old bundle) counts as false.
- **Accept, on synthetic data shaped like the live evidence:**
  - a 3,940-key parent read as 100 rows plus a 17-value true child: not dropped;
  - the same parent complete and capped at 256, with a disjoint 93-value child: dropped when the range holds at least `min_token_support` values, otherwise "not measured";
  - the same with a true child: kept with a number;
  - a 25% overlap: kept with a number;
  - mutation proof for (a) and (b);
  - the existing `test_a1_overlap.py` and `attack2_a1_overlap.py` expectations are re-checked, and any that flips is flipped on purpose and named in the commit.
- **Live:** with the owner's settings (DEC-19), 0 true pairs dropped and the 16 known-wrong dropped (replayed in `lead-review-drop-replay-21.json`).

**P6-3 — F7 ClickHouse value hashes** (`runtime.py`, new `_tokenize_clickhouse` beside `_tokenize_postgresql`).
- **The read:** one bounded read per table, `SELECT <columns> FROM <db>.<table> LIMIT <sample_rows>`, with identifiers from the server's own catalog through `_quoted`, `max_execution_time` from the config, the read charged to the budget, and `sample_method = first_rows_limit:<n>`.
- **The hashes:** made with the same `_value_tokens`, `_token_text`, key and key id as PostgreSQL and MongoDB, with `complete` as in P6-1.
- **Skipped columns:** nested types (`_NESTED_TYPE`) are skipped, as the probe does.
- **Accept:**
  - a unit test with a fake client: the query shape, no `SELECT *`, the row limit, the budget charge;
  - the ClickHouse live tests in CI's `source-integration` jobs produce hash sets through an existing live test file (changing `ci.yml` is a Decision);
  - a 25% overlap against a PostgreSQL parent shows its number.
- **Live:** the 21-27% ClickHouse pairs show numbers; `freeMails` does not come back (P6-4).

**P6-4 — F10: the stricter ordinary-name rule (DEC-20) and an evidence tie-break** (`builder.py` key_name branch lines ~633-646 and the sort at line 678).
- **(a) Ordinary names:** a parent that is not a declared key (`declared_unique`) and whose name is not reference-style (`_is_reference_style`) no longer links by `key_name`, with or without measured overlap. This replaces the DEC-16 `needs_overlap` path for these parents, and a weak ClickHouse key claim counts as not declared. Keep the manifest count `dropped_unmeasured_key_name_candidates`, or rename it if its meaning changes; renaming is additive in the manifest, say which in the commit. Flip `tests/catalogue_fixes/test_dec16_name_only_keys.py` on purpose where it asserted a link after overlap.
- **(b) Ties:** among equal scores, sort by
  1. same source before cross-source;
  2. then declared key, then weak key claim, then measured uniqueness;
  3. then parent id.
- **Accept:**
  - a same-name ordinary column (`description`) with 100% overlap on a measured-unique parent: no candidate;
  - a reference-style name still links;
  - a child with 7 tied parents where the same-source weak key sorts 6th by id: it is kept at `top_k_per_field = 5`;
  - mutation proof.
- **Live:** rebuilding the probe snapshot returns `ch.Experience.companyID -> ch.Company.companyID`, `ch.CompanyIndustry.companyID -> ch.Company.companyID` and Z4, and links none of the 30 ordinary-name pairs or `freeMails`.

**P6-5 — F8 review `max_tokens` optional (DEC-18)** (`dbig_builder/config.py` `ReviewConfig.max_tokens` line 18, `reviewer.py` line 248).
- **The change:** `max_tokens: int | None = None`. Unset sends no `max_tokens`, using the OpenAI SDK `omit` sentinel exactly as `answer.py` does.
- **Example and checklist:** `examples/local/builder-with-vllm.toml` gets `timeout_seconds = 900` (the largest measured batch took 243 s) and a comment that `max_tokens` is unset on purpose. `LOCAL_VERIFY.md` drops its `max_tokens = 24000`.
- **Accept:** a recording stub sees no `max_tokens` key when unset and the configured value when set; mutation proof; `tests/test_examples.py` passes.

**P6-6 — F9 probe row cap** (`runtime.py` `_probe_clickhouse` lines ~770-790, `collectors/config.py` lines 41-42).
- **The query:** read through `SELECT count(), uniq(...) FROM (SELECT <columns> FROM <db>.<table> LIMIT <cardinality_probe_max_rows>)` with `max_threads = 1` and `max_execution_time`; drop `max_rows_to_read` and `read_overflow_mode`.
- **Why:** measured on ClickHouse 26.8, this counts exactly the cap and reads about 2× (`lead-review-rowcap.json`), against 7.8× for the shipped query.
- **The comment:** change "at most" to "about: the count covers exactly this many rows; the server may read up to about one block per thread more".
- **Accept:** a fake-client test of the query and settings; in CI's live ClickHouse job, `sample_count` equals the cap on a table larger than it, or the table's row count when the table is smaller.

**P6-7 — F11 the source on every relationship given to the model** (`search.py` relationship body lines ~228-238, `answer.py` system prompt line ~216).
- **The fields:** each relationship body gains `from_source`, `from_namespace`, `to_source`, `to_namespace`, taken from the snapshot nodes, and `INDEX_VERSION` becomes "7" (an old index answers `STALE_INDEX`).
- **The prompt:** one sentence: "A relationship between objects of two different sources cannot be joined in one query; say so instead of writing one."
- **Accept:** the recording stub sees the four fields on a cross-source relationship; a version-6 index answers `STALE_INDEX`; no value or description text is added.

**P6-8 — F12 `outcome` required** (`answer.py` class `Answer` line 30).
- **The change:** no default, so the sent schema lists `outcome` in `required`. The prompt adds: "Put the verdict only in the outcome field."
- **Accept:** a stub reply that omits `outcome` gives `MODEL_UNAVAILABLE_OR_INVALID`, and is not an answer; the schema's `required` holds `answer`, `citations`, `outcome`; mutation proof.

**P6-9 — F13 expand retrieved tables to their columns, uncapped (DEC-18)** (`answer.py` `answer_question` lines ~172-199).
- **The change:** after retrieval, every field of each retrieved object, and of each object of a retrieved field, joins the context with the same keys as the other items, and its id becomes citable. There is no per-table cap (owner decision); `retrieval_limit` and `relationship_limit` stay. No type text is indexed (C deferred).
- **Accept:** a fixture where the needed column is not in the top `retrieval_limit` but its table is: the stub sees the column, and its citation is accepted; `NO_CATALOGUE_MATCH` still means zero hits and no model call.
- **Live:** in the replay, expected names reaching the model went from 44% to 70% (`lead-review-retrieval.json`).

**P6-10 — regression gate (owner decision: full).**
- the full check (§8);
- all attack files by path: only the known 37 may fail, and any change is explained, item by item, as an intended flip;
- the H4 privacy canaries green, extended to the new ClickHouse hashes (P6-3), the relationship source fields (P6-7) and the expanded columns (P6-9);
- `pip-audit`;
- CI dispatched on the final head, green in all 8 jobs;
- `/ponytail-review` of the Phase 6 diff and `/ponytail-debt`;
- `TECHNICAL_IMPLEMENTATION.md` (§8b, §12a, §17), `docs/LOCAL_RUN.md`, `docs/LIVE_TEST.md` and `examples/local/*.toml` updated for every changed key, code and default;
- `FINAL_REPORT.md` gains a Phase 6 section.

**P6-11 — hand-over.**
- **`LOCAL_VERIFY.md`:** gains a "Phase 6 live re-check" section, as below.
- **Next action:** `READY FOR LOCAL VERIFY - waiting for the user`.
- **Notifications entry and commit:** start with `[READY FOR LOCAL VERIFY]`, as H8 does.
- **Then:** clear the lock, push, stop. Never merge.

The "Phase 6 live re-check" section lists, in order, each step that touches the owner's systems, and each needs its own explicit yes:
1. Gate 2 re-run with ClickHouse hashes and the DEC-19 settings;
2. the probe snapshot;
3. the 40 questions with the model.

Each step gives its command, its "before" value from `LEAD_REVIEW.md`, and the expected "after".

## 10. After the final phase

The branch is **not merged by the agents.** The user runs `LOCAL_VERIFY.md` against the real
environment, then merges to `main` locally. Anything the live re-test shows still wrong is a new report; the report's untested areas (TLS,
vLLM, `observed` join paths, the model server's missing API key) stay with the user.

## 11. Known limits of this plan (stated, not hidden)

- Cloud-verifiable evidence is mechanism-level; the report's live numbers can only be confirmed
  locally, which is why `LOCAL_VERIFY.md` exists.
- Parallelism is limited by shared files and by the usage window; the larger saving is cost, from
  routing mechanical items to Sonnet.
- File ownership for items the report does not name is inferred until Phase 0.
- CI does not run on pushes to this branch; the plan relies on `gh workflow run` working with the cloud's GitHub token. The dry run checks it; if it cannot, the user decides whether to add a `push` trigger for `feat/**` to `ci.yml` (not done by the agents).
- `uv` and Python 3.12 in the cloud image are unverified; the dry run records them.
- The measured-zero drop only works where the user turns value sampling on; with it off, candidates are labelled, not filtered.
- `main` is 14 commits behind the base of this branch; the fast-forward in `LAUNCH.md` carries them to `main`.
