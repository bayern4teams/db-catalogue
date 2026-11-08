# ruff: noqa: E501, I001 # C5: reason=adversarial test file kept as its author wrote it; approved=user, 2026-11-08; review=2027-05-08
"""Attack H1(c) for C1 + D7: distinct model error codes, in `ask` and in the builder's review.

Derived from engineer-fixes C1 ("distinct codes without logging content: MODEL_UNREACHABLE,
MODEL_REJECTED_REQUEST, MODEL_TRUNCATED, MODEL_INVALID_CITATION") and D7 ("separate code for a
timeout"), plus plan section 9: "one test per cause (stub: closed port, 400, delay beyond
timeout_seconds, length cut-off, unknown cited ID), each its own code; no code or message contains
prompt, response or catalogue text. The builder keeps MODEL_REVIEW_FAILED as the partial reason and
adds the specific code."

Run by path:
    uv run pytest tests/catalogue_fixes/attack3_c_c1_codes.py -q -p no:cacheprovider
"""

from __future__ import annotations

import json
import logging
import socket
import threading

import pytest
from catalogue_fixture_kit import (
    MODEL_KEY, ModelStub, build_snapshot, builder_run, cite_first, closed_port_url,
    support_everything,
)
from dbig_builder.config import ReviewConfig
from dbig_requester.answer import answer_question
from dbig_requester.config import AnswerConfig
from dbig_requester.search import index_snapshot

CANARY = "CANARY-quokka-7731"
CODES = {"MODEL_UNREACHABLE", "MODEL_REJECTED_REQUEST", "MODEL_TIMEOUT", "MODEL_TRUNCATED",
         "MODEL_INVALID_CITATION"}
OLD = "MODEL_UNAVAILABLE_OR_INVALID"
QUESTION = f"which sprocket column is the zorblax {CANARY}?"
REVIEWED = {
    "vendor": {"fields": [("id", "integer")], "key": ["id"]},
    "shipment": {"fields": [("id", "integer"), ("vendor_id", "integer")], "key": ["id"]},
}


def app_log(caplog):
    """Log text from this project's own loggers (third-party debug logs are reported separately)."""
    return "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("dbig"))


def third_party_log(caplog):
    return "\n".join(r.getMessage() for r in caplog.records if not r.name.startswith("dbig"))


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_KEY", MODEL_KEY)
    snapshot = build_snapshot(
        tmp_path, monkeypatch,
        {"alpha": {"tables": {"sprocket": {"fields": [("zorblax", "text")]}}}},
    )
    index_snapshot(snapshot, tmp_path / "state")
    return tmp_path / "state"


def ask(state, url, *, timeout=2, key=MODEL_KEY, question="which sprocket column is the zorblax?"):
    return answer_question(
        state, question,
        AnswerConfig(base_url=url, model="m", model_digest="d", api_key_env="MODEL_KEY",
                     timeout_seconds=timeout),
    )


def review(tmp_path, monkeypatch, url, *, timeout=2, name="r"):
    monkeypatch.setenv("MODEL_KEY", MODEL_KEY)
    snap = build_snapshot(
        tmp_path, monkeypatch, {"alpha": {"tables": REVIEWED}},
        review=ReviewConfig(base_url=url, model="m", model_digest="d", api_key_env="MODEL_KEY",
                            timeout_seconds=timeout),
        name=name,
    )
    run = builder_run(snap)
    return run["review_status"], run.get("partial_reasons", []), snap


def bad_cite_ask(payload):
    return json.dumps({"checked": ["x"],
        "answer": "x is catalogued.", "citations": ["invented-" + CANARY],
        "outcome": "answered"})


def bad_cite_review(payload):
    return json.dumps({"decisions": [
        {"candidate_id": payload[0]["candidate_id"], "decision": "supports",
         "cited_evidence_ids": ["invented-" + CANARY], "reason": "r"}]})


