# Harder questions for `dbig-requester ask` (DRAFT for your approval; nothing has been asked yet)

Linked from [`live-test-report.md`](live-test-report.md) (section 10). Expected answers use only the catalogue's names and types and the relationships you confirmed. How I will grade each answer: **right** (names the expected tables/columns and cites IDs that exist), **partial**, **wrong**, **refused** or **error**. The ten questions already run (all `MODEL_UNAVAILABLE_OR_INVALID`) stay in the report as category 0 "easy".

What the requester gives the model, from [`packages/requester/src/dbig_requester/answer.py`](../packages/requester/src/dbig_requester/answer.py#L67-L89) lines 67-89: only the top `retrieval_limit` (default 10) search hits, each with `id`, `name`, `kind`, `source_id`, `data_type`, `object_name`, `namespace` and `description`. It gives no relationships, no keys, no statistics, and no table summaries. A question that contains `lineage`, `derived from`, `flows into` or `writes to` is refused before search (`LINEAGE_REQUIRES_PLANNER`), and an answer with no citations is rejected. That shapes what the harder categories can reach, and each category below says what I expect to fail and why.

## 1. Direct answer (one catalogue entry is enough)

| # | Question | Expected answer |
|---|---|---|
| D1 | Which column holds a person's role in PostgreSQL? | `person.role` (pg) |
| D2 | Where is a company's ticker symbol stored, and what type is it? | `Company.companyTicker`, `Nullable(String)` (ch) |
| D3 | Which PostgreSQL column stores the postal code of a location? | `location.postalcode` |
| D4 | Which ClickHouse columns hold company revenue, and what are their types? | `Company.companyRevenue` (`Nullable(String)`), `Company.companyRevenueIn000s` (`Nullable(Int64)`), also `companyRevenueRange` and `companyRevenue_currency` |
| D5 | Where are the names of industries stored? | pg `industryclassification.name`, ch `Industry.name`, mongo `zoominfo_industry.name` |

Expected problem: D4 and D5 name camelCase columns (`companyRevenue`), which search cannot find by a part of the name (engineer item B3).

## 2. With steps (several tables must be chained)

| # | Question | Expected answer |
|---|---|---|
| S1 | In PostgreSQL, which tables and columns do I use to get the industry names of a company? | `company.companyid` to `companyindustry.companyid`; `companyindustry.classificationid` to `industryclassification.classificationid`; name in `industryclassification.name` (both links are among the relationships you confirmed but the app did not propose) |
| S2 | In PostgreSQL, how do I get from a person's name to the companies that person worked at? | `person.name`; `person.personid` to `companyexperience.personid`; `companyexperience.companyid` to `company.companyid` |
| S3 | In ClickHouse, how do I get the industry names for a company? | `Company.companyID` to `CompanyIndustry.companyID`; `CompanyIndustry.industryID` to `Industry.industryID`; name in `Industry.name` |
| S4 | In the MongoDB zoominfo collections, how do I get from a staff member to the company? | `zoominfo_staff.personID` to `zoominfo_Experience.personID`; `zoominfo_Experience.companyID` to `zoominfo_Company.companyID` |
| S5 | In PostgreSQL, which tables do I join to list the investment rounds of a company together with the investor rows? | `company.companyid` to `companyinvestmentround.companyid`; `companyinvestmentround.investmentid` to `companyinvestor.investmentid` |

Expected problem: the model only sees the retrieved entries, never the relationships, and the app's own relationship list lacks these links, so it can only guess from names (engineer item B6).

## 3. Comprehensive (survey many entries and sources)

| # | Question | Expected answer |
|---|---|---|
| C1 | List every place in the catalogue where a company's website is stored. | pg `company.website`; ch `Company.website` (and `companyDomain`); mongo `zoominfo_Company.website`, `diffbot_Company.website` |
| C2 | Which tables in all three databases describe industries, and how do they relate to companies? | pg `industryclassification`, `companyindustry`; ch `Industry`, `CompanyIndustry`; mongo `zoominfo_industry`, `zoominfo_CompanyIndustry`, `diffbot_CompanyIndustry` |
| C3 | What does the ClickHouse database contain? List its tables. | `Company`, `CompanyIndustry`, `Experience`, `Industry`, `Location`, `Person`, `PersonContact` |
| C4 | In every source, which tables hold personal contact or identity details of people? | pg `person`, `companycontact`; ch `Person`, `PersonContact`; mongo `zoominfo_contact_information`, `zoominfo_staff` |
| C5 | Which columns identify a company across the catalogue? | `companyid` / `companyID` / `company_id` columns in the tables listed in the report, in pg, ch and mongo |

Expected problem: `retrieval_limit = 10` caps what the model sees; C1, C4 and C5 have more than 10 matching entries, and the MongoDB schema-description collections add look-alike entries (engineer item C4).

## 4. Inference (the catalogue does not state the answer; a good answer may say so)

| # | Question | Expected answer |
|---|---|---|
| I1 | Can I join PostgreSQL `company` and ClickHouse `Company` directly on their company ids? | The ideal answer: both have a company id column, but nothing in the catalogue says they use the same identifiers, no relationship is declared, and different types are used; it should not claim they join. Measured: they do not (0 matches) |
| I2 | Which ClickHouse tables do the MongoDB `zoominfo_*` collections correspond to? | `zoominfo_Company` / `Company`, `zoominfo_CompanyIndustry` / `CompanyIndustry`, `zoominfo_Experience` / `Experience`, `zoominfo_industry` / `Industry`, `zoominfo_location` / `Location`, `zoominfo_staff` / `Person` (by names; the `testzoominfo_clone2` namespace helps) |
| I3 | Is `companyrelation.relatedcompanyid` a reference to `company`? | The ideal answer: the name suggests it, the catalogue declares nothing, and it cannot be confirmed from metadata. Your finding: it is not (the related companies are not in the database) |
| I4 | Which column should I use to count a company's employees? | ch `Company.companyEmployees` (`Nullable(Int64)`), not `companyEmployeeCountRange` (a range string) |
| I5 | Where would I look for the relationships between companies, such as subsidiaries or partners? | pg `companyrelation` (`companyid`, `relatedcompanyid`) |

Expected problems: an honest "I cannot tell" is rejected, because an answer without citations is treated as invalid ([`answer.py`](../packages/requester/src/dbig_requester/answer.py#L129) line 129); the words `derived from` in a question would be refused outright. I1 and I3 are the cases where the correct answer is uncertainty, so they test whether the app can say "not established" at all.

## How I will run them (after you approve)

1. Run each of the 20 with `dbig-requester ask` and record the answer or error code and the time. All are expected to fail on this server because of the `maxLength` rejection (engineer item B1); that is recorded as the result.
2. Because the app cannot reach the model with these questions, I will also record, for each question, with the app's own search (no model): which of the expected entries appear in the top 10 results. That separates "retrieval cannot find it" from "the model cannot answer".
3. Optionally, as a clearly labelled diagnostic outside the app, send the same retrieved context to the model as plain text, with no JSON schema, and record whether the model answers correctly. That shows whether the failures are the app's format, the retrieval, or the model. Tell me if you want step 3.
