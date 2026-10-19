# Lead review of F1-F13 (2026-10-19)

An independent check of [`LEADER_BRIEF.md`](LEADER_BRIEF.md), [`FIX_PROPOSALS.md`](FIX_PROPOSALS.md) and [`../LIVE_VERIFY_REPORT.md`](../LIVE_VERIFY_REPORT.md), on branch `verify/live-20261014` at `526a75c`.

**How it was checked.** Nothing was taken from the earlier agent's scripts or summaries.
- **Code:** every cited line was read.
- **Fixes F1-F4:** each fix was undone in place and its test run, to see it fail.
- **Databases:** with the owner's yes of 2026-10-19, my own read-only queries ran on all three databases as `dbig_reader`. They read whole columns with no cap, used the app's own `exact_text` normalisation, and kept counts and names only.
- **Search:** the app's retrieval was replayed for the 40 questions, with no model call.
- **Sampling rules:** the drop rule and the proposed fixes were replayed in memory, on the real columns, with a fresh random key.
- **Documentation:** a short research pass on llama.cpp, ClickHouse and sketch-based containment.

**Evidence files** (counts and names only):

| File | What it holds |
|---|---|
| `lead-review-inventory.json` | server versions, row counts, ClickHouse keys |
| `lead-review-overlap.json` | whole-column containment and table-wide uniqueness for all 207 candidate pairs seen in the run |
| `lead-review-drop-replay.json`, `lead-review-drop-replay-21.json` | the drop rule replayed under each option |
| `lead-review-rowcap.json` | rows ClickHouse really reads for the probe |
| `lead-review-retrieval.json` | the replay of `ask` retrieval |

**Owner decisions of 2026-10-19, used below:**
1. No limit on model tokens, input or output: no `max_tokens` on `ask` or the review, and no cap on how much catalogue context reaches the model. `retrieval_limit` (10) and `relationship_limit` (20) stay, as the ranking cut.
2. F5 settings: 25,000 sample rows and 4,096 value hashes per column, with `max_sample_bytes` raised to fit.
3. DEC-16 made stricter: an ordinary name links only to a declared key. Measured overlap is no longer enough.
4. The mark `mongo.zoominfo_products.industryID -> mongo.zoominfo_industry.industryID` is removed: the field does not exist (the collection has `industry_id`, which is null in all 1,325 documents).

## Verdicts

