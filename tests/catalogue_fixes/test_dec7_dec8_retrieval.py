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


# --- DEC-5: relationships and their evidence status reach the model -------------------------


def _ask_payload(state, monkeypatch, question, **config_kwargs):
    monkeypatch.setenv("MODEL_KEY", MODEL_KEY)
    with ModelStub(cite_first) as stub:
        config = AnswerConfig(
            base_url=stub.url, model="m", model_digest="d", api_key_env="MODEL_KEY",
            timeout_seconds=5, **config_kwargs,
        )
        reply = answer_question(state, question, config)
        import json as _json

        payload = _json.loads(stub.requests[-1]["body"]["messages"][-1]["content"])
    return reply, payload


@pytest.fixture
def linked(tmp_path, monkeypatch):
    snapshot = build_snapshot(tmp_path, monkeypatch, {"alpha": {"tables": {
        "vendor": {"fields": [("id", "integer"), ("name", "text")], "key": ["id"]},
        "shipment": {"fields": [("id", "integer"), ("vendor_id", "integer")], "key": ["id"]},
        "unrelated": {"fields": [("note", "text")]},
    }}})
    index_snapshot(snapshot, tmp_path / "state")
    return tmp_path / "state"


def test_relationships_with_status_and_evidence_are_in_the_context(linked, monkeypatch):
    reply, payload = _ask_payload(linked, monkeypatch, "How does shipment relate to vendor?")
    assert "error" not in reply, reply
    [edge] = payload["relationships"]
    assert (edge["from"], edge["to"], edge["status"]) == ("shipment", "vendor", "tentative")
    assert edge["columns"] == [["vendor_id", "id"]]
    assert "values not compared" in edge["evidence"]


def test_a_relationship_can_be_cited(linked, monkeypatch):
    _, payload = _ask_payload(linked, monkeypatch, "How does shipment relate to vendor?")
    edge_id = payload["relationships"][0]["id"]
    monkeypatch.setenv("MODEL_KEY", MODEL_KEY)
    import json as _json

    with ModelStub(
        content=_json.dumps({"answer": "a tentative link", "citations": [edge_id]})
    ) as stub:
        config = AnswerConfig(
            base_url=stub.url, model="m", model_digest="d", api_key_env="MODEL_KEY",
            timeout_seconds=5,
        )
        reply = answer_question(linked, "How does shipment relate to vendor?", config)
    assert reply["citations"] == [edge_id], reply


def test_relationships_are_bounded_and_can_be_turned_off(linked, monkeypatch):
    _, payload = _ask_payload(
        linked, monkeypatch, "How does shipment relate to vendor?", relationship_limit=0
    )
    assert payload["relationships"] == []


def test_objects_with_no_relationship_add_none(linked, monkeypatch):
    _, payload = _ask_payload(linked, monkeypatch, "What is the unrelated note?")
    assert payload["relationships"] == []


# --- DEC-6: claims are validated, not words; "not established" is an outcome --------------------


def _ask_with(state, monkeypatch, question, content):
    monkeypatch.setenv("MODEL_KEY", MODEL_KEY)
    with ModelStub(content=content) as stub:
        config = AnswerConfig(
            base_url=stub.url, model="m", model_digest="d", api_key_env="MODEL_KEY",
            timeout_seconds=5,
        )
        return answer_question(state, question, config), stub


def test_the_word_lineage_in_a_question_is_answered(linked, monkeypatch):
    import json as _json

    first = _json.dumps({"answer": "No lineage is catalogued.", "citations": []})
    reply, stub = _ask_with(linked, monkeypatch, "Is there lineage for shipment?", first)
    assert reply["error"]["code"] == "MODEL_INVALID_CITATION"  # answered outcome must cite
    assert stub.requests, "the model was called: the question was not refused"


def test_a_not_established_outcome_may_cite_nothing_and_has_its_own_code(linked, monkeypatch):
    import json as _json

    content = _json.dumps(
        {"answer": "The catalogue records no lineage for shipment.", "citations": [],
         "outcome": "not_established"}
    )
    reply, _ = _ask_with(linked, monkeypatch, "What is the lineage of shipment?", content)
    assert "error" not in reply, reply
    assert reply["code"] == "NOT_ESTABLISHED_BY_CATALOGUE"
    assert reply["citations"] == []


def test_not_established_still_rejects_an_unknown_citation(linked, monkeypatch):
    import json as _json

    content = _json.dumps({"answer": "none", "citations": ["made-up"],
                           "outcome": "not_established"})
    reply, _ = _ask_with(linked, monkeypatch, "What is the lineage of shipment?", content)
    assert reply["error"]["code"] == "MODEL_INVALID_CITATION"


def test_a_lineage_claim_in_an_answer_is_still_rejected(linked, monkeypatch):
    import json as _json

    _, payload = _ask_payload(linked, monkeypatch, "How does shipment relate to vendor?")
    cited = payload["items"][0]["id"]
    content = _json.dumps({"answer": "shipment flows into vendor", "citations": [cited]})
    reply, _ = _ask_with(linked, monkeypatch, "How does shipment relate to vendor?", content)
    assert reply["error"]["code"] == "MODEL_UNAVAILABLE_OR_INVALID"


def test_the_answer_schema_has_the_outcome_and_no_length_bounds(linked, monkeypatch):
    from catalogue_fixture_kit import schema_bounds

    _, stub = _ask_with(linked, monkeypatch, "What is the lineage of shipment?", "{}")
    schema = stub.schemas[-1]
    assert "outcome" in schema["properties"] and schema_bounds(schema) == []


# --- DEC-9: cited IDs resolve to names in the reply ---------------------------------------------


def test_cited_ids_are_resolved_to_names_app_side(linked, monkeypatch):
    reply, payload = _ask_payload(linked, monkeypatch, "How does shipment relate to vendor?")
    [cited] = reply["cited"]
    first = payload["items"][0]
    assert cited["id"] == first["id"] == reply["citations"][0]
    assert cited["name"] == first["name"]


def test_a_cited_relationship_resolves_to_its_tables_and_columns(linked, monkeypatch):
    import json as _json

    _, payload = _ask_payload(linked, monkeypatch, "How does shipment relate to vendor?")
    edge_id = payload["relationships"][0]["id"]
    content = _json.dumps({"answer": "a tentative link", "citations": [edge_id]})
    reply, _ = _ask_with(linked, monkeypatch, "How does shipment relate to vendor?", content)
    assert reply["cited"] == [{
        "id": edge_id, "kind": "relationship",
        "name": "shipment -> vendor (vendor_id -> id)", "status": "tentative",
    }]
