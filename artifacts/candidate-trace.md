# Why the app proposed each relationship, traced to the code

Linked from [`live-test-report.md`](live-test-report.md) (sections 8 and 9). Data: [`run-20261006-035124/candidate-trace.json`](run-20261006-035124/candidate-trace.json) (rule, score and stem of each candidate), [`overlap-counts.json`](run-20261006-035124/overlap-counts.json) and [`overlap-counts-zoominfo-to-clickhouse.json`](run-20261006-035124/overlap-counts-zoominfo-to-clickhouse.json) (value overlap, counts only), `snapshot/edges.jsonl` and `snapshot/evidence.jsonl`. Code: [`packages/catalog-builder/src/dbig_builder/builder.py`](../packages/catalog-builder/src/dbig_builder/builder.py).

## Your marks (recorded as given)

| Group | True | False |
|---|---|---|
| The 21 proposed candidates | #2, #7, #8, #11, #12 (5) | #1, #3, #4, #5, #6, #9, #10, #13 to #21 (16) |
| PostgreSQL to PostgreSQL (my 14 missing pairs) | 12: M1-M8, M11-M14 | 2: M9 `companyinvestor.investorcompanyid`, M10 `companyrelation.relatedcompanyid` (their parent rows are not in the database) |
| ClickHouse to ClickHouse (6) | all 6 | none |
| MongoDB to MongoDB (10) | all 10 | none |
| Cross-source M26 `diffbot_CompanyIndustry.classificationID` to pg `industryclassification` | M26 | none |
| Mongo `zoominfo_*` to ClickHouse (my 11 pairs Z1 to Z11) | all 11 | none |

Result for the guide's section 13, item 5: **true 5, false 16, unsure 0**. The `blocking_rule` of every wrong candidate is `suffix` with `ratio_percent` 100; no candidate came from `exact_name` or `key_name`. The value-overlap measurement separates your marks exactly: the 5 you marked true are the only candidates with 100% overlap; the 16 you marked false all have 0%.

## Why the app related databases that belong to different datasets

