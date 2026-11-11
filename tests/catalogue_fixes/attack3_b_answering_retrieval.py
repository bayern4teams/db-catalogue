# ruff: noqa: E501 # C5: reason=adversarial test file kept as its author wrote it; approved=user, 2026-11-08; review=2027-05-08
"""Attack checks for DEC-5, DEC-6, DEC-7, DEC-8, DEC-9 (requester: answering and retrieval).

Derived from `artifacts/engineer-fixes.md` (D5, D6, D8, C4) and the Decision text in HANDOFF.md,
before the implementation was opened. The only interface facts used beyond that text are the
public calls the existing tests use (`index_snapshot`, `search`, `answer_question`, the model
reply shape `answer`/`citations`, and the reply fields named in TECHNICAL_IMPLEMENTATION.md
17.3: `outcome`, `code`, `cited`).

Not collected by default. Run by path:

    uv run --python 3.12 pytest tests/catalogue_fixes/attack3_b_answering_retrieval.py -q -p no:cacheprovider

A failure is a defect, a mutation target, or (marked in the test name `probe_`) a hardening
probe whose result is reported. Names are invented and generic.
"""

from __future__ import annotations

import json
import logging
import sqlite3

import pytest
from catalogue_fixture_kit import (
    MODEL_KEY,
    ModelStub,
    build_snapshot,
    cite_first,
    schema_bounds,
)
from dbig_contracts.artifact import read_artifact
from dbig_requester.answer import answer_question
from dbig_requester.config import AnswerConfig
from dbig_requester.search import SearchRequest, index_snapshot, search

CANARY = "zq9-canary-value-77"


# --- helpers -------------------------------------------------------------------------------------


def _cfg(stub, **extra):
    return AnswerConfig(
        base_url=stub.url, model="local-model", model_digest="rev-1", api_key_env="MODEL_KEY",
        **extra,
    )


def _payload(stub):
    return json.loads(stub.requests[-1]["body"]["messages"][-1]["content"])


def _ids_in(payload):
    """Every string that looks like an id anywhere in the payload (flat scan)."""
    return json.dumps(payload)


def _state(tmp_path, monkeypatch, sources, *, name="s", exclude=None):
    snapshot = build_snapshot(tmp_path, monkeypatch, sources, name=name)
    state = tmp_path / f"state-{name}"
    if exclude is None:
        index_snapshot(snapshot, state)
    else:
        index_snapshot(snapshot, state, exclude)
    return snapshot, state


def _ask(state, question, monkeypatch, reply=cite_first, *, stub_kwargs=None, **cfg):
    monkeypatch.setenv("MODEL_KEY", MODEL_KEY)
    with ModelStub(reply, **(stub_kwargs or {})) as stub:
        result = answer_question(state, question, _cfg(stub, **cfg))
        return result, list(stub.requests), stub.schemas


def _edges(snapshot):
    _, data = read_artifact(snapshot)
    return list(data["edges.jsonl"])


def _nodes(snapshot):
    _, data = read_artifact(snapshot)
    return {n.id: n for n in data["nodes.jsonl"]}


def _edge(snapshot, kind, child, parent):
    nodes = _nodes(snapshot)
    return next(
        e for e in _edges(snapshot)
        if e.kind == kind and nodes[e.from_id].name == child and nodes[e.to_id].name == parent
    )


def _answer_with(**fields):
    def reply(payload):
        body = {"checked": ["x"],
            "answer": "ok", "citations": [payload["items"][0]["id"]], "outcome": "answered"}
        body.update(fields)
        return json.dumps(body)

    return reply


def _static(body):
    return lambda payload: json.dumps(body)


def _rel_world():
    """alpha: gadget <- part (candidate by name), widget -> part (declared FK). Plus an unrelated
    pair (sprocket <- cog) that must never reach a question about gadget."""
    return {
        "alpha": {
            "kind": "postgresql",
            "tables": {
                "gadget": {"fields": [("id", "integer"), ("title", "text")], "key": ["id"]},
                "part": {"fields": [("id", "integer"), ("gadget_id", "integer")], "key": ["id"]},
                "widget": {
                    "fields": [("id", "integer"), ("part_ref", "integer")],
                    "key": ["id"],
                    "foreign": [("part_ref", "part", "id")],
                },
                "sprocket": {"fields": [("id", "integer"), ("label", "text")], "key": ["id"]},
                "cog": {"fields": [("id", "integer"), ("sprocket_id", "integer")], "key": ["id"]},
            },
        }
    }


# =============================== DEC-5: relationships in the model context =====================


def test_dec5_relationships_with_status_reach_the_model(tmp_path, monkeypatch):
    snapshot, state = _state(tmp_path, monkeypatch, _rel_world())
    result, requests, _ = _ask(state, "how does part relate to gadget", monkeypatch)
    assert "error" not in result
    sent = json.dumps(_payload_of(requests)).lower()
    _edges(snapshot)
    candidate = _edge(snapshot, "REFERENCE_CANDIDATE", "part", "gadget")
    # the edge is in the context by id (citable) and says what kind it is
    assert candidate.id in sent
    assert "gadget_id" in sent  # the column pair names, not only the table names
    assert "candidate" in sent


def test_dec5_declared_and_candidate_are_distinguishable(tmp_path, monkeypatch):
    snapshot, state = _state(tmp_path, monkeypatch, _rel_world())
    _, requests, _ = _ask(state, "widget part gadget links", monkeypatch)
    sent = json.dumps(_payload_of(requests)).lower()
    declared = _edge(snapshot, "DECLARES_FK", "widget", "part")
    candidate = _edge(snapshot, "REFERENCE_CANDIDATE", "part", "gadget")
    assert declared.id in sent and candidate.id in sent
    assert "declared" in sent and ("tentative" in sent or "candidate" in sent)


