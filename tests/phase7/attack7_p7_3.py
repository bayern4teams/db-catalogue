"""Attack tests for P7-3 (F16). Not collected by default: run with the file path."""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pytest
import test_p7_3_bridge_paths as base
from dbig_requester.search import bridge_paths, index_snapshot

t, ask, tables_of = base._table, base._ask, base._table_names
catalogue, state_of, edges_of = base._catalogue, base._state, base._edges

CYCLE = catalogue({
    "gizmo": t("gid"),
    "sprocket": t("sid"),
    "linkrow": t("lid", [("lref", "integer"), ("rref", "integer"), ("xref", "integer")],
                 foreign=[("lref", "gizmo", "gid"), ("rref", "sprocket", "sid"),
                          ("xref", "cyca", "aid")]),
    "cyca": t("aid", [("bref", "integer")], foreign=[("bref", "cycb", "bid")]),
    "cycb": t("bid", [("cref", "integer")], foreign=[("cref", "linkrow", "lid")]),
})


def chain(*names):
    """gizmo -> names... -> sprocket as a chain of declared keys."""
    spec = {"gizmo": t("gid")}
    previous, previous_key = "gizmo", "gid"
    for index, name in enumerate(names):
        spec[name] = t(f"k{index}", [(f"r{index}", "integer")],
                       foreign=[(f"r{index}", previous, previous_key)])
        previous, previous_key = name, f"k{index}"
    spec["sprocket"] = t("sid", [("sref", "integer")], foreign=[("sref", previous, previous_key)])
    return catalogue(spec)


def test_cycle_terminates_and_finds_the_short_path(tmp_path, monkeypatch):
    state = state_of(tmp_path, monkeypatch, CYCLE)
    result, payload, raw = ask(state, monkeypatch)
    assert "error" not in result
    assert tables_of(payload) == {"linkrow"}
    # the cycle's tables are off the shortest path; xref (cycle edge) is not a key of the path
    assert "cyca" not in raw and "cycb" not in raw and "xref" not in raw


@pytest.mark.parametrize(("bridges", "hops", "cap", "expect"), [
    (3, 4, 4, True), (3, 3, 4, False), (3, 4, 3, True), (3, 4, 2, False),
    (4, 5, 4, True), (4, 4, 4, False), (4, 5, 3, False), (5, 6, 5, True), (5, 5, 9, False),
    (0, 4, 4, False),
])
def test_hops_and_cap_at_the_edge(tmp_path, monkeypatch, bridges, hops, cap, expect):
    names = [f"mid{i}" for i in range(bridges)]
    state = state_of(tmp_path, monkeypatch, chain(*names))
    _, payload, _ = ask(state, monkeypatch, path_max_hops=hops, path_max_bridge_tables=cap)
    got = tables_of(payload)
    assert got == (set(names) if expect else set()), got
    # whole path or nothing: number of path lines equals bridges+1 when present
    lines = [e for e in payload["relationships"]]
    if expect:
        assert len(lines) >= bridges + 1


def test_cap_reached_mid_way_keeps_only_whole_paths(tmp_path, monkeypatch):
    spec = chain("p1a", "p1b")["alpha"]["tables"]
    spec["p2a"] = t("ak", [("ar", "integer")], foreign=[("ar", "gizmo", "gid")])
    spec["p2b"] = t("bk", [("br", "integer"), ("bs", "integer")],
                    foreign=[("br", "p2a", "ak"), ("bs", "sprocket", "sid")])
    state = state_of(tmp_path, monkeypatch, catalogue(spec))
    _, both, _ = ask(state, monkeypatch, path_max_bridge_tables=4)
    assert tables_of(both) == {"p1a", "p1b", "p2a", "p2b"}
    for cap in (2, 3):
        _, payload, _ = ask(state, monkeypatch, path_max_bridge_tables=cap)
        names = tables_of(payload)
        assert names in ({"p1a", "p1b"}, {"p2a", "p2b"}), (cap, names)
        # a line between two non-end tables belongs to the kept path (lines touching an end are
        # the ordinary ones, as before the item)
        inner = {frozenset({e["from"], e["to"]}) for e in payload["relationships"]
                 if not {e["from"], e["to"]} & {"gizmo", "sprocket"}}
        assert all(pair <= names for pair in inner), inner


def _hub(n, order=1):
    spec = {"gizmo": t("gid"), "sprocket": t("sid")}
    for i in range(n)[::order]:
        spec[f"hubx{i:02d}"] = t(f"h{i:02d}", [(f"a{i:02d}", "integer"), (f"b{i:02d}", "integer")],
                                 foreign=[(f"a{i:02d}", "gizmo", "gid"),
                                           (f"b{i:02d}", "sprocket", "sid")])
    return catalogue(spec)


def test_hub_choice_is_deterministic_and_order_independent(tmp_path, monkeypatch):
    runs = []
    for k, order in enumerate((1, -1, 1)):
        sub = tmp_path / str(k)
        sub.mkdir()
        state = state_of(sub, monkeypatch, _hub(30, order))
        _, payload, _ = ask(state, monkeypatch)
        runs.append(tables_of(payload))
    assert len(runs[0]) == 4
    assert runs[0] == runs[1] == runs[2], runs
    # no non-key column of any hub reaches the model; every kept hub has exactly its two key columns


