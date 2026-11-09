"""P7-4 (F17): no bare verdicts.

Source: docs/plans/PHASE7_LOCAL_PLAN.md section 4 "P7-4" and artifacts/run-20261020-pgch/
research-f14-f17.md section F17. Written from those texts only; the implementation was not read.

Behaviour specified (not implemented when this file was written):

* `Answer` gets `checked: list[str]` as its first field; every field is required, none has a
  default. The schema sent to the server lists `checked` first in `properties` and in `required`
  and still carries no `minLength`/`minItems`/`maxLength`/`maxItems` (B1).
* The app refuses a reply with an empty `checked`, a blank `answer`, or an `answer` that is only an
  outcome word (case, punctuation, whitespace and underscore-versus-space insensitive). The code
  is `MODEL_ANSWER_UNEXPLAINED`.
* `[requester.answer] answer_retries` (default 1) extra attempts. Each retry re-sends the
  conversation plus one fixed correction message that holds no data from the question, the
  catalogue or the reply. After the retries are spent the code is returned.
* Telemetry gets only the code and the attempt count.
* TECHNICAL_IMPLEMENTATION.md section 12a lists `MODEL_ANSWER_UNEXPLAINED`.

The correction message is not specified word for word; the tests pin that it is the last message,
that it is identical for different questions and catalogues, and that it carries none of their
words. Everything runs against a local recording stub (the kit's `ModelStub` reads the LAST message
as JSON, so it cannot serve a retry). Names are invented and generic (plan section 2.4).
"""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "catalogue_fixes"))

import pytest
from catalogue_fixture_kit import MODEL_KEY, build_snapshot, schema_bounds
from dbig_requester.answer import CORRECTION, Answer, answer_question
from dbig_requester.config import AnswerConfig
from dbig_requester.search import index_snapshot

CODE = "MODEL_ANSWER_UNEXPLAINED"
ROOT = Path(__file__).resolve().parents[2]
CANARIES = ("zorblax", "quokka-canary", "sprocket", "gizmo-canary", MODEL_KEY)

CATALOGUE_ONE = {"alpha": {"tables": {"gadget": {"fields": [("gadget_website", "text")]}}}}
QUESTION_ONE = "which column holds the gadget website, zorblax?"
CATALOGUE_TWO = {"beta": {"tables": {"sprocket": {"fields": [("sprocket_colour", "text")]}}}}
QUESTION_TWO = "which column holds the sprocket colour, quokka-canary?"


class SequenceStub:
    """A loopback model server that answers request N with `replies[N]` (the last one repeats).

    A reply is a string, or a callable taking the parsed first user message (the question and the
    items) and returning a string. Every request body is recorded.
    """

    def __init__(self, *replies: Any) -> None:
        self.requests: list[dict] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_: object) -> None:
                pass

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                stub.requests.append(body)
                reply = replies[min(len(stub.requests), len(replies)) - 1]
                if callable(reply):
                    first_user = next(m for m in body["messages"] if m["role"] == "user")
                    reply = reply(json.loads(first_user["content"]))
                raw = json.dumps({
                    "id": "cmpl-1", "object": "chat.completion", "created": 0,
                    "model": body["model"],
                    "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": reply}}],
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def __enter__(self) -> SequenceStub:
        return self

    def __exit__(self, *_: object) -> None:
        self.server.shutdown()
        self.server.server_close()


def reply(answer: str, *, outcome: str = "answered", checked: Any = ("the retrieved columns",),
          cite: bool = True, drop: tuple[str, ...] = ()):
    """A model reply as a callable: cites the first item when `cite`, omits keys in `drop`."""
    def make(payload: dict) -> str:
        data = {
            "checked": list(checked),
            "answer": answer,
            "citations": [payload["items"][0]["id"]] if cite else [],
            "outcome": outcome,
        }
        return json.dumps({k: v for k, v in data.items() if k not in drop})
    return make


BARE = reply("not_established", outcome="not_established", cite=False)
EXPLAINED = reply("The only column listed is the website column of the gadget table.")


def ask(tmp_path, monkeypatch, replies, *, sources=None, question=QUESTION_ONE, **config):
    snapshot = build_snapshot(tmp_path, monkeypatch, sources or CATALOGUE_ONE)
    index_snapshot(snapshot, tmp_path / "state")
    monkeypatch.setenv("MODEL_KEY", MODEL_KEY)
    with SequenceStub(*replies) as stub:
        cfg = AnswerConfig(base_url=stub.url, model="local-model", model_digest="rev-1",
                           api_key_env="MODEL_KEY", **config)
        result = answer_question(tmp_path / "state", question, cfg)
        requests = list(stub.requests)
    return result, requests


