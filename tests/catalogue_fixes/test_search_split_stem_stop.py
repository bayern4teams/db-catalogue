"""B3/D6a (camelCase split), D6b (stemming) and B4 (schema words as stopwords): requester search.

All three items live in `search.py`, one serial lane, so they share a file here. Acceptance from
plan section 9, Phase 1:

* B3/D6a: a query for `revenue` returns `accountRevenue`; snake_case and whole-name queries still
  work; an upper-case run (`ID`, `URL`) is one word; an old index is rebuilt or refused, never
  misread.
* D6b: `industries` returns what `industry` returns, likewise `tables`/`table` and
  `companies`/`company`; unrelated words are not merged; an exact-name query keeps its rank.
* B4: a question whose only matching words are schema words returns `NO_CATALOGUE_MATCH` and the
  model is never called; a column literally named `number` is still found with a table word.

Tests marked xfail fail on the pre-change code; the others are controls that must keep passing.
"""

from __future__ import annotations

import sqlite3

import pytest
from catalogue_fixture_kit import MODEL_KEY, ModelStub, build_snapshot, cite_first
from dbig_requester.answer import answer_question
from dbig_requester.config import AnswerConfig
from dbig_requester.search import SearchRequest, index_snapshot, search


def _state(tmp_path, monkeypatch, tables, name="snapshot"):
    snapshot = build_snapshot(tmp_path, monkeypatch, {"alpha": {"tables": tables}}, name=name)
    index_snapshot(snapshot, tmp_path / f"state-{name}")
    return tmp_path / f"state-{name}"


def _names(state, query, **kwargs):
    return [item["name"] for item in search(state, SearchRequest(query=query, **kwargs))["results"]]


def _ids(state, query, **kwargs):
    return {item["id"] for item in search(state, SearchRequest(query=query, **kwargs))["results"]}


# --- B3 / D6a: camelCase and PascalCase are words -----------------------------------------------

CAMEL = {
    "account": {"fields": [
        ("accountRevenue", "numeric"), ("companyID", "integer"),
        ("accountRevenueIn000s", "numeric"), ("customerURL", "text"),
        ("HTTPServerID", "integer"), ("account_status", "text"), ("ID", "integer"),
        ("companyCode", "text"),
    ]},
}


@pytest.fixture
def camel(tmp_path, monkeypatch):
    return _state(tmp_path, monkeypatch, CAMEL)


def test_a_part_of_a_camel_case_name_finds_it(camel):
    found = _names(camel, "revenue", kind="field")
    assert "accountRevenue" in found and "accountRevenueIn000s" in found


def test_an_upper_case_run_is_one_word_and_splits_from_its_neighbours(camel):
    assert "customerURL" in _names(camel, "url", kind="field")
    found = _names(camel, "server", kind="field")
    assert "HTTPServerID" in found, "HTTPServerID is http + server + id"
    assert "HTTPServerID" in _names(camel, "http", kind="field")
    assert {"companyID", "HTTPServerID", "ID"} <= set(_names(camel, "id", kind="field"))


def test_an_upper_case_run_is_not_split_into_letters(camel):
    """Control, and the guard against over-splitting once B3 lands: `URL` is not u, r, l."""
    assert _names(camel, "rl", kind="field") == []
    assert _names(camel, "tt", kind="field") == []


def test_a_whole_name_query_still_finds_and_ranks_the_exact_name_first(camel):
    assert _names(camel, "companyid", kind="field")[0] == "companyID"
    assert _names(camel, "accountrevenue", kind="field")[0] == "accountRevenue"
    assert _names(camel, "customerurl", kind="field")[0] == "customerURL"


def test_snake_case_still_splits(camel):
    assert _names(camel, "status", kind="field") == ["account_status"]
    assert _names(camel, "account_status", kind="field") == ["account_status"]


def test_a_leading_word_of_a_camel_case_name_already_matches(camel):
    """Control: `company` is the prefix of `companyID`, which worked before the fix."""
    assert {"companyID", "companyCode"} <= set(_names(camel, "company", kind="field"))


def test_an_index_in_the_old_format_is_refused_or_rebuilt_never_misread(tmp_path, monkeypatch):
    """Rewrite the index into the shape the pre-change code wrote (whole lower-cased names in the
    full-text table, no version marker), then search it. Either the fix rebuilds it and `revenue`
    finds `accountRevenue`, or it says STALE_INDEX. What it must not do is answer `[]` as if the
    catalogue had no such column. Limit of this emulation: a fix that stores its version inside
    the `digest` value cannot be emulated from here; the attacker should test that by building the
    index with the parent commit's code."""
    state = _state(tmp_path, monkeypatch, CAMEL)
    database = sqlite3.connect(state / "catalog.sqlite3")
    try:
        database.execute("DROP TABLE fts")
        database.execute(
            "CREATE VIRTUAL TABLE fts USING fts5(id UNINDEXED, name, context, description)"
        )
        database.execute(
            "INSERT INTO fts(id, name, context, description) SELECT id, lower(name), "
            "coalesce(object_name, ''), coalesce(description, '') FROM entries"
        )
        database.execute("DELETE FROM meta WHERE key NOT IN ('snapshot', 'digest')")
        database.commit()
    finally:
        database.close()
    try:
        found = _names(state, "revenue", kind="field")
    except ValueError as error:
        assert "STALE_INDEX" in str(error)
        return
    assert "accountRevenue" in found, "an old index must not silently answer with nothing"


