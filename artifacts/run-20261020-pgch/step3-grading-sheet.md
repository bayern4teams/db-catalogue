# Step 3 grading sheet (PostgreSQL + ClickHouse re-run, 2026-10-20)

Write **your grade** after each question: Right, Partial, Wrong, NOT_ESTABLISHED, NO_CATALOGUE_MATCH, or the error code shown. This morning's grade is for reference only. Answers are the model's text as returned (catalogue names, no data values).

## N-D1
- **Question:** Which PostgreSQL column holds the fiscal year of a company's financial record, and what is its type?
- **Expected:** pg.companyfinancials.year (integer)
- **Result code:** answered (203.98 s)
- **Model answer:** The relevant PostgreSQL column is `year` in the `companyfinancials` table, and its type is `integer`. The catalogue metadata does not provide an explicit description confirming it is the fiscal year, but it is the only year column associated with company financial records.
- **Cited:** pg.companyfinancials.year
- **This morning:** Right
- **Your grade:** 

## N-D2
- **Question:** Which ClickHouse column holds a person's first name, and what is its type?
- **Expected:** ch.Person.firstName (Nullable(String))
- **Result code:** answered (109.64 s)
- **Model answer:** ClickHouse column Person.firstName holds a person's first name, and its data type is Nullable(String).
- **Cited:** ch.Person, ch.Person.firstName
- **This morning:** Right
- **Your grade:** 

## N-D3
- **Question:** Which PostgreSQL column holds the latitude of a company location, and what type is it?
- **Expected:** pg.location.latitude (text)
- **Result code:** answered (112.51 s)
- **Model answer:** In the PostgreSQL source `pg-main`, the latitude of a company location is held by the column `latitude` in the table `location` in the `public` namespace. The column’s data type is `text`.
- **Cited:** pg.location.latitude, pg.location
- **This morning:** Right
- **Your grade:** 

## N-D4
- **Question:** In ClickHouse, which table and column hold the free email addresses of people?
- **Expected:** ch.PersonContact.freeMails (String), linked to a person by ch.PersonContact.personID
- **Result code:** answered (48.62 s)
- **Model answer:** The free email addresses are held in the table/object PersonContact in the column freeMails. The metadata also shows a tentative relationship from PersonContact to Person via personID, but it is not established. Evidence: name, compatible type, and weak key claim (source sorting or primary key, uniqueness unproven); value overlap 0% of 4042 sampled child values.
- **Cited:** ch.PersonContact.freeMails, relationship.PersonContact -> Person (personID -> personID)
- **This morning:** Right
- **Your grade:** 

## N-D5
- **Question:** Which PostgreSQL column stores the name of a technology a company uses?
- **Expected:** pg.companytechnographics.technologyname (text); related: technologytype, categories, techid
- **Result code:** answered (158.99 s)
- **Model answer:** The PostgreSQL column is public.companytechnographics.technologyname. Metadata lists it as a text field named technologyname on the companytechnographics object (field:10a7e6a6c31530ce482c9bd1bdf7539e). The same object also has companyid (field:68568d18befbaa3edc1097a6ccdc2420), with a tentative reference-candidate relationship to company (edge:12a153b35e900302a718b5d2860598f8): "name, compatible type, and uniqueness measured from source statistics, not declared; value overlap 100% of 814 sampled child values".
- **Cited:** pg.companytechnographics.technologyname, pg.companytechnographics.companyid, relationship.companytechnographics -> company (companyid -> companyid)
- **This morning:** Right
- **Your grade:** 

## N-D6
- **Question:** Which ClickHouse columns hold a person's job title in the work-history table?
- **Expected:** ch.Experience.jobTitle (Nullable(String)) and ch.Experience.title (Nullable(String))
- **Result code:** answered (38.88 s)
- **Model answer:** In the ClickHouse catalogue entries for source `ch-main` and namespace `testzoominfo_clone2`, the `Experience` table lists two columns whose names indicate a person's job title: `jobTitle` and `title`. Both are typed as `Nullable(String)`. The metadata does not include a table explicitly labeled “work-history,” nor descriptions confirming semantic meaning; this answer is based on the listed field names and their `object_name` values.
- **Cited:** ch.Experience.jobTitle, ch.Experience.title
- **This morning:** Right
- **Your grade:** 

## N-D7
- **Question:** Which PostgreSQL columns record the amount and currency of an investment round, and what are their types?
- **Expected:** pg.companyinvestmentround.amount (bigint), pg.companyinvestmentround.currency (text)
- **Result code:** answered (40.75 s)
- **Model answer:** In the PostgreSQL catalog, the investment round amount is recorded by the `companyinvestmentround.amount` column as `bigint`, and the currency is recorded by the `companyinvestmentround.currency` column as `text`.
- **Cited:** pg.companyinvestmentround.amount, pg.companyinvestmentround.currency
- **This morning:** Right
- **Your grade:** 

## N-D8
- **Question:** Which ClickHouse column holds a company's phone number?
- **Expected:** ch.Company.companyPhone (Nullable(String))
- **Result code:** answered (24.65 s)
- **Model answer:** The ClickHouse column is `companyPhone` on the `Company` table.
- **Cited:** ch.Company.companyPhone, ch.Company
- **This morning:** Right
- **Your grade:** 

## N-D9
- **Question:** Which PostgreSQL column holds the founding date of a company, and is it a date type?
- **Expected:** pg.company.foundingdate (text); not a date or timestamp type
- **Result code:** answered (25.76 s)
- **Model answer:** In PostgreSQL source `pg-main`, the founding date is represented by the column `foundingdate` in the `public.company` table. Its recorded `data_type` is `text`, so it is not stored as a date type.
- **Cited:** pg.company, pg.company.foundingdate
- **This morning:** Right
- **Your grade:** 

## N-D10
- **Question:** Which ClickHouse column holds the country code of a location, and what is its type?
- **Expected:** ch.Location.CountryCode (Nullable(String))
- **Result code:** answered (24.2 s)
- **Model answer:** The ClickHouse column is "CountryCode" in the "Location" table, and its type is "Nullable(String)".
- **Cited:** ch.Location.CountryCode, ch.Location
- **This morning:** Right
- **Your grade:** 

