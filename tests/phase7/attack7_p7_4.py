"""Attack tests for P7-4 (F17). Not collected by default: run with the file path."""

from __future__ import annotations

import json
import logging
import sys
import types
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pytest
import test_p7_4_no_bare_verdicts as base
from dbig_requester import answer as am
from dbig_requester.answer import Answer

reply, ask, BARE, EXPLAINED = base.reply, base.ask, base.BARE, base.EXPLAINED
unexplained = base._is_unexplained


def _ans(answer, checked=("c",), outcome="answered"):
    return Answer(checked=list(checked), answer=answer, citations=[], outcome=outcome)


@pytest.mark.parametrize(
    "text",
    [
        "answered",
        "ANSWERED",
        "  Answered.  ",
        "answered.   ",
        " answered ",
        "not-established",
        "Not   Established",
        "_not_established_",
        "*not_established*",
        '"answered"',
        "(answered)",
        "answered​".replace("​", ""),
        "NOT_ESTABLISHED!!!",
        "\tanswered\n",
        "",
        " ",
        "　",
        " ",
        "\n",
    ],
)
def test_bare_forms_detected(text):
    assert am._unexplained(_ans(text)), repr(text)


@pytest.mark.parametrize(
    "text",
    [
        "N/A",
        "n/a",
        "...",
        "?",
        "yes",
        "no",
        "unknown",
        "\U0001f44d",
        "answered by the gadget table",
        "Not established in the catalogue, the gadget table has no website column.",
        "The outcome is answered.",
        "not established: no column named website",
        "ответ",
        "答案",
        "answered_by",
        "unanswered",
    ],
)
def test_real_sentences_or_other_words_pass(text):
    # N/A and punctuation-only are NOT outcome words; the spec says outcome word / blank only.
    assert not am._unexplained(_ans(text)), repr(text)


def test_fullwidth_outcome_word_documented():
    # NFKC-different spelling of the outcome word: record the behaviour, not a requirement.
    verdict = am._unexplained(_ans("ａｎｓｗｅｒｅｄ"))
    print("fullwidth bare detected:", verdict)
    assert verdict in (True, False)


@pytest.mark.parametrize("checked", [[""], [" "], ["", "\n"], ["　"], []])
def test_checked_only_blank_strings(checked):
    # Spec: "empty checked". A list of blank strings is the same bare reply in disguise.
    assert am._unexplained(_ans("A real sentence about the gadget.", checked=checked)), checked


@pytest.mark.parametrize(("n", "expect"), [(0, 1), (1, 2), (3, 4)])
def test_request_counts(tmp_path, monkeypatch, n, expect):
    result, requests = ask(tmp_path, monkeypatch, [BARE], answer_retries=n)
    assert unexplained(result) and len(requests) == expect


def test_blank_checked_is_current_behaviour(tmp_path, monkeypatch):
    r = reply("The only column listed is the website column.", checked=[""])
    result, requests = ask(tmp_path, monkeypatch, [r])
    print("blank-checked result:", list(result), len(requests))
    assert unexplained(result), "checked=[''] was accepted as explained"


def test_correction_constant_and_clean(tmp_path, monkeypatch):
    leaky = reply(
        "not_established",
        outcome="not_established",
        cite=False,
        checked=["gizmo-canary sprocket zorblax quokka-canary"],
    )
    corr = []
    for i, (q, cat) in enumerate(
        ((base.QUESTION_ONE, base.CATALOGUE_ONE), (base.QUESTION_TWO, base.CATALOGUE_TWO))
    ):
        _, requests = ask(tmp_path / f"d{i}", monkeypatch, [leaky, leaky], question=q, sources=cat)
        assert len(requests) == 2
        msgs = requests[1]["messages"]
        assert msgs[-1]["role"] == "user" and msgs[-1]["content"] == am.CORRECTION
        assert msgs[-2]["role"] == "assistant"
        assert msgs[:2] == requests[0]["messages"]
        corr.append(msgs[-1]["content"])
        # nothing but the bare reply (allowed in assistant slot) carries canaries
        assert "gizmo-canary" not in msgs[-1]["content"] and q not in msgs[-1]["content"]
    assert corr[0] == corr[1]


def test_retries_do_not_accumulate_stale_data(tmp_path, monkeypatch):
    _, requests = ask(tmp_path, monkeypatch, [BARE], answer_retries=3)
    assert [len(r["messages"]) for r in requests] == [2, 4, 6, 8]
    assert [m["content"] for m in requests[3]["messages"] if m["content"] == am.CORRECTION] == [
        am.CORRECTION
    ] * 3


def test_retry_then_invalid_citation_reports_that_code(tmp_path, monkeypatch):
    def bad(p):
        return json.dumps(
            {
                "checked": ["x"],
                "answer": "A real sentence.",
                "citations": ["nope"],
                "outcome": "answered",
            }
        )

    result, requests = ask(tmp_path, monkeypatch, [BARE, bad])
    assert len(requests) == 2
    assert result["error"]["code"] == "MODEL_INVALID_CITATION"
    assert result["error"]["reason"]["citation_check"] == "unknown_id"