def test_dec5_unrelated_relationships_are_not_sent(tmp_path, monkeypatch):
    snapshot, state = _state(tmp_path, monkeypatch, _rel_world())
    _, requests, _ = _ask(state, "gadget title", monkeypatch)
    sent = json.dumps(_payload_of(requests))
    nodes = _nodes(snapshot)
    for edge in _edges(snapshot):
        touched = {nodes[edge.from_id].name, nodes[edge.to_id].name}
        if touched == {"sprocket", "cog"}:
            assert edge.id not in sent, "relationship of objects that were not retrieved leaked"


def test_dec5_limit_bounds_and_zero_disables(tmp_path, monkeypatch):
    # six objects all named in the question, a chain of candidates between them
    tables = {
        f"node{n}": {
            "fields": [("id", "integer")] + ([(f"node{n - 1}_id", "integer")] if n else []),
            "key": ["id"],
        }
        for n in range(7)
    }
    snapshot, state = _state(tmp_path, monkeypatch, {"alpha": {"tables": tables}})
    ids = [e.id for e in _edges(snapshot) if e.kind == "REFERENCE_CANDIDATE"]
    assert len(ids) >= 5
    question = " ".join(f"node{n}" for n in range(7))
    for limit in (2, 3):
        _, requests, _ = _ask(state, question, monkeypatch, relationship_limit=limit)
        sent = json.dumps(_payload_of(requests))
        assert sum(1 for i in ids if i in sent) <= limit
    _, requests, _ = _ask(state, question, monkeypatch, relationship_limit=0)
    sent = json.dumps(_payload_of(requests))
    assert not any(i in sent for i in ids)
    _, requests, _ = _ask(state, question, monkeypatch, relationship_limit=200)
    sent = json.dumps(_payload_of(requests))
    assert sum(1 for i in ids if i in sent) == len(ids)


def test_dec5_limit_keeps_declared_before_candidates(tmp_path, monkeypatch):
    snapshot, state = _state(tmp_path, monkeypatch, _rel_world())
    declared = _edge(snapshot, "DECLARES_FK", "widget", "part")
    _, requests, _ = _ask(state, "widget part gadget", monkeypatch, relationship_limit=1)
    assert declared.id in json.dumps(_payload_of(requests))


def test_dec5_relationship_id_is_a_valid_citation_and_foreign_one_is_not(tmp_path, monkeypatch):
    snapshot, state = _state(tmp_path, monkeypatch, _rel_world())
    candidate = _edge(snapshot, "REFERENCE_CANDIDATE", "part", "gadget")
    ok, _, _ = _ask(
        state, "part gadget", monkeypatch,
        _static({"checked": ["x"],
            "answer": "linked", "citations": [candidate.id], "outcome": "answered"}),
    )
    assert "error" not in ok, ok
    # an edge that exists in the snapshot but was not in the context (limit 0) is not citable
    bad, _, _ = _ask(
        state, "part gadget", monkeypatch,
        _static({"checked": ["x"], "answer": "linked", "citations": [candidate.id],
            "outcome": "answered"}), relationship_limit=0,
    )
    assert bad.get("error", {}).get("code") == "MODEL_INVALID_CITATION", bad


def test_dec5_no_values_in_context(tmp_path, monkeypatch):
    world = _rel_world()
    world["alpha"]["tables"]["gadget"]["values"] = {"id": [CANARY, CANARY + "x", CANARY + "y", CANARY + "z"]}
    world["alpha"]["tables"]["part"]["values"] = {"gadget_id": [CANARY, CANARY + "x", CANARY + "y", CANARY + "z"]}
    _, state = _state(tmp_path, monkeypatch, world)
    _, requests, _ = _ask(state, "part gadget", monkeypatch)
    assert CANARY not in json.dumps(requests)


def test_dec5_candidate_evidence_text_is_the_builders_sentence(tmp_path, monkeypatch):
    snapshot, state = _state(tmp_path, monkeypatch, _rel_world())
    candidate = _edge(snapshot, "REFERENCE_CANDIDATE", "part", "gadget")
    _, requests, _ = _ask(state, "part gadget", monkeypatch)
    # whatever the evidence says about unchecked values must survive into the context
    assert "values not compared" in json.dumps(_payload_of(requests)).lower(), candidate.explanation


def test_dec5_limit_validation(tmp_path):
    with pytest.raises(ValueError, match=r"relationship_limit"):
        AnswerConfig(base_url="http://127.0.0.1:1/v1", model="m", model_digest="d", api_key_env="K", relationship_limit=-1)


def _payload_of(requests):
    return json.loads(requests[-1]["body"]["messages"][-1]["content"])


# =============================== DEC-6: claims, not words ======================================


@pytest.mark.parametrize("question", ["lineage of gadget", "gadget LINEAGE?", "Lineage gadget", "what is the Lineage of the gadget title"])
def test_dec6_the_word_lineage_in_a_question_does_not_refuse(tmp_path, monkeypatch, question):
    _, state = _state(tmp_path, monkeypatch, _rel_world())
    result, requests, _ = _ask(state, question, monkeypatch)
    assert result.get("error", {}).get("code") != "LINEAGE_REQUIRES_PLANNER", result
    assert "error" not in result, result
    assert requests, "the model must be called"


def test_dec6_answer_denying_lineage_is_accepted(tmp_path, monkeypatch):
    _, state = _state(tmp_path, monkeypatch, _rel_world())
    reply = _answer_with(answer="The catalogue holds no lineage for this table.")
    result, _, _ = _ask(state, "gadget", monkeypatch, reply)
    assert "error" not in result, result