## N-S1
- **Question:** In PostgreSQL, how do I list the financial amounts per year together with the name of the company?
- **Expected:** Join pg.companyfinancials.companyid -> pg.company.companyid. Final: pg.companyfinancials.year, pg.companyfinancials.amount, pg.companyfinancials.currency, and pg.company.fullname and pg.company.entityname (owner 2026-10-18: either is fine; a good answer mentions both)
- **Result code:** answered (79.71 s)
- **Model answer:** Use PostgreSQL `companyfinancials` and join it to `company` on the candidate key `companyid`. The metadata shows `companyfinancials.year` (field:9431fa59187fb3d015d2b2238c779159) and `companyfinancials.amount` (field:9544b40caa989a5ecbf2abeb953ac2db). It also shows `company` (object:cdbf59a0739bcf46a9eef890233bf217) and `company.companyid` (field:0006585d4b70be3a0f20a3714fa9a277). The candidate relationship from `companyfinancials.companyid` (field:016c0eddecd67ae864f4ca53ded32d9c) to `company.companyid` is edge:d37d5bd241167af3738fec495f13c0ae. This is tentative, not declared. Evidence: "name, compatible type, and uniqueness measured from source statistics, not declared; value overlap 100% of 29 sampled child values".

The metadata does not show a `company.name` field. It shows `company.entityname` (field:9d09e4066544e092c095abcc1af8476b) and `company.fullname` (field:f09ae868731d63bd8a5a7e08b085f99d). If `entityname` is the intended company name field, a row-level query is:

SELECT cf.year, cf.amount, c.entityname AS company_name
FROM companyfinancials AS cf
JOIN company AS c ON cf.companyid = c.companyid
ORDER BY cf.year, c.entityname;

If you want one row per year and company, aggregate:

SELECT cf.year, c.entityname AS company_name, SUM(cf.amount) AS total_amount
FROM companyfinancials AS cf
JOIN company AS c ON cf.companyid = c.companyid
GROUP BY cf.year, c.entityname
ORDER BY cf.year, c.entityname;

Replace `c.entityname` with `c.fullname` if that is the company-name field you intend, and confirm the tentative join key before relying on it.
- **Cited:** pg.company, pg.companyfinancials.year, pg.companyfinancials.amount, pg.companyfinancials.companyid, pg.company.companyid, relationship.companyfinancials -> company (companyid -> companyid), pg.company.entityname, pg.company.fullname
- **This morning:** Partial
- **Your grade:** 

## N-S2
- **Question:** In PostgreSQL, how do I get the technologies used by a company together with the company website?
- **Expected:** Join pg.companytechnographics.companyid -> pg.company.companyid. Final: pg.companytechnographics.technologyname, pg.company.website
- **Result code:** answered (138.33 s)
- **Model answer:** Use `public.company` and `public.companytechnographics`, joined on `companyid`. The catalogue contains `company.website`, `company.companyid`, and `companytechnographics.companyid`, `companytechnographics.technologyname`. The relationship between `companytechnographics` and `company` is `tentative`, not declared; its evidence is: "name, compatible type, and uniqueness measured from source statistics, not declared; value overlap 100% of 814 sampled child values".

Example PostgreSQL query returning technologies alongside the company website:

```sql
SELECT c.website,
       string_agg(DISTINCT t.technologyname, ', ') AS technologies
FROM public.company AS c
JOIN public.companytechnographics AS t
  ON t.companyid = c.companyid
GROUP BY c.companyid, c.website;
```

If you need one row per company-technology pair instead of one aggregated row per company, use:

```sql
SELECT c.website,
       t.technologyname,
       t.technologytype
FROM public.company AS c
JOIN public.companytechnographics AS t
  ON t.companyid = c.companyid;
```
- **Cited:** pg.company, pg.company.website, pg.company.companyid, pg.companytechnographics.companyid, pg.companytechnographics.technologyname, pg.companytechnographics.technologytype, relationship.companytechnographics -> company (companyid -> companyid)
- **This morning:** MODEL_INVALID_CITATION
- **Your grade:** 

## N-S3
- **Question:** In ClickHouse, how do I get from a person's name to the job titles and the names of the companies they hold them at?
- **Expected:** Join ch.Experience.personID -> ch.Person.personID and ch.Experience.companyID -> ch.Company.companyID. Final: ch.Person.name, ch.Experience.jobTitle and ch.Experience.title (owner: mention both), ch.Company.companyName
- **Result code:** answered (71.44 s)
- **Model answer:** Use the ClickHouse objects in source ch-main, namespace testzoominfo_clone2. Start from Person (object:364d20537dea8b3c54c5c93685285687), using its name field (field:423465b8356d027b942c8de2060c1173) for a stored full-name lookup, or firstName (field:055ac3c827cb9d840704592325514de4), lastName (field:900d74cdf7588a9ddca14263575d3657), and middleInitial (field:61d2d3d779f343b228301a2862619f67) if matching parts. Join Person to Experience using the tentative candidate relationship edge:2569a3532b8abeff3a3d90010ee81262 on Person.personID (field:06b1befeb60ad6fe33aa28ba79c79b31) = Experience.personID (field:185053ed6692d43fdaf5c3bda313d1c2). From Experience select jobTitle (field:257400d421de3d60c3a4c87ddf9b14a2) or title (field:d6156efd7957fab3a15e264c9010af75). Then join Experience to Company (object:b3653bad92366ef20567af26b5ca7ce6) using the tentative candidate relationship edge:2623e3f6bf42984777c7fae4599cd25a on Experience.companyID (field:6201990413b074090cd6f8cf34c2aa87) = Company.companyID (field:861fe226e14fcf38588592aad6e62355), and select Company.companyName (field:acb560e6086a0d2545806ce87102ef28). Both edges are tentative, not declared; their evidence is: 'name, compatible type, and weak key claim (source sorting or primary key, uniqueness unproven); value overlap 24% of 4096 sampled child values' for edge:2569a3532b8abeff3a3d90010ee81262, and 'name, compatible type, and weak key claim (source sorting or primary key, uniqueness unproven); value overlap 1% of 2702 sampled child values' for edge:2623e3f6bf42984777c7fae4599cd25a. Because both candidate joins are between ch-main objects in testzoominfo_clone2, they are not a cross-source join limitation.
- **Cited:** relationship.Experience -> Company (companyID -> companyID), relationship.Experience -> Person (personID -> personID), ch.Company, ch.Company.companyName, ch.Company.companyID, ch.Experience.companyID, ch.Experience.title, ch.Experience.jobTitle, ch.Experience.personID, ch.Person.personID, ch.Person.middleInitial, ch.Person.lastName, ch.Person.firstName, ch.Person.name, ch.Person
- **This morning:** Right
- **Your grade:** 