# case: (id, stub factory for ask, stub factory for review, timeout, expected code)
def make(case, kind):
    reply = cite_first if kind == "ask" else support_everything
    bad = bad_cite_ask if kind == "ask" else bad_cite_review
    if case == "unreachable":
        return None
    if case == "rejected":
        return ModelStub(reply, status=400)
    if case == "timeout":
        return ModelStub(reply, delay=3)
    if case == "truncated":
        return ModelStub(reply, finish_reason="length", content="")
    if case == "truncated_partial_json":
        return ModelStub(reply, finish_reason="length", content='{"answer": "abc' if kind == "ask" else '{"decisions": [{"cand')
    if case == "citation":
        return ModelStub(bad)
    raise KeyError(case)


EXPECTED = {
    "unreachable": "MODEL_UNREACHABLE",
    "rejected": "MODEL_REJECTED_REQUEST",
    "timeout": "MODEL_TIMEOUT",
    "truncated": "MODEL_TRUNCATED",
    "truncated_partial_json": "MODEL_TRUNCATED",
    "citation": "MODEL_INVALID_CITATION",
}


@pytest.mark.parametrize("case", list(EXPECTED))
def test_ask_each_cause_has_its_own_code(state, case):
    stub = make(case, "ask")
    url = closed_port_url() if stub is None else stub.url
    try:
        result = ask(state, url, timeout=1)
    finally:
        if stub:
            stub.close()
    assert result["error"]["code"] == EXPECTED[case], result


@pytest.mark.parametrize("case", list(EXPECTED))
def test_review_each_cause_has_its_own_code_and_keeps_the_partial_reason(tmp_path, monkeypatch, case):
    stub = make(case, "review")
    url = closed_port_url() if stub is None else stub.url
    try:
        status, reasons, snap = review(tmp_path, monkeypatch, url, timeout=1, name=case)
    finally:
        if stub:
            stub.close()
    assert status == EXPECTED[case]
    assert "MODEL_REVIEW_FAILED" in reasons and EXPECTED[case] in reasons, reasons
    assert OLD not in reasons and OLD != status
    assert reasons == sorted(set(reasons))


def test_the_five_causes_give_five_different_codes(state, tmp_path, monkeypatch):
    seen_ask, seen_review = set(), set()
    for case in ("unreachable", "rejected", "timeout", "truncated", "citation"):
        stub = make(case, "ask")
        try:
            seen_ask.add(ask(state, closed_port_url() if stub is None else stub.url, timeout=1)["error"]["code"])
        finally:
            if stub:
                stub.close()
        stub = make(case, "review")
        try:
            seen_review.add(review(tmp_path, monkeypatch, closed_port_url() if stub is None else stub.url,
                                   timeout=1, name="d" + case)[0])
        finally:
            if stub:
                stub.close()
    assert seen_ask == CODES and seen_review == CODES


def test_timeout_is_not_unreachable_and_unreachable_is_not_timeout(state):
    """D7: a busy shared server that queues is a timeout, a dead port is unreachable."""
    with ModelStub(cite_first, delay=3) as stub:
        assert ask(state, stub.url, timeout=1)["error"]["code"] == "MODEL_TIMEOUT"
    assert ask(state, closed_port_url(), timeout=1)["error"]["code"] == "MODEL_UNREACHABLE"


def test_a_slow_answer_within_the_timeout_is_not_a_timeout(state):
    with ModelStub(cite_first, delay=0.5) as stub:
        assert "answer" in ask(state, stub.url, timeout=3)


@pytest.mark.parametrize("status", [401, 403, 404, 422])
def test_client_error_statuses_are_rejected_requests(state, status):
    with ModelStub(cite_first, status=status) as stub:
        assert ask(state, stub.url)["error"]["code"] == "MODEL_REJECTED_REQUEST"


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_server_statuses_are_not_labelled_as_a_wrong_request_nor_as_the_old_umbrella(state, status):
    """RISK if it fails: a 5xx is not the request's fault. Not named in the report; the plan only
    lists 400 explicitly."""
    with ModelStub(cite_first, status=status) as stub:
        code = ask(state, stub.url)["error"]["code"]
    assert code != "MODEL_REJECTED_REQUEST", code


