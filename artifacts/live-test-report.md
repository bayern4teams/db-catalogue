# Live test report: db-catalogue

Final report. Last updated 2026-10-12. Test run 2026-10-06 to 2026-10-12. No secret, password, key, connection string or row value is in this report or in the files it links to; value-based results are counts only.

## Results at a glance

| Section | Result | Main point |
|---|---|---|
| 1 Install and tests | PASS | 477 passed, 69 skipped, 0 failed |
| 2 Read-only accounts | PASS | all three writes refused (messages in section 2) |
| 3 `.env` | PASS after fixes | three problems found and fixed |
| 4 Source configs | PASS | |
| 5 TLS | SKIPPED | plaintext on loopback; TLS never tested |
| 6 Collect, assemble, render | PASS | `complete`, 48 objects, 1035 fields |
| 7 Counts match | PASS / PARTIAL | counts, names, types match; keys, statistics, comments limited (database vs app table) |
| 8 Relationships | RESULT | 5 true, 16 false of 21 proposed; 40 confirmed relationships missing |
| 9 Search and plans | PARTIAL | 4 of 5 searches pass; J1, J2 no path; J3, J4 built on false candidates and join 0 rows |
| 10 Model questions | FAIL | the app answers 0 of 30 questions; model itself behaves well; retrieval reaches 25% of what is needed |
| 11 Model review | PARTIAL | fails with shipped settings; 5 candidates complete; 1 of 5 suggestions agrees with your marks |
| 12 Optional | SKIPPED | |
| 13 Report | this file | 8 items mapped below |

## Linked documents (all in `artifacts/`, all committed)

Start here, then follow the links. Written documents:

| File | What it holds |
|---|---|
| [`candidate-trace.md`](candidate-trace.md) | Why the app proposed each of the 21 relationships, traced to code lines; your marks; why J1 to J4 behave as they do |
| [`statistics-analysis.md`](statistics-analysis.md) | Why some catalogue cells (Null %, Distinct, Uniqueness, Avg bytes) are empty, per source |
| [`engineer-fixes.md`](engineer-fixes.md) | Every proposed fix for the engineer, grouped A to D, each with code location and a check |
| [`model-questions.md`](model-questions.md) | The 20 harder questions in four categories with expected answers |
| [`model-questions-results.md`](model-questions-results.md) | Results of those 20: the app's `ask`, retrieval recall, and the graded plain-text model diagnostic |

Evidence files (data behind the numbers; counts and names only, no row values):

| File | What it holds |
|---|---|
| [`run-20261006-035124/overlap-counts.json`](run-20261006-035124/overlap-counts.json), [`run-20261006-035124/overlap-counts-zoominfo-to-clickhouse.json`](run-20261006-035124/overlap-counts-zoominfo-to-clickhouse.json) | Value-overlap counts per pair (section 8) |
| [`run-20261006-035124/candidate-trace.json`](run-20261006-035124/candidate-trace.json) | Rule, score and stem of every candidate |
| [`run-20261006-035124/join-paths.json`](run-20261006-035124/join-paths.json), [`run-20261006-035124/plan-J3.json`](run-20261006-035124/plan-J3.json), [`run-20261006-035124/plan-J4.json`](run-20261006-035124/plan-J4.json) | Join paths and plans (section 9) |
| [`run-20261006-035124/model-questions-ask.json`](run-20261006-035124/model-questions-ask.json), [`run-20261006-035124/model-questions-diag.json`](run-20261006-035124/model-questions-diag.json) | The 20 questions: `ask` results and the graded model answers. The `-no-model` and `-tunnel-down-run` variants are the first attempts made while the SSH tunnel was down |
| [`run-20261006-035124/snapshot/`](run-20261006-035124/snapshot/) | The catalogue snapshot of the run (nodes, edges, evidence, runs, manifest) |
| [`run-20261006-035124/snapshot-reviewed-small/`](run-20261006-035124/snapshot-reviewed-small/), [`run-20261006-035124/snapshot-no-model/`](run-20261006-035124/snapshot-no-model/) | The snapshot with the model review (5 suggestions) and with the model unreachable (`partial`) |
| [`run-20261006-035124/bundle-pg/`](run-20261006-035124/bundle-pg/), [`run-20261006-035124/bundle-ch/`](run-20261006-035124/bundle-ch/), [`run-20261006-035124/bundle-mongo/`](run-20261006-035124/bundle-mongo/) | The three collector bundles; `profiles.jsonl` is the source of the statistics |
| [`run-20261006-035124/catalog.html`](run-20261006-035124/catalog.html), [`run-20261006-035124/catalog-reviewed.html`](run-20261006-035124/catalog-reviewed.html), [`run-20261006-035124/catalog-no-model.html`](run-20261006-035124/catalog-no-model.html) | The rendered catalogue pages (download and open in a browser) |
| [`run-20261006-035124/requester.toml`](run-20261006-035124/requester.toml), [`run-20261006-035124/requester-vllm.toml`](run-20261006-035124/requester-vllm.toml) | The requester configurations used |
| [`config/`](config/) | The source and builder configurations used, including three model-review variants |
| [`logs/`](logs/) | Command output of collect, assemble, render, `run_plan` and the model diagnostic |
| [`section1-pytest.log`](section1-pytest.log), [`section2-write-refusals.txt`](section2-write-refusals.txt), [`section2_write_check.py`](section2_write_check.py) | Test-suite log; write-refusal output and the script that produced it |

