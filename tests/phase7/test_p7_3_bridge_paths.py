"""P7-3 (F16): complete the path between two retrieved tables through bridge tables.

Source: docs/plans/PHASE7_LOCAL_PLAN.md section 4 "P7-3" and section 2, and
artifacts/run-20261020-pgch/research-f14-f17.md section F16. Written from those texts only; the
implementation was not read.

Behaviour specified (not implemented when this file was written). After retrieval, `answer_question`
runs a stdlib breadth-first search over the requester's `relationships` index table between the
retrieved tables (objects):

* `[requester.answer] path_max_hops` (default 4) bounds the number of edges on a path and
  `path_max_bridge_tables` (default 4) bounds how many tables are added in all.
* A table added only because it lies on such a path appears in the model context `items` with
  `role: "path_only"`, carrying only its key columns (the columns named in the relationship lines
  of the path); its relationship lines appear in `relationships`.
* A path that rests on a candidate edge keeps that edge's `status`: nothing upgrades it.
* The requester does not import the planner.

Fixtures use invented generic names (plan section 2.4). Column names are chosen so that no
question word hits a bridge table and no builder name rule invents a shortcut edge between two
ends; `test_fixture_premises` asserts that, so a failure there is a fixture fault, not a verdict
on the item. Nothing here prints a value.

Tests 1-4 and 6 fail on code before the item; 5 and 7 are controls that pass before and after. The
"absent at 2 hops" cases also fail before the item, but only because `AnswerConfig` forbids the
unknown field; the control for "nothing added" is `test_no_path_adds_nothing`.
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "catalogue_fixes"))

import pytest
from catalogue_fixture_kit import MODEL_KEY, ModelStub, build_snapshot
from dbig_requester.answer import answer_question
from dbig_requester.config import AnswerConfig
from dbig_requester.search import index_snapshot

ROOT = Path(__file__).resolve().parents[2]
QUESTION = "How are gizmo and sprocket connected?"
ONLY_GIZMO = "What does gizmo hold?"
NOTE_CANARY = "quokka-canary-note-description"


def _table(key, extra=(), *, foreign=(), notes=False):
    fields = [(key, "integer"), *extra]
    spec = {"fields": fields, "key": [key], "foreign": list(foreign)}
    if notes:
        spec["fields"].append(("note", "text"))
        spec["descriptions"] = {"note": NOTE_CANARY}
    return spec


def _catalogue(tables):
    return {"alpha": {"kind": "postgresql", "tables": tables}}


# A -> bridge -> B: the bridge is a many-to-many link table with two declared foreign keys.
TWO_HOP = _catalogue({
    "gizmo": _table("gid"),
    "sprocket": _table("sid"),
    "linkrow": _table("lid", [("lref", "integer"), ("rref", "integer")], notes=True,
                      foreign=[("lref", "gizmo", "gid"), ("rref", "sprocket", "sid")]),
    # a neighbour of an end that leads nowhere: never on a path
    "deadend": _table("did", [("dref", "integer")], foreign=[("dref", "gizmo", "gid")]),
})

# A -> X -> Y -> B: three edges, two bridge tables.
THREE_HOP = _catalogue({
    "gizmo": _table("gid"),
    "hopone": _table("oid", [("oref", "integer")], notes=True,
                     foreign=[("oref", "gizmo", "gid")]),
    "hoptwo": _table("tid", [("tref", "integer")], foreign=[("tref", "hopone", "oid")]),
    "sprocket": _table("sid", [("sref", "integer")], foreign=[("sref", "hoptwo", "tid")]),
})

# Two independent 2-hop paths between the same ends: two bridge tables are available.
TWO_BRIDGES = _catalogue({
    "gizmo": _table("gid"),
    "sprocket": _table("sid"),
    "linkone": _table("aid", [("aleft", "integer"), ("aright", "integer")],
                      foreign=[("aleft", "gizmo", "gid"), ("aright", "sprocket", "sid")]),
    "linktwo": _table("bid", [("bleft", "integer"), ("bright", "integer")],
                      foreign=[("bleft", "gizmo", "gid"), ("bright", "sprocket", "sid")]),
})

# gizmo -- (declared) -- sprocket's link: one edge is a name-based candidate with no declaration.
# `midrow.gid` repeats the name of gizmo's key, so the builder proposes a tentative edge to it.
TENTATIVE = _catalogue({
    "gizmo": _table("gid"),
    "midrow": _table("mid", [("gid", "integer")]),
    "sprocket": _table("sid", [("sref", "integer")], foreign=[("sref", "midrow", "mid")]),
})

# Two ends with no connection at all, plus an island joined to one of them.
NO_PATH = _catalogue({
    "gizmo": _table("gid"),
    "sprocket": _table("sid"),
    "satellite": _table("kid", [("kref", "integer")], foreign=[("kref", "gizmo", "gid")]),
})

PATH_TABLES = {"linkrow", "hopone", "hoptwo", "linkone", "linktwo", "midrow"}


def _state(tmp_path, monkeypatch, sources):
    snapshot = build_snapshot(tmp_path, monkeypatch, sources)
    index_snapshot(snapshot, tmp_path / "state")
    return tmp_path / "state"


def _reply(payload: dict) -> str:
    first = payload["items"][0]
    return json.dumps({
        "checked": ["the retrieved tables and their relationship lines"],  # required by P7-4
        "answer": f"{first['name']} is catalogued and linked as listed.",
        "citations": [first["id"]],
        "outcome": "answered",
    })


def _ask(state, monkeypatch, question=QUESTION, **overrides):
    """(result, parsed first user message: question, items, relationships; raw request body)."""
    monkeypatch.setenv("MODEL_KEY", MODEL_KEY)
    with ModelStub(_reply) as stub:
        config = AnswerConfig(base_url=stub.url, model="m", model_digest="d",
                              api_key_env="MODEL_KEY", timeout_seconds=5, **overrides)
        result = answer_question(state, question, config)
    assert stub.requests, f"the model was never called: {result}"
    body = stub.requests[0]["body"]
    user = next(m for m in body["messages"] if m["role"] == "user")
    return result, json.loads(user["content"]), json.dumps(body)


def _path_only(payload: dict) -> list[dict]:
    return [item for item in payload["items"] if item.get("role") == "path_only"]


def _table_names(payload: dict) -> set[str]:
    return {item["name"] for item in _path_only(payload) if item.get("kind") != "field"}


def _edges(payload: dict) -> dict[frozenset, dict]:
    return {frozenset({e["from"], e["to"]}): e for e in payload["relationships"]}


# --- fixture premises ---------------------------------------------------------------------------


@pytest.mark.parametrize("sources", [TWO_HOP, THREE_HOP, TWO_BRIDGES, TENTATIVE, NO_PATH])
def test_fixture_premises(tmp_path, monkeypatch, sources):
    """Retrieval finds the two ends only, and no relationship joins the ends directly."""
    state = _state(tmp_path, monkeypatch, sources)
    # Lead: the premise is about retrieval, so the path search is off here (0 bridge tables).
    _, payload, _ = _ask(state, monkeypatch, path_max_bridge_tables=0)
    ends = {item["name"] for item in payload["items"] if item.get("kind") != "field"}
    assert {"gizmo", "sprocket"} <= ends, ends
    assert not ends & PATH_TABLES, f"the question reached a bridge table: {ends & PATH_TABLES}"
    assert frozenset({"gizmo", "sprocket"}) not in _edges(payload)
    # every relationship the index holds, to prove a shortcut is not merely cut by a limit
    with closing(sqlite3.connect(state / "catalog.sqlite3")) as db:
        direct = [r for r in db.execute("SELECT body FROM relationships")
                  if {json.loads(r[0])["from"], json.loads(r[0])["to"]} == {"gizmo", "sprocket"}]
    assert direct == []


# --- 1. A -> bridge -> B ------------------------------------------------------------------------


def test_the_bridge_between_two_retrieved_tables_is_path_only_with_key_columns(
    tmp_path, monkeypatch
):
    state = _state(tmp_path, monkeypatch, TWO_HOP)
    result, payload, raw = _ask(state, monkeypatch)
    assert "error" not in result, result
    assert _table_names(payload) == {"linkrow"}, (
        "the bridge between the two retrieved tables must be added with role path_only; "
        f"got {sorted(_table_names(payload))}"
    )
    edges = _edges(payload)
    assert frozenset({"linkrow", "gizmo"}) in edges and frozenset({"linkrow", "sprocket"}) in edges
    # only the key columns: the ones the two relationship lines name
    key_columns = {
        column for edge in edges.values() if "linkrow" in (edge["from"], edge["to"])
        for pair in edge["columns"] for column in pair
    } & {"lid", "lref", "rref", "note"}
    assert key_columns == {"lref", "rref"}
    bridge_fields = {
        item["name"] for item in payload["items"]
        if item.get("kind") == "field" and item.get("object_name") == "linkrow"
    }
    assert bridge_fields == {"lref", "rref"}, bridge_fields
    assert NOTE_CANARY not in raw, "a non-key column's description reached the model"
    assert '"note"' not in raw, "a non-key column of the bridge reached the model"


def test_the_ends_and_a_dead_end_neighbour_are_not_path_only(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch, TWO_HOP)
    _, payload, _ = _ask(state, monkeypatch)
    assert "linkrow" in _table_names(payload)  # the feature is on, else this proves nothing
    assert _table_names(payload).isdisjoint({"gizmo", "sprocket", "deadend"})
    assert not any(
        item.get("role") == "path_only" for item in payload["items"]
        if item["name"] in {"gizmo", "sprocket"}
    )
    # the neighbour that leads nowhere may keep its relationship line (an edge touching an end,
    # as today) but is not added as a table
    assert "deadend" not in {item["name"] for item in payload["items"]}


def test_path_only_items_have_ids_and_the_added_lines_are_not_duplicated(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch, TWO_HOP)
    _, payload, _ = _ask(state, monkeypatch)
    assert _path_only(payload)
    ids = [item["id"] for item in payload["items"]]
    assert len(ids) == len(set(ids))
    lines = [e["id"] for e in payload["relationships"]]
    assert len(lines) == len(set(lines))


# --- 2. hop limit -------------------------------------------------------------------------------


@pytest.mark.parametrize("hops", [3, 4])
def test_a_three_hop_path_appears_when_the_limit_allows_it(tmp_path, monkeypatch, hops):
    state = _state(tmp_path, monkeypatch, THREE_HOP)
    _, payload, raw = _ask(state, monkeypatch, path_max_hops=hops)
    assert _table_names(payload) == {"hopone", "hoptwo"}
    # the middle line joins two tables neither of which is an end: only the search finds it
    assert frozenset({"hopone", "hoptwo"}) in _edges(payload)
    assert '"note"' not in raw and NOTE_CANARY not in raw


def test_the_default_limit_reaches_three_hops(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch, THREE_HOP)
    _, payload, _ = _ask(state, monkeypatch)
    assert _table_names(payload) == {"hopone", "hoptwo"}


def test_a_three_hop_path_is_absent_at_two_hops(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch, THREE_HOP)
    _, payload, _ = _ask(state, monkeypatch, path_max_hops=2)
    assert _path_only(payload) == []
    assert frozenset({"hopone", "hoptwo"}) not in _edges(payload)


def test_a_two_hop_path_still_appears_at_two_hops(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch, TWO_HOP)
    _, payload, _ = _ask(state, monkeypatch, path_max_hops=2)
    assert _table_names(payload) == {"linkrow"}


def test_one_hop_allows_no_bridge(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch, TWO_HOP)
    _, payload, _ = _ask(state, monkeypatch, path_max_hops=1)
    assert _path_only(payload) == []


# --- 3. bridge cap ------------------------------------------------------------------------------


@pytest.mark.parametrize(("cap", "expected"), [(1, 1), (2, 2), (4, 2)])
def test_the_bridge_cap_limits_the_added_tables(tmp_path, monkeypatch, cap, expected):
    state = _state(tmp_path, monkeypatch, TWO_BRIDGES)
    _, payload, _ = _ask(state, monkeypatch, path_max_bridge_tables=cap)
    names = _table_names(payload)
    assert len(names) == expected, sorted(names)
    assert names <= {"linkone", "linktwo"}
    # a table that is kept brings its relationship lines; one that is dropped brings none
    for kept in names:
        assert {frozenset({kept, "gizmo"}), frozenset({kept, "sprocket"})} <= set(_edges(payload))


def test_a_path_that_needs_more_bridges_than_the_cap_adds_no_partial_path(tmp_path, monkeypatch):
    """Two bridge tables are needed; with a cap of one the half path is no use to the model."""
    state = _state(tmp_path, monkeypatch, THREE_HOP)
    _, with_room = _ask(state, monkeypatch, path_max_bridge_tables=2)[:2]
    assert _table_names(with_room) == {"hopone", "hoptwo"}, "premise: two bridges fit in two"
    _, capped, _ = _ask(state, monkeypatch, path_max_bridge_tables=1)
    assert len(_table_names(capped)) <= 1


# --- 4. a candidate edge stays a candidate ------------------------------------------------------


def test_a_path_over_a_candidate_edge_keeps_the_candidate_status(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch, TENTATIVE)
    with closing(sqlite3.connect(state / "catalog.sqlite3")) as db:
        premise = {frozenset({b["from"], b["to"]}): b for b in
                   (json.loads(r[0]) for r in db.execute("SELECT body FROM relationships"))}
    assert premise[frozenset({"midrow", "gizmo"})]["status"] == "tentative", (
        "fixture fault: the builder proposed no tentative edge between midrow and gizmo")
    _, payload, _ = _ask(state, monkeypatch)
    assert _table_names(payload) == {"midrow"}, "the path through the candidate edge is missing"
    edges = _edges(payload)
    candidate = edges[frozenset({"midrow", "gizmo"})]
    assert candidate["kind"] == "REFERENCE_CANDIDATE"
    assert candidate["status"] == "tentative", "nothing may upgrade a candidate edge"
    assert edges[frozenset({"midrow", "sprocket"})]["status"] == "declared"
    # no line in the context claims more than the index holds for it
    with closing(sqlite3.connect(state / "catalog.sqlite3")) as db:
        held = {json.loads(r[0])["id"]: json.loads(r[0])["status"]
                for r in db.execute("SELECT body FROM relationships")}
    for line in payload["relationships"]:
        assert line["status"] == held[line["id"]], line["id"]


# --- 5. control: no path, nothing added ---------------------------------------------------------


def test_no_path_adds_nothing(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch, NO_PATH)
    result, payload, _ = _ask(state, monkeypatch)
    assert "error" not in result, result
    assert _path_only(payload) == []
    assert all("role" not in item for item in payload["items"])
    assert "satellite" not in {item["name"] for item in payload["items"]}
    # as today: the lines are the edges touching a retrieved table, no more
    assert {frozenset({e["from"], e["to"]}) for e in payload["relationships"]} == {
        frozenset({"satellite", "gizmo"})
    }


def test_one_retrieved_table_has_no_pair_to_connect(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch, TWO_HOP)
    _, payload, _ = _ask(state, monkeypatch, question=ONLY_GIZMO)
    assert {i["name"] for i in payload["items"] if i.get("kind") != "field"} == {"gizmo"}
    assert _path_only(payload) == []


# --- 6. config ----------------------------------------------------------------------------------


def test_path_limits_default_to_four_and_four():
    fields = AnswerConfig.model_fields
    assert fields["path_max_hops"].default == 4
    assert fields["path_max_bridge_tables"].default == 4
    config = AnswerConfig(base_url="http://127.0.0.1:1/v1", model="m", model_digest="d",
                          api_key_env="MODEL_KEY")
    assert (config.path_max_hops, config.path_max_bridge_tables) == (4, 4)


@pytest.mark.parametrize(("name", "value"), [
    ("path_max_hops", 0), ("path_max_hops", -1),
    ("path_max_bridge_tables", -1),
])
def test_path_limits_are_validated(name, value):
    assert name in AnswerConfig.model_fields, f"{name} is not a config field yet"
    with pytest.raises(ValueError, match=name):
        AnswerConfig(base_url="http://127.0.0.1:1/v1", model="m", model_digest="d",
                     api_key_env="MODEL_KEY", **{name: value})


def test_the_architecture_note_lists_the_two_keys():
    """Documentation: the two keys of `[requester.answer]` are described."""
    text = (ROOT / "TECHNICAL_IMPLEMENTATION.md").read_text(encoding="utf-8")
    assert "path_max_hops" in text and "path_max_bridge_tables" in text, (
        "the architecture note must list the two new keys of [requester.answer]"
    )


# --- 7. package isolation and no hard-coded limits ----------------------------------------------


def test_the_requester_imports_nothing_from_the_planner():
    sources = sorted((ROOT / "packages" / "requester" / "src").rglob("*.py"))
    assert {"answer.py", "search.py"} <= {p.name for p in sources}
    pattern = re.compile(r"^\s*(?:from|import)\s+dbig_planner\b", re.MULTILINE)
    assert [p.name for p in sources if pattern.search(p.read_text(encoding="utf-8"))] == []


def test_the_limits_are_not_hard_coded_outside_the_config():
    """Plan 2.4/2.6: the numbers live in `config.py`; the search reads them from the config."""
    source = ROOT / "packages" / "requester" / "src" / "dbig_requester"
    literal = re.compile(r"path_max_(?:hops|bridge_tables)\s*[:=]\s*(?:Field\()?\s*(?:default=)?\d")
    hits = [p.name for p in sorted(source.glob("*.py"))
            if p.name != "config.py" and literal.search(p.read_text(encoding="utf-8"))]
    assert hits == []
    assert "path_max_hops" in (source / "answer.py").read_text(encoding="utf-8") or any(
        "path_max_hops" in p.read_text(encoding="utf-8") for p in source.glob("*.py")
        if p.name != "config.py"
    ), "no module outside config.py reads path_max_hops"
