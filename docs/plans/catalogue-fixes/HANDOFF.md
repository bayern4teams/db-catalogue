# Catalogue fixes handoff — the only memory that survives a session

Overwrite this file at every commit. A fresh session reads it first, then
[the plan](../CATALOGUE_FIXES_IMPLEMENTATION_PLAN.md). Git is the memory; a session's context is not.

**Lock:** none · **Autonomy:** unattended-through-phase-4 · **Last heartbeat:** 2026-10-12T17:46:17Z

(Lock/heartbeat protocol: plan §7. A session sets `Lock: <ISO time>`, pushes it, and refreshes it at
every commit; a lock older than 75 min is stale. `Autonomy` is `off`, `dryrun` (a tick only records
what works and exits) or `unattended-through-phase-N` (authorises the supervisor to chain phases).
Only the user changes it.)

**Branch:** `feat/catalogue-fixes` (from `claude/gracious-bardeen-x1y6he` at `2e87fa7`, plus the report commit) · **Current phase:** 3 (all remaining items blocked on Decisions)

| item | phase | state | commit | lead verdict |
|---|---|---|---|---|
| P0 baseline, ownership, fixtures | 0 | accepted-cloud | | lead: 80 pass/50 xfail(strict) fixtures verified; CI green on branch (38256268573); grep baseline 0 hits |
| B1 `ask` schema (llama.cpp rejects `maxLength`) | 1 | unverified-live | | |
| B3 + D6a camelCase split in search | 1 | unverified-live | | |
| D6b stemming in search | 1 | unverified-live | | |
| B4 stopwords / no-match guard | 1 | unverified-live | | |
| C1 + D7 distinct model error codes | 1 | unverified-live | | |
| C2 `partial` diagnostics in manifest and CLI | 1 | todo (DEC-11b answered 2026-10-13) | | |
| C3 documentation gaps | 1 | unverified-live | | |
| D1b reasons for empty statistic cells | 1 | unverified-live | | |
| D3 + B5(2) lowercase names (`companyid`) when the stem is a known object | 2 | unverified-live | | |
| A1 + D2 + D9 + A3 overlap labels, measured-zero drop | 2 | unverified-live | | |
| A2 parent column name resembles child's (DEC-1) | 3 | todo (DEC answered 2026-10-13) | | |
| D1a ClickHouse key names, weak parent claim (DEC-2) | 3 | todo (DEC answered 2026-10-13) | | |
| D2(3) dataset groups / forbidden source pairs config (DEC-3) | 3 | todo (DEC answered 2026-10-13) | | |
| D4 + A1(3) planner prefers same-source, warns on unverified edges (DEC-4) | 3 | todo (DEC answered 2026-10-13) | | |
| D5a relationships and evidence in the model context (DEC-5) | 3 | todo (DEC answered 2026-10-13) | | |
| D5b answer rules: `lineage`, uncited "not established" (DEC-6) | 3 | todo (DEC answered 2026-10-13) | | |
| D6c source/engine words, object retrieval, ranking (DEC-7) | 3 | todo (DEC answered 2026-10-13) | | |
| C4 exclude schema-description collections (DEC-8) | 3 | todo (DEC answered 2026-10-13) | | |
| D8 resolve cited IDs to names (DEC-9) | 3 | todo (DEC answered 2026-10-13) | | |
| B2 review in batches (DEC-10) | 3 | todo (DEC answered 2026-10-13) | | |
| B5/X1 `key_name` for measured-unique parents (DEC-12) | 3 | todo (DEC answered 2026-10-13) | | |
| E1 `DBIG_SIGNING_KEY` under 32 bytes: one-line operator error instead of a ValueError traceback (user approved 2026-10-13; found by the lead, outside engineer-fixes.md) | 3 | todo | | |
| Final integration, ponytail audit/debt/gain, final report | 4 | todo | | |

States: `todo` · `in-progress` · `accepted-cloud` (lead accepted, cloud-verifiable parts proven)
· `unverified-live` (accepted but needs the real environment — see LOCAL_VERIFY.md) · `blocked` (needs a user Decision or a missing tool) · `deferred-by-user` (the user answered "no" or "later").

## Decisions (only the user writes the Answer column)

Rules: plan section 2.9. A row with an empty Answer keeps its items `blocked`. Answer with `yes`, `no`, `later`, or a short instruction that changes the proposal. The lead may add rows.

