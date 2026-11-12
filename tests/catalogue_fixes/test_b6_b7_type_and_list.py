"""B6 and B7 (owner, 2026-11-10: "Map type words (Recommended)", "List mode (Recommended)").

A question that asks about a type ("which columns have a timestamp type") retrieves the fields of
that type, and a list question ("list all ...", or a type question) is not cut at
`retrieval_limit`. The groups follow the SQL standard's type categories: DATE, TIME and TIMESTAMP
are datetime types; exact (INTEGER, DECIMAL, NUMERIC) and approximate (REAL, FLOAT, DOUBLE
PRECISION) types are numeric. Invented names only.
"""

from __future__ import annotations

import json

import pytest
from catalogue_fixture_kit import MODEL_KEY, ModelStub, build_snapshot, cite_first
from dbig_requester.answer import answer_question
from dbig_requester.config import AnswerConfig
from dbig_requester.search import index_snapshot, question_type, type_categories


@pytest.mark.parametrize(
    ("data_type", "category"),
    [
        ("timestamp with time zone", "datetime"),
        ("date", "datetime"),
        ("time without time zone", "datetime"),
        ("Nullable(DateTime64(3))", "datetime"),
        ("Date32", "datetime"),
        ("datetime", "datetime"),
        ("numeric(10,2)", "numeric"),
        ("double precision", "numeric"),
        ("real", "numeric"),
        ("UInt64", "numeric"),
        ("Decimal(18, 4)", "numeric"),
        ("Float64", "numeric"),
        ("bigint", "integer"),
        ("LowCardinality(String)", "text"),
        ("boolean", "boolean"),
        ("Bool", "boolean"),
    ],
)
def test_engine_type_names_fall_into_their_sql_category(data_type, category):
    assert category in type_categories(data_type)


@pytest.mark.parametrize(
    ("data_type", "category"),
    [("interval", "datetime"), ("text", "numeric"), ("double precision", "integer"),
     ("point", "numeric"), ("uuid", "text")],
)
def test_other_types_stay_out_of_the_category(data_type, category):
    assert category not in type_categories(data_type)


@pytest.mark.parametrize(
    ("question", "category"),
    [
        ("Which columns in the catalogue have a timestamp or datetime type?", "datetime"),
        ("Which columns have a numeric type in PostgreSQL?", "numeric"),
        ("List the columns of type boolean", "boolean"),
        ("What date did the last run happen?", None),
        ("Which type of company is acme?", None),
    ],
)
def test_a_type_word_counts_only_in_a_question_about_types(question, category):
    assert question_type(question) == category


TABLES = {
    f"t{number:02d}": {"fields": [("id", "integer"), (f"made{number:02d}", "timestamp"),
                                  ("code", "text")]}
    for number in range(12)
}


def _context(tmp_path, monkeypatch, question: str) -> list[dict]:
    snapshot = build_snapshot(tmp_path, monkeypatch, {"alpha": {"tables": TABLES}})
    index_snapshot(snapshot, tmp_path / "state")
    monkeypatch.setenv("MODEL_KEY", MODEL_KEY)
    with ModelStub(cite_first) as stub:
        config = AnswerConfig(base_url=stub.url, model="local-model", model_digest="rev-1",
                              api_key_env="MODEL_KEY")
        assert config.retrieval_limit < len(TABLES), "control: the default limit would cut"
        result = answer_question(tmp_path / "state", question, config)
        requests = list(stub.requests)
    assert "error" not in result, result
    return json.loads(requests[0]["body"]["messages"][1]["content"])["items"]


def test_a_type_question_retrieves_every_field_of_that_type(tmp_path, monkeypatch):
    items = _context(tmp_path, monkeypatch, "Which columns have a timestamp type?")
    made = sorted(item["name"] for item in items if item["name"].startswith("made"))
    assert made == [f"made{number:02d}" for number in range(12)]


def test_a_list_all_question_is_not_cut_at_the_retrieval_limit(tmp_path, monkeypatch):
    items = _context(tmp_path, monkeypatch, "List all code columns")
    owners = {item["object_name"] for item in items if item["name"] == "code"}
    assert owners == set(TABLES)


def test_an_ordinary_question_keeps_the_retrieval_limit(tmp_path, monkeypatch):
    items = _context(tmp_path, monkeypatch, "Which code columns exist?")
    owners = {item["object_name"] for item in items if item["name"] == "code"}
    assert len(owners) == AnswerConfig.model_fields["retrieval_limit"].default


def test_a_type_question_context_holds_only_fields_of_that_type(tmp_path, monkeypatch):
    """Owner ruling 2026-11-10, "Only matching fields (Recommended)": the DEC-18 table expansion
    is skipped for a type question, so the `id` and `code` columns of each table stay out."""
    items = _context(tmp_path, monkeypatch, "Which columns have a timestamp type?")
    fields = [item for item in items if item["kind"] == "field"]
    assert fields, "control: the timestamp fields were retrieved"
    assert {item["data_type"] for item in fields} == {"timestamp"}, [i["name"] for i in fields]


@pytest.mark.parametrize(
    ("question", "category"),
    [
        # Review finding 6 (owner "Yes, fix all", 2026-11-12): the owner's H4 examples name the
        # type beside a column word, without the word "type".
        ("show integer columns", "integer"),
        ("which columns are dates?", "datetime"),
        ("Which fields are text?", "text"),
        ("what date did it run", None),
        ("What date did the last run happen?", None),
    ],
)
def test_a_type_word_beside_a_column_word_asks_about_a_type(question, category):
    assert question_type(question) == category


def test_all_inside_a_yes_no_question_keeps_the_retrieval_limit(tmp_path, monkeypatch):
    """Review finding 7: "all" only asks for a list beside a table or column word."""
    items = _context(tmp_path, monkeypatch, "Are all codes linked to a customer?")
    owners = {item["object_name"] for item in items if item["name"] == "code"}
    assert len(owners) == AnswerConfig.model_fields["retrieval_limit"].default
