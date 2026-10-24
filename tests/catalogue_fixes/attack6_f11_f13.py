"""Attack on P6-7 (F11) and P6-9 (F13, DEC-18), commit 5b5fae2, and the H4 canaries for both (P6-10).

Names are invented and generic. Run: `uv run pytest -q tests/catalogue_fixes/attack6_f11_f13.py`
(the file name does not match `test_*.py`, so it is named on the command line).
"""

# ruff: noqa: E402, E501, F811, S106
from __future__ import annotations

import json
import os
import sqlite3
import sys
import types
from contextlib import closing
from pathlib import Path

import catalogue_fixture_kit as kit
import pytest
from catalogue_fixture_kit import MODEL_KEY, ModelStub, build_snapshot
from click.testing import CliRunner
from dbig_contracts.artifact import read_artifact, write_artifact
from dbig_contracts.models import Edge, Object
from dbig_requester import search as _search_module
from dbig_requester.answer import answer_question
from dbig_requester.api import create_app
from dbig_requester.cli import main as requester_main
from dbig_requester.config import AnswerConfig, RequesterConfig
from dbig_requester.search import (
    INDEX_VERSION,
    SearchRequest,
    StaleIndexError,
    index_snapshot,
    search,
)
from fastapi.testclient import TestClient

fields_of = getattr(_search_module, "fields_of", None)  # absent before 5b5fae2: those tests fail

# The H4 world (collector + builder + index of three sources) and its capture helpers are reused,
# so the canaries are the ones P6-10 names.
from test_h4_privacy_canaries import (  # noqa: F401  (world is a fixture)
    GADGET_QUESTION,
    HEX64,
    PROMPT,
    SQL_LITERAL,
    Capture,
    EchoingServer,
    ask_config,
    assert_clean,
    complete_messages,
    leaks,
    system_message,
    world,
)
from test_h4_privacy_canaries import QUESTION as H4_QUESTION

ROOT = Path(__file__).parents[2]
SENTENCE = (
    "A relationship between objects of two different sources cannot be joined in one query; "
    "say so instead of writing one."
)
ITEM_KEYS = {"id", "name", "kind", "source_id", "data_type", "object_name", "namespace", "description"}


def _config(url: str, **overrides) -> AnswerConfig:
    return AnswerConfig(
        base_url=url, model="m", model_digest="d", api_key_env="MODEL_KEY",
        timeout_seconds=5, **overrides,
    )


def _answered(*ids: str) -> str:
    return json.dumps({"answer": "ok", "citations": list(ids), "outcome": "answered"})


def _ask(state: Path, monkeypatch, question: str, reply=None, **overrides):
    monkeypatch.setenv("MODEL_KEY", MODEL_KEY)
    reply = reply or (lambda p: _answered(p["items"][0]["id"]))
    with ModelStub(reply) as stub:
        result = answer_question(state, question, _config(stub.url, **overrides))
    payload = (
        json.loads(stub.requests[-1]["body"]["messages"][-1]["content"]) if stub.requests else None
    )
    return result, payload, stub


def _index(tmp_path, monkeypatch, sources, exclude=None) -> Path:
    snapshot = build_snapshot(tmp_path, monkeypatch, sources)
    index_snapshot(snapshot, tmp_path / "state", exclude)
    return tmp_path / "state"


def _db(state: Path) -> Path:
    return next(p for p in state.iterdir() if p.suffix.startswith(".sqlite"))


def _bodies(state: Path) -> list[dict]:
    with closing(sqlite3.connect(_db(state))) as c:
        return [json.loads(r[0]) for r in c.execute("SELECT body FROM relationships ORDER BY id")]


TWO = {
    "alpha": {"kind": "postgresql", "tables": {
        "vendor": {"fields": [("vendor_id", "integer")], "key": ["vendor_id"],
                   "values": {"vendor_id": [11, 12, 13]},
                   "descriptions": {"vendor_id": "ALPHA-DESC-CANARY"}},
        "shipment": {"fields": [("shipment_id", "integer"), ("vendor_id", "integer")],
                     "values": {"vendor_id": [11, 12, 13]}},
    }},
    "beta": {"kind": "clickhouse", "tables": {
        "vendor": {"fields": [("vendor_id", "integer"), ("beta_only", "text")],
                   "weak_key": "vendor_id", "values": {"vendor_id": [11, 12, 13]}},
    }},
}


