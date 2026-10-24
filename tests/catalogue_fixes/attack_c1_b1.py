# ruff: noqa: E501
"""Attack tests for B1 (ask schema) and C1 (error codes) in `ask` and in the builder's review.

Run by file name:

    uv run pytest tests/catalogue_fixes/attack_c1_b1.py -p no:cacheprovider

A failing test is a defect (or, where the docstring says so, a risk) found in the implementation.
Nothing is marked xfail. Names are invented and generic.
"""

from __future__ import annotations

import json
import logging
import socket
import threading

import pytest
from catalogue_fixture_kit import (
    MODEL_KEY,
    ModelStub,
    build_snapshot,
    builder_run,
    candidates,
    cite_first,
    schema_bounds,
    support_everything,
)
from dbig_builder.config import ReviewConfig
from dbig_requester import answer as _answer_module
from dbig_requester.answer import Answer, answer_question
from dbig_requester.config import AnswerConfig
from dbig_requester.search import index_snapshot

CANARIES = ("zorblax", "quokka-canary", "sprocket", MODEL_KEY)
# the report's 2000: a constant from B1 until 2026-10-18, now the probes' `max_answer_chars`
ANSWER_MAX_CHARS = getattr(_answer_module, "ANSWER_MAX_CHARS", 2000)
QUESTION = "which sprocket column is the zorblax?"
REVIEWED = {
    "vendor": {"fields": [("id", "integer")], "key": ["id"]},
    "shipment": {"fields": [("id", "integer"), ("vendor_id", "integer")], "key": ["id"]},
}


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_KEY", MODEL_KEY)
    snapshot = build_snapshot(
        tmp_path, monkeypatch, {"alpha": {"tables": {"sprocket": {"fields": [("zorblax", "text")]}}}}
    )
    index_snapshot(snapshot, tmp_path / "state")
    return tmp_path / "state"


def _ask(state, base_url, *, timeout=2, client=None, max_answer_chars=ANSWER_MAX_CHARS):
    # The limit is `max_answer_chars`, unset by default since 2026-10-18; these probes set it.
    config = AnswerConfig(
        base_url=base_url, model="m", model_digest="d", api_key_env="MODEL_KEY",
        timeout_seconds=timeout, max_answer_chars=max_answer_chars,
    )
    return answer_question(state, QUESTION, config, client=client)


def _review(tmp_path, monkeypatch, base_url, *, timeout=2, name="r"):
    monkeypatch.setenv("MODEL_KEY", MODEL_KEY)
    review = ReviewConfig(
        base_url=base_url, model="m", model_digest="d", api_key_env="MODEL_KEY",
        timeout_seconds=timeout,
    )
    snapshot = build_snapshot(
        tmp_path, monkeypatch, {"alpha": {"tables": REVIEWED}}, review=review, name=name
    )
    run = builder_run(snapshot)
    return run["review_status"], run.get("partial_reasons", []), snapshot


# --- B1 -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("text", "accepted"), [
    ("x" * ANSWER_MAX_CHARS, True), ("x" * (ANSWER_MAX_CHARS + 1), False),
    # characters outside the BMP are one character each, however many bytes
    ("\U0001f600" * ANSWER_MAX_CHARS, True), ("é" * ANSWER_MAX_CHARS, True),
    ("\U0001f600" * (ANSWER_MAX_CHARS + 1), False),
])
def test_the_answer_limit_is_exact_and_counts_characters_not_bytes(state, text, accepted):
    def reply(payload):
        return json.dumps({"checked": ["x"],
            "answer": text, "citations": [payload["items"][0]["id"]],
            "outcome": "answered"})

    with ModelStub(reply) as stub:
        result = _ask(state, stub.url)
    assert ("answer" in result) is accepted, result
    if not accepted:
        assert result["error"]["code"] == "MODEL_ANSWER_TOO_LONG"


def test_the_limit_comes_from_one_constant_and_the_json_schema_names_no_number():
    schema = json.dumps(Answer.model_json_schema())
    assert str(ANSWER_MAX_CHARS) not in schema
    assert schema_bounds(Answer.model_json_schema()) == []


