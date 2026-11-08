# Gate 2 (sampling on, sample_rows 100, key id run-20261014), run 2026-10-17 on 444ff9c

Counts and names only. Raw: gate2-commands.txt, candidates-on.json, gate2-overlap-pairs.json, gate2-overlap.json, candidates-{off,on}-diag.json. `tokens.jsonl` stays local (ignored).

| check | result |
|---|---|
| pipeline | PASS: collect 0.23 / 0.19 / 0.66 s, assemble `complete`, 0 diagnostics, `partial_reasons` null, render 82 candidates, index 0.54 s |
| tokens produced | PostgreSQL 106, MongoDB 450, **ClickHouse 0** (the ClickHouse collector has no token code; LOCAL_RUN.md step 8 says so; LOCAL_VERIFY step 5 assumed all three) |
| `dropped_zero_overlap_candidates` | 25; candidates that disappeared 23; the other 2 were `dropped_unmeasured_key_name` in the off run (3 -> 1), so 23 + 2 = 25: counter correct (proved with a diagnostic assemble at `top_k_per_field = 100`: same 23 / 25) |
| `dropped_unmeasured_key_name_candidates` | 1 (off run 3) |
| **the 16 wrong 0% candidates gone** | **FAIL: 9 of 16 gone** (#9, #20 by DEC-1; #6, #10, #15, #17, #18, #19, #21 by measured zero). Still present: #3, #4, #5, #13, #14, #16 (ClickHouse children: no tokens, "values not compared") and #1 (`sample_too_small`) |
| **the 5 right candidates kept** | **FAIL: 3 of 5** (#2, #8, #11 kept; **#7 and #12 dropped**) |
| **no real FK dropped** | **FAIL: 12 candidates the user marked true were dropped** (#7, #12, `diffbot_CompanyInvestmentRound.investmentID`, `pg.companyfinancials.companyid -> pg.company.companyid`, and 8 diffbot -> `location`/`companyindustry`). Independent overlap (child first 5000 distinct vs all parent distinct, in memory): 95-100% for all 12 (e.g. 17/17, 29/29, 11/11). Cause: both sides sampled from the first 100 rows; 100 of 3940 parent ids miss the sampled child ids; `parent_truncated` is false for a row-limited sample, so the "capped parent never drops" guard does not apply |
| dropped and false (11) | correct: independent overlap 0 for all 11 (0/10 to 0/5000) |
| kept sample (6, all true) | independent overlap 98-100% (K1-K6) |
| 21-27% ClickHouse pairs kept with numbers | **FAIL (numbers)**: the 5 proposed CH-CH pairs and Z1-Z11 are kept but show `no_value_tokens`, no number (no ClickHouse tokens) |
| J1, J2, X1 | PASS: all three edges present (PostgreSQL, measured overlap) |
| ordinary shared names (email, status) | PASS: none linked |
