# Results of the 20 harder questions (complete)

Linked from [`live-test-report.md`](live-test-report.md) section 10. Questions and expected answers: [`model-questions.md`](model-questions.md). Raw data: [`run-20261006-035124/model-questions-ask.json`](run-20261006-035124/model-questions-ask.json) (the app's `ask`) and [`model-questions-diag.json`](run-20261006-035124/model-questions-diag.json) (retrieval recall and, from the second run on 2026-10-12, the model's plain-text answers with their grading inputs). [`model-questions-diag-no-model.json`](run-20261006-035124/model-questions-diag-no-model.json) is the first diagnostic attempt, when the SSH tunnel had dropped; [`model-questions-ask-tunnel-down-run.json`](run-20261006-035124/model-questions-ask-tunnel-down-run.json) is the first `ask` run, in the same state.

## Part 1. `dbig-requester ask` as the app works (2026-10-12)

| Result | Questions |
|---|---|
| `MODEL_UNAVAILABLE_OR_INVALID` (17) | D1 D2 D3 D4, S1 S2 S3 S4 S5, C1 C2 C4 C5, I1 I2 I3 I4 |
| `NO_CATALOGUE_MATCH` (3, wrong: the catalogue does contain the answer) | D5 (names of industries), C3 (ClickHouse tables), I5 (relationships between companies) |
| Right / partial / wrong answer | 0 / 0 / 0 |

Times: 0.47 s for `NO_CATALOGUE_MATCH` (no model call); 0.69 s to 0.9 s for the other 17. The 17 are the schema rejection already found (`maxLength: 2000`, engineer item B1); their time is about 0.25 s above the no-model baseline, which fits a request reaching the server and being rejected. I will re-run this part when the tunnel is back, to be certain it was up.

