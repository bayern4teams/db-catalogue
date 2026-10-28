# Builds REPORT.md from the run outputs (names, counts and model answers only; no data values).
import json, collections, subprocess
R = "artifacts/run-20261028-remote"
man = lambda p: json.load(open(f"{R}/{p}/manifest.json"))
cnt = lambda m: {f["path"]: f["count"] for f in m["files"]}
tiers = lambda p: ", ".join(f"{n} {t}" for t, n in sorted(collections.Counter(json.loads(l)["evidence_tier"] for l in open(f"{R}/{p}/edges.jsonl")).items()))
def prof(p):
    x = [json.loads(l) for l in open(f"{R}/{p}/profiles.jsonl")]
    return len(x), sum(1 for i in x if i.get("formats"))
def steps(f):
    out, cur = [], None
    for l in open(f"{R}/{f}"):
        if l.startswith("### "): cur = l[4:].strip()
        elif l.startswith("exit ") and cur: out.append(f"{cur}: exit {l.split()[1]}, {l.split()[3]} s"); cur = None
    return out
A = json.load(open(f"{R}/answers-both.json"))
def outcome(v):
    if v.get("error"): return "refused: " + v["error"]["code"]
    if v.get("code") == "NOT_ESTABLISHED_BY_CATALOGUE": return "not in catalogue"
    return "answered"