# =============================== P6-7 ==========================================================


def test_index_version_is_7():
    assert INDEX_VERSION == "8"  # P7-1 (F14) bumped it


def _rewrite(snapshot: Path, dest: Path, extra_edges) -> Path:
    """Re-write a snapshot with extra hand-made edges (possibly dangling)."""
    manifest, data = read_artifact(snapshot)
    tables = {name: list(rows) for name, rows in data.items()}
    tables["edges.jsonl"] = [*tables["edges.jsonl"], *extra_edges]
    write_artifact(
        dest, "catalog-snapshot", tables, producer="attack",
        source_run_ids=list(manifest.source_run_ids),
    )
    return dest


def _edge(template: Edge, **changes) -> Edge:
    return Edge.model_validate(template.model_dump(mode="json") | changes)


def _hand_edge(tmp_path, monkeypatch, name: str, pick, kind="REFERENCE_CANDIDATE"):
    """Index a snapshot with one hand-made edge whose ends `pick(nodes)` chooses."""
    snapshot = build_snapshot(tmp_path, monkeypatch, TWO)
    _, data = read_artifact(snapshot)
    from_id, to_id = pick(data["nodes.jsonl"])
    edge = _edge(
        data["edges.jsonl"][0], id=f"edge:{name}", from_id=from_id, to_id=to_id, kind=kind,
        field_pairs=[],
    )
    rewritten = _rewrite(snapshot, tmp_path / f"snap-{name}", [edge])
    index_snapshot(rewritten, tmp_path / "st")
    return next(b for b in _bodies(tmp_path / "st") if b["id"] == f"edge:{name}")


def _first(nodes, record_type, **match):
    return next(
        n for n in nodes
        if n.record_type == record_type and all(getattr(n, k) == v for k, v in match.items())
    )


def test_a_dangling_edge_end_cannot_reach_the_indexer_the_artifact_reader_refuses_it(
    tmp_path, monkeypatch
):
    """So `places.get(..., (None, None))` is reachable only through a source-node end, below."""
    with pytest.raises(ValueError, match="dangling edge"):
        _hand_edge(
            tmp_path, monkeypatch, "ghost",
            lambda nodes: (_first(nodes, "object", source_id="alpha").id, "object:" + "0" * 32),
        )


def test_an_edge_to_a_source_node_has_no_places(tmp_path, monkeypatch):
    body = _hand_edge(
        tmp_path, monkeypatch, "tosrc",
        lambda nodes: (_first(nodes, "object", source_id="alpha").id, _first(nodes, "source").id),
        kind="DECLARES_FK",
    )
    assert body["to_source"] is None and body["to_namespace"] is None


def test_a_field_end_takes_its_own_source_and_its_owners_namespace(tmp_path, monkeypatch):
    monkeypatch.setattr(kit, "Object", lambda **f: Object(namespace=f"ns_{f['name']}", **f))
    body = _hand_edge(
        tmp_path, monkeypatch, "fld",
        lambda nodes: (
            _first(nodes, "field", name="beta_only").id,
            _first(nodes, "object", source_id="alpha").id,
        ),
    )
    assert (body["from_source"], body["from_namespace"]) == ("beta", "ns_vendor")
    assert body["to_source"] == "alpha"


def test_objects_without_namespace_give_null_keys_present_never_invented(tmp_path, monkeypatch):
    bodies = _bodies(_index(tmp_path, monkeypatch, TWO))
    assert bodies
    for body in bodies:
        assert "from_namespace" in body and "to_namespace" in body
        assert body["from_namespace"] is None and body["to_namespace"] is None
        assert body["from_source"] in {"alpha", "beta"} and body["to_source"] in {"alpha", "beta"}