@pytest.mark.parametrize("length", [ANSWER_MAX_CHARS, ANSWER_MAX_CHARS + 1])
def test_ask_end_to_end_accepts_the_limit_and_refuses_one_over(state, length):
    def reply(payload):
        first = payload["items"][0]
        return json.dumps({"checked": ["x"], "answer": "q" * length, "citations": [first["id"]],
            "outcome": "answered"})

    with ModelStub(reply, reject_bounds=True) as stub:
        result = _ask(state, stub.url)
    if length <= ANSWER_MAX_CHARS:
        assert "answer" in result, result
    else:
        assert "answer" not in result and "error" in result, result
        assert "q" * 20 not in json.dumps(result)


def test_ask_sends_exactly_one_schema_and_it_has_no_bound_anywhere(state):
    with ModelStub(cite_first, reject_bounds=True) as stub:
        _ask(state, stub.url)
    assert len(stub.schemas) == 1 and stub.schemas[0]
    assert schema_bounds(stub.schemas[0]) == []
    assert stub.rejected == 0
    sent = json.dumps(stub.requests[0]["body"])
    for keyword in ("maxLength", "minLength", "maxItems", "minItems"):
        assert keyword not in sent, keyword


def test_the_builder_review_schema_carries_no_bound_a_llama_cpp_server_refuses(
    tmp_path, monkeypatch
):
    """RISK, outside B1's literal scope (plan section 9 names `answer.py` only). The review
    schema sent by the builder still carries `minItems` (cited_evidence_ids) and `maxLength`
    (reason). The report says llama.cpp accepted that schema with `max_length=256`, so this is
    unverified rather than a proven defect; `minItems` was never tested on the server. With the
    plan's own stub rule the review fails, and the whole review then ends as a partial snapshot
    on a server that rejects any bound."""
    monkeypatch.setenv("MODEL_KEY", MODEL_KEY)
    with ModelStub(support_everything, reject_bounds=True) as stub:
        status, reasons, _ = _review(tmp_path, monkeypatch, stub.url)
        sent = [schema_bounds(s) for s in stub.schemas]
    assert status == "OK", (status, reasons, sent)


# --- C1: classification --------------------------------------------------------------------------


@pytest.mark.parametrize("status_code", [400, 401, 404, 408, 413, 422, 429, 500, 502, 503, 504])
def test_ask_and_the_review_classify_every_http_status_the_same_way(
    tmp_path, monkeypatch, state, status_code
):
    """One cause, one code, whichever of the two callers saw it. In the pre-fix code a 503
    (llama.cpp answers 503 while a model loads or all slots are busy) is the same code in both."""
    with ModelStub(cite_first, status=status_code) as stub:
        asked = _ask(state, stub.url)["error"]["code"]
    with ModelStub(support_everything, status=status_code) as stub:
        status, reasons, _ = _review(tmp_path, monkeypatch, stub.url, name=f"s{status_code}")
    specific = [r for r in reasons if r != "MODEL_REVIEW_FAILED"]
    assert specific == [status] or status in specific, (status, reasons)
    assert asked == status, f"HTTP {status_code}: ask says {asked}, the review says {status}"


@pytest.mark.parametrize("status_code", [500, 502, 503, 504])
def test_a_server_side_failure_is_not_reported_as_a_rejected_request(
    tmp_path, monkeypatch, state, status_code
):
    """`MODEL_REJECTED_REQUEST` tells the operator the request was wrong (fix the schema/model
    name). A 5xx is the server's state, not the request's."""
    with ModelStub(support_everything, status=status_code) as stub:
        status, _, _ = _review(tmp_path, monkeypatch, stub.url, name=f"x{status_code}")
    assert status != "MODEL_REJECTED_REQUEST"
    with ModelStub(cite_first, status=status_code) as stub:
        assert _ask(state, stub.url)["error"]["code"] != "MODEL_REJECTED_REQUEST"


def test_a_server_that_accepts_and_never_answers_is_a_timeout(state):
    with ModelStub(cite_first, delay=3) as stub:
        assert _ask(state, stub.url, timeout=1)["error"]["code"] == "MODEL_TIMEOUT"