Why the three got `NO_CATALOGUE_MATCH` (tested with the app's search, no model): search has no word stemming and no vocabulary for sources.
- D5: `names`, `industries`, `stored` match nothing. `industry` matches 49 entries; `industries` matches 0.
- C3: `ClickHouse`, `database`, `contain`, `tables` match nothing. `table` matches 13 entries; `tables` matches 0; a source name is not searchable.
- I5: `relationships`, `companies`, `subsidiaries`, `partners` match nothing. `company` matches 50; `companies` 0.
The app refuses to call the model when nothing matches, so a plural or a source name makes a perfectly answerable question fail.

### `ask` re-run with the tunnel up (2026-10-12)

Result codes were identical (17 `MODEL_UNAVAILABLE_OR_INVALID`, 3 `NO_CATALOGUE_MATCH`: D5, C3, I5). Times varied from 0.8 s to 61 s because the model server was in use by someone else: D1 2.0, D2 0.9, D3 0.8, D4 4.1, S1 3.7, S2 9.0, S3 21.4, S4 10.9, S5 0.8, C1 60.9, C2 61.0, C4 45.0, C5 0.8, I1 35.5, I2 0.9, I3 12.7, I4 0.9 seconds. C1 and C2 hit the 60 s `timeout_seconds` (queueing); for the others the server's `400 failed to parse grammar` is the most likely cause (a replay with dummy data took 19 s to be rejected, with the same message). The app cannot tell these apart, because every case returns the same code (engineer item C1, D7). The earlier all-0.7 s run was made while the tunnel was down, so its times were not meaningful.

## Part 2. Retrieval: do the expected catalogue entries reach the model?

The app gives the model only the top 10 search hits (`retrieval_limit = 10`). For each question the expected entries (tables/columns from [`model-questions.md`](model-questions.md)) were checked against the top 10, and against the top 30 for comparison. This does not involve the model.

| Category | Expected entries in the top 10 | In the top 30 |
|---|---|---|
| Direct (D1-D5) | 2 of 8 | 2 of 8 |
| Steps (S1-S5) | 8 of 23 | 12 of 23 |
| Comprehensive (C1-C5) | 6 of 28 | 6 of 28 |
| Inference (I1-I5) | 2 of 13 | 4 of 13 |
| **All** | **18 of 72 (25%)** | **24 of 72 (33%)** |

| Question | Top 10 | Top 30 | Not retrieved even at 30 |
|---|---|---|---|
| D1 | 1/1 | 1/1 | |
| D2 ticker | 0/1 | 0/1 | `Company.companyTicker` (camelCase, B3) |
| D3 | 1/1 | 1/1 | |
| D4 revenue | 0/2 | 0/2 | `companyRevenue`, `companyRevenueIn000s` (camelCase) |
| D5 | 0 hits | 0 hits | everything (plural) |
| S1 | 0/5 | 0/5 | all five; the question's words hit other columns first |
| S2 | 2/5 | 3/5 | `companyexperience.companyid`, `company.companyid` |
| S3 | 2/5 | 2/5 | `Company.companyID`, `CompanyIndustry.companyID`, `Industry.name` |
| S4 | 1/4 | 3/4 | `zoominfo_Experience.personID` |
| S5 | 3/4 | 4/4 | |
| C1 | 4/4 | 4/4 | |
| C2 | 1 hit, 0/7 | 1 hit, 0/7 | all seven tables |
| C3 | 0 hits | 0 hits | all seven ClickHouse tables |
| C4 | 2/6 | 2/6 | `person`, `Person`, `PersonContact`, `zoominfo_staff` |
| C5 | 0/4 | 0/4 | all four company key columns |
| I1 | 1/2 | 2/2 | |
| I2 | 0/6 | 0/6 | all six ClickHouse tables |
| I3 | 1/2 | 2/2 | |
| I4 | 0/2 | 0/2 | `companyEmployees`, `companyEmployeeCountRange` (camelCase) |
| I5 | 0 hits | 0 hits | `companyrelation` (plural) |

Reading: for most questions the model could not answer correctly even with a perfect model, because the entries it would need never reach it. Raising the limit from 10 to 30 helps the steps questions (8 to 12 of 23) and little else; the misses are caused by how words are matched (camelCase, plurals, source names), not by the count.

## Part 3. Plain-text diagnostic outside the app (2026-10-12)

What it is: for each question, the context the app would give the model (the top 10 search hits, and separately the top 30) was sent to `Qwen3.8-Flash-Next` as a normal chat request, with the app's own system prompt plus "reply in plain text, IDs in square brackets", and no JSON schema. `max_tokens` 12000. This is not what the app does; it shows what the model can do with what retrieval gives it. 40 requests, 0 errors, every one finished normally (`finish_reason: stop`). Time per request 2.1 s to 101 s (shared server); up to 9294 completion tokens (the model reasons at length). The context was catalogue metadata only (names, types, namespaces).

Grading rubric (written before reading the answers): **Right** = names the expected tables/columns and states nothing false. **Partial** = some of it right, or right with a missing piece or an extra false claim. **Wrong** = false, from the wrong source, or unreadable. **Gap** = the model says the supplied entries do not contain the answer, and they indeed do not (retrieval failure; the model behaved correctly).

| Q | Top 10 | Top 30 | What happened |
|---|---|---|---|
| D1 | Right | Right | `person.role` |
| D2 | Gap | Gap | `companyTicker` was never retrieved; the model said no ticker field exists in what it was given |
| D3 | Right | Right | `postalcode` (top 10 names only the column) |
| D4 | Gap | **Wrong** | top 30: listed `db_schema.Company.columns.companyRevenue` etc. (MongoDB schema-description noise, type "dict"), not the ClickHouse columns; it did say the metadata does not identify them as ClickHouse columns |
| D5 | Gap | Gap | zero hits (plural), model got an empty context |
| S1 | **Wrong** | Gap | top 10: answered a PostgreSQL question with ClickHouse tables (`CompanyIndustry`, `Industry`); top 30: only `industryclassification` retrieved |
| S2 | Gap | Partial | top 30: `person.name`, `personid`, `companyexperience.personid`; said no company column was listed (`companyexperience.companyid` not retrieved) |
| S3 | Partial | Partial | right join `CompanyIndustry.industryID` to `Industry.industryID`; said the name column was not listed (`Industry.name` not retrieved) |
| S4 | Gap | Gap | would not guess the staff-to-company link; offered `main_entity_id`, flagged as unconfirmed |
| S5 | Partial | Right | top 10: correct join on `investmentid`, plus the false claim `company.companyid` to `companyinvestor.investorcompanyid` (a relationship you marked false, guessed from names); top 30: correct |
| C1 | Right | Partial | top 10: the four correct columns; top 30: seven bare IDs, four right and three unexplained (schema-description noise) |
| C2 | Gap | Gap | only one irrelevant hit retrieved |
| C3 | Gap | Gap | zero hits |
| C4 | Partial | Partial | `companycontact` and `zoominfo_contact_information` found; both `person` tables, `PersonContact` and `zoominfo_staff` missed |
| C5 | **Wrong** | **Wrong** | top 10: five bare IDs, unreadable; top 30: listed `db_schema.*.columns.companyID` (MongoDB schema-description noise), not the real key columns |
| I1 | Right | **Wrong** | the trap question. Top 10: "I can't confirm a direct join" (right, by luck: the ClickHouse side was not retrieved). Top 30: began "Yes", both ids are strings and no relationship is shown; your measurement is 0 matches, so "Yes" is wrong, though it added "no explicit cross-source join relationship is shown" |
| I2 | Gap | Gap | said it cannot determine the correspondence; no ClickHouse table was retrieved |
| I3 | Right | Right | "Not confirmed ... does not define it as a reference/FK": the ideal answer |
| I4 | Gap | Gap | no employee column retrieved (`companyEmployees` is camelCase) |
| I5 | Gap | Gap | zero hits (plural) |

Tally (20 questions each):

| | Right | Partial | Wrong | Gap (retrieval failure) |
|---|---|---|---|---|
| Top 10 (what the app gives the model) | 5 (D1, D3, C1, I1, I3) | 3 (S3, S5, C4) | 2 (S1, C5) | 10 |
| Top 30 | 4 (D1, D3, S5, I3) | 4 (S2, S3, C1, C4) | 3 (D4, C5, I1) | 9 |

By category (top 10): direct 2 right, 3 gap; steps 0 right, 2 partial, 1 wrong, 2 gap; comprehensive 1 right, 1 partial, 1 wrong, 2 gap; inference 2 right, 3 gap.

What this shows:
1. **The model is not the main problem.** Of 20 questions at the app's 10 hits, half are Gaps: the model correctly says the catalogue entries it was given do not contain the answer, because retrieval did not fetch them (camelCase, plurals, source names, generic words, MongoDB schema-description noise). It never invented a table or column. Its two false statements came from the context: the wrong-source answer (S1) and a name-guessed relationship (S5).
2. **More context is not the fix.** Going from 10 to 30 hits changed the tally little (right 5 to 4, wrong 2 to 3) and made some answers worse, because the extra hits are mostly MongoDB schema-description noise.
3. **The trap question was only passed by luck.** I1 (can the company ids be joined?) came out right at 10 hits only because the ClickHouse entry was not retrieved; at 30 hits the model said "Yes". The catalogue has nothing to tell the model that the two key columns have no common values.
4. **Relationships are missing from the model's context.** Steps questions (S1 to S5) need the links between tables, which the app does not pass. The only complete steps answer (S5 at 30) was the one where the table names matched.
5. **The app's own rules would reject two kinds of honest answers.** S4 at 30 hits says the catalogue "does not specify an explicit join or lineage"; the app rejects any answer containing the word `lineage` ([`answer.py`](../packages/requester/src/dbig_requester/answer.py#L62) lines 62 and 131-132). And the app (correctly) would not have called the model for the six zero-hit questions (D5, C3, I5 at both limits); those answers carry no cited ID.
6. **Cost.** Reasoning models need many tokens here: 148 to 9294 per answer, 2 s to 101 s on a shared server. The app's default `max_tokens` of 512 would cut most of them off (engineer item B2).

The expected fixes are in [`engineer-fixes.md`](engineer-fixes.md), items B1 to B5, D5, D6, D7 and D8.