@pytest.mark.parametrize(
    "claim",
    [
        "gadget is derived from part.",
        "Gadget IS DERIVED FROM part.",
        "data flows into gadget.",
        "Data FLOWS INTO gadget",
        "the job writes to gadget",
        "Lineage: part -> gadget is established, gadget is derived from part",
        "gadget is derived from part",  # no trailing punctuation
        "gadget\nis derived from part",  # newline in front
    ],
)
def test_dec6_claim_phrases_are_still_rejected(tmp_path, monkeypatch, claim):
    _, state = _state(tmp_path, monkeypatch, _rel_world())
    result, _, _ = _ask(state, "gadget", monkeypatch, _answer_with(answer=claim))
    assert "error" in result, f"fabricated lineage claim accepted: {claim!r}"


@pytest.mark.parametrize(
    "claim",
    [
        "gadget is derived  from part.",  # double space
        "gadget is derived\nfrom part.",  # newline inside phrase
        "gadget is derived from part.",  # no-break space
        "gadget is DERIVED\tfrom part.",  # tab
        "gadget flows into part",  # em space
    ],
)
def test_dec6_probe_whitespace_variants_of_claim_phrases(tmp_path, monkeypatch, claim):
    _, state = _state(tmp_path, monkeypatch, _rel_world())
    result, _, _ = _ask(state, "gadget", monkeypatch, _answer_with(answer=claim))
    assert "error" in result, f"claim phrase bypassed by whitespace variant: {claim!r}"


@pytest.mark.parametrize(
    "claim",
    [
        "gadget is fed by part.",
        "gadget is populated from part.",
        "part loads into gadget",
        "gadget is built from part",
        "the lineage of gadget is part",
    ],
)
def test_dec6_probe_paraphrased_claims(tmp_path, monkeypatch, claim):
    """Not in the Decision's phrase list (derived from / flows into / writes to). The guarantee
    is the structured output (owner D8, 2026-11-10, "Code writes it"): the reply cites a table,
    so no relationship is claimed, whatever its text says."""
    snapshot, state = _state(tmp_path, monkeypatch, _rel_world())
    result, _, _ = _ask(state, "gadget", monkeypatch, _answer_with(answer=claim))
    # C5: reason=the refusal assertion is retired; B3 makes relationships code-written from
    # stored edges instead; approved=owner, 2026-11-10; review=2027-05-10
    stored = {edge.id for edge in _edges(snapshot)}
    assert "error" not in result, result
    assert all(entry["id"] in stored for entry in result["relationships"]), result
    assert result["relationships"] == [], f"a relationship was claimed for {claim!r}"


def test_dec6_not_established_needs_no_citation_and_has_its_own_code(tmp_path, monkeypatch):
    _, state = _state(tmp_path, monkeypatch, _rel_world())
    body = {"checked": ["x"],
        "answer": "The catalogue does not establish this.", "citations": [], "outcome": "not_established"}
    result, _, _ = _ask(state, "gadget", monkeypatch, _static(body))
    assert "error" not in result, result
    assert result.get("code") == "NOT_ESTABLISHED_BY_CATALOGUE"
    assert result["citations"] == []


def test_dec6_answered_without_citation_is_still_invalid(tmp_path, monkeypatch):
    _, state = _state(tmp_path, monkeypatch, _rel_world())
    for body in (
        {"checked": ["x"], "answer": "x is y", "citations": [], "outcome": "answered"},
        {"checked": ["x"], "answer": "x is y", "citations": [], "outcome": "answered"},
    ):
        result, _, _ = _ask(state, "gadget", monkeypatch, _static(body))
        assert "error" in result, f"uncited answer accepted: {body}"
        assert result.get("code") != "NOT_ESTABLISHED_BY_CATALOGUE"


def test_dec6_not_established_is_not_a_bypass_for_claims(tmp_path, monkeypatch):
    _, state = _state(tmp_path, monkeypatch, _rel_world())
    body = {"checked": ["x"],
        "answer": "gadget is derived from part and data flows into cog.", "citations": [], "outcome": "not_established"}
    result, _, _ = _ask(state, "gadget", monkeypatch, _static(body))
    assert result.get("code") != "NOT_ESTABLISHED_BY_CATALOGUE", result
    assert "error" in result, "uncited fabricated claim passed as 'not established'"


def test_dec6_not_established_with_unknown_citation_is_invalid(tmp_path, monkeypatch):
    _, state = _state(tmp_path, monkeypatch, _rel_world())
    body = {"checked": ["x"],
        "answer": "not found", "citations": ["id-that-is-not-in-context"], "outcome": "not_established"}
    result, _, _ = _ask(state, "gadget", monkeypatch, _static(body))
    assert result.get("error", {}).get("code") == "MODEL_INVALID_CITATION", result


def test_dec6_not_established_may_cite_what_was_checked(tmp_path, monkeypatch):
    _, state = _state(tmp_path, monkeypatch, _rel_world())

    def reply(payload):
        return json.dumps({"checked": ["x"],
            "answer": "checked, not established", "citations": [payload["items"][0]["id"]], "outcome": "not_established"})

    result, _, _ = _ask(state, "gadget", monkeypatch, reply)
    assert result.get("code") == "NOT_ESTABLISHED_BY_CATALOGUE" and "error" not in result


@pytest.mark.parametrize("outcome", ["maybe", "", None, 5, ["answered"], "NOT_ESTABLISHED", "Not_Established "])
def test_dec6_unknown_outcome_values_never_crash_and_never_pass_uncited(tmp_path, monkeypatch, outcome):
    _, state = _state(tmp_path, monkeypatch, _rel_world())
    body = {"checked": ["x"], "answer": "text", "citations": [], "outcome": outcome}
    result, _, _ = _ask(state, "gadget", monkeypatch, _static(body))
    assert "error" in result or result.get("code") == "NOT_ESTABLISHED_BY_CATALOGUE"
    # an uncited reply with a bad outcome must not be a normal answered reply
    assert not (result.get("citations") == [] and "error" not in result and result.get("code") != "NOT_ESTABLISHED_BY_CATALOGUE")