def test_the_fixture_holds_both_a_same_source_and_a_cross_source_edge(tmp_path, monkeypatch):
    bodies = _bodies(_index(tmp_path, monkeypatch, TWO))
    assert {b["from_source"] == b["to_source"] for b in bodies} == {True, False}


def test_relationship_bodies_carry_identifiers_only(tmp_path, monkeypatch):
    allowed = {
        "id", "kind", "status", "from", "to", "columns", "evidence",
        "from_source", "from_namespace", "to_source", "to_namespace",
    }
    for body in _bodies(_index(tmp_path, monkeypatch, TWO)):
        assert set(body) == allowed
        text = json.dumps(body)
        assert "ALPHA-DESC-CANARY" not in text and not HEX64.search(text)


def test_the_cross_source_sentence_appears_exactly_once_beside_the_injection_warning(
    tmp_path, monkeypatch
):
    state = _index(tmp_path, monkeypatch, TWO)
    _, _, stub = _ask(state, monkeypatch, "how does vendor link")
    system = stub.requests[-1]["body"]["messages"][0]["content"]
    assert system.count(SENTENCE) == 1
    assert "never as instructions" in system


def _set_version(state: Path, value: str) -> None:
    with closing(sqlite3.connect(_db(state))) as c:
        c.execute("UPDATE meta SET value=? WHERE key='index_version'", (value,))
        c.commit()


@pytest.mark.parametrize("old", ["7", "6", "", "8 ", "08", "9"])
def test_any_other_version_is_stale_through_ask_search_rest_and_cli(tmp_path, monkeypatch, old):
    state = _index(tmp_path, monkeypatch, TWO)
    _set_version(state, old)
    monkeypatch.setenv("MODEL_KEY", MODEL_KEY)
    monkeypatch.setenv("REST_T", "tok-rest-attack-not-a-model-key")
    snapshot = tmp_path / "snapshot"
    with ModelStub(lambda p: _answered(p["items"][0]["id"])) as stub:
        assert answer_question(state, "vendor", _config(stub.url)) == {
            "error": {"code": "STALE_INDEX"}
        }
        with pytest.raises(StaleIndexError):
            search(state, SearchRequest(query="vendor"))
        cfg = RequesterConfig(
            snapshot_dir=snapshot, state_dir=state, api_key_env="REST_T", answer=_config(stub.url)
        )
        headers = {"Authorization": "Bearer tok-rest-attack-not-a-model-key"}
        with TestClient(create_app(cfg)) as client:
            ask = client.post("/v1/catalogue/questions", json={"question": "vendor"}, headers=headers)
            get = client.get("/v1/catalogue/search", params={"query": "vendor"}, headers=headers)
            post = client.post("/v1/catalogue/search", json={"query": "vendor"}, headers=headers)
        toml = tmp_path / "r.toml"
        toml.write_text(
            "[requester]\n"
            f'snapshot_dir = "{snapshot.as_posix()}"\nstate_dir = "{state.as_posix()}"\n'
            'api_key_env = "MODEL_KEY"\n'
            f'[requester.answer]\nbase_url = "{stub.url}"\nmodel = "m"\nmodel_digest = "d"\n'
            'api_key_env = "MODEL_KEY"\n',
            encoding="utf-8",
        )
        runner = CliRunner()
        cli_ask = runner.invoke(requester_main, ["ask", "--config", str(toml), "--question", "vendor"])
        cli_search = runner.invoke(requester_main, ["search", "--config", str(toml), "--query", "vendor"])
        assert stub.requests == [], "a stale index must not reach the model"
    assert ask.json() == {"detail": {"code": "STALE_INDEX"}}
    assert get.status_code == 409 and get.json() == {"detail": {"code": "STALE_INDEX"}}
    assert post.json() == {"detail": {"code": "STALE_INDEX"}}, post.status_code
    assert json.loads(cli_ask.stdout) == {"error": {"code": "STALE_INDEX"}}, cli_ask.output
    assert "STALE_INDEX" in cli_search.output, (cli_search.exit_code, cli_search.output)
    assert "Traceback" not in cli_search.output


