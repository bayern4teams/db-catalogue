"""C1 + D7: one error code per cause, never from content (plan section 9, Phase 1).

Before the fix every model failure in `ask` and in the builder's review is
`MODEL_UNAVAILABLE_OR_INVALID`, whether the server is unreachable, rejects the request, is too
slow, cuts the answer off, or the model cites an ID that does not exist. Acceptance: one test per
cause, each its own code, and no code or message contains prompt, response or catalogue text.

Codes: MODEL_UNREACHABLE, MODEL_REJECTED_REQUEST, MODEL_TIMEOUT, MODEL_TRUNCATED,
MODEL_INVALID_CITATION. In the builder the partial reason `MODEL_REVIEW_FAILED` stays and the
specific code is added (as the run's `review_status` or beside it in `partial_reasons`).

The fixtures run each cause once per module (the timeout cases take seconds) and the tests read
the recorded outcome.
"""

from __future__ import annotations

import json
import logging

import pytest
from catalogue_fixture_kit import (
    MODEL_KEY,
    ModelStub,
    build_snapshot,
    builder_run,
    candidates,
    cite_first,
    closed_port_url,
    support_everything,
)
from dbig_builder.config import ReviewConfig
from dbig_contracts.artifact import read_artifact
from dbig_requester.answer import answer_question
from dbig_requester.config import AnswerConfig
from dbig_requester.search import index_snapshot

TIMEOUT_SECONDS = 1
SLOW_SECONDS = 3.0  # beyond the timeout, below pytest's patience
# Invented words that must never appear in an error: the question, the model's reply, the
# catalogue's own names and the key.
CANARIES = ("zorblax", "quokka-canary", "sprocket", MODEL_KEY)

CAUSES = {
    "unreachable": "MODEL_UNREACHABLE",
    "rejected": "MODEL_REJECTED_REQUEST",
    "timeout": "MODEL_TIMEOUT",
    "truncated_empty": "MODEL_TRUNCATED",
    "truncated_partial": "MODEL_TRUNCATED",
    "invalid_citation": "MODEL_INVALID_CITATION",
}


def _stub_for(cause: str, *, reply) -> ModelStub | None:
    if cause == "unreachable":
        return None
    if cause == "rejected":
        return ModelStub(reply, status=400)
    if cause == "timeout":
        return ModelStub(reply, delay=SLOW_SECONDS)
    if cause == "truncated_empty":  # the report's shape: all tokens spent reasoning, no content
        return ModelStub(reply, finish_reason="length", content="")
    if cause == "truncated_partial":
        return ModelStub(reply, finish_reason="length", content='{"answer": "quokka-canary')
    return ModelStub(reply)  # invalid_citation: the model's reply is the cause


ASK_CALLS: dict[str, int] = {}  # how many requests the stub saw per cause (fixture sanity)


# --- ask ----------------------------------------------------------------------------------------


def _invented_citation(payload: dict) -> str:
    return json.dumps({"answer": "quokka-canary says so", "citations": ["id-that-does-not-exist"],
        "outcome": "answered"})