The app does not know that two databases are different datasets. It decides only from four things, all in [`builder.py`](../packages/catalog-builder/src/dbig_builder/builder.py#L486-L600) lines 486-600:

1. **It compares every field of every source with every unique field of every source.** The loops are `for child in fields.values()` (line 503) and `for parent in unique_fields` (line 513). The only exclusions are: same field, same table (line 518), a pair already linked by a declared foreign key (line 519), and a different type family (line 520). There is no test on `source_id`, so nothing says "these two databases are unrelated".
2. **A name match is enough.** A column `companyID` is split into stem `company` plus suffix (`_snake` and `_reference_stem`, lines 108-132), and the stem is compared with the parent *table's* name, lowercased with a trailing "s" removed (line 523). `company` equals `company`, ratio 100, rule `suffix`. Every table called `company` or `Company` and every column called `companyID` therefore pair up, in any source.
3. **Only PostgreSQL can supply a parent.** A parent must be a declared unique key or measured unique (>= 0.99) from planner statistics (lines 486-502). PostgreSQL declares no keys but returns `pg_stats`; ClickHouse and MongoDB give no uniqueness (see [`statistics-analysis.md`](statistics-analysis.md)). So the set of possible parents is exactly three PostgreSQL columns (`company.companyid`, `person.personid`, `location.companyid`), and every child in the other two databases that is named like them gets attached to PostgreSQL.
4. **No check of the values.** With sampling off (the default), the only evidence is `name_similarity`, `compatible_type` (both columns are strings) and `measured_uniqueness` (about the *parent*, not about whether the child's values appear in it). [`TECHNICAL_IMPLEMENTATION.md`](../TECHNICAL_IMPLEMENTATION.md#L289) line 289 says blocking should "require compatible value domains" and "abstain when sample support or uniqueness is insufficient". The code does not abstain; the page shows the candidate with confidence "not calibrated".

The five correct candidates are correct by coincidence of data, not by evidence: the MongoDB `diffbot_*` collections come from the same upstream dataset as the PostgreSQL database (whose name contains `diffbot`), so their ids match. The `zoominfo_*` collections and ClickHouse (whose database name contains `zoominfo`) come from another dataset, and the app produced the same output with the same confidence for them. A reviewer cannot tell the two groups apart from the page.

The data supports your reading. Mongo `zoominfo_*` columns against **ClickHouse** (counts only; Z-numbers in [`overlap-counts-zoominfo-to-clickhouse.json`](run-20261006-035124/overlap-counts-zoominfo-to-clickhouse.json)): `Company.companyID` 27/93, `CompanyIndustry.companyID` 25/91, `Experience.companyID` 27/93, `Experience.personID` 1222/5000, `staff.personID` 1264/5000, `contact_information.personID` 928/3777, `location.locationID` 39/103, `industry.industryID` 8/41. The same columns against PostgreSQL: 0 in all cases. So the zoominfo collections belong with ClickHouse, with partial overlap (25% to 40%), and the app never proposed those links because ClickHouse tables cannot be parents (point 3).

## Each candidate

| # | Child to parent | Rule / score | Name logic | Overlap | Your mark | Why it is wrong, or why it happens to be right |
|---|---|---|---|---|---|---|
| 1 | mongo `zoominfo_Experience.companyID` to pg `company.companyid` | suffix / 100 | stem `company` = table `company` | 0/93 | false | zoominfo data against the diffbot database |
| 2 | mongo `diffbot_CompanyExperience.personID` to pg `person.personid` | suffix / 100 | `person` = `person` | 84/84 | true | same upstream dataset |
| 3 | ch `Experience.companyID` to pg `company.companyid` | suffix / 100 | `company` = `company` | 0/5000 | false | company ids: ClickHouse 7 to 9 characters, PostgreSQL 23 (measured on `Company`) |
| 4 | ch `Experience.personID` to pg `person.personid` | suffix / 100 | `person` = `person` | 0/5000 | false | ClickHouse against PostgreSQL; person id lengths were not measured, only the 0 overlap |
| 5 | ch `CompanyIndustry.companyID` to pg `company.companyid` | suffix / 100 | `company` = `company` | 0/5000 | false | company ids: ClickHouse 7 to 9 characters, PostgreSQL 23 (measured on `Company`) |
| 6 | mongo `zoominfo_Experience.personID` to pg `person.personid` | suffix / 100 | `person` = `person` | 0/5000 | false | zoominfo against diffbot |
| 7 | mongo `diffbot_CompanyInvestmentRound.companyID` to pg `company.companyid` | suffix / 100 | `company` = `company` | 5/5 | true | same dataset |
| 8 | mongo `diffbot_CompanyIndustry.companyID` to pg `company.companyid` | suffix / 100 | `company` = `company` | 22/22 | true | same dataset |
| 9 | ch `Location.locationID` to pg `location.companyid` | suffix / 100 | stem `location` = table `location` | 0/5000 | false | two faults: wrong database, and the wrong column of `location`. `location.locationid` measures 0.9826 unique (below 0.99), `location.companyid` measures 1.0, so the only qualifying parent column of the table is the company key. The name rule never compares the child's column name with the parent column's name |
| 10 | mongo `zoominfo_Company.companyID` to pg `company.companyid` | suffix / 100 | `company` = `company` | 0/93 | false | the table is called `zoominfo_Company`, the child column `companyID` has stem `company`, the parent table `company` is in the other dataset. Looks plausible because both ends have the same words |
| 11 | mongo `diffbot_Company.companyID` to pg `company.companyid` | suffix / 100 | `company` = `company` | 22/22 | true | same dataset |
| 12 | mongo `diffbot_CompanyExperience.companyID` to pg `company.companyid` | suffix / 100 | `company` = `company` | 17/17 | true | same dataset |
| 13 | ch `Company.companyID` to pg `company.companyid` | suffix / 100 | `company` = `company` | 0/5000 (and 0/3940 reverse) | false | the table is ClickHouse's own company dimension; its key is the parent of the ClickHouse tables (your marks), not a child of PostgreSQL |
| 14 | ch `Person.personID` to pg `person.personid` | suffix / 100 | `person` = `person` | 0/5000 | false | ClickHouse against PostgreSQL |
| 15 | mongo `zoominfo_contact_information.personID` to pg `person.personid` | suffix / 100 | `person` = `person` | 0/3777 | false | zoominfo against diffbot |
| 16 | ch `PersonContact.personID` to pg `person.personid` | suffix / 100 | `person` = `person` | 0/5000 | false | ClickHouse against PostgreSQL |
| 17 | mongo `zoominfo_evidence.company_id` to pg `company.companyid` | suffix / 100 | `company_id` splits to `company` | 0/69 | false | zoominfo against diffbot |
| 18 | mongo `zoominfo_contracts.company_id` to pg `company.companyid` | suffix / 100 | `company_id` splits to `company` | 0/10 | false | zoominfo against diffbot |
| 19 | mongo `zoominfo_CompanyIndustry.companyID` to pg `company.companyid` | suffix / 100 | `company` = `company` | 0/91 | false | zoominfo against diffbot |
| 20 | mongo `zoominfo_location.locationID` to pg `location.companyid` | suffix / 100 | `location` = `location` | 0/103 | false | same two faults as #9 |
| 21 | mongo `zoominfo_staff.personID` to pg `person.personid` | suffix / 100 | `person` = `person` | 0/5000 | false | zoominfo against diffbot |

Candidates 10 and 13 look "complicated" because the child is the main `Company` table of its own database. The app has no notion of a table being the root of its own database; only a unique key marks a parent, and only PostgreSQL has one, so it attached everything named `company` to the PostgreSQL parent.

## Why the app did not find the relationships you consider correct

| Case | Trace |
|---|---|
| J1: pg `companyexperience.companyid` to `company.companyid` | The child name is `companyid`, all lowercase. `_snake("companyid")` = `companyid`, no underscore, so no suffix is recognised (`_reference_stem` returns `('companyid', False)`). Without a suffix the stem must equal the parent *table name* exactly (line 543): `companyid` is not `company`. The shortcut for "same name as a declared key" (`key_name`, lines 524-533) needs the parent to be a *declared* unique key; PostgreSQL declares none, so the measured-unique `company.companyid` does not qualify. No other branch applies. The edge is never created, and the planner, which works only from candidate edges, answers `unavailable`. The same trace explains J2 (`personid`). With `companyID` or `company_id` the same pair would be found, as the ClickHouse and MongoDB children show. |
| `companyindustry.classificationid` to `industryclassification.classificationid` | Same: `classificationid` has no separator, stem `classificationid`, table name `industryclassification`. |
| J3: ch `Experience.companyID` to ch `Company.companyID` | The child is recognised (stem `company`), but the parent ClickHouse `Company.companyID` is not in `unique_fields`: no declared key, no uniqueness measurement. ClickHouse does declare it in its sorting key, which the collector reads but stores only as a SHA-256 hash (`primary_key_sha256`, `sort_key_sha256` in the object attributes), so the builder could not use it even if it tried. The planner then answered J3 with three paths that go through PostgreSQL, built from candidates #3 and #13, both of which you marked false. The "found" answer is therefore built on wrong relationships and returns 0 joined rows when executed. |
| J4: ch `Company` to pg `company` | Built on false candidate #13. Executed: 0 joined rows of 1000. |
| MongoDB `zoominfo_*` to ClickHouse | Not proposed: ClickHouse tables cannot be parents. |
