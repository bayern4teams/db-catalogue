# Live test report — Diffbot (PostgreSQL) and ZoomInfo (ClickHouse), 2026-10-28

**Product:** db-catalogue, branch `fix/phase7` (all offline tests and local CI green).  
**Model:** `Qwen/Qwen3.6-35B-A3B-FP8`, revision `95a723d08a9490559dae23d0cff1d9466213d989`, served by vLLM on the GPU host.  
**How to run it yourself:** see [GUIDE.md](GUIDE.md) — every CLI command, configuration, secret, tunnel and HTTP API.  
**Rule:** names, counts and model answers only. No data values, credentials or host addresses appear here.

## 1. Summary

| Stage | Diffbot (PostgreSQL 16.15) | ZoomInfo (ClickHouse) | Both together |
|---|---|---|---|
| Connect (encrypted SSH tunnel) | PASS | PASS | — |
| Collect metadata + value-free profiles | PASS, complete | PASS, complete | — |
| Verify bundle signature | PASS | PASS | PASS |
| Build catalogue | PASS | PASS | PASS, complete |
| Render HTML catalogue | `catalog-pg.html` | `catalog-ch.html` | `catalog-both.html` |
| Search index | PASS | PASS | PASS |
| Join paths + read plans (planner) | — | — | PASS |
| 40 questions to the model | — | — | **40/40 returned**: 24 answered, 10 "not in catalogue", 6 refused |

Each database was first tested **separately** (its own bundle, catalogue and index). The questions then ran against **one catalogue holding both databases**, as the owner asked.

## 2. Stages in order, with timings

**Diffbot alone** (`pg-commands.txt`):

- collect pg: exit 0, 7.9 s
- validate pg: exit 0, 0.32 s
- assemble pg: exit 0, 1.34 s
- render pg: exit 0, 1.13 s
- index pg: exit 0, 0.58 s

**ZoomInfo alone** (`ch-commands.txt`):

- collect ch: exit 0, 16.04 s
- validate ch: exit 0, 0.31 s
- assemble ch: exit 0, 1.17 s
- render ch: exit 0, 1.06 s
- index ch: exit 0, 0.55 s

**Both databases** (`both-commands.txt`): assemble exit 0; render exit 0; index exit 0; 40 questions, 6 in parallel.
Answer time per question: min 0.54 s, median 29.21 s, max 65.0 s.

**Planner** (`planner-commands.txt`): build exit 0 (38 reference edges); join-paths from Diffbot `companyexperience` to ZoomInfo `Person`: default = no path (cross-source links with no measured overlap are hidden); with `--include-unverified-cross-source` = 2 tentative paths, flagged `CROSS_SOURCE_CANDIDATES_UNVERIFIED`; `plan` produced a PostgreSQL read, a parameterised ClickHouse read and a logical join, and listed `approved_staging_or_federated_target` and `final_cross_source_join` as unresolved prerequisites (it never executes anything).

## 3. Numbers

| | Diffbot | ZoomInfo | Both |
|---|---|---|---|
| Tables | 13 | 7 | 20 |
| Columns | 124 | 45 | 169 |
| Declared keys/constraints | 0 | 5 | |
| Column profiles / with format profile | 235 / 111 | 79 / 38 | |
| Catalogue nodes | 138 | 53 | 191 |
| Join candidates by evidence tier | 30 verified | 5 plausible | 5 plausible, 3 unverified_cross_source, 30 verified |

All join candidates are `tentative`: they wait for human review before anyone relies on them.

## 4. Findings and changes made today

1. **Query budget raised (owner decision).** With the default `max_queries = 50`, Diffbot's optional format profiles stopped after the budget (78 not read, bundle `partial`). Raised to 200 in `config/postgresql.toml`; the re-run is `complete` with 111 format profiles.
2. **Security guard worked.** The collector refused an unencrypted connection to the remote address; the run used an SSH tunnel. Credentials came only from `.env` and appear in no output file (checked by scanning the run folder).
3. **Cross-source links are conservative.** 3 Diffbot↔ZoomInfo candidates exist (person, location, company experience) but are `unverified_cross_source`: no value overlap was measured, so search, answers and the planner hide them unless explicitly asked.
4. **ZoomInfo uses ReplacingMergeTree** (known, owner-recorded): counts are after de-duplication.
5. **Model refusals.** 4 replies gave an answer with **no citation at all**, so the requester refused them (`MODEL_INVALID_CITATION`, check `none`); every shown answer must cite catalogue items it was given; 2 questions found no matching catalogue item (`NO_CATALOGUE_MATCH`). Refusing is the designed behaviour: no unsupported answer is shown as fact.
6. **The 40 questions were written for the earlier test databases** (run of 2026-10-20). Some refer to tables or columns that these databases do not have, which explains several "not in catalogue" results. **Correctness has not been graded yet**; grading is the owner's step.

## 4a. Spot check of answers (5 of the 24 answered, every 5th; checked against the catalogue collected live today)

| Question | Verdict | Why |
|---|---|---|
| N-D1 fiscal-year column and type | correct | `companyfinancials.year`, integer, matches the catalogue |
| N-D7 investment amount and currency columns | correct | `companyinvestmentround.amount` bigint, `.currency` text |
| N-S3 ClickHouse person → job titles → company names | correct | Person→Experience on `personID`, Experience→Company on `companyID`; both candidates exist, measured overlap stated honestly |
| N-S10 employee category by city (PostgreSQL) | correct | join on `companyid` (verified, ≈97% overlap); also gives the route via `company` |
| N-C6 numeric non-version columns (PostgreSQL) | **incomplete** | named 2 of 4: missed `companyfinancials.amount` and `.year`. Cause: "list all" questions see only the top retrieved items (`retrieval_limit = 10`) |

**Spot check: 4 of 5 correct, 1 incomplete.** Not a full grading.

## 5. Not done

- 3 repeats per question (Phase 7 plan): this run is 1 repeat. `python3 ask.py` repeats it.
- Grading of the answers (owner).
- Measured value overlap between Diffbot and ZoomInfo (needs both collected with the same token key and overlapping columns; the cross-source candidates stay unverified until then).

## 6. Model answers (all 40)

Outcome legend: **answered** — the model answered with citations that were checked against the catalogue; **not in catalogue** — the model checked and the catalogue does not show it; **refused** — the requester rejected the reply or found nothing to retrieve.

### N-D1 — answered (18.98 s)

**Q:** Which PostgreSQL column holds the fiscal year of a company's financial record, and what is its type?

**A:** Based on the provided metadata, the fiscal year is stored in the `year` column of the `companyfinancials` table, and its data type is `integer` (ID: field:e8f2d9d5d4c1fb9279709e7f9b628899).