@pytest.fixture(scope="module")
def ask_outcomes(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("c1-ask")
    outcomes: dict[str, dict] = {}
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("MODEL_KEY", MODEL_KEY)
        snapshot = build_snapshot(
            tmp_path, monkeypatch,
            {"alpha": {"tables": {"sprocket": {"fields": [("zorblax", "text")]}}}},
        )
        index_snapshot(snapshot, tmp_path / "state")
        for cause in CAUSES:
            reply = _invented_citation if cause == "invalid_citation" else cite_first
            stub = _stub_for(cause, reply=reply)
            url = stub.url if stub else closed_port_url()
            try:
                config = AnswerConfig(
                    base_url=url, model="local-model", model_digest="rev-1",
                    api_key_env="MODEL_KEY", timeout_seconds=TIMEOUT_SECONDS,
                )
                outcomes[cause] = answer_question(
                    tmp_path / "state", "which sprocket column is the zorblax?", config
                )
            finally:
                if stub:
                    ASK_CALLS[cause] = len(stub.requests)
                    stub.close()
    return outcomes


def _code(result: dict) -> str | None:
    return (result.get("error") or {}).get("code")


@pytest.mark.parametrize("cause", list(CAUSES))
def test_ask_reports_each_cause_under_its_own_code(ask_outcomes, cause):
    assert _code(ask_outcomes[cause]) == CAUSES[cause], ask_outcomes[cause]


def test_ask_five_causes_give_five_distinct_codes(ask_outcomes):
    codes = {_code(result) for result in ask_outcomes.values()}
    assert len(codes) == len(set(CAUSES.values())) == 5, codes


@pytest.mark.parametrize("cause", [name for name in CAUSES if name != "unreachable"])
def test_each_fixture_really_reached_the_stub(ask_outcomes, cause):
    """Control: the failures above come from the server's behaviour, not from retrieval or the
    fixture: the stub saw the request (a timeout case may see retries)."""
    assert ASK_CALLS[cause] >= 1


@pytest.mark.parametrize("cause", list(CAUSES))
def test_every_cause_is_still_an_error_and_never_an_answer(ask_outcomes, cause):
    """Control: the failure stays a failure whatever its code (no fabricated answer)."""
    result = ask_outcomes[cause]
    assert "error" in result and "answer" not in result and "citations" not in result


@pytest.mark.parametrize("cause", list(CAUSES))
def test_no_error_contains_prompt_response_or_catalogue_text(ask_outcomes, cause):
    """Control that must hold for every new code: codes come from exception types and structure."""
    text = json.dumps(ask_outcomes[cause])
    for canary in CANARIES:
        assert canary not in text, f"{canary!r} leaked into the {cause} error"


def test_nothing_is_logged_or_printed_with_content(tmp_path, monkeypatch, caplog, capfd):
    """Control: the application's own log and the console carry no prompt, response or name. (The
    HTTP client's DEBUG log is the library's and is excluded; the app configures none.)"""
    monkeypatch.setenv("MODEL_KEY", MODEL_KEY)
    snapshot = build_snapshot(
        tmp_path, monkeypatch,
        {"alpha": {"tables": {"sprocket": {"fields": [("zorblax", "text")]}}}},
    )
    index_snapshot(snapshot, tmp_path / "state")
    with caplog.at_level(logging.DEBUG), ModelStub(_invented_citation) as stub:
        answer_question(
            tmp_path / "state", "which sprocket column is the zorblax?",
            AnswerConfig(base_url=stub.url, model="m", model_digest="d", api_key_env="MODEL_KEY"),
        )
    own = [r.getMessage() for r in caplog.records
           if not r.name.startswith(("openai", "httpx", "httpcore"))]
    console = capfd.readouterr()
    for canary in CANARIES:
        assert not any(canary in line for line in own), canary
        assert canary not in console.out + console.err, canary


# --- the builder's review -----------------------------------------------------------------------

REVIEWED = {
    "vendor": {"fields": [("id", "integer")], "key": ["id"]},
    "shipment": {"fields": [("id", "integer"), ("vendor_id", "integer")], "key": ["id"]},
}


def _invented_evidence(candidates_: list[dict]) -> str:
    return json.dumps({"decisions": [
        {"candidate_id": candidates_[0]["candidate_id"], "decision": "supports",
         "cited_evidence_ids": ["evidence-that-does-not-exist"], "reason": "quokka-canary"}
    ]})


@pytest.fixture(scope="module")
def review_outcomes(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("c1-review")
    outcomes: dict[str, dict] = {}
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("MODEL_KEY", MODEL_KEY)
        for cause in CAUSES:
            reply = _invented_evidence if cause == "invalid_citation" else support_everything
            stub = _stub_for(cause, reply=reply)
            review = ReviewConfig(
                base_url=stub.url if stub else closed_port_url(), model="local-model",
                model_digest="rev-1", api_key_env="MODEL_KEY", timeout_seconds=TIMEOUT_SECONDS,
            )
            try:
                snapshot = build_snapshot(
                    tmp_path, monkeypatch, {"alpha": {"tables": REVIEWED}}, review=review,
                    name=cause,
                )
            finally:
                if stub:
                    stub.close()
            run = builder_run(snapshot)
            manifest, _ = read_artifact(snapshot)
            outcomes[cause] = {
                "status": run["review_status"],
                "reasons": run.get("partial_reasons", []),
                "completeness": manifest.completeness,
                "candidates": candidates(snapshot),
                "calls": len(stub.requests) if stub else None,
            }
    return outcomes


@pytest.mark.parametrize("cause", list(CAUSES))
def test_review_reports_each_cause_under_its_own_code(review_outcomes, cause):
    outcome = review_outcomes[cause]
    assert CAUSES[cause] in {outcome["status"], *outcome["reasons"]}, outcome


@pytest.mark.parametrize("cause", list(CAUSES))
def test_review_failure_stays_partial_and_publishes_the_deterministic_candidates(
    review_outcomes, cause
):
    """Control: the builder keeps `MODEL_REVIEW_FAILED` as the partial reason and publishes the
    graph whatever the model did; a model or telemetry failure never blocks publication."""
    outcome = review_outcomes[cause]
    assert "MODEL_REVIEW_FAILED" in outcome["reasons"], outcome
    assert outcome["completeness"] == "partial"
    assert ("shipment", "vendor_id", "vendor", "id") in outcome["candidates"]


@pytest.mark.parametrize("cause", list(CAUSES))
def test_review_status_and_reasons_carry_no_content(review_outcomes, cause):
    outcome = review_outcomes[cause]
    text = json.dumps([outcome["status"], outcome["reasons"]])
    for canary in (*CANARIES, "vendor", "shipment"):  # table names are catalogue text too
        assert canary not in text, f"{canary!r} leaked into the {cause} review status"


@pytest.mark.parametrize("cause", [name for name in CAUSES if name != "unreachable"])
def test_each_review_fixture_really_reached_the_stub(review_outcomes, cause):
    """Control: the review did call the model (there is a candidate to review)."""
    assert review_outcomes[cause]["calls"] >= 1


def test_the_review_schema_also_avoids_the_bounds_a_llama_cpp_server_refuses(
    tmp_path, monkeypatch
):
    """Finding, not an acceptance item. The report says llama.cpp accepted the review schema
    (`max_length=256`), but the plan's own stub rule (reject any `maxLength`, `minLength`,
    `maxItems`, `minItems`) would refuse it: the schema sent carries `minItems` and `maxLength`.
    Non-strict so the lead can decide either way without a hard failure."""
    from catalogue_fixture_kit import schema_bounds

    monkeypatch.setenv("MODEL_KEY", MODEL_KEY)
    with ModelStub(support_everything, reject_bounds=True) as stub:
        review = ReviewConfig(
            base_url=stub.url, model="m", model_digest="d", api_key_env="MODEL_KEY",
            timeout_seconds=5,
        )
        snapshot = build_snapshot(tmp_path, monkeypatch, {"alpha": {"tables": REVIEWED}},
                                  review=review)
        assert stub.requests and all(not schema_bounds(s) for s in stub.schemas)
    assert builder_run(snapshot)["review_status"] == "OK"
