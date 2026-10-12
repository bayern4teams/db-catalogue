# Local verification checklist — what the cloud could not prove

Everything here is **implemented, unverified**. The user runs each entry locally against the real
environment; confirm with an *independent* check (direct query, log inspection, a regenerated
report), never the application's own success message.

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

(no entries yet)