| # | Report's claim | Independent check | Verdict | Recommendation set |
|---|---|---|---|---|
| F1 | ClickHouse writes a one-column key as `(companyID)`, so 0 keys were read | `system.tables`: Company, Industry, Location, Person and PersonContact have `(col)`; the multi-column keys have no parentheses. With the fix undone, its test fails | **confirmed**, fix correct | keep `8ed4c87` |
| F2 | `freeMails` linked to a weak key with no overlap | with the fix undone, its test fails. **New:** the two columns really share 35% of their values (1,313 of 3,776), so once ClickHouse has value hashes (F7) the link would come back | **confirmed**; incomplete once F7 lands | keep `444ff9c`; owner decision 3 closes the gap |
| F3 | `max_tokens` cut answers off | with the fix undone, `test_unset_max_tokens_sends_no_limit` fails. llama.cpp documents `n_predict` default `-1` (no limit) | **confirmed** | keep; owner decision 1 |
| F4 | the 2000-character limit rejected answers | with the fix undone, 3 tests fail | **confirmed** | keep |
| F5 | 12 true pairs dropped; the cause is two first-100-row samples | own whole-column count: all 12 at 95-100% (identical numbers); the 11 dropped-and-false at 0. The app's own sampled hashes share **0** values for all 23. Every hash set is `first_*_limit:100`, `truncated: false`, at most 100 distinct. Odds recomputed | **confirmed**. **Correction:** the recommended option B does not work as written (below) | **A + complete reads + range comparison** (below) |
| F6 | 7 of 16 wrong remain; 2 of 5 right dropped | replayed on real data: under the set recommendation, all 16 wrong are dropped and all 5 right kept | **confirmed**; resolves with F5 + F7 | no separate fix |
| F7 | ClickHouse produces no value hashes | 0 token sets in `bundle-ch-on`; no ClickHouse token code in `runtime.py`. Real containment of the true ClickHouse pairs: only **9-27%** (e.g. `Experience.companyID` to `Company` 25%), so the report's 21-27% pairs are real and would stay (a non-zero value never drops) | **confirmed** | **A**, built after F5, with owner decision 3 |
| F8 | the example review config times out | `ReviewConfig.max_tokens` defaults to 512 and is always sent (`reviewer.py:248`); the example sets timeout 60 | **confirmed** | **A** per owner decision 1: `max_tokens` optional, unset sends none, default unset; example timeout 900 (largest measured batch 243 s); LOCAL_VERIFY drops its 24,000 |
| F9 | probe reads up to 2.3× its cap | today it read **7.8×** (778,240 rows, cap 100,000; server `max_threads` auto(16)). The overshoot depends on threads, so the reported 2.3× was one load level | **confirmed, understated** | **new:** read through `SELECT … FROM (SELECT col FROM t LIMIT n)` with `max_threads = 1`. Measured: exactly n rows counted, about 2× read. Correct the comment to "about" |
| F10 | the probe makes false parents and links ordinary names | the 30 ordinary-name parents (`description`, `run_timestamp`, `snippet_text`, …) **are unique across their whole collection**, and so are the child columns that became parents (e.g. `diffbot_CompanyIndustry.companyID`, 22/22): the probe read every MongoDB collection completely. Those 84 candidates were never marked by the owner. **New:** the 3 lost true pairs were not outranked; they were cut on a tie broken by hash id | **partly wrong**: option A would change nothing on MongoDB | **C** per owner decision 3, plus a **new tie-break** (below). Not A |
| F11 | the model joins PostgreSQL and ClickHouse, which "share 0 ids" | all 30 PostgreSQL↔ClickHouse candidate pairs: **0** shared values in whole columns. Bare names confirmed (`search.py:232-233`); column entries do carry `source_id` and `namespace` (`answer.py:185`). Wrong grades match: N-S5, N-C10, N-I1, N-I3 | **confirmed** | **A** |
| F12 | the text says not_established, the field says answered | the schema sent has `required: ["answer", "citations"]`, with `outcome` optional and defaulting to answered. 12 of 40 answers start their text with `outcome:`; the 4 named say not_established with no outcome code | **confirmed** | **A + B's sentence** |
| F13 | search misses needed columns; type questions find nothing | full-text columns are `name, context, description` only. Replay: expected names reached the model **59 of 134 (44%)** at 10 hits, **81 (60%)** at 30, **94 (70%)** with table expansion. Steps 29 → 58 of 66; Comprehensive 10 → 22 of 41 | **confirmed** | **A**, with no cap per table (owner decision 1); **C deferred** (below) |

## Changed or new recommendations, with reasons

### F5: the set recommendation
**Why option B fails as written.**
- `_capped_tokens` keeps the 256 smallest hashes and sets `truncated` when more exist. A parent read completely with 3,940 keys is therefore truncated.
- [`builder.py:693`](../../../packages/catalog-builder/src/dbig_builder/builder.py) never drops against a truncated parent.
- Replayed: at 25,000 rows and 256 hashes, rule A dropped none of the 17 false PostgreSQL/MongoDB pairs.

**What is set, in three parts:**
1. **A (root cause, collector and builder):** a hash set records whether its read was complete, meaning fewer rows came back than `sample_rows`. A zero overlap counts as a measurement, and may drop the pair, only against a complete parent.
2. **Range comparison (builder, a few lines):** when either set is capped, compare only within the hash range both sets cover. The kept hashes are the smallest of a keyed hash, so they form a coordinated bottom-k sketch: inside that range, a value missing from a complete parent really is absent. This keeps the rule exact whenever a parent has more distinct keys than the cap allows (standard bottom-k / KMV containment estimation).
3. **Settings (owner decision 2, no code):** `sample_rows = 25000`, `max_value_tokens = 4096`, `max_sample_bytes = 16777216`.
   - 25,000 rows covers every PostgreSQL table (largest 20,092 rows) and every MongoDB collection (largest 13,518).
   - The first 25,000 documents of all MongoDB collections measured 10,263,100 bytes, so 16 MiB leaves room. The value is derived and can be changed.
   - The "read small parents completely" part of option B then needs no new setting: a `LIMIT` read of a smaller table is complete.

