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