**Cited:** pg-diffbot.companyfinancials.year

### N-D2 — answered (18.7 s)

**Q:** Which ClickHouse column holds a person's first name, and what is its type?

**A:** The column holding a person's first name is `firstName` (id: field:0d93ada3876599b4cf1c5808e4604bf7) within the `Person` table (id: object:a4e6327622408083a25aa983d7932b29), and its data type is `Nullable(String)`.

**Cited:** ch-zoominfo.Person, ch-zoominfo.Person.firstName

### N-D3 — answered (12.1 s)

**Q:** Which PostgreSQL column holds the latitude of a company location, and what type is it?

**A:** The `latitude` column in the `location` table holds the latitude, and its data type is `text` (field:647a0598a33c8e78cb31387b2f77fde8).

**Cited:** pg-diffbot.location.latitude

### N-D4 — answered (16.64 s)

**Q:** In ClickHouse, which table and column hold the free email addresses of people?

**A:** The provided metadata lists the column `freeMails` in the table `PersonContact` (ID: field:d2e1f81c5c66f67829719dec5b763f70), which corresponds to the requested data.

**Cited:** ch-zoominfo.PersonContact.freeMails

### N-D5 — answered (13.46 s)

**Q:** Which PostgreSQL column stores the name of a technology a company uses?

**A:** The PostgreSQL column that stores the name of a technology a company uses is `technologyname` in the `companytechnographics` table (ID: field:cd6d8fefaead8d3cc55a3b7c2914a041), as directly indicated by its name and object association in the metadata.

**Cited:** pg-diffbot.companytechnographics.technologyname

### N-D6 — not in catalogue (26.6 s)

**Q:** Which ClickHouse columns hold a person's job title in the work-history table?

**A:** The retrieved metadata does not contain a table named "work-history". I checked the object list [object:a4e6327622408083a25aa983d7932b29, object:609d1de1cfc59c40b8dcc6a87949579c] and all field definitions. While fields named "jobTitle" [field:6babb1faf2da76a5a2ea448791657d6c] and "title" [field:39186b739251a3c15c1ffdbcc9ac1267] exist within an object named "Experience", the specific "work-history" table requested is not established in the catalog.

**Cited:** ch-zoominfo.Experience.jobTitle, ch-zoominfo.Experience.title, ch-zoominfo.Person, ch-zoominfo.PersonContact

### N-D7 — answered (19.66 s)

**Q:** Which PostgreSQL columns record the amount and currency of an investment round, and what are their types?

**A:** The `amount` column (`field:3216098d25f7b6efae63b43a2ed26bd0`) records the amount and has data type `bigint`. The `currency` column (`field:ad17f0f9d5b7420315e43f866c7404a7`) records the currency and has data type `text`. Both columns are defined in the `companyinvestmentround` table.

**Cited:** pg-diffbot.companyinvestmentround.amount, pg-diffbot.companyinvestmentround.currency

### N-D8 — answered (30.21 s)

**Q:** Which ClickHouse column holds a company's phone number?

**A:** The catalogue metadata identifies the field named `companyPhone` with ID `field:48c746911a6ebf1a4da4687cc09147a4` (data type: String) under the `Company` table as the column corresponding to the company's phone number.

**Cited:** ch-zoominfo.Company.companyPhone

### N-D9 — refused: MODEL_INVALID_CITATION (25.69 s)

**Q:** Which PostgreSQL column holds the founding date of a company, and is it a date type?

### N-D10 — answered (15.91 s)

**Q:** Which ClickHouse column holds the country code of a location, and what is its type?

**A:** The `CountryCode` column in the `Location` table holds the country code, and its data type is `Nullable(String)` [[field:b89980f871c8e4c3248843d1b0ecf7cd]].

**Cited:** ch-zoominfo.Location.CountryCode

### N-S1 — answered (28.77 s)

**Q:** In PostgreSQL, how do I list the financial amounts per year together with the name of the company?

**A:** To retrieve the financial amounts per year alongside the company name, you should join the `companyfinancials` table with the `company` table using the `companyid` column. The corresponding SQL query is:  ```sql SELECT cf.amount, cf.year, c.entityname FROM companyfinancials cf JOIN company c ON cf.companyid = c.companyid; ```  This leverages the `amount` field (`field:fbb52c18ccf3f448c4c96d9716afeb63`) and `year` field (`field:e8f2d9d5d4c1fb9279709e7f9b628899`) from the `companyfinancials` table, combined with the `entityname` field (`field:1c4e4117566a44ce6db1326be433bfde`) from the `company` table. The join is established via the tentative reference relationship (`edge:9ddb0f122b3ba4b2249e399211323b2e`), which links `companyfinancials` to `company` on matching `companyid` values.

**Cited:** None.companyfinancials -> company (companyid -> companyid), pg-diffbot.company.entityname, pg-diffbot.companyfinancials.amount, pg-diffbot.companyfinancials.year

### N-S2 — answered (47.01 s)

**Q:** In PostgreSQL, how do I get the technologies used by a company together with the company website?

**A:** The metadata shows that the `company` table (ID: `object:2833c9256a0c77503b92e0c12362e9b5`) contains the `website` field (ID: `field:5af50ec19e8ca063c1e82aeafb9c6e61`), and the `companytechnographics` table contains technology attributes `technologyname` (ID: `field:cd6d8fefaead8d3cc55a3b7c2914a041`), `technologytype` (ID: `field:77882268514cb334b645dc9b0b2facdf`), and `categories` (ID: `field:e1f988afe90a63524c7fcfe0525e3dfd`), all linked to the company via `companyid` (ID: `field:4ecb63ee4e15c4f7e54a01d913ac0ec0`).  The reference relationship between these tables is `edge:2100c8546c9d85c20161108676335fcd`, which is tentative. Its evidence is: "name, compatible type, and uniqueness measured from source statistics, not declared; value overlap ≈100% (95% CI 99-100%) of 814 sampled child values".  To get the requested data, you would join these two tables on the `companyid` field and select the `website` and technology columns from their respective records.

**Cited:** None.companytechnographics -> company (companyid -> companyid), pg-diffbot.company, pg-diffbot.company.website, pg-diffbot.companytechnographics.categories, pg-diffbot.companytechnographics.companyid, pg-diffbot.companytechnographics.technologyname, pg-diffbot.companytechnographics.technologytype

### N-S3 — answered (44.9 s)

**Q:** In ClickHouse, how do I get from a person's name to the job titles and the names of the companies they hold them at?