def test_rebuilding_the_index_clears_the_stale_state(tmp_path, monkeypatch):
    state = _index(tmp_path, monkeypatch, TWO)
    _set_version(state, "6")
    index_snapshot(tmp_path / "snapshot", state)
    result, payload, _ = _ask(state, monkeypatch, "vendor")
    assert "error" not in result and payload["items"]


# =============================== P6-9 ==========================================================

WIDE = {
    "alpha": {"kind": "postgresql", "tables": {
        "gadget": {"fields": [("gadget_label", "text"), *[(f"attr_{n:03d}", "integer") for n in range(60)]]},
        "sprocket": {"fields": [("sprocket_id", "integer"), ("gadget_ref", "integer")]},
    }},
    "beta": {"kind": "clickhouse", "tables": {
        "gadget": {"fields": [("gadget_label", "text"), ("beta_col", "text")]},
        "widget": {"fields": [("widget_id", "integer")]},
    }},
}


def test_fields_of_a_source_a_relationship_a_field_the_empty_and_hostile_is_nothing(
    tmp_path, monkeypatch
):
    state = _index(tmp_path, monkeypatch, TWO)
    with closing(sqlite3.connect(_db(state))) as c:
        field_id = c.execute("SELECT id FROM entries WHERE kind='field' LIMIT 1").fetchone()[0]
        edge_id = c.execute("SELECT id FROM relationships LIMIT 1").fetchone()[0]
    for owners in (set(), {"alpha"}, {edge_id}, {field_id}, {"x') OR 1=1 --"}, {"%"}, {""}):
        assert fields_of(state, owners) == [], owners


@pytest.mark.xfail(strict=True, reason="fields_of returns tags/capabilities/evidence_status as "
                   "JSON text, unlike search(); never reaches the model (projection drops them)")
def test_fields_of_rows_have_exactly_the_shape_search_returns(tmp_path, monkeypatch):
    state = _index(tmp_path, monkeypatch, WIDE)
    direct = search(state, SearchRequest(query="gadget_label", limit=50, source_id="alpha"))["results"]
    column = next(r for r in direct if r["name"] == "gadget_label")
    [expanded] = [r for r in fields_of(state, {column["object_id"]}) if r["id"] == column["id"]]
    assert expanded == column


def test_expanded_keys_equal_retrieved_keys_and_the_context_projection_is_the_same(
    tmp_path, monkeypatch
):
    state = _index(tmp_path, monkeypatch, WIDE)
    direct = search(state, SearchRequest(query="gadget_label", limit=50, source_id="alpha"))["results"]
    column = next(r for r in direct if r["name"] == "gadget_label")
    [expanded] = [r for r in fields_of(state, {column["object_id"]}) if r["id"] == column["id"]]
    keep = lambda row: {k: row[k] for k in ITEM_KEYS if k in row}  # noqa: E731
    assert keep(expanded) == keep(column)
    assert set(expanded) == set(column)
    _, payload, _ = _ask(state, monkeypatch, "sprocket", retrieval_limit=1)
    for item in payload["items"]:
        assert set(item) <= ITEM_KEYS


@pytest.mark.parametrize(
    "question",
    ["gadget", "gadget sprocket", "gadget_label", "Which tables are in ClickHouse?",
     "attr_003 gadget"],
)
@pytest.mark.parametrize("limit", [1, 2, 3, 50])
def test_no_duplicates_and_retrieved_items_stay_first_in_search_order(
    tmp_path, monkeypatch, question, limit
):
    state = _index(tmp_path, monkeypatch, WIDE)
    result, payload, _ = _ask(state, monkeypatch, question, retrieval_limit=limit)
    assert payload is not None, result
    ids = [i["id"] for i in payload["items"]]
    assert len(ids) == len(set(ids)), "duplicate item in the model context"
    if "ClickHouse" not in question:
        plain = search(state, SearchRequest(query=question, limit=limit, match="any"))["results"]
        assert ids[: len(plain)] == [r["id"] for r in plain]