def test_dec6_no_match_guard_still_skips_the_model(tmp_path, monkeypatch):
    _, state = _state(tmp_path, monkeypatch, _rel_world())
    result, requests, _ = _ask(state, "zzqq unrelated vocabulary", monkeypatch)
    assert result.get("error", {}).get("code") == "NO_CATALOGUE_MATCH"
    assert requests == []


def test_dec6_schema_sent_has_no_bounds(tmp_path, monkeypatch):
    """Regression of B1 through the new `outcome` field."""
    _, state = _state(tmp_path, monkeypatch, _rel_world())
    _, _, schemas = _ask(state, "gadget", monkeypatch, stub_kwargs={"reject_bounds": True})
    assert schemas and all(schema_bounds(s) == [] for s in schemas), schemas


def test_dec6_not_established_over_rest(tmp_path, monkeypatch):
    from dbig_requester.api import create_app
    from dbig_requester.config import RequesterConfig
    from fastapi.testclient import TestClient

    snapshot, state = _state(tmp_path, monkeypatch, _rel_world())
    monkeypatch.setenv("REQUESTER_TOKEN", "synthetic-api-token")
    monkeypatch.setenv("MODEL_KEY", MODEL_KEY)
    body = {"checked": ["x"],
        "answer": "The catalogue does not establish this.", "citations": [],
        "outcome": "not_established"}
    with ModelStub(_static(body)) as stub:
        config = RequesterConfig(
            snapshot_dir=snapshot, state_dir=state, api_key_env="REQUESTER_TOKEN",
            answer=_cfg(stub),
        )
        with TestClient(create_app(config)) as client:
            response = client.post(
                "/v1/catalogue/questions", json={"question": "gadget"},
                headers={"Authorization": "Bearer synthetic-api-token"},
            )
    assert response.status_code == 200, response.text
    assert response.json().get("code") == "NOT_ESTABLISHED_BY_CATALOGUE"


def test_dec6_no_prompt_or_response_in_logs(tmp_path, monkeypatch, caplog):
    _, state = _state(tmp_path, monkeypatch, _rel_world())
    caplog.set_level(logging.INFO)  # an operator who turns on DEBUG gets the library's own log
    secret_answer = "claim-canary-" + CANARY
    _ask(state, f"gadget {CANARY}", monkeypatch, _answer_with(answer=f"{secret_answer} derived from part"))
    _ask(state, "gadget", monkeypatch, _static({"checked": ["x"],
        "answer": secret_answer, "citations": [], "outcome": "not_established"}))
    text = caplog.text
    assert CANARY not in text


def test_dec6_error_does_not_echo_response(tmp_path, monkeypatch):
    _, state = _state(tmp_path, monkeypatch, _rel_world())
    result, _, _ = _ask(state, "gadget", monkeypatch, _answer_with(answer=f"{CANARY} derived from part"))
    assert "error" in result
    assert CANARY not in json.dumps(result)


# =============================== DEC-7: source / engine words, object retrieval ================


def _three_sources(n_tables=4):
    def tables(prefix):
        return {
            f"{prefix}{i}": {"fields": [("id", "integer"), ("title", "text")], "key": ["id"]}
            for i in range(n_tables)
        }

    return {
        "pgsrc": {"kind": "postgresql", "tables": tables("pgtab")},
        "chsrc": {"kind": "clickhouse", "tables": tables("chtab")},
        "mgsrc": {"kind": "mongodb", "tables": tables("mgcol")},
    }


@pytest.mark.parametrize("query", ["clickhouse", "ClickHouse", "CLICKHOUSE", "chsrc", "CHSRC"])
def test_dec7_engine_and_source_words_are_searchable(tmp_path, monkeypatch, query):
    _, state = _state(tmp_path, monkeypatch, _three_sources())
    found = search(state, SearchRequest(query=query, limit=50))["results"]
    assert found, f"{query!r} finds nothing"
    assert {r["source_id"] for r in found} == {"chsrc"}


def test_dec7_postgres_alias_word(tmp_path, monkeypatch):
    _, state = _state(tmp_path, monkeypatch, _three_sources())
    found = search(state, SearchRequest(query="postgresql", limit=50))["results"]
    assert {r["source_id"] for r in found} == {"pgsrc"}


def test_dec7_list_question_retrieves_objects_of_the_named_source(tmp_path, monkeypatch):
    _, state = _state(tmp_path, monkeypatch, _three_sources())
    for question in ("List the ClickHouse tables", "which tables are in chsrc", "clickhouse tables"):
        result, requests, _ = _ask(state, question, monkeypatch)
        assert "error" not in result, (question, result)
        items = _payload_of(requests)["items"]
        assert items, question
        assert {i["source_id"] for i in items} == {"chsrc"}, (question, [i["source_id"] for i in items])
        # P6-9 (F13, DEC-18) flip: retrieval still returns objects only; each retrieved table's
        # columns then join the context, so only the non-field items are the retrieved ones.
        objects = [i for i in items if i["kind"] != "field"]
        assert {i["kind"] for i in objects} == {"table"}, "list question must retrieve objects, not columns"
        assert {i["name"] for i in objects} == {f"chtab{n}" for n in range(4)}
        assert {i["object_name"] for i in items if i["kind"] == "field"} <= {i["name"] for i in objects}


