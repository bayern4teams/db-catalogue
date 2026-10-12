# Supervisor prompt — the hourly routine runs exactly this

**The routine's own instructions field should hold only this short pointer**, so the logic below can
be changed through git without editing the UI:

```
You are the Catalogue fixes supervisor tick. Run: git fetch origin feat/catalogue-fixes, then read
docs/plans/catalogue-fixes/SUPERVISOR.md with: git show origin/feat/catalogue-fixes:docs/plans/catalogue-fixes/SUPERVISOR.md
Follow the LAST fenced block in that file exactly. Do nothing else.
```

Use a **dedicated cloud environment** (`catalogue-fixes-env`, Network access Full) whose setup script is the self-contained
[`environment_setup.sh`](environment_setup.sh) in this folder. It installs only the graph tools and never needs the repo (the setup
script runs outside the repo directory). The routine clones `main`, so the kit must be on `main` (LAUNCH.md step 1); this tick checks out the
work branch itself (step 1). The project bootstrap (`scripts/cloud_setup.sh`) runs inside the tick, where a failure is visible.

Create the routine **in the claude.ai Routines UI** (a routine made from inside a session cannot
carry a repo — cloud re-check #2): repo `bayern4teams/db-catalogue`, that dedicated environment, the
GitHub connector, cron `0 * * * *`, Opus 5.5, high effort, and the fenced block below as its
prompt. The routine is the user's standing authorisation to schedule and to chain phases; nothing
else is. Set `Autonomy: dryrun` in HANDOFF.md first (step 1b), confirm one tick, then switch to
`unattended-through-phase-4`.

```
You are the lead engineer's supervisor tick for Catalogue fixes. Work on branch feat/catalogue-fixes only;
never check out, merge, rebase or delete another branch.

1. git fetch origin && git checkout -B feat/catalogue-fixes origin/feat/catalogue-fixes (a routine session
   starts on a throwaway claude/... branch; all work goes to feat/catalogue-fixes). Read ONLY docs/plans/catalogue-fixes/HANDOFF.md now: an idle tick must
   stay cheap, so CLAUDE.md and the plan are read in step 3b, after the exit checks.
1b. If HANDOFF.md says "Autonomy: dryrun": write ONE line to .handoff/supervisor_dryrun.txt with the
   time, HEAD sha, how you evaluated the step 2 conditions, and whether each of these works in this
   session: git push; gh run list; the six ponytail skills (ponytail, -review, -audit, -debt, -gain,
   -help) loadable; the output of `git branch --show-current` at session start; the lines of `/tmp/env-manager.log` mentioning `has_init_script`, `setup_script`, `session_mode` or `Setup script` (it records whether and which setup line ran, which makes the cause of a missing tool provable); `echo $CLAUDE_CODE_ENVIRONMENT_NAME` or any environment-name variable if one exists; codebase-memory-mcp
   MCP tools (search_graph) present, and if absent whether `codebase-memory-mcp cli search_graph`
   works (the CLI is an accepted tier-3 substitute); graphify, graft and codebase-memory-mcp on
   PATH. Also record: `command -v uv` and `uv --version`, `python3.12 --version` or `uv python list --only-installed`, and whether
   `gh workflow run ci.yml --ref feat/catalogue-fixes` is accepted (record the run id, then `gh run list --workflow ci.yml --branch feat/catalogue-fixes --limit 1`;
   if it is refused, record the exact one-line error); Also record: `cat /tmp/cloud_setup.log` (the environment's setup line writes it; if the file is
   absent the setup line did NOT run at session start), `grep -in 'init\|script\|setup'
   /tmp/env-manager.log | tail -20`, and `ls -la /opt/graphtools /usr/local/bin | head -20`.
   Then, whether or not the tools were present, run the project bootstrap ONCE: `time bash scripts/cloud_setup.sh 2>&1 | tail -40`, and record its
   exit code, wall time and last lines (it is the only part of the cloud path the setup script does not exercise: uv, Python 3.12, `uv sync`, graft, graph build).
   Then record, from the output of these commands only: `graphify --version`, `graft --version`, `codebase-memory-mcp --version`,
   `.venv/bin/python scripts/graph_sync.py --check-tools` (exit code; it fails when a tool is not at the pin in `pyproject.toml`), `ls .git/hooks` vs
   `git config core.hooksPath`, and `time uv run pytest -q 2>&1 | tail -3` (expected on this code: 477 passed, 69 skipped). Do not touch
   `scripts/refresh_graphs.py`: this project has none; its graph script is `scripts/graph_sync.py`. The bootstrap and these checks are the only actions
   beyond the one-line file allowed in dryrun. Commit, push, stop. Do nothing else (no lock, no code).
2. EXIT IMMEDIATELY, doing nothing else and writing nothing, if any of these hold:
   - your session's rate limit is a hard stop (any status other than "allowed" or
     "allowed_warning"; the warning band is NOT a reason to stop: work, commit per step, and let
     the limit end the session);
   - HANDOFF.md says "Autonomy: off";
   - the Next action says Catalogue fixes is complete or "waiting for the user";
   - HANDOFF.md "Lock" is younger than 75 minutes (another session is alive).
3. Otherwise take the lock: set "Lock: <current ISO time>" in HANDOFF.md, commit, push. If that push
   is rejected, another session got there first: git pull --rebase, re-read "Lock"; if it is now
   held, EXIT as in step 2. Never force-push.
3b. Now read CLAUDE.md and docs/plans/CATALOGUE_FIXES_IMPLEMENTATION_PLAN.md (sections 2-5, 7-9).
4. If graphify or codebase-memory-mcp are not on PATH, run bash scripts/cloud_setup.sh; if
   graphify-out/graph.json is missing, run it (it also builds the graphs). If one of
   those two is still missing, that is a [BLOCKED] (step 8), not something to skip. graft is
   OPTIONAL (tier 2): its install can hang, so if it is missing continue without it, note it once in
   HANDOFF.md, and use graphify and codebase-memory-mcp instead. Run any install under
   `timeout -k 5 150`.
5. Act as the lead exactly as the plan says, from the Next action in HANDOFF.md. First read the Decisions table: an item `blocked` on a
   Decision whose Answer column is filled becomes `todo`; build exactly that answer. Never write an Answer, never answer a Decision yourself, and never
   build a blocked item. A new question goes into the table (plan section 2.9) and `TODO.md`; keep working on independent items. When only
   blocked items remain, set the Next action to "waiting for the user: <DEC ids>", clear the Lock, push, and stop (that is not a [BLOCKED]).
   Keep `TODO.md` live as you go. Refresh the lock
   timestamp in every commit and push each commit. Commit "WIP <id>" before any long step.
   Pass model "sonnet" for workers, at most 2 concurrent, lead approval for every item. If any push
   is rejected, git pull --rebase and retry (never force-push). If your context is compacted or you
   lose track, re-read HANDOFF.md and the plan sections before continuing.
6. At a phase end: the project's full check command (the "Full check" in plan section 8) green, push, wait for CI on the pushed head to be green
   (gh run list/watch), run /ponytail-review on the phase diff, then advance HANDOFF.md and
   record "[PHASE n DONE]" under Notifications. Continue into the next phase.
7. After Phase 4: write the final report, set Next action to "Catalogue fixes complete - waiting for the
   user", clear the Lock, commit "[Catalogue fixes COMPLETE]", push, and stop. Never merge.
8. If you are blocked (CI red you cannot fix, an item rejected twice by yourself, a missing
   tool), write it under Notifications as "[BLOCKED]", clear the Lock, push, and stop.
   Do not work around a denied permission.
```
