"""B1: `ask` must work on a llama.cpp style server (plan section 9, Phase 1).

The server refuses a JSON schema that carries `maxLength`, `minLength`, `maxItems` or `minItems`
("failed to parse grammar"). The fix drops the bounds from the schema it sends and enforces the
same limit after parsing, so a model that ignores the limit is still refused. Acceptance:

* the recording stub rejects any bound, and `ask` returns a cited answer;
* a recursive walk of every schema `ask` sends finds none of the four keywords;
* an answer over the limit is rejected after parsing; an answer at the limit is accepted.

The two xfail tests fail on the old code because the old `Answer` schema carries `maxLength`.
"""

from __future__ import annotations

import json

from catalogue_fixture_kit import MODEL_KEY, ModelStub, build_snapshot, cite_first, schema_bounds
from dbig_requester.answer import answer_question
from dbig_requester.config import AnswerConfig
from dbig_requester.search import index_snapshot

QUESTION = "which column holds the gadget website?"
# The report names this limit (2000, `answer.py` line 22). The fix keeps it in one named constant;
# the test states the number the report gives so a silent change of the limit shows up here.
ANSWER_LIMIT = 2000


def _state(tmp_path, monkeypatch):
    snapshot = build_snapshot(
        tmp_path,
        monkeypatch,
        {"alpha": {"tables": {"gadget": {"fields": [
            ("gadget_name", "text"), ("gadget_website", "text")]}}}},
    )
    index_snapshot(snapshot, tmp_path / "state")
    return tmp_path / "state"


def _config(stub: ModelStub, monkeypatch, **overrides) -> AnswerConfig:
    monkeypatch.setenv("MODEL_KEY", MODEL_KEY)
    return AnswerConfig(
        base_url=stub.url, model="local-model", model_digest="rev-1", api_key_env="MODEL_KEY",
        **overrides,
    )


def test_ask_answers_when_the_server_rejects_length_bounds(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch)
    with ModelStub(cite_first, reject_bounds=True) as stub:
        result = ask(state, stub, monkeypatch)
        assert stub.requests, "the model must actually be called"
    assert "error" not in result, result
    assert result["citations"], result
    assert stub.rejected == 0, "the sent schema must not carry a bound the server refuses"


def test_the_schema_sent_carries_no_bound_keyword(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch)
    with ModelStub(cite_first) as stub:  # accepts everything, so even the old code gets through
        ask(state, stub, monkeypatch)
        schemas = stub.schemas
    assert schemas and all(schemas), "a schema must still be sent (the answer stays structured)"
    for schema in schemas:
        assert schema_bounds(schema) == []


def ask(state, stub: ModelStub, monkeypatch, question: str = QUESTION) -> dict:
    return answer_question(state, question, _config(stub, monkeypatch))


def test_a_schema_is_still_sent_and_names_the_two_answer_fields(tmp_path, monkeypatch):
    """Control: dropping the bounds must not drop the structure."""
    state = _state(tmp_path, monkeypatch)
    with ModelStub(cite_first) as stub:
        ask(state, stub, monkeypatch)
        schema = stub.schemas[0]
    text = json.dumps(schema)
    assert '"answer"' in text and '"citations"' in text


def test_an_answer_over_the_limit_is_rejected_after_parsing(tmp_path, monkeypatch):
    """Control (passes on the old code through Pydantic, must pass after the fix through the
    post-parse check): a model that ignores the limit is refused, not passed on."""
    state = _state(tmp_path, monkeypatch)

    def too_long(payload: dict) -> str:
        return json.dumps({"answer": "x" * (ANSWER_LIMIT + 1),
                           "citations": [payload["items"][0]["id"]]})

    with ModelStub(too_long) as stub:
        result = ask(state, stub, monkeypatch)
    assert "error" in result and "answer" not in result, "over-limit text must not be returned"
    assert "x" * 50 not in json.dumps(result), "the refused text must not be echoed"


def test_an_answer_at_the_limit_is_accepted(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch)

    def at_limit(payload: dict) -> str:
        return json.dumps({"answer": "x" * ANSWER_LIMIT,
                           "citations": [payload["items"][0]["id"]]})

    with ModelStub(at_limit) as stub:
        result = ask(state, stub, monkeypatch)
    assert "error" not in result, result
    assert len(result["answer"]) == ANSWER_LIMIT
