# Local verification checklist — what the cloud could not prove

Everything here is **implemented, unverified**. The user runs each entry locally against the real
environment; confirm with an *independent* check (direct query, log inspection, a regenerated
report), never the application's own success message.

## Start here (one ordered run, written 2026-10-14)

1. **Get the code.** `git fetch origin feat/catalogue-fixes && git checkout feat/catalogue-fixes && git pull`. Never merge before step 12.
2. **Install.** `uv sync --locked --all-packages --all-extras --dev --python 3.12` (Python 3.12 is pinned; 3.13 fails one certificate test). Optional graph tools are pinned in `pyproject.toml` (graphify 0.9.65, graft 0.18.0, codebase-memory-mcp 0.11.0); only needed if you run the commit hooks (`python scripts/install_hooks.py`).
3. **Cloud-proven pieces first.** `uv run pytest -q` (cloud: 751 passed, 68 skipped, 1 xfailed; your machine has 69 skipped, 1 more pass). Then `uv run ruff check . && uv run mypy packages`.
4. **Rebuild the environment** as in "Restore the environment first" below, with a new `artifacts/run-<date>/`.
5. **Turn value overlap on in all three source configs** (same key and ID everywhere, a new key per run) and cap the probe off for now:

   ```toml
   sample_rows = 100
   value_token_key_env = "DBIG_TOKEN_KEY"
   value_token_key_id = "run-<date>"
   # optional, default off (L1): cardinality_probe = true   # ClickHouse and MongoDB only
   ```

   Run once with sampling off (to see "values not compared") and once on.
6. **Model settings for a reasoning model on a shared llama.cpp server** (measured in the report): builder `[builder.review]` `max_tokens = 24000`, `timeout_seconds = 900`, `batch_size = 5` (5 candidates took 14.9 s and 1858 tokens; 20 at once did not finish in 8192 tokens); requester `[requester.answer]` `max_tokens` at least 2048 and `timeout_seconds = 300` (a reply that took 19 s idle took over 60 s when the server was busy). Use the API key the server should have.
7. **Collect, assemble, render, index.** `dbig collect`, `dbig-builder assemble`, `dbig-builder render`, `dbig-requester index` (the index version is 6: an old index answers `STALE_INDEX`). Check the builder run's completeness and `partial_reasons`, `dropped_zero_overlap_candidates`, `dropped_unmeasured_key_name_candidates`.
8. **Relationships (A1, D2, D3, DEC-1/2/3/12, DEC-16).** Compare with `artifacts/candidate-trace.md`: the 16 wrong 0% candidates gone, the 5 right at 100% kept, the 21% to 27% ClickHouse pairs kept with numbers, J1 and J2 return a path, no ordinary shared name (`email`, `status`) links.
9. **Planner (DEC-4).** `dbig-planner build` then `dbig-planner join-paths` for J1 to J4 and X1; J3 offers the direct ClickHouse join first.
10. **Search and `ask` (B1, B3, B4, D6, DEC-5 to DEC-9).** `search --query revenue`; the 20 questions in `artifacts/model-questions.md` with `ask` (target: no Wrong); the credit-card question gives `NO_CATALOGUE_MATCH`; set `exclude_objects` for the Mongo schema-description collections and re-index. Serve the REST API and use `POST /v1/catalogue/search` (L3) with `--no-access-log`.
11. **Optional probe (L1).** Set `cardinality_probe = true` in the ClickHouse and MongoDB files, collect again, check the new `sample` profiles and the query count.
12. **Send back** the report below; merge only after you are satisfied.

### Send-back template (counts and names only, no values, no secrets)

```
Environment: PostgreSQL <ver>, ClickHouse <ver>, MongoDB <ver>; model <name/revision>, server <llama.cpp|vLLM>; pytest: <summary line>
Sampling: off run / on run (sample_rows <n>); probe: off / on
Build: completeness <complete|partial>; partial_reasons <codes>; dropped_zero_overlap <n>; dropped_unmeasured_key_name <n>; seconds <n>
Candidates: <total> (true <n>, false <n>, unsure <n>, missing <n>); the 16 wrong 0% gone: yes/no; the 5 right kept: yes/no; 21-27% pairs kept: yes/no
Wrong blocking_rule values (names only): <list>
Joins: J1 <path|none> J2 <path|none> J3 <first hop same-source?> J4 <path|none> X1 <path|none>; warnings <codes>
Search: revenue <hits>; plurals (industries, tables) <hits>; credit-card -> NO_CATALOGUE_MATCH: yes/no
Ask: answered <n>/20; Wrong <n>; NOT_ESTABLISHED_BY_CATALOGUE <n>; error codes seen <list> (MODEL_TIMEOUT? MODEL_TRUNCATED?); seconds per question min/median/max
Review: batches <n>, completed yes/no, seconds, tokens
Probe (L1): profiles added <n>; STATS_UNAVAILABLE diagnostics <n>; extra queries <n>
REST (L3): POST search ok yes/no; access log silent with --no-access-log yes/no
Tracebacks: <full text, no values> or none
```

