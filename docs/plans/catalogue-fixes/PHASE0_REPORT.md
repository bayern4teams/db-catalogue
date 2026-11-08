# Phase 0 report

Baseline: CI green on the branch (run 38256268573); `pytest` 478 passed, 68 skipped in cloud; §2.4 grep: 0 hits in `packages/*/src`; ownership table locked in HANDOFF.md.
Fixtures: `tests/catalogue_fixes/` (80 controls pass, 50 xfail, 49 strict). Per-item before-behaviour: `.handoff/PHASE0.before.md` (folded here at Phase 4).

Findings the lead must act on:
- **B1:** over-limit answers are already rejected after parsing; only the schema changes. The builder review schema also sends `minItems`/`maxLength` (non-strict test); verify against llama.cpp live (LOCAL_VERIFY).
- **B4 vs D6b:** `table` becomes a stopword, but `tables` must retrieve via stemming. Resolve: apply the stopword check to the exact question word before stemming, and keep explicit/search by stem separate. Lead decides when D6b lands.
- **D3:** with a declared parent key the existing `key_name` rule already links `vendorid`; the fix matters for undeclared keys.
- **DEC-11:** `MODEL_UNAVAILABLE_OR_INVALID` is in no schema or doc, so no closed list exists; lead to confirm at C1 (likely moot, keep as umbrella is unnecessary).
- Planner warning for A1 is DEC-4, untested for now.