## N-S4
- **Question:** In ClickHouse, how do I get the free email column for the people who worked at a given company?
- **Expected:** Join ch.Experience.companyID -> ch.Company.companyID, then ch.Experience.personID -> ch.PersonContact.personID. Final: ch.PersonContact.freeMails, ch.Company.companyName
- **Result code:** answered (58.5 s)
- **Model answer:** The free email column is `PersonContact.freeMails` (field:7b45c137b580b26cc328fc4aa0dbefc6).
- **Cited:** ch.PersonContact.freeMails
- **This morning:** NOT_ESTABLISHED
- **Your grade:** 

## N-S5
- **Question:** In ClickHouse, how do I list the city of each company location together with the industry names of that company?
- **Expected:** Join ch.Location.companyID -> ch.Company.companyID; ch.CompanyIndustry.companyID -> ch.Company.companyID; ch.CompanyIndustry.industryID -> ch.Industry.industryID. Final: ch.Location.City, ch.Industry.name (or displayName)
- **Result code:** NOT_ESTABLISHED_BY_CATALOGUE (164.11 s)
- **Model answer:** (empty)
- **Cited:** ch.Industry.industryID, ch.Industry.name, ch.Location.companyID, ch.Company.companyID, ch.Industry.displayName, ch.Location.City, ch.Location, ch.Company, ch.Industry, relationship.CompanyIndustry -> Company (companyID -> companyID), relationship.CompanyIndustry -> Industry (industryID -> industryID), relationship.location -> Company (companyid -> companyID)
- **This morning:** Partial (owner)
- **Your grade:** 

## N-S6
- **Question:** In PostgreSQL, how do I get the contact strings of companies together with the industry names of those companies?
- **Expected:** Join pg.companycontact.companyid -> pg.companyindustry.companyid, then pg.companyindustry.classificationid -> pg.industryclassification.classificationid. Final: pg.companycontact.contactstring, pg.companycontact.contacttype, pg.industryclassification.name
- **Result code:** answered (188.4 s)
- **Model answer:** The relevant metadata shows `companycontact.contactstring` (field:c9a2611a4dd860082af598de8a2c5341) and `industryclassification.name` (field:630bfde3eea8bda3e9a668a339ed3202). A candidate same-source PostgreSQL query is: SELECT cc.contactstring, ic.name FROM companycontact AS cc JOIN companyindustry AS ci ON cc.companyid = ci.companyid JOIN industryclassification AS ic ON ci.classificationid = ic.classificationid; The companycontact to companyindustry join is a tentative reference candidate (edge:38b465032bbb068376d1b3a098d1b83d), and the companyindustry to industryclassification join is a tentative reference candidate (edge:f496e1a200c0d7c4e8f5e04bc38f3654); the metadata does not declare these relationships. If you want to anchor the result to the company object, add JOIN company AS c ON cc.companyid = c.companyid using the tentative companycontact to company candidate (edge:b0e429b0a4b3462314573c9b9722fef3). All cited tables/fields are in pg-main/public, so no cross-source join is used.
- **Cited:** pg.company, pg.industryclassification, pg.company.companyid, pg.companycontact.contactstring, pg.companycontact.companyid, pg.industryclassification.name, pg.industryclassification.classificationid, relationship.companycontact -> companyindustry (companyid -> companyid), relationship.companyindustry -> industryclassification (classificationid -> classificationid), relationship.companycontact -> company (companyid -> companyid)
- **This morning:** Partial
- **Your grade:** 

## N-S7
- **Question:** In PostgreSQL, how do I list the people with a company experience record together with the company name?
- **Expected:** Join pg.companyexperience.personid -> pg.person.personid and pg.companyexperience.companyid -> pg.company.companyid. Final: pg.person.name, pg.companyexperience.role, pg.company.fullname and pg.company.entityname (owner: mention both)
- **Result code:** answered (85.83 s)
- **Model answer:** Use the pg-main/public metadata: companyexperience contains personid [field:9895a6239314be7552969ff02192e7ff] and companyid [field:188a84e5dbad19e9bccedd9ef313173d]. The company table object [object:cdbf59a0739bcf46a9eef890233bf217] has companyid [field:0006585d4b70be3a0f20a3714fa9a277] and two text columns that can serve as a company name: entityname [field:9d09e4066544e092c095abcc1af8476b] and fullname [field:f09ae868731d63bd8a5a7e08b085f99d]; the returned metadata does not include a company field literally named name. person has personid [field:be34b2e648fef29d2674e6a288243107] and name [field:35bbe1f69041cc864971e65231bbe28c]. Join companyexperience to company on companyid and to person on personid. The companyexperience-to-company edge edge:86e6d44e7dfd5644c735966621e5076e is a tentative reference candidate, not declared: "name, compatible type, and uniqueness measured from source statistics, not declared; value overlap 100% of 482 sampled child values". The companyexperience-to-person edge edge:0eaf0e7bad62b09c807f0de9e49e7809 is a tentative reference candidate, not declared: "name, compatible type, and uniqueness measured from source statistics, not declared; value overlap 100% of 1215 sampled child values". Because both edges are pg-main to pg-main, they can be joined in one query. Example: SELECT p.name AS person_name, ce.role AS experience_role, c.entityname AS company_name FROM public.companyexperience AS ce JOIN public.company AS c ON c.companyid = ce.companyid JOIN public.person AS p ON p.personid = ce.personid; If the intended company-name column is fullname instead of entityname, replace c.entityname with c.fullname. If only the person name recorded on the experience row is needed, select companyexperience.personname [field:9007cd6bf64aa2c28fbd8ae9c23c3c86] and companyexperience.role [field:8353045f95517fb5445f62e204c75d78] instead of joining person.
- **Cited:** pg.company, pg.companyexperience.personid, pg.companyexperience.companyid, pg.company.companyid, pg.company.entityname, pg.company.fullname, pg.person.personid, pg.person.name, relationship.companyexperience -> company (companyid -> companyid), relationship.companyexperience -> person (personid -> personid), pg.companyexperience.personname, pg.companyexperience.role
- **This morning:** Partial
- **Your grade:** 

