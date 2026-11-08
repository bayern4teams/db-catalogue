# Phase 7 plan: F11-b, F14-F17 and a general benchmark. Local, multi-agent, lead-approved

**Status:** owner-approved 2026-10-20 (§8). New questions go to §8a and block only their item.
**Where:** local only (owner 2026-10-20: "build locally on new session"). The cloud routine is not used.
**Sources of truth:**
- Findings: `artifacts/run-20261020/step{1,2,3}-summary.md` and `artifacts/run-20261020-pgch/summary.md`.
- Research: `artifacts/run-20261020-pgch/research-f14-f17.md` (§F11-b is added by this plan's P7-0).
- Earlier F5-F13: `artifacts/run-20261014/leader/LEAD_REVIEW.md`.

## 1. Present state (checked by the lead 2026-10-20)

**Branches:**

| Branch | State |
|---|---|
| `main` | 111 commits behind `fix/live-findings`; nothing is merged without the owner's "merge it" |
| `fix/live-findings` at `d06dc25` | Phase 6 (F5-F13) done; CI 39296289133 green (8 jobs); local 903 passed, 70 skipped, 1 xfailed; attack files fail on the known 37 ids |
| `verify/live-20261020` at `4ae2e85` | `fix/live-findings` plus evidence, TODO and `.gitignore` only; no code differences; `4ae2e85` is not pushed (owner) |

**Live results without MongoDB** (owner: the MongoDB data is wrong, U01-U44 are all false):
- **Step 1:** 48 candidates, 35 true. All 13 false ones are PG->CH pairs (F11-b).
- **Step 2:** 56 candidates, 39 true. All 17 false ones are PG<->CH pairs. F15 does not occur.
- **Step 3:** 40 questions. answered 28, NOT_ESTABLISHED 9, NO_CATALOGUE_MATCH 2, `MODEL_UNAVAILABLE_OR_INVALID` 1 (N-S8). Bare verdicts: N-S5, N-I2, N-I4, N-I8. The owner grades in `step3-grading-sheet.md`.

**Answers vary run to run on the same input:**
- N-S2 was MODEL_INVALID_CITATION this morning, then answered twice.
- N-S4 went from NOT_ESTABLISHED to answered.
- N-S8 went from NOT_ESTABLISHED to rejected.

**Model server:** llama.cpp (llama-server) through a tunnel at 127.0.0.1:8000, Qwen3.8-Flash-Next rev 178b998. No `max_tokens` (DEC-18).

**What is collected today:**
- PostgreSQL: per-column `pg_stats` (distinct estimate, null fraction, uniqueness, width) and a `reltuples` row estimate.
- ClickHouse with the probe off: width only. With the probe on: distinct estimate and uniqueness from a capped sample.
- Value tokens (keyed HMAC, bottom-k 4096) from 25,000 sampled rows: PG 106 of 124 columns, CH 38 of 46.
- Not collected: min/max, length or format profiles, full-column sketches.

## 2. Operating rules (every agent)
Rules 2.2-2.9 of `docs/plans/CATALOGUE_FIXES_IMPLEMENTATION_PLAN.md` apply unchanged: tool order, Ponytail, no hardcoded values or corpus names, mechanical checks, known traps, independent scoring, decisions blocked and never guessed. Local differences:

1. **One work branch:** `fix/phase7` from `verify/live-20261020`. Never merge, rebase or force-push, and never touch `main`.
2. **Commits:** use `PATH="$PWD/.tmp/bin:$PATH" git commit ...` so the hooks find the pinned graph tools. Never bypass a failed hook.
3. **Databases and the model:** touched only by the lead, and only after the owner's explicit yes for that step. Workers and verifiers use stubs and fixtures only.
4. **Evidence:** names and counts only. Never values, `.env`, or `tokens.jsonl`. Run the secret scan (`.tmp/verify/secretscan.sh`) on every staged artifact.
5. **Subagents save tokens.** The lead keeps judgement: briefs, approvals, live runs, owner questions. Routine work goes to subagents with `model: sonnet`:
   - `catalogue-fixes-worker` implements one item;
   - `catalogue-fixes-verifier` writes acceptance tests before the code, then attacks the result;
   - `Explore` does read-only searches;
   - `general-purpose` does web research (no downloads without the owner's yes).

   Opus is used only for the lead.
6. **Keep `TODO.md` live,** with a "Current change: Phase 7" section. State lives in `docs/plans/phase7/HANDOFF.md` (items, decisions, next action).

## 3. Lead approval protocol (per item)
1. The verifier writes the acceptance tests first. They must fail on the current code.
2. The worker implements.
3. The verifier attacks the result.
4. The lead reads the diff and runs `/ponytail-review`.
5. Gate: the full suite, ruff, mypy, `ci_check`, `package_isolation`, and the attack files at exactly the known 37 failures plus any intended flips the lead names.
6. Commit.

An item is done only when its acceptance tests pass and the regression gate is green.

## 4. Items

### P7-0 Research record (done by the setup session)
§F11-b and the benchmark findings are in `research-f14-f17.md`.

### P7-1 F14: say "at least" for a lower bound (owner yes, option A)
**Where:** `dbig_builder/builder.py` `_overlap` evidence sentence (around line 395) and its metrics.

**Behaviour:**
- When `parent_complete` is false, or `lower_bound_only` is true and the comparison was not inside the common hash range, the sentence reads "value overlap at least X% of N sampled child values (parent only partly read; the true overlap may be much higher)".
- Otherwise it reads "≈X% (95% CI a-b%)". Use a Wilson interval on shared/N, with the confidence level as config.

**Metrics:** add `estimate`, `lower`, `upper`, `basis` (`measured` | `lower_bound`). The requester passes them to the model unchanged.

**Acceptance:**
- A partly read parent gives "at least"; a complete one gives a CI.
- The model context carries `basis`.
- Golden snapshots are regenerated, and the lead reviews the diff.

### P7-2 F15: measured evidence ranks above unmeasured (owner yes, option A; declared keys first)
**Where:** `builder.py` `rank()` (around line 706).

**Behaviour:**
- Compute `_overlap` for all name-filtered candidates before sorting. The zero-drop moves with it, with identical results.
- Sort key: `(-name_score, claim_is_declared ? 0 : 1, evidence_tier, -containment, other_source, claim, id)`, where `evidence_tier` is measured 0, lower bound 1, unmeasured 2.

**Acceptance:**
- **F15 shape:** a weak, capped key versus a measured-unique parent at 100% containment, equal names, `top_k` 1. The measured parent is kept.
- A declared key still wins.
- An offline replay of `artifacts/run-20261020` (MongoDB bundles, no database) restores the 2 cut `diffbot -> pg.location` pairs.
- The PG+CH candidate set is unchanged, or every difference is listed.

### P7-3 F16: complete paths through bridge tables (owner yes, option A)
**Where:** requester `search.py` `relationships_for` and its caller in `answer.py`.

**Behaviour:**
- After retrieval, run a stdlib breadth-first search over the requester's `relationships` table between the retrieved tables.
- Hop limit `path_max_hops` and bridge cap `path_max_bridge_tables` live in `[requester.answer]`; defaults 4 and 4.
- Each added bridge table appears as `role: path_only`, with only its key columns and its relationship lines.
- A path that rests on any candidate edge keeps `status: candidate`.
- No new dependency. The requester does not import the planner (`package_isolation`).

**Acceptance:**
- **Fixture A -> companyindustry-like bridge -> B:** a 2-hop path appears.
- **A 3-hop fixture:** appears when the limit is 3 or more, and does not at 2.
- **N-S4, N-S8 and N-I10, checked with the stub context recorder** (no model): the expected path's tables reach the context.

### P7-4 F17: no bare verdicts (owner yes, option A)
**Where:** `answer.py` `Answer` and `answer_question`, plus the system prompt.

**Behaviour:**
- Add `checked: list[str]` as the first field; all fields are required.
- In the app, reject an empty `checked`, a blank `answer`, or an `answer` that is only an outcome word. Return a new code `MODEL_ANSWER_UNEXPLAINED`.
- Allow `answer_retries` attempts (config, default 1), each re-sent with a fixed correction message that contains no data.
- Telemetry gets the code and the attempt count only.
- No schema `minLength`/`minItems` (B1 stays).
- Thinking stays on (owner).

**Acceptance:**
- `ModelStub`: a bare "not_established" leads to a retry, then the error code. An explained reply passes. Whitespace fails.
- The B1 test still passes.
- `TECHNICAL_IMPLEMENTATION.md` §12a lists the new code.

### P7-5 F11-b: general control of cross-source candidates (owner: A + B + E now, C next, D later)
These options are general for any databases; none is tuned to this deployment.
- **A. Evidence tiers and owner decisions:**
  - Every candidate gets a tier: `verified` (declared, or measured containment > 0 on a complete parent), `plausible` (name and type agree, containment inconclusive), or `unverified_cross_source` (a different source with no positive measurement).
  - `unverified_cross_source` is shown in the catalogue page, but kept out of the model context and the planner unless `[requester.answer] include_unverified_cross_source = true`.
  - An optional owner decisions file (`[builder] decisions_file`, JSON: child, parent, `confirmed`|`rejected`) overrides the tier. This is the pattern catalogue products use: suggest, and the user confirms.
- **B. Conclusive zero by expected overlap (statistics, no new collection):**
  - Expected shared values if the link were true at containment `c_min`: `E = n_child_in_range × f_parent × c_min`, where `f_parent` = parent distinct values read / the parent's distinct estimate.
  - If `E >= min_expected_shared` (config, e.g. 5, so P(0 | true) ≈ e^-5 < 1%) and none are observed, the zero is a measurement and the candidate drops.
  - `c_min` (config) states the weakest link the catalogue promises to find.
  - Needs the parent's distinct estimate: present for PostgreSQL; ClickHouse needs the probe, or one `uniqCombined` per key column.
- **C. Cheap value-free profiles:**
  - Per column: length min/max/avg, a character-class signature (digits / hex / uuid / alpha share), and a dominant format token.
  - Reject on disjoint formats above a share threshold. One aggregate query per column.
  - Numeric min/max ranges are real values: local only, never in traces or to the model; the exported artifact keeps only a boolean "ranges overlap".
  - (D3L, NextiaJD, Rostin's out-of-range feature, Auctus range pruning.)
- **D. Coordinated hash sampling in the database:**
  - Choose rows by a hash both engines compute the same way (e.g. `md5` of the normalised text in PostgreSQL and ClickHouse), so both sides are complete inside one hash range and a zero is exact.
  - This is F5 option C. Most accurate; a full-column scan per key column; MongoDB has no `md5` in aggregation (*check*).
- **E. `allowed_source_pairs`** in the builder config: the default is all pairs (no change); an owner can list the pairs that may link.

**Acceptance (per chosen option):** fixture tests, then the live PG+CH run gives 0 PG<->CH candidates in the model context and planner, and 0 owner-true pairs lost.

### P7-6 Regression gate and live re-check (lead, owner yes per run)
1. Re-run steps 1-3 on PG+CH as in `run-20261020-pgch`.
2. Each of the 40 questions runs **3 times**.
3. The owner grades.
4. Record S2 and S8 replies if they fail again (owner yes).

### P7-7 General benchmark (owner yes; layout in §8; every compose file is shown to the owner before it is written or started)
**Data already on disk** in `/home/bayern/Projects/benchmark/datasets`; no new download is needed:
- **Declared-FK ground truth:**
  - Pagila (about 22 tables) and Chinook (11) in PostgreSQL;
  - TPC-H SF1 (8 tables, 8 FKs by the spec), split across PostgreSQL and ClickHouse, so that every cross-engine FK is a known true cross-source link.
- **Real shared identifiers across sources:** GLEIF LEI golden copy (3,458,719 records, counted by the lead) in ClickHouse;
  - France SIRENE in PostgreSQL: 164,048 LEI records name RA000189 (INSEE);
  - Norway Enhetsregisteret (in PostgreSQL, or in MongoDB if wanted): 63,396 LEI records name RA000472 (Brønnøysund).
  - True links: `RegistrationAuthorityEntityID` = siren or organisasjonsnummer (to be checked after loading). Sirene <-> Enhetsregisteret gives a 2-hop test through GLEIF.
- **Unrelated sources (must give 0 links):** raha sets, nyc-taxi, ontime, uk-price-paid, openaddresses-lu.

**Stack:**
- A new compose project `dbcat-bench` in `/home/bayern/Projects/benchmark/dbcat-bench/compose.yaml`.
- Services `dbcat-bench-pg` (postgres:16-alpine, local image) and `dbcat-bench-ch` (clickhouse/clickhouse-server:25.12, local image), plus optionally `dbcat-bench-mongo` (mongo:7).
- Named volumes `dbcat_bench_pg_data` and `dbcat_bench_ch_data` (and `dbcat_bench_mongo_data`).
- Free ports bound to 127.0.0.1 only (proposed 5440, 8126/9003, 27020). Read-only `dbig_reader` accounts.
- Disk: 82 GB free on /home; the data needs about 3 GB loaded (TPC-H SF10 not used).

**Score:**
- Precision and recall against declared FKs, TPC-H FKs and the GLEIF links.
- False-link count among the unrelated sources.
- 10-20 new questions per dataset with expected answers. Drafted by a Sonnet agent from names and types only; the owner approves them before any run.

### P7-8 F18: a value-free reason for model failures (owner yes 2026-10-20)
**Where:** `answer.py` `_failure_code` and the result it builds.

**Behaviour:**
- Keep the public code, and add `reason` holding structure only: exception class, schema error types (e.g. `missing`, `extra_forbidden`), `finish_reason`, reply length, and the citation check that failed (`unknown_id` | `none`).
- Never text, IDs from the reply, or values. The CLI output and telemetry carry it.

**Acceptance:**
- One `ModelStub` test per cause.
- A test that `reason` never contains reply text, using a canary string.

## 5. Order
P7-0 → P7-8 → P7-1 → P7-4 → P7-2 → P7-3 → P7-5 (as answered) → P7-6 → P7-7 (if approved). The lead may run independent items in parallel. They own disjoint files: builder (P7-1, P7-2, P7-5), requester (P7-3, P7-4, P7-8).

## 6. Definition of done for Phase 7
- Every approved item meets its acceptance.
- Regression gate green; CI started with `gh workflow run ci.yml --ref fix/phase7` and green in all 8 jobs.
- Live re-check graded by the owner; `HANDOFF.md` and `TODO.md` updated; evidence committed and pushed with the owner's yes.
- No merge.

## 7. Known limits
- The model's answers vary from run to run, so one run proves little (hence N repeats).
- F15 cannot be shown live without MongoDB (offline replay only).
- Option B is a statistical test: at `c_min` it misses links weaker than `c_min` by design.

## 8. Owner decisions (2026-10-20; copy to `docs/plans/phase7/HANDOFF.md` as DEC-23 onward)
1. **F11-b:** A + B + E now; C next; D later. B: `c_min` 0.25, `min_expected_shared` 5 (both config, with these as documented defaults).
2. **F16:** `path_max_hops` 4 (the same as the planner), `path_max_bridge_tables` 4 (config).
3. **Benchmark:** yes, with datasets from `/home/bayern/Projects/benchmark`. **One compose project per dataset**, each in its own folder `/home/bayern/Projects/benchmark/dbcat-bench/<dataset>/compose.yaml`, with its own container and named volume, local images only, ports on 127.0.0.1 only, and a read-only `dbig_reader` account. Layout:
   - `pagila` -> PostgreSQL
   - `chinook` -> PostgreSQL
   - `tpch-sf1` split: customer, part, supplier, nation, region -> PostgreSQL; orders, lineitem, partsupp -> ClickHouse
   - `gleif` -> ClickHouse
   - `sirene` -> PostgreSQL
   - `enhetsregisteret` -> MongoDB
   - negatives: `nyc-taxi` -> ClickHouse, `raha-hospital` -> PostgreSQL, `raha-beers` -> MongoDB

   Before writing or starting anything, the lead shows the owner each compose file (names, volumes, ports) and waits for a yes.
4. **Other running containers:** not included.
5. **F17:** thinking stays on.
6. **Base of `fix/phase7`:** `verify/live-20261020`.
7. **Repeats:** 3 per question in P7-6.
8. `4ae2e85` pushed.
9. **N-S8 recorded once:** NOT_ESTABLISHED with 6 allowed citations. The earlier `MODEL_UNAVAILABLE_OR_INVALID` did not recur, and its cause is not recorded by the app (see §8a).

## 8a. Open owner decisions
(filled as they arise)