@pytest.mark.parametrize(
    ("question", "source", "names"),
    [
        ("What does the ClickHouse database contain? List its tables.", "chsrc", "chtab"),
        ("Which tables does the postgresql source hold?", "pgsrc", "pgtab"),
        ("show me the mongodb collections", "mgsrc", "mgcol"),
        ("What tables exist in clickhouse", "chsrc", "chtab"),
        ("Give me the names of the clickhouse tables", "chsrc", "chtab"),
        ("How many tables are in clickhouse", "chsrc", "chtab"),
    ],
)
def test_dec7_list_question_with_ordinary_extra_words_still_lists_the_source(tmp_path, monkeypatch, question, source, names):
    """The report's C3 is the first of these, verbatim. The Decision: for list questions retrieve
    by object (all objects of that source). Words that match nothing must not cancel that."""
    _, state = _state(tmp_path, monkeypatch, _three_sources())
    result, requests, _ = _ask(state, question, monkeypatch)
    assert result.get("error", {}).get("code") != "NO_CATALOGUE_MATCH", result
    items = _payload_of(requests)["items"]
    assert {i["source_id"] for i in items} == {source}
    # P6-9 flip: each listed object's fields follow it in the context; the objects are unchanged.
    assert {i["name"] for i in items if i["kind"] != "field"} == {f"{names}{n}" for n in range(4)}


def test_dec7_wrong_source_is_not_returned_even_when_other_names_match(tmp_path, monkeypatch):
    world = _three_sources()
    # a clickhouse table whose name is a strong match for the question word
    world["chsrc"]["tables"]["tables_registry"] = {"fields": [("id", "integer"), ("tables", "text")], "key": ["id"]}
    _, state = _state(tmp_path, monkeypatch, world)
    _, requests, _ = _ask(state, "Which tables are in postgresql", monkeypatch)
    items = _payload_of(requests)["items"]
    assert items and {i["source_id"] for i in items} == {"pgsrc"}


def test_dec7_two_sources_named_gives_both(tmp_path, monkeypatch):
    _, state = _state(tmp_path, monkeypatch, _three_sources())
    _, requests, _ = _ask(state, "tables in pgsrc and chsrc", monkeypatch)
    sources = {i["source_id"] for i in _payload_of(requests)["items"]}
    assert sources == {"pgsrc", "chsrc"}


def test_dec7_nonexistent_source_named_does_not_crash_or_pick_another(tmp_path, monkeypatch):
    _, state = _state(tmp_path, monkeypatch, _three_sources())
    result, requests, _ = _ask(state, "list the oracle tables", monkeypatch)
    # either no match, or an answer; but never a crash, and never a silent substitute that
    # claims oracle: if the model was called, the question's other words were what matched
    assert isinstance(result, dict)
    if requests:
        assert not any(i["kind"] == "oracle" for i in _payload_of(requests)["items"])


def test_dec7_plural_tables_word_alone_without_source_does_not_dump_everything_unbounded(tmp_path, monkeypatch):
    _, state = _state(tmp_path, monkeypatch, _three_sources(n_tables=15))
    result, requests, _ = _ask(state, "list all tables", monkeypatch)
    if requests:
        assert len(_payload_of(requests)["items"]) <= 10 + 0, "retrieval_limit must bound the context"


# Owner ruling 2026-11-10, "Retire as stale (Recommended)": list mode (H5) replaces the cap.
@pytest.mark.skip(reason="stale: H5 list mode retrieves up to list_limit")  # C5: reason=H5 list mode replaces the retrieval_limit cap for list questions; approved=owner, 2026-11-10; review=2027-05-10
def test_dec7_retrieval_limit_bounds_object_listing(tmp_path, monkeypatch):
    _, state = _state(tmp_path, monkeypatch, _three_sources(n_tables=15))
    _, requests, _ = _ask(state, "list the clickhouse tables", monkeypatch, retrieval_limit=5)
    # P6-9 flip: `retrieval_limit` bounds the retrieved objects; their columns are added uncapped.
    assert len([i for i in _payload_of(requests)["items"] if i["kind"] != "field"]) <= 5


def test_dec7_object_hit_outranks_generic_column_hits(tmp_path, monkeypatch):
    tables = {
        "shipment": {"fields": [("id", "integer"), ("carrier", "text")], "key": ["id"]},
    }
    for i in range(12):  # many columns that merely mention the word, with a heavy description
        tables[f"misc{i}"] = {
            "fields": [("id", "integer"), (f"shipment_note_{i}", "text")],
            "descriptions": {f"shipment_note_{i}": "shipment shipment shipment shipment shipment"},
            "key": ["id"],
        }
    _, state = _state(tmp_path, monkeypatch, {"alpha": {"tables": tables}})
    result, requests, _ = _ask(state, "what is in the shipment table", monkeypatch)
    items = _payload_of(requests)["items"]
    assert items[0]["name"] == "shipment" and items[0]["kind"] == "table", [(i["name"], i["kind"]) for i in items[:4]]


def test_dec7_exact_name_rank_unchanged_for_plain_search(tmp_path, monkeypatch):
    _, state = _state(tmp_path, monkeypatch, _three_sources())
    names = [r["name"] for r in search(state, SearchRequest(query="chtab1"))["results"]]
    assert names[0] == "chtab1"


@pytest.mark.parametrize("question", ["ClickHouse tables", "ｃｌｉｃｋｈｏｕｓｅ tables", "клик tables ClickHouse \U0001F600", "tables " * 200 + "clickhouse"])
def test_dec7_unicode_and_long_questions_do_not_crash(tmp_path, monkeypatch, question):
    _, state = _state(tmp_path, monkeypatch, _three_sources())
    result, _, _ = _ask(state, question, monkeypatch)
    assert isinstance(result, dict)


@pytest.mark.parametrize("query", ['"', "'", "chsrc OR", "chsrc*", "(", "NEAR(", "a:b", "-chsrc", "\x00chsrc"])
def test_dec7_fts_metacharacters_in_question_do_not_raise(tmp_path, monkeypatch, query):
    _, state = _state(tmp_path, monkeypatch, _three_sources())
    search(state, SearchRequest(query=query))
    result, _, _ = _ask(state, f"list tables {query}", monkeypatch)
    assert isinstance(result, dict)


