# Lane R report (B1, B3/D6a, D6b, B4, requester half of C1)

Files changed: `packages/requester/src/dbig_requester/{answer,search}.py`, `tests/test_requester.py`;
only xfail marks (and the then-unused `pytest` import in test_b1_schema.py) removed in
`tests/catalogue_fixes/{test_b1_schema,test_search_split_stem_stop,test_c1_error_codes}.py`.
`config.py` untouched. Checks after the last item: ruff clean, mypy clean, pytest 593 passed, 68 skipped,
15 xfailed (none in my three test files except the non-strict review-schema finding).
Corpus-name grep over packages/*/src prints nothing; no new numeric literal in the diff.

## B1 (answer.py)
`Answer.answer` lost `Field(max_length=2000)`; a `field_validator` enforces `ANSWER_MAX_CHARS = 2000`
after parsing (2000 accepted, 2001 refused, text not echoed). The sent schema has no
maxLength/minLength/maxItems/minItems (recursive walk test passes; stub rejecting bounds now gets a cited answer).

## B3/D6a (search.py)
`_words()` splits at lower->UPPER, the end of an upper-case run before a capitalised word, and
letter/digit boundaries; `URL`/`ID` stay one word. `index_snapshot` writes the whole name, then its words
when case/digits split it (snake_case unchanged). Index format versioned: `INDEX_VERSION` ("3") stored in
`meta.index_version`; `search` raises `ValueError("STALE_INDEX")` for an index without or with another
version (the API turns that into 409 STALE_INDEX; remedy is `dbig-requester index`). I refused rather than
auto-rebuilt because a search must not write to the state dir: lead can switch to auto-rebuild (about 6 lines)
if wanted. ponytail: case read for ASCII letters only (comment in code).

## D6b (search.py)
Chose a plural fold, NOT the FTS5 porter tokenizer: I tried porter first and it fails
`test_an_exact_name_query_keeps_its_rank` (porter drops the exact form, so the table-owned
`industry_name` outranks the field literally named `industries`). Fold `_singular`: `-ies`->`y`, `-sses`->`ss`,
other trailing `s` dropped except `ss/us/is`. Index adds the singular of every plural word (name, context,
description); a query term that is a plural becomes `("plural"* OR "singular")` (singular as whole word, not prefix,
to avoid `gas`->`ga`* noise). Ceiling (ponytail comment): `-es` words other than `-sses` lose only `s`
(`boxes`->`boxe`, `box` does not find `boxes`); a singular query does not find a run-together
name like `industrycode` through a plural query. Unmerged words guard (`invoice`/`invoke`) passes.

## B4 (search.py)
`column table field number holds store stored` added to `STOPWORDS` (ponytail comment names the ceiling and the
deferred alternative). Stopword check is on the word as asked AND its fold, so `tables`/`numbers` count as schema
words in `ask` (match="any"). That resolves the known conflict without conflict: the D6b test uses explicit
(match="all") search where stopwords do not apply, and it passes. Consequence for the lead (C3 / DEC-7): a
natural question "which ClickHouse tables ..." gets no retrieval from the word `tables`; the other words must match.
If the lead prefers `tables` retrieving in `ask`, change the stopword check to the raw word only (one line);
B4 test still passes (it uses singular schema words) but the hole stays open for plurals.

## C1 requester half (answer.py)
Codes derived from types/structure only: `APITimeoutError`->MODEL_TIMEOUT (tested before its parent),
`APIConnectionError`->MODEL_UNREACHABLE, `APIStatusError` with status < 500->MODEL_REJECTED_REQUEST,
`finish_reason == "length"` (checked before parsing)->MODEL_TRUNCATED, empty or unknown citations->MODEL_INVALID_CITATION.
Everything else (malformed JSON, lineage claim, 5xx, missing key, any other exception) stays
`MODEL_UNAVAILABLE_OR_INVALID`, kept as the umbrella pending DEC-11. `tests/test_requester.py`: fake responses
gained `finish_reason="stop"` (real responses always carry it); the invented-citation assertion now expects
MODEL_INVALID_CITATION, plus a lineage-claim case expecting the umbrella. Builder half untouched.

## Could NOT verify
Real llama.cpp grammar acceptance, busy-server timeout behaviour on the real server, retrieval recall on the
real catalogue, real 5xx behaviour of a shared server (mapped to the umbrella by design, unverified).
`export_schemas.py --check` ran clean (no schema changed).
Transient: one full run mid-way showed reviewer tests failing while another lane was editing reviewer.py; they
passed on re-run and in the final full run.

# Round 1 corrections (lead review of phase1.verdict.md)

Checks: ruff clean for my files (remaining 9 E501 are in the lead's `attack_*.py`), mypy clean, full pytest
597 passed, 68 skipped, 14 xfailed, 1 xpassed (the non-strict review-schema finding, lead's); attack_search.py and
attack_c1_b1.py all 103 pass. No attack test edited.

## D6b redesign (search.py)
- Index no longer adds the singular to every plural (that made `invoices` rank with `invoice`). It adds only the
  `-y` form of an `-ies` word (`industries` also holds `industry`); every other singular is already a prefix of its plural.
- Query: a plural term is `("plural"* OR "candidate" ...)`; candidates come from `_singulars`: drop `s`; for `-ies` also
  `y`; for `-es` after a sibilant (s x z ch sh) also drop `es`. So indexes/statuses/boxes/taxes/batches/matches/prefixes/
  movies/cookies/industries/categories all find their singular. Each candidate is a whole word, not a prefix.
- False merges cut by two rules, chosen over a vocabulary check because `new_flag` IS in the index, so no vocabulary test
  can reject `news`: `MIN_SINGULAR_LENGTH = 3` (pos, gas) and the finite `NON_PLURALS` list (news lens series species bias
  alias canvas atlas). Ceilings (ponytail comment): a two-letter singular (`ids`->`id`) is not folded; a non-plural
  word ending in s that is missing from the list merges with its shorter word.
- Exact-name rank: `ORDER BY e.name = <query> COLLATE NOCASE DESC, rank, ...` for any query with terms. Needed for
  `invoices` (plural query also matches the singular table, bm25 tie); `invoice` -> `invoice` first now holds too.
- Stopword check uses `_singulars` (so `tables`, `stores`, `numbers` remain schema words; the trade-off in the verdict
  stands and is deliberate).
- D-2 (not asked, cheap): `_broken()` reads case with `str.isupper/islower`, so accented and Cyrillic capitals split
  (3 attack cases now pass); the ASCII-only ceiling is gone. `INDEX_VERSION` stays "3": the index layout changed from the
  previously committed version-3 content (D6b index additions removed), so BUMP IT if any version-3 index was built
  from the earlier WIP commit; not bumped here because no release carried it (lead decides).

## B3 stale index typed (search.py, answer.py)
`StaleIndexError(OperatorError)` (subclass of ValueError; message is the bare code `STALE_INDEX`) now raised by both the
digest and the version check. Surfaces: `search` raises it (HTTP search route: 409 `{"code":"STALE_INDEX"}` unchanged; CLI
`search`/`index` commands: Click error instead of a traceback, via OperatorError); `answer_question` catches it and returns
`{"error": {"code": "STALE_INDEX"}}`, so CLI `ask` prints JSON and the HTTP question route answers 503 with that code
(api.py is not mine; 409 like the search route would be more consistent: lead's call). Tests added in
tests/test_requester.py for both version and digest staleness through ask and the HTTP search route.

## C1 addendum (answer.py)
`APITimeoutError` whose `__cause__` class name is `ConnectTimeout` -> MODEL_UNREACHABLE, other timeouts MODEL_TIMEOUT,
mirroring reviewer.py (class name, no httpx import in src). 5xx stays the umbrella in ask. Test added (httpx is imported
by the test only, as a transitive dependency of openai).

## Not verified
Live behaviour of any of it on the real catalogue; whether the real client wraps a connect timeout with `ConnectTimeout`
as `__cause__` (emulated with the same class chain in the test).
