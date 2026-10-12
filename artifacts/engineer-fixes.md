# Proposed fixes for the engineer (from the live test, nothing here is applied)

Source of every item: [`live-test-report.md`](live-test-report.md) and the run in `artifacts/run-20261006-035124/`. Branch `claude/gracious-bardeen-x1y6he`, commit `2e87fa7`. Test setup: PostgreSQL 16.15, ClickHouse 26.8.2.7, MongoDB 7.0.41; the model server was llama.cpp (its replies carry `system_fingerprint: b1-d1a9235` and a `timings` block) serving `Qwen3.8-Flash-Next`, a reasoning model. The guide assumes vLLM; nothing here was tested against vLLM.

Priority: A = wrong or misleading results, B = a feature does not work in this setup, C = usability and documentation. Every item ends with a check that would fail before the fix and pass after it.

## A. Wrong or misleading output

### A1. A relationship is proposed without proof that its values link (affects 16 of 21 candidates)
- Symptom: with sampling off, a candidate rests on name, type and the parent's uniqueness. Measured value overlap (counts only, first 5000 distinct child values, child values found in the parent column): 5 candidates are 100% (all MongoDB `diffbot_*` to PostgreSQL), 16 are 0%. Examples: ClickHouse `Company.companyID` to PostgreSQL `company.companyid` 0 of 5000 (PostgreSQL ids are 23 characters, ClickHouse ids 7 to 9); `zoominfo_*` MongoDB to PostgreSQL 0%. A plan built on such a candidate joins zero rows (`run_plan`: `joined_rows` 0, `left_rows_without_a_match` 1000), and the planner's only warning is "candidate relationship requires human review".
- Where: candidate creation [`packages/catalog-builder/src/dbig_builder/builder.py`](../packages/catalog-builder/src/dbig_builder/builder.py#L503-L600) lines 503-600 (evidence is only `name_similarity`, `compatible_type`, `measured_uniqueness`); containment is computed only when tokens exist (`_containment`, line 244, needs `sample_rows`).
- Proposal: (1) state on every candidate, in the evidence and on the page, what was *not* checked ("value overlap not measured"); (2) make a measured zero overlap an `opposes`-type evidence that lowers the status, not silence; (3) have the planner warn when a plan's keys are known to have different shapes or zero measured overlap.
- Check: a candidate between two columns with disjoint value sets must show "overlap 0" or "not measured" on the page, and `dbig-planner plan` must carry a warning that names it.

### A2. The parent column can be the wrong column of the right table
- Symptom: candidates 9 and 20 (`Location.locationID`, `zoominfo_location.locationID`) point at PostgreSQL `location.companyid`, not `location.locationid`. Cause: `location.locationid` measures 0.9826 unique (below `MEASURED_UNIQUE = 0.99`, [`builder.py`](../packages/catalog-builder/src/dbig_builder/builder.py#L86) line 86) while `location.companyid` measures 1.0, and name matching is by table name only.
- Proposal: when several unique columns of the parent table qualify, or when the best one is under the threshold, prefer the parent column whose name matches the child column's name; do not accept a column whose name does not resemble the child's.
- Check: with those two tables the candidate must name `locationid`, or no candidate must be proposed.

### A3. Related-entity columns whose targets are not in the database (user decision)
- Symptom: PostgreSQL `companyrelation.relatedcompanyid` and `companyinvestor.investorcompanyid` look like references to `company.companyid`; measured overlap is 0 of 5000 and 0 of 794. The owner confirmed both are wrong because their parent rows do not exist in the database. Neither was proposed by the builder here, but any future candidate of this kind would be.
- Proposal: same as A1. A claim must carry its proof or say it has none.

## B. Features that do not work in this setup

### B1. `dbig-requester ask` fails for every question on a llama.cpp server
- Symptom: `{"error": {"code": "MODEL_UNAVAILABLE_OR_INVALID"}}` for all ten questions; the server returns `400 Failed to initialize samplers: failed to parse grammar`.
- Cause (tested with dummy data): the `Answer` schema has `maxLength: 2000` ([`packages/requester/src/dbig_requester/answer.py`](../packages/requester/src/dbig_requester/answer.py#L22) line 22, sent at lines 118-126). Without `maxLength`, `minLength`, `maxItems`, `minItems` the same server accepts it. The builder's schema ([`reviewer.py`](../packages/catalog-builder/src/dbig_builder/reviewer.py#L27) line 27, `max_length=256`) was accepted, so the size of the bound may matter; I did not test other sizes.
- Proposal: drop the length bound from the schema sent to the server and enforce 2000 characters in the existing Pydantic validation after parsing (the response is already validated by `Answer.model_validate_json`). Optionally make the schema style configurable.
- Check: against a llama.cpp server, `ask` returns a cited answer for "Which column holds a company's website?".

### B2. Reasoning models exhaust `max_tokens` before answering
- Symptom: with the default `max_tokens = 512` ([`packages/catalog-builder/src/dbig_builder/config.py`](../packages/catalog-builder/src/dbig_builder/config.py#L18) line 18, [`packages/requester/src/dbig_requester/config.py`](../packages/requester/src/dbig_requester/config.py#L21) line 21) the model used all 512 tokens reasoning: `finish_reason: length`, empty content, review fails. With 20 candidates and 8192 tokens it still ran out (72 s). With 5 candidates and 24000 tokens it completed in 14.9 s using 1858 tokens.
- Proposal: document it; recommend smaller batches; consider sending the server's switch that turns thinking off if one is supported (not verified for this model); consider reviewing candidates in batches instead of one call of 20.
- Check: the review of the 21 candidates completes with the shipped example config on the same model.

### B3. Search cannot find camelCase column names by a part of the name
- Symptom: `revenue` returns nothing though `Company.companyRevenue` exists. Search is SQLite FTS5 prefix matching on whole words ([`packages/requester/src/dbig_requester/search.py`](../packages/requester/src/dbig_requester/search.py#L68) line 68 creates `fts`; line 166 builds the terms). `companyRevenue` is one word; underscored names are split, camelCase names are not.
- Proposal: split camelCase and PascalCase at index time, as `_snake` already does for inference ([`builder.py`](../packages/catalog-builder/src/dbig_builder/builder.py#L108) line 108), and index the split words.
- Check: `search --query revenue` returns `Company.companyRevenue`.

### B4. The "nothing matches" guard is defeated by schema words
- Symptom: the question "Which column holds a customer's credit card number?" returned catalogue entries, because `column` and `number` match MongoDB fields literally named `column` (`provenance.column`, `evidence_columns`). It should have returned `NO_CATALOGUE_MATCH` and not called the model. `column` is not in `STOPWORDS` ([`search.py`](../packages/requester/src/dbig_requester/search.py#L23) line 23).
- Proposal: add schema words (`column`, `table`, `field`, `number`, `holds`, `store`, `stored`) to the stopwords, or require a minimum fraction of the meaningful words to match.
- Check: that question returns `NO_CATALOGUE_MATCH`.

### B5. Relationship inference misses most references
- Symptom: 21 candidates against 31 further pairs found from names, types and statistics. The owner confirmed 29 of them as true (12 PostgreSQL, 6 ClickHouse, 10 MongoDB, and the cross-source M26) and rejected 2 (`investorcompanyid`, `relatedcompanyid`, see A3). A further 11 pairs from the MongoDB `zoominfo_*` collections to ClickHouse were also confirmed true, so 40 confirmed relationships are missing in all. Of the PostgreSQL pairs, the twelve true ones measure 99.7% to 100% overlap. Causes:
  1. ClickHouse and MongoDB tables can never be parents: uniqueness is measured only from PostgreSQL planner statistics ([`packages/collectors/src/dbig_collectors/runtime.py`](../packages/collectors/src/dbig_collectors/runtime.py) around line 278); ClickHouse's declared sorting keys (`Company(companyID)`, `Industry(industryID)`, `Location(locationID)`, `Person(personID)`) are not used as keys;
  2. names without a separator before the suffix (`companyid`, `classificationid`) are not recognised as references ([`builder.py`](../packages/catalog-builder/src/dbig_builder/builder.py#L119-L132) lines 119-132 and 534-546), so the whole PostgreSQL schema is missed;
  3. see A2.
- Proposal: treat ClickHouse `primary_key`/`sorting_key` as a (weak, labelled) key claim; recognise a suffix at the end of a lowercase name when the stem is a known object name (`company` + `id`); make the 0.99 threshold and the key claim visible in the evidence.
- Check: `companyindustry.classificationid` to `industryclassification.classificationid` is proposed.

## C. Usability and documentation

### C1. One error code hides the real cause
- Every failure in `ask` and in the review returns `MODEL_UNAVAILABLE_OR_INVALID`, whether the server is unreachable, rejects the request, cuts the answer off, or cites an unknown ID ([`answer.py`](../packages/requester/src/dbig_requester/answer.py#L135) line 135, [`reviewer.py`](../packages/catalog-builder/src/dbig_builder/reviewer.py#L125) lines 125 and 229). The operator cannot tell them apart. Proposal: distinct codes without logging content (`MODEL_UNREACHABLE`, `MODEL_REJECTED_REQUEST`, `MODEL_TRUNCATED`, `MODEL_INVALID_CITATION`).

### C2. `partial` is not explained in the machine-readable places
- `dbig-builder render` printed `"diagnostics": 0` and the manifest shows `"diagnostics": null` for a snapshot whose `completeness` is `partial`; the page does list `MODEL_REVIEW_FAILED`. Proposal: make the CLI summary and the manifest carry the same diagnostics as the page.

### C3. Guide gaps
- [`docs/LOCAL_RUN.md`](../docs/LOCAL_RUN.md) step 2 gives the ClickHouse account only `SHOW TABLES, SHOW COLUMNS`; [`scripts/run_plan.py`](../scripts/run_plan.py) needs `SELECT` (code 497 otherwise). Say so.
- The example `.env.example` has `DBIG_SIGNING_KEY` commented out, and `. ./.env` fails on a malformed line without naming it. Add a one-line `.env` checker or name the variable in the guide.
- The guide's SQL checks assume `psql` and `mongosh`; neither the collector's drivers (`pg8000`, `clickhouse_connect`, `pymongo`) nor this machine have `mongosh`.
- The guide assumes vLLM; llama.cpp is a likely setup. Add a compatibility note for B1 and B2.

### C4. Noise from MongoDB schema-description collections
- `diffbot__schemas`, `zoominfo__schemas` (200 and 212 field paths) and `zoominfo_source_profiles` (143) document other tables' columns (`db_schema.Company.columns.website`, `...sample_row.CountryCode`). They crowd search results and create look-alike key names. Proposal: an optional exclude list per source, or a tag that lets search skip them.

## D. Added after the owner's review (2026-10-12)

### D1. Statistics are missing for ClickHouse and MongoDB, and the key columns are hashed
- Detail and per-source tables: [`statistics-analysis.md`](statistics-analysis.md). ClickHouse yields only an average width (Null %, Distinct, Uniqueness empty for all 46 fields; the 4 `Industry` fields have nothing because `system.columns` returns 0 uncompressed bytes for them). MongoDB yields only null fraction and sample count from at most 100 documents. The ClickHouse sorting and primary keys are stored only as `primary_key_sha256` / `sort_key_sha256`, so the column names of the key are lost.
- Proposal: store the key column names; show on the page why a cell is empty; optionally add a bounded cardinality probe for ClickHouse and MongoDB under the existing budgets.
- Check: ClickHouse `Company` shows `companyID` as its key, and `Company.companyID` becomes a candidate parent (flagged weak).

### D2. Cross-source candidates need a provenance check (links to A1, A2)
- Detail: [`candidate-trace.md`](candidate-trace.md). The builder compares every field of every source with every parent in every source ([`builder.py`](../packages/catalog-builder/src/dbig_builder/builder.py#L503-L513) lines 503-513) and the only blockers are type and table identity. 16 of 21 candidates linked two databases that hold different datasets. The owner's marks and the value overlap agree exactly (5 true with 100% overlap, 16 false with 0%).
- Proposal: (1) do not publish a cross-source candidate as `REFERENCE_CANDIDATE` with equal standing unless value evidence exists; at minimum label it "cross-source, values not compared"; (2) when sampling is on, treat 0% containment as a reason to drop the candidate; (3) let the operator declare dataset groups or forbidden source pairs in configuration; (4) note that a partial overlap is not proof either: the ClickHouse to ClickHouse relationships the owner confirmed show only 21% to 27% in the first 5000 values (the clone has orphan rows), so thresholds must be calibrated on labelled data, which the page already says ("not calibrated").
- Check: the 16 false candidates are no longer shown as ordinary candidates; the 5 true ones are.

### D3. Lower-case names with no separator are never read as references (links to B5)
- Trace in [`candidate-trace.md`](candidate-trace.md): `companyid`, `personid`, `classificationid` give stem == whole name, so `companyexperience.companyid` and `companyindustry.classificationid` are never candidates, and the planner answers J1 and J2 with `unavailable`. The planner depends entirely on the builder's edges, so every gap above becomes a missing join path.

### D4. The planner answers "found" for paths built on false candidates
- J3 (ClickHouse `Experience` to `Company`) is answered with three `tentative` paths through PostgreSQL built from candidates #3 and #13, both false; executed, they join 0 rows. The direct ClickHouse join is not offered. Proposal: when the plan's two ends are in one source, prefer a same-source relationship over a roundabout cross-source one, and make the planner warn when a path's every edge is a cross-source candidate with no value evidence.

### D5. The question-answering context cannot support harder questions
- Questions and expected answers: [`model-questions.md`](model-questions.md) (4 categories x 5). Facts from [`answer.py`](../packages/requester/src/dbig_requester/answer.py#L67-L89) lines 67-89 and 129: the model receives only the top 10 search hits (`id`, `name`, `kind`, `source_id`, `data_type`, `object_name`, `namespace`, `description`); no relationships, keys, statistics or table summaries; an answer without citations is rejected; the words `lineage`, `derived from`, `flows into`, `writes to` refuse the question.
- Consequences to fix, per category: **steps** questions need the relationship edges (declared, candidate with their evidence status, and confirmed) for the retrieved objects in the context; **comprehensive** questions need either a larger or grouped retrieval (all columns of an object, one line per object) or a two-stage answer (first select objects, then expand their columns); **inference** questions need an allowed way to answer "not established by the catalogue" with a citation to what was checked, since an uncited answer is currently invalid; **direct** questions need B3 (camelCase search). The refusal on the words `derived from` should apply to the model's claim of lineage, not to the user's wording.
- Check: after the fix, the 20 questions in [`model-questions.md`](model-questions.md) run against the same snapshot and the results are compared with the expected answers.

### D6. Search vocabulary: no stemming, source names and kinds not searchable, camelCase not split
- Detail and per-question numbers: [`model-questions-results.md`](model-questions-results.md). Tested with the app's own search: `industry` matches 49 entries and `industries` 0; `company` 50 and `companies` 0; `table` 13 and `tables` 0; `ClickHouse` 0. Three answerable questions (industry names, ClickHouse tables, relationships between companies) returned `NO_CATALOGUE_MATCH` for this reason, and the app then refuses to call the model.
- Retrieval measured without the model: of the 72 catalogue entries the 20 expected answers need, the top 10 hits (the model's whole context) contain 18 (25%) and the top 30 contain 24 (33%). Misses come from camelCase (`companyTicker`, `companyRevenue`, `companyEmployees`), plurals, source names, and generic words ranking other columns first. So most hard questions cannot be answered correctly even by a perfect model.
- Proposal: (1) index a stemmed form (FTS5 `porter` tokenizer or a plural fold) next to the exact form; (2) split camelCase at index time (B3); (3) index `source_id`, the engine kind and the namespace as searchable words, so "ClickHouse tables" works; (4) for questions that ask for a list of tables or columns of a source, retrieve by object (all objects of that source) instead of by word match; (5) rank object-level hits above generic column hits when the question names a table or a topic.
- Check: the three questions above return an answer; the retrieval table in [`model-questions-results.md`](model-questions-results.md) is re-run and shows a higher share of expected entries in the top 10.

### D7. Busy shared model server: the app's 60 s timeout turns queueing into "model unavailable"
- With the model in use by someone else, requests queued: the same rejected request took 19 s, and two questions hit `timeout_seconds = 60` (60.9 s). The app reports them with the same `MODEL_UNAVAILABLE_OR_INVALID`. Proposal: separate code for a timeout (see C1); document that `timeout_seconds` must cover queueing on shared servers.

### D8. What the model diagnostic adds (details and per-question grading: [`model-questions-results.md`](model-questions-results.md), Part 3)
- Plain-text answers from the same retrieved context (outside the app): at the app's 10 hits, 5 right, 3 partial, 2 wrong, 10 gaps (retrieval did not supply the entries; the model correctly said so). The model never invented a table or column.
- **Noise collections cause wrong answers.** D4 and C5 were answered with `db_schema.Company.columns.*` paths from the MongoDB schema-description collections instead of the real columns. Item C4 (exclude or demote those collections) is now tied to wrong answers, not only to noise.
- **Wrong-source answers.** S1 asked for PostgreSQL and was answered with ClickHouse tables. Proposal: when the question names a source or engine, filter or boost retrieval by `source_id`/kind (needs D6 point 3).
- **The model guessed a relationship from names.** S5 proposed `company.companyid` to `companyinvestor.investorcompanyid`, which the owner marked false. Giving the model the relationship list with its evidence status (declared, candidate, measured overlap) would let it say which links are weak.
- **The trap question.** I1 (join PostgreSQL and ClickHouse company ids) was answered "Yes" at 30 hits. Value-overlap evidence in the context ("cross-source, 0 of 5000 values found") would let the model answer correctly.
- **Answer rules.** An answer containing the word `lineage` is rejected even when it says the catalogue has none ([`answer.py`](../packages/requester/src/dbig_requester/answer.py#L62) lines 62 and 131-132). An uncited "the catalogue does not contain this" answer is invalid (line 129), though it is the right answer to seven of the twenty questions. Proposal: validate claims, not words, and allow an explicit "not established" outcome with a code, not an error.
- **Answers as bare IDs.** C1 (top 30), C4 and C5 returned lists of IDs with no names. Proposal: have the model, or the app, resolve each cited ID to its object and column name in the reply.
- **Check:** after the fixes, re-run the 20 questions with `ask` (it must return answers on llama.cpp as well) and compare with the grading table; the target is no Wrong, and Gaps only for questions the catalogue truly cannot answer.

### D9. The ClickHouse links the app misses are real, and they are the zoominfo half of the data
- The owner confirmed all 11 Mongo `zoominfo_*` to ClickHouse pairs (Z1 to Z11) as true. Their value overlap is only 25% to 40% (for example `zoominfo_Company.companyID` 27 of 93, `zoominfo_Experience.personID` 1222 of 5000), and the same columns against PostgreSQL score 0%. The ClickHouse to ClickHouse pairs the owner also confirmed score 21% to 27%.
- Consequence for the design: a true relationship can show a low overlap because the clones are partial (child values without parents), so a measured overlap must be used as a ranking and a warning, not as a pass/fail test. The signal that separates the two groups is "0 of N" against "some". Proposal: report the overlap number and the sample size on the candidate, treat zero found in a large sample as strong evidence against, and calibrate any further threshold on the owner's labelled pairs (21 candidates and 42 pairs are now labelled in [`candidate-trace.md`](candidate-trace.md) and [`live-test-report.md`](live-test-report.md) section 8).
- Check: with ClickHouse tables allowed as parents (D1, B5), Z1 to Z11 are proposed with their overlap shown, and the 16 false candidates are shown with 0 found.