Not committed (regenerable, large, or machine-local): the planner graph and the requester search index (rebuild with `dbig-planner build` and `dbig-requester index`, commands in `docs/LOCAL_RUN.md`), the three failed-review snapshots, `.env`, and any password, key or connection string. The signing key is not in the repository, so the bundles can be read but not re-assembled.

## Run facts

| Item | Value |
|---|---|
| Branch / commit | `claude/gracious-bardeen-x1y6he` / `2e87fa7` |
| Remote | `https://github.com/Reza-Sh83/db-catalogue.git` (the guide's clone URL names `bayern4teams/db-catalogue`; not changed) |
| Python / uv | 3.12.13 / 0.12.17 |
| Run folder | `artifacts/run-20261006-035124/` |
| Sources | PostgreSQL schema `public` (`pg-main`), ClickHouse database `testzoominfo_clone2` (`ch-main`), MongoDB database `enrichment_engine` (`mongo-main`) |
| Server versions | PostgreSQL 16.15 (a version the guide verified), ClickHouse 26.8.2.7 (between the guide's two verified sets, 25.8 and 26.9; the guide says "will probably work"), MongoDB 7.0.41 (verified) |
| Network | all three on this machine, plaintext on `127.0.0.1` (ports 5433, 8125, 27018), `tls = false`, no CA file |
| Accounts | `dbig_reader` on each; created by you; ClickHouse `SELECT` on `testzoominfo_clone2.*` added by you on 2026-10-12 |
| Model | `Qwen3.8-Flash-Next` at `10.10.9.45:8000` through your SSH tunnel at `127.0.0.1:8000`; revision recorded as `178b998806b7b406c311a3e6174aa99cf6304eaf`; GPU: an RTX card (model not given) |
| Model server software | You use llama.cpp and sometimes vLLM. This run's replies carry `system_fingerprint: b1-d1a9235` and a `timings` block, which identify llama.cpp's server. Nothing was tested against vLLM |
| Model auth | The server answers without any API key (see limitation 23) |
| Reading of databases | Metadata for the catalogue. Value-based checks: the guide's hand check (section 9) and the overlap measurement you approved (section 8). Both held values in memory only and kept counts |

## Failures and tracebacks (first)

No Python traceback came from the project. The one traceback-like output in the run was my own script (a wrong import) and is not a project fault. Failures, most serious first:

1. **Section 8 / app: 16 of 21 proposed relationships are wrong** (your marks; value overlap 0% for exactly those 16), and the app shows them with the same status and score as the 5 correct ones. The app also misses 40 relationships you confirmed as true. Cause in [`candidate-trace.md`](candidate-trace.md): no source boundary, name-only match, only PostgreSQL can supply a parent, no value check. Engineer items A1, A2, B5, D2, D3.
2. **Section 9 / planner: two of three same-database joins have no path (J1, J2), the third (J3) is answered with roundabout paths built on false candidates, and the cross-database plan (J4) joins 0 rows.** Causes in [`candidate-trace.md`](candidate-trace.md). Engineer items D3, D4.
3. **Section 10: `dbig-requester ask` fails for every question** (`MODEL_UNAVAILABLE_OR_INVALID`). The model server answers `400 failed to parse grammar` to the request's JSON schema because of `maxLength: 2000`; without that keyword it accepts it. App-side request format, not the model. [`answer.py`](../packages/requester/src/dbig_requester/answer.py#L22) lines 22 and 118-126. Engineer item B1.
4. **Section 10, harder questions: the app cannot answer any of the 20, and retrieval is the main reason.** `ask` returns an error or `NO_CATALOGUE_MATCH` for all 20. Outside the app, with the model given the same context, only 5 of 20 answers are right at the app's 10 hits, because only 18 of the 72 catalogue entries the answers need are retrieved (25%). The model never invented a table or column; it said so when the context lacked the answer, and was wrong only where retrieval supplied noise or a name-guessed link. Details: [`model-questions-results.md`](model-questions-results.md). Engineer items B1, B3, B4, D5 to D8.
5. **Section 11 with the shipped settings: the model review fails** (`partial`). The model is a reasoning model and spends the default 512 tokens before it answers; 20 candidates did not finish in 8192 tokens; 5 candidates with 24000 tokens completed. Engineer item B2.
6. **Section 9: search misses camelCase names** (`revenue` does not find `Company.companyRevenue`). Engineer item B3.
7. **Section 10: the "no match" guard failed** for a question about something not in the catalogue. Engineer item B4.
8. **Section 9: plan execution first failed with ClickHouse code 497** because the account in [`docs/LOCAL_RUN.md`](../docs/LOCAL_RUN.md) step 2 has no `SELECT`. Fixed by your grant. Engineer item C3.
9. **Section 3:** `.env` did not load at first (unquoted JSON, missing `uri` key, a malformed `VLLM_API_KEY` line). Fixed by you.

## Section 1. Install and test suite: PASS

Commands: `uv sync --locked --all-packages --all-extras --python 3.12`, then `pytest -q`. Result: `477 passed, 69 skipped in 40.44s`, 0 failed (the guide expects about 480 passed, 70 skipped; the skipped tests need the CI database containers). Log: [`section1-pytest.log`](section1-pytest.log).

## Section 2. Read-only accounts: PASS

`dbig_reader` reads catalogue metadata on all three servers (sections 6 and 7). Write refusals, from [`section2_write_check.py`](section2_write_check.py) that you ran on 2026-10-12 (output in [`section2-write-refusals.txt`](section2-write-refusals.txt)):

| Server | Result |
|---|---|
| PostgreSQL | refused: `DatabaseError`, SQLSTATE `42501`, "permission denied for table person" |
| ClickHouse | refused: HTTP 403, `Code: 497 ... Not enough privileges ... grant INSERT(...) ON testzoominfo_clone2.Person` (ACCESS_DENIED), server version 26.8.2.7 |
| MongoDB | refused: `OperationFailure`, "not authorized on enrichment_engine to execute command { insert: dbig_write_probe ... }" |

The agent's own attempt to run these checks was blocked by the permission classifier and was not repeated. Note: with `SELECT` granted by you, the ClickHouse account now reads table data; it still cannot write.

## Section 3. Secrets (`.env`): PASS after fixes

Final state: the three connection values are valid JSON in single quotes, `DBIG_SIGNING_KEY` is 64 URL-safe characters, `VLLM_API_KEY` is set, `. ./.env` loads. Problems found on the way: unquoted JSON (shell parse error at line 8), a MongoDB value without the `"uri"` key, a malformed `VLLM_API_KEY` line (4 characters, with a quote and space) that made `. ./.env` fail with "command not found". The error never names the variable (limitation 25).

## Section 4. Source configuration files: PASS

`artifacts/config/*.toml` from [`examples/local/`](../examples/local/); changed only `source_id` (`pg-main`, `ch-main`, `mongo-main`), `namespaces` and `tls = false`. Nothing in `examples/` changed. Model values were filled into `builder-with-vllm.toml`; three variants exist for section 11.

## Section 5. TLS: SKIPPED

All connections are plaintext on loopback. PostgreSQL answers the TLS request with `N`; ClickHouse fails over HTTPS and answers `200` over HTTP on 8125; MongoDB was not probed. The collector accepts `tls = false` only for a loopback IP literal ([`runtime.py`](../packages/collectors/src/dbig_collectors/runtime.py#L1050-L1060) lines 1050-1060), hence `127.0.0.1`, not `localhost`. Certificate verification, CA files and host-name checks were not tested at all.

## Section 6. Collect, assemble, render: PASS

| Command | Result | Time |
|---|---|---|
| `dbig collect` PostgreSQL | bundle written, exit 0 | about 0.2 s |
| `dbig collect` ClickHouse | exit 0 | about 1 s |
| `dbig collect` MongoDB | exit 0 | under 1 s |
| `dbig-builder assemble` (no model) | `"completeness":"complete"`, `diagnostics: null` | about 1 s |
| `dbig-builder render` | 3 sources, 48 objects, 1035 fields, 0 declared foreign keys, 21 candidates, 0 diagnostics | under 1 s |

A search of every file under `artifacts/` for the signing key and the database passwords found nothing. You opened [`catalog.html`](run-20261006-035124/catalog.html) and reported it good.

## Section 7. The catalogue matches your databases: PASS for names, types and counts; PARTIAL for keys, statistics and comments

Counts, from read-only queries against the three databases:

| Source | Objects (DB / catalogue) | Columns (DB / catalogue) | Foreign keys (DB / catalogue) |
|---|---|---|---|
| PostgreSQL `public` | 13 tables, 0 materialized views / 13 | 124 / 124 | 0 / 0 |
| ClickHouse `testzoominfo_clone2` | 7 tables / 7 | 46 / 46 | none / none |
| MongoDB `enrichment_engine` | 28 collections, 0 views / 28 | no declared columns; 865 sampled fields | none / none |

Spot-check (names and types compared with the database's own catalogue): PostgreSQL `company` 25 of 25, ClickHouse `Company` 15 of 15, equal names and types. MongoDB `zoominfo_Company`: 15 sampled fields; not checkable without reading documents. One statistic checked by hand: `company._version` uniqueness 0.0002538 in the catalogue equals `n_distinct 1` over 3940 rows in `pg_stats`.

Why this section is not a full PASS, and whether each point is the database or the app:

| Point | What I found | Database or app |
|---|---|---|
| Keys, PostgreSQL | `public` has no primary, unique or foreign key (`constraints.jsonl` is empty). The page cannot show keys that do not exist | **Database design.** The app reports it faithfully |
| Keys, ClickHouse | The database declares sorting/primary keys (`Company(companyID)`, `Industry(industryID)`, `Location(locationID)`, `Person(personID)`, `Experience(personID, companyID)`, `CompanyIndustry(companyID, industryID)`, `PersonContact(freeMails)`). The collector reads them but stores only SHA-256 hashes (`primary_key_sha256`, `sort_key_sha256`), so the catalogue cannot say which columns are the key | **App** (engineer item D1) |
| Keys, MongoDB | One `index` constraint per collection, the default `_id` only (28) | Database |
| Statistics | Null %, Distinct, Uniqueness and Avg bytes are present for some fields and empty for others, by source: details in [`statistics-analysis.md`](statistics-analysis.md). PostgreSQL gives everything except Distinct/Uniqueness for 5 all-NULL columns; ClickHouse gives only Avg bytes (42 of 46 fields; none for the 4 `Industry` fields, where ClickHouse returns 0 bytes); MongoDB gives only Null % and a sample count from at most 100 documents. A missing value shows as an empty cell, not the word "null" | Mostly **database** (what each source exposes), plus **app** choices: no explanation on the page, no cardinality probe for ClickHouse and MongoDB, and the consequence that only PostgreSQL can supply a parent key (candidate-trace.md) |
| Comments | None of the three databases has a table or column comment, so the Description column and comment search were not exercised | Database (nothing to show) |
| MongoDB fields | Come from a sample of at most 100 documents per collection, so cannot be verified without reading more documents | By design |
| MongoDB views | None exist, so `VIEW_NOT_CATALOGUED` was not exercised | Not applicable |

## Section 8. Are the proposed relationships right?

**Your marks (recorded as given; details in [`candidate-trace.md`](candidate-trace.md)).**

| Group | True | False | Unsure |
|---|---|---|---|
| 21 proposed candidates | 5 (#2, #7, #8, #11, #12) | 16 (#1, #3, #4, #5, #6, #9, #10, #13 to #21) | 0 |
| My 14 PostgreSQL pairs | 12 | 2 (`investorcompanyid`, `relatedcompanyid`: their parent rows are not in the database) | 0 |
| My 6 ClickHouse pairs | 6 | 0 | 0 |
| My 10 MongoDB pairs | 10 | 0 | 0 |
| Cross-source M26 (`diffbot_CompanyIndustry.classificationID` to pg `industryclassification`) | 1 | 0 | 0 |
| 11 new MongoDB-zoominfo-to-ClickHouse pairs (Z1 to Z11, below) | 11 | 0 | 0 |
| **All my 42 pairs** (14 + 6 + 10 + 1 + 11) | **40** | **2** | **0** |

**`blocking_rule` of the wrong candidates:** all 16 are `suffix` with `ratio_percent` 100. The correct 5 are the same rule and score.

**Missing relationships.** The 21 proposed candidates miss 40 relationships you confirmed: 12 PostgreSQL, 6 ClickHouse, 10 MongoDB, 1 cross-source (M26) and 11 MongoDB-to-ClickHouse (Z1 to Z11). The complete list is below.

PostgreSQL to PostgreSQL (true): `companycontact`, `companyemployeecategory`, `companyexperience`, `companyfinancials`, `companyindustry`, `companyinvestmentround`, `companyrelation`, `companytechnographics` (each `.companyid` to `company.companyid`); `location.companyid` to `company.companyid`; `companyexperience.personid` to `person.personid`; `companyindustry.classificationid` to `industryclassification.classificationid`; `companyinvestor.investmentid` to `companyinvestmentround.investmentid`.

ClickHouse to ClickHouse (true): `CompanyIndustry.companyID` and `Experience.companyID` and `Location.companyID` to `Company.companyID`; `CompanyIndustry.industryID` to `Industry.industryID`; `Experience.personID` and `PersonContact.personID` to `Person.personID`.

MongoDB to MongoDB (true): `zoominfo_CompanyIndustry.companyID`, `zoominfo_Experience.companyID`, `zoominfo_location.companyID`, `zoominfo_contracts.company_id`, `zoominfo_evidence.company_id` to `zoominfo_Company.companyID`; `zoominfo_CompanyIndustry.industryID` and `zoominfo_products.industryID` to `zoominfo_industry.industryID`; `diffbot_CompanyExperience.companyID`, `diffbot_CompanyIndustry.companyID`, `diffbot_CompanyInvestmentRound.companyID` to `diffbot_Company.companyID`.

New, found after your explanation that the databases hold different datasets (you marked all eleven true): Mongo `zoominfo_*` columns to ClickHouse, Z1 `zoominfo_Company.companyID` to `Company.companyID`, Z2 `zoominfo_CompanyIndustry.companyID` to `Company.companyID`, Z3 `zoominfo_CompanyIndustry.industryID` to `Industry.industryID`, Z4 `zoominfo_Experience.companyID` to `Company.companyID`, Z5 `zoominfo_Experience.personID` to `Person.personID`, Z6 `zoominfo_staff.personID` to `Person.personID`, Z7 `zoominfo_contact_information.personID` to `Person.personID`, Z8 `zoominfo_location.locationID` to `Location.locationID`, Z9 `zoominfo_evidence.company_id` and Z10 `zoominfo_contracts.company_id` to `Company.companyID`, Z11 `zoominfo_industry.industryID` to `Industry.industryID`.

Not resolvable from the catalogue (no parent table exists in the three namespaces): `manufacturerID`, `sourceID`, `profileID`, `techID`, `contactid`, `financialid`, `empcatid`, `contractid`, `productid`, `provenanceRowID`, `countryCode`.

**Value overlap (counts only; method below the tables).** Child values found in the parent column, from up to 5000 distinct child values (the first rows returned, not random):

| Pairs | Result |
|---|---|
| 21 candidates, the 5 you marked true | 100%: #2 84/84, #7 5/5, #8 22/22, #11 22/22, #12 17/17 |
| 21 candidates, the 16 you marked false | 0%: #1 0/93, #3 0/5000, #4 0/5000, #5 0/5000, #6 0/5000, #9 0/5000, #10 0/93, #13 0/5000, #14 0/5000, #15 0/3777, #16 0/5000, #17 0/69, #18 0/10, #19 0/91, #20 0/103, #21 0/5000 |
| PostgreSQL pairs you marked true (12) | 99.7% to 100% (e.g. `companyindustry.classificationid` 339/340, `companyexperience.personid` 1214/1215, `companyindustry.companyid` 3940/3940) |
| PostgreSQL pairs you marked false | `investorcompanyid` 0/794, `relatedcompanyid` 0/5000 |
| ClickHouse to ClickHouse (you marked true) | 20.6% to 26.9% (M15 1294/5000, M16 37/180, M17 1343/5000, M18 1278/5000, M19 1343/5000, M20 1209/5000) |
| MongoDB to MongoDB (you marked true) | 100% where measurable; two pairs (`zoominfo_products.industryID`, `zoominfo_location.companyID`) had no string values to measure |
| Cross-source M26 (you marked true) | 14/14 |
| Mongo `zoominfo_*` to ClickHouse (Z1 to Z11, you marked all true) | 27/93, 25/91, 8/41, 27/93, 1222/5000, 1264/5000, 928/3777, 39/103, 20/69, 3/10, 8/41 (about 25% to 40%); the reverse direction Z12 `Industry.industryID` to `zoominfo_industry` is 8/37 |

Method and cautions: values were read from the databases with the read-only accounts, held in memory, compared, and discarded; only the counts are stored. A sample of the first rows can mislead (a 26% result is not a measure of the whole table; 0 of 5000 is strong evidence). The ClickHouse to ClickHouse pairs you marked true score only 21% to 27%: the clone has child values without parents, so overlap alone cannot prove or disprove a relationship and thresholds need calibration on labelled data. This read key columns of all three databases, which you approved for this step; a first reverse count of PostgreSQL's 3940 company ids against ClickHouse was done earlier in section 9 and went beyond the guide's hand check.

**Why the app proposed the 16 wrong ones, and why it missed the others:** [`candidate-trace.md`](candidate-trace.md), with the code lines.

## Section 9. Search and plans: PARTIAL

**Search.** Setup: `printf ... > requester.toml`, then `dbig-requester index --config "$out/requester.toml"` (0.5 s, digest printed), then `dbig-requester search` for each question. Each search took about 0.5 s.

| # | Question | Expected in the top five | Result |
|---|---|---|---|
| 1 | `company website` (all words) | pg `company.website`, ch `Company.website` | PASS: results 1 and 2 |
| 2 | `industry classification` | `industryclassification`, `Industry` | PASS for pg `industryclassification.classificationid` (the only hit). ch `Industry` not returned |
| 3 | `person role` | `person.role` | PASS |
| 4 | `where is the company's revenue` (any word) | `Company.companyRevenue` | **FAIL**: five `companyid` columns |
| 5 | `location country` | `location.country` | PASS |

Why #4 fails: search is SQLite FTS5 prefix matching on whole words ([`search.py`](../packages/requester/src/dbig_requester/search.py#L68) lines 68 and 166). `companyRevenue` is stored as one word, `companyrevenue`. Underscored names are split, camelCase names are not. `revenue` alone returns nothing, `company revenue` with all words returns nothing, with any word only `company*` columns match. This affects every camelCase column of ClickHouse and MongoDB.

**Join paths** (`dbig-planner build` found 21 reference edges, 0 lineage edges; then `join-paths` between object ids):

| Join | Answer | Why (full trace in [`candidate-trace.md`](candidate-trace.md)) |
|---|---|---|
| J1 pg `companyexperience` to `company` | `unavailable` | You are right that the link exists (99.7% to 100% overlap). The builder never created the edge: the child name `companyid` has no separator, so no reference suffix is recognised, and the parent is not a declared key so the same-name shortcut does not apply. The planner uses only the builder's edges |
| J2 pg `companyexperience` to `person` | `unavailable` | Same trace for `personid` |
| J3 ch `Experience` to ch `Company` | `found`, 3 `tentative` paths, none direct | ClickHouse `Company.companyID` can never be a parent (no uniqueness measurement; its key is stored only as a hash). The paths go through pg `company` using candidates #3 and #13, both false. Two further paths detour through pg `person` and a MongoDB collection |
| J4 ch `Company` to pg `company` | `found`, 1 `tentative` path | Built on false candidate #13 |
| X1 pg `companyindustry` to `industryclassification` (extra) | `unavailable` | Same trace as J1 |

**Plans.** `dbig-planner plan --path-id ...` for the first J3 path and for J4 ([`plan-J3.json`](run-20261006-035124/plan-J3.json), [`plan-J4.json`](run-20261006-035124/plan-J4.json)): `status: unresolved`, as the guide says for cross-database plans; one read per database plus `logical_join` steps; row limit 1000; the only warnings are the row limit and "candidate relationship requires human review".

**Execution.** `python scripts/run_plan.py --plan ... --config` for the three source files:

| Attempt | Result |
|---|---|
| First (2026-10-06), account as in [`docs/LOCAL_RUN.md`](../docs/LOCAL_RUN.md) step 2 | Both plans stopped: `Code: 497 ... Not enough privileges ... grant SELECT(...) ON testzoominfo_clone2.Company` (and `.Experience`). The documented ClickHouse account has `SHOW TABLES, SHOW COLUMNS` only |
| After your `GRANT SELECT` (2026-10-12) | Both ran, exit 0, about 1 s each |

| Plan | Step 1 (ClickHouse) | Step 2 (PostgreSQL) | Joined rows | Left rows without a match |
|---|---|---|---|---|
| J4 ch `Company` to pg `company` | 1000 rows | 0 rows (1000 keys bound) | **0** | 1000 |
| J3 ch `Experience` to ch `Company` via pg `company` | 1000 rows (825 distinct company keys) | 0 rows | **0** | 1000 |

(J3's third step received 0 keys and read 0 rows.)

**Hand check, as the guide asks.** I took 1000 ClickHouse `Company.companyID` keys and counted them in PostgreSQL `company.companyid` with my own query: **0 matches**, equal to the plan's `joined_rows`. So the plan matches the hand count; the finding is that the databases do not share company identifiers: all 3940 PostgreSQL values are 23 characters, ClickHouse's are 7 to 9 characters (776 of 1000 sampled have 9, 194 have 8, 24 have 7), and 0 of 3940 PostgreSQL ids exist in ClickHouse `Company`. The reverse count read PostgreSQL's key column in memory beyond the guide's hand check, which I disclosed to you before continuing. Not run: a same-database plan (J1, J2 have no path) and the optional observation test (no query logs were provided), so the same-database row-count comparison of the guide was not possible.

## Section 10. Your model: questions: FAIL

Setup: [`requester-vllm.toml`](run-20261006-035124/requester-vllm.toml) with `model = "Qwen3.8-Flash-Next"` and the revision above. The server and key work (`/v1/models` lists the model; a plain chat answers in 0.65 s). The model is a reasoning model (replies carry `reasoning_content`).

Easy questions (category 0), run 2026-10-06:

| # | Question | Time | Result |
|---|---|---|---|
| 1 | Which column holds a company's website? | 0.97 s | `MODEL_UNAVAILABLE_OR_INVALID` |
| 2 | Where is a person's name stored? | 0.74 s | same |
| 3 | Which table links companies to industries? | 0.74 s | same |
| 4 | Where is the company's employee count? | 0.74 s | same |
| 5 | Which column holds a company's revenue? | 0.72 s | same |
| 6 | Where are company locations stored? | 0.74 s | same |
| 7 | Which column holds the country of a location? | 0.72 s | same |
| 8 | Where is a company's ticker symbol? | 0.73 s | same |
| 9 | Which table holds investment rounds? | 0.73 s | same |
| 10 | Which column holds a customer's credit card number? (expected `NO_CATALOGUE_MATCH`) | 0.73 s | `MODEL_UNAVAILABLE_OR_INVALID`: the model was called |

Right / wrong / refused: 0 / 0 / 10 errors. The model's answer quality was not measured.

**Cause of 1 to 9.** The request carries a JSON schema with `maxLength: 2000` on `answer` ([`answer.py`](../packages/requester/src/dbig_requester/answer.py#L22) line 22, sent at lines 118-126). The model server returns `400 Failed to initialize samplers: failed to parse grammar`. Tested with dummy data: the schema is accepted without `maxLength`, `minLength`, `maxItems`, `minItems`; the builder's review schema (`reason` with `max_length=256`, [`reviewer.py`](../packages/catalog-builder/src/dbig_builder/reviewer.py#L27) line 27) is accepted, so the size of the bound may matter. Not a problem of the model or of your data; the app was written for vLLM, which was not tested.
**Cause of 10.** Retrieval found entries: `column` and `number` match MongoDB fields literally named `column` (`provenance.column`, `evidence_columns`), and `column` is not a stopword ([`search.py`](../packages/requester/src/dbig_requester/search.py#L23) line 23). The guard returned nothing to stop on, so the model was called.

**Harder questions (20, four categories, approved by you).** Questions, expected answers and grading: [`model-questions.md`](model-questions.md), [`model-questions-results.md`](model-questions-results.md).

1. *As the app works (`dbig-requester ask`).* 17 of 20 return `MODEL_UNAVAILABLE_OR_INVALID` (the schema rejection above, or a 60 s timeout while the shared model server was busy: C1 and C2 hit it). 3 return `NO_CATALOGUE_MATCH` although the catalogue holds the answer (D5, C3, I5): the search has no word stemming (`industry` finds 49 entries, `industries` 0; `company`/`companies`, `table`/`tables`) and a source name such as "ClickHouse" is not searchable. 0 answers.
2. *Retrieval, without the model.* The model receives only the top 10 search hits. Of the 72 catalogue entries the expected answers need, 18 (25%) are in the top 10 and 24 (33%) in the top 30: direct 2 of 8, steps 8 of 23, comprehensive 6 of 28, inference 2 of 13.
3. *Diagnostic outside the app (plain text, no JSON schema, same context, 40 requests, 0 errors).* Graded by a rubric written beforehand:

| | Right | Partial | Wrong | Gap (retrieval failure) |
|---|---|---|---|---|
| Context of 10 hits (the app's) | 5 | 3 | 2 | 10 |
| Context of 30 hits | 4 | 4 | 3 | 9 |

By category at 10 hits: direct 2 right, 3 gap; steps 0 right, 2 partial, 1 wrong, 2 gap; comprehensive 1 right, 1 partial, 1 wrong, 2 gap; inference 2 right, 3 gap. Right: D1, D3, C1, I1, I3. Wrong: S1 (a PostgreSQL question answered with ClickHouse tables), C5 (five bare IDs). At 30 hits the wrong ones are D4 and C5 (MongoDB schema-description noise, `db_schema.*.columns.*`, instead of the real columns) and I1.

What this shows:
- The model behaves well: it never invented a table or a column, said so when the entries lacked the answer (10 of 20), and gave the ideal "not confirmed" answer to I3. Its two false statements were caused by what it was given: the wrong source (S1) and a name-guessed relationship that you marked false (S5: `company` to `companyinvestor.investorcompanyid`).
- The trap question I1 (can the company ids be joined directly?) was answered correctly at 10 hits only because the ClickHouse side was not retrieved; at 30 hits the model said "Yes". The catalogue gives it no way to know that the two key columns share no values (0 of 5000).
- More context is not the fix: 30 hits changed the tally little and added noise.
- The steps questions need the relationships, which the app does not pass to the model. The one fully correct steps answer (S5 at 30 hits) was a case where the table names line up.
- The app's own answer rules would reject two kinds of correct answers: any answer containing the word `lineage` (S4 at 30 hits said the catalogue "does not specify an explicit join or lineage"), and any answer without a cited ID, although "the catalogue does not contain this" is the right answer to seven of the twenty questions.
- Cost: 148 to 9294 completion tokens per answer and 2 to 101 s on a shared server; the default `max_tokens = 512` would cut most of them off.

Recorded for the engineer: the app should answer these hard questions too (engineer items D5 to D8 in [`engineer-fixes.md`](engineer-fixes.md), with a check that re-runs the same 20 questions).

## Section 11. Your model: relationship review: PARTIAL

| Run | Settings | Result |
|---|---|---|
| Shipped defaults | 20 candidates, `max_tokens` 512, timeout 60 s | `partial`, review failed in 11 s: 512 reasoning tokens, empty answer (`finish_reason: length`, content length 0) |
| Variant A (`config/builder-with-vllm-bigtokens.toml`) | 20 candidates, `max_tokens` 8192, timeout 300 s | `partial`, 72 s, answer cut off at the limit (1042 characters of unfinished JSON) |
| Variant B (`config/builder-with-vllm-small.toml`) | **5 candidates**, `max_tokens` 24000, timeout 900 s | `complete`, 5 `model_suggestion` records, 1858 tokens, 14.9 s |

Variants A and B change settings the guide does not mention. The model said `supports` for all five: ch `Experience.companyID` to pg `company` (false by your mark), mongo `zoominfo_Experience.companyID` to pg `company` (false), ch `Experience.personID` to pg `person` (false), mongo `diffbot_CompanyExperience.personID` to pg `person` (true), ch `CompanyIndustry.companyID` to pg `company` (false). **Against your marks: 1 of 5 correct.** The model said `supports` for four relationships you marked false; its explanations repeat the existing evidence (name, type, measured uniqueness) and add nothing. This is expected: the review prompt gets names and evidence metrics, no values (by design), so it cannot detect that the ids belong to different datasets. Fixes: engineer items A1 and D2 (give it the overlap).

Model failure case. The guide says to stop vLLM; I could not stop a server on another machine, and you confirmed the substitute is enough. Substitute: a config pointing at an unused local port. Result: assemble exit 0 in 1 s, `completeness: partial`, the page lists `MODEL_REVIEW_FAILED` and `MODEL_UNAVAILABLE_OR_INVALID`. Publication survives an unreachable model. The same two codes appear when the model answered badly (shipped defaults), so the page cannot tell the cases apart (engineer item C1).

## Section 12. Optional: SKIPPED

Value-overlap sampling inside the product (`sample_rows` with `value_token_key_env`), the HTTP services and scan requests were not requested. The overlap in section 8 is a separate, one-off measurement by the tester.

## Section 13. What to send back (the guide's eight items)

| Item | Where |
|---|---|
| 1. `pytest -q` summary | Section 1: 477 passed, 69 skipped |
| 2. Server versions, three write refusals | Run facts; section 2 |
| 3. collect/assemble messages, completeness, diagnostics, timings | Section 6 (complete, 0 diagnostics); section 11 for partial runs |
| 4. Count comparison, wrong tables | Section 7 (no wrong table found; keys, statistics, comments noted) |
| 5. Relationship marks, `blocking_rule` of wrong ones, missing ones | Section 8: true 5, false 16, unsure 0; all wrong ones `suffix`; 40 confirmed missing |
| 6. Search questions; plan summaries next to own counts | Section 9 |
| 7. Model name and revision, GPU, question results and times, review comparison | Run facts; sections 10 and 11: easy questions 0 of 10 answered by the app; harder questions 0 of 20 by the app, 5 right at 10 hits outside it; review 1 of 5 suggestions agrees with your marks |
| 8. Every traceback | None from the project |

## Limitations observed (all)

**Tested and not tested**
1. TLS was not tested: no certificate, CA file or host-name check ran (section 5).
2. Only loopback, plaintext; one version of each server; ClickHouse 26.8.2.7 is not one of the two verified sets.
3. The model was tested only through llama.cpp, not vLLM, which the app targets.
4. Comments, views, declared foreign keys, PostgreSQL primary keys, composite keys and partitions are absent from your data, so those code paths were not exercised.
5. MongoDB field lists cannot be verified without reading more documents.
6. Query logs and observation files were not provided, so `observed` join paths, lineage paths and the same-database plan execution of section 9 were not tested.
7. Section 10 was run on a shared model server whose speed varied from 2 s to 101 s per request; the model's answers were graded once, by the tester, against expected answers taken from the catalogue and the owner's confirmed relationships.

**Relationship inference (details in [`candidate-trace.md`](candidate-trace.md))**
8. 16 of 21 candidates are false (your marks); 40 relationships you confirmed are not proposed. The page presents both groups identically (`tentative`, confidence "not calibrated").
9. No source boundary: every field is compared with every parent in every source ([`builder.py`](../packages/catalog-builder/src/dbig_builder/builder.py#L503-L513) lines 503-513).
10. Name match against the parent table's name is enough (lines 108-132, 519-546); no check of the parent *column's* name, so `Location.locationID` attached to `location.companyid`.
11. Only PostgreSQL can supply a parent key, through planner statistics at uniqueness >= 0.99; ClickHouse and MongoDB cannot ([statistics-analysis.md](statistics-analysis.md)). ClickHouse's declared keys are stored only as hashes.
12. Lowercase names without a separator (`companyid`, `classificationid`) are not read as references, which hides the whole PostgreSQL schema.
13. With sampling off there is no value evidence at all, though the design ([`TECHNICAL_IMPLEMENTATION.md`](../TECHNICAL_IMPLEMENTATION.md#L289) line 289) says to require compatible value domains and abstain when support is insufficient.
14. Statistics are estimates (`pg_stats`), and the 0.99 threshold can pick the wrong column (`locationid` 0.9826 against `companyid` 1.0).
15. MongoDB schema-description collections (`__schemas`, `zoominfo_source_profiles`; 143 to 212 field paths each) create look-alike names and noise in search.
16. Value overlap alone is not a sufficient test either: relationships you confirmed score 21% to 27% (ClickHouse) because the clone has orphan rows.

**Planner**
17. Join paths exist only over the builder's edges, so every gap above is a missing path (J1, J2, X1). J3 is answered with paths built on false candidates and J4 joins 0 rows; the warning does not say so.
18. Lineage paths are empty without observation files (`lineage_edges: 0`).
19. Plan execution needs `SELECT` on ClickHouse, which [`docs/LOCAL_RUN.md`](../docs/LOCAL_RUN.md) step 2 does not grant.

**Search and answers**
20. camelCase column names are one word; search by a part of the name fails (`companyRevenue`).
21. The "no match" guard is defeated by schema words (`column`, `number`).
22. Every failure of `ask` and of the review returns the same code `MODEL_UNAVAILABLE_OR_INVALID`; the `ask` request is rejected by llama.cpp (`maxLength`), and a reasoning model exhausts the default `max_tokens = 512`.
23. The model server accepts requests without any API key (HTTP 200 with and without an `Authorization` header). The model-review and answer paths therefore ran unauthenticated; the guide requires `--api-key`.
24. The model is reached through an SSH tunnel because the config accepts plaintext only to loopback IP literals; a direct `http://10.10.9.45:8000` would be refused.
25. `. ./.env` fails on any malformed line without naming the variable; `.env.example` has `DBIG_SIGNING_KEY` commented out.
26. The model's answers are constrained to cite retrieved IDs and exclude lineage claims; an honest "not established" answer is rejected, and a question containing `derived from` is refused (engineer item D5).
27. The `render` summary prints `diagnostics: 0` and the manifest shows `diagnostics: null` for a `partial` snapshot whose page lists two diagnostic codes.

**Retrieval and harder questions (details in [`model-questions-results.md`](model-questions-results.md))**
30. Of the 72 catalogue entries the 20 harder questions need, 25% reach the model at the app's limit of 10 hits and 33% at 30 hits.
31. No stemming: plurals and inflected words match nothing; a source or engine name ("ClickHouse") is not searchable. Three answerable questions got `NO_CATALOGUE_MATCH`.
32. MongoDB schema-description collections produce wrong answers (D4, C5 at 30 hits), not only noise.
33. The model is given no relationships, keys or value-overlap evidence, so it cannot answer the steps questions or detect that two key columns never meet (I1).
34. The app rejects any answer containing `lineage`, and any answer without a cited ID, including honest "not in the catalogue" answers.
35. On a busy shared model server, queueing turns into `MODEL_UNAVAILABLE_OR_INVALID` after 60 s, indistinguishable from other failures.

**Other**
36. The drivers the collector uses are `pg8000`, `clickhouse_connect`, `pymongo`; `psycopg` and `mongosh` are not installed here, while the guide's checks assume `psql` and `mongosh`.
37. The catalogue shows no explanation for empty statistic cells.

## Open items

1. **No input is missing from you.** All your marks are recorded: 21 candidates (5 true, 16 false), 42 of my pairs (40 true, 2 false).
2. **Engineer handoff:** [`engineer-fixes.md`](engineer-fixes.md) (items A1 to D9) is ready to give to the engineer. No source code was changed in this run.
3. **Not done and why:** TLS (section 5, plaintext on loopback), the real "stop the model" test (substituted by a wrong port, you agreed), vLLM (only llama.cpp was reached), query-log observations and lineage, the same-database plan execution (no path existed), the product's own value sampling (section 12), the model server's missing API key (a security gap in your setup, listed above).
4. `TODO.md` was not updated, as you asked.