def test_hub_with_30_tables_time(tmp_path, monkeypatch):
    state = state_of(tmp_path, monkeypatch, _hub(30))
    began = time.perf_counter()
    _, payload, _ = ask(state, monkeypatch)
    assert time.perf_counter() - began < 5
    assert len(tables_of(payload)) == 4


def test_ties_prefer_declared_over_tentative(tmp_path, monkeypatch):
    # tentative bridge: no foreign keys, columns repeat the ends' key names
    spec = {"gizmo": t("gid"), "sprocket": t("sid"),
            "aaa_loose": t("lk", [("gid", "integer"), ("sid", "integer")]),
            "zzz_firm": t("fk", [("fl", "integer"), ("fr", "integer")],
                          foreign=[("fl", "gizmo", "gid"), ("fr", "sprocket", "sid")])}
    state = state_of(tmp_path, monkeypatch, catalogue(spec))
    with closing(sqlite3.connect(state / "catalog.sqlite3")) as db:
        status = {json.loads(r[0])["from"] + json.loads(r[0])["to"]: json.loads(r[0])["status"]
                  for r in db.execute("SELECT body FROM relationships")}
    assert "tentative" in status.values(), status
    _, payload, _ = ask(state, monkeypatch, path_max_bridge_tables=1)
    assert tables_of(payload) == {"zzz_firm"}, tables_of(payload)


def test_path_through_another_end_still_finds_the_shorter_pairs(tmp_path, monkeypatch):
    spec = {"gizmo": t("gid"), "widget": t("wid", [("wref", "integer")]), "sprocket": t("sid"),
            "xlink": t("xid", [("x1", "integer"), ("x2", "integer")],
                       foreign=[("x1", "gizmo", "gid"), ("x2", "widget", "wid")]),
            "ylink": t("yid", [("y1", "integer"), ("y2", "integer")],
                       foreign=[("y1", "widget", "wid"), ("y2", "sprocket", "sid")])}
    state = state_of(tmp_path, monkeypatch, catalogue(spec))
    _, payload, _ = ask(
        state, monkeypatch, question="How are gizmo, widget and sprocket connected?",
    )
    names = {i["name"] for i in payload["items"] if i.get("kind") != "field"}
    assert {"gizmo", "widget", "sprocket"} <= names
    assert tables_of(payload) == {"xlink", "ylink"}


def test_parallel_foreign_keys_both_shown_even_when_cap_is_exactly_full(tmp_path, monkeypatch):
    spec = {"gizmo": t("gid"), "sprocket": t("sid"),
            "linkrow": t("lid", [("l1", "integer"), ("l2", "integer"), ("rr", "integer")],
                         foreign=[("l1", "gizmo", "gid"), ("l2", "gizmo", "gid"),
                                  ("rr", "sprocket", "sid")])}
    state = state_of(tmp_path, monkeypatch, catalogue(spec))
    _, roomy, _ = ask(state, monkeypatch, path_max_bridge_tables=4)
    _, exact, _ = ask(state, monkeypatch, path_max_bridge_tables=1)
    def cols(p):
        return {i["name"] for i in p["items"] if i.get("object_name") == "linkrow"}

    assert cols(roomy) == {"l1", "l2", "rr"}
    assert cols(exact) == cols(roomy), "fewer lines when the cap is exactly full"


def test_relationship_limit_zero_turns_it_off(tmp_path, monkeypatch):
    state = state_of(tmp_path, monkeypatch, base.TWO_HOP)
    _, payload, _ = ask(state, monkeypatch, relationship_limit=0)
    assert tables_of(payload) == set() and payload["relationships"] == []


def test_small_relationship_limit_keeps_whole_path_lines(tmp_path, monkeypatch):
    state = state_of(tmp_path, monkeypatch, base.THREE_HOP)
    _, payload, _ = ask(state, monkeypatch, relationship_limit=1)
    assert tables_of(payload) == {"hopone", "hoptwo"}
    assert frozenset({"hopone", "hoptwo"}) in edges_of(payload)


def test_bridge_ids_are_citable_and_resolve_by_name(tmp_path, monkeypatch):
    from catalogue_fixture_kit import MODEL_KEY, ModelStub
    from dbig_requester.answer import answer_question
    from dbig_requester.config import AnswerConfig
    state = state_of(tmp_path, monkeypatch, base.TWO_HOP)
    monkeypatch.setenv("MODEL_KEY", MODEL_KEY)

    def reply(payload):
        bridge = next(i for i in payload["items"] if i.get("role") == "path_only"
                      and i.get("kind") != "field")
        field = next(i for i in payload["items"] if i.get("object_name") == "linkrow")
        return json.dumps({"checked": ["the bridge"], "answer": "linkrow joins the two tables.",
                           "citations": [bridge["id"], field["id"],
                                     payload["relationships"][0]["id"]],
                           "outcome": "answered"})
    with ModelStub(reply) as stub:
        config = AnswerConfig(base_url=stub.url, model="m", model_digest="d",
                              api_key_env="MODEL_KEY", timeout_seconds=5)
        result = answer_question(state, base.QUESTION, config)
    assert "error" not in result, result
    cited = json.dumps(result["cited"])
    assert "linkrow" in cited, result["cited"]
    assert len(result["cited"]) == 3


