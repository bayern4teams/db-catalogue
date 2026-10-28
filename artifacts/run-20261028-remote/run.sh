#!/usr/bin/env bash
# Usage: run.sh <pg|ch>. Needs the SSH tunnel: local 15401 -> remote 5401, 18101 -> remote 8101. Loads .env without echoing it; logs commands, exit codes and timings only.
set -u
cd "$(dirname "$0")/../.."
R=artifacts/run-20261028-remote; S=$1; C=$([ "$S" = pg ] && echo postgresql || echo clickhouse)
set -a; eval "$(python3 -I -c '
import json,shlex
for l in open(".env",encoding="utf-8-sig"):
    if "=" in l and not l.lstrip().startswith("#"):
        k,v=l.split("=",1); k=k.strip(); v=v.strip()
        if v[:1] in "\"\x27" and v[-1:]==v[:1]: v=v[1:-1]
        if k=="PG_CONNECTION": d=json.loads(v); d.update(host="127.0.0.1",port=15401); v=json.dumps(d)
        if k=="CLICKHOUSE_CONNECTION": d=json.loads(v); d.update(host="127.0.0.1",port=18101); v=json.dumps(d)
        if k.isidentifier() and k not in ("USER","IP"): print(f"{k}={shlex.quote(v)}")
')"; set +a
step(){ echo "### $1"; shift; echo "\$ $*"; local t=$(date +%s.%N); "$@"; local e=$?; echo "exit $e seconds $(python3 -c "print(round($(date +%s.%N)-$t,2))")"; return $e; }
cat > $R/config/requester-$S.toml <<T
[requester]
snapshot_dir = "$R/snapshot-$S"
state_dir = "$R/requester-$S"
api_key_env = "REQUESTER_TOKEN"
T
U="systemd-run --user --scope -p MemoryMax=4G -q uv run --python 3.12"
step "collect $S" $U dbig collect --config $R/config/$C.toml --out $R/bundle-$S &&
step "validate $S" $U dbig validate-bundle --key-env DBIG_SIGNING_KEY $R/bundle-$S &&
step "assemble $S" $U dbig-builder assemble --config $R/config/builder.toml --bundle $R/bundle-$S --out $R/snapshot-$S &&
step "render $S" $U dbig-builder render --config $R/config/builder.toml --snapshot $R/snapshot-$S --bundle $R/bundle-$S --out $R/catalog-$S.html &&
step "index $S" $U dbig-requester index --config $R/config/requester-$S.toml
