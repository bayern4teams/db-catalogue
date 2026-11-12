# Implementation progress (feat/improvements)

Updated 2026-11-11 after the limit reset. The scope the lead set is done, the remote TLS check included. Details and evidence are in TODO.md.

## Done

- Part 1 and Part 3 code (earlier commits); rulings 1 to 6 and A to C of 2026-11-10 and
  2026-11-11; B3 code-written relationships (74ad38d, 2bf154a).
- Compose end-to-end run on this machine with the shipped compose file (TODO.md).
- T4 (loopback stand-in), T5 and T7 in throwaway `unshare -rn` namespaces; T7 comparison bug
  fixed with a failing test first (`tests/test_lan_boundary_check.py`).
- Deployment stamps set in ARCHITECTURE.md and TECHNICAL_IMPLEMENTATION.md §17.6.
- CI: Docs checks were red from b715a82 to 734629c (markdownlint MD012 and MD047, then five
  Vale errors); fixed in 306fd6d and bdbf3e7. GitHub Rule checks and Docs checks pass on
  bdbf3e7. GitLab shows no pipelines for this branch.

## Next: owner "Yes, fix all", 2026-11-12 (relayed by the lead): 10 review findings

Stopped before starting at 78% of the 5-hour limit (80% rule; resets 09:50 UTC). Failing test first
for each bug (D3, L3); check each against the code first and skip one that is wrong, saying why.
Checked so far against the code (2026-11-12), nothing changed yet:
- 8 confirmed: `dbig_contracts.service::ServiceGuard` sends 413 when Content-Length is missing
  (`"".isdecimal()` is false), even with an empty body.
- 9 confirmed: one `timestamps` deque for all callers.
- 3 confirmed for `dbig_contracts.jobs::requeue_running`: no attempt count; `scan.requeue_running`
  not read yet.
- 1, 2, 4, 5, 6, 7, 10: not checked yet.
The findings, as relayed:
1. `deploy/lan_firewall.py` ip6tables branch drops forwarded IPv6 for every container; scope it to
   our compose network or bridge (B4).
2. `_apply`: a failed append after the flush leaves the chain without its final DROP; build a new
   chain and swap the DOCKER-USER jump atomically (or iptables-restore).
3. `jobs.requeue_running`, `scan.requeue_running`: attempt count; after N crash requeues, fail
   with a clear code.
4. builder `api.run_one`: a requeued job whose snapshot directory exists fails with
   FileExistsError; finish a complete snapshot (point `current`) or remove a partial one safely.
5. `security.private_host_refusal`: the name is resolved twice (check, driver); connect to the
   checked address with the name kept for TLS, or record why not.
6. `search.question_type` needs "type"/"typed": "show integer columns", "which columns are dates?"
   must reach the type filter, "what date did it run" must not; replay N-C4 and the owner examples.
7. LIST_WORDS: "all"/"every" anywhere switches to list mode; "Are all orders linked to a
   customer?" must not.
8. ServiceGuard: missing length with an empty body counts as 0; keep the bound for real and
   chunked bodies.
9. ServiceGuard rate limit per caller key.
10. `search.bridge_paths` and `chained_paths` each load `_visible_edges`; load once per question.
Rules: capped batches, ruff, mypy, rule_check, Docs: line per commit, push, CI green (start ci.yml
by workflow_dispatch), no merge; firewall tested only in a throwaway namespace.

## Open

- Remote TLS (D2): on for all five, verified (TODO.md). Collector over TLS complete for diffbot-pg and
  zoominfo-ch; bench-pg, bench-ch and bench-mongo wait for credentials from the owner. zoominfo-ch has a
  pre-existing merge error on one part (TODO.md); reported, not touched.


- Requester scan status stays `queued` over the HTTP adapter (TODO.md, needs a decision).
- On the LAN host, with an owner yes: install the firewall, T4 from a LAN host and from off the
  subnet, T5, T6 after a Docker restart and a reboot, T7.

## Scratch to clean up (ask first; nothing deleted)

Session scratch: `dbig-images-0.1.0.tar`, `stack/` (throwaway CA, keys, config, rig scripts).
Docker: the `dbig` compose stack and its volumes, the container `dbig-e2e-pg`, images
`dbig-*:0.1.0`, `python:3.12-slim-bookworm`, `ghcr.io/astral-sh/uv:0.12.19`.