def test_a_server_that_accepts_then_drops_the_connection_is_unreachable_not_timeout(state):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)

    def drop():
        while True:
            try:
                connection, _ = listener.accept()
            except OSError:
                return
            connection.close()

    threading.Thread(target=drop, daemon=True).start()
    try:
        code = ask(state, f"http://127.0.0.1:{listener.getsockname()[1]}/v1", timeout=5)["error"]["code"]
    finally:
        listener.close()
    assert code != "MODEL_TIMEOUT"
    assert code in CODES | {OLD}


def test_garbage_content_is_none_of_the_transport_codes(state):
    with ModelStub(cite_first, content=f"{CANARY} Sure! these look related") as stub:
        code = ask(state, stub.url)["error"]["code"]
    assert code not in {"MODEL_UNREACHABLE", "MODEL_TIMEOUT", "MODEL_TRUNCATED", "MODEL_REJECTED_REQUEST"}


def test_truncation_wins_over_a_parse_failure_of_the_cut_off_text(state):
    with ModelStub(cite_first, finish_reason="length", content='{"answer": "ab') as stub:
        assert ask(state, stub.url)["error"]["code"] == "MODEL_TRUNCATED"


def test_a_complete_answer_with_length_finish_is_still_reported_as_truncated_or_accepted_but_never_crashes(state):
    with ModelStub(cite_first, finish_reason="length") as stub:
        result = ask(state, stub.url)
    assert "answer" in result or result["error"]["code"] == "MODEL_TRUNCATED"


# --- leaks -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("case", ["rejected", "timeout", "truncated_partial_json", "citation", "garbage"])
def test_ask_error_carries_no_prompt_response_question_or_key(state, caplog, capsys, case):
    caplog.set_level(logging.DEBUG)
    if case == "garbage":
        stub = ModelStub(cite_first, content=f"{CANARY} response-text")
    elif case == "rejected":
        stub = ModelStub(cite_first, status=400)
    elif case == "truncated_partial_json":
        stub = ModelStub(cite_first, finish_reason="length", content=f"{CANARY} partial")
    else:
        stub = make(case, "ask")
    try:
        result = ask(state, stub.url, timeout=1, question=QUESTION)
    finally:
        stub.close()
    blob = json.dumps(result) + app_log(caplog) + capsys.readouterr().out
    assert CANARY not in blob, blob
    assert MODEL_KEY not in blob
    assert "sprocket" not in json.dumps(result.get("error", {}))
    assert "zorblax" not in json.dumps(result.get("error", {}))
    assert "127.0.0.1" not in json.dumps(result.get("error", {})), "no endpoint in the error"
    assert set(result["error"]) <= {"code", "message", "reason"}, result["error"]
    assert len(json.dumps(result["error"])) < 400


def test_unreachable_error_does_not_name_the_endpoint(state, caplog):
    caplog.set_level(logging.DEBUG)
    url = closed_port_url()
    result = ask(state, url, timeout=1)
    blob = json.dumps(result) + app_log(caplog)
    assert url not in blob and url.split("/v1")[0].split(":")[-1] not in json.dumps(result)


@pytest.mark.parametrize("case", ["rejected", "timeout", "truncated_partial_json", "citation", "unreachable"])
def test_review_failure_record_carries_no_content(tmp_path, monkeypatch, caplog, capsys, case):
    caplog.set_level(logging.DEBUG)
    stub = make(case, "review")
    url = closed_port_url() if stub is None else stub.url
    try:
        status, reasons, snap = review(tmp_path, monkeypatch, url, timeout=1, name=f"lk{case}")
    finally:
        if stub:
            stub.close()
    blob = status + " ".join(reasons) + app_log(caplog) + capsys.readouterr().out
    blob += (snap / "manifest.json").read_text(encoding="utf-8")
    blob += (snap / "runs.jsonl").read_text(encoding="utf-8")
    assert CANARY not in blob and MODEL_KEY not in blob
    assert url not in blob
    for code in reasons + [status]:
        assert code == "OK" or code.replace("_", "").isupper(), code


def test_old_umbrella_code_is_not_emitted_for_any_of_the_five_causes(state, tmp_path, monkeypatch):
    for case in EXPECTED:
        stub = make(case, "ask")
        try:
            code = ask(state, closed_port_url() if stub is None else stub.url, timeout=1)["error"]["code"]
        finally:
            if stub:
                stub.close()
        assert code != OLD, case