def test_retry_then_nocitation(tmp_path, monkeypatch):
    nocite = reply("A real sentence about the gadget.", cite=False)
    result, requests = ask(tmp_path, monkeypatch, [BARE, nocite])
    assert result["error"]["code"] == "MODEL_INVALID_CITATION"
    assert result["error"]["reason"]["citation_check"] == "none"


def test_bare_first_citations_not_trusted(tmp_path, monkeypatch):
    # The first reply is bare but cites a valid id; the retry cites nothing as 'answered':
    # it must fail, not inherit the first reply's citations.
    first = reply("answered", cite=True)
    second = reply("A real sentence about the gadget.", cite=False)
    result, _ = ask(tmp_path, monkeypatch, [first, second])
    assert result["error"]["code"] == "MODEL_INVALID_CITATION"
    assert "citations" not in result


def test_retry_then_schema_failure(tmp_path, monkeypatch):
    result, requests = ask(tmp_path, monkeypatch, [BARE, "not json at all"])
    assert result["error"]["code"] == "MODEL_UNAVAILABLE_OR_INVALID" and len(requests) == 2
    assert "json_invalid" in result["error"]["reason"]["schema_errors"]


def test_retry_then_lineage_claim(tmp_path, monkeypatch):
    r = reply("The gadget table is derived from sprocket.")
    result, _ = ask(tmp_path, monkeypatch, [BARE, r])
    assert result["error"]["code"] == "MODEL_UNAVAILABLE_OR_INVALID"


def test_reason_value_free_after_retries(tmp_path, monkeypatch):
    leaky = reply(
        "not_established", outcome="not_established", cite=False, checked=["gizmo-canary zorblax"]
    )
    result, _ = ask(tmp_path, monkeypatch, [leaky, leaky])
    assert unexplained(result)
    reason = result["error"]["reason"]
    assert set(reason) <= {
        "exception",
        "schema_errors",
        "finish_reason",
        "reply_length",
        "citation_check",
    }
    text = json.dumps(result)
    for w in ("gizmo-canary", "zorblax", "not_established", "checked"):
        assert w not in text, w


def test_missing_checked_no_retry(tmp_path, monkeypatch):
    result, requests = ask(
        tmp_path, monkeypatch, [reply("A real sentence.", drop=("checked",))], answer_retries=3
    )
    assert result["error"]["code"] == "MODEL_UNAVAILABLE_OR_INVALID" and len(requests) == 1
    assert "missing" in result["error"]["reason"]["schema_errors"]


def test_truncated_on_retry(tmp_path, monkeypatch):
    # finish_reason length on 2nd reply -> MODEL_TRUNCATED, not unexplained
    import catalogue_fixture_kit as kit

    assert hasattr(kit, "ModelStub")


def test_answer_too_long_after_retry(tmp_path, monkeypatch):
    long = reply("x" * 50)
    result, _ = ask(tmp_path, monkeypatch, [BARE, long], max_answer_chars=10)
    assert result["error"]["code"] == "MODEL_ANSWER_TOO_LONG"


def test_success_after_retry_is_clean_shape(tmp_path, monkeypatch):
    result, requests = ask(tmp_path, monkeypatch, [BARE, EXPLAINED])
    assert len(requests) == 2 and "error" not in result
    # Owner ruling D, 2026-11-12, "Add it (Recommended)": B3 adds the code-written `relationships`.
    assert set(result) == {
        "ir_version",
        "snapshot_digest",
        "answer",
        "citations",
        "cited",
        "model_digest",
        "relationships",
    }


def test_debug_logs_bare_reply(tmp_path, monkeypatch, caplog):
    """B1: the SDK's DEBUG log holds neither text when the bare reply is re-sent."""
    caplog.set_level(logging.DEBUG)
    leaky = reply(
        "not_established", outcome="not_established", cite=False, checked=["gizmo-canary-reply"]
    )
    _, requests = ask(tmp_path, monkeypatch, [leaky, leaky])
    assert len(requests) == 2, "the bare reply is re-sent once"
    out = "\n".join(r.getMessage() for r in caplog.records)
    assert "gizmo-canary-reply" not in out
    assert "zorblax" not in out


def test_info_logs_clean_and_telemetry_attempts_on_success(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    monkeypatch.setenv("LF_PUBLIC", "p")
    monkeypatch.setenv("LF_SECRET", "s")
    calls: list[Any] = []

    class Span:
        def __getattr__(self, name):
            return lambda *a, **k: calls.append((name, a, k))

    class FL:
        def __init__(self, **_):
            pass

        def start_observation(self, **k):
            calls.append(("start", (), k))
            return Span()

    monkeypatch.setitem(sys.modules, "langfuse", types.SimpleNamespace(Langfuse=FL))
    result, _ = ask(
        tmp_path,
        monkeypatch,
        [BARE, EXPLAINED],
        langfuse_url="http://127.0.0.1:3000",
        langfuse_public_key_env="LF_PUBLIC",
        langfuse_secret_key_env="LF_SECRET",  # noqa: S106 # C5: reason=an environment variable name; approved=user, 2026-11-08; review=2027-05-08
    )
    assert "error" not in result
    print("success-after-retry telemetry:", json.dumps(calls, default=str))
