"""B3 (owner D8, 2026-11-10: "Code writes it (Recommended)"; shape agreed with the researcher).

An answer's `relationships` are written by code from stored edges: one entry per cited ID that is
a stored edge, with its names, status and metadata-only evidence. A cited table gives no entry,
and the model's text never becomes one. `answer` stays the model's own text, and the response
schema says so. Invented names only.
"""

from __future__ import annotations

import json

from catalogue_fixture_kit import MODEL_KEY, ModelStub, build_snapshot
from dbig_contracts.artifact import read_artifact
from dbig_requester.answer import answer_question
from dbig_requester.api import create_app
from dbig_requester.config import AnswerConfig, RequesterConfig
from dbig_requester.search import index_snapshot

# part.gadget_id names the table gadget, so the builder proposes part -> gadget.
SOURCES = {"alpha": {"kind": "postgresql", "tables": {
    "gadget": {"fields": [("id", "integer"), ("title", "text")], "key": ["id"]},
    "part": {"fields": [("id", "integer"), ("gadget_id", "integer")], "key": ["id"]},
}}}
# Evidence in the response is metadata only: the tier and the measured numbers, never text.
EVIDENCE_KEYS = {"tier", "basis", "estimate", "lower", "upper"}


def _ask(tmp_path, monkeypatch, cite):
    """Ask about gadget and part; `cite(payload)` picks the IDs the model cites."""
    snapshot = build_snapshot(tmp_path, monkeypatch, SOURCES)
    index_snapshot(snapshot, tmp_path / "state")
    monkeypatch.setenv("MODEL_KEY", MODEL_KEY)

    def reply(payload):
        return json.dumps({"checked": ["x"], "answer": "gadget is fed by part.",
                           "citations": cite(payload), "outcome": "answered"})

    with ModelStub(reply) as stub:
        config = AnswerConfig(base_url=stub.url, model="local-model", model_digest="rev-1",
                              api_key_env="MODEL_KEY")
        result = answer_question(tmp_path / "state", "how do gadget and part link?", config)
    _, data = read_artifact(snapshot)
    return result, {edge.id: edge for edge in data["edges.jsonl"]}


def test_a_cited_edge_is_written_from_the_stored_edge(tmp_path, monkeypatch):
    result, edges = _ask(tmp_path, monkeypatch, lambda p: [p["relationships"][0]["id"]])
    assert "error" not in result, result
    [entry] = result["relationships"]
    stored = edges[entry["id"]]
    assert entry["status"] == stored.status
    assert {entry["from"], entry["to"]} == {"part", "gadget"}
    assert set(entry["evidence"]) <= EVIDENCE_KEYS


def test_a_cited_table_gives_no_relationship(tmp_path, monkeypatch):
    result, _ = _ask(tmp_path, monkeypatch, lambda p: [p["items"][0]["id"]])
    assert "error" not in result, result
    assert result["relationships"] == []


def test_the_model_text_never_becomes_a_relationship(tmp_path, monkeypatch):
    """The reply says "fed by", a lineage paraphrase; with no edge cited, nothing is claimed."""
    result, edges = _ask(tmp_path, monkeypatch, lambda p: [i["id"] for i in p["items"]])
    assert all(entry["id"] in edges for entry in result["relationships"])
    assert result["relationships"] == []


def test_the_response_schema_says_which_field_is_model_text(tmp_path, monkeypatch):
    monkeypatch.setenv("UNUSED_TOKEN", "synthetic-token-for-the-schema-only")
    app = create_app(RequesterConfig(
        snapshot_dir=tmp_path, state_dir=tmp_path, api_key_env="UNUSED_TOKEN",
    ))
    schemas = app.openapi()["components"]["schemas"]
    properties = schemas["QuestionResponse"]["properties"]
    assert "model" in properties["answer"]["description"].lower()
    assert "stored edge" in properties["relationships"]["description"].lower()