def _is_unexplained(result: dict) -> bool:
    # P7-8 (F18) adds a structural `reason` next to the code.
    error = result.get("error") or {}
    return error.get("code") == CODE and not {"answer", "citations", "cited"} & set(result)


# --- 1. a bare verdict is retried once with a fixed message, then refused ---------------------


def test_a_bare_verdict_gets_one_retry_and_then_the_code(tmp_path, monkeypatch):
    result, requests = ask(tmp_path, monkeypatch, [BARE, BARE])
    assert _is_unexplained(result), result
    assert len(requests) == 2, "default answer_retries is 1: one attempt and one retry"


def test_the_correction_is_the_last_message_and_the_conversation_is_resent(tmp_path, monkeypatch):
    _, requests = ask(tmp_path, monkeypatch, [BARE, BARE])
    first, second = requests
    assert second["messages"][: len(first["messages"])] == first["messages"]
    assert len(second["messages"]) > len(first["messages"])
    correction = second["messages"][-1]
    assert isinstance(correction["content"], str) and correction["content"].strip()
    assert correction["role"] in {"user", "system"}
    assert second["response_format"] == first["response_format"]


def test_the_correction_is_the_same_text_for_different_questions_and_catalogues(
    tmp_path, monkeypatch
):
    """It must carry no data: two unrelated runs send byte-identical corrections."""
    leaky = reply("not_established", outcome="not_established", cite=False,
                  checked=["gizmo-canary sprocket gadget zorblax"])
    corrections = []
    for name, sources, question in (
        ("one", CATALOGUE_ONE, QUESTION_ONE), ("two", CATALOGUE_TWO, QUESTION_TWO),
    ):
        _, requests = ask(tmp_path / name, monkeypatch, [leaky, leaky], sources=sources,
                          question=question)
        assert len(requests) == 2, name
        corrections.append(requests[1]["messages"][-1])
    assert corrections[0] == corrections[1]
    text = corrections[0]["content"].casefold()
    for word in (*CANARIES, "gadget", "alpha", "beta", "website", "colour", "not_established"):
        assert word.casefold() not in text, word
    assert "rev-1" not in text


@pytest.mark.parametrize("bare", [
    "not_established", "Not established.", "  NOT_ESTABLISHED  ", "not established\n",
    "answered", "Answered.", "ANSWERED", "Not_Established!",
])
def test_an_answer_that_is_only_an_outcome_word_is_refused(tmp_path, monkeypatch, bare):
    outcome = "answered" if bare.strip().casefold().startswith("answered") else "not_established"
    bare_reply = reply(bare, outcome=outcome, cite=outcome == "answered")
    result, requests = ask(tmp_path, monkeypatch, [bare_reply, bare_reply])
    assert _is_unexplained(result), (bare, result)
    assert len(requests) == 2


def test_an_explained_not_established_still_passes(tmp_path, monkeypatch):
    """Control against an over-eager check: the word inside a real explanation is fine."""
    text = "Not established: the catalogue lists only the website column of the gadget table."
    result, requests = ask(tmp_path, monkeypatch,
                           [reply(text, outcome="not_established", cite=False)])
    assert len(requests) == 1
    assert "error" not in result, result
    assert result["code"] == "NOT_ESTABLISHED_BY_CATALOGUE" and result["answer"] == text


# --- 2. bare then explained ---------------------------------------------------------------------


def test_a_bare_reply_followed_by_an_explained_one_succeeds(tmp_path, monkeypatch):
    result, requests = ask(tmp_path, monkeypatch, [BARE, EXPLAINED])
    assert len(requests) == 2
    assert "error" not in result, result
    assert result["answer"].startswith("The only column") and result["citations"]
    assert result["cited"] and "code" not in result


# --- 3. retries are configurable ----------------------------------------------------------------


def test_answer_retries_defaults_to_one_and_is_not_negative():
    field = AnswerConfig.model_fields["answer_retries"]
    assert field.default == 1
    base = dict(base_url="http://127.0.0.1:1/v1", model="m", model_digest="d", api_key_env="K")
    assert AnswerConfig(**base, answer_retries=0).answer_retries == 0
    with pytest.raises(ValueError, match=r"answer_retries"):
        AnswerConfig(**base, answer_retries=-1)