def test_a_server_that_accepts_then_drops_the_connection_is_not_a_timeout(state):
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
        url = f"http://127.0.0.1:{listener.getsockname()[1]}/v1"
        assert _ask(state, url, timeout=5)["error"]["code"] in {
            "MODEL_UNREACHABLE", "MODEL_UNAVAILABLE_OR_INVALID"
        }
    finally:
        listener.close()


def test_a_host_that_never_completes_the_connection_is_unreachable_not_busy(state):
    """A host that is down or firewalled drops the SYN, so the client's connect times out. That
    is `unreachable` to an operator; `MODEL_TIMEOUT` tells them to raise `timeout_seconds` for a
    busy server, which cannot help. Emulated with a listener whose accept queue is full."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(0)
    port = listener.getsockname()[1]
    fillers = []
    try:
        for _ in range(4):  # fill the queue; further SYNs are dropped by the kernel
            probe = socket.socket()
            probe.setblocking(False)
            try:
                probe.connect(("127.0.0.1", port))
            except (BlockingIOError, OSError):
                pass
            fillers.append(probe)
        check = socket.socket()
        check.settimeout(0.5)
        try:
            check.connect(("127.0.0.1", port))
            pytest.skip("this kernel does not drop SYNs on a full accept queue")
        except TimeoutError:
            pass
        except OSError:
            pytest.skip("this kernel refuses instead of dropping")
        finally:
            check.close()
        code = _ask(state, f"http://127.0.0.1:{port}/v1", timeout=1)["error"]["code"]
    finally:
        for probe in fillers:
            probe.close()
        listener.close()
    assert code == "MODEL_UNREACHABLE", code


# --- C1: structure of the reply --------------------------------------------------------------------


@pytest.mark.parametrize(
    "content",
    [
        "",
        "not json at all quokka-canary",
        "[]",
        "null",
        '{"answer": "ok"}',
        '{"answer": "ok", "citations": "x"}',
        '{"answer": 5, "citations": []}',
        '{"answer": "ok", "citations": [], "extra": 1}',
    ],
)
def test_a_malformed_reply_is_the_umbrella_code_and_never_an_exception(state, content):
    with ModelStub(cite_first, content=content) as stub:
        result = _ask(state, stub.url)
    assert result["error"]["code"] in {"MODEL_UNAVAILABLE_OR_INVALID", "MODEL_INVALID_CITATION"}
    assert "quokka-canary" not in json.dumps(result)


def test_empty_citations_and_mixed_citations_are_invalid_citation(state):
    def mixed(payload):
        return json.dumps(
            {"checked": ["x"],
                "answer": "a", "citations": [payload["items"][0]["id"], "no-such-id"],
                "outcome": "answered"}
        )

    with ModelStub(None, content=json.dumps({"checked": ["x"], "answer": "a", "citations": [],
        "outcome": "answered"})) as stub:
        assert _ask(state, stub.url)["error"]["code"] == "MODEL_INVALID_CITATION"
    with ModelStub(mixed) as stub:
        assert _ask(state, stub.url)["error"]["code"] == "MODEL_INVALID_CITATION"


def test_a_complete_reply_cut_off_at_the_token_limit_is_truncated_not_accepted(state):
    def reply(payload):
        return json.dumps({"checked": ["x"],
            "answer": "a", "citations": [payload["items"][0]["id"]],
            "outcome": "answered"})

    with ModelStub(reply, finish_reason="length") as stub:
        assert _ask(state, stub.url)["error"]["code"] == "MODEL_TRUNCATED"


def test_a_lineage_claim_is_not_reported_as_a_citation_or_a_truncation(state):
    def claim(payload):
        return json.dumps(
            {"checked": ["x"],
                "answer": "this is derived from quokka-canary", "citations": [payload["items"][0]["id"]],
                "outcome": "answered"}
        )

    with ModelStub(claim) as stub:
        result = _ask(state, stub.url)
    assert result["error"]["code"] == "MODEL_UNAVAILABLE_OR_INVALID"
    assert "quokka-canary" not in json.dumps(result)


class _ExplodingClient:
    """A client whose call raises an exception whose text is full of catalogue and prompt text."""

    class chat:  # noqa: N801
        class completions:  # noqa: N801
            @staticmethod
            def create(**kwargs):
                raise RuntimeError(f"zorblax sprocket quokka-canary {MODEL_KEY} {kwargs['messages']}")


def test_an_unexpected_exception_with_content_in_its_text_leaks_nothing(state, capfd):
    result = _ask(state, "http://127.0.0.1:9/v1", client=_ExplodingClient())
    text = json.dumps(result)
    assert result["error"]["code"] == "MODEL_UNAVAILABLE_OR_INVALID"
    out = capfd.readouterr()
    for canary in CANARIES:
        assert canary not in text + out.out + out.err


@pytest.mark.parametrize("cause", ["rejected", "truncated", "citation", "closed"])
def test_no_logger_at_info_level_carries_content_for_ask_or_review(
    tmp_path, monkeypatch, state, caplog, cause
):
    """Every logger, the HTTP client's included, at INFO (what an operator turns on). At DEBUG the
    openai library logs its own request body; the application configures no logging, so that is a
    deployment setting and is not tested here."""
    from catalogue_fixture_kit import closed_port_url

    kwargs = {
        "rejected": dict(status=400),
        "truncated": dict(finish_reason="length", content='{"checked": ["x"], "answer": "quokka-canary'),
        "citation": dict(content=json.dumps({
            "checked": ["x"], "answer": "quokka-canary", "citations": ["zz"],
            "outcome": "answered"})),
    }
    with caplog.at_level(logging.INFO):
        if cause == "closed":
            _ask(state, closed_port_url())
            _review(tmp_path, monkeypatch, closed_port_url(), name="c")
        else:
            with ModelStub(cite_first, **kwargs[cause]) as stub:
                _ask(state, stub.url)
            with ModelStub(support_everything, **kwargs[cause]) as stub:
                _review(tmp_path, monkeypatch, stub.url, name="l")
    lines = [r.getMessage() for r in caplog.records]
    for canary in CANARIES:
        assert not any(canary in line for line in lines), (canary, lines)


# --- C1: builder ---------------------------------------------------------------------------------


def test_review_failure_codes_in_the_run_are_a_closed_set_and_publish_the_candidates(
    tmp_path, monkeypatch
):
    with ModelStub(support_everything, status=503) as stub:
        status, reasons, snapshot = _review(tmp_path, monkeypatch, stub.url)
    assert "MODEL_REVIEW_FAILED" in reasons
    assert status.startswith("MODEL_") and status in reasons
    assert ("shipment", "vendor_id", "vendor", "id") in candidates(snapshot)


def test_review_citing_an_unknown_candidate_is_invalid_citation(tmp_path, monkeypatch):
    def unknown(payload):
        return json.dumps({"decisions": [
            {"candidate_id": "nope", "decision": "supports",
             "cited_evidence_ids": [payload[0]["evidence"][0]["id"]], "reason": "r"}]})

    def foreign_evidence(payload):
        return json.dumps({"decisions": [
            {"candidate_id": payload[0]["candidate_id"], "decision": "supports",
             "cited_evidence_ids": ["not-offered"], "reason": "r"}]})

    for name, reply in (("u", unknown), ("f", foreign_evidence)):
        with ModelStub(reply) as stub:
            status, _, _ = _review(tmp_path, monkeypatch, stub.url, name=name)
        assert status == "MODEL_INVALID_CITATION", (name, status)


def test_review_reply_with_too_many_or_empty_decisions(tmp_path, monkeypatch):
    """More decisions than candidates is not a citation problem and an empty list is no failure
    of the model's contract; neither may raise out of the builder."""
    with ModelStub(None, content=json.dumps({"decisions": []})) as stub:
        status, reasons, snapshot = _review(tmp_path, monkeypatch, stub.url, name="e")
    assert status == "OK" and "MODEL_REVIEW_FAILED" not in reasons
    assert ("shipment", "vendor_id", "vendor", "id") in candidates(snapshot)