oc = collections.Counter(outcome(v) for v in A.values())
secs = sorted(v["seconds"] for v in A.values())
bp, bc, bb = man("bundle-pg"), man("bundle-ch"), man("snapshot-both")
pp, pc = prof("bundle-pg"), prof("bundle-ch")
L = []
w = L.append
w("# Live test report — Diffbot (PostgreSQL) and ZoomInfo (ClickHouse), 2026-10-28\n")
w("**Product:** db-catalogue, branch `fix/phase7` (all offline tests and local CI green).  \n**Model:** `Qwen/Qwen3.6-35B-A3B-FP8`, revision `95a723d08a9490559dae23d0cff1d9466213d989`, served by vLLM on the GPU host.  \n**How to run it yourself:** see [GUIDE.md](GUIDE.md) — every CLI command, configuration, secret, tunnel and HTTP API.  \n**Rule:** names, counts and model answers only. No data values, credentials or host addresses appear here.\n")
w("## 1. Summary\n")
w("| Stage | Diffbot (PostgreSQL 16.15) | ZoomInfo (ClickHouse) | Both together |\n|---|---|---|---|")
w(f"| Connect (encrypted SSH tunnel) | PASS | PASS | — |")
w(f"| Collect metadata + value-free profiles | PASS, {bp['completeness']} | PASS, {bc['completeness']} | — |")
w("| Verify bundle signature | PASS | PASS | PASS |")
w(f"| Build catalogue | PASS | PASS | PASS, {bb['completeness']} |")
w("| Render HTML catalogue | `catalog-pg.html` | `catalog-ch.html` | `catalog-both.html` |")
w("| Search index | PASS | PASS | PASS |")
w("| Join paths + read plans (planner) | — | — | PASS |")
w(f"| 40 questions to the model | — | — | **40/40 returned**: {oc['answered']} answered, {oc['not in catalogue']} \"not in catalogue\", {sum(v for k,v in oc.items() if k.startswith('refused'))} refused |")
w("\nEach database was first tested **separately** (its own bundle, catalogue and index). The questions then ran against **one catalogue holding both databases**, as the owner asked.\n")
w("## 2. Stages in order, with timings\n")
w("**Diffbot alone** (`pg-commands.txt`):\n"); [w(f"- {s}") for s in steps("pg-commands.txt")]
w("\n**ZoomInfo alone** (`ch-commands.txt`):\n"); [w(f"- {s}") for s in steps("ch-commands.txt")]
w("\n**Both databases** (`both-commands.txt`): assemble exit 0; render exit 0; index exit 0; 40 questions, 6 in parallel.")
w(f"Answer time per question: min {secs[0]} s, median {secs[len(secs)//2]} s, max {secs[-1]} s.\n")
w("**Planner** (`planner-commands.txt`): build exit 0 (38 reference edges); join-paths from Diffbot `companyexperience` to ZoomInfo `Person`: default = no path (cross-source links with no measured overlap are hidden); with `--include-unverified-cross-source` = 2 tentative paths, flagged `CROSS_SOURCE_CANDIDATES_UNVERIFIED`; `plan` produced a PostgreSQL read, a parameterised ClickHouse read and a logical join, and listed `approved_staging_or_federated_target` and `final_cross_source_join` as unresolved prerequisites (it never executes anything).\n")
w("## 3. Numbers\n")
w("| | Diffbot | ZoomInfo | Both |\n|---|---|---|---|")
w(f"| Tables | {cnt(bp)['objects.jsonl']} | {cnt(bc)['objects.jsonl']} | {cnt(bp)['objects.jsonl']+cnt(bc)['objects.jsonl']} |")
w(f"| Columns | {cnt(bp)['fields.jsonl']} | {cnt(bc)['fields.jsonl']} | {cnt(bp)['fields.jsonl']+cnt(bc)['fields.jsonl']} |")
w(f"| Declared keys/constraints | {cnt(bp)['constraints.jsonl']} | {cnt(bc)['constraints.jsonl']} | |")
w(f"| Column profiles / with format profile | {pp[0]} / {pp[1]} | {pc[0]} / {pc[1]} | |")
w(f"| Catalogue nodes | {cnt(man('snapshot-pg'))['nodes.jsonl']} | {cnt(man('snapshot-ch'))['nodes.jsonl']} | {cnt(bb)['nodes.jsonl']} |")
w(f"| Join candidates by evidence tier | {tiers('snapshot-pg')} | {tiers('snapshot-ch')} | {tiers('snapshot-both')} |")
w("\nAll join candidates are `tentative`: they wait for human review before anyone relies on them.\n")
w("## 4. Findings and changes made today\n")
w("1. **Query budget raised (owner decision).** With the default `max_queries = 50`, Diffbot's optional format profiles stopped after the budget (78 not read, bundle `partial`). Raised to 200 in `config/postgresql.toml`; the re-run is `complete` with 111 format profiles.")
w("2. **Security guard worked.** The collector refused an unencrypted connection to the remote address; the run used an SSH tunnel. Credentials came only from `.env` and appear in no output file (checked by scanning the run folder).")
w("3. **Cross-source links are conservative.** 3 Diffbot↔ZoomInfo candidates exist (person, location, company experience) but are `unverified_cross_source`: no value overlap was measured, so search, answers and the planner hide them unless explicitly asked.")
w("4. **ZoomInfo uses ReplacingMergeTree** (known, owner-recorded): counts are after de-duplication.")
w(f"5. **Model refusals.** {oc.get('refused: MODEL_INVALID_CITATION',0)} replies gave an answer with **no citation at all**, so the requester refused them (`MODEL_INVALID_CITATION`, check `none`); every shown answer must cite catalogue items it was given; {oc.get('refused: NO_CATALOGUE_MATCH',0)} questions found no matching catalogue item (`NO_CATALOGUE_MATCH`). Refusing is the designed behaviour: no unsupported answer is shown as fact.")
w("6. **The 40 questions were written for the earlier test databases** (run of 2026-10-20). Some refer to tables or columns that these databases do not have, which explains several \"not in catalogue\" results. **Correctness has not been graded yet**; grading is the owner's step.\n")
w("## 4a. Spot check of answers (5 of the 24 answered, every 5th; checked against the catalogue collected live today)\n")
w("| Question | Verdict | Why |\n|---|---|---|")
w("| N-D1 fiscal-year column and type | correct | `companyfinancials.year`, integer, matches the catalogue |")
w("| N-D7 investment amount and currency columns | correct | `companyinvestmentround.amount` bigint, `.currency` text |")
w("| N-S3 ClickHouse person → job titles → company names | correct | Person→Experience on `personID`, Experience→Company on `companyID`; both candidates exist, measured overlap stated honestly |")
w("| N-S10 employee category by city (PostgreSQL) | correct | join on `companyid` (verified, ≈97% overlap); also gives the route via `company` |")
w("| N-C6 numeric non-version columns (PostgreSQL) | **incomplete** | named 2 of 4: missed `companyfinancials.amount` and `.year`. Cause: \"list all\" questions see only the top retrieved items (`retrieval_limit = 10`) |")
w("\n**Spot check: 4 of 5 correct, 1 incomplete.** Not a full grading.\n")
w("## 4b. What was done today, in order\n")
for i, t in enumerate([
    "Checked access to the remote host: SSH by key works; Docker readable; `sudo` needs a password (not used).",
    "Found the databases: `diffbot-pg` (PostgreSQL, db `testdiffbot`, schema `diffbot`) and `zoominfo-ch` (ClickHouse, db `testzoominfo`). The `bench-*` containers were not touched.",
    "Wrote one collector config per database (`config/`), sampling on, format profiles on.",
    "First collect was refused by the plain-text guard (remote address without TLS). Opened an SSH tunnel; the collector then connected to localhost only.",
    "Diffbot alone: all steps passed but the bundle was `partial` (format profiles stopped at the query budget of 50). Owner raised it to 200; re-run `complete`.",
    "ZoomInfo alone: all steps passed, `complete`.",
    "Model: owner's tunnel to vLLM; model name `Qwen/Qwen3.6-35B-A3B-FP8` and revision `95a723d0…` supplied by the owner.",
    "Both databases in one catalogue; 40 questions, 6 in parallel, 1 repeat.",
    "Planner: graph built, cross-database join paths found (hidden by default, shown with the flag), read plan written, nothing executed.",
    "Spot check of 5 answers (section 4a).",
    "Report, guide and evidence committed (`29b8e82` and later) to branch `fix/phase7` and pushed to GitLab. Raw bundles, `tokens.jsonl` and `ranges.jsonl` stay local.",
], 1): w(f"{i}. {t}")
w("\n## 4c. Problems met while running, and the fix (you will meet these too)\n")
w("| Problem | Message | Fix |\n|---|---|---|")
w("| Plain connection to a remote database | `tls = false is allowed only for loopback IP literals` | enable TLS on the server, or SSH tunnel to `127.0.0.1` (GUIDE §3) |")
w("| Bundle check needs the key name | `Missing option '--key-env'` | `dbig validate-bundle --key-env DBIG_SIGNING_KEY <bundle>` |")
w("| Format profiles cut short | diagnostic `FORMAT_PROFILES_SKIPPED`: \"78 format profiles not read: query budget reached\" | raise `max_queries` in the source config (200 here) |")
w("| Requester config without model revision | `model_digest` field required | set `model_digest` to the model's Hugging Face revision (`refs/main` on the GPU host) |")
w("| Planner given a file for observations | `UNSAFE_OBSERVATION_DIRECTORY` | `--observations` must be a directory (an empty one is fine) |")
w("| \"List all\" questions incomplete | answer names only some items | known limit: the model sees the top `retrieval_limit` (10) items; raise it or ask narrower questions |")
w("\n## 4d. Security note\n")
w("- The database password is written in plain text in the remote host's README (Stacks table) and was shown once in the operator's console during setup. It is in **no file of this run**. **Action: change the database passwords** and keep them only in `.env`.\n")
w("## 5. Not done\n")
w("- 3 repeats per question (Phase 7 plan): this run is 1 repeat. `python3 ask.py` repeats it.\n- Grading of the answers (owner).\n- Measured value overlap between Diffbot and ZoomInfo (needs both collected with the same token key and overlapping columns; the cross-source candidates stay unverified until then).\n")
w("## 6. Model answers (all 40)\n")
w("Outcome legend: **answered** — the model answered with citations that were checked against the catalogue; **not in catalogue** — the model checked and the catalogue does not show it; **refused** — the requester rejected the reply or found nothing to retrieve.\n")
for k, v in A.items():
    cites = ", ".join(sorted({f'{c.get("source_id")}.{c.get("object_name") + "." if c.get("object_name") else ""}{c.get("name")}' for c in v.get("cited") or []}))
    w(f"### {k} — {outcome(v)} ({v['seconds']} s)\n\n**Q:** {v['question']}\n")
    if v.get("answer"): w("**A:** " + v["answer"].replace("\n", " ") + "\n")
    if cites: w(f"**Cited:** {cites}\n")
w("## 7. Catalogue output (both databases)\n")
w("Full interactive page: `catalog-both.html`. Text form below (names and types only).\n")
w(subprocess.run(["python3", f"{R}/catalogue_summary.py"], capture_output=True, text=True, check=True).stdout)
w("## 8. Files in this folder\n")
w("| File | What |\n|---|---|\n| `GUIDE.md` | how to run everything, all APIs |\n| `config/` | all configuration used |\n| `run.sh`, `both.sh`, `ask.py` | the exact scripts run |\n| `*-commands.txt` | command logs with exit codes and timings |\n| `catalog-pg.html`, `catalog-ch.html`, `catalog-both.html` | catalogue pages |\n| `answers-both.json` | raw model output for the 40 questions |\n| `bundle-*`, `snapshot-*`, `planner-graph/` | machine-readable artifacts (`tokens.jsonl`, `ranges.jsonl` stay local) |")
open(f"{R}/REPORT.md", "w").write("\n".join(L) + "\n")
print(oc)
