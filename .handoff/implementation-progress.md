# Implementation progress (feat/improvements)

Updated 2026-11-11. Stopped at 90% of the 5-hour limit (resets 14:20 UTC) on the lead's order.
Start no new work until the lead says the limit has reset. Details and evidence are in TODO.md.

## Done

- Part 1 and Part 3 code (earlier commits); rulings 1 to 6 and A to C of 2026-11-10 and
  2026-11-11; B3 code-written relationships (74ad38d, 2bf154a).
- Compose end-to-end run on this machine with the shipped compose file (TODO.md).
- T4 (loopback stand-in), T5 and T7 in throwaway `unshare -rn` namespaces; T7 comparison bug
  fixed with a failing test first (`tests/test_lan_boundary_check.py`).
- Deployment stamps set in ARCHITECTURE.md and TECHNICAL_IMPLEMENTATION.md §17.6.

## Open

- CI on 306fd6d (head; the docs lint fix for the Docs checks red since b715a82): pending at stop.
- Owner's approval for the remote TLS probe: not arrived in this session (2026-11-11).

- Remote TLS check per database: `not checked`. This session's permission check refused the
  probe; retry only after the owner approves it in this session.
- Requester scan status stays `queued` over the HTTP adapter (TODO.md, needs a decision).
- On the LAN host, with an owner yes: install the firewall, T4 from a LAN host and from off the
  subnet, T5, T6 after a Docker restart and a reboot, T7.

## Scratch to clean up (ask first; nothing deleted)

Session scratch: `dbig-images-0.1.0.tar`, `stack/` (throwaway CA, keys, config, rig scripts).
Docker: the `dbig` compose stack and its volumes, the container `dbig-e2e-pg`, images
`dbig-*:0.1.0`, `python:3.12-slim-bookworm`, `ghcr.io/astral-sh/uv:0.12.19`.
