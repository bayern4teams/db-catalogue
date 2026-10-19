"""Phase 6 acceptance checks for P6-5 (F8) and P6-8 (F12), written from the plan only.

P6-5 (F8, DEC-18): the review's `max_tokens` is optional. Unset, the request carries no
`max_tokens` key; set, the configured value is sent. The example vLLM builder config sets
`timeout_seconds = 900` and leaves `max_tokens` unset.

P6-8 (F12): `outcome` is required in the answer schema sent to the model. A reply that omits it is
`MODEL_UNAVAILABLE_OR_INVALID` and is not an answer. The system prompt gains the sentence
"Put the verdict only in the outcome field."

Names are invented and generic (plan section 2.4). An `xfail(strict=True)` marks a check that fails
on the code before the item is built; the lead removes the mark when the item lands.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from catalogue_fixture_kit import (
    MODEL_KEY,
    ModelStub,
    build_snapshot,
    candidates,
    support_everything,
)
from dbig_builder.config import ReviewConfig, load_config
from dbig_requester.answer import answer_question
from dbig_requester.config import AnswerConfig
from dbig_requester.search import index_snapshot
from pydantic import ValidationError

EXAMPLE = Path(__file__).parents[2] / "examples" / "local" / "builder-with-vllm.toml"
VERDICT_SENTENCE = "Put the verdict only in the outcome field."


# --- P6-5 / F8: the review's max_tokens ---------------------------------------------------------


def _pairs(count: int) -> dict:
    tables = {}
    for number in range(count):
        tables[f"parent{number}"] = {
            "fields": [(f"parent{number}_id", "integer")], "key": [f"parent{number}_id"],
        }
        tables[f"child{number}"] = {
            "fields": [("id", "integer"), (f"parent{number}_id", "integer")],
        }
    return tables


def _review_requests(tmp_path, monkeypatch, *, pairs: int = 1, **review) -> tuple[list[dict], Path]:
    monkeypatch.setenv("MODEL_KEY", MODEL_KEY)
    with ModelStub(support_everything) as stub:
        config = ReviewConfig(
            base_url=stub.url, model="local-model", model_digest="rev-1",
            api_key_env="MODEL_KEY", timeout_seconds=5, max_candidates=100, **review,
        )
        snapshot = build_snapshot(
            tmp_path, monkeypatch, {"alpha": {"tables": _pairs(pairs)}}, review=config
        )
        bodies = [item["body"] for item in stub.requests]
    assert bodies, "the review must have called the model"
    return bodies, snapshot


def test_review_max_tokens_defaults_to_unset():
    assert ReviewConfig.model_fields["max_tokens"].default is None


def test_an_explicit_none_is_accepted():
    config = ReviewConfig(base_url="http://127.0.0.1:8000/v1", model="m", model_digest="d",
                          api_key_env="MODEL_KEY", max_tokens=None)
    assert config.max_tokens is None


@pytest.mark.parametrize("bad", [0, -1])
def test_a_set_max_tokens_must_still_be_positive(bad):
    with pytest.raises(ValidationError):
        ReviewConfig(base_url="http://127.0.0.1:8000/v1", model="m", model_digest="d",
                     api_key_env="MODEL_KEY", max_tokens=bad)


def test_unset_max_tokens_sends_no_limit_in_any_batch(tmp_path, monkeypatch):
    """Three pairs give more than one batch; none of them may carry `max_tokens`.

    Fails on the old code, which always sent its default of 512."""
    bodies, snapshot = _review_requests(tmp_path, monkeypatch, pairs=3, batch_size=2)
    assert len(bodies) > 1 and len(candidates(snapshot)) > 2
    for body in bodies:
        assert "max_tokens" not in body, body.get("max_tokens")
        assert "max_completion_tokens" not in body


@pytest.mark.parametrize("value", [7, 4096, 24000])
def test_a_set_max_tokens_is_sent_unchanged_in_every_batch(tmp_path, monkeypatch, value):
    bodies, _ = _review_requests(tmp_path, monkeypatch, pairs=3, batch_size=2, max_tokens=value)
    assert len(bodies) > 1
    assert all(body["max_tokens"] == value for body in bodies)


def test_the_vllm_example_sets_a_long_timeout_and_leaves_max_tokens_unset(monkeypatch):
    """`tests/test_examples.py` only checks that the example loads; the values are the point."""
    monkeypatch.setenv("DBIG_SIGNING_KEY", "x" * 40)
    config = load_config(EXAMPLE)
    assert config.review is not None
    assert config.review.timeout_seconds == 900
    assert config.review.max_tokens is None


def test_the_vllm_example_has_no_active_max_tokens_line():
    """An operator copying the example must not inherit a cap by accident (a commented mention
    explaining why it is unset is fine)."""
    active = [
        line for line in EXAMPLE.read_text().splitlines()
        if line.strip().startswith("max_tokens")
    ]
    assert active == []


# --- P6-8 / F12: outcome is required ------------------------------------------------------------


def _ask(tmp_path, monkeypatch, reply) -> tuple[dict, list[dict]]:
    """Ask one question; `reply(payload)` returns the model's raw content. Returns the result and
    the requests the stub recorded."""
    snapshot = build_snapshot(
        tmp_path, monkeypatch,
        {"alpha": {"tables": {"gadget": {"fields": [("gadget_website", "text")]}}}},
    )
    index_snapshot(snapshot, tmp_path / "state")
    monkeypatch.setenv("MODEL_KEY", MODEL_KEY)
    with ModelStub(reply) as stub:
        config = AnswerConfig(base_url=stub.url, model="local-model", model_digest="rev-1",
                              api_key_env="MODEL_KEY")
        result = answer_question(tmp_path / "state", "which column holds the gadget website?",
                                 config)
        requests = list(stub.requests)
    assert len(requests) == 1, "the model must be called exactly once"
    return result, requests


def _cites_first(**extra):
    def reply(payload: dict) -> str:
        first = payload["items"][0]
        return json.dumps({"answer": f"{first['name']} is catalogued.",
                           "citations": [first["id"]], **extra})
    return reply


def _sent_schema(requests: list[dict]) -> dict:
    return requests[0]["body"]["response_format"]["json_schema"]["schema"]


def test_the_schema_sent_requires_answer_citations_and_outcome(tmp_path, monkeypatch):
    _, requests = _ask(tmp_path, monkeypatch, _cites_first(outcome="answered"))
    schema = _sent_schema(requests)
    assert sorted(schema["required"]) == ["answer", "citations", "outcome"]
    assert "default" not in schema["properties"]["outcome"]


def test_the_schema_still_lists_both_outcomes_and_forbids_extra_keys(tmp_path, monkeypatch):
    _, requests = _ask(tmp_path, monkeypatch, _cites_first(outcome="answered"))
    schema = _sent_schema(requests)
    outcome = schema["properties"]["outcome"]
    assert set(outcome.get("enum", [])) == {"answered", "not_established"}
    assert schema.get("additionalProperties") is False


def test_a_reply_without_outcome_is_not_an_answer(tmp_path, monkeypatch):
    """Fails on the old code, where the missing field defaulted to `answered`."""
    result, _ = _ask(tmp_path, monkeypatch, _cites_first())
    assert result["error"] == {"code": "MODEL_UNAVAILABLE_OR_INVALID"}
    assert "answer" not in result and "citations" not in result and "cited" not in result


def test_a_reply_without_outcome_and_without_citations_is_not_an_answer(tmp_path, monkeypatch):
    result, _ = _ask(
        tmp_path, monkeypatch,
        lambda _: json.dumps({"answer": "The catalogue does not show it.", "citations": []}),
    )
    assert result["error"] == {"code": "MODEL_UNAVAILABLE_OR_INVALID"}


def test_a_null_or_unknown_outcome_is_not_an_answer(tmp_path, monkeypatch):
    for bad in (None, "maybe", "ANSWERED", ""):
        result, _ = _ask(tmp_path / str(bad), monkeypatch, _cites_first(outcome=bad))
        assert result["error"] == {"code": "MODEL_UNAVAILABLE_OR_INVALID"}, bad
        assert "answer" not in result


def test_an_answered_reply_with_a_citation_is_accepted(tmp_path, monkeypatch):
    result, _ = _ask(tmp_path, monkeypatch, _cites_first(outcome="answered"))
    assert "error" not in result, result
    assert result["citations"] and "code" not in result


def test_not_established_may_cite_nothing_and_says_so(tmp_path, monkeypatch):
    result, _ = _ask(
        tmp_path, monkeypatch,
        lambda _: json.dumps({"answer": "Checked gadget; no such column.", "citations": [],
                              "outcome": "not_established"}),
    )
    assert "error" not in result, result
    assert result["code"] == "NOT_ESTABLISHED_BY_CATALOGUE"
    assert result["citations"] == []


def test_answered_with_no_citation_is_still_refused(tmp_path, monkeypatch):
    """Making the field required must not loosen the rule that an answer cites an ID."""
    result, _ = _ask(
        tmp_path, monkeypatch,
        lambda _: json.dumps({"answer": "It is there.", "citations": [], "outcome": "answered"}),
    )
    assert result["error"] == {"code": "MODEL_INVALID_CITATION"}


def test_the_system_prompt_tells_the_model_where_the_verdict_goes(tmp_path, monkeypatch):
    _, requests = _ask(tmp_path, monkeypatch, _cites_first(outcome="answered"))
    system = requests[0]["body"]["messages"][0]
    assert system["role"] == "system"
    assert VERDICT_SENTENCE in system["content"]


def test_the_earlier_prompt_sentences_survive(tmp_path, monkeypatch):
    _, requests = _ask(tmp_path, monkeypatch, _cites_first(outcome="answered"))
    content = requests[0]["body"]["messages"][0]["content"]
    assert "untrusted data" in content
    assert "set outcome to not_established" in content
    assert "Do not infer lineage" in content


def test_no_question_text_or_catalogue_value_reaches_an_error(tmp_path, monkeypatch):
    """A refused reply is reported by code only, never by the model's text or the question."""
    result, _ = _ask(
        tmp_path, monkeypatch,
        lambda _: json.dumps({"answer": "SECRET-ANSWER-TEXT", "citations": []}),
    )
    assert "SECRET-ANSWER-TEXT" not in json.dumps(result)
    assert "gadget website" not in json.dumps(result)