## N-S8
- **Question:** In PostgreSQL, how do I get the relation type of each company relation together with the industry names of the company?
- **Expected:** Join pg.companyrelation.companyid -> pg.companyindustry.companyid, then pg.companyindustry.classificationid -> pg.industryclassification.classificationid. Final: pg.companyrelation.relationtype, pg.industryclassification.name
- **Result code:** MODEL_UNAVAILABLE_OR_INVALID (81.45 s)
- **Model answer:** (empty)
- **Cited:** (none)
- **This morning:** NOT_ESTABLISHED
- **Your grade:** 

## N-S9
- **Question:** In PostgreSQL, how do I list the investors of each investment round of a company with the company name and round amount?
- **Expected:** Join pg.companyinvestmentround.companyid -> pg.company.companyid and pg.companyinvestor.investmentid -> pg.companyinvestmentround.investmentid. Final: pg.company.fullname, pg.companyinvestmentround.amount, pg.companyinvestor.surfaceform and pg.companyinvestor.summary (owner: both describe the investor). The investor company id column (investorcompanyid) must NOT be joined to pg.company.companyid (listed FALSE)
- **Result code:** answered (106.83 s)
- **Model answer:** You can list the investor records by joining company to companyinvestmentround on companyid and companyinvestor to companyinvestmentround on investmentid. The company name field available in the metadata is entityname (fullname is also available if that is the intended name), and the round amount is companyinvestmentround.amount. The joins use tentative candidate relationships, not declared relationships. The retrieved metadata does not show a relationship from companyinvestor.investorcompanyid to company.companyid, so the query does not join companyinvestor to company for investor-company names.
- **Cited:** pg.company, pg.company.entityname, pg.company.fullname, pg.company.companyid, pg.companyinvestmentround.companyid, pg.companyinvestmentround.amount, pg.companyinvestmentround.investmentid, pg.companyinvestor.investmentid, pg.companyinvestor.surfaceform, pg.companyinvestor.types, pg.companyinvestor.summary, pg.companyinvestor.investorcompanyid, relationship.companyinvestmentround -> company (companyid -> companyid), relationship.companyinvestor -> companyinvestmentround (investmentid -> investmentid)
- **This morning:** Partial
- **Your grade:** 

## N-S10
- **Question:** In PostgreSQL, how do I get the employee category of companies located in a given city?
- **Expected:** Join pg.companyemployeecategory.companyid -> pg.location.companyid. Final: pg.location.city, pg.companyemployeecategory.category (optionally pg.companyemployeecategory.empcatid)
- **Result code:** answered (99.91 s)
- **Model answer:** Use `companyemployeecategory.category` for the employee category and `location.city` for the city filter. The metadata shows both `companyemployeecategory` and `location` have a `companyid` field, and there is a tentative reference candidate joining them by that column. All relevant objects are in the same source (`pg-main`), so they can be joined in one PostgreSQL query.
- **Cited:** pg.companyemployeecategory.category, pg.location.city, pg.companyemployeecategory.companyid, pg.location.companyid, relationship.companyemployeecategory -> location (companyid -> companyid)
- **This morning:** Right
- **Your grade:** 

## N-C1
- **Question:** List every column in both databases that holds a part of a postal address.
- **Expected:** pg.location: street, city, region, country, postalcode, addressfull (all text; latitude, longitude, precision are coordinates, not address parts). ch.Location: Street, City, State, CountryCode, Zip (all Nullable(String))
- **Result code:** NOT_ESTABLISHED_BY_CATALOGUE (121.79 s)
- **Model answer:** I checked all returned field metadata (field:f30788483ff4f1db25789c625862549f, field:07881a985e5af07193a5165f89d2df8c, field:f128752420b5952c69cf9f4e2ebab887, field:46ee056cc036c6d85f6c8458a3d5293d, field:8d39aa813552fb804eb90af7e14f1221, field:8276e5ecfe3a91c3c7925fe1279ad701, field:70887869bcd4fcecdb8cab28844c94c4, field:508c06656aa4615277b88ca5cc839bdb, field:cd59b81fca3bed99927ac9398dd314bc, field:3ef7953d5e6d306b98778ceaba6c6581, field:2d7244fee9e418d077b9952dbb3d7d38, field:8cc26e5b0efdb149aeb64a8261c1e822, field:0bd1b27902d1d447defde90e5f772bb3, field:1bafab2370a8680d51f8cf828cc81939, field:b8f5ded6c3b317cad0dfac8d63ae80ba). These are all from source_id pg-main, namespace public, object_name location. The returned metadata contains no field items for the ch-main source, so it cannot list every column in both databases. The pg-main columns whose names indicate postal-address content are: addressfull (field:f30788483ff4f1db25789c625862549f; full address string, not a discrete component unless counted), postalcode (field:07881a985e5af07193a5165f89d2df8c), city (field:46ee056cc036c6d85f6c8458a3d5293d), country (field:8276e5ecfe3a91c3c7925fe1279ad701), region (field:1bafab2370a8680d51f8cf828cc81939), and street (field:b8f5ded6c3b317cad0dfac8d63ae80ba).
- **Cited:** pg.location.addressfull, pg.location.postalcode, pg.location.city, pg.location.country, pg.location.region, pg.location.street
- **This morning:** Partial (owner)
- **Your grade:** 