def test_dec7_source_word_that_is_also_a_plain_word_does_not_hide_other_sources(tmp_path, monkeypatch):
    """A source called `billing`; a different source has a table `billing_run`. The question names
    `billing_run`, not the source."""
    world = {
        "billing": {"kind": "postgresql", "tables": {"invoice": {"fields": [("id", "integer")], "key": ["id"]}}},
        "ops": {"kind": "postgresql", "tables": {"billing_run": {"fields": [("id", "integer")], "key": ["id"]}}},
    }
    _, state = _state(tmp_path, monkeypatch, world)
    _, requests, _ = _ask(state, "what is billing_run", monkeypatch)
    names = {i["name"] for i in _payload_of(requests)["items"]}
    assert "billing_run" in names


def test_dec7_stale_index_is_refused_not_misread(tmp_path, monkeypatch):
    snapshot, state = _state(tmp_path, monkeypatch, _three_sources())
    databases = list(state.rglob("*.sqlite*")) + list(state.rglob("*.db"))
    assert databases, list(state.rglob("*"))
    con = sqlite3.connect(databases[0])
    tables = [r[0] for r in con.execute("select name from sqlite_master where type='table'")]
    con.close()
    meta = [t for t in tables if "meta" in t.lower() or "state" in t.lower() or "info" in t.lower()]
    assert meta or tables  # observation only
    # remove the version marker: whichever table/column holds it
    con = sqlite3.connect(databases[0])
    done = False
    for t in tables:
        cols = [r[1] for r in con.execute(f"pragma table_info('{t}')")]
        if "index_version" in cols:
            con.execute(f"update '{t}' set index_version='1'")  # noqa: S608 # C5: reason=SQL text for a test fixture built from constant names; approved=user, 2026-11-08; review=2027-05-08
            done = True
        elif "key" in cols and "value" in cols and con.execute(f"select 1 from '{t}' where key='index_version'").fetchone():  # noqa: S608 # C5: reason=SQL text for a test fixture built from constant names; approved=user, 2026-11-08; review=2027-05-08
            con.execute(f"update '{t}' set value='1' where key='index_version'")  # noqa: S608 # C5: reason=SQL text for a test fixture built from constant names; approved=user, 2026-11-08; review=2027-05-08
            done = True
    con.commit()
    con.close()
    if not done:
        pytest.skip("could not locate the version marker; reported as unverified")  # C5: reason=could not locate the version marker, reported as unverified; approved=user, 2026-11-08; review=2027-05-08
    with pytest.raises(ValueError, match="STALE_INDEX"):
        search(state, SearchRequest(query="chsrc"))
    result, _, _ = _ask(state, "clickhouse tables", monkeypatch)
    assert result.get("error", {}).get("code") == "STALE_INDEX", result
    assert "Traceback" not in json.dumps(result)


# =============================== DEC-8: per-source exclude_objects =============================


def _noisy_world():
    return {
        "mgsrc": {
            "kind": "mongodb",
            "tables": {
                "real_account": {"fields": [("_id", "text"), ("website", "text")]},
                "docs__schemas": {"fields": [("path", "text"), ("website_path", "text")]},
                "docs_source_profiles": {"fields": [("path", "text"), ("website_hint", "text")]},
            },
        },
        "pgsrc": {
            "kind": "postgresql",
            "tables": {
                "docs__schemas": {"fields": [("id", "integer"), ("website_x", "text")], "key": ["id"]},
                "account": {"fields": [("id", "integer")], "key": ["id"]},
            },
        },
    }


def _names_in(state, query, **kw):
    return {(r["source_id"], r["name"], r.get("object_name")) for r in search(state, SearchRequest(query=query, limit=100, **kw))["results"]}


def test_dec8_default_none_keeps_everything(tmp_path, monkeypatch):
    _, state = _state(tmp_path, monkeypatch, _noisy_world())
    assert any(n[1] == "website_path" for n in _names_in(state, "website_path"))
    assert any(n[1] == "docs__schemas" for n in _names_in(state, "docs__schemas"))
    _, state2 = _state(tmp_path, monkeypatch, _noisy_world(), name="e", exclude={})
    assert any(n[1] == "website_path" for n in _names_in(state2, "website_path"))


def test_dec8_exclude_is_per_source_and_removes_fields_too(tmp_path, monkeypatch):
    _, state = _state(tmp_path, monkeypatch, _noisy_world(), exclude={"mgsrc": ["*__schemas", "*_source_profiles"]})
    hits = _names_in(state, "docs")
    assert not [h for h in hits if h[0] == "mgsrc" and "docs" in h[1]]
    assert not [h for h in _names_in(state, "website_path")]
    assert not [h for h in _names_in(state, "website_hint")]
    # same name in ANOTHER source stays
    assert ("pgsrc", "docs__schemas", None) in {(a, b, None) for a, b, _ in _names_in(state, "docs__schemas")}
    assert any(h[1] == "website_x" for h in _names_in(state, "website_x"))
    # a non-matching object of the same source stays
    assert any(h[1] == "real_account" for h in _names_in(state, "real_account"))
    assert any(h[1] == "website" and h[0] == "mgsrc" for h in _names_in(state, "website"))


def test_dec8_exclude_all_via_star_and_unknown_source_and_bad_globs(tmp_path, monkeypatch):
    _, state = _state(tmp_path, monkeypatch, _noisy_world(), exclude={"mgsrc": ["*"]})
    assert not [h for h in _names_in(state, "real_account")]
    assert [h for h in _names_in(state, "account") if h[0] == "pgsrc"]
    for number, glob in enumerate(("[abc", "[", "]", "**", "", "?", "docs__schemas\n")):
        _, st = _state(tmp_path, monkeypatch, _noisy_world(), name=f"g{number}", exclude={"nosuchsrc": [glob], "mgsrc": [glob]})
        search(st, SearchRequest(query="account"))


