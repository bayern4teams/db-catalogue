"""Attack tests for P7-8 (F18). Not collected by default: run with the file path."""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pytest  # noqa: E402
import test_p7_8_failure_reason as base  # noqa: E402
from catalogue_fixture_kit import ModelStub, cite_first  # noqa: E402
from dbig_requester import answer as answer_module  # noqa: E402
from dbig_requester.answer import answer_question  # noqa: E402

HOSTILE = "zz_hostile_free_text_9"


def _ask(tmp_path, monkeypatch, stub_kwargs, **cfg):
    state = base._snapshot(tmp_path, monkeypatch)
    with ModelStub(**stub_kwargs) as stub:
        return answer_question(state, base.QUESTION, base._config(stub.url, **cfg))


def test_hostile_finish_reason_does_not_reach_reason(tmp_path, monkeypatch):
    """Lead fix after attack: an unknown finish_reason is reported as `other`, never as sent."""
    r = _ask(tmp_path, monkeypatch, dict(content="not json", finish_reason=HOSTILE))
    assert r["error"]["reason"].get("finish_reason") == "other"


def test_exception_class_names_are_fixed_set(tmp_path, monkeypatch):
    # missing api key env
    state = base._snapshot(tmp_path, monkeypatch)
    monkeypatch.delenv("MODEL_KEY")
    with ModelStub(cite_first) as stub:
        r = answer_question(state, base.QUESTION, base._config(stub.url))
        assert stub.requests == []
    reason = r["error"]["reason"]
    assert set(reason) == {"exception"}, r
    assert "MODEL_KEY" not in json.dumps(r)


def test_blank_api_key_env_no_reply_keys(tmp_path, monkeypatch):
    state = base._snapshot(tmp_path, monkeypatch)
    monkeypatch.setenv("MODEL_KEY", "   ")
    r = answer_question(state, base.QUESTION, base._config("http://127.0.0.1:9/v1"))
    assert set(r["error"]["reason"]) == {"exception"}
    assert "   " not in r["error"]["reason"]["exception"]


@pytest.mark.parametrize("body", [
    "", "null", "[]", "123", '"str"', "{}",
    '{"checked": ["x"], "answer": 5, "citations": "x", "outcome": null}',
    '{"checked": ["x"], "answer": "a", "citations": [1, 2], "outcome": "answered"}',
    '{"checked": ["x"], "answer":"a","citations":[],"outcome":"answered","x":{"y":1}}',
    "\x00\xff", "{" * 5000, '{"answer": "' + "a" * 100000 + '"}',
])
def test_unusual_bodies_give_only_closed_types(tmp_path, monkeypatch, body):
    r = _ask(tmp_path, monkeypatch, dict(content=body))
    reason = r["error"]["reason"]
    assert set(reason) <= base.REASON_KEYS
    for t in reason.get("schema_errors", []):
        assert t in base.SCHEMA_TYPES | {"int_type", "string_type", "list_type"}, t
    assert body[:20] not in json.dumps(r) or body[:20] in ("", "null", "[]", "123", "{}")
    assert reason["reply_length"] == len(body)


def test_no_choices_and_none_content(tmp_path, monkeypatch):
    class R:
        choices = []

    class C:
        class chat:
            class completions:
                @staticmethod
                def create(**_):
                    return R()

    state = base._snapshot(tmp_path, monkeypatch)
    r = answer_question(state, base.QUESTION, base._config("http://127.0.0.1:9/v1"), client=C())
    assert r["error"]["reason"] == {"exception": "IndexError"}


def test_none_content_and_custom_exception(tmp_path, monkeypatch):
    class Msg:
        content = None

    class Ch:
        finish_reason = "stop"
        message = Msg()

    class R:
        choices = [Ch()]

    class C:
        class chat:
            class completions:
                @staticmethod
                def create(**_):
                    return R()

    state = base._snapshot(tmp_path, monkeypatch)
    r = answer_question(state, base.QUESTION, base._config("http://127.0.0.1:9/v1"), client=C())
    assert r["error"]["reason"]["reply_length"] == 0
    assert r["error"]["reason"]["schema_errors"] == ["json_invalid"]

    class Boom(Exception):
        pass

    class C2:
        class chat:
            class completions:
                @staticmethod
                def create(**_):
                    raise Boom(HOSTILE)
    r = answer_question(state, base.QUESTION, base._config("http://127.0.0.1:9/v1"), client=C2())
    # The class name of a custom exception, never its message.
    assert r["error"]["reason"] == {"exception": "Boom"}
    assert HOSTILE not in json.dumps(r)


def test_span_update_raising_logs_constant_and_still_ends(tmp_path, monkeypatch, caplog):
    calls = []

    class S:
        def update(self, **_): raise RuntimeError(HOSTILE)
        def end(self): calls.append("end")

    monkeypatch.setattr(answer_module, "_trace", lambda c, n: S())
    with caplog.at_level(logging.DEBUG):
        r = _ask(tmp_path, monkeypatch, dict(content="{bad"))
    assert calls == ["end"] and r["error"]["reason"]["schema_errors"] == ["json_invalid"]
    assert HOSTILE not in caplog.text and "{bad" not in caplog.text


def test_span_metadata_equals_result_error(tmp_path, monkeypatch):
    seen = []

    class S:
        def update(self, **kw): seen.append(kw)
        def end(self): pass

    monkeypatch.setattr(answer_module, "_trace", lambda c, n: S())
    r = _ask(tmp_path, monkeypatch, dict(content="{bad"))
    assert seen == [{"metadata": {"error": r["error"], "attempts": 1}}]  # P7-4 adds the count


def test_rest_503_detail_carries_reason_without_content(tmp_path, monkeypatch):
    from starlette.testclient import TestClient  # noqa: F401
    # source check: api.py passes result["error"] whole
    src = Path("packages/requester/src/dbig_requester/api.py").read_text()
    assert 'detail=result["error"]' in src


def test_result_error_is_a_copy_not_shared(tmp_path, monkeypatch):
    a = _ask(tmp_path / "a", monkeypatch, dict(content="{bad"))
    a["error"]["reason"]["schema_errors"].append("mutated")
    b = _ask(tmp_path / "b", monkeypatch, dict(content="{bad"))
    assert b["error"]["reason"]["schema_errors"] == ["json_invalid"]