## N-C2
- **Question:** Which PostgreSQL tables have a confidence column?
- **Expected:** pg.companycontact, companyemployeecategory, companyexperience, companyfinancials, companyinvestmentround, companyinvestor, companyrelation, companytechnographics, industryclassification, person (all text). pg.company has metadata_confidence, not confidence. companyindustry and location have none
- **Result code:** answered (104.78 s)
- **Model answer:** Based on the returned catalog entries with source_id `pg-main`, the tables that have a column named `confidence` are: `companycontact`, `companyemployeecategory`, `companyexperience`, `companyfinancials`, `companyinvestor`, `companyinvestmentround`, `companyrelation`, and `companytechnographics`. The `company` table is not included because the returned metadata shows a `metadata_confidence` field, not a `confidence` field.
- **Cited:** pg.companycontact, pg.companycontact.confidence, pg.companyemployeecategory, pg.companyemployeecategory.confidence, pg.companyexperience, pg.companyexperience.confidence, pg.companyfinancials, pg.companyfinancials.confidence, pg.companyinvestor, pg.companyinvestor.confidence, pg.companyinvestmentround, pg.companyinvestmentround.confidence, pg.companyrelation, pg.companyrelation.confidence, pg.companytechnographics, pg.companytechnographics.confidence, pg.company, pg.company.metadata_confidence
- **This morning:** Partial
- **Your grade:** 

## N-C3
- **Question:** List every column in the catalogue that holds a person's name or a part of it.
- **Expected:** pg.person.name; pg.companyexperience.personname; pg.company.foundername; pg.company.ceoname; ch.Person.name, firstName, middleInitial, lastName
- **Result code:** answered (31.06 s)
- **Model answer:** The returned catalogue metadata shows these columns as holding a person's name or a part of a person's name, based on their column names and table context: `public.person.name`, `testzoominfo_clone2.Person.name`, `testzoominfo_clone2.Person.firstName`, `testzoominfo_clone2.Person.lastName`, and `testzoominfo_clone2.Person.middleInitial`.
- **Cited:** pg.person.name, ch.Person.name, ch.Person.firstName, ch.Person.lastName, ch.Person.middleInitial
- **This morning:** Partial
- **Your grade:** 

## N-C4
- **Question:** Which columns in the catalogue have a timestamp or datetime type, and in which tables?
- **Expected:** pg: created_at (timestamp without time zone) in all 13 tables. ch: _version (Nullable(DateTime)) in all 7 tables. pg._version is integer, not a timestamp
- **Result code:** NO_CATALOGUE_MATCH (0.54 s)
- **Model answer:** (empty)
- **Cited:** (none)
- **This morning:** NO_CATALOGUE_MATCH
- **Your grade:** 

## N-C5
- **Question:** List every column in both databases that holds a money amount or the currency of one.
- **Expected:** pg.company.totalinvestment (bigint), pg.company.totalinvestmentcurrency (text), pg.companyfinancials.amount (bigint), pg.companyfinancials.currency (text), pg.companyinvestmentround.amount (bigint), pg.companyinvestmentround.currency (text); ch.Company.companyRevenueIn000s (Nullable(Int64)), companyRevenue (Nullable(String)), companyRevenueRange (Nullable(String)), companyRevenue_currency (Nullable(String))
- **Result code:** answered (76.85 s)
- **Model answer:** Based only on the returned field metadata (names, data types, object/namespace/source), the following columns are the money-amount or currency columns across the two sources. I have separated one monetary-range column because it is not a single amount.

**Money amount or currency columns:**
- pg-main / public.companyinvestmentround.amount — field:4382c7b4a2f817850726f368d5a736b8 — bigint — money amount
- pg-main / public.companyfinancials.amount — field:9544b40caa989a5ecbf2abeb953ac2db — bigint — money amount
- pg-main / public.companyinvestmentround.currency — field:6eadd41789773a04386dec450cf5d4a0 — text — currency
- pg-main / public.companyfinancials.currency — field:8052b7c84d7912b48e831735c70136c7 — text — currency
- ch-main / testzoominfo_clone2.Company.companyRevenue — field:675640b0bb67f300bc70991ae573177b — Nullable(String) — money amount (stored as text)
- ch-main / testzoominfo_clone2.Company.companyRevenueIn000s — field:1181504f836ac21be205c52fd26c49e2 — Nullable(Int64) — money amount in thousands
- ch-main / testzoominfo_clone2.Company.companyRevenue_currency — field:bea678fd1330ff63ec0d9a74225453c9 — Nullable(String) — currency

**Monetary-range column (not a single amount/currency, included only if amount ranges count):**
- ch-main / testzoominfo_clone2.Company.companyRevenueRange — field:2abb66ab3fd3bd1218585e396a26a6d4 — Nullable(String) — monetary revenue range

No explicit semantic money/currency tags are present in the catalogue metadata; the classification above is based on the listed field names and data types.
- **Cited:** pg.companyinvestmentround.amount, pg.companyfinancials.amount, pg.companyinvestmentround.currency, pg.companyfinancials.currency, ch.Company.companyRevenue, ch.Company.companyRevenueIn000s, ch.Company.companyRevenue_currency, ch.Company.companyRevenueRange
- **This morning:** Partial
- **Your grade:** 

## N-C6
- **Question:** Which columns, other than version columns, have a numeric type in PostgreSQL?
- **Expected:** pg.company.totalinvestment (bigint), pg.companyfinancials.year (integer), pg.companyfinancials.amount (bigint), pg.companyinvestmentround.amount (bigint). Everything else is text or timestamp
- **Result code:** answered (65.6 s)
- **Model answer:** In the PostgreSQL catalogue metadata for source pg-main, namespace public, the non-version columns with numeric data types are: amount in companyinvestmentround (bigint), amount in companyfinancials (bigint), totalinvestment in company (bigint), and year in companyfinancials (integer). These correspond to field:4382c7b4a2f817850726f368d5a736b8, field:9544b40caa989a5ecbf2abeb953ac2db, field:a7d40ea5bc0e90b96a2688ed69800d13, and field:9431fa59187fb3d015d2b2238c779159. No other returned non-version columns are shown as numeric.
- **Cited:** pg.companyinvestmentround.amount, pg.companyfinancials.amount, pg.company.totalinvestment, pg.companyfinancials.year
- **This morning:** Right
- **Your grade:** 

## N-C7
- **Question:** Which ClickHouse columns are not nullable, and what do they have in common?
- **Expected:** ch.Company.companyID, ch.CompanyIndustry.companyID, ch.CompanyIndustry.industryID, ch.Experience.personID, ch.Experience.companyID, ch.Industry.industryID, ch.Location.locationID, ch.Person.personID, ch.PersonContact.freeMails; all String; mostly ids
- **Result code:** NO_CATALOGUE_MATCH (0.57 s)
- **Model answer:** (empty)
- **Cited:** (none)
- **This morning:** NO_CATALOGUE_MATCH
- **Your grade:** 