def test_zero_retries_makes_one_request_and_returns_the_code(tmp_path, monkeypatch):
    result, requests = ask(tmp_path, monkeypatch, [BARE, EXPLAINED], answer_retries=0)
    assert len(requests) == 1
    assert _is_unexplained(result), result


def test_two_retries_allow_a_third_attempt(tmp_path, monkeypatch):
    result, requests = ask(tmp_path, monkeypatch, [BARE, BARE, EXPLAINED], answer_retries=2)
    assert len(requests) == 3 and "error" not in result, result
    result, requests = ask(tmp_path / "again", monkeypatch, [BARE, BARE, BARE, EXPLAINED],
                           answer_retries=2)
    assert len(requests) == 3, "the retries are spent after the third attempt"
    assert _is_unexplained(result), result


# --- 4. other unexplained shapes ----------------------------------------------------------------


@pytest.mark.parametrize("blank", ["", "   ", "\n\t "])
def test_a_blank_answer_is_refused(tmp_path, monkeypatch, blank):
    bad = reply(blank)
    result, requests = ask(tmp_path, monkeypatch, [bad, bad])
    assert _is_unexplained(result), (blank, result)
    assert len(requests) == 2


def test_an_empty_checked_list_is_refused(tmp_path, monkeypatch):
    bad = reply("The only column listed is the website column of the gadget table.", checked=[])
    result, requests = ask(tmp_path, monkeypatch, [bad, bad])
    assert _is_unexplained(result), result
    assert len(requests) == 2


def test_an_empty_checked_list_then_a_filled_one_succeeds(tmp_path, monkeypatch):
    empty = reply("The only column listed is the website column of the gadget table.", checked=[])
    result, requests = ask(tmp_path, monkeypatch, [empty, EXPLAINED])
    assert len(requests) == 2 and "error" not in result, result


def test_a_reply_without_checked_is_not_an_answer(tmp_path, monkeypatch):
    """Fails on the old code, which never asked for the field and accepted this reply."""
    without = reply("The only column listed is the website column of the gadget table.",
                    drop=("checked",))
    result, _ = ask(tmp_path, monkeypatch, [without, without])
    assert "error" in result and not {"answer", "citations", "cited"} & set(result), result


# --- 5. the good path is untouched --------------------------------------------------------------


def test_an_explained_reply_passes_on_the_first_attempt_with_the_same_result_shape(
    tmp_path, monkeypatch
):
    result, requests = ask(tmp_path, monkeypatch, [EXPLAINED])
    assert len(requests) == 1
    assert "error" not in result and "code" not in result, result
    assert set(result) == {"ir_version", "snapshot_digest", "answer", "citations", "cited",
                           "model_digest"}, "no `checked` or attempt count leaks into the result"
    assert result["model_digest"] == "rev-1" and result["citations"]


# --- 6. the schema ------------------------------------------------------------------------------


def test_answer_has_checked_first_and_every_field_required():
    assert list(Answer.model_fields)[0] == "checked"
    assert set(Answer.model_fields) == {"checked", "answer", "citations", "outcome"}
    assert all(f.is_required() for f in Answer.model_fields.values())


def test_the_schema_sent_lists_checked_first_and_requires_it(tmp_path, monkeypatch):
    _, requests = ask(tmp_path, monkeypatch, [EXPLAINED])
    schema = requests[0]["response_format"]["json_schema"]["schema"]
    assert list(schema["properties"])[0] == "checked"
    assert schema["required"][0] == "checked"
    assert sorted(schema["required"]) == ["answer", "checked", "citations", "outcome"]
    assert schema["properties"]["checked"]["type"] == "array"
    assert schema["properties"]["checked"]["items"] == {"type": "string"}
    assert "default" not in schema["properties"]["checked"]
    assert schema.get("additionalProperties") is False


def test_the_schema_still_carries_no_bound_keyword(tmp_path, monkeypatch):
    """B1: the fix is in the app, not in minItems/minLength."""
    _, requests = ask(tmp_path, monkeypatch, [BARE, BARE])
    assert len(requests) == 2
    for body in requests:
        assert schema_bounds(body["response_format"]["json_schema"]["schema"]) == []


# --- 7. documentation ---------------------------------------------------------------------------


def test_section_12a_or_the_codes_paragraph_lists_the_new_code():
    text = (ROOT / "TECHNICAL_IMPLEMENTATION.md").read_text(encoding="utf-8")
    assert CODE in text
    start = text.index("### 12a.")
    end = text.index("\n### ", start + 1)
    assert CODE in text[start:end], "the code must be listed inside section 12a"


