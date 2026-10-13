"""DEC-7 and DEC-8 (user, 2026-10-13): source and engine words are searchable, a list question
retrieves by object, an object named in the question outranks columns that mention it, a named
source filters, and an operator can exclude schema-description collections (default none)."""

from __future__ import annotations

import pytest
from catalogue_fixture_kit import MODEL_KEY, ModelStub, build_snapshot, cite_first
from dbig_requester.answer import answer_question
from dbig_requester.config import AnswerConfig
from dbig_requester.search import SearchRequest, index_snapshot, question_scope, search

SOURCES = {
    "pgmain": {"kind": "postgresql", "tables": {
        "company": {"fields": [("companyid", "integer"), ("website", "text")]},
        "person": {"fields": [("personid", "integer"), ("companyid", "integer")]},
    }},
    "chfast": {"kind": "clickhouse", "tables": {
        "Experience": {"fields": [("personID", "integer"), ("companyID", "integer")]},
        "Industry": {"fields": [("industryID", "integer")]},
    }},
    "mongo1": {"kind": "mongodb", "tables": {
        "__schemas": {"fields": [("db_schema.Company.columns.website", "text")]},
        "company_source_profiles": {"fields": [("website", "text")]},
        "notes": {"fields": [("body", "text")]},
    }},
}


@pytest.fixture
def state(tmp_path, monkeypatch):
    snapshot = build_snapshot(tmp_path, monkeypatch, SOURCES)
    index_snapshot(snapshot, tmp_path / "state")
    return tmp_path / "state"


def _names(state, **kwargs):
    return [item["name"] for item in search(state, SearchRequest(**kwargs))["results"]]


def test_the_engine_kind_is_a_searchable_word(state):
    names = _names(state, query="clickhouse")
    assert {"Experience", "Industry", "personID"} <= set(names)
    assert "company" not in names


def test_a_question_naming_an_engine_scopes_to_its_source(state):
    sources, listing, words = question_scope(state, "Which tables are in ClickHouse?")
    assert (sources, listing, words) == (["chfast"], True, {"clickhouse"})


def test_a_singular_table_word_is_not_a_list_question(state):
    assert question_scope(state, "Which table stores the website?")[1] is False


def test_a_list_question_for_a_source_returns_its_objects_only(state):
    names = _names(
        state, query="", match="any", source_ids=["chfast"], objects_only=True, limit=50
    )
    assert names == ["Experience", "Industry"]


def test_a_list_question_without_a_source_or_term_returns_nothing(state):
    assert _names(state, query="the tables", match="any", objects_only=True) == []


def test_an_object_named_in_the_question_ranks_before_columns_that_mention_it(state):
    names = _names(state, query="company website", match="any", limit=50)
    assert names.index("company") < names.index("companyid")


def test_a_named_source_filters_ask_retrieval(state, monkeypatch):
    monkeypatch.setenv("MODEL_KEY", MODEL_KEY)
    with ModelStub(cite_first) as stub:
        config = AnswerConfig(
            base_url=stub.url, model="m", model_digest="d", api_key_env="MODEL_KEY",
            timeout_seconds=5,
        )
        reply = answer_question(state, "Which tables are in ClickHouse?", config)
        sent = str(stub.requests[-1]["body"]["messages"][-1]["content"])
    assert "error" not in reply, reply
    assert "chfast" in sent and "pgmain" not in sent and "mongo1" not in sent


def test_no_exclusion_by_default(tmp_path, monkeypatch):
    snapshot = build_snapshot(tmp_path, monkeypatch, SOURCES)
    index_snapshot(snapshot, tmp_path / "state")
    assert "__schemas" in _names(tmp_path / "state", query="schemas")


def test_a_per_source_exclude_list_hides_schema_collections_and_their_fields(
    tmp_path, monkeypatch
):
    snapshot = build_snapshot(tmp_path, monkeypatch, SOURCES)
    index_snapshot(
        snapshot, tmp_path / "state",
        {"mongo1": ["__schemas", "*_SOURCE_PROFILES"], "pgmain": ["nothing*"]},
    )
    names = _names(tmp_path / "state", query="website", limit=50)
    assert names == ["website"], names  # only pgmain.company.website remains
    assert "notes" in _names(tmp_path / "state", query="notes")