## N-C8
- **Question:** List every column in both databases that links people to companies or to person records.
- **Expected:** pg.companyexperience.personid, pg.companyexperience.companyid; pg.person.personid; ch.Experience.personID, ch.Experience.companyID; ch.Person.personID; ch.PersonContact.personID. Confirmed TRUE: pg.companyexperience.personid -> pg.person.personid; ch.Experience.personID -> ch.Person.personID and -> ch.PersonContact.personID; ch.Experience.companyID -> ch.Company.companyID
- **Result code:** answered (172.21 s)
- **Model answer:** Using only the returned catalogue metadata, the person/company and person-record links are all `tentative` `REFERENCE_CANDIDATE` relationships; no `declared` relationships were returned. Candidate link columns are listed separately from the person/company key columns they reference. Cross-source candidates are marked and cannot be joined in one query.
- **Cited:** relationship.person -> Person (personid -> personID), relationship.Experience -> Person (personID -> personID), relationship.Experience -> Company (companyID -> companyID), relationship.PersonContact -> Person (personID -> personID), relationship.companyexperience -> person (personid -> personid), relationship.companyexperience -> company (companyid -> companyid), relationship.companyexperience -> Person (personid -> personID), ch.Experience.personID, ch.Experience.companyID, ch.PersonContact.personID, ch.Person.personID, ch.Company.companyID, pg.person.personid, pg.company.companyid, ch.Person, ch.Company, ch.PersonContact, pg.person, pg.company
- **This morning:** Right
- **Your grade:** 

## N-C9
- **Question:** In every source, which tables hold a role, title or category of a person or employees?
- **Expected:** pg.person.role; pg.companyexperience.role; pg.companyemployeecategory.category; ch.Experience.jobTitle, title
- **Result code:** answered (73.3 s)
- **Model answer:** From the returned catalogue metadata, `pg-main` shows one table holding a person’s role: `public.person` (`object:a304cb9932fe92f0dc604f4fcbf4e156`), via field `role` (`field:5e6cab3f3d0f18ecd1720ee376af56d4`). It also has field `types` (`field:fab61c9103d684f2ddf949d6fa897fd5`), which may represent categories, but `role` is the explicit role field. For `ch-main`, the returned metadata lists tables `testzoominfo_clone2.Person` (`object:364d20537dea8b3c54c5c93685285687`) and `testzoominfo_clone2.PersonContact` (`object:5696f20c5f1a765ab463bd4aaa930e13`), but none of their returned fields are labelled `role`, `title`, or `category` (checked fields: `field:a74642e42b5d24d21983b8e5492ca629`, `field:055ac3c827cb9d840704592325514de4`, `field:900d74cdf7588a9ddca14263575d3657`, `field:61d2d3d779f343b228301a2862619f67`, `field:423465b8356d027b942c8de2060c1173`, `field:06b1befeb60ad6fe33aa28ba79c79b31`, `field:4fdfb2ce09a237e2342293753b0c8e6e`, `field:7b45c137b580b26cc328fc4aa0dbefc6`, `field:df329c939e41b4df0640f5f9fbaa1036`). Therefore: pg-main — `public.person`; ch-main — no such table is shown in the returned metadata.
- **Cited:** pg.person, pg.person.role, pg.person.types, ch.Person, ch.PersonContact, ch.Person._version, ch.Person.firstName, ch.Person.lastName, ch.Person.middleInitial, ch.Person.name, ch.Person.personID, ch.PersonContact._version, ch.PersonContact.freeMails, ch.PersonContact.personID
- **This morning:** Partial
- **Your grade:** 

## N-C10
- **Question:** Which tables in both databases describe locations of companies, and which columns carry the company id?
- **Expected:** pg.location (companyid, locationid); ch.Location (companyID Nullable(Int64), locationID String). Confirmed TRUE: pg.location.companyid -> pg.company.companyid, ch.Location.companyID -> ch.Company.companyID
- **Result code:** answered (163.53 s)
- **Model answer:** answered - The retrieved metadata shows two source-specific location tables. In pg-main namespace public, table location (object:46db43985f4307a123288605607dc896) contains location fields such as addressfull (field:f30788483ff4f1db25789c625862549f), city (field:46ee056cc036c6d85f6c8458a3d5293d), street (field:b8f5ded6c3b317cad0dfac8d63ae80ba), postalcode (field:07881a985e5af07193a5165f89d2df8c), country (field:8276e5ecfe3a91c3c7925fe1279ad701), latitude (field:3ef7953d5e6d306b98778ceaba6c6581), and longitude (field:8cc26e5b0efdb149aeb64a8261c1e822), and it carries the company identifier in companyid (field:8d39aa813552fb804eb90af7e14f1221, text). In ch-main namespace testzoominfo_clone2, table Location (object:50cd5e5900edd832934fb32887fcce5e) contains location fields such as Street (field:b977254ef8f73aba8c968a0d6aff6324), City (field:eeb837e2c47fa1c228f0851018538b42), State (field:cd6d60490f14286bc01a18c11a92f1ce), Zip (field:44265af0ed655347f064a46986480caf), and CountryCode (field:ba5f408c511bc395d51951c82ceed724), and it carries the company identifier in companyID (field:831df8415aab92eec49c715ed1acbf2a, Nullable(Int64)). The returned relationship metadata does not declare a relationship linking these two source-specific location tables; a relationship between pg-main and ch-main objects cannot be joined in one query.
- **Cited:** pg.location, pg.location.addressfull, pg.location.city, pg.location.street, pg.location.postalcode, pg.location.country, pg.location.latitude, pg.location.longitude, pg.location.companyid, ch.Location, ch.Location.Street, ch.Location.City, ch.Location.State, ch.Location.Zip, ch.Location.CountryCode, ch.Location.companyID
- **This morning:** Right
- **Your grade:** 