def test_a_scoped_question_expands_only_inside_the_named_source(tmp_path, monkeypatch):
    state = _index(tmp_path, monkeypatch, WIDE)
    for question, source in (
        ("ClickHouse gadget", "beta"),
        ("postgresql gadget", "alpha"),
        ("Which tables are in ClickHouse?", "beta"),
    ):
        result, payload, _ = _ask(state, monkeypatch, question)
        assert payload, (question, result)
        assert {i["source_id"] for i in payload["items"]} == {source}, question
        names = {i["name"] for i in payload["items"]}
        if source == "beta":
            assert not names & {"attr_000", "sprocket"}
        else:
            assert not names & {"beta_col", "widget"}


def test_same_named_tables_in_two_sources_each_expand_only_their_own_columns(tmp_path, monkeypatch):
    state = _index(tmp_path, monkeypatch, WIDE)
    _, payload, _ = _ask(state, monkeypatch, "gadget", retrieval_limit=2)
    columns: dict[tuple[str, str], set[str]] = {}
    for item in payload["items"]:
        if item["kind"] == "field":
            columns.setdefault((item["source_id"], item["object_name"]), set()).add(item["name"])
    assert ("alpha", "gadget") in columns and ("beta", "gadget") in columns
    assert "beta_col" not in columns[("alpha", "gadget")]
    assert "attr_000" not in columns[("beta", "gadget")]


def test_cited_resolves_an_expanded_column_with_object_namespace_and_source(tmp_path, monkeypatch):
    monkeypatch.setattr(kit, "Object", lambda **f: Object(namespace=f"ns_{f['source_id']}", **f))
    state = _index(tmp_path, monkeypatch, WIDE)

    def reply(payload):
        return _answered(next(i for i in payload["items"] if i["name"] == "attr_042")["id"])

    result, payload, _ = _ask(state, monkeypatch, "gadget", reply=reply, retrieval_limit=2)
    assert "error" not in result, result
    [cited] = result["cited"]
    assert cited["name"] == "attr_042" and cited["kind"] == "field"
    assert cited["object_name"] == "gadget" and cited["source_id"] == "alpha"
    assert cited["namespace"] == "ns_alpha"
    assert set(cited) <= {"id", "kind", "name", "object_name", "namespace", "source_id"}


def test_a_column_of_an_unretrieved_table_is_still_refused(tmp_path, monkeypatch):
    state = _index(tmp_path, monkeypatch, WIDE)
    with closing(sqlite3.connect(_db(state))) as c:
        other = c.execute("SELECT id FROM entries WHERE name='widget_id'").fetchone()[0]
    result, _, _ = _ask(
        state, monkeypatch, "sprocket", reply=lambda p: _answered(other), retrieval_limit=1
    )
    assert result["error"]["code"] == "MODEL_INVALID_CITATION"


EXCLUDE_SOURCES = {
    "alpha": {"kind": "postgresql", "tables": {
        "vault": {"fields": [("vault_key", "text"), ("shared_col", "text")]},
        "ledger": {"fields": [("ledger_id", "integer"), ("shared_col", "text")]},
    }},
}


def test_exclusion_survives_expansion(tmp_path, monkeypatch):
    state = _index(tmp_path, monkeypatch, EXCLUDE_SOURCES, exclude={"alpha": ["vault"]})
    with closing(sqlite3.connect(_db(state))) as c:
        names = {r[0] for r in c.execute("SELECT name FROM entries")}
    assert "vault" not in names and "vault_key" not in names, "control: the index excluded it"
    nodes = read_artifact(tmp_path / "snapshot")[1]["nodes.jsonl"]
    vault = _first(nodes, "object", name="vault")
    assert fields_of(state, {vault.id}) == []
    for question in ("vault", "vault_key", "shared_col", "ledger"):
        _, payload, _ = _ask(state, monkeypatch, question, retrieval_limit=5)
        if payload is None:
            continue
        assert "vault_key" not in {i["name"] for i in payload["items"]}, question
        assert "vault" not in {i.get("object_name") for i in payload["items"]}, question
        assert "vault" not in json.dumps(payload["relationships"]), question
    forged = _first(nodes, "field", name="vault_key").id
    result, _, _ = _ask(state, monkeypatch, "ledger", reply=lambda p: _answered(forged))
    assert result["error"]["code"] == "MODEL_INVALID_CITATION"


