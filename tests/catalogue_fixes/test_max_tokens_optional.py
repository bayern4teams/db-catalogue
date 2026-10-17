"""`[requester.answer] max_tokens` is optional (user decision 2026-10-17, live run).

At the old fixed 2048 a reasoning model ended 14 of 20 live questions with `MODEL_TRUNCATED`.
Unset, the request carries no `max_tokens` and the server's own maximum applies; set, it is sent.
"""

from __future__ import annotations

from catalogue_fixture_kit import MODEL_KEY, ModelStub, build_snapshot, cite_first
from dbig_requester.answer import answer_question
from dbig_requester.config import AnswerConfig
from dbig_requester.search import index_snapshot


def _ask(tmp_path, monkeypatch, **overrides) -> tuple[dict, dict]:
    snapshot = build_snapshot(
        tmp_path, monkeypatch,
        {"alpha": {"tables": {"gadget": {"fields": [("gadget_website", "text")]}}}},
    )
    index_snapshot(snapshot, tmp_path / "state")
    monkeypatch.setenv("MODEL_KEY", MODEL_KEY)
    with ModelStub(cite_first) as stub:
        config = AnswerConfig(base_url=stub.url, model="local-model", model_digest="rev-1",
                              api_key_env="MODEL_KEY", **overrides)
        result = answer_question(tmp_path / "state", "which column holds the gadget website?",
                                 config)
        body = stub.requests[0]["body"]
    return result, body


def test_unset_max_tokens_sends_no_limit(tmp_path, monkeypatch):
    result, body = _ask(tmp_path, monkeypatch)
    assert "error" not in result, result
    assert "max_tokens" not in body


def test_a_set_max_tokens_is_still_sent(tmp_path, monkeypatch):
    result, body = _ask(tmp_path, monkeypatch, max_tokens=4096)
    assert "error" not in result, result
    assert body["max_tokens"] == 4096