def test_reindexing_over_an_existing_state_directory_works(tmp_path, monkeypatch):
    """Control: the index is rebuilt in place from the same snapshot (what `index` does after an
    upgrade). Nothing may be duplicated."""
    snapshot = build_snapshot(tmp_path, monkeypatch, {"alpha": {"tables": CAMEL}})
    index_snapshot(snapshot, tmp_path / "state")
    index_snapshot(snapshot, tmp_path / "state")
    assert _names(tmp_path / "state", "companyid", kind="field") == ["companyID"]


# --- D6b: stemming ------------------------------------------------------------------------------

STEM = {
    "industry": {"fields": [("industry_name", "text"), ("sector_code", "text")]},
    "company": {"fields": [("company_name", "text")]},
    "audit_log": {"fields": [("table_name", "text"), ("invoke_count", "integer")]},
    "price_table": {"fields": [("amount", "numeric")]},
    "invoice": {"fields": [("invoice_total", "numeric")]},
    "directory": {"fields": [("address_line", "text"), ("status", "text")]},
    "catalog_item": {"fields": [("industries", "text"), ("industry_code", "text")]},
}


@pytest.fixture
def stem(tmp_path, monkeypatch):
    return _state(tmp_path, monkeypatch, STEM, name="stem")


@pytest.mark.parametrize("singular", ["industry", "table", "company", "invoice"])
def test_the_singular_fixture_queries_find_something(stem, singular):
    """Control: the fixture is real, so the plural tests below are not vacuous."""
    assert _ids(stem, singular)


@pytest.mark.xfail(strict=True, reason="D6b not implemented")
@pytest.mark.parametrize(
    ("singular", "plural"),
    [("industry", "industries"), ("table", "tables"), ("company", "companies"),
     ("invoice", "invoices")],
)
def test_a_plural_returns_what_its_singular_returns(stem, singular, plural):
    expected = _ids(stem, singular)
    assert expected <= _ids(stem, plural), f"{plural!r} must find everything {singular!r} finds"


def test_unrelated_words_are_not_merged(stem):
    """Control and guard: `invoice` and `invoke` share a prefix of five letters, not a stem."""
    assert "invoke_count" not in _names(stem, "invoice")
    assert "invoke_count" not in _names(stem, "invoices")
    assert "invoice_total" not in _names(stem, "invoke")


def test_words_that_end_in_s_are_not_mangled(stem):
    """Control: `address` and `status` end in s and are not plurals."""
    assert _names(stem, "address", kind="field") == ["address_line"]
    assert _names(stem, "status", kind="field") == ["status"]


def test_an_exact_name_query_keeps_its_rank(stem):
    """Control: a field literally named `industries` outranks `industry_code` for that query,
    before and after a stemmed form is added beside the exact one."""
    assert _names(stem, "industries", kind="field")[0] == "industries"
    assert _names(stem, "industry_name", kind="field")[0] == "industry_name"


# --- B4: schema words do not count as a match ---------------------------------------------------

SCHEMA_WORDS = ["column", "table", "field", "number", "holds", "store", "stored"]

NOISE = {
    # A schema-description collection, as in the report: fields literally named like schema words.
    "provenance": {"fields": [(word, "text") for word in SCHEMA_WORDS]},
    "invoice": {"fields": [("number", "integer"), ("amount", "numeric")]},
    "part": {"fields": [("flange", "text"), ("weight", "numeric")]},
}


@pytest.fixture
def noise(tmp_path, monkeypatch):
    return _state(tmp_path, monkeypatch, NOISE, name="noise")


def _ask(state, question, monkeypatch, reply=cite_first):
    monkeypatch.setenv("MODEL_KEY", MODEL_KEY)
    with ModelStub(reply) as stub:
        config = AnswerConfig(
            base_url=stub.url, model="local-model", model_digest="rev-1", api_key_env="MODEL_KEY"
        )
        result = answer_question(state, question, config)
        return result, list(stub.requests)


@pytest.mark.xfail(strict=True, reason="B4 not implemented")
@pytest.mark.parametrize("word", SCHEMA_WORDS)
def test_a_question_matching_only_through_a_schema_word_finds_nothing(
    noise, monkeypatch, word
):
    question = f"which {word} is the vessel gasket?"
    result, calls = _ask(noise, question, monkeypatch)
    assert result.get("error", {}).get("code") == "NO_CATALOGUE_MATCH", result
    assert calls == [], "the model must not be called when nothing meaningful matched"
    assert search(noise, SearchRequest(query=question, match="any"))["results"] == []


def test_the_report_question_shape_calls_the_model_when_a_meaningful_word_matches(
    noise, monkeypatch
):
    """Control: one meaningful word (`flange`) among schema words still retrieves and asks."""
    result, calls = _ask(noise, "which column holds the flange?", monkeypatch)
    assert len(calls) == 1 and "error" not in result, result


def test_a_column_named_number_is_still_found_with_a_table_word(noise):
    """Control: `number` is a schema word in a question, yet the invoice's own `number` column is
    found through its table's name, and by an explicit search for it."""
    hits = search(noise, SearchRequest(query="invoice number", match="any"))["results"]
    assert any(item["name"] == "number" and item["object_name"] == "invoice" for item in hits)
    explicit = search(noise, SearchRequest(query="invoice number"))["results"]
    assert any(item["name"] == "number" and item["object_name"] == "invoice" for item in explicit)


def test_an_unmatched_question_still_refuses_without_calling_the_model(noise, monkeypatch):
    """Control: the existing guard (no meaningful word matches) keeps working."""
    result, calls = _ask(noise, "zebra giraffe", monkeypatch)
    assert result["error"]["code"] == "NO_CATALOGUE_MATCH"
    assert calls == []