def test_dec8_empty_list_excludes_nothing(tmp_path, monkeypatch):
    _, state = _state(tmp_path, monkeypatch, _noisy_world(), exclude={"mgsrc": []})
    assert _names_in(state, "docs__schemas")


def test_dec8_glob_is_case_sensitive_or_documented_and_exact_name_works(tmp_path, monkeypatch):
    _, state = _state(tmp_path, monkeypatch, _noisy_world(), exclude={"mgsrc": ["docs__schemas"]})
    assert not [h for h in _names_in(state, "docs__schemas") if h[0] == "mgsrc"]
    assert [h for h in _names_in(state, "docs_source_profiles") if h[0] == "mgsrc"]


def test_dec8_reindex_without_exclusion_restores(tmp_path, monkeypatch):
    snapshot, state = _state(tmp_path, monkeypatch, _noisy_world(), exclude={"mgsrc": ["*__schemas"]})
    assert not [h for h in _names_in(state, "docs__schemas") if h[0] == "mgsrc"]
    index_snapshot(snapshot, state)
    assert [h for h in _names_in(state, "docs__schemas") if h[0] == "mgsrc"]
    index_snapshot(snapshot, state, {"mgsrc": ["*__schemas"]})
    assert not [h for h in _names_in(state, "docs__schemas") if h[0] == "mgsrc"]


def test_dec8_excluded_objects_do_not_leak_through_relationships_or_citations(tmp_path, monkeypatch):
    world = {
        "alpha": {
            "kind": "postgresql",
            "tables": {
                "gadget": {"fields": [("id", "integer"), ("title", "text")], "key": ["id"]},
                "gadget__schemas": {"fields": [("id", "integer"), ("gadget_id", "integer")], "key": ["id"]},
            },
        }
    }
    snapshot, state = _state(tmp_path, monkeypatch, world, exclude={"alpha": ["*__schemas"]})
    nodes = _nodes(snapshot)
    noise_edges = [e for e in _edges(snapshot) if "gadget__schemas" in {nodes[e.from_id].name, nodes[e.to_id].name}]
    assert noise_edges, "fixture must plant a relationship to the excluded object"
    result, requests, _ = _ask(state, "gadget gadget_id schemas", monkeypatch)
    sent = json.dumps(requests)
    assert "gadget__schemas" not in sent
    for edge in noise_edges:
        assert edge.id not in sent
    noise_ids = [n.id for n in nodes.values() if n.name == "gadget__schemas" or (getattr(n, "object_id", None) and nodes.get(n.object_id) and nodes[n.object_id].name == "gadget__schemas")]
    bad, _, _ = _ask(state, "gadget", monkeypatch, _static({"checked": ["x"],
        "answer": "x", "citations": [noise_ids[0]],
        "outcome": "answered"}))
    assert bad.get("error", {}).get("code") == "MODEL_INVALID_CITATION"


def test_dec8_exclude_via_config_file_and_cli(tmp_path, monkeypatch):
    from dbig_requester.config import load_config

    snapshot = build_snapshot(tmp_path, monkeypatch, _noisy_world(), name="c")
    cfg = tmp_path / "r.toml"
    cfg.write_text(
        f'[requester]\nsnapshot_dir = "{snapshot}"\nstate_dir = "{tmp_path / "st"}"\n'
        'api_key_env = "REQUESTER_TOKEN"\n'
        '[requester.exclude_objects]\nmgsrc = ["*__schemas"]\n',
        encoding="utf-8",
    )
    config = load_config(cfg)
    assert config.exclude_objects == {"mgsrc": ["*__schemas"]}
    from click.testing import CliRunner
    from dbig_requester.cli import main

    out = CliRunner().invoke(main, ["index", "--config", str(cfg)])
    assert out.exit_code == 0, out.output
    assert not [h for h in _names_in(tmp_path / "st", "docs__schemas") if h[0] == "mgsrc"]


def test_dec8_inline_table_form_from_the_docs_loads(tmp_path):
    from dbig_requester.config import load_config

    cfg = tmp_path / "r.toml"
    cfg.write_text(
        '[requester]\nsnapshot_dir = "s"\nstate_dir = "t"\napi_key_env = "K"\n'
        'exclude_objects = { mgsrc = ["*__schemas"] }\n',
        encoding="utf-8",
    )
    assert load_config(cfg).exclude_objects == {"mgsrc": ["*__schemas"]}


# =============================== DEC-9: cited ids resolved app-side ============================


def test_dec9_cited_names_come_from_the_catalogue_not_the_model(tmp_path, monkeypatch):
    snapshot, state = _state(tmp_path, monkeypatch, _rel_world())

    def reply(payload):
        item = next(i for i in payload["items"] if i["kind"] == "field")
        return json.dumps({"checked": ["x"],
            "answer": "it is liar_table.liar_column in liar_source", "citations": [item["id"]],
            "outcome": "answered"})

    result, _, _ = _ask(state, "gadget title", monkeypatch, reply)
    assert "error" not in result, result
    cited = result["cited"]
    assert len(cited) == 1
    blob = json.dumps(cited)
    assert "liar" not in blob
    node = _nodes(snapshot)[result["citations"][0]]
    assert node.name in blob
    assert "alpha" in blob  # the source
    owner = _nodes(snapshot)[node.object_id].name
    assert owner in blob