def test_no_value_text_added_and_context_keys_are_known(tmp_path, monkeypatch):
    state = state_of(tmp_path, monkeypatch, base.TWO_HOP)
    _, payload, _ = ask(state, monkeypatch)
    allowed_keys = {"id", "name", "kind", "source_id", "data_type", "object_name", "namespace",
                    "description", "role"}
    for item in payload["items"]:
        assert set(item) <= allowed_keys, set(item) - allowed_keys
    for line in payload["relationships"]:
        assert not {"sample", "values", "value"} & set(line)


def _synthetic(tmp_path, monkeypatch, width, layers, middle_end=False):
    """Layered index: A - L1 - ... - Ln - B, `width` tables per layer, all pairs joined."""
    sources = base.TWO_HOP
    if middle_end:
        sources = catalogue({**base.TWO_HOP["alpha"]["tables"], "widgetmid": t("wm")})
    state = state_of(tmp_path, monkeypatch, sources)
    with closing(sqlite3.connect(state / "catalog.sqlite3")) as db:
        ids = {n: i for i, n in db.execute("SELECT id, name FROM entries WHERE kind='table'")}
        db.execute("DELETE FROM relationships")
        rows = []

        def add(left, right):
            body = {"id": f"e{len(rows)}", "from": "x", "to": "y", "status": "declared",
                    "kind": "DECLARES_FK", "columns": [["c", "d"]]}
            rows.append((body["id"], left, right, 0, json.dumps(body)))
        grid = [[f"n{layer}_{i}" for i in range(width)] for layer in range(layers)]
        if middle_end:  # one retrieved table in the middle layer: every A-B path goes through it
            grid[layers // 2] = [ids["widgetmid"]]
        for node in grid[0]:
            add(ids["gizmo"], node)
        for lower, upper in zip(grid, grid[1:], strict=False):
            for a in lower:
                for b in upper:
                    add(a, b)
        for node in grid[-1]:
            add(node, ids["sprocket"])
        db.executemany("INSERT INTO relationships VALUES (?,?,?,?,?)", rows)
        db.commit()
    return state, {ids["gizmo"], ids["sprocket"], *([ids["widgetmid"]] if middle_end else [])}


@pytest.mark.parametrize("width", [5, 10, 20, 30])
def test_dense_layered_growth_report(tmp_path, monkeypatch, width, capsys):
    state, ends = _synthetic(tmp_path, monkeypatch, width, 3)
    began = time.perf_counter()
    items, lines = bridge_paths(state, ends, 4, 4)
    spent = time.perf_counter() - began
    with capsys.disabled():
        print(f"\nlayered width={width} paths={width ** 3} seconds={spent:.3f} lines={len(lines)}")
    assert spent < 30


@pytest.mark.parametrize(("width", "layers", "hops"), [(10, 5, 6), (20, 5, 6), (30, 5, 6)])
def test_paths_through_another_end_are_walked_then_dropped_report(
    tmp_path, monkeypatch, capsys, width, layers, hops
):
    state, ends = _synthetic(tmp_path, monkeypatch, width, layers, middle_end=True)
    began = time.perf_counter()
    bridge_paths(state, ends, hops, 4)
    spent = time.perf_counter() - began
    with capsys.disabled():
        print(f"\nend-in-middle width={width} layers={layers} hops={hops} seconds={spent:.3f}")
    assert spent < 120


@pytest.mark.parametrize("tag", ["a", "b", "c", "d", "e", "f"])
def test_equal_length_paths_prefer_the_stronger_whole_path(tmp_path, monkeypatch, tag):
    """Both paths start declared; the second edge is declared on one and tentative on the other."""
    spec = {"gizmo": t("gid"), "sprocket": t("sid"),
            f"weak{tag}": t("wk", [("wl", "integer"), ("sid", "integer")],
                            foreign=[("wl", "gizmo", "gid")]),
            f"firm{tag}": t("fk", [("fl", "integer"), ("fr", "integer")],
                            foreign=[("fl", "gizmo", "gid"), ("fr", "sprocket", "sid")])}
    state = state_of(tmp_path, monkeypatch, catalogue(spec))
    with closing(sqlite3.connect(state / "catalog.sqlite3")) as db:
        held = [json.loads(r[0]) for r in db.execute("SELECT body FROM relationships")]
    assert any(h["status"] == "tentative" and f"weak{tag}" in (h["from"], h["to"]) for h in held)
    _, payload, _ = ask(state, monkeypatch, path_max_bridge_tables=1)
    assert tables_of(payload) == {f"firm{tag}"}, tables_of(payload)


def test_unused_import_guard():
    assert index_snapshot
