"""P7-8 (F18): a value-free `error.reason` on every model failure of `answer_question`.

Source: docs/plans/PHASE7_LOCAL_PLAN.md section 4 "P7-8". Written from the plan only; the
implementation was not read beyond the public interface (`answer_question`, `AnswerConfig`, the
public codes).

Behaviour specified. The public `error.code` is unchanged. `error.reason` is added: a dict of
structure only, with these keys (named in the constants below; change the constants, nowhere else,
if the lead's spec names them differently):

* `exception`: the exception class name (`ValidationError`, `APIConnectionError`, ...).
* `schema_errors`: pydantic error types (`missing`, `extra_forbidden`, `json_invalid`,
  `literal_error`), only for a reply that failed the schema.
* `finish_reason`: the server's finish reason, when a reply arrived.
* `reply_length`: an int, the length of the reply text, when a reply arrived.
* `citation_check`: `unknown_id` | `none`, only for a citation failure.

A key that does not apply is absent or None. No key may carry reply text, an ID taken from the
reply, a field name or a value: the canary tests plant a unique string in each of those places.

Names are invented and generic (plan section 2.4). Everything runs against `ModelStub`.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "catalogue_fixes"))

import pytest  # noqa: E402
from catalogue_fixture_kit import (  # noqa: E402
    MODEL_KEY,
    ModelStub,
    build_snapshot,
    cite_first,
    closed_port_url,
)
from dbig_requester import answer as answer_module  # noqa: E402
from dbig_requester.answer import answer_question  # noqa: E402
from dbig_requester.config import AnswerConfig  # noqa: E402
from dbig_requester.search import index_snapshot  # noqa: E402

EXCEPTION, SCHEMA, FINISH, LENGTH, CITATION = (
    "exception", "schema_errors", "finish_reason", "reply_length", "citation_check",
)
REASON_KEYS = {EXCEPTION, SCHEMA, FINISH, LENGTH, CITATION}
# Every pydantic error type a reply to the answer schema can produce; anything else is a leak
# or a free-text field.
SCHEMA_TYPES = {
    "missing", "extra_forbidden", "json_invalid", "literal_error", "string_type", "list_type",
    "model_type", "json_type",
}

CANARY = "xq9canaryvalue"
OTHER_CANARIES = ("zorblax", "sprocket", MODEL_KEY)
QUESTION = "which sprocket column is the zorblax?"
TIMEOUT_SECONDS = 1
SLOW_SECONDS = 3.0


# --- the model's replies: each plants the canary where a leak would come from -------------------


def _first_id(payload: dict) -> str:
    return payload["items"][0]["id"]


def _reply(**body):
    def make(payload: dict) -> str:
        return json.dumps({"checked": ["x"],
            "citations": [_first_id(payload)], "outcome": "answered", **body})
    return make


def _extra_field(payload: dict) -> str:
    return json.dumps({
        "checked": ["x"],
            "answer": f"{CANARY} text", "citations": [_first_id(payload)], "outcome": "answered",
        f"{CANARY}_name": CANARY,
    })


def _unknown_id(_: dict) -> str:
    return json.dumps({"checked": ["x"], "answer": f"{CANARY} text", "citations": [f"{CANARY}-id"],
                       "outcome": "answered"})


def _no_citation(_: dict) -> str:
    return json.dumps({"checked": ["x"],
        "answer": f"{CANARY} text", "citations": [], "outcome": "answered"})


def _missing(_: dict) -> str:
    return json.dumps({"checked": ["x"], "answer": f"{CANARY} text", "citations": [f"{CANARY}-id"]})


def _bad_outcome(payload: dict) -> str:
    return json.dumps({"checked": ["x"],
        "answer": f"{CANARY} text", "citations": [_first_id(payload)],
                       "outcome": f"{CANARY}-verdict"})


# name -> (stub kwargs, config kwargs, expected code). `reply` is given to ModelStub unless the
# case sets `content` (a raw body) or has no stub.
CAUSES: dict[str, dict] = {
    "invalid_json": dict(stub={"content": f"{{ not json {CANARY}"},
                         code="MODEL_UNAVAILABLE_OR_INVALID"),
    "missing_field": dict(stub={"reply": _missing}, code="MODEL_UNAVAILABLE_OR_INVALID"),
    "extra_field": dict(stub={"reply": _extra_field}, code="MODEL_UNAVAILABLE_OR_INVALID"),
    "bad_outcome": dict(stub={"reply": _bad_outcome}, code="MODEL_UNAVAILABLE_OR_INVALID"),
    "truncated": dict(stub={"finish_reason": "length", "content": f'{{"answer": "{CANARY}'},
                      code="MODEL_TRUNCATED"),
    "truncated_empty": dict(stub={"finish_reason": "length", "content": ""},
                            code="MODEL_TRUNCATED"),
    "unknown_id": dict(stub={"reply": _unknown_id}, code="MODEL_INVALID_CITATION"),
    "no_citation": dict(stub={"reply": _no_citation}, code="MODEL_INVALID_CITATION"),
    "too_long": dict(stub={"reply": _reply(answer=CANARY * 10)}, config={"max_answer_chars": 5},
                     code="MODEL_ANSWER_TOO_LONG"),
    "lineage_claim": dict(stub={"reply": _reply(answer=f"{CANARY} is derived from elsewhere")},
                          code="MODEL_UNAVAILABLE_OR_INVALID"),
    "unreachable": dict(stub=None, code="MODEL_UNREACHABLE"),
    "timeout": dict(stub={"reply": cite_first, "delay": SLOW_SECONDS}, code="MODEL_TIMEOUT"),
    "rejected_400": dict(stub={"reply": cite_first, "status": 400},
                         code="MODEL_REJECTED_REQUEST"),
    "server_500": dict(stub={"reply": cite_first, "status": 500},
                       code="MODEL_UNAVAILABLE_OR_INVALID"),
}
REPLY_CAUSES = [
    name for name, spec in CAUSES.items()
    if spec["stub"] and name not in {"timeout", "rejected_400", "server_500"}
]


def _config(url: str, **extra) -> AnswerConfig:
    return AnswerConfig(
        base_url=url, model="local-model", model_digest="rev-1", api_key_env="MODEL_KEY",
        timeout_seconds=TIMEOUT_SECONDS, **extra,
    )


def _snapshot(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("MODEL_KEY", MODEL_KEY)
    snapshot = build_snapshot(
        tmp_path, monkeypatch,
        {"alpha": {"tables": {"sprocket": {"fields": [("zorblax", "text")]}}}},
    )
    index_snapshot(snapshot, tmp_path / "state")
    return tmp_path / "state"


def _run(state: Path, name: str, spec: dict) -> dict:
    """One failure: its result, the model's raw reply length and the log lines it produced."""
    stub = ModelStub(**spec["stub"]) if spec["stub"] else None
    try:
        url = stub.url if stub else closed_port_url()
        return answer_question(state, QUESTION, _config(url, **spec.get("config", {})))
    finally:
        if stub:
            stub.close()