def test_dec9_cited_matches_citations_order_and_count(tmp_path, monkeypatch):
    snapshot, state = _state(tmp_path, monkeypatch, _rel_world())

    def reply(payload):
        ids = [i["id"] for i in payload["items"][:3]][::-1]
        return json.dumps({"checked": ["x"],
            "answer": "several", "citations": ids, "outcome": "answered"})

    result, _, _ = _ask(state, "gadget part widget", monkeypatch, reply)
    assert len(result["cited"]) == len(result["citations"]) == 3
    nodes = _nodes(snapshot)
    for entry, cid in zip(result["cited"], result["citations"], strict=True):
        assert nodes[cid].name in json.dumps(entry)


def test_dec9_duplicate_citations_do_not_break(tmp_path, monkeypatch):
    _, state = _state(tmp_path, monkeypatch, _rel_world())

    def reply(payload):
        i = payload["items"][0]["id"]
        return json.dumps({"checked": ["x"],
            "answer": "dup", "citations": [i, i, i], "outcome": "answered"})

    result, _, _ = _ask(state, "gadget", monkeypatch, reply)
    assert "error" in result or len(result["cited"]) in (1, 3)


def test_dec9_relationship_is_resolved_as_from_to(tmp_path, monkeypatch):
    snapshot, state = _state(tmp_path, monkeypatch, _rel_world())
    candidate = _edge(snapshot, "REFERENCE_CANDIDATE", "part", "gadget")
    result, _, _ = _ask(state, "part gadget", monkeypatch, _static({"checked": ["x"],
        "answer": "linked", "citations": [candidate.id],
        "outcome": "answered"}))
    assert "error" not in result, result
    blob = json.dumps(result["cited"]).lower()
    nodes = _nodes(snapshot)
    assert nodes[candidate.from_id].name.lower() in blob and nodes[candidate.to_id].name.lower() in blob
    assert "gadget_id" in blob


def test_dec9_not_established_has_empty_cited_and_errors_carry_no_names(tmp_path, monkeypatch):
    _, state = _state(tmp_path, monkeypatch, _rel_world())
    result, _, _ = _ask(state, "gadget", monkeypatch, _static({"checked": ["x"],
        "answer": "none", "citations": [], "outcome": "not_established"}))
    assert result.get("cited", []) == []
    bad, _, _ = _ask(state, "gadget", monkeypatch, _static({"checked": ["x"],
        "answer": "x", "citations": ["nope"],
        "outcome": "answered"}))
    assert bad["error"]["code"] == "MODEL_INVALID_CITATION"
    assert "gadget" not in json.dumps(bad["error"])


def test_dec9_names_with_unicode_and_odd_characters(tmp_path, monkeypatch):
    world = {"alpha": {"tables": {"café_表": {"fields": [("id", "integer"), ("na\"me'\n", "text")], "key": ["id"]}}}}
    _, state = _state(tmp_path, monkeypatch, world)
    result, _, _ = _ask(state, "café_表", monkeypatch)
    assert "error" not in result, result
    assert json.loads(json.dumps(result, ensure_ascii=False))


def test_dec9_cited_carries_no_sample_values(tmp_path, monkeypatch):
    world = _rel_world()
    world["alpha"]["tables"]["gadget"]["values"] = {"title": [CANARY + str(i) for i in range(6)]}
    _, state = _state(tmp_path, monkeypatch, world)
    result, _, _ = _ask(state, "gadget title", monkeypatch)
    assert CANARY not in json.dumps(result)


# =============================== extra attacks found while reading the results ================


def test_dec5_edge_between_the_two_asked_objects_is_not_crowded_out_by_a_hub(tmp_path, monkeypatch):
    """25 declared links into `hub` rank before the one candidate between `hub` and `leaf`. A
    question that names both must still see the edge that joins them (default limit)."""
    tables = {"hub": {"fields": [("id", "integer")], "key": ["id"]},
              "leaf": {"fields": [("id", "integer"), ("hub_id", "integer")], "key": ["id"]}}
    for n in range(25):
        tables[f"spoke{n:02d}"] = {
            "fields": [("id", "integer"), ("ref", "integer")], "key": ["id"],
            "foreign": [("ref", "hub", "id")],
        }
    snapshot, state = _state(tmp_path, monkeypatch, {"alpha": {"tables": tables}})
    wanted = _edge(snapshot, "REFERENCE_CANDIDATE", "leaf", "hub")
    _, requests, _ = _ask(state, "how does leaf relate to hub", monkeypatch)
    assert wanted.id in json.dumps(_payload_of(requests)), "the edge joining the two asked tables was cut by the limit"


def test_dec5_relationship_count_never_exceeds_default_limit(tmp_path, monkeypatch):
    tables = {"hub": {"fields": [("id", "integer")], "key": ["id"]}}
    for n in range(40):
        tables[f"spoke{n:02d}"] = {
            "fields": [("id", "integer"), ("ref", "integer")], "key": ["id"],
            "foreign": [("ref", "hub", "id")],
        }
    _, state = _state(tmp_path, monkeypatch, {"alpha": {"tables": tables}})
    _, requests, _ = _ask(state, "hub", monkeypatch)
    assert len(_payload_of(requests)["relationships"]) == 20


def test_dec7_source_id_equal_to_an_ordinary_word_probe(tmp_path, monkeypatch):
    """A source whose ID is a common word silently narrows every question containing that word."""
    world = {
        "sales": {"kind": "postgresql", "tables": {"orders": {"fields": [("id", "integer")], "key": ["id"]}}},
        "ops": {"kind": "postgresql", "tables": {"sales_report": {"fields": [("id", "integer")], "key": ["id"]}}},
    }
    _, state = _state(tmp_path, monkeypatch, world)
    _, requests, _ = _ask(state, "what is in the sales_report table", monkeypatch)
    assert "sales_report" in {i["name"] for i in _payload_of(requests)["items"]}
    _, requests, _ = _ask(state, "tell me about the sales report", monkeypatch)
    names = {i["name"] for i in _payload_of(requests)["items"]}
    assert "sales_report" in names, names  # the word `sales` is also a source ID
