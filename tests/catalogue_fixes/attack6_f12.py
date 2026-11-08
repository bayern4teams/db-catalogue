"""Attack on P6-8 (F12): `outcome` is required in the answer schema (commit 28866a2).

Names are invented and generic. Run against the committed code with
`PYTHONPATH=<extracted 28866a2>/packages/requester/src:...` when the working tree is moving.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import pytest
from catalogue_fixture_kit import MODEL_KEY, ModelStub, build_snapshot
from dbig_requester.answer import answer_question
from dbig_requester.config import AnswerConfig
from dbig_requester.search import index_snapshot

CANARY = "quokka-canary-f12"
UMBRELLA = {"error": {"code": "MODEL_UNAVAILABLE_OR_INVALID"}}
ROOT = Path(__file__).parents[2]


@pytest.fixture
def state(tmp_path, monkeypatch):
    snapshot = build_snapshot(
        tmp_path, monkeypatch,
        {"alpha": {"tables": {"gadget": {"fields": [("gadget_website", "text")]}}}},
    )
    index_snapshot(snapshot, tmp_path / "state")
    monkeypatch.setenv("MODEL_KEY", MODEL_KEY)
    return tmp_path / "state"


def _config(url: str, **extra) -> AnswerConfig:
    return AnswerConfig(base_url=url, model="local-model", model_digest="rev-1",
                        api_key_env="MODEL_KEY", **extra)


def _ask(state, content=None, reply=None, **stub_kwargs):
    with ModelStub(reply, content=content, **stub_kwargs) as stub:
        result = answer_question(state, "which column holds the gadget website?",
                                 _config(stub.url))
        return result, list(stub.requests)


def _first_id(state):
    holder = {}

    def reply(payload):
        holder["id"] = payload["items"][0]["id"]
        return "{}"

    _ask(state, reply=reply)
    return holder["id"]


# (1) the schema actually sent ---------------------------------------------------------------


def test_sent_schema_is_strict_requires_outcome_and_has_exact_enum(state):
    _, requests = _ask(state, reply=lambda p: "{}")
    fmt = requests[0]["body"]["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    schema = fmt["json_schema"]["schema"]
    assert "outcome" in schema["required"]
    assert sorted(schema["required"]) == ["answer", "checked", "citations", "outcome"]
    outcome = schema["properties"]["outcome"]
    # Literal[...] serialises as enum (or const per member via anyOf); accept only the exact set.
    values = set(outcome.get("enum", []))
    for branch in outcome.get("anyOf", []) + outcome.get("oneOf", []):
        values |= set(branch.get("enum", [])) | ({branch["const"]} if "const" in branch else set())
    assert values == {"answered", "not_established"}, outcome
    assert "default" not in outcome and "null" not in json.dumps(outcome)


def test_schema_carries_no_bound_keywords_for_llama_cpp(state):
    """B1 must be unaffected: a stub that answers 400 to maxLength etc. still gets an answer."""
    first = _first_id(state)
    body = {"checked": ["x"], "answer": "ok", "citations": [first], "outcome": "answered"}
    reply = lambda p: json.dumps(body)  # noqa: E731 # C5: reason=short test helper lambda; approved=user, 2026-11-08; review=2027-05-08
    with ModelStub(reply, reject_bounds=True) as stub:
        result = answer_question(state, "which column holds the gadget website?", _config(stub.url))
        assert stub.rejected == 0, "the schema must carry no length or item bound"
    assert "error" not in result, result
    blob = json.dumps(stub.requests[0]["body"]["response_format"])
    for word in ("maxLength", "minLength", "maxItems", "minItems", "minimum", "maximum"):
        assert word not in blob


def test_answer_max_chars_still_enforced_after_parsing_with_outcome_required(state):
    first = _first_id(state)
    content = json.dumps({"checked": ["x"],
        "answer": "x" * 11, "citations": [first], "outcome": "answered"})
    with ModelStub(content=content) as stub:
        result = answer_question(state, "which column holds the gadget website?",
                                 _config(stub.url, max_answer_chars=10))
    assert result["error"]["code"] == "MODEL_ANSWER_TOO_LONG"


# (2) malformed outcome shapes ---------------------------------------------------------------

BAD_OUTCOMES = [None, "", "Answered", "NOT_ESTABLISHED", " answered", "answered ", "answered\n",
                "not-established", "maybe", True, False, 0, 1, [], ["answered"], {},
                {"v": "answered"}, "answered|not_established"]


@pytest.mark.parametrize("bad", BAD_OUTCOMES, ids=[repr(b) for b in BAD_OUTCOMES])
def test_a_bad_outcome_value_is_the_umbrella_code_and_echoes_nothing(state, bad):
    first = _first_id(state)
    content = json.dumps({"checked": ["x"], "answer": CANARY, "citations": [first], "outcome": bad})
    result, _ = _ask(state, content=content)
    assert result["error"]["code"] == UMBRELLA["error"]["code"], (bad, result)
    assert CANARY not in json.dumps(result)
    assert not ({"answer", "citations", "cited", "code"} & set(result))


def test_extra_fields_with_a_valid_outcome_are_refused(state):
    first = _first_id(state)
    for extra in ({"verdict": "answered"}, {"Outcome": "answered"}, {"outcome2": 1}):
        content = json.dumps({"checked": ["x"],
            "answer": CANARY, "citations": [first], "outcome": "answered",
                              **extra})
        result, _ = _ask(state, content=content)
        assert result["error"]["code"] == UMBRELLA["error"]["code"], extra
        assert CANARY not in json.dumps(result)


def test_only_a_capitalised_outcome_key_does_not_satisfy_the_requirement(state):
    first = _first_id(state)
    content = json.dumps({"answer": "a", "citations": [first], "Outcome": "answered"})
    assert _ask(state, content=content)[0]["error"]["code"] == UMBRELLA["error"]["code"]


@pytest.mark.parametrize("body", [
    {"outcome": "answered", "citations": "FIRST"},          # answer missing
    {"outcome": "not_established", "citations": []},       # answer missing
    {"outcome": "answered", "answer": CANARY},             # citations missing
    {"outcome": "not_established", "answer": CANARY},      # citations missing
    {"outcome": "answered"},
    {},
])
def test_outcome_present_but_another_required_field_missing(state, body):
    first = _first_id(state)
    body = {k: ([first] if v == "FIRST" else v) for k, v in body.items()}
    result, _ = _ask(state, content=json.dumps(body))
    assert result["error"]["code"] == UMBRELLA["error"]["code"], result
    assert CANARY not in json.dumps(result)


def test_duplicate_outcome_keys_resolve_to_a_valid_one_only(state):
    """A raw JSON with two outcome keys: last wins in pydantic; first one invalid must not
    smuggle an answer through when the last is invalid."""
    first = _first_id(state)
    raw = ('{"checked": ["x"], "answer": "a", "citations": ["' + first
           + '"], "outcome": "answered", "outcome": "bogus"}')
    assert _ask(state, content=raw)[0]["error"]["code"] == UMBRELLA["error"]["code"]


def test_outcome_nested_in_the_answer_text_is_not_a_verdict(state):
    first = _first_id(state)
    content = json.dumps({"answer": 'outcome: "not_established"', "citations": [first]})
    result, _ = _ask(state, content=content)
    assert result["error"]["code"] == UMBRELLA["error"]["code"]
    assert "code" not in result


def test_verdict_written_in_prose_with_missing_outcome_is_not_not_established(state):
    content = json.dumps({"answer": "NOT_ESTABLISHED_BY_CATALOGUE", "citations": []})
    result, _ = _ask(state, content=content)
    assert result["error"]["code"] == UMBRELLA["error"]["code"]
    assert result.get("code") is None


# (3) citation rules unchanged ----------------------------------------------------------------


def test_not_established_without_citations_is_accepted_with_its_code(state):
    content = json.dumps({"checked": ["x"], "answer": "Checked gadget.", "citations": [],
                          "outcome": "not_established"})
    result, _ = _ask(state, content=content)
    assert "error" not in result
    assert result["code"] == "NOT_ESTABLISHED_BY_CATALOGUE"
    assert result["citations"] == [] and result["cited"] == []


def test_answered_without_citations_is_refused_as_invalid_citation(state):
    content = json.dumps({"checked": ["x"],
        "answer": "It is.", "citations": [], "outcome": "answered"})
    assert _ask(state, content=content)[0]["error"]["code"] == "MODEL_INVALID_CITATION"


def test_not_established_with_a_foreign_citation_is_still_refused(state):
    content = json.dumps({"checked": ["x"], "answer": "x", "citations": ["not-retrieved"],
                          "outcome": "not_established"})
    assert _ask(state, content=content)[0]["error"]["code"] == "MODEL_INVALID_CITATION"


def test_not_established_may_cite_what_it_checked(state):
    first = _first_id(state)
    content = json.dumps({"checked": ["x"], "answer": "Checked it.", "citations": [first],
                          "outcome": "not_established"})
    result, _ = _ask(state, content=content)
    assert result["code"] == "NOT_ESTABLISHED_BY_CATALOGUE" and result["citations"] == [first]
    assert result["cited"] and result["cited"][0]["id"] == first


def test_answered_with_a_citation_has_no_code(state):
    first = _first_id(state)
    content = json.dumps({"checked": ["x"],
        "answer": "ok", "citations": [first], "outcome": "answered"})
    result, _ = _ask(state, content=content)
    assert "code" not in result and "error" not in result


def test_lineage_claim_is_refused_under_either_outcome(state):
    first = _first_id(state)
    for outcome in ("answered", "not_established"):
        content = json.dumps({"checked": ["x"],
            "answer": "a is derived from b", "citations": [first],
                              "outcome": outcome})
        code = _ask(state, content=content)[0]["error"]["code"]
        assert code == UMBRELLA["error"]["code"], outcome


def test_truncated_reply_is_truncated_not_missing_outcome(state):
    """finish_reason is checked before parsing, so a cut-off reply keeps its own code."""
    result, _ = _ask(state, content='{"answer": "' + CANARY, finish_reason="length")
    assert result["error"]["code"] == "MODEL_TRUNCATED"


def test_a_refused_reply_is_not_logged_with_content(state, caplog):
    first = _first_id(state)
    with caplog.at_level(logging.DEBUG):
        _ask(state, content=json.dumps({"answer": CANARY, "citations": [first]}))
        _ask(state, content=json.dumps({"checked": ["x"], "answer": CANARY, "citations": [first],
                                        "outcome": CANARY}))
    assert CANARY not in caplog.text


# (4) REST route and CLI --------------------------------------------------------------------------


def _rest(tmp_path, monkeypatch, state, content):
    from dbig_requester.api import create_app
    from dbig_requester.config import RequesterConfig
    from fastapi.testclient import TestClient

    monkeypatch.setenv("REQUESTER_TOKEN", "synthetic-api-token")
    with ModelStub(content=content) as stub:
        config = RequesterConfig(snapshot_dir=tmp_path / "snapshot", state_dir=state,
                                 api_key_env="REQUESTER_TOKEN", answer=_config(stub.url))
        with TestClient(create_app(config)) as client:
            return client.post("/v1/catalogue/questions",
                               json={"question": "which column holds the gadget website?"},
                               headers={"Authorization": "Bearer synthetic-api-token"})


def test_rest_route_maps_a_missing_outcome_to_503_with_the_code_only(tmp_path, monkeypatch, state):
    first = _first_id(state)
    response = _rest(tmp_path, monkeypatch, state,
                     json.dumps({"answer": CANARY, "citations": [first]}))
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == UMBRELLA["error"]["code"]
    assert CANARY not in response.text


def test_rest_route_serves_not_established_as_200_with_its_code(tmp_path, monkeypatch, state):
    response = _rest(tmp_path, monkeypatch, state, json.dumps(
        {"checked": ["x"], "answer": "Checked.", "citations": [], "outcome": "not_established"}))
    assert response.status_code == 200
    assert response.json()["code"] == "NOT_ESTABLISHED_BY_CATALOGUE"


def test_rest_route_serves_an_answered_reply(tmp_path, monkeypatch, state):
    first = _first_id(state)
    response = _rest(tmp_path, monkeypatch, state, json.dumps(
        {"checked": ["x"], "answer": "ok", "citations": [first], "outcome": "answered"}))
    assert response.status_code == 200 and response.json()["citations"] == [first]


def test_cli_ask_prints_the_umbrella_code_for_a_missing_outcome(tmp_path, monkeypatch, state):
    from click.testing import CliRunner
    from dbig_requester.cli import main

    first = _first_id(state)
    config_file = tmp_path / "requester.toml"
    with ModelStub(content=json.dumps({"answer": CANARY, "citations": [first]})) as stub:
        config_file.write_text(
            f'[requester]\nsnapshot_dir = "{tmp_path / "snapshot"}"\nstate_dir = "{state}"\n'
            'api_key_env = "REQUESTER_TOKEN"\n\n[requester.answer]\n'
            f'base_url = "{stub.url}"\nmodel = "local-model"\nmodel_digest = "rev-1"\n'
            'api_key_env = "MODEL_KEY"\n', encoding="utf-8")
        monkeypatch.setenv("REQUESTER_TOKEN", "synthetic-api-token")
        run = CliRunner().invoke(main, ["ask", "--config", str(config_file), "--question",
                                        "which column holds the gadget website?"])
    assert run.exit_code == 0, run.output
    assert json.loads(run.output)["error"]["code"] == UMBRELLA["error"]["code"]
    assert CANARY not in run.output


def test_no_code_constructs_answer_without_outcome():
    """Nothing in packages builds `Answer(...)`, so nothing can crash on the now-required field."""
    uses = []
    for path in (ROOT / "packages").rglob("*.py"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\bAnswer\b", line) and "class Answer(" not in line:
                uses.append(f"{path.name}:{number}")
    assert all(use.startswith("answer.py:") for use in uses), uses
    constructing = [
        f"{path.name}" for path in (ROOT / "packages").rglob("*.py")
        if re.search(r"\bAnswer\(|Answer\.model_construct", path.read_text(encoding="utf-8"))
        and path.name != "answer.py"
    ]
    assert constructing == [], constructing


def test_docs_and_examples_do_not_show_a_reply_without_outcome():
    """Any shipped example reply shape (docs/examples/schemas) must carry outcome."""
    schemas = list((ROOT / "schemas").glob("**/*")) if (ROOT / "schemas").exists() else []
    offenders = [p for p in schemas if p.is_file() and '"citations"' in p.read_text(errors="ignore")
                 and '"outcome"' not in p.read_text(errors="ignore")]
    assert offenders == [], offenders


# plan 2.4 -------------------------------------------------------------------------------------


def test_prompt_sentence_is_a_fixed_instruction_not_a_threshold(state):
    _, requests = _ask(state, reply=lambda p: "{}")
    system = requests[0]["body"]["messages"][0]["content"]
    assert system.count("Put the verdict only in the outcome field.") == 1