**Replay on the real columns (A + range, 25,000 rows, 4,096 hashes):**
- PostgreSQL/MongoDB: 54 true kept with a number, **0 true dropped**, 17 of 17 false dropped.
- With simulated ClickHouse hashes: 13 false dropped, 0 true dropped. The rest are kept as "values not compared": a ClickHouse parent of 1.27 M rows is never read completely.

**Limits that remain:**
- A ClickHouse parent is never read completely, so no zero against it is ever trusted. Option C (a full ClickHouse column read to build the sketch) stays the upgrade path.
- The extra reads cost about 25,000 rows per table, per collect.

### F10: new tie-break, and C
**The cut is decided by hash order.**
- Every `key_name` candidate scores 100, and ties are sorted by `parent.id` ([`builder.py:678`](../../../packages/catalog-builder/src/dbig_builder/builder.py)), a stable hash.
- Rebuilt locally from the probe bundles with `top_k_per_field = 100`: the kept 5 are exactly the 5 smallest ids. `ch.Company.companyID` (id `861fe226…`) came 6th for both ClickHouse children.

**Set:**
- Break ties by evidence instead: same source first, then declared key, then weak key claim, then measured uniqueness, then id. This is the report's option D, extended.
- Re-check: rebuild the probe snapshot; the 3 lost pairs should return.

**Not A:** whole-table uniqueness changes nothing on MongoDB here. On ClickHouse the only affected parents score ≥ 0.99 table-wide, except `ch.Location.companyID` (97.9%), which feeds one unmarked candidate.

**C:** per owner decision 3. It replaces DEC-16's "measured overlap is enough" and removes `freeMails` and the 30 same-name pairs.

### F13: why C is deferred
- Indexing type text fixes N-C4 only.
- For N-C7 ("not nullable") it would retrieve the 37 *nullable* columns, the opposite of the question.
- The word "text" would match 94 of 170 fields.
- The other 38 questions contain no type word, so A is not affected by this choice.

### F9: why not B as proposed
`LIMIT` alone still read 638,976 to 720,896 rows. `max_threads = 1` is what brings the read down to about 2×. Adding `max_block_size = 8192` brings it to 1.15× (114,688 rows), but that would be one more fixed number, so it is left out.

## Corrections to the report's evidence
- **F9:** the overshoot is load-dependent; it was 7.8× on 2026-10-19, not at most 2.3×.
- **F10:**
  - The "31 false" ordinary-name pairs were the earlier lead's judgement. The owner never marked them.
  - The pairs are real value overlaps (data copied between collections), not wrong measurements.
  - Option A's premise ("unique only within the rows read") does not hold for MongoDB, where every collection was read whole.
- **Mongo-Mongo true pairs:** 9, not 10 (owner decision 4). `mongo.zoominfo_industry.industryID` is not unique (41 distinct in 261 documents), so the app cannot propose the remaining owner-true pair `zoominfo_CompanyIndustry.industryID -> zoominfo_industry.industryID` by design. This is not a defect.
- **F11:** the claim "the two databases share 0 ids" is now measured for all 30 PostgreSQL↔ClickHouse candidate pairs, not only J4.

## Not verified
- Model answers were not re-run (no new model run). Grades are the owner's, recounted from `q40-grades.json`: Right 17, Partial 13, Wrong 6, NOT_ESTABLISHED 2, NO_CATALOGUE_MATCH 2.
- Papers S1-S3, S5, S9 and S11-S12 were not re-read. The recommendations above rest on the measurements, not on them.
- The llama.cpp handling of an optional schema property is shown by this run's answers (the model omitted `outcome` 4 times), not by its documentation, which does not say.

## Order
F5 (A, range comparison, settings) → F7 → F10 (C and tie-break) → F11 A → F12 → F8 → F9 → F13 A.

After that, re-run Gate 2 (with ClickHouse), the probe snapshot and the 40 questions, and compare against this file's numbers.

## Sources
- llama.cpp server README, `n_predict` "Default: `-1`, where `-1` is infinity": https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md
- ClickHouse, restrictions on query complexity (checked after data parts are processed, not per row; `break` returns a partial result): https://clickhouse.com/docs/operations/settings/query-complexity
- Bottom-k and coordinated sampling for set estimation: M. Thorup et al., *Hashing for Sampling-Based Estimation*, https://arxiv.org/pdf/2411.19394. Containment with KMV sketches: *GB-KMV*, https://arxiv.org/pdf/1809.00458
