# Step 2: candidates waiting for the owner's marks (2026-10-20)

44 candidates in `snapshot-probe` that no earlier mark covers. Mark each `true` or `false`. "Real overlap" is my whole-column count of 2026-10-19 (`../run-20261014/lead-review-overlap.json`); "not measured" means the pair was not in that file.

| id | child | parent | real overlap | mark |
|---|---|---|---|---|
| U01 | `mongo.diffbot_CompanyExperience.personID` | `ch.Experience.personID` | 0 of 84 | |
| U02 | `mongo.zoominfo_Experience.personID` | `ch.Experience.personID` | 4262 of 13513 | |
| U03 | `mongo.zoominfo_contact_information.personID` | `ch.Experience.personID` | 1252 of 3777 | |
| U04 | `mongo.zoominfo_staff.personID` | `ch.Experience.personID` | 4262 of 13513 | |
| U05 | `mongo.zoominfo_location.companyID` | `ch.Location.companyID` | 36 of 93 | |
| U06 | `mongo.diffbot_CompanyExperience.personID` | `ch.PersonContact.personID` | 0 of 84 | |
| U07 | `mongo.zoominfo_Experience.personID` | `ch.PersonContact.personID` | 1330 of 13513 | |
| U08 | `mongo.zoominfo_contact_information.personID` | `ch.PersonContact.personID` | 1330 of 3777 | |
| U09 | `mongo.zoominfo_staff.personID` | `ch.PersonContact.personID` | 1330 of 13513 | |
| U10 | `pg.company.companyid` | `mongo.diffbot_Company.companyID` | 22 of 3940 | |
| U11 | `pg.companyindustry.companyid` | `mongo.diffbot_Company.companyID` | not measured | |
| U12 | `pg.location.companyid` | `mongo.diffbot_Company.companyID` | not measured | |
| U13 | `pg.companyexperience.personid` | `mongo.diffbot_CompanyExperience.personID` | 84 of 1215 | |
| U14 | `pg.person.personid` | `mongo.diffbot_CompanyExperience.personID` | 84 of 1215 | |
| U15 | `mongo.diffbot_Company.companyID` | `mongo.diffbot_CompanyIndustry.companyID` | 22 of 22 | |
| U16 | `mongo.diffbot_CompanyExperience.companyID` | `mongo.diffbot_CompanyIndustry.companyID` | 17 of 17 | |
| U17 | `mongo.diffbot_CompanyInvestmentRound.companyID` | `mongo.diffbot_CompanyIndustry.companyID` | 5 of 5 | |
| U18 | `pg.company.companyid` | `mongo.diffbot_CompanyIndustry.companyID` | 22 of 3940 | |
| U19 | `pg.companycontact.companyid` | `mongo.diffbot_CompanyIndustry.companyID` | not measured | |
| U20 | `pg.companyemployeecategory.companyid` | `mongo.diffbot_CompanyIndustry.companyID` | not measured | |
| U21 | `pg.companyexperience.companyid` | `mongo.diffbot_CompanyIndustry.companyID` | 17 of 482 | |
| U22 | `pg.companyfinancials.companyid` | `mongo.diffbot_CompanyIndustry.companyID` | 2 of 29 | |
| U23 | `pg.companyindustry.companyid` | `mongo.diffbot_CompanyIndustry.companyID` | not measured | |
| U24 | `pg.companyinvestmentround.companyid` | `mongo.diffbot_CompanyIndustry.companyID` | not measured | |
| U25 | `pg.companyrelation.companyid` | `mongo.diffbot_CompanyIndustry.companyID` | not measured | |
| U26 | `pg.companytechnographics.companyid` | `mongo.diffbot_CompanyIndustry.companyID` | not measured | |
| U27 | `pg.location.companyid` | `mongo.diffbot_CompanyIndustry.companyID` | not measured | |
| U28 | `mongo.diffbot_CompanyExperience.main_entity_id` | `mongo.diffbot_CompanyIndustry.main_entity_id` | 17 of 17 | |
| U29 | `mongo.diffbot_CompanyInvestmentRound.main_entity_id` | `mongo.diffbot_CompanyIndustry.main_entity_id` | 5 of 5 | |
| U30 | `pg.companyinvestmentround.investmentid` | `mongo.diffbot_CompanyInvestmentRound.investmentID` | not measured | |
| U31 | `pg.companyinvestor.investmentid` | `mongo.diffbot_CompanyInvestmentRound.investmentID` | not measured | |
| U32 | `ch.Experience.companyID` | `mongo.zoominfo_Company.companyID` | 82 of 2109112 | |
| U33 | `ch.Experience.personID` | `mongo.zoominfo_Experience.personID` | 4262 of 12326104 | |
| U34 | `ch.Person.personID` | `mongo.zoominfo_Experience.personID` | 3319 of 11795168 | |
| U35 | `mongo.zoominfo_contact_information.personID` | `mongo.zoominfo_Experience.personID` | 3777 of 3777 | |
| U36 | `mongo.zoominfo_staff.personID` | `mongo.zoominfo_Experience.personID` | 13513 of 13513 | |
| U37 | `ch.Experience.personID` | `mongo.zoominfo_contact_information.personID` | not measured | |
| U38 | `ch.Person.personID` | `mongo.zoominfo_contact_information.personID` | not measured | |
| U39 | `mongo.zoominfo_Experience.personID` | `mongo.zoominfo_contact_information.personID` | 3777 of 13513 | |
| U40 | `mongo.zoominfo_staff.personID` | `mongo.zoominfo_contact_information.personID` | 3777 of 13513 | |
| U41 | `ch.Experience.personID` | `mongo.zoominfo_staff.personID` | 4262 of 12326104 | |
| U42 | `ch.Person.personID` | `mongo.zoominfo_staff.personID` | 3319 of 11795168 | |
| U43 | `mongo.zoominfo_Experience.personID` | `mongo.zoominfo_staff.personID` | 13513 of 13513 | |
| U44 | `mongo.zoominfo_contact_information.personID` | `mongo.zoominfo_staff.personID` | 3777 of 3777 | |