| id | question | recommendation | blocks | Answer |
|---|---|---|---|---|
| DEC-1 | A2: when choosing the parent column, prefer the one whose name matches the child's, and propose nothing if none resembles it? | yes | A2 | yes (user, 2026-10-13, in chat) |
| DEC-2 | D1a: keep ClickHouse primary/sorting key column names (metadata, not values; changes the bundle schema) and treat them as a weak, labelled parent claim? | yes | D1a | yes (user, 2026-10-13, in chat) |
| DEC-3 | D2(3): add an optional config for dataset groups or forbidden source pairs (default none)? | yes | D2(3) | yes (user, 2026-10-13, in chat) |
| DEC-4 | D4/A1(3): planner prefers same-source paths and warns when every edge is an unverified cross-source candidate? | yes | D4 | yes (user, 2026-10-13, in chat) |
| DEC-5 | D5a: pass relationship edges with their evidence status to the answering model (bounded, metadata only)? | yes | D5a | yes (user, 2026-10-13, in chat) |
| DEC-6 | D5b: stop rejecting the word `lineage`; validate claims instead; allow an uncited "not established" outcome with its own code? | yes | D5b | yes (user, 2026-10-13, in chat) |
| DEC-7 | D6c: index source/engine/namespace words, retrieve by object for list questions, rank object hits above generic columns, filter by a named source? | yes | D6c | yes (user, 2026-10-13, in chat) |
| DEC-8 | C4: optional per-source exclude list for schema-description collections (default none)? | per-source list | C4 | per-source exclude list, default none (user, 2026-10-13, in chat) |
| DEC-9 | D8: resolve cited IDs to object and column names in the reply, app-side? | yes | D8 | yes (user, 2026-10-13, in chat) |
| DEC-10 | B2: review candidates in batches (configured size, default 5); no "thinking off" switch because it is unverified for this model? | yes | B2 | yes (user, 2026-10-13, in chat) |
| DEC-11 | C1 (moot: the umbrella code is in no schema or document): keep `MODEL_UNAVAILABLE_OR_INVALID` as an umbrella or replace it, if a schema or document lists it as a closed set? (added only if the lead finds such a list) | decide after evidence | C1 | |
| DEC-12 | B5/X1: let the `key_name` rule apply to a measured-unique parent column with an identical non-generic name (so `classificationid` links to `industryclassification.classificationid`)? The 2026-10-05 decision admitted it only for declared keys. | yes | B5/X1 | yes (user, 2026-10-13, in chat) |
| DEC-13 | Supply chain: `pip-audit` now reports 3 CVEs (CVE-2026-96747/8/9) in locked `pymongo 4.18.1`, fixed in 4.18.2; they were published after the lock and are not caused by this branch. Bump `pymongo` to 4.18.2 in `uv.lock` (patch-level, same `>=4.14,<5` range)? Plan §2.5 forbids dependency changes without approval. Until answered, the `pip-audit` step stays red on this branch and on `main`. | yes (lock-only patch bump) | pip-audit stage of the full check / CI | yes (lockfile-only patch bump to pymongo 4.18.2) (user, 2026-10-13, in chat) |
| DEC-11b | C2: a snapshot manifest cannot list partial reasons in `diagnostics` without a contract change: `read_artifact` (artifact.py ~line 272) requires `manifest.diagnostics` to equal the counts of `diagnostics.jsonl`, and snapshots carry none, so an older reader would reject the new snapshot. Options: (a) new optional manifest field `partial_reasons: list[str]` (additive, `extra=allow`, old readers ignore it; regenerate schemas; golden unchanged for complete snapshots) and render/CLI count = page diagnostics + reasons; (b) reuse `diagnostics` and relax the reader check for snapshots (old readers reject new snapshots). | (a) new optional field `partial_reasons` | C2 | (a) new optional manifest field `partial_reasons` (user, 2026-10-13, in chat) |

Already decided by the user on 2026-10-12 (not open): base the work on the tested branch; `companyid` is read as `company` + `id` only when the stem is a known object; a measured 0-of-N cross-source overlap drops the candidate and an unchecked one is labelled "values not compared"; graft is best effort in the cloud; Phase 1 bugs go ahead.

## Locked file-ownership table (Phase 0)
Phase 0 table (file -> sole owner while its items are open; plan §8/§9 DAG; graph cross-check: graphify built 2029 nodes, graft 1082, cbm 2535):

