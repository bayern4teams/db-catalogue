# Paste as the cloud environment's SETUP SCRIPT (claude.ai/code -> environment -> Setup script).
# Self-contained on purpose: the setup script's working directory is NOT the repo. Rules learned the
# hard way (phase-kit LESSONS.md 3 and 4): (1) never pipe output through `tee`/process substitution:
# an orphaned child (graft's postinstall downloader) kept the pipe open and the setup phase hung for
# hours; redirect to a FILE. (2) every command is bounded with `timeout -k`. (3) graft is optional in
# the cloud (user decision 2026-10-12), so it is installed detached and can never block or fail setup.
# (4) the session must start even if an install fails. Only the graph tools are installed here; the
# supervisor runs `scripts/cloud_setup.sh` (uv sync, hooks, graph build) inside the tick, where it is visible.
# Versions are this project's pins ([tool.dbig.graph-tools] in pyproject.toml); scripts/graph_sync.py
# refuses any other version, so change them only together with pyproject.toml and ci.yml.
{
date
timeout -k 5 300 python3.11 -m venv /opt/graphtools || echo "FAILED: venv"
timeout -k 5 600 /opt/graphtools/bin/pip install -q --default-timeout=30 graphifyy==0.9.65 codebase-memory-mcp==0.11.0 || echo "FAILED: pip"
for t in graphify codebase-memory-mcp; do ln -sf /opt/graphtools/bin/$t /usr/local/bin/$t; done
setsid nohup timeout -k 5 300 npm install -g @nanonets/graft@0.18.0 > /tmp/graft_install.log 2>&1 < /dev/null &
for t in graphify codebase-memory-mcp; do command -v $t || echo "MISSING: $t"; done
date
} >> /tmp/cloud_setup.log 2>&1 < /dev/null
