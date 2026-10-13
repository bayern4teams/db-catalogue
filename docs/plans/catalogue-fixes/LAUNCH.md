# Launch guide — Catalogue fixes (bayern4teams/db-catalogue)

Do these in order. Each step says what to check and what to send back.

## 0. Preconditions
`git pull --ff-only` on `feat/catalogue-fixes`. CI does not run on pushes to this branch: start it with `gh workflow run ci.yml --ref feat/catalogue-fixes` and wait for green (`gh run list --workflow ci.yml --branch feat/catalogue-fixes`). Confirm the remote: `git remote -v` shows `Reza-Sh83/db-catalogue` but `gh repo view` says `bayern4teams/db-catalogue`; the routine uses the latter, so make sure that is the repository you mean.

## 1. Put the kit on `main` (needs the user's explicit OK)
A routine always clones `main` and skills/agents/`.mcp.json`/`scripts/cloud_setup.sh` load at session
start, so they must be on `main`. This one fast-forward also carries the 14 commits that `main` lacks (it is at `8b96ca8`; the tested code is `2e87fa7`, CI green), the live-test
report in `artifacts/` (7 MB of catalogue metadata, no secrets or row values) and these kit files: `docs/plans/`, `.claude/agents/`, `.claude/skills/` (six vendored Ponytail skills, MIT,
license kept), `.mcp.json`, `scripts/cloud_setup.sh`, the CLAUDE.md pointer. Check first: `git log --oneline main..feat/catalogue-fixes` and `git diff --stat main feat/catalogue-fixes | tail -1`.
```bash
git checkout main && git merge --ff-only feat/catalogue-fixes && git push origin main && git checkout feat/catalogue-fixes
```
(If `main` has moved and a fast-forward is impossible, or the push is refused by branch protection, stop and ask; the alternative is a pull request from `feat/catalogue-fixes` that you merge yourself.)

## 2. Cloud environment
claude.ai/code -> create a NEW environment `catalogue-fixes-env` (so other cloud work is unaffected),
Network access Full, and paste [`environment_setup.sh`](environment_setup.sh) from this folder (verbatim) as its setup script. It uses this project's graph-tool pins (graphifyy 0.9.65, codebase-memory-mcp 0.11.0, graft 0.18.0), not the kit's. Add no secrets and no environment variables: the cloud never gets `.env`, database credentials or a model key.

## 3. Routine (create it in the claude.ai Routines UI, not from inside a session)
| Field | Value |
|---|---|
| Repository | bayern4teams/db-catalogue |
| Environment | `catalogue-fixes-env` |
| Model / effort | Opus 5.5, high |
| Trigger | every hour (cron `0 * * * *`) |
| Connector | GitHub |
| Instructions | the pointer below |

```
You are the Catalogue fixes supervisor tick. Run: git fetch origin feat/catalogue-fixes, then read
docs/plans/catalogue-fixes/SUPERVISOR.md with: git show origin/feat/catalogue-fixes:docs/plans/catalogue-fixes/SUPERVISOR.md
Follow the LAST fenced block in that file exactly. Do nothing else.
```
If the UI offers a push-permission option, allow pushes to non-`claude/` branches.

## 4. Dry run
```bash
sed -i 's/\*\*Autonomy:\*\* off/**Autonomy:** dryrun/' docs/plans/catalogue-fixes/HANDOFF.md
git add docs/plans/catalogue-fixes/HANDOFF.md && git commit -m "handoff: Autonomy dryrun" && git push origin feat/catalogue-fixes
```
Run the routine once (run-now), wait a few minutes, then:
```bash
git pull --ff-only && tail -1 .handoff/supervisor_dryrun.txt
```
All must be yes (the line records each): `uv` and Python 3.12 are available or installable, `gh workflow run ci.yml` is accepted by the cloud's GitHub token, setup log ends cleanly and the three graph tools are on PATH at session start, the six
Ponytail skills load, codebase-memory MCP tools present (or its CLI works), `git push` and `gh run list`
work. Send the line back if anything fails; the line also records the setup log.

## 5. Go live
```bash
git pull --ff-only
sed -i 's/\*\*Autonomy:\*\* dryrun/**Autonomy:** unattended-through-phase-5/' docs/plans/catalogue-fixes/HANDOFF.md
git add docs/plans/catalogue-fixes/HANDOFF.md && git commit -m "handoff: unattended" && git push origin feat/catalogue-fixes
```
Do not work on `feat/catalogue-fixes` locally while it runs.

## 6. Watch
```bash
git fetch origin && git log --oneline origin/feat/catalogue-fixes | head -8
git show origin/feat/catalogue-fixes:docs/plans/catalogue-fixes/HANDOFF.md | sed -n 1,10p
```
If the Next action says `waiting for the user`, answer the listed Decisions. Healthy: a recent `Lock:` and commits per item. `[PHASE n DONE]` = a phase finished. Act only on
`[BLOCKED]`. Silence after a limit stop is normal; the next hourly tick resumes after the reset.

## Answering a Decision
`HANDOFF.md` has a Decisions table; items that need your decision stay `blocked` until its `Answer` cell is filled. Only you write it:
```bash
git pull --rebase origin feat/catalogue-fixes   # then edit the Answer cell(s) in docs/plans/catalogue-fixes/HANDOFF.md
git add docs/plans/catalogue-fixes/HANDOFF.md && git commit -m "handoff: answer DEC-n" && git push origin feat/catalogue-fixes
```
If the push is rejected, a tick pushed first: `git pull --rebase` and push again. The next tick sees the answer and builds it. When only blocked items remain the supervisor writes "waiting for the user: <DEC ids>" and exits.

## Stop switch
Set `Autonomy: off` in HANDOFF.md, push, and disable the routine.