## N-I1
- **Question:** Can I match PostgreSQL location rows to ClickHouse Location rows on their location ids?
- **Expected:** No. pg.location.locationid -> ch.Location.locationID is listed FALSE; the two databases share no ids
- **Result code:** answered (83.09 s)
- **Model answer:** Checked the PostgreSQL object object:46db43985f4307a123288605607dc896 and field field:2d7244fee9e418d077b9952dbb3d7d38, the ClickHouse object object:50cd5e5900edd832934fb32887fcce5e and field field:c6fc905c5b7a537250e69c31d1d663f2, and relationship edge:e7e65689bc87e89a4b10d5d0526cf9ff. The relationship is tentative, so it is a candidate from names, types and sometimes sampled values; its evidence is: "name, compatible type, and weak key claim (source sorting or primary key, uniqueness unproven); parent sampled, a zero is not a measurement". It is also a relationship between objects from two different sources (pg-main and ch-main), so it cannot be joined in one query. The metadata does not show a declared, proven match.
- **Cited:** pg.location, pg.location.locationid, ch.Location, ch.Location.locationID, relationship.location -> Location (locationid -> locationID)
- **This morning:** Partial (owner)
- **Your grade:** 

## N-I2
- **Question:** Is pg.companyinvestor.investorcompanyid a reference to pg.company.companyid?
- **Expected:** No. Listed FALSE; the catalogue cannot say which table the investor company id refers to (no investor company table)
- **Result code:** NOT_ESTABLISHED_BY_CATALOGUE (148.9 s)
- **Model answer:** not_established
- **Cited:** pg.companyinvestor, pg.company, pg.companyinvestor.investorcompanyid, pg.company.companyid, relationship.companytechnographics -> company (companyid -> companyid), relationship.companyemployeecategory -> Company (companyid -> companyID), relationship.company -> Company (companyid -> companyID), relationship.companyexperience -> company (companyid -> companyid), relationship.companyfinancials -> Company (companyid -> companyID), relationship.companyemployeecategory -> company (companyid -> companyid), relationship.companycontact -> company (companyid -> companyid), relationship.companytechnographics -> Company (companyid -> companyID), relationship.companycontact -> Company (companyid -> companyID), relationship.companyfinancials -> company (companyid -> companyid), relationship.companyexperience -> Company (companyid -> companyID), relationship.companyexperience -> person (personid -> personid), relationship.CompanyIndustry -> Company (companyID -> companyID), relationship.companyexperience -> Person (personid -> personID), relationship.Experience -> Company (companyID -> companyID), relationship.companycontact -> companyindustry (companyid -> companyid), relationship.companycontact -> location (companyid -> companyid), relationship.companyindustry -> Company (companyid -> companyID), relationship.companyemployeecategory -> location (companyid -> companyid), relationship.company -> companyindustry (companyid -> companyid)
- **This morning:** NOT_ESTABLISHED
- **Your grade:** 

## N-I3
- **Question:** Can I find the ClickHouse person for a PostgreSQL company experience record via personid?
- **Expected:** No. pg.companyexperience.personid -> ch.Person.personID (and ch.Experience, ch.PersonContact) is listed FALSE; use pg.person within PostgreSQL only
- **Result code:** answered (44.59 s)
- **Model answer:** Yes, the catalogue contains a tentative reference candidate from PostgreSQL company experience records to the ClickHouse Person table via personid. The candidate maps pg-main public.companyexperience.personid to ch-main testzoominfo_clone2.Person.personID, but it is not declared; it is only a tentative candidate based on the metadata evidence. A relationship between pg-main and ch-main objects cannot be joined in one query.
- **Cited:** pg.companyexperience.personid, ch.Person.personID, relationship.companyexperience -> Person (personid -> personID)
- **This morning:** Partial (owner)
- **Your grade:** 

## N-I4
- **Question:** Between ch.Experience.jobTitle and ch.Experience.title, which should I use for a person's job title?
- **Expected:** Both are Nullable(String) in the same table; the catalogue does not state the difference. A good answer lists both, says the difference cannot be established from names and types, and suggests title might be a shorter or alternate form only as a guess
- **Result code:** NOT_ESTABLISHED_BY_CATALOGUE (30.34 s)
- **Model answer:** not_established
- **Cited:** ch.Experience, ch.Experience.jobTitle, ch.Experience.title
- **This morning:** Right
- **Your grade:** 

## N-I5
- **Question:** Between pg.company.entityname, fullname and description, which column holds the company's name?
- **Expected:** entityname and fullname are both text name candidates; description is a free-text description, not a name. Which is canonical cannot be established
- **Result code:** NOT_ESTABLISHED_BY_CATALOGUE (38.11 s)
- **Model answer:** Based on the retrieved catalogue metadata, it is not established which of the three columns holds the company’s name. The metadata lists entityname, fullname, and description as text columns in pg-main.company, but it provides no descriptions, comments, sample values, or mappings that identify which one stores the company's name. Related metadata for the ch-main Company table also does not establish a mapping for these pg-main columns.
- **Cited:** pg.company, pg.company.entityname, pg.company.fullname, pg.company.description, ch.Company, ch.Company.companyName
- **This morning:** NOT_ESTABLISHED
- **Your grade:** 

## N-I6
- **Question:** In the pg.company and pg.location relationship on companyid, which table is the parent?
- **Expected:** Cannot be established. Both pg.company.companyid -> pg.location.companyid and pg.location.companyid -> pg.company.companyid are listed TRUE; companyid is the shared key. pg.company is the natural entity table by its columns
- **Result code:** answered (101.43 s)
- **Model answer:** Tentatively, the parent is pg.company (public.company) in the pg.location → pg.company reference candidate on companyid. This is a tentative candidate based on names, compatible types, and sampled values, not a declared relationship.
- **Cited:** relationship.location -> company (companyid -> companyid), pg.company, pg.location, pg.location.companyid, pg.company.companyid
- **This morning:** Partial (owner)
- **Your grade:** 

## N-I7
- **Question:** Does the catalogue tell me which company is the parent and which is the subsidiary in pg.companyrelation?
- **Expected:** Cannot be established. The columns are companyid, relatedcompanyid, relationtype (text), types, summary. Direction is not stated. Also pg.companyrelation.relatedcompanyid -> pg.company.companyid is listed FALSE, so that column is not a confirmed reference to pg.company
- **Result code:** NOT_ESTABLISHED_BY_CATALOGUE (97.16 s)
- **Model answer:** Based on the retrieved catalogue metadata, it is **not established** which company is the parent and which is the subsidiary in `pg.companyrelation`.