## Restore the environment first
Your machine, with your own three databases and your model: follow [`docs/LIVE_TEST.md`](../../LIVE_TEST.md) sections 0 to 9 (install, the read-only accounts, `.env`,
configs, TLS preflight, collect, assemble, render) using the configs in `artifacts/config/` (they hold no secrets; keys come from `.env`). The previous run's evidence is
`artifacts/run-20261006-035124/`; compare against it, do not overwrite it (use a new `artifacts/run-<date>/`). Your marks for the 21 candidates and 42 pairs are in
`artifacts/candidate-trace.md` and `artifacts/live-test-report.md` section 8. Needed for the items that say so:
- **Value sampling** (A1/D2/D9): set `sample_rows` and `value_token_key_env`/`value_token_key_id` identically in all three source configs and a `[builder]` key (see the comments in `artifacts/config/postgresql.toml`); with sampling off the candidates are only labelled "values not compared".
- **The model**: your llama.cpp (or vLLM) server through the SSH tunnel, as before, with the API key it should have (the previous run had none).
- Run the cloud-verified pieces first with `git pull` on `feat/catalogue-fixes`, `uv sync --locked --all-packages --all-extras --dev --python 3.12`, then `pytest -q`.

## The report's "before" values (the user must see these change)

| item | before value (from the report) | where in the report |
|---|---|---|
| B1 | `ask` answered 0 of 30 questions; `400 failed to parse grammar` | live-test-report section 10 |
| B3 / D6a | `search --query revenue` returned nothing; retrieval 18 of 72 expected entries in the top 10 | section 9 #4; model-questions-results Part 2 |
| D6b | `industry` matched 49 entries, `industries` 0; D5, C3, I5 got `NO_CATALOGUE_MATCH` | model-questions-results Part 1 |
| B4 | the credit-card question called the model instead of `NO_CATALOGUE_MATCH` | section 10 (cause of 10) |
| C1 / D7 | every `ask` and review failure printed `MODEL_UNAVAILABLE_OR_INVALID`; two questions timed out at 60 s | section 10, 11 |
| C2 | `render` printed `diagnostics: 0` and the manifest `diagnostics: null` for a `partial` snapshot | section 11 |
| C3 | ClickHouse code 497 for `run_plan.py`; `. ./.env` failed without naming the variable | section 3, 9 |
| D1b | blank Null %, Distinct, Uniqueness, Avg bytes cells with no reason | section 7, statistics-analysis.md |
| D3 | J1 `companyexperience` to `company` and J2 to `person`: `unavailable`; the 21 candidates miss 12 PostgreSQL pairs | section 8, 9 |
| A1 / D2 / D9 | 16 of 21 candidates wrong, all 0% overlap; the 5 right ones 100%; true ClickHouse pairs 21% to 27%, `zoominfo_*` to ClickHouse 25% to 40% | section 8 |
| DEC items | each Decision's check in `artifacts/engineer-fixes.md` (A2: `locationID` names `locationid`; D4: J3 offers the direct ClickHouse join; D5/D8: re-run the 20 questions in `artifacts/model-questions.md`, target no Wrong) | engineer-fixes.md |

## Per-item entries (added by the lead when it accepts an item)

```
### <id> — <title>
- Why cloud could not verify: <no database / no endpoint / ...>
- Command: <exact command>
- Independent check: <query / file / report to compare>
- Expected evidence: <numbers from the report, or "any value > X because ...">
- Result (filled in locally):
```

### C3 (points 1-4) — guide gaps
- Why cloud could not verify: no ClickHouse server or docker daemon in the session.
- Command: `GRANT SELECT ON <db>.* TO <reader>` then `scripts/run_plan.py`; the LIVE_TEST section 3 helpers (`pgq`, `chq`, `mongoq`, `mongoprobe`) against the real servers.
- Independent check: run_plan.py without the grant gives code 497; with it, the plan executes.
- Expected evidence: 497 before the grant, success after; helpers return rows (they were only run as far as a refused connection).
- Result (filled in locally):

