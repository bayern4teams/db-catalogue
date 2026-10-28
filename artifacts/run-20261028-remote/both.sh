#!/usr/bin/env bash
# Combined catalogue of both bundles, then the 40 questions (parallel, see ask.py). Loads .env unprinted.
set -u; cd "$(dirname "$0")/../.."; R=artifacts/run-20261028-remote
set -a; eval "$(python3 -I -c '
import shlex
for l in open(".env",encoding="utf-8-sig"):
    if "=" in l and not l.lstrip().startswith("#"):
        k,v=l.split("=",1); k=k.strip(); v=v.strip()
        if v[:1] in "\"\x27" and v[-1:]==v[:1]: v=v[1:-1]
        if k.isidentifier() and k not in ("USER","IP"): print(f"{k}={shlex.quote(v)}")
')"; set +a
U="uv run --python 3.12"
rm -rf $R/snapshot-both $R/requester-both
$U dbig-builder assemble --config $R/config/builder.toml --bundle $R/bundle-pg --bundle $R/bundle-ch --out $R/snapshot-both >/dev/null && echo "assemble both exit 0" &&
$U dbig-builder render --config $R/config/builder.toml --snapshot $R/snapshot-both --bundle $R/bundle-pg --bundle $R/bundle-ch --out $R/catalog-both.html && 
$U dbig-requester index --config $R/config/requester-both.toml >/dev/null && echo "index both exit 0" &&
python3 $R/ask.py
