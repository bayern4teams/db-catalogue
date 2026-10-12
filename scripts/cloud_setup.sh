#!/usr/bin/env bash
# Cloud bootstrap, run by the supervisor tick when a tool is missing (docs/plans/catalogue-fixes/SUPERVISOR.md
# step 4). Same graph-tool installs as the environment's setup script, at this project's pins, then the
# project bootstrap. Fails loudly on a missing required tool; graft is best effort in the cloud only (user
# decision 2026-10-12). Not used by CI or local development.
set -euo pipefail
cd "$(dirname "$0")/.."

[ -x /opt/graphtools/bin/graphify ] || python3.11 -m venv /opt/graphtools
/opt/graphtools/bin/pip install -q graphifyy==0.9.65 codebase-memory-mcp==0.11.0
# graft's postinstall downloads a tree-sitter binary from GitHub and that download has hung, so install it
# once, bounded and killable, and never let it block or fail setup.
# An image may ship graft at another version; graph_sync.py refuses anything but the pin, so test the version.
graft_ok() { graft --version 2>/dev/null | grep -qx "0.18.0"; }
graft_ok || timeout -k 5 150 npm install -g @nanonets/graft@0.18.0 \
  || echo "setup: graft install failed or timed out; continuing without it" >&2
for t in graphify codebase-memory-mcp; do ln -sf "/opt/graphtools/bin/$t" "/usr/local/bin/$t"; done
for t in graphify codebase-memory-mcp; do command -v "$t" >/dev/null || { echo "setup: $t not on PATH" >&2; exit 1; }; done
graft_ok || echo "setup: graft missing or not 0.18.0 (optional in the cloud)" >&2

command -v uv >/dev/null || python3 -m pip install -q "uv==0.12.19"
uv sync --locked --all-packages --all-extras --dev --python 3.12

# All three tools: install the versioned hooks and let graph_sync.py build and check every graph. Without
# graft: no hooks (they require all three); build the other two by hand, and CI's graph-index job covers graft.
if graft_ok; then
  .venv/bin/python scripts/install_hooks.py
  .venv/bin/python scripts/graph_sync.py
else
  graphify update .
  codebase-memory-mcp cli index_repository --repo-path "$PWD" --mode fast --persistence false
fi