**A:** Based on the metadata, you can retrieve job titles and associated company names by joining the `Person` table with the `Experience` table (referenced by fields like `jobTitle` field:6babb1faf2da76a5a2ea448791657d6c and `title` field:39186b739251a3c15c1ffdbcc9ac1267), and then joining `Experience` with the `Company` table. This path relies on two `tentative` relationship candidates:  1. `Experience` to `Person` (edge:431f6c1b15197e94c7dcf5bbcf6c4f40) linking on `personID`. This is a candidate based on: "name, compatible type, and weak key claim (source sorting or primary key, uniqueness unproven); value overlap at least 24% of 4096 sampled child values (parent only partly read; the true overlap may be much higher)". 2. `Experience` to `Company` (edge:deeb9cc072f3bb7addad6597d5ed9348) linking on `companyID`. This is a candidate based on: "name, compatible type, and weak key claim (source sorting or primary key, uniqueness unproven); value overlap at least 1% of 2719 sampled child values (parent only partly read; the true overlap may be much higher)".  To construct the query, you would join `Person` (object:a4e6327622408083a25aa983d7932b29) to `Experience` on `Person`.`personID` (field:73a5af825cb12c0d0d41110db290023e) = `Experience`.`personID` (field:46b7bf14a9e261713aa38d789704b2f4), and then join `Experience` to `Company` (object:8a3344280cad5c81d6dd7c50f077fc0e) on `Experience`.`companyID` (field:56d3564a908ca3c0b6eacf08dc4f8132) = `Company`.`companyID` (field:881a851556bca80d9712600d2e0116d7). You would filter using `Person`.`name` (field:ca8c7bb88608db84c5a5f16ba6448044) and select `Experience`.`jobTitle` (field:6babb1faf2da76a5a2ea448791657d6c) and `Company`.`companyName` (field:bd38724dab3ee43c1eb9d3e057e2c059).

**Cited:** None.Experience -> Company (companyID -> companyID), None.Experience -> Person (personID -> personID), ch-zoominfo.Company, ch-zoominfo.Company.companyID, ch-zoominfo.Company.companyName, ch-zoominfo.Experience.companyID, ch-zoominfo.Experience.jobTitle, ch-zoominfo.Experience.personID, ch-zoominfo.Person, ch-zoominfo.Person.name, ch-zoominfo.Person.personID

### N-S4 — answered (62.71 s)

**Q:** In ClickHouse, how do I get the free email column for the people who worked at a given company?