# --- telemetry and logs carry the code and the count, nothing else ------------------------------


def test_telemetry_gets_the_code_and_the_attempt_count_only(tmp_path, monkeypatch):
    monkeypatch.setenv("LF_PUBLIC", "cnry-public-not-secret")
    monkeypatch.setenv("LF_SECRET", "cnry-langfuse-secret-material")
    calls: list[Any] = []

    class Span:
        def __getattr__(self, name: str):
            def record(*args: Any, **kwargs: Any) -> None:
                calls.append((name, args, kwargs))
            return record

    class FakeLangfuse:
        def __init__(self, **_: Any) -> None:
            pass

        def start_observation(self, **kwargs: Any) -> Span:
            calls.append(("start_observation", (), kwargs))
            return Span()

    monkeypatch.setitem(sys.modules, "langfuse", types.SimpleNamespace(Langfuse=FakeLangfuse))
    leaky = reply("not_established", outcome="not_established", cite=False,
                  checked=["gizmo-canary sprocket zorblax"])
    result, requests = ask(
        tmp_path, monkeypatch, [leaky] * 5, question=QUESTION_ONE, answer_retries=4,
        langfuse_url="http://127.0.0.1:3000", langfuse_public_key_env="LF_PUBLIC",
        langfuse_secret_key_env="LF_SECRET",  # noqa: S106 # C5: reason=an environment variable name; approved=user, 2026-11-08; review=2027-05-08
    )
    assert _is_unexplained(result) and len(requests) == 5
    dump = json.dumps(calls, default=str)
    for word in CANARIES:
        assert word not in dump, word
    assert CODE in dump, "the code reaches the span"
    # 5 attempts, a number no other span metadata (retrieval count, versions) can equal here.
    assert re.search(r"(?<![\w.])5(?![\w.])", dump), "the attempt count reaches the span"


def test_nothing_from_the_exchange_reaches_the_log(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO)  # DEBUG would log the SDK's own request options
    leaky = reply("not_established", outcome="not_established", cite=False,
                  checked=["gizmo-canary sprocket zorblax"])
    result, _ = ask(tmp_path, monkeypatch, [leaky, leaky])
    assert _is_unexplained(result)
    out = "\n".join(r.getMessage() for r in caplog.records) + json.dumps(result)
    for word in ("zorblax", "gizmo-canary", "quokka-canary", MODEL_KEY):
        assert word not in out, word


# --- B2: an answer with no citation gets the same bounded retry, with its own text ------------


def test_an_answer_with_no_citation_is_retried_and_then_succeeds(tmp_path, monkeypatch):
    uncited = reply("The gadget table has a website column.", cite=False)
    result, requests = ask(tmp_path, monkeypatch, [uncited, EXPLAINED])
    assert result["citations"], result
    assert len(requests) == 2
    correction = requests[1]["messages"][-1]
    assert correction["role"] == "user"
    assert correction["content"] != CORRECTION, "CORRECTION is about a missing explanation"
    assert "citations" in correction["content"]


def test_an_answer_that_never_cites_is_refused_after_the_retry(tmp_path, monkeypatch):
    uncited = reply("The gadget table has a website column.", cite=False)
    result, requests = ask(tmp_path, monkeypatch, [uncited, uncited])
    assert result["error"]["code"] == "MODEL_INVALID_CITATION"
    assert result["error"]["reason"]["citation_check"] == "none"
    assert len(requests) == 2


def test_no_retry_for_a_missing_citation_when_retries_are_off(tmp_path, monkeypatch):
    uncited = reply("The gadget table has a website column.", cite=False)
    result, requests = ask(tmp_path, monkeypatch, [uncited, EXPLAINED], answer_retries=0)
    assert result["error"]["code"] == "MODEL_INVALID_CITATION"
    assert len(requests) == 1


# --- B1: the SDK's DEBUG log never holds the question or the reply ----------------------------


def test_debug_logging_holds_neither_question_nor_reply(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    for name in ("openai", "httpx", "httpcore"):
        caplog.set_level(logging.DEBUG, logger=name)
    leaky = reply("not_established", outcome="not_established", cite=False,
                  checked=["reply-canary-text"])
    _, requests = ask(tmp_path, monkeypatch, [leaky, leaky])
    assert len(requests) == 2
    out = "\n".join(record.getMessage() for record in caplog.records)
    assert "reply-canary-text" not in out
    assert QUESTION_ONE not in out