The metadata confirms the `companyrelation` table (`object:d5f2735a9a250bd27b6aa437997b18a9`) contains fields like `companyid` (`field:c48ca6e0a92eee71fc22cf1439eb34c0`), `relatedcompanyid` (`field:401a1c792968a2ca378dc86a63994c4b`), and `relationtype` (`field:cf5c43e09845156183b9ed66db7b589f`). However, there are no descriptions, value domains, or explicit mappings in the returned metadata that define the parent/subsidiary roles for these fields.

The relationship candidates involving this table are all **tentative** (based on names, types, and sometimes sampled values, not declared lineage) and do not provide parent/subsidiary semantics:

1.  `edge:f4a07bf2a888068903e05a843fbda6b9` (Tentative)
    *   **Evidence:** `"name, compatible type, and uniqueness measured from source statistics, not declared; value overlap 100% of 245 sampled child values"`
    *   This candidate only links `companyrelation.companyid` to `company.companyid`.

2.  `edge:f651bc7a8701e90bd91041bae8576a3a` (Tentative)
    *   **Evidence:** `"name, compatible type, and weak key claim (source sorting or primary key, uniqueness unproven); parent sampled, a zero is not a measurement"`
    *   This candidate links `companyrelation.companyid` to `Company.companyID` in a different source (`ch-main`).

The catalogue metadata does not specify whether the `companyid` is the parent or the subsidiary, nor how `relatedcompanyid` should be interpreted in this context.
- **Cited:** pg.companyrelation, pg.companyrelation.companyid, pg.companyrelation.relatedcompanyid, pg.companyrelation.relationtype, relationship.companyrelation -> company (companyid -> companyid), relationship.companyrelation -> Company (companyid -> companyID)
- **This morning:** Right
- **Your grade:** 

## N-I8
- **Question:** Which PostgreSQL column holds a person's email address?
- **Expected:** No dedicated person email column exists: pg.person has name, role, summary, types, confidence only. pg.companycontact.contactstring with contacttype is attached to companies, not to persons. The catalogue cannot establish that either holds a person's email
- **Result code:** NOT_ESTABLISHED_BY_CATALOGUE (47.52 s)
- **Model answer:** not_established
- **Cited:** pg.person, pg.person.personid, pg.person._version, pg.person.confidence, pg.person.name, pg.person.role, pg.person.summary, pg.person.created_at, pg.person.types
- **This morning:** Right
- **Your grade:** 

## N-I9
- **Question:** Can I tell which ClickHouse Location row is a company's current location?
- **Expected:** No. ch.Location has no current or primary flag (columns: Zip, companyID, Street, CountryCode, locationID, State, City, _version). Only pg.location has iscurrent and isprimary (text), and those cannot be applied to ClickHouse because the databases share no ids
- **Result code:** NOT_ESTABLISHED_BY_CATALOGUE (75.04 s)
- **Model answer:** No. The ClickHouse Location table has no column or relationship metadata indicating which row is the current location for a company.
- **Cited:** ch.Location, ch.Location.locationID, ch.Location._version, ch.Location.City, ch.Location.State, ch.Location.Street, ch.Location.Zip, ch.Location.companyID, ch.Location.CountryCode, ch.Company, ch.Company.companyID
- **This morning:** Right (owner)
- **Your grade:** 

## N-I10
- **Question:** In ClickHouse, how can I find the company name for a row in PersonContact?
- **Expected:** PersonContact has no company id (columns: freeMails, personID, _version). Go two hops: ch.PersonContact.personID -> ch.Experience.personID, then ch.Experience.companyID -> ch.Company.companyID; final ch.Company.companyName. A person may have several experience rows
- **Result code:** NOT_ESTABLISHED_BY_CATALOGUE (207.59 s)
- **Model answer:** I checked the ClickHouse object PersonContact (object:5696f20c5f1a765ab463bd4aaa930e13) and its returned fields: _version (field:4fdfb2ce09a237e2342293753b0c8e6e), freeMails (field:7b45c137b580b26cc328fc4aa0dbefc6), and personID (field:df329c939e41b4df0640f5f9fbaa1036); there is no companyID or companyName field. I also checked the Company object (object:b3653bad92366ef20567af26b5ca7ce6), which has companyName (field:acb560e6086a0d2545806ce87102ef28) and companyID (field:861fe226e14fcf38588592aad6e62355). The relationship list shows only one edge from PersonContact, a tentative candidate to Person via personID/personID (edge:fddd5f6f08ae38306830023c32fcde14). Evidence sentence: name, compatible type, and weak key claim (source sorting or primary key, uniqueness unproven); value overlap 0% of 4042 sampled child values. No direct PersonContact-to-Company relationship is returned. Other Company-related candidates are separate: Experience-to-Company via companyID/companyID (edge:2623e3f6bf42984777c7fae4599cd25a); evidence sentence: name, compatible type, and weak key claim (source sorting or primary key, uniqueness unproven); value overlap 1% of 2702 sampled child values. There is also a tentative Experience-to-Person candidate (edge:2569a3532b8abeff3a3d90010ee81262); evidence sentence: name, compatible type, and weak key claim (source sorting or primary key, uniqueness unproven); value overlap 24% of 4096 sampled child values. These are candidates and do not by themselves establish a ClickHouse-only path from a PersonContact row to Company.companyName; a pg-main companycontact-to-Company candidate (edge:d10e95facec4fa246a9199b0b29f899b) spans pg-main and ch-main and cannot be joined in one query.
- **Cited:** ch.PersonContact, ch.PersonContact._version, ch.PersonContact.freeMails, ch.PersonContact.personID, ch.Company, ch.Company.companyName, ch.Company.companyID, relationship.PersonContact -> Person (personID -> personID), relationship.Experience -> Company (companyID -> companyID), relationship.Experience -> Person (personID -> personID), relationship.companycontact -> Company (companyid -> companyID)
- **This morning:** NOT_ESTABLISHED
- **Your grade:** 