def test_zero_hits_is_no_catalogue_match_with_no_model_call(tmp_path, monkeypatch):
    state = _index(tmp_path, monkeypatch, WIDE)
    for question in ("zzzzqqq", "the of and", "varchar integer", "attr_999"):
        result, _, stub = _ask(state, monkeypatch, question)
        assert result["error"] == {"code": "NO_CATALOGUE_MATCH"} or result["error"][
            "code"
        ] == "NO_CATALOGUE_MATCH", (question, result)
        assert stub.requests == []
        assert result["snapshot_digest"]


def test_a_very_wide_table_is_one_model_call_with_every_column(tmp_path, monkeypatch):
    wide = {"alpha": {"tables": {"big": {"fields": [(f"c{n:04d}", "text") for n in range(1500)]}}}}
    state = _index(tmp_path, monkeypatch, wide)
    _, payload, stub = _ask(state, monkeypatch, "big", retrieval_limit=1)
    assert len(stub.requests) == 1 and len(payload["items"]) == 1501


def test_many_retrieved_owners_do_not_hit_the_sqlite_variable_limit(tmp_path, monkeypatch):
    many = {"alpha": {"tables": {f"tbl{n:04d}": {"fields": [(f"f{n}", "text")]} for n in range(1200)}}}
    state = _index(tmp_path, monkeypatch, many)
    result, payload, _ = _ask(state, monkeypatch, "tbl", retrieval_limit=100)
    assert "error" not in result, result
    assert len(payload["items"]) == 200


def test_the_trace_metadata_is_a_count_a_digest_and_a_version_only(tmp_path, monkeypatch):
    state = _index(tmp_path, monkeypatch, TWO)
    seen: list[dict] = []

    class Span:
        def end(self):
            pass

    class Client:
        def __init__(self, **_):
            pass

        def start_observation(self, **kwargs):
            seen.append(kwargs)
            return Span()

    module = types.ModuleType("langfuse")
    module.Langfuse = Client
    monkeypatch.setitem(sys.modules, "langfuse", module)
    monkeypatch.setenv("LF_PUB", "x" * 40)
    monkeypatch.setenv("LF_SEC", "y" * 40)
    result, _, _ = _ask(
        state, monkeypatch, "vendor", langfuse_url="http://127.0.0.1:1",
        langfuse_public_key_env="LF_PUB", langfuse_secret_key_env="LF_SEC",
    )
    assert "error" not in result, result
    [call] = seen
    assert set(call["metadata"]) == {"retrieval_count", "model_digest", "schema_version"}
    assert isinstance(call["metadata"]["retrieval_count"], int)
    assert not leaks(json.dumps(call))


# =============================== P6-10 canaries ================================================
# The H4 world holds a PostgreSQL source with sampled-value canaries, a SQL literal, keys, and two
# more sources. Every surface below is checked with the same planted-string table.


def _recorded_ask(world, question, **overrides):
    seen: dict = {}

    def reply(payload):
        seen["payload"] = payload
        return _answered(payload["items"][0]["id"], *[e["id"] for e in payload["relationships"][:1]])

    os.environ["MODEL_KEY"] = MODEL_KEY
    with ModelStub(reply) as stub, Capture() as capture:
        result = answer_question(world.state, question, ask_config(stub.url, **overrides))
    return result, stub, capture, seen.get("payload")


def _outside_the_question(payload, stub) -> str:
    return json.dumps({**payload, "question": ""}) + json.dumps(
        [m for m in stub.requests[0]["body"]["messages"] if m["role"] == "system"]
    )


