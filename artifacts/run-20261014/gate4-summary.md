# Gate 4 (Step 9, L1): cardinality probe (2026-10-18)

User yes to Gate 4. Configs `config/clickhouse-probe.toml`, `config/mongodb-probe.toml` (the `-on` files plus `cardinality_probe = true`). Commands and exit codes in `gate4-commands.txt`. Counts and names only.

## Collect

| Source | Result | Profiles before -> after | Diagnostics | Notes |
|---|---|---|---|---|
| ClickHouse | exit 0, 0.99 s | 42 -> 88 (+46 `sample`, one per column) | 0 | 10 columns uniqueness >= 0.99; fields `distinct_estimate`, `uniqueness`, `sample_count`, no value; value tokens still 0 |
| MongoDB | first try exit 1, login refused; user updated `.env`; retry exit 0, 0.72 s | 863 -> 1158 | 0 | sample_count up to 13,518; 104 columns uniqueness >= 0.99 |

**Finding (open, user: decide at the end):** the row cap is soft on ClickHouse 26.8. 42 of 46 probes read 163,696 to 229,376 rows with `cardinality_probe_max_rows = 100000`; `read_overflow_mode = "break"` finishes the block it is reading. `config.py` says "at most". Counts only, so nothing leaks; the load is up to 2.3 times the cap.

Extra query count: not measured (the bundle records no query count).

## Candidates

| Snapshot | Candidates | true | false | unmarked |
|---|---|---|---|---|
| three sources, probe off (`snapshot-on`) | 82 | | | 0 |
| three sources, probe on (`snapshot-probe`) | 162 (+89, -9) | | | 84 |
| PostgreSQL + ClickHouse, probe off (`snapshot-on-nomongo`) | 59 | 34 | 25 | 0 |
| PostgreSQL + ClickHouse, probe on (`snapshot-probe-nomongo`) | 67 (+8, -0) | 38 | 29 | 0 |

With MongoDB, the probe proposed 5 of the 10 true Mongo-Mongo pairs (0 before), but lost 3 true pairs (ch `Experience`/`CompanyIndustry` -> ch `Company`, Z4) to the `top_k_per_field` 5 cut behind measured-unique cross-source parents, linked 31 ordinary names (`description`, `run_timestamp`, `name`, ...) by `key_name` with measured value containment, and made probe-unique child columns into parents. The user then chose (2026-10-18) to drop MongoDB from this step and the remaining work; those 84 candidates stay unmarked and are not judged. Fix decision for the probe effects: at the end (user).

Without MongoDB the probe added 8 candidates and removed none: 4 ClickHouse person links (user: true) and 4 PostgreSQL -> ClickHouse person links (user: false, the databases share no ids).