class _Collect(logging.Handler):
    def __init__(self, sink: list[logging.LogRecord]) -> None:
        super().__init__(level=logging.DEBUG)
        self.sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        self.sink.append(record)


@pytest.fixture(scope="module")
def outcomes(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("p7-8")
    results: dict[str, dict] = {}
    logs: dict[str, list[str]] = {}
    with pytest.MonkeyPatch.context() as monkeypatch:
        state = _snapshot(tmp_path, monkeypatch)
        for name, spec in CAUSES.items():
            handler_records: list[logging.LogRecord] = []
            capture = _Collect(handler_records)
            root = logging.getLogger()
            previous = root.level
            root.addHandler(capture)
            root.setLevel(logging.DEBUG)
            try:
                results[name] = _run(state, name, spec)
            finally:
                root.removeHandler(capture)
                root.setLevel(previous)
            logs[name] = [
                f"{r.getMessage()} {r.exc_text or ''} {r.args!r}" for r in handler_records
                if not r.name.startswith(("openai", "httpx", "httpcore"))
            ]
    results["_logs"] = logs  # type: ignore[assignment]
    return results


def _error(result: dict) -> dict:
    return result.get("error") or {}


def _reason(result: dict) -> dict:
    reason = _error(result).get("reason")
    assert isinstance(reason, dict), f"error.reason missing or not a dict: {_error(result)}"
    return reason


def _stub_content(name: str) -> str | None:
    return (CAUSES[name]["stub"] or {}).get("content")


# --- 1. the public code is unchanged; a reason is added -----------------------------------------


@pytest.mark.parametrize("name", list(CAUSES))
def test_code_is_unchanged_and_a_reason_is_added(outcomes, name):
    result = outcomes[name]
    assert _error(result).get("code") == CAUSES[name]["code"], result
    _reason(result)
    # Still a failure, not an answer, and the snapshot digest is still reported.
    assert "answer" not in result and "citations" not in result
    assert result.get("snapshot_digest")


@pytest.mark.parametrize("name", list(CAUSES))
def test_reason_is_structure_only(outcomes, name):
    """Only the specified keys; each of a fixed shape (no free text can fit in these)."""
    reason = _reason(outcomes[name])
    assert set(reason) <= REASON_KEYS, set(reason) - REASON_KEYS
    assert isinstance(reason[EXCEPTION], str) and reason[EXCEPTION].isidentifier()
    if reason.get(SCHEMA) is not None:
        assert isinstance(reason[SCHEMA], list) and set(reason[SCHEMA]) <= SCHEMA_TYPES
    if reason.get(LENGTH) is not None:
        assert isinstance(reason[LENGTH], int) and not isinstance(reason[LENGTH], bool)
    if reason.get(FINISH) is not None:
        assert isinstance(reason[FINISH], str) and reason[FINISH].isidentifier()
    if reason.get(CITATION) is not None:
        assert reason[CITATION] in {"unknown_id", "none"}


# --- one test per cause -------------------------------------------------------------------------


def test_invalid_json(outcomes):
    reason = _reason(outcomes["invalid_json"])
    assert reason[EXCEPTION] == "ValidationError"
    assert reason[SCHEMA] == ["json_invalid"]
    assert reason[FINISH] == "stop"
    assert reason[LENGTH] == len(_stub_content("invalid_json"))


def test_schema_missing_field(outcomes):
    reason = _reason(outcomes["missing_field"])
    assert reason[EXCEPTION] == "ValidationError"
    assert reason[SCHEMA] == ["missing"]
    assert reason[FINISH] == "stop"
    assert isinstance(reason[LENGTH], int) and reason[LENGTH] > 0


def test_schema_extra_field(outcomes):
    reason = _reason(outcomes["extra_field"])
    assert reason[EXCEPTION] == "ValidationError"
    assert reason[SCHEMA] == ["extra_forbidden"]
    assert reason[FINISH] == "stop"


def test_schema_value_outside_the_allowed_set(outcomes):
    reason = _reason(outcomes["bad_outcome"])
    assert reason[EXCEPTION] == "ValidationError"
    assert reason[SCHEMA] == ["literal_error"]


@pytest.mark.parametrize("name", ["truncated", "truncated_empty"])
def test_truncated_reports_finish_reason_and_reply_length(outcomes, name):
    reason = _reason(outcomes[name])
    assert reason[FINISH] == "length"
    assert reason[LENGTH] == len(_stub_content(name))  # 0 for an empty reply, still an int
    assert not reason.get(SCHEMA)  # the reply was never parsed


def test_citation_of_an_unknown_id(outcomes):
    reason = _reason(outcomes["unknown_id"])
    assert reason[CITATION] == "unknown_id"
    assert reason[FINISH] == "stop" and isinstance(reason[LENGTH], int)
    assert not reason.get(SCHEMA)  # the reply was valid JSON of the right shape


def test_no_citation_with_outcome_answered(outcomes):
    reason = _reason(outcomes["no_citation"])
    assert reason[CITATION] == "none"
    assert not reason.get(SCHEMA)


def test_answer_too_long(outcomes):
    reason = _reason(outcomes["too_long"])
    assert reason[LENGTH] > 5  # the whole reply, not the configured limit
    assert reason[FINISH] == "stop"
    assert reason.get(CITATION) not in {"unknown_id", "none"}


def test_lineage_claim(outcomes):
    reason = _reason(outcomes["lineage_claim"])
    assert isinstance(reason[LENGTH], int) and reason[FINISH] == "stop"
    assert not reason.get(SCHEMA)
    assert reason.get(CITATION) not in {"unknown_id", "none"}


def test_connection_error_has_no_reply_fields(outcomes):
    reason = _reason(outcomes["unreachable"])
    assert reason[EXCEPTION] == "APIConnectionError"
    assert reason.get(LENGTH) is None and reason.get(FINISH) is None
    assert not reason.get(SCHEMA)


def test_timeout(outcomes):
    reason = _reason(outcomes["timeout"])
    assert reason[EXCEPTION] == "APITimeoutError"
    assert reason.get(LENGTH) is None and reason.get(FINISH) is None


def test_rejected_request_names_the_exception_class_only(outcomes):
    reason = _reason(outcomes["rejected_400"])
    assert reason[EXCEPTION] == "BadRequestError"
    assert reason.get(LENGTH) is None
    # The server's error body ("forced status") is text from the server: never in the reason.
    assert "forced" not in json.dumps(outcomes["rejected_400"])


def test_server_error(outcomes):
    assert _reason(outcomes["server_500"])[EXCEPTION] == "InternalServerError"
    assert "forced" not in json.dumps(outcomes["server_500"])


def test_the_reasons_tell_the_causes_apart(outcomes):
    """The point of F18: the three causes that share one public code differ in `reason`."""
    shared = [n for n in CAUSES if CAUSES[n]["code"] == "MODEL_UNAVAILABLE_OR_INVALID"
              and n in REPLY_CAUSES]
    seen = {json.dumps(_reason(outcomes[n]), sort_keys=True) for n in shared}
    assert len(seen) >= 4, seen  # json_invalid, missing, extra, literal differ


# --- 2. canaries: no reply text, no ID from the reply, no field name or value --------------------


@pytest.mark.parametrize("name", list(CAUSES))
def test_no_canary_in_the_result(outcomes, name):
    text = json.dumps(outcomes[name])
    for canary in (CANARY, *OTHER_CANARIES):
        assert canary not in text, f"{canary!r} leaked into the {name} result"


@pytest.mark.parametrize("name", list(CAUSES))
def test_no_canary_in_the_application_log(outcomes, name):
    for line in outcomes["_logs"][name]:
        for canary in (CANARY, *OTHER_CANARIES):
            assert canary not in line, f"{canary!r} logged for {name}: {line[:80]}"


def test_the_canary_replies_really_carried_the_canary():
    """Control: the planted replies contain the canary where a leak would come from, so the
    absence above means something."""
    payload = {"items": [{"id": "some-id"}]}
    for make in (_extra_field, _unknown_id, _no_citation, _missing, _bad_outcome):
        assert CANARY in make(payload)
    assert CANARY in json.dumps(json.loads(_extra_field(payload)))  # in a field name and a value
    assert f"{CANARY}_name" in json.loads(_extra_field(payload))


def test_nothing_is_printed_with_content(tmp_path, monkeypatch, capfd):
    state = _snapshot(tmp_path, monkeypatch)
    for name in ("extra_field", "invalid_json", "unknown_id"):
        _run(state, name, CAUSES[name])
    out = capfd.readouterr()
    for canary in (CANARY, *OTHER_CANARIES):
        assert canary not in out.out + out.err


# --- the CLI prints it --------------------------------------------------------------------------


def test_cli_ask_prints_the_reason(tmp_path, monkeypatch):
    from click.testing import CliRunner
    from dbig_requester.cli import main

    state = _snapshot(tmp_path, monkeypatch)
    with ModelStub(_extra_field) as stub:
        config = tmp_path / "requester.toml"
        config.write_text(
            f'[requester]\nsnapshot_dir = "{tmp_path / "snapshot"}"\nstate_dir = "{state}"\n'
            f'api_key_env = "REQUESTER_TOKEN"\n\n[requester.answer]\nbase_url = "{stub.url}"\n'
            'model = "local-model"\nmodel_digest = "rev-1"\napi_key_env = "MODEL_KEY"\n',
            encoding="utf-8",
        )
        result = CliRunner().invoke(main, ["ask", "--config", str(config), "--question", QUESTION])
    assert result.exit_code == 0, result.output
    printed = json.loads(result.output)
    assert printed["error"]["code"] == "MODEL_UNAVAILABLE_OR_INVALID"
    assert printed["error"]["reason"][SCHEMA] == ["extra_forbidden"]
    for canary in (CANARY, *OTHER_CANARIES):
        assert canary not in result.output


# --- telemetry ----------------------------------------------------------------------------------


class _Span:
    """A fake span that records whatever is sent to it, under any method name."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name: str):
        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return self
        return record


def _dicts(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _dicts(value)
    elif isinstance(node, (list, tuple)):
        for value in node:
            yield from _dicts(value)


def test_telemetry_span_carries_the_reason_and_no_canary(tmp_path, monkeypatch):
    """The reason reaches the span's metadata. Verified here against a fake span: whether the real
    Langfuse span accepts the call is "implemented, unverified" until a live Langfuse is up."""
    state = _snapshot(tmp_path, monkeypatch)
    span = _Span()
    monkeypatch.setattr(answer_module, "_trace", lambda config, count: span)
    with ModelStub(_extra_field) as stub:
        result = answer_question(state, QUESTION, _config(stub.url))
    reason = _reason(result)
    assert [name for name, *_ in span.calls].count("end") == 1  # the span is still ended
    carried = [
        d for d in _dicts([(a, k) for _, a, k in span.calls])
        if all(d.get(key) == value for key, value in reason.items())
    ]
    assert carried, f"no span call carried the reason {reason}: {span.calls}"
    sent = repr(span.calls)
    for canary in (CANARY, *OTHER_CANARIES):
        assert canary not in sent, canary


def test_a_failing_span_does_not_change_the_result(tmp_path, monkeypatch):
    """Telemetry failure must not hide the failure from the caller (plan: publication survives)."""
    state = _snapshot(tmp_path, monkeypatch)

    class Broken:
        def __getattr__(self, name: str):
            def boom(*_, **__):
                raise RuntimeError(f"{CANARY} telemetry down")
            return boom

    monkeypatch.setattr(answer_module, "_trace", lambda config, count: Broken())
    with ModelStub(_extra_field) as stub:
        result = answer_question(state, QUESTION, _config(stub.url))
    assert _error(result)["code"] == "MODEL_UNAVAILABLE_OR_INVALID"
    assert _reason(result)[SCHEMA] == ["extra_forbidden"]
    assert CANARY not in json.dumps(result)


# --- controls that pass today -------------------------------------------------------------------


def test_a_good_answer_has_no_error_and_no_reason(tmp_path, monkeypatch):
    state = _snapshot(tmp_path, monkeypatch)
    with ModelStub(cite_first) as stub:
        result = answer_question(state, QUESTION, _config(stub.url))
    assert "error" not in result and result["answer"] and result["citations"]


def test_not_established_is_not_a_failure(tmp_path, monkeypatch):
    state = _snapshot(tmp_path, monkeypatch)
    reply = _reply(answer="not shown by the catalogue", outcome="not_established")
    with ModelStub(reply) as stub:
        result = answer_question(state, QUESTION, _config(stub.url))
    assert "error" not in result and result["code"] == "NOT_ESTABLISHED_BY_CATALOGUE"


def test_a_failure_before_the_model_has_no_reason(tmp_path, monkeypatch):
    """The reason is for model failures. No match is the retrieval's code and stays as it is."""
    state = _snapshot(tmp_path, monkeypatch)
    result = answer_question(state, "zebra giraffe", _config("http://127.0.0.1:9/v1"))
    assert _error(result) == {"code": "NO_CATALOGUE_MATCH"}