**A:** The free email column (`freeMails`, `field:d2e1f81c5c66f67829719dec5b763f70`) belongs to the `PersonContact` object (identified by the field's `object_name`). To retrieve it for individuals linked to a specific company, join the `Company` table (`object:8a3344280cad5c81d6dd7c50f077fc0e`, `companyID` `field:881a851556bca80d9712600d2e0116d7`) to the `Experience` table (`object:bf008576f7f2f9243eb4688ddfb869f1`) via `companyID` (`field:56d3564a908ca3c0b6eacf08dc4f8132`). This connection is documented by `edge:deeb9cc072f3bb7addad6597d5ed9348`. Next, join `Experience` to the `Person` table (`object:a4e6327622408083a25aa983d7932b29`) using `personID` (`field:46b7bf14a9e261713aa38d789704b2f4` to `field:73a5af825cb12c0d0d41110db290023e`), which corresponds to `edge:431f6c1b15197e94c7dcf5bbcf6c4f40`. Finally, join `Person` to `PersonContact` on `personID` (`field:73a5af825cb12c0d0d41110db290023e` to `field:bd76fb3b79997840e6aa87a72d3b9933`), as indicated by `edge:ed4138421af0fdb275805bc7a4e73e66`. All objects and relationships reside in the `ch-zoominfo` source, allowing them to be queried together.

**Cited:** None.Experience -> Company (companyID -> companyID), None.Experience -> Person (personID -> personID), None.PersonContact -> Person (personID -> personID), ch-zoominfo.Company, ch-zoominfo.Company.companyID, ch-zoominfo.Experience, ch-zoominfo.Experience.companyID, ch-zoominfo.Experience.personID, ch-zoominfo.Person, ch-zoominfo.Person.personID, ch-zoominfo.PersonContact.freeMails, ch-zoominfo.PersonContact.personID

### N-S5 — answered (55.34 s)

**Q:** In ClickHouse, how do I list the city of each company location together with the industry names of that company?

**A:** Based on the catalogue metadata, you can retrieve the requested data by joining the `Location` table (`object:c8853922a8207e6aade33d345f499d46`), `Company` table (`object:8a3344280cad5c81d6dd7c50f077fc0e`), the `CompanyIndustry` junction table (`object:0673a3a110275b5f214153dc0ff5303b`), and the `Industry` table (`object:b4b5e63cc449b40de31014afa35b151d`). The `CompanyIndustry` table bridges the relationship between companies and industries, as supported by `edge:1fe055b8f84b76e83d762cefcd905da3` and `edge:e093b912893245d179e95d3af480d8cb`.   Note that `Location`.`companyID` (`field:681e1a2c1473f931ff6aa7b628ffce1e`) is typed as `Nullable(Int64)` while the company identifiers are `String` (`field:881a851556bca80d9712600d2e0116d7`), so a type cast is required for the join condition.  ```sql SELECT      l.`City`,      i.`name` AS industry_name FROM `testzoominfo`.`Location` l JOIN `testzoominfo`.`Company` c      ON CAST(l.`companyID` AS String) = c.`companyID` JOIN `testzoominfo`.`CompanyIndustry` ci      ON c.`companyID` = ci.`companyID` JOIN `testzoominfo`.`Industry` i      ON ci.`industryID` = i.`industryID`; ```

**Cited:** None.CompanyIndustry -> Company (companyID -> companyID), None.CompanyIndustry -> Industry (industryID -> industryID), ch-zoominfo.Company, ch-zoominfo.Company.companyID, ch-zoominfo.CompanyIndustry, ch-zoominfo.CompanyIndustry.companyID, ch-zoominfo.CompanyIndustry.industryID, ch-zoominfo.Industry, ch-zoominfo.Industry.industryID, ch-zoominfo.Industry.name, ch-zoominfo.Location, ch-zoominfo.Location.City, ch-zoominfo.Location.companyID

### N-S6 — answered (56.42 s)

**Q:** In PostgreSQL, how do I get the contact strings of companies together with the industry names of those companies?

**A:** Based on the provided schema, you can retrieve the contact strings and industry names by joining the `company`, `companycontact`, `companyindustry`, and `industryclassification` tables using their shared identifiers. The following PostgreSQL query accomplishes this:  ```sql SELECT cc.contactstring, ic.name FROM company AS c JOIN companycontact AS cc ON c.companyid = cc.companyid JOIN companyindustry AS ci ON c.companyid = ci.companyid JOIN industryclassification AS ic ON ci.classificationid = ic.classificationid; ```  This query relies on the following metadata-defined relationships and fields: - `companycontact` to `company` linkage via `companyid` (`edge:9113b59d1a19c73c1e8c5af31c5a1923`). - `company` to `companyindustry` linkage via `companyid` (`edge:6d7ea6c6a4e2725d7e85f05960bf2893`). - `companyindustry` to `industryclassification` linkage via `classificationid` (`edge:9929d58e5445bf604067eba7cdf583bf`). - The target column `contactstring` is `field:a66618fff5070136ad9eba847994384e` in `companycontact`, and the industry `name` is `field:3136b5b57384f40a684b981c25cefc47` in `industryclassification`.

**Cited:** None.company -> companyindustry (companyid -> companyid), None.companycontact -> company (companyid -> companyid), None.companyindustry -> industryclassification (classificationid -> classificationid), pg-diffbot.company, pg-diffbot.companycontact.contactstring, pg-diffbot.companyindustry, pg-diffbot.industryclassification, pg-diffbot.industryclassification.name

### N-S7 — answered (41.04 s)

**Q:** In PostgreSQL, how do I list the people with a company experience record together with the company name?

**A:** To list people with a company experience record alongside the company name, join the `companyexperience` table with the `person` and `company` tables using the candidate relationships. The link between `companyexperience` and `person` is tentative (edge:bc2f2b20731b2f36778e3880f41eea0a) based on matching `personid` columns, and the link to `company` is tentative (edge:6ecab5e9224f54476962c26684422067) based on matching `companyid` columns. The person's name is in `field:2a77276869de351a9b5c50aa2a424c60` and the company's name is stored in `field:1c4e4117566a44ce6db1326be433bfde` (entityname). The corresponding PostgreSQL query is: ```sql SELECT p.name, c.entityname FROM companyexperience ce JOIN person p ON ce.personid = p.personid JOIN company c ON ce.companyid = c.companyid; ```

**Cited:** None.companyexperience -> company (companyid -> companyid), None.companyexperience -> person (personid -> personid), pg-diffbot.company, pg-diffbot.company.companyid, pg-diffbot.company.entityname, pg-diffbot.companyexperience, pg-diffbot.companyexperience.companyid, pg-diffbot.companyexperience.personid, pg-diffbot.person.name

### N-S8 — refused: MODEL_INVALID_CITATION (48.41 s)

**Q:** In PostgreSQL, how do I get the relation type of each company relation together with the industry names of the company?

### N-S9 — refused: MODEL_INVALID_CITATION (50.93 s)

**Q:** In PostgreSQL, how do I list the investors of each investment round of a company with the company name and round amount?

### N-S10 — answered (42.66 s)

**Q:** In PostgreSQL, how do I get the employee category of companies located in a given city?

**A:** To retrieve the employee category for companies located in a given city, you join the `companyemployeecategory` table with the `location` table on their shared `companyid` field.   Relevant metadata identifiers: - `companyemployeecategory.category` (field:b301300464d36b8466c062335c090aa6) - `location.city` (field:6c5f9f3dfd5b139149b50424d9211ae9) - `companyemployeecategory.companyid` (field:42df2791cfd5f5ae446889372c05c707) and `location.companyid` (field:39799b6dd024b84891bacbbee9868b7f)  The link between these tables is a candidate relationship (status: tentative) with edge ID edge:46d89768b893cb79ab173906fa205a18. Per the metadata, its evidence sentence is: "name, compatible type, and uniqueness measured from source statistics, not declared; value overlap ≈97% (95% CI 95-99%) of 618 sampled child values".  A representative SQL query would be: ```sql SELECT e.category FROM companyemployeecategory e JOIN location l ON e.companyid = l.companyid WHERE l.city = '<given_city>';```  Alternatively, you can join via the `company` table (object:2833c9256a0c77503b92e0c12362e9b5) using edge:1f54c0198f548451ad25ff12e50dfe90 (`companyemployeecategory` -> `company`) and edge:4c9e89d399c9acec959c05d6d3e31e20 (`location` -> `company`), both also marked as tentative candidates based on sampled value overlap.

**Cited:** None.companyemployeecategory -> company (companyid -> companyid), None.companyemployeecategory -> location (companyid -> companyid), None.location -> company (companyid -> companyid), pg-diffbot.company, pg-diffbot.companyemployeecategory.category, pg-diffbot.companyemployeecategory.companyid, pg-diffbot.location.city, pg-diffbot.location.companyid

### N-C1 — answered (27.22 s)

**Q:** List every column in both databases that holds a part of a postal address.

**A:** The retrieved metadata only contains one database/source (`pg-diffbot`), not two. From this single database, the columns that hold a part of a postal address are: `addressfull` (field:a4d9cc672d7ea5e3d66dac59785d9155), `postalcode` (field:b7b0b2bc779d38f82dd214fe7affa249), `city` (field:6c5f9f3dfd5b139149b50424d9211ae9), `country` (field:f349bd12f744491755bce7f8904d4059), `region` (field:adc7b1d78504ee011cb9297a7172dac2), and `street` (field:dc7da0cc329cf2d0156604e270b7198c).

**Cited:** pg-diffbot.location.addressfull, pg-diffbot.location.city, pg-diffbot.location.country, pg-diffbot.location.postalcode, pg-diffbot.location.region, pg-diffbot.location.street

### N-C2 — answered (28.2 s)

**Q:** Which PostgreSQL tables have a confidence column?

**A:** The retrieved metadata identifies eight tables that contain a column named 'confidence': companyrelation [field:089cdcdace556687942010d2fdba8d3e], companyemployeecategory [field:2ab8a8f48929a47196804a433b67596d], companyexperience [field:6e7207ca57249ef069d2969e0807fb93], companyfinancials [field:9dee0d1aa655bc50243a3164527a0bc1], companyinvestmentround [field:a262f4e44de50e57e20cc0612580b3f1], companytechnographics [field:b7c12a4e6cc4b4fcf2daeb717346d71a], companyinvestor [field:dace3b399c7c3d52c014696dcb75c0ec], and companycontact [field:fbbb2fcf55fec2a07103187fac159951].

**Cited:** pg-diffbot.companycontact.confidence, pg-diffbot.companyemployeecategory.confidence, pg-diffbot.companyexperience.confidence, pg-diffbot.companyfinancials.confidence, pg-diffbot.companyinvestmentround.confidence, pg-diffbot.companyinvestor.confidence, pg-diffbot.companyrelation.confidence, pg-diffbot.companytechnographics.confidence

### N-C3 — answered (29.21 s)

**Q:** List every column in the catalogue that holds a person's name or a part of it.

**A:** The following columns hold a person's name or a part of it: - `field:2a77276869de351a9b5c50aa2a424c60` (name) - `field:ca8c7bb88608db84c5a5f16ba6448044` (name) - `field:0d93ada3876599b4cf1c5808e4604bf7` (firstName) - `field:e61adb471f0a3aa7f9b3a15404c0eec8` (lastName) - `field:7f0f389d797d07033ca95dc2fbeb255c` (middleInitial) These are all fields under the `person` and `Person` tables. No other columns in the catalogue match this description.

**Cited:** ch-zoominfo.Person.firstName, ch-zoominfo.Person.lastName, ch-zoominfo.Person.middleInitial, ch-zoominfo.Person.name, pg-diffbot.person.name

### N-C4 — refused: NO_CATALOGUE_MATCH (0.56 s)

**Q:** Which columns in the catalogue have a timestamp or datetime type, and in which tables?

### N-C5 — answered (18.83 s)

**Q:** List every column in both databases that holds a money amount or the currency of one.

**A:** The catalogue metadata identifies four columns that correspond to money amounts or currencies: - `amount` in `companyinvestmentround` (ID: field:3216098d25f7b6efae63b43a2ed26bd0) - `amount` in `companyfinancials` (ID: field:fbb52c18ccf3f448c4c96d9716afeb63) - `currency` in `companyinvestmentround` (ID: field:ad17f0f9d5b7420315e43f866c7404a7) - `currency` in `companyfinancials` (ID: field:bcb08950d6cfa9e26697415c4f40537d) All other listed fields contain dates, timestamps, boolean/text flags, or identifier references, and do not match the requested criteria.

**Cited:** pg-diffbot.companyfinancials.amount, pg-diffbot.companyfinancials.currency, pg-diffbot.companyinvestmentround.amount, pg-diffbot.companyinvestmentround.currency

### N-C6 — answered (27.4 s)

**Q:** Which columns, other than version columns, have a numeric type in PostgreSQL?

**A:** Based on the catalogue metadata, only two columns have numeric types other than version columns: `amount` (type `bigint`, `field:3216098d25f7b6efae63b43a2ed26bd0`) and `totalinvestment` (type `bigint`, `field:78f2638de28a8b4fb67325077a790540`). All other `_version` columns are of type `integer`, while every other listed field uses `text` or `timestamp without time zone`.

**Cited:** pg-diffbot.company._version, pg-diffbot.company.totalinvestment, pg-diffbot.companycontact._version, pg-diffbot.companyexperience._version, pg-diffbot.companyinvestmentround._version, pg-diffbot.companyinvestmentround.amount, pg-diffbot.companyinvestor._version, pg-diffbot.companyrelation._version, pg-diffbot.companytechnographics._version, pg-diffbot.person._version

### N-C7 — refused: NO_CATALOGUE_MATCH (0.54 s)

**Q:** Which ClickHouse columns are not nullable, and what do they have in common?

### N-C8 — answered (65.0 s)

**Q:** List every column in both databases that links people to companies or to person records.

**A:** The following columns serve as linking keys between people, companies, and related records in the provided metadata. Because relationships between objects of two different sources cannot be joined in one query, the links are categorized by source database:  **ch-zoominfo database:** • `personID` (field:73a5af825cb12c0d0d41110db290023e) in the `Person` table • `personID` (field:46b7bf14a9e261713aa38d789704b2f4) in the `Experience` table (links to person via edge:431f6c1b15197e94c7dcf5bbcf6c4f40) • `personID` (field:bd76fb3b79997840e6aa87a72d3b9933) in the `PersonContact` table (links to person via edge:ed4138421af0fdb275805bc7a4e73e66) • `companyID` (field:56d3564a908ca3c0b6eacf08dc4f8132) in the `Experience` table (links to company via edge:deeb9cc072f3bb7addad6597d5ed9348) • `companyID` (field:881a851556bca80d9712600d2e0116d7) in the `Company` table  **pg-diffbot database:** • `personid` (field:60b8cc818da761c732025edc6c2e0ac8) in the `person` table • `personid` (field:c51cb9cd06ba8028acc5fdd2043e8066) in the `companyexperience` table (links to person via edge:bc2f2b20731b2f36778e3880f41eea0a) • `companyid` (field:7c779703f3dc826fb8819d10164c7b75) in the `company` table • `companyid` (field:f46a1d89bdf5ba2141efee79ae75baa5) in the `companyexperience` table (links to company via edge:6ecab5e9224f54476962c26684422067)  These columns explicitly connect person records to company records or act as foreign keys pointing to person records, as evidenced by the `REFERENCE_CANDIDATE` relationships and their column mappings.

**Cited:** None.Experience -> Company (companyID -> companyID), None.Experience -> Person (personID -> personID), None.PersonContact -> Person (personID -> personID), None.companyexperience -> company (companyid -> companyid), None.companyexperience -> person (personid -> personid), ch-zoominfo.Company.companyID, ch-zoominfo.Experience.companyID, ch-zoominfo.Experience.personID, ch-zoominfo.Person.personID, ch-zoominfo.PersonContact.personID, pg-diffbot.company.companyid, pg-diffbot.companyexperience.companyid, pg-diffbot.companyexperience.personid, pg-diffbot.person.personid

### N-C9 — not in catalogue (36.57 s)

**Q:** In every source, which tables hold a role, title or category of a person or employees?

**A:** The retrieved metadata does not show tables with role, title, or category attributes in every source. Only the pg-diffbot source contains the person table (object:a1e7a2729a2091108a4289caefeb1802) with role (field:db4760cb5460ac62e55076674cba020d) and types (field:b315ee1e091efcdf9ece82757566efa1). The ch-zoominfo source tables Person (object:a4e6327622408083a25aa983d7932b29) and PersonContact (object:609d1de1cfc59c40b8dcc6a87949579c) do not list any fields corresponding to role, title, or category.

**Cited:** ch-zoominfo.Person, ch-zoominfo.PersonContact, pg-diffbot.person, pg-diffbot.person.role, pg-diffbot.person.types

### N-C10 — answered (29.55 s)

**Q:** Which tables in both databases describe locations of companies, and which columns carry the company id?

**A:** The catalogue metadata lists "location" in the pg-diffbot database (object:9a29adea11e9a7ddc3e68593e1f5d728) and "Location" in the ch-zoominfo database (object:c8853922a8207e6aade33d345f499d46) as the tables in question. The column carrying the company ID for the pg-diffbot table is "companyid" (field:39799b6dd024b84891bacbbee9868b7f), and the corresponding column for the ch-zoominfo table is "companyID" (field:681e1a2c1473f931ff6aa7b628ffce1e).

**Cited:** ch-zoominfo.Location, ch-zoominfo.Location.companyID, pg-diffbot.location, pg-diffbot.location.companyid

### N-I1 — not in catalogue (47.55 s)

**Q:** Can I match PostgreSQL location rows to ClickHouse Location rows on their location ids?

**A:** The metadata confirms a locationid field (field:8bdc822014a10ef140bd4edcb3baf58a) on the pg-diffbot location table (object:9a29adea11e9a7ddc3e68593e1f5d728) and a locationID field (field:f5e50dbd7deacc0da515af975a739f7e) on the ch-zoominfo Location table (object:c8853922a8207e6aade33d345f499d46). All provided relationships, however, are strictly internal to the pg-diffbot source. There is no cross-source relationship declared between the PostgreSQL location rows and the ClickHouse Location rows. A relationship between objects of two different sources cannot be joined in one query; say so instead of writing one.

### N-I2 — not in catalogue (35.71 s)

**Q:** Is pg.companyinvestor.investorcompanyid a reference to pg.company.companyid?

**A:** The retrieved metadata does not establish a relationship between pg.companyinvestor.investorcompanyid and pg.company.companyid. I checked the field definitions (field:cb0d88781269443b159d5d9aae9138c2, field:7c779703f3dc826fb8819d10164c7b75), the table objects (object:254cbf926e27dd236adf19bc39197774, object:2833c9256a0c77503b92e0c12362e9b5), and the full list of candidate/reference relationships. None of the relationships link investorcompanyid to companyid, nor is there any declared or tentative reference between the companyinvestor and company tables.

**Cited:** None.CompanyIndustry -> Company (companyID -> companyID), None.company -> companyindustry (companyid -> companyid), None.company -> location (companyid -> companyid), None.companycontact -> company (companyid -> companyid), None.companycontact -> companyindustry (companyid -> companyid), None.companycontact -> location (companyid -> companyid), None.companyemployeecategory -> company (companyid -> companyid), None.companyemployeecategory -> companyindustry (companyid -> companyid), None.companyemployeecategory -> location (companyid -> companyid), None.companyexperience -> company (companyid -> companyid), None.companyfinancials -> company (companyid -> companyid), None.companyindustry -> company (companyid -> companyid), None.companyindustry -> industryclassification (classificationid -> classificationid), None.companyinvestmentround -> company (companyid -> companyid), None.companyinvestmentround -> companyindustry (companyid -> companyid), None.companyinvestmentround -> location (companyid -> companyid), None.companyinvestor -> companyinvestmentround (investmentid -> investmentid), None.companytechnographics -> company (companyid -> companyid), None.companytechnographics -> companyindustry (companyid -> companyid), None.companytechnographics -> location (companyid -> companyid), None.location -> company (companyid -> companyid), None.location -> companyindustry (companyid -> companyid), pg-diffbot.company, pg-diffbot.company.companyid, pg-diffbot.companyinvestor, pg-diffbot.companyinvestor.investorcompanyid

### N-I3 — refused: MODEL_INVALID_CITATION (31.27 s)

**Q:** Can I find the ClickHouse person for a PostgreSQL company experience record via personid?

### N-I4 — not in catalogue (27.13 s)

**Q:** Between ch.Experience.jobTitle and ch.Experience.title, which should I use for a person's job title?

**A:** The retrieved metadata only provides structural definitions for both fields under the `Experience` table, indicating they are both `Nullable(String)` types. It does not include descriptions, data dictionaries, sample values, or documentation that distinguish their semantic meanings. Without this information, I cannot verify which field is intended to store a person's job title.

**Cited:** ch-zoominfo.Experience, ch-zoominfo.Experience.jobTitle, ch-zoominfo.Experience.title

### N-I5 — not in catalogue (26.89 s)

**Q:** Between pg.company.entityname, fullname and description, which column holds the company's name?

**A:** The metadata provides only field names, data types, and parent object associations for these columns, with no descriptions or evidence indicating their semantic content. Therefore, it cannot be determined from the given information which column holds the company's name.

**Cited:** pg-diffbot.company, pg-diffbot.company.description, pg-diffbot.company.entityname, pg-diffbot.company.fullname

### N-I6 — answered (32.03 s)

**Q:** In the pg.company and pg.location relationship on companyid, which table is the parent?

**A:** The relationship is marked as tentative. According to edge:4c9e89d399c9acec959c05d6d3e31e20, location references company on companyid, making company (object:2833c9256a0c77503b92e0c12362e9b5) the parent table. The evidence sentence is: "name, compatible type, and uniqueness measured from source statistics, not declared; value overlap ≈100% (95% CI 99-100%) of 3506 sampled child values".

**Cited:** None.company -> location (companyid -> companyid), None.location -> company (companyid -> companyid), pg-diffbot.company, pg-diffbot.location

### N-I7 — not in catalogue (25.63 s)

**Q:** Does the catalogue tell me which company is the parent and which is the subsidiary in pg.companyrelation?

**A:** The catalogue does not establish which company is the parent and which is the subsidiary in `companyrelation`. I examined the `companyrelation` table (ID: object:252499265eb26718f6599a6013fa9743) and its fields, specifically `companyid` (ID: field:edfeb47751f5e0a4b6fd7354a7cc1740), `relatedcompanyid` (ID: field:a3d1706bb03a932604f500760fbb9d8f), and `relationtype` (ID: field:45fbcb5185e96409c90173a0ab478e94), as well as the reference relationships pointing to and from it. The metadata only provides field names and data types without descriptions, enumerated values, or directional semantics that would define or distinguish parent versus subsidiary roles.

**Cited:** None.companyrelation -> company (companyid -> companyid), pg-diffbot.companyrelation, pg-diffbot.companyrelation.companyid, pg-diffbot.companyrelation.relatedcompanyid, pg-diffbot.companyrelation.relationtype

### N-I8 — not in catalogue (27.2 s)

**Q:** Which PostgreSQL column holds a person's email address?

**A:** The metadata for the person table (object:a1e7a2729a2091108a4289caefeb1802) lists the fields personid (field:60b8cc818da761c732025edc6c2e0ac8), name (field:2a77276869de351a9b5c50aa2a424c60), role (field:db4760cb5460ac62e55076674cba020d), summary (field:5f701f1cc336bb77dee28742120d8785), confidence (field:b9c7b9e22827aa912fa78e8145c14aeb), created_at (field:fc18a75d6c021007522abde55f860899), types (field:b315ee1e091efcdf9ece82757566efa1), and _version (field:1e6b53e2368060ad62901ac7635e044e). No column for an email address is present in this table or any other listed object, so the requested column cannot be identified from the provided metadata.

**Cited:** pg-diffbot.person, pg-diffbot.person._version, pg-diffbot.person.confidence, pg-diffbot.person.created_at, pg-diffbot.person.name, pg-diffbot.person.personid, pg-diffbot.person.role, pg-diffbot.person.summary, pg-diffbot.person.types

### N-I9 — not in catalogue (32.41 s)

**Q:** Can I tell which ClickHouse Location row is a company's current location?

**A:** The retrieved metadata does not show how to identify a company's current location. I checked all fields in the Location table (object:c8853922a8207e6aade33d345f499d46) including locationID, _version, companyID, and address components, as well as all Company table fields (object:8a3344280cad5c81d6dd7c50f077fc0e). No field contains a 'current', 'active', or status flag for locations. Furthermore, the only relationships provided in the metadata (edge:1fe055b8f84b76e83d762cefcd905da3, edge:deeb9cc072f3bb7addad6597d5ed9348) connect CompanyIndustry and Experience to Company, not Location. Without a designated current-location field or a relationship with a validity/timestamp column, this cannot be established from the given metadata.

**Cited:** None.CompanyIndustry -> Company (companyID -> companyID), None.Experience -> Company (companyID -> companyID), ch-zoominfo.Company, ch-zoominfo.Company._version, ch-zoominfo.Company.companyDescription, ch-zoominfo.Company.companyDomain, ch-zoominfo.Company.companyEmployeeCountRange, ch-zoominfo.Company.companyEmployees, ch-zoominfo.Company.companyID, ch-zoominfo.Company.companyName, ch-zoominfo.Company.companyPhone, ch-zoominfo.Company.companyRevenue, ch-zoominfo.Company.companyRevenueIn000s, ch-zoominfo.Company.companyRevenueRange, ch-zoominfo.Company.companyTicker, ch-zoominfo.Company.companyType, ch-zoominfo.Company.website, ch-zoominfo.Location, ch-zoominfo.Location.City, ch-zoominfo.Location.CountryCode, ch-zoominfo.Location.State, ch-zoominfo.Location.Street, ch-zoominfo.Location.Zip, ch-zoominfo.Location._version, ch-zoominfo.Location.companyID, ch-zoominfo.Location.locationID

### N-I10 — not in catalogue (40.23 s)

**Q:** In ClickHouse, how can I find the company name for a row in PersonContact?

**A:** The retrieved metadata does not show a direct field or defined join path from PersonContact to Company to retrieve the company name. PersonContact (object:609d1de1cfc59c40b8dcc6a87949579c) contains only personID (field:bd76fb3b79997840e6aa87a72d3b9933), _version (field:a66edea1b9a64eea3f4fa16650d9d506), and freeMails (field:d2e1f81c5c66f67829719dec5b763f70). It has a tentative reference relationship to Person (edge:ed4138421af0fdb275805bc7a4e73e66) on personID, with evidence stating 'name, compatible type, and weak key claim (source sorting or primary key, uniqueness unproven); value overlap at least 5 of 4038 sampled child values'. The Company table (object:8a3344280cad5c81d6dd7c50f077fc0e) contains the companyName field (field:bd38724dab3ee43c1eb9d3e057e2c059). Although the Experience table (object:bf008576f7f2f9243eb4688ddfb869f1) offers tentative links to Person (edge:431f6c1b15197e94c7dcf5bbcf6c4f40) and Company (edge:deeb9cc072f3bb7addad6597d5ed9348) via personID and companyID, no explicit join path connecting PersonContact to Company is provided in the metadata. Therefore, the requested lookup method cannot be established.

**Cited:** None.Experience -> Company (companyID -> companyID), None.Experience -> Person (personID -> personID), None.PersonContact -> Person (personID -> personID), ch-zoominfo.Company, ch-zoominfo.Company.companyName, ch-zoominfo.Experience, ch-zoominfo.PersonContact, ch-zoominfo.PersonContact.personID

## 7. Catalogue output (both databases)

Full interactive page: `catalog-both.html`. Text form below (names and types only).

### Tables per source

| Source | Table | Columns | Column names (type) |
|---|---|---|---|
| ch-zoominfo | Company | 14 | _version (DateTime), companyDescription (String), companyDomain (String), companyEmployeeCountRange (String), companyEmployees (Int64), companyID (String), companyName (String), companyPhone (String), companyRevenue (String), companyRevenueIn000s (Int64), companyRevenueRange (String), companyTicker (String), companyType (String), website (String) |
| ch-zoominfo | CompanyIndustry | 4 | _version (DateTime), companyID (String), industryID (String), isPrimary (String) |
| ch-zoominfo | Experience | 6 | _version (DateTime), companyID (String), jobTitle (Nullable(String)), personHashtags (Nullable(String)), personID (String), title (Nullable(String)) |
| ch-zoominfo | Industry | 4 | _version (DateTime), displayName (Nullable(String)), industryID (String), name (Nullable(String)) |
| ch-zoominfo | Location | 8 | City (Nullable(String)), CountryCode (Nullable(String)), State (Nullable(String)), Street (Nullable(String)), Zip (Nullable(String)), _version (DateTime), companyID (Nullable(Int64)), locationID (String) |
| ch-zoominfo | Person | 6 | _version (DateTime), firstName (Nullable(String)), lastName (Nullable(String)), middleInitial (Nullable(String)), name (Nullable(String)), personID (String) |
| ch-zoominfo | PersonContact | 3 | _version (DateTime), freeMails (String), personID (Nullable(String)) |
| pg-diffbot | company | 25 | _version (integer), alldescriptions (text), ceoname (text), companyid (text), created_at (timestamp without time zone), description (text), descriptors (text), entityname (text), explicit_origins (text), foundername (text), foundingdate (text), fullname (text), isacquired (text), isdissolved (text), isnonprofit (text), ispublic (text), logo (text), metadata_confidence (text), motto (text), sourcecategory (text), summary (text), totalinvestment (bigint), totalinvestmentcurrency (text), types (text), website (text) |
| pg-diffbot | companycontact | 7 | _version (integer), companyid (text), confidence (text), contactid (text), contactstring (text), contacttype (text), created_at (timestamp without time zone) |
| pg-diffbot | companyemployeecategory | 7 | _version (integer), category (text), companyid (text), confidence (text), created_at (timestamp without time zone), empcatid (text), firsthiredate (text) |
| pg-diffbot | companyexperience | 8 | _version (integer), companyid (text), confidence (text), created_at (timestamp without time zone), personid (text), personname (text), role (text), types (text) |
| pg-diffbot | companyfinancials | 9 | _version (integer), amount (bigint), companyid (text), confidence (text), created_at (timestamp without time zone), currency (text), financialid (text), iscurrent (text), year (integer) |
| pg-diffbot | companyindustry | 4 | _version (integer), classificationid (text), companyid (text), created_at (timestamp without time zone) |
| pg-diffbot | companyinvestmentround | 9 | _version (integer), amount (bigint), companyid (text), confidence (text), created_at (timestamp without time zone), currency (text), date (text), explicit_origin (text), investmentid (text) |
| pg-diffbot | companyinvestor | 8 | _version (integer), confidence (text), created_at (timestamp without time zone), investmentid (text), investorcompanyid (text), summary (text), surfaceform (text), types (text) |
| pg-diffbot | companyrelation | 10 | _version (integer), companyid (text), confidence (text), created_at (timestamp without time zone), explicit_origin (text), relatedcompanyid (text), relationtype (text), summary (text), surfaceform (text), types (text) |
| pg-diffbot | companytechnographics | 8 | _version (integer), categories (text), companyid (text), confidence (text), created_at (timestamp without time zone), techid (text), technologyname (text), technologytype (text) |
| pg-diffbot | industryclassification | 6 | _version (integer), classificationid (text), confidence (text), created_at (timestamp without time zone), isprimary (text), name (text) |
| pg-diffbot | location | 15 | _version (integer), addressfull (text), city (text), companyid (text), country (text), created_at (timestamp without time zone), iscurrent (text), isprimary (text), latitude (text), locationid (text), longitude (text), postalcode (text), precision (text), region (text), street (text) |
| pg-diffbot | person | 8 | _version (integer), confidence (text), created_at (timestamp without time zone), name (text), personid (text), role (text), summary (text), types (text) |

### Join candidates (all `tentative`, awaiting human review)

| Tier | From | To | Columns |
|---|---|---|---|
| plausible | ch-zoominfo.CompanyIndustry | ch-zoominfo.Company | companyID = companyID |
| plausible | ch-zoominfo.CompanyIndustry | ch-zoominfo.Industry | industryID = industryID |
| plausible | ch-zoominfo.Experience | ch-zoominfo.Person | personID = personID |
| plausible | ch-zoominfo.Experience | ch-zoominfo.Company | companyID = companyID |
| plausible | ch-zoominfo.PersonContact | ch-zoominfo.Person | personID = personID |
| unverified_cross_source | pg-diffbot.companyexperience | ch-zoominfo.Person | personid = personID |
| unverified_cross_source | pg-diffbot.location | ch-zoominfo.Location | locationid = locationID |
| unverified_cross_source | pg-diffbot.person | ch-zoominfo.Person | personid = personID |
| verified | pg-diffbot.company | pg-diffbot.companyindustry | companyid = companyid |
| verified | pg-diffbot.company | pg-diffbot.location | companyid = companyid |
| verified | pg-diffbot.companycontact | pg-diffbot.company | companyid = companyid |
| verified | pg-diffbot.companycontact | pg-diffbot.location | companyid = companyid |
| verified | pg-diffbot.companycontact | pg-diffbot.companyindustry | companyid = companyid |
| verified | pg-diffbot.companyemployeecategory | pg-diffbot.company | companyid = companyid |
| verified | pg-diffbot.companyemployeecategory | pg-diffbot.location | companyid = companyid |
| verified | pg-diffbot.companyemployeecategory | pg-diffbot.companyindustry | companyid = companyid |
| verified | pg-diffbot.companyexperience | pg-diffbot.company | companyid = companyid |
| verified | pg-diffbot.companyexperience | pg-diffbot.person | personid = personid |
| verified | pg-diffbot.companyexperience | pg-diffbot.companyindustry | companyid = companyid |
| verified | pg-diffbot.companyexperience | pg-diffbot.location | companyid = companyid |
| verified | pg-diffbot.companyfinancials | pg-diffbot.location | companyid = companyid |
| verified | pg-diffbot.companyfinancials | pg-diffbot.company | companyid = companyid |
| verified | pg-diffbot.companyfinancials | pg-diffbot.companyindustry | companyid = companyid |
| verified | pg-diffbot.companyindustry | pg-diffbot.industryclassification | classificationid = classificationid |
| verified | pg-diffbot.companyindustry | pg-diffbot.location | companyid = companyid |
| verified | pg-diffbot.companyindustry | pg-diffbot.company | companyid = companyid |
| verified | pg-diffbot.companyinvestmentround | pg-diffbot.companyindustry | companyid = companyid |
| verified | pg-diffbot.companyinvestmentround | pg-diffbot.company | companyid = companyid |
| verified | pg-diffbot.companyinvestmentround | pg-diffbot.location | companyid = companyid |
| verified | pg-diffbot.companyinvestor | pg-diffbot.companyinvestmentround | investmentid = investmentid |
| verified | pg-diffbot.companyrelation | pg-diffbot.company | companyid = companyid |
| verified | pg-diffbot.companyrelation | pg-diffbot.companyindustry | companyid = companyid |
| verified | pg-diffbot.companyrelation | pg-diffbot.location | companyid = companyid |
| verified | pg-diffbot.companytechnographics | pg-diffbot.company | companyid = companyid |
| verified | pg-diffbot.companytechnographics | pg-diffbot.companyindustry | companyid = companyid |
| verified | pg-diffbot.companytechnographics | pg-diffbot.location | companyid = companyid |
| verified | pg-diffbot.location | pg-diffbot.companyindustry | companyid = companyid |
| verified | pg-diffbot.location | pg-diffbot.company | companyid = companyid |

## 8. Files in this folder

| File | What |
|---|---|
| `GUIDE.md` | how to run everything, all APIs |
| `config/` | all configuration used |
| `run.sh`, `both.sh`, `ask.py` | the exact scripts run |
| `*-commands.txt` | command logs with exit codes and timings |
| `catalog-pg.html`, `catalog-ch.html`, `catalog-both.html` | catalogue pages |
| `answers-both.json` | raw model output for the 40 questions |
| `bundle-*`, `snapshot-*`, `planner-graph/` | machine-readable artifacts (`tokens.jsonl`, `ranges.jsonl` stay local) |
