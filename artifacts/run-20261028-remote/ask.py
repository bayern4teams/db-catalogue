# Asks the 40 questions of run-20261020-pgch once each, WORKERS at a time; writes answers-both.json.
import json, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor
R = "artifacts/run-20261028-remote"
WORKERS = 6
qs = {k: v["question"] for k, v in json.load(open("artifacts/run-20261020-pgch/step3-answers.json")).items()}
def ask(item):
    qid, q = item; t = time.time()
    p = subprocess.run(["systemd-run", "--user", "--scope", "-p", "MemoryMax=1500M", "-q", "uv", "run",
                        "--python", "3.12", "dbig-requester", "ask", "--config", f"{R}/config/requester-both.toml",
                        "--question", q], capture_output=True, text=True)
    try: out = json.loads(p.stdout)
    except ValueError: out = {"raw_stdout_bytes": len(p.stdout)}
    print(qid, "exit", p.returncode, round(time.time() - t), "s", file=sys.stderr, flush=True)
    return qid, {"question": q, "exit": p.returncode, "seconds": round(time.time() - t, 2),
                 "stderr_tail": p.stderr[-300:] if p.returncode else "", **(out if isinstance(out, dict) else {"answer": out})}
with ThreadPoolExecutor(WORKERS) as ex:
    res = dict(ex.map(ask, qs.items()))
json.dump(res, open(f"{R}/answers-both.json", "w"), indent=1, default=str)
print("answered", sum(1 for v in res.values() if v["exit"] == 0), "of", len(res))