(Point 5, the llama.cpp note, waits for B1 and C1.) Ticket: a `DBIG_SIGNING_KEY` under 32 bytes gives a ValueError traceback, not a one-line error (outside C3's files).

### Phase 1 (B1, B3/D6a, D6b, B4, C1/D7, D1b) — live checks
- Why cloud could not verify: no llama.cpp server, no real catalogue.
- B1: `ask "Which column holds a company's website?"` answers with citations on llama.cpp (before: 0 of 30). Also confirm llama.cpp accepts the builder review schema (bounds removed here too).
- B3/D6a: `search --query revenue` returns the camelCase revenue column; rerun `dbig-requester index` first (old indexes give `STALE_INDEX`).
- D6b/B4: plural questions (`industries`) retrieve; the credit-card question gives `NO_CATALOGUE_MATCH`. "tables" alone no longer retrieves in `ask` until DEC-7.
- C1: a busy server shows `MODEL_TIMEOUT`; a down host shows `MODEL_UNREACHABLE`; confirm a real connect timeout is classified `MODEL_UNREACHABLE`.
- D1b: open the page; no blank statistic cell on the real sources.
- Result (filled in locally):

### Phase 2 (D3, A1/D2/D9) — live checks
- Why cloud could not verify: needs the user's databases and, for the drop, sampling on (`sample_rows`, `value_token_key_env`).
- D3: J1 and J2 return a path; the 21 candidates grow by the PostgreSQL pairs (`companyid` style names).
- A1/D2/D9: with sampling on, of the 21 candidates the 16 with 0% overlap disappear and the 5 with 100% stay; the 21% to 27% true pairs stay with their numbers; with sampling off every candidate says "values not compared". Check `dropped_zero_overlap_candidates` in the builder run record equals the disappeared count. Watch for a real FK dropped because its parent ids lie outside the sampled rows.
- Result (filled in locally):

### Phase 2 and Phase 3 — live checks (added 2026-10-13)
All need the real databases and model (see "Restore the environment first"). Rerun `dbig-requester index` first: the index version is now 6.
- D3 / DEC-12: `companyexperience.companyid` and `companyindustry.classificationid` are proposed; J1 and J2 return a path. DEC-1: `locationID` names `location.locationid`, never `location.companyid`.
- A1 / D2 / D9 (needs sampling on): the 16 candidates with 0% overlap disappear, the 5 with 100% stay, the 21% to 27% ClickHouse pairs stay with their numbers; with sampling off every candidate says "values not compared".
- DEC-2: the Z1 to Z11 `zoominfo_*` to ClickHouse pairs are proposed; check the key claim reads "weak key claim" and that ClickHouse sorting keys are single plain columns (expression keys are skipped).
- DEC-3: optional `source_groups` / `forbidden_source_pairs` in `[builder]`; with the PostgreSQL and ClickHouse/Mongo datasets in separate groups the cross-dataset candidates are gone.
- DEC-4: J3 (ClickHouse `Experience` to `Company`) offers the direct ClickHouse join first; a path of only unverified cross-source candidates carries `CROSS_SOURCE_CANDIDATES_UNVERIFIED`.
- DEC-5/6/7/8/9: rerun the 20 questions in `artifacts/model-questions.md` with `ask`; target no Wrong. Check: "ClickHouse tables" lists that source's tables; relationships appear with evidence; an unanswerable question returns `NOT_ESTABLISHED_BY_CATALOGUE` as a normal reply; `cited` shows names; set `exclude_objects` for the Mongo schema-description collections and re-index.
- DEC-10: the review of the 21 candidates completes in batches of 5 with the shipped example config (state `max_tokens`/`timeout_seconds` used).
- E1: a `DBIG_SIGNING_KEY` under 32 bytes gives a one-line error naming the variable.
- C2: `render` `diagnostics` equals the page count and the manifest has `partial_reasons` for a partial snapshot.
- Result (filled in locally):

### Phase 5 L1 to L3 — live checks (added 2026-10-14)
- **L1 (DEC-14), cardinality probe, default off.** In the ClickHouse and MongoDB source files set `cardinality_probe = true` (MongoDB also `sample_rows`), re-run `collect`, `assemble`. Expect: `profiles.jsonl` gains `sample` profiles with `distinct_estimate`/`uniqueness`/`sample_count` and no value; a unique ClickHouse/MongoDB column can now be a measured-unique parent. Check the query count against `max_queries` (one extra per table or collection) and that no `STATS_UNAVAILABLE` "cardinality probe unavailable" diagnostic appears on a healthy server. Not provable in the cloud: the real `uniq`/`max_rows_to_read` behaviour on ClickHouse 26.8 and the `$facet` pipeline on MongoDB 7.0 (CI's live containers run no probe).
- **L2 (DEC-16).** With sampling off, ordinary shared names (`email`, `status`, `url`, `created_at`) must no longer link to a same-named measured-unique column; stem+id names (`classificationid`) still link. With sampling on they link only with measured overlap. The manifest shows `dropped_unmeasured_key_name_candidates` when any were dropped.
- **L3 (DEC-17).** `POST /v1/catalogue/search` with `{"query": "..."}` answers like the `GET`; start `dbig-requester serve ... --no-access-log` and confirm the server prints no request lines.
