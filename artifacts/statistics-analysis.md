# Why some catalogue fields show statistics and others show nothing

Linked from [`live-test-report.md`](live-test-report.md) (section 7). The page columns are `Null %`, `Distinct (est.)`, `Uniqueness`, `Avg bytes` and `Statistics from` ([`packages/catalog-builder/src/dbig_builder/render.py`](../packages/catalog-builder/src/dbig_builder/render.py#L303-L315) lines 303-315 and 387-388). The values come from `profiles.jsonl` in each bundle (`artifacts/run-20261006-035124/bundle-*/profiles.jsonl`). Each database exposes different information, and the collector does not invent what a source does not give. Nothing here is a bug in your data.

## What each source provides

| Metric on the page | PostgreSQL (124 fields) | ClickHouse (46 fields) | MongoDB (865 fields) |
|---|---|---|---|
| Statistics from (`method`) | `source_statistic` (planner statistics, `pg_stats`) | `source_statistic` (column byte counts in `system.columns`) | `sample` (field presence in at most 100 documents per collection, `sample_rows = 100`) |
| Null % (`null_fraction`) | 124 of 124 | 0 of 46 | 863 of 865 |
| Distinct (est.) (`distinct_estimate`) | 119 of 124 | 0 of 46 | 0 of 865 |
| Uniqueness (`uniqueness`) | 119 of 124 | 0 of 46 | 0 of 865 |
| Avg bytes (`average_width_bytes`) | 124 of 124 | 42 of 46 | 0 of 865 |
| Sample count (not shown on the page) | none | none | 863 of 865 |

## Reasons, per source

**PostgreSQL.** `pg_stats` holds, per column, `null_frac`, `avg_width` and `n_distinct`. They are estimates from the last `ANALYZE` sample. `n_distinct` is a count when positive and a fraction of the row count when negative ([`runtime.py`](../packages/collectors/src/dbig_collectors/runtime.py#L318-L334) lines 318-334 handles the sign); `uniqueness` is that count divided by the rows. Five fields show empty Distinct and Uniqueness although they have a null fraction: `company.explicit_origins`, `company.foundername`, `company.totalinvestmentcurrency`, `companyinvestmentround.confidence` and `companyrelation.explicit_origin`. Each is 100% null (`null_fraction = 1.0`), and PostgreSQL reports `n_distinct = 0` for a column with no non-null values, which the collector treats as "no estimate". Database behaviour, handled correctly by the app.

**ClickHouse.** ClickHouse keeps no per-column null counts or distinct counts by default (the collector's own comment at [`runtime.py`](../packages/collectors/src/dbig_collectors/runtime.py#L610-L612) lines 610-612 says "average width is honestly all this yields"). The only number available is `data_uncompressed_bytes` per column, divided by the table's `total_rows` to get an average width. So Null %, Distinct and Uniqueness are empty for all 46 fields. This is a limit of the source, but it matters: the builder decides which column can be a parent from the uniqueness (see below), so no ClickHouse column can ever qualify. Four fields have no number at all, all in `Industry` (`industryID`, `name`, `displayName`, `_version`): `system.columns` returns `data_uncompressed_bytes = 0` for each of them (the table has 37 rows). I did not establish why ClickHouse reports 0 for this table.

**MongoDB.** MongoDB has no catalog statistics. The collector samples up to 100 documents per collection (`sample_rows = 100` in the example `mongodb.toml`) and keeps only field paths, types, the fraction of sampled documents where the field is null or absent (`Null %`) and the sample size. It does not compute distinct counts or sizes, so those columns are empty. Because the numbers come from at most 100 documents, a collection with 13,518 documents (`zoominfo_Experience`, `zoominfo_staff`) is described by under 1% of its documents. Two fields have no profile: `_id` of `diffbot_metadata` and `zoominfo_metadata` (type unknown, likely empty collections).

**Object attributes.** ClickHouse tables carry `row_count_estimate`; the sorting and primary keys are stored only as `primary_key_sha256` and `sort_key_sha256`, i.e. hashes, so the page and the builder cannot tell which columns they cover. MongoDB reports one `index` constraint per collection (28), the default `_id` index only. PostgreSQL has no constraints in `public` (`constraints.jsonl` is empty), so no primary key, unique key or foreign key can be shown.

## Why it matters

Parent-key detection in [`builder.py`](../packages/catalog-builder/src/dbig_builder/builder.py) (lines 486-502) needs a declared unique key or `uniqueness >= 0.99`. Only PostgreSQL provides the second, and PostgreSQL has no declared keys. This is the direct cause of "all 21 candidates point at PostgreSQL" ([`candidate-trace.md`](candidate-trace.md)).

## Proposals (also in [`engineer-fixes.md`](engineer-fixes.md), item D1)

- Keep the ClickHouse `primary_key` and `sorting_key` column names (schema metadata, not values), not only their hashes, and let the builder treat them as a weak, labelled key claim.
- For ClickHouse and MongoDB, optionally allow a bounded cardinality probe (for example `uniq` on a sampled range), under the existing read budgets and denylist, clearly labelled as measured.
- Show on the page why a value is empty ("not provided by this source", "all values null", "not computed") instead of a blank.
