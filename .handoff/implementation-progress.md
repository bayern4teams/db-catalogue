# Implementation progress (feat/improvements)

Stopped 2026-11-10 at 92% of the 5-hour plan limit (80% rule). Resume from here.

## Done

- Part 1: A1 A3 A5 A6 B1 B2 B4 B5 (no fix) B8 C2 D1 D3 D4 D5 D6.
- Owner rulings of 2026-11-10 recorded (TODO.md; TECHNICAL_IMPLEMENTATION.md §16 item 12).
- Test rulings 1 to 4; DEC-1 and DEC-2 fixed (commit f32e0a0).
- B6 type words and B7 list mode (f32e0a0). Replay: N-C4 20 of 20, N-C6 4 of 4.
- Part 3, code (this commit): bind rule, caller keys, `ServiceGuard`, `/healthz`, secret
  `_FILE`, TLS on every `serve`, collector worker and crash requeue, builder service and jobs,
  requester reindex, planner graph, contracts service, `deploy/` (Dockerfile, compose,
  firewall, systemd unit, env template), `scripts/build_images.py`,
  `scripts/lan_boundary_check.py`, T1 in `scripts/ci_check.py`, cert script `--ip`.
- Images built here, `docker save` to one 123 MB tarball (scratch, not committed). Each
  image's `serve --help` runs.
- D2 checked on local containers: collector image at private IPs, verified TLS, private CA,
  all three engines complete and `tls-verified`; another CA refused by all three.

## Next, in order

1. B3 (D8): waits on the researcher's answer on the response shape (asked 2026-11-10).
2. Full-stack smoke test: `docker compose up` with generated secrets and config in scratch,
   LAN_IP=127.0.0.1, `/healthz` on all five, one assembly, reindex and graph through the API.
3. Stamp `<!-- verified: PENDING -->` in ARCHITECTURE.md (Deployment) and
   TECHNICAL_IMPLEMENTATION.md §17.6 with the commit they were checked against; then D2 stamps.
4. Remote host: report what it needs (below); do not contact it.

## Waiting on rulings (sent to the lead)

- `test_dec7_retrieval_limit_bounds_object_listing` vs list mode; the canary test vs `paths`.
- Skip DEC-18 expansion for type questions, or keep.
- C5 approval text for new `noqa: S603` lines; T1 on GitLab (no Docker in the policy image):
  PyYAML as a dev dependency, or Docker in that job.
- L4 on the database host: two tests use host "synthetic"; `mongodb+srv://` would be refused.

## What the remote host needs for D2 (not checked there)

Each database serves TLS with a certificate whose SAN is the IP the collector connects to,
issued by the private CA (`scripts/make_ci_certs.py --name <db> --ip <address>`). The SSH
tunnel is no longer used. Docker with the `iptables` backend; the firewall unit installed with
an owner yes.

## Cleanup to ask about (nothing deleted)

Scratch: `dbig-images-0.1.0.tar`, `d2/` (bundles owned by uid 10001), `replay-state/`.
Docker: images `dbig-*:0.1.0`, pulled `python:3.12-slim-bookworm`, `ghcr.io/astral-sh/uv:0.12.19`.