| file | owner | items (serial order) |
|---|---|---|
| `packages/requester/src/dbig_requester/{answer,search,config}.py`, `tests/test_requester.py` | lane R worker | B1, B3/D6a, D6b, B4, C1 requester half; later DEC-5/6/7/8/9 (lead) |
| `docs/LOCAL_RUN.md`, `docs/LIVE_TEST.md`, `.env.example` | lane D worker | C3 (llama.cpp note last, after B1+C1) |
| `packages/catalog-builder/src/dbig_builder/{reviewer,render,cli,state}.py` | lead | C1 builder half, C2, D1b, then B2 |
| `packages/catalog-builder/src/dbig_builder/builder.py` | lead (serial chain) | D3, A1/D2/D9, DEC-12, DEC-2, DEC-1, DEC-3 |
| `packages/planner/src/dbig_planner/{paths,plans}.py` | lead | D4 (DEC-4) |
| `tests/catalogue_fixes/` | verifier | Phase 0 fixtures (xfail strict until the item lands) |

## Lane plan
Phase 1: lane R and lane D concurrent (disjoint files); lead does C1 builder half, C2, D1b. Phase 2: lead chain only.

## Next action
Phase 3. The user answered DEC-1 to DEC-10, DEC-11b, DEC-12 and DEC-13 on 2026-10-13 (all as recommended; STALE_INDEX stays as built, 503 on the question route and 409 on search) and approved a new item E1. Build in DAG order: DEC-13 first (lockfile-only pymongo 4.18.2 so `pip-audit` is green; record the `uv.lock` diff), then C2 with the `partial_reasons` field (DEC-11b), then the builder chain DEC-12, DEC-2, DEC-1, DEC-3, DEC-4, then the requester chain DEC-7 (with DEC-8), DEC-5, DEC-6, DEC-9, then DEC-10 and E1. Then Phase 4. Remember the `tables` word retrieves again only after DEC-7.

## Notifications
(append `[PHASE n DONE]`, `[BLOCKED]`, `[Catalogue fixes COMPLETE]` entries here; the same prefixes go in commit messages)
- [PHASE 2 DONE] 2026-10-12: D3 and A1/D2/D9 accepted (unverified-live); CI run 38286115142 green in all 7 jobs on the pushed head; ponytail-review of the phase diff: nothing to cut. Phase 3 items are all blocked on Decisions.
- [PHASE 1 DONE] 2026-10-12: B1, B3/D6a, D6b, B4, C1/D7, C3, D1b accepted (unverified-live); C2 blocked on DEC-11b. Full check green locally except `pip-audit` (DEC-13, new pymongo CVEs); CI run 38283628671 green in all 7 jobs on the pushed head; ponytail-review of the phase diff: no deletions required, minor `_singulars`/`_ies_singular` overlap left deliberately (readability).

## Open escalations (worker/verifier results that passed only written tests or one case)
- A1: the 0-of-N drop on a first-rows sample could drop a real FK whose parent ids lie outside the sampled rows (live risk, needs the user's sampling setup); a capped parent never drops. Attack files `tests/catalogue_fixes/attack*_*.py` are not collected by default; run them by path.
- (round 2 done) D6b: fold keeps only `-ies`->`-y` at index time, query tries several singulars, min singular length 3 + short non-plural list (`ids` no longer folds to `id`); INDEX_VERSION is now "4"; `ask` returns `STALE_INDEX`, the question route answers 503 (search route 409): lead call, user may prefer 409. DEC-11 is moot (umbrella code is in no schema/doc; kept for malformed JSON, lineage claim, 5xx, missing key).
- B3/D6a: a stale index (no `index_version`) is refused with `STALE_INDEX` (rerun `dbig-requester index`), not rebuilt, because `search` must not write the state directory. Plan said "rebuilt"; lead accepts the refusal (never misread). Tell the user.
- D6b/B4: the plural fold is not a stemmer (`boxes` -> `boxe`); and `tables`/`numbers` are schema words in `ask` (singular is a stopword), so "tables" alone no longer retrieves in `ask` until DEC-7. Explicit `search` is unaffected.
- Builder review schema still carries `minItems`/`maxLength` (non-strict test); the report says llama.cpp accepted it. Verify live.
- Ticket: `DBIG_SIGNING_KEY` under 32 bytes gives a ValueError traceback, not a one-line error.
