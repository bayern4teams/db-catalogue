# db-catalogue: improvement report

**Date:** 2026-11-09
**Status:** Proposal. No application code changed.
**Base:** `main` at 3ff51f3, branch `docs/improvement-report`.
**Done so far:** §0 and Part 1. Parts 2, 3 and 4 and the recommended sequence wait for the lead's go-ahead.
**Evidence rule:** every item below was checked against the code at 3ff51f3, a measurement or a named run file. `not checked` marks anything that was not. Line numbers appear only for artifacts, where they do not move.

## Table of contents

- [0. Confirmed constraints](#0-confirmed-constraints)
- [Part 1: Defects and fixes to apply now](#part-1-defects-and-fixes-to-apply-now)
  - [Summary](#summary)
  - [A. Release and test tooling](#a-release-and-test-tooling)
  - [B. Answer quality and privacy](#b-answer-quality-and-privacy)
  - [C. Relationship inference and collection](#c-relationship-inference-and-collection)
  - [D. Documentation and repository](#d-documentation-and-repository)
  - [E. Live checks not yet run](#e-live-checks-not-yet-run)
  - [F. Older open questions](#f-older-open-questions)
  - [G. Already fixed: do not touch](#g-already-fixed-do-not-touch)
  - [H. Decisions needed](#h-decisions-needed)
- Part 2: Research, cited (not started)
- Part 3: Target architecture (not started)
- Part 4: Appendices (not started)
- Recommended sequence (not started)

---

## 0. Confirmed constraints
<!-- verified: 3ff51f3 -->

| # | Constraint | Source |
| --- | --- | --- |
| Q1 | db-catalogue becomes a service that other programs call by API. | Owner, 2026-11-08 |
| Q2 | All five packages (collectors, catalog-builder, requester, planner, contracts) become API services, one Docker container each. | Owner, 2026-11-08 |
| Q3 | Extend the existing optional HTTP services; do not build a second one. | AGENTS.md F5; task brief |
| Q4 | Local network only: no inbound from outside the LAN, no outbound beyond it. The remote database host is inside the LAN and counts as local. | Owner, 2026-11-08 |
| Q5 | Research covers `TODO.md`, the reports of prompts 1 to 3, live findings F5-F13, the later remote run, speed and precision. | Owner, 2026-11-08 |
| Q6 | This report is a proposal on its own branch. No merge until the owner writes `merge it`. | AGENTS.md B1 |
| Q7 | No cloud model fallback. The model is a private vLLM endpoint. Raw values, prompts and replies stay out of traces. | AGENTS.md section 4 |
| Q8 | A model never declares lineage and never gets source credentials or database tools. | AGENTS.md section 4 |

---

## Part 1: Defects and fixes to apply now
<!-- verified: 3ff51f3 -->

### Summary

| ID | Problem | Status | Severity | Who acts |
| --- | --- | --- | --- | --- |
| A1 | `release.yml` cannot succeed | Open | High | Implementer |
| A2 | 37 attack tests fail; `TODO.md` says 23 | Open | High | Implementer, then owner on each test |
| A3 | The documented setup misses package drivers | New, open | Medium | Implementer |
| A4 | GitLab `source-integration` needs a privileged runner | Not checked on the runner | Medium | Owner |
| A5 | `serve` has no end-to-end bind test | Open | Medium | Implementer |
| A6 | `test_live_collectors` cannot re-run on the same containers | Open | Low | Implementer |
| B1 | SDK DEBUG log can hold question and reply text | Open | High | Owner decides |
| B2 | An answer with no citation is refused with no retry | Open | High | Implementer |
| B3 | Paraphrased lineage claims pass the check | New, open | High | Implementer |
| B4 | Model does not chain edges through retrieved tables (N-I10) | Open | Medium | Implementer |
| B5 | A question about several sources can retrieve only one (N-C1) | Cause not replayed | Medium | Implementer |
| B6 | Questions about types find nothing (F13) | Partial | Medium | Owner decides |
| B7 | "List all" answers are cut at 10 hits | Open | Medium | Owner decides |
| B8 | `requester index` may show a traceback on a signed snapshot | Not reproduced | Medium | Implementer |
| C1 | 18 false pairs remain against ClickHouse parents | Partial | Medium | Owner decides |
| C2 | Query budget cuts format profiles, and the message gives no remedy | Open | Medium | Implementer |
| C3 | Cross-source candidates stay unverified when token keys differ | Open, by design | Medium | Owner config |
| C4 | Name-rule limits: differing names and differing type families | Open, by design | Low | Owner decides |
| C5 | Three inference defects from the attack tests | New, open | Medium | Implementer |
| D1 | `docs.yml` skips `docs/*.md`: 92 Vale errors | Open | Medium | Implementer |
| D2 | `TECHNICAL_IMPLEMENTATION.md` stamps missing | Open | Low | Implementer |
| D3 | Live-test report says 28 and 40 | Open | Low | Implementer |
| D4 | `.claude/skills/ponytail*` differ from v5.3.3 | Open | Low | Owner decides |
| D5 | `.handoff/` holds 37 older tracked files | Open | Low | Owner decides |
| D6 | `TODO.md` is out of date | Open | Low | Implementer |
| E1 | Fixes not re-run live; run incomplete | Open | Medium | Owner yes per run |

### A. Release and test tooling

#### A1: `release.yml` cannot succeed
<!-- verified: 3ff51f3 -->

`.github/workflows/release.yml` runs `test -d src`, but the repository has no root `src` folder, so the job stops before it publishes. It also runs plain `uv build`, which does not build all workspace packages.

**Fix:** delete `test -d src`; change the build step to `uv build --all-packages`.
**Verify:** `actionlint .github/workflows/release.yml`, then `uv build --all-packages` with a memory cap.

#### A2: 37 attack tests fail, and the record is wrong
<!-- verified: 3ff51f3 -->

Measured on 2026-11-09: 29 `attack*.py` files, run one at a time with a 6 GB cap and full package extras. Files outside the eight below all pass. The two files whose failures came from missing drivers then passed (see A3).

`TODO.md` says 23 failures, with "ids in the P7 ledger." The ledger holds no ids; it says 37, and the id list was in an ignored file. The measured count is 37. The figure 23 has no source: `not checked` beyond that.

| File under `tests/catalogue_fixes/` | Failures | What it shows | Assessment |
| --- | --- | --- | --- |
| `attack2_d3_lowercase.py` | 27 | Expected lowercase `vendorid` to link to `vendor`; found no pair | Probably superseded by the later DEC-1 and DEC-16 rules. `not checked` per test. Must be confirmed before anything changes (L1). |
| `attack3_b_answering_retrieval.py` | 6 | 5 lineage paraphrases accepted (B3); 1 DEC-7 probe | B3 is a real defect. DEC-7 probe `not checked`. |
| `attack3_a_dec1.py` | 1 | Two candidates for one child and parent table | C5 |
| `attack3_a_dec2.py` | 1 | A column measured 0.2 unique is still treated as a key parent | C5 |
| `attack3_a_dec4.py` | 1 | `KeyError: 'warnings'` on a three-hop path | C5 |
| `attack_search.py` | 1 | Search for `pos` returns `po_number` | Minor retrieval noise |

**Fix:** record the 37 ids and a verdict for each in `TODO.md`. Fix the real defects. If a test contradicts a later decision, stop and ask (L1). Also, `tests/phase7/attack7_p7_4.py::test_debug_logs_bare_reply` asserts only the re-send, so it follows B1.
**Verify:** the batch run reports the same ids as the ledger.

#### A3: The documented setup misses package drivers (new)
<!-- verified: 3ff51f3 -->

AGENTS.md section 5 says `uv sync --locked --all-extras --dev`. Measured: this leaves `pg8000` and `clickhouse_connect` uninstalled, and 17 attack tests fail with `ModuleNotFoundError`. With `--all-packages` added they pass or skip (`attack3_c_e1_key.py` 46 passed; `attack6_f7_f9.py` 13 passed, 12 skipped). `.github/workflows/ci.yml` uses both forms, in different jobs.

**Fix:** use `uv sync --locked --all-packages --all-extras --dev` in AGENTS.md section 5. Check which CI job needs which form before changing `ci.yml`.
**Verify:** on a fresh environment, both attack files show no import error.

#### A4: GitLab `source-integration` needs a privileged runner
<!-- verified: 3ff51f3 -->

`.gitlab-ci.yml` runs `docker:29.9.2-dind`, and its header asks for a privileged Linux Docker runner. The failure itself is not recorded in the repository. Runner state and pipeline status: `not checked`.

**Fix:** the owner sets `privileged = true` for that runner in its `config.toml`. This is outside the repository, so B10 needs the runner config kept in a committed file or documented.
**Verify:** `source-integration` is green for both matrix legs.

#### A5: `serve` has no end-to-end bind test
<!-- verified: 3ff51f3 -->

`TODO.md` records that `create_app` and the bind refusal are tested apart, never as one served request. This matters more once services are the product. Part 3 will include it in the service acceptance tests.

**Verify:** a test binds a port, sends one authenticated request and reads the reply.

#### A6: `test_live_collectors` cannot re-run on the same containers
<!-- verified: 3ff51f3 -->

`tests/test_live_collectors.py` creates its schema without dropping it first. CI uses fresh containers, so only local re-runs are affected.

**Fix:** drop the schema in the test's setup.

### B. Answer quality and privacy

#### B1: SDK DEBUG log can hold question and reply text
<!-- verified: 3ff51f3 -->

`packages/requester/src/dbig_requester/answer.py::answer_question` re-sends a bare reply with `CORRECTION`. Nothing in `packages` or `scripts` sets a level for the `openai`, `httpx` or `httpcore` loggers. `TODO.md` records that the attack test saw both texts in the DEBUG log. AGENTS.md forbids raw prompt or reply tracing until canary tests prove masking. This needs DEBUG to be switched on, so the default level does not leak.

**Options:** see H1.
**Verify:** a canary question and reply do not appear in captured logs at DEBUG.

#### B2: An answer with no citation is refused with no retry
<!-- verified: 3ff51f3 -->

In the 2026-10-28 run, 4 of 40 answers failed with `MODEL_INVALID_CITATION`, check `none` (N-D9, N-S8, N-S9, N-I3). In `answer.py::answer_question` the correction retry covers only `_unexplained`; a reply with no citations raises at once.

**Fix:** treat "answered, no citation" as a second retry reason, inside the same loop and `answer_retries` bound. It needs its own fixed correction text, because `CORRECTION` says the verdict had no explanation.
**Verify:** a stub model returns no citations, then valid ones; the answer succeeds. Replay the four questions.
**Expected gain:** up to 4 of 40 refusals become answers. Not measured.

#### B3: Paraphrased lineage claims pass the check (new)
<!-- verified: 3ff51f3 -->

`answer.py::LINEAGE_CLAIM` matches only `derived from`, `flows into` and `writes to`. The attack tests show that "gadget is fed by part," "populated from," "loads into," "built from" and "the lineage of gadget is part" all pass. Claims of lineage are a stated limit of the product (AGENTS.md: the model has no authority to declare lineage).

A longer word list is a tuned fix and will miss the next phrase. **Direction:** have the reply carry a structured claim type, checked against the cited edge kinds, and keep the pattern as a backstop. Part 2 will compare options.
**Verify:** the five attack cases are refused, and ordinary "joins to" answers are not.

#### B4: The model does not chain edges through retrieved tables (N-I10)
<!-- verified: 3ff51f3 -->

N-I10 asked for the company name of a `PersonContact` row. `answers-both.json` on `fix/phase7` shows the reply citing all three edges, then saying no path exists. So retrieval worked and the model did not chain them. `search.py::bridge_paths` adds path lines only through tables that are not themselves ends; here `Person` and `Experience` were retrieved, so no path line was written. That link between the two is inferred; `not replayed`.

**Fix:** also write the shortest path when the intermediate tables are retrieved, under the same hop limit.
**Verify:** replay N-I10 and check the path line is in the context, then ask once more live.

#### B5: A question about several sources can retrieve only one (N-C1)
<!-- verified: 3ff51f3 -->

N-C1 asked about "both databases." The reply says only `pg-diffbot` was retrieved, and all citations are from it. Top-10 domination by one source fits, but the context was not replayed (`not checked`).

**Fix, if the replay confirms it:** split the limit across sources when a question spans several sources, in `search.py`.
**Verify:** replay N-C1 and count hits per source.

#### B6: Questions about types find nothing (F13)
<!-- verified: 3ff51f3 -->

Retrieval went from 59 of 134 expected names (44%) to 104 of 134 (78%) (`artifacts/run-20261020/step3-summary.md`). N-C4 and N-C7 still return `NO_CATALOGUE_MATCH`, because `search.py::index_snapshot` indexes only `name, context, description`. Adding type text was deferred because "text" matches 94 of 170 fields.

**Options:** see H4.

#### B7: "List all" answers are cut at 10 hits
<!-- verified: 3ff51f3 -->

N-C6 named 2 of 4 numeric columns. `requester/config.py` sets `retrieval_limit` to 10. **Options:** see H5.

#### B8: `requester index` may show a traceback on a signed snapshot
<!-- verified: 3ff51f3 -->

`TODO.md` records this. `cli.py::index` calls `search.py::index_snapshot`, which calls `read_artifact` without a key, and neither handles an exception. Behaviour on a real signed snapshot was not reproduced.

**Fix:** reproduce first (D3). Pass the key from config and report a one-line error with the fix (J11).

### C. Relationship inference and collection

#### C1: 18 false pairs remain against ClickHouse parents
<!-- verified: 3ff51f3 -->

`artifacts/run-20261020/step1-summary.md`: 79 candidates, 61 true, 18 false. All 18 have a ClickHouse parent (14 `Company.companyID`, 4 `Person` or `Location`). A zero overlap can drop a pair only against a complete parent, and only 3 of 38 ClickHouse hash sets are complete, by design (`builder.py`, reason `parent_sampled`). **Options:** see H6.

#### C2: Query budget cuts format profiles, and the message gives no remedy
<!-- verified: 3ff51f3 -->

With the default `max_queries` of 50 (`collectors/config.py::SourceConfig`), the remote `pg-diffbot` run left 78 format profiles unread and the bundle `partial`. The run passed with 200. The message in `runtime.py` says only "format profiles not read: query budget reached."

**Fix:** name the setting and the count needed in the message (J11). Keep the default unless the owner asks.
**Verify:** collect with the default and read the diagnostic.

#### C3: Cross-source candidates stay unverified when token keys differ
<!-- verified: 3ff51f3 -->

The remote run has 3 `unverified_cross_source` candidates, because the two collections used different token keys. **Fix:** collect both sources with one `DBIG_TOKEN_KEY`, which is a configuration step. Part 3 will make it one service-wide secret.
**Verify:** cross-source candidates carry an overlap number.

#### C4: Name-rule limits
<!-- verified: 3ff51f3 -->

`investorcompanyid` to `company.companyid` is not proposed (DEC-20). `ch.Location.companyID` (`Nullable(Int64)`) to `Company.companyID` (`String`) is not proposed (type-family rule). Both are by design. **Fix:** the decisions file (DEC-36) can add such a pair by hand. Value-based discovery is for Part 2.

#### C5: Inference defects from the attack tests (new)
<!-- verified: 3ff51f3 -->

From A2: two candidates for one child and parent table (`attack3_a_dec1.py`); a parent measured 0.2 unique that a weak claim still admits (`attack3_a_dec2.py`); and a `KeyError` on a three-hop path (`attack3_a_dec4.py`). Each needs reading against its DEC entry first: a test may be stale.

**Verify:** each test passes, or the owner rules it stale.

### D. Documentation and repository

#### D1: `docs.yml` skips `docs/*.md`
<!-- verified: 3ff51f3 -->

`.github/workflows/docs.yml` lists only `docs/LESSONS.md` under `docs/`. Measured with `vale --minAlertLevel=error` on 2026-11-09: 92 errors in four files.

| File | Errors |
| --- | --- |
| `docs/ARCHITECTURE_RESEARCH.md` | 70 |
| `docs/THREAT_MODEL.md` | 12 |
| `docs/LOCAL_RUN.md` | 9 |
| `docs/LIVE_TEST.md` | 1 |

**Fix:** fix the errors, then change the list to `docs/*.md`. `markdownlint` and `lychee` on these files: `not checked`.
**Verify:** the same Vale command reports 0; the docs workflow is green.

#### D2: Stamps missing in `TECHNICAL_IMPLEMENTATION.md`
<!-- verified: 3ff51f3 -->

§5-11, 13 and 16 have no `verified` stamp. §4 and 15 are stamped bce5fee, and §14 is f30780c. `TODO.md` also lists README License and Links, `THREAT_MODEL.md`, `LIVE_TEST.md` and `LOCAL_RUN.md`. The owner has chosen not to add a stamp check (commit 3ff51f3).

**Fix:** verify each section against the code, then stamp it. Do this after the service work, which rewrites several sections.

#### D3: Live-test report says 28 and 40
<!-- verified: 3ff51f3 -->

`artifacts/live-test-report.md` line 75 says 28; lines 16, 165, 307 and 324 say 40. Line 165 adds up to 40 (12 + 6 + 10 + 1 + 11). **Fix:** change line 75 to 40.

#### D4: `.claude/skills/ponytail*` differ from v5.3.3
<!-- verified: 3ff51f3 -->

`SKILL.md` differs from `~/Tools/ponytail` in `ponytail`, `ponytail-audit` and `ponytail-review`. The other three skills were not compared. `TODO.md` records the user's earlier "leave them alone." **Options:** see H3.

#### D5: `.handoff/` holds 37 older tracked files
<!-- verified: 3ff51f3 -->

`scripts/ci_check.py` checks only names in the root, so a folder never fails. M1 gives every file a fixed home, and `.handoff/` has none. **Options:** see H2.

#### D6: `TODO.md` is out of date
<!-- verified: 3ff51f3 -->

`TODO.md` has 163 open boxes. About 25 lines describe work that exists in code (checked by grep for existence, not for each clause).

- Line 55 calls `.gitlab-ci.yml` untracked; it is tracked.
- Line 370 says relational connectors do not sample; `runtime.py::_tokenize_clickhouse` and the 2026-10-20 run show they do.
- Line 151 (camelCase keys) is handled by the case-boundary split in `builder.py`.
- Line 21: see A2.

**Fix:** tick the done lines with their proof, and correct these lines.

### E. Live checks not yet run

Each run needs its own owner yes.

| Item | State |
| --- | --- |
| F8 review config | Code fixed: `ReviewConfig.max_tokens` is optional. Review not re-run: the 2026-10-28 run had no review section. |
| F14, F15, F17, F18 | Code on `main`. Not re-checked against their original cases. |
| P7-6 | 1 of 3 repeats done (2026-10-28). Owner grading not done. |
| P7-7 benchmark | Not run. |
| `tests/test_live_model.py` | Written, not run. |
| Deployment items | vLLM and Langfuse deployment, canary traces, GPU model and throughput: not done. Part 3 will cover them. |
| Remote-run files | `artifacts/run-20261028-remote/` exists only on `fix/phase7`, which is not an ancestor of `main`. Its fixes came through squash cf7a5c3. |

Separately, the remote run found a password in a README on the remote host. It is outside this repository. The owner should rotate it.

### F. Older open questions

These are from `TODO.md` and still await an answer. They were not re-tested in this round.

| `TODO.md` line | Question | Proposal |
| --- | --- | --- |
| 87 | DEC-22: UUID and date normalisation for value tokens | Part 2 |
| 150 | A 2-row lookup is unique in every column. Require a minimum row count? | Part 2. Current behaviour not re-tested. |
| 152 | MongoDB views have no UUID and make a catalogue `partial` | Part 2 |
| 153 | Observed joins never become paths | Part 2 |
| 167 | A MongoDB key inside an array cannot be a `$lookup` path | Keep as a stated limit |
| 270, 271 | Observed-SQL sources and their classification | Owner input |
| 396 | Scheduler interface, authentication and key retention | Part 3 |
| 445 | Public endpoint against `private_base_url` | Closed by constraint Q4: keep the control |

### G. Already fixed: do not touch

| ID | Fix | Evidence |
| --- | --- | --- |
| F5 | Zero overlap drops a pair only against a complete parent | `builder.py`; `run-20261020/step1-summary.md`: 0 owner-true dropped, was 12 |
| F6 | 16 of 16 known-wrong pairs gone, 5 of 5 kept; residual in C1 | same file |
| F7 | ClickHouse value hashes | `runtime.py::_tokenize_clickhouse`; 38 hash sets |
| F9 | Probe read cap | `max_threads: 1` in `runtime.py`; 1.10x to 1.31x, was 7.8x |
| F10 | Ordinary names link only to a declared key | `dropped_ordinary_name` in `builder.py` |
| F11 | No PG and ClickHouse join in one query | 2026-10-20 step 3; N-C8, N-S3, N-S7 |
| F12 | `outcome` is a required field | `answer.py`; 0 mismatches, was 4 |

### H. Decisions needed

| # | Decision | Options | Recommendation |
| --- | --- | --- | --- |
| H1 | B1 DEBUG text | (a) cap `openai`, `httpx`, `httpcore` at WARNING where the client is built, and make the test assert the canaries are absent; (b) accept and document in `THREAT_MODEL.md` | (a): it is three lines and matches the AGENTS.md rule |
| H2 | D5 `.handoff/` | (a) move finished files to `docs/archive/` and keep the folder for live handoffs; (b) keep and document; (c) also add a top-level folder list to `ci_check.py` | (a), with (c) only if wanted |
| H3 | D4 ponytail copies | (a) sync to v5.3.3; (b) record the drift | (b), which honours the user's earlier "leave them alone," unless the owner has changed their mind |
| H4 | B6 type questions | (a) map type words in a question to the existing `data_type` filter; (b) index type text; (c) leave | (a): (b) was measured as too broad |
| H5 | B7 "list all" | (a) a list mode that returns every field of the matched tables; (b) raise `retrieval_limit`; (c) leave | (a): (b) makes prompts larger and slower for every question |
| H6 | C1 false pairs | (a) read the full ClickHouse column for a sketch of the parent; (b) leave them labelled `parent sampled` | Decide in Part 2, after its cost is measured |
| H7 | A2 the 27 D3 tests | (a) implementer checks them against DEC-1 and DEC-16 and the owner rules on each group; (b) leave them failing | (a) |
