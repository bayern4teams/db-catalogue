# Fix proposals for the open items of the live verification (F5-F13)

Companion to [`LEADER_BRIEF.md`](LEADER_BRIEF.md). Full results: [`../LIVE_VERIFY_REPORT.md`](../LIVE_VERIFY_REPORT.md). Every claim cites a code line on branch `verify/live-20261014`, a number measured in this run (file under `artifacts/run-20261014/`), or an external source (listed at the end, [S1]-[S12]). Nothing here has been implemented; each item needs the owner's decision.

How to read each item:
- **Evidence:** what the run measured.
- **Root cause:** the code behind it.
- **Options:** each with its advantages (+) and disadvantages (−).
- **Recommendation:** the option I prefer, and why I prefer it over the others.

Priority order (my view, by damage to the catalogue's main promise, "relationships you can trust"):
- **High:** F5, F11, F7.
- **Medium:** F10, F13, F8.
- **Low:** F12, F9.
- **No separate fix:** F6 follows from F5 and F7.

---

## F5. Sampling on drops true relationships (0-of-N rule)

**Evidence:**
- 12 relationships the owner marked true were dropped as "zero overlap" ([`gate2-summary.md`](../gate2-summary.md)).
- An independent in-memory count (child up to 5000 distinct values, the whole parent column) found 95-100% containment for all 12 ([`gate2-overlap.json`](../gate2-overlap.json)).

**Root cause:**
- Both sides are a sample of the first 100 rows ([`runtime.py:211`](../../../packages/collectors/src/dbig_collectors/runtime.py) `LIMIT %s` with `sample_rows`; method `first_rows_limit:100`).
- The drop rule ([`builder.py:688-698`](../../../packages/catalog-builder/src/dbig_builder/builder.py)) drops when 0 tokens are shared, unless `parent_truncated` is set.
- `parent_truncated` means only that the token cap was hit, not that the parent was read partly. So a 100-row slice of a 3,940-key parent is treated as the complete parent.

**Why this cannot be tuned away:**
- Model: true reference, child values spread over the parent's keys, parent sample holds 100 of 3,940 keys.
- The chance that k child values share **zero** values with that sample is C(3840,k)/C(3940,k):

| k child values | 5 | 10 | 17 | 29 | 50 | 100 |
|---|---|---|---|---|---|---|
| chance of 0 shared | 88% | 77% | 65% | 47% | 27% | 7% |

- Below 1% needs k = 176 distinct child values, more than the whole 100-row sample. So no threshold on the current samples makes the rule safe.
- The literature measures inclusion on complete columns or on sketches built from complete columns, never on two independent row prefixes [S1][S2][S3].

**Options:**
- **A. Drop only when the parent was read completely.** The collector marks a token set `complete` when it read every row: the row count is below `sample_rows`, or it compares with `pg_class.reltuples` / `system.tables.total_rows` / `estimatedDocumentCount()`. The builder drops on zero only against a complete parent; otherwise the evidence says "parent sampled, a zero is not a measurement".
  - (+) Small change, keeps the privacy design (keyed digests only), and removes this whole class of false drops.
  - (−) With partial parents, the false candidates the drop used to remove stay too: the 11 dropped-and-false ones would come back, still marked "values not compared".
- **B. Read small parents completely.** A parent with ≤ M rows (configurable, for example 100,000) is tokenised in full (`SELECT DISTINCT key`); larger ones fall back to A.
  - (+) In this run every parent of a dropped true pair is small (PostgreSQL, 470 to 3,940 distinct keys), so drops become exact measurements. Both errors are fixed: true pairs are kept and false ones still dropped (the independent count showed 0 overlap for all 11 false ones).
  - (+) Matches the guidance to bound reads, because only small tables are read in full.
  - (−) One more read per small parent; it needs a configurable limit and a budget charge.
- **C. Coordinated (hash-based) sampling.** Both sides keep only values whose keyed hash falls in the same range, so a true child value in range always meets its parent value [S6][S2].
  - (+) Unbiased containment estimates at any table size; this is the standard technique (bottom-k / KMV sketches, ClickHouse `SAMPLE BY` is deterministic and consistent across tables [S6]).
  - (−) Each side must scan the whole column to pick the in-range values. That costs a full column read on PostgreSQL and MongoDB, which the design (§20, [`runtime.py:185-188`](../../../packages/collectors/src/dbig_collectors/runtime.py)) avoids on production sources. It is also a bigger change.
- **D. Random sample (`TABLESAMPLE BERNOULLI`, `$sample`).**
  - (−) It does not fix the problem: two independent random samples still miss each other with the same odds as the table above [S7][S8]. Not recommended.

**Recommendation: B, then A as its fallback.**
- **Why over A alone:** B also keeps the useful drops of false pairs, which A gives up.
- **Why over C:** B is a small, bounded change that covers this deployment's parents. Keep C as the upgrade path if large parents matter (a `ponytail:` note with that ceiling).
- **Check:** rerun Gate 2. The 12 true pairs should be kept and the 11 false ones still dropped, against the same independent counts.

---

## F6. Known-wrong candidates remain and known-right ones drop

**Evidence:**
- 7 of the 16 known-wrong remain: 6 have ClickHouse children, and #1 had too small a sample.
- 2 of the 5 known-right were dropped (#7, #12).

**Root cause:** F5 for the drops and F7 for the ClickHouse children. #1 is honest: `sample_too_small` is reported, not hidden.

**Recommendation:** no separate fix; it resolves with F5 and F7. Re-measure after them.

---

## F7. ClickHouse produces no value tokens

**Evidence:**
- PostgreSQL produced 106 token sets, MongoDB 450, ClickHouse 0 ([`gate2-summary.md`](../gate2-summary.md)).
- So every ClickHouse pair shows "values not compared", the 21-27% pairs show no number, and the probe did not change that ([`gate4-summary.md`](../gate4-summary.md)).

**Root cause:** the collector has `_tokenize_postgresql` ([`runtime.py:170`](../../../packages/collectors/src/dbig_collectors/runtime.py)) and a MongoDB token path ([`runtime.py:899`](../../../packages/collectors/src/dbig_collectors/runtime.py)), but no ClickHouse one. `docs/LOCAL_RUN.md` says so; `LOCAL_VERIFY.md` step 5 assumed all three.

**Options:**
- **A. Add `_tokenize_clickhouse`** using the same `_value_tokens` / `_capped_tokens` helpers and the same key and normalisation, with a bounded read (`LIMIT`, `max_rows_to_read`).
  - (+) Reuses tested code; ClickHouse pairs get numbers and drops.
  - (−) One more read per table. It needs the F5 decision first, or it inherits the same false drops.
- **B. Keep the gap and document it in LOCAL_VERIFY and the page.**
  - (+) No work.
  - (−) ClickHouse is 1 of 3 sources, and its relationships stay unmeasured.

**Recommendation: A, after F5 B.**
- **Why:** without tokens, the most useful same-source ClickHouse pairs can only ever be "values not compared".
- **Why after F5:** otherwise ClickHouse true pairs would start being dropped exactly as PostgreSQL ones were.

---

## F8. The example review config cannot finish

**Evidence:**
- **Example settings** (timeout 60 s, `max_tokens` default 512): `MODEL_TIMEOUT` on the first batch, 0 suggestions.
- **LOCAL_VERIFY settings:** completed. The four batches used 2,508 / 6,966 / 10,322 / 8,755 completion tokens and took 29 / 84 / 99 / 243 s ([`review-lv-calls.json`](../review-lv-calls.json)).

**Root cause:**
- `ReviewConfig.max_tokens` defaults to 512 ([`config.py:18`](../../../packages/catalog-builder/src/dbig_builder/config.py)), and the example sets `timeout_seconds = 60` ([`examples/local/builder-with-vllm.toml`](../../../examples/local/builder-with-vllm.toml)).
- A reasoning model spends most of its budget thinking. The requester had the same defect (F3), fixed by making the limit optional.

**Options:**
- **A. Same as F3:** `max_tokens` optional, unset sends none; example timeout 900.
  - (+) Consistent with the owner's F3 decision; uses the server's own maximum.
  - (−) One review call can run long on a shared server; the timeout is the only bound.
- **B. Keep the limit; change the example to the measured values** (24,000 tokens, 900 s, batch 5).
  - (+) A bounded call.
  - (−) The numbers are model-specific and will be wrong for the next model.

**Recommendation: A.**
- **Why:** the owner chose A for the requester for the same reason. Two different rules for the same server would confuse operators.
- **Note:** vLLM's chat endpoint allows `max_tokens` to be omitted [S10]; the requester already relies on that (live: 20 answers with no limit).

---

## F9. The ClickHouse probe's row cap is soft

**Evidence:** 42 of 46 probes read 163,696 to 229,376 rows against a cap of 100,000 ([`gate4-summary.md`](../gate4-summary.md)).

**Root cause:**
- [`runtime.py:783-784`](../../../packages/collectors/src/dbig_collectors/runtime.py) uses `max_rows_to_read` with `read_overflow_mode = "break"`.
- ClickHouse checks the limit per block, not per row, so it "can be broken a little" and stops only at a block boundary [S4].
- The config comment ([`config.py:41-42`](../../../packages/collectors/src/dbig_collectors/config.py)) says "at most".

**Options:**
- **A. Fix the comment and docs** to say "about": up to roughly one block per thread more.
  - (+) Honest, no behaviour change.
  - (−) The load stays up to 2.3 times the cap.
- **B. Make the cap tighter:** read through `SELECT ... FROM (SELECT col FROM t LIMIT n)` and also set `max_block_size` to n.
  - (+) The read stops close to n.
  - (−) Still block-granular; needs a live check on 26.8.

**Recommendation: A now, B only if the extra load matters.**
- **Why:** only counts are kept, so nothing leaks. The cost is a bounded overshoot, and an honest comment is the cheapest correct fix.

---

## F10. With MongoDB, the probe creates false parents and pushes out true ones

**Evidence** (probe on, three sources; [`gate4-summary.md`](../gate4-summary.md)):
- Candidates went from 82 to 162.
- 5 of 10 true Mongo-Mongo pairs were found (gain).
- 3 true pairs were lost at the `top_k_per_field = 5` cut.
- 31 ordinary-name pairs (`description`, `run_timestamp`, `name`) were linked with real value overlap.
- Child columns (for example `diffbot_CompanyIndustry.companyID`) became parents because they look unique within the rows read.
- Without MongoDB the probe was clean: +8 candidates, 0 lost.

**Root cause:**
- **Uniqueness measured over the rows read is treated as key evidence.** `distinct / rows` over the first rows read ([`runtime.py:733-748`](../../../packages/collectors/src/dbig_collectors/runtime.py) admits this is a lower bound on duplicates) feeds `declared_unique`, which exempts the parent from the name rules ([`builder.py:575, 625, 641`](../../../packages/catalog-builder/src/dbig_builder/builder.py)).
- **Overlap alone counts as proof for ordinary names.** DEC-16 lets an ordinary name link once overlap is measured, and shared timestamps or descriptions do overlap.

**Options:**
- **A. Parent must be unique table-wide.** Accept probe uniqueness as a key only when the probe read the whole table (`sample_count` ≥ row count), the same rule as F5 A.
  - (+) Removes the false parents.
  - (−) Large Mongo collections never qualify.
- **B. Penalise a column that is itself a child elsewhere** (Rostin et al.'s "DependentAndReferenced" feature [S5]): if X → Y is a candidate, X ranks low as a parent of others.
  - (+) Matches the pattern seen here (a child-table `companyID` used as a parent).
  - (−) Needs a second pass and a ranking change.
- **C. Ordinary names need more than overlap:** link them only with a declared key, or with `stem+id`-style names on both sides.
  - (+) Removes the 31 pairs.
  - (−) A real reference with an ordinary name (rare) is missed.
- **D. Rank a same-source parent before a cross-source one** at the `top_k` cut.
  - (+) Restores the 3 lost true pairs. Same idea as the planner's DEC-4.
  - (−) A real cross-source key can be cut if a same-source look-alike exists.

**Recommendation: A + C + D together.**
- **Why:** each is a small rule change that targets one measured failure.
- **Why not B first:** B is the most general, but also the largest change. Keep it as the next step if A + C + D leave false parents.

---

## F11. The model cannot tell the two databases apart

**Evidence:**
- **Questions that joined across databases:** N-S5 joined ClickHouse `Company` to PostgreSQL `location` to ClickHouse `Location` in one query. N-C8, N-C10, N-I1 and N-I3, and I1 of the first 20, offered PostgreSQL ↔ ClickHouse joins.
- **The owner marked them Wrong**, except N-C8 (Partial) ([`q40-grades.json`](../q40-grades.json)).
- **The data:** the two databases share 0 ids ([`gate3-handcount.json`](../gate3-handcount.json)).

**Root cause:**
- The relationship entries given to the model carry only bare table names: `"from": names.get(edge.from_id), "to": ...` ([`search.py:232-233`](../../../packages/requester/src/dbig_requester/search.py)), with no source, namespace or engine. So `location` and `Location` differ only by letter case.
- The system prompt ([`answer.py:216`](../../../packages/requester/src/dbig_requester/answer.py)) does not say that a candidate between two sources cannot be joined in one query.
- The column entries do carry `source_id`; only the relationships lack it.

**Options:**
- **A. Add `from_source`, `from_namespace`, `to_source`, `to_namespace` to each relationship entry** (an index change, so bump `INDEX_VERSION`), plus one prompt sentence: candidates between different sources cannot be joined in one query.
  - (+) Small, and gives the model the facts it lacks. Schema-linking work shows that retrieval units carrying their database context reduce cross-database mistakes [S11][S12].
  - (−) The prompt rule is still only advice to the model.
- **B. Also filter cross-source tentative edges with no measured overlap out of the `ask` context.**
  - (+) The model cannot offer what it never sees.
  - (−) Hides a real cross-source candidate from questions about it.
- **C. Post-check the answer:** reject an answer whose cited tables come from two sources joined in one SQL snippet.
  - (−) Fragile text parsing; the codebase already avoids checking content this way (see the `LINEAGE_CLAIM` ponytail note, [`answer.py`](../../../packages/requester/src/dbig_requester/answer.py)).

**Recommendation: A, then measure; add B only if Wrong answers remain.**
- **Why:** A fixes the missing information at its root, at the smallest cost.
- **Why not B yet:** B loses information that a user asking about cross-source candidates needs.
- **Check:** rerun the 40 questions. N-S5, N-C8, N-C10, N-I1 and N-I3 should stop offering cross-database joins.

---

## F12. The answer text says "not established" while the outcome says answered

**Evidence:** N-S8, N-C2, N-I9 and N-I10: the text begins "outcome: not_established", while the structured `outcome` is `answered` ([`q40-answers.json`](../q40-answers.json)).

**Root cause:**
- The structured field `outcome` defaults to `"answered"` ([`answer.py`](../../../packages/requester/src/dbig_requester/answer.py), class `Answer`).
- When the model writes its verdict in the text instead of the field, the default wins.

**Options:**
- **A. Make `outcome` required** (no default), so the schema forces the model to choose.
  - (+) One-line change, enforced by the JSON schema.
  - (−) A model that omits it gets `MODEL_UNAVAILABLE_OR_INVALID` instead of an answer.
- **B. Say in the prompt to put the verdict only in the field.**
  - (+) No schema change.
  - (−) Advice only.

**Recommendation: A (with B's sentence too).**
- **Why:** the schema is what the server enforces, and a strict schema is already sent ([`answer.py`](../../../packages/requester/src/dbig_requester/answer.py) `strict: True`).
- **Check:** a stub test where the model omits `outcome`.

---

## F13. Search misses needed columns, and type questions find nothing

**Evidence:**
- **Questions that missed a key column:** S1, S2, S4, S6, S7 and S9 (new) each said "not established" because search did not return a key column (company name, `technologyname`), although each cited the right joins.
- **Type questions found nothing:** N-C4 (timestamp columns) and N-C7 (non-nullable columns) returned `NO_CATALOGUE_MATCH`.
- **Measured split:** of the 40, Direct got 10/10 and Comprehensive 0/10 Right ([`q40-grades.json`](../q40-grades.json)).

**Root cause:**
- The model sees the top `retrieval_limit = 10` search hits ([`config.py:27`](../../../packages/requester/src/dbig_requester/config.py)).
- The full-text index covers `name, context, description` only ([`search.py:174`](../../../packages/requester/src/dbig_requester/search.py)). `data_type` is a filter, not searchable text.

**Options:**
- **A. Expand retrieved tables to their columns:** when a table is hit, pass its column list (names and types).
  - (+) Recent schema-linking methods report high recall from this expansion [S11][S12], and it fixes the "no company name field" refusals directly.
  - (−) A longer prompt; needs a cap per table.
- **B. Raise `retrieval_limit`** (a config value, up to 100).
  - (+) No code.
  - (−) More noise, and it does not help type questions.
- **C. Index `data_type` and nullability as searchable text.**
  - (+) Fixes N-C4 and N-C7.
  - (−) Words like "string" match very widely.

**Recommendation: A + C.**
- **Why over B:** B only widens the same ranking. A gives the model the missing columns that caused 6 refusals, and C fixes the two zero-hit questions.
- **Check:** rerun the 40 questions; compare the Comprehensive and Steps grades.

---

## Sources
- [S1] M. Zhang, M. Hadjieleftheriou, B. C. Ooi, C. M. Procopiuc, D. Srivastava. *On Multi-Column Foreign Key Discovery*. PVLDB 3(1), 2010 (bottom-k sketches for inclusion). https://pure.bit.edu.cn/en/publications/on-multi-column-foreign-key-discovery/
- [S2] A. Nazi, B. Ding, V. Narasayya, S. Chaudhuri. *Efficient Estimation of Inclusion Coefficient using HyperLogLog Sketches*. PVLDB 2018 (HLL estimator more accurate than bottom-k for FK discovery). https://rmarcus.info/dbscholar/papers/h2572d1f6ccba1780
- [S3] Z. Chen, V. Narasayya, S. Chaudhuri. *Fast Foreign-Key Detection in Microsoft SQL Server PowerPivot for Excel*. PVLDB 7(13), 2014. https://www.microsoft.com/en-us/research/publication/fast-foreign-key-detection-in-microsoft-sql-server-powerpivot-for-excel/
- [S4] ClickHouse documentation, *Restrictions on query complexity* (limits checked per block; `read_overflow_mode`). https://clickhouse.com/docs/operations/settings/query-complexity
- [S5] A. Rostin, O. Albrecht, J. Bauckmann, F. Naumann, U. Leser. *A Machine Learning Approach to Foreign Key Discovery*. WebDB 2009 (features incl. DependentAndReferenced, MultiReferenced, name similarity). https://hpi.de/oldsite/fileadmin/user_upload/fachgebiete/naumann/publications/2009/WebDB09_crc.pdf
- [S6] ClickHouse documentation, *SAMPLE clause* (deterministic, consistent across tables with the same sampling key). https://clickhouse.com/docs/sql-reference/statements/select/sample
- [S7] PostgreSQL wiki, *TABLESAMPLE Implementation* (SYSTEM page-level vs BERNOULLI row-level). https://wiki.postgresql.org/wiki/TABLESAMPLE_Implementation
- [S8] MongoDB manual, *$sample* (pseudo-random cursor conditions). https://mongodb.com/docs/v7.3/reference/operator/aggregation/sample
- [S9] E. Zhu, F. Nargesian, K. Q. Pu, R. J. Miller. *LSH Ensemble: Internet-Scale Domain Search*. PVLDB 2016 (containment, not Jaccard, for joinability). https://arxiv.org/pdf/1603.07410
- [S10] vLLM max_tokens behaviour on the chat endpoint (may be omitted). https://fast.io/resources/vllm-max-tokens/ (secondary source; confirmed live in this run: 20 answers with no `max_tokens` sent)
- [S11] *LinkAlign: Scalable Schema Linking for Real-World Large-Scale Multi-Database Text-to-SQL*, 2025. https://arxiv.org/html/2503.18596v3
- [S12] *AutoLink: Autonomous Schema Exploration and Expansion for Scalable Schema Linking*, AAAI 2026. https://ojs.aaai.org/index.php/AAAI/article/view/40672 ; and *RASL: Retrieval Augmented Schema Linking for Massive Database Text-to-SQL* (Amazon Science). https://assets.amazon.science/1b/95/8f62e89647348f4c4836f6c3040d/rasl-retrieval-augmented-schema-linking-for-massive-database-text-to-sql.pdf

The F5 probability table is my own calculation (hypergeometric, child values spread over the parent's keys). The run's evidence matches it: the 12 dropped true pairs had 5 to 29 child values against parents of 470 to 3,940 keys ([`gate2-overlap.json`](../gate2-overlap.json)).