def test_relationship_source_fields_carry_no_value_digest_literal_or_secret(world):
    result, stub, capture, payload = _recorded_ask(world, H4_QUESTION)
    assert "error" not in result, result
    assert payload["relationships"], "control"
    places = set()
    for edge in payload["relationships"]:
        for key in ("from_source", "from_namespace", "to_source", "to_namespace"):
            value = edge[key]
            assert value is None or isinstance(value, str)
            places.add(value)
            assert not leaks(str(value)) and not HEX64.search(str(value))
    assert {"alpha", "beta", "coll"} & places, "control: sources really present"
    outside = _outside_the_question(payload, stub)
    assert_clean("the relationship context", outside, allow=("description text",))
    assert not HEX64.search(outside)
    capture.assert_clean("ask with relationship sources", extra=complete_messages(stub.requests))


def test_expanded_columns_carry_no_value_digest_literal_or_secret(world):
    result, stub, capture, payload = _recorded_ask(world, GADGET_QUESTION, retrieval_limit=1)
    assert "error" not in result, result
    assert len(payload["items"]) > 1 and any(i["kind"] == "field" for i in payload["items"]), (
        "control: columns were expanded beyond limit 1"
    )
    outside = _outside_the_question(payload, stub)
    assert_clean("expanded columns", outside, allow=("description text",))
    assert not HEX64.search(outside)
    for item in payload["items"]:
        assert set(item) <= ITEM_KEYS
    assert payload["question"] == GADGET_QUESTION
    for needle in (PROMPT, SQL_LITERAL):
        assert needle not in json.dumps({**payload, "question": ""})
    capture.assert_clean("ask with expansion", extra=complete_messages(stub.requests))


def test_every_table_of_the_world_expands_without_a_digest_or_secret(world):
    asked = 0
    for question in (
        "vendor", "shipment", "gadget part", "Which tables are in ClickHouse?",
        "Which tables are in PostgreSQL?",
    ):
        result, stub, capture, payload = _recorded_ask(world, question, retrieval_limit=2)
        if payload is None:
            continue
        asked += 1
        blob = json.dumps({**payload, "question": ""}) + json.dumps(
            {k: v for k, v in result.items() if k != "snapshot_digest"}  # public manifest hash
        )
        assert_clean(question, blob, allow=("description text",))
        assert not HEX64.search(blob), question
        assert not leaks(system_message(stub.requests[0]))
        capture.assert_clean(question, extra=complete_messages(stub.requests))
    assert asked >= 3, "control: most questions reached the model"


def test_failures_after_expansion_are_codes_and_echo_no_column_or_value(world):
    os.environ["MODEL_KEY"] = MODEL_KEY
    for status in (400, 500):
        with EchoingServer(status) as server, Capture() as capture:
            result = answer_question(
                world.state, GADGET_QUESTION, ask_config(server.url, retrieval_limit=1)
            )
        assert set(result) <= {"error", "snapshot_digest"}
        # P7-8 (F18) adds a structural `reason`.
        assert set(result["error"]) <= {"code", "reason"} and result["error"]["code"].isupper()
        text = json.dumps(result)
        assert_clean("the failure result", text)
        assert "part" not in text and "gadget" not in text
        capture.assert_clean(f"failure {status}")


def test_no_catalogue_match_in_the_world_makes_no_model_call(world):
    os.environ["MODEL_KEY"] = MODEL_KEY
    with ModelStub(lambda p: _answered("x")) as stub, Capture() as capture:
        result = answer_question(world.state, "qqqq zzzz unmatched", ask_config(stub.url))
    assert result["error"] == {"code": "NO_CATALOGUE_MATCH"} and stub.requests == []
    capture.assert_clean("no match")


def test_fields_of_has_no_cap_in_its_query_and_the_new_code_prints_nothing():
    base = ROOT / "packages/requester/src/dbig_requester"
    body = (base / "search.py").read_text(encoding="utf-8").split("def fields_of", 1)[1]
    body = body.split("\n\n\n", 1)[0]
    assert "LIMIT" not in body.upper().replace("COLLATE", "")
    for name in ("search.py", "answer.py"):
        assert "print(" not in (base / name).read_text(encoding="utf-8")
