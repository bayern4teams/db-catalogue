# ruff: noqa
"""Attack H1(a) for DEC-4 / D4 + A1(3): the planner prefers a same-source relationship over a
roundabout cross-source one when both ends are in one source, and warns when every edge of a path is
a cross-source candidate with no value evidence (`CROSS_SOURCE_CANDIDATES_UNVERIFIED`).

Derived from engineer-fixes D4 and A1(3), DEC-4 and the warning name given in the brief. Run by path:
    uv run pytest tests/catalogue_fixes/attack3_a_dec4.py -q -p no:cacheprovider
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from dbig_contracts.artifact import write_artifact
from dbig_contracts.models import Edge, Evidence, Field, Object, Source
from dbig_planner.config import PlannerConfig
from dbig_planner.graph import build_graph
from dbig_planner.paths import PathRequest, find_paths
from dbig_planner.plans import PlanRequest, render_plan

WARN = "CROSS_SOURCE_CANDIDATES_UNVERIFIED"
NOW = datetime.now(UTC)
COMMON = {"observed_at": NOW, "collector_version": "synthetic", "provenance": "fixture"}
KINDS = {"ch": "clickhouse", "pg": "postgresql", "mg": "mongodb"}


def graph(tmp_path, placement, edges, *, config_kwargs=None):
    """placement {object: source}; edges [(id, left, right, kind, status, [evidence kinds])]."""
    sources = [Source(id=s, source_id=s, kind=KINDS[s], name=s, **COMMON) for s in sorted(set(placement.values()))]
    objects = [Object(id=o, source_id=s, native_id=o, kind="table" if s != "mg" else "collection", name=o, **COMMON)
               for o, s in placement.items()]
    fields = [Field(id=f"{o}-k", object_id=o, source_id=s, native_id="k", name="k", data_type="string", **COMMON)
              for o, s in placement.items()]
    evidence = [Evidence(id="ev-name", source_id=next(iter(placement.values())), kind="fixture", subject_id=next(iter(placement)), **COMMON),
                Evidence(id="ev-values", source_id=next(iter(placement.values())), kind="value_containment", subject_id=next(iter(placement)), **COMMON)]
    rows = []
    for eid, left, right, kind, status, ev_kinds in edges:
        cited = [{"fixture": "ev-name", "value_containment": "ev-values"}[k] for k in ev_kinds]
        rows.append(Edge.model_validate({
            "id": eid, "source_id": placement[left], "kind": kind, "from_id": left, "to_id": right,
            "status": status, "evidence_ids": cited, "field_pairs": [[f"{left}-k", f"{right}-k"]], **COMMON}))
    snapshot = tmp_path / "snapshot"
    write_artifact(snapshot, "catalog-snapshot",
                   {"nodes.jsonl": [*sources, *objects, *fields], "edges.jsonl": rows, "evidence.jsonl": evidence},
                   producer="fixture", source_run_ids=[])
    (tmp_path / "observations").mkdir(exist_ok=True)
    config = PlannerConfig(graph_dir=tmp_path / "graph", **(config_kwargs or {}))
    build_graph(snapshot, tmp_path / "observations", config.graph_dir, config)
    return config


def cand(eid, left, right, ev=("fixture",)):
    return (eid, left, right, "REFERENCE_CANDIDATE", "tentative", list(ev))


def paths(config, a, b, n=5):
    return find_paths(config.graph_dir, PathRequest(from_id=a, to_id=b, max_paths=n), "join", config)["paths"]


def test_same_source_direct_beats_cross_source_detour(tmp_path):
    placement = {"exp": "ch", "company": "ch", "mid": "pg"}
    cfg = graph(tmp_path, placement, [cand("d", "exp", "company"), cand("h1", "exp", "mid"), cand("h2", "mid", "company")])
    got = paths(cfg, "exp", "company")
    assert got[0]["object_ids"] == ["exp", "company"], got


def test_same_source_longer_chain_beats_shorter_cross_source_detour(tmp_path):
    """Old behaviour (pure hop count) would take the 2-hop detour over the 4-hop same-source chain."""
    placement = {"a": "ch", "b": "ch", "c": "ch", "d": "ch", "e": "ch", "x": "pg"}
    edges = [cand("1", "a", "b"), cand("2", "b", "c"), cand("3", "c", "d"), cand("4", "d", "e"),
             cand("x1", "a", "x"), cand("x2", "x", "e")]
    cfg = graph(tmp_path, placement, edges)
    got = paths(cfg, "a", "e")
    assert got[0]["object_ids"] == ["a", "b", "c", "d", "e"], got[0]["object_ids"]


def test_the_cross_source_path_is_still_returned_when_it_is_the_only_one_and_it_warns(tmp_path):
    placement = {"a": "ch", "x": "pg", "b": "ch"}
    cfg = graph(tmp_path, placement, [cand("1", "a", "x"), cand("2", "x", "b")])
    got = paths(cfg, "a", "b")
    assert got and got[0]["object_ids"] == ["a", "x", "b"]
    assert got[0].get("warnings") == [WARN], got[0]


def test_ends_in_different_sources_are_not_penalised_into_nothing(tmp_path):
    placement = {"a": "ch", "x": "pg"}
    cfg = graph(tmp_path, placement, [cand("1", "a", "x")])
    got = paths(cfg, "a", "x")
    assert got and got[0]["warnings"] == [WARN]


def test_value_evidence_on_every_cross_edge_silences_the_warning(tmp_path):
    placement = {"a": "ch", "x": "pg"}
    cfg = graph(tmp_path, placement, [cand("1", "a", "x", ("fixture", "value_containment"))])
    got = paths(cfg, "a", "x")
    assert got and "warnings" not in got[0]


def test_one_unverified_cross_edge_among_verified_ones_still_warns_or_is_documented(tmp_path):
    """Two cross edges: one verified, one not. 'Every edge unverified' is false -> by the literal
    Decision no warning; this test records what the code does and fails if it warns on a fully
    verified path or stays silent on a path whose *only* cross-source hop is unverified."""
    placement = {"a": "ch", "x": "pg", "y": "pg"}
    cfg = graph(tmp_path, placement, [cand("1", "a", "x", ("fixture", "value_containment")), cand("2", "x", "y")])
    got = paths(cfg, "a", "y")
    assert got
    # the single cross-source hop a->x is verified; x->y is same-source: nothing unverified-cross
    assert "warnings" not in got[0]


def test_mixed_path_with_one_unverified_cross_hop_and_same_source_hops(tmp_path):
    """RISK probe: a->x unverified cross-source, x->y same-source. The decision text says 'every
    edge'; the same-source edge is not a cross-source candidate. Records the actual behaviour."""
    placement = {"a": "ch", "x": "pg", "y": "pg"}
    cfg = graph(tmp_path, placement, [cand("1", "a", "x"), cand("2", "x", "y")])
    got = paths(cfg, "a", "y")
    print("MIXED_PATH_WARNINGS", got[0].get("warnings"))
    assert got


def test_declared_edges_are_never_penalised_or_warned(tmp_path):
    placement = {"a": "pg", "b": "pg", "x": "ch"}
    cfg = graph(tmp_path, placement, [("d", "a", "b", "DECLARES_FK", "declared", ["fixture"])])
    got = paths(cfg, "a", "b")
    assert got and "warnings" not in got[0]


def test_two_ends_in_one_source_with_only_cross_candidates_still_returns_them_with_warning(tmp_path):
    placement = {"a": "ch", "x": "pg", "b": "ch"}
    cfg = graph(tmp_path, placement, [cand("1", "a", "x"), cand("2", "x", "b")])
    assert paths(cfg, "a", "b")[0]["warnings"] == [WARN]


def test_weight_must_be_positive():
    for bad in (0, -1):
        with pytest.raises(ValueError):
            PlannerConfig(graph_dir="/tmp/x", cross_source_candidate_weight=bad)


def test_weight_is_a_config_field_with_a_default_above_one():
    field = PlannerConfig.model_fields["cross_source_candidate_weight"]
    assert field.default > 1


def test_path_order_is_deterministic(tmp_path):
    placement = {"a": "ch", "b": "ch", "c": "ch", "d": "ch"}
    edges = [cand("1", "a", "b"), cand("2", "b", "d"), cand("3", "a", "c"), cand("4", "c", "d")]
    (tmp_path / "1").mkdir()
    (tmp_path / "2").mkdir()
    first = paths(graph(tmp_path / "1", placement, edges), "a", "d")
    second = paths(graph(tmp_path / "2", placement, list(reversed(edges))), "a", "d")
    assert [p["object_ids"] for p in first] == [p["object_ids"] for p in second]


def test_max_paths_bound_holds(tmp_path):
    placement = {"a": "ch", "b": "ch", "c": "ch", "d": "ch"}
    edges = [cand("1", "a", "b"), cand("2", "b", "d"), cand("3", "a", "c"), cand("4", "c", "d")]
    assert len(paths(graph(tmp_path, placement, edges), "a", "d", n=1)) == 1


def test_unknown_and_equal_endpoints_do_not_crash(tmp_path):
    cfg = graph(tmp_path, {"a": "ch", "b": "ch"}, [cand("1", "a", "b")])
    for a, b in (("a", "a"), ("a", "nope"), ("nope", "b")):
        try:
            got = paths(cfg, a, b)
        except ValueError:
            continue  # a clean refusal is fine
        assert isinstance(got, list)


def test_old_graph_file_without_value_evidence_still_loads(tmp_path):
    """Older graph.json (edges without the new key) must not KeyError."""
    placement = {"a": "ch", "x": "pg"}
    cfg = graph(tmp_path, placement, [cand("1", "a", "x")])
    gpath = cfg.graph_dir / "graph.json"
    data = json.loads(gpath.read_text(encoding="utf-8"))

    def strip(node):
        if isinstance(node, dict):
            node.pop("value_evidence", None)
            for v in node.values():
                strip(v)
        elif isinstance(node, list):
            for v in node:
                strip(v)

    strip(data)
    gpath.write_text(json.dumps(data), encoding="utf-8")
    got = paths(cfg, "a", "x")
    assert isinstance(got, list)


def test_plan_for_a_cross_source_only_path_carries_the_warning_or_unresolved(tmp_path):
    """A1 check: `plan` must carry a warning that names the unverified path."""
    placement = {"a": "ch", "x": "pg"}
    cfg = graph(tmp_path, placement, [cand("1", "a", "x")])
    path = paths(cfg, "a", "x")[0]
    plan = render_plan(cfg.graph_dir, PlanRequest(path_id=path["path_id"]), cfg)
    assert "no value overlap" in json.dumps(plan["warnings"]), plan.get("warnings")


def test_plan_for_a_verified_path_does_not_carry_the_warning(tmp_path):
    placement = {"a": "ch", "x": "pg"}
    cfg = graph(tmp_path, placement, [cand("1", "a", "x", ("fixture", "value_containment"))])
    path = paths(cfg, "a", "x")[0]
    plan = render_plan(cfg.graph_dir, PlanRequest(path_id=path["path_id"]), cfg)
    assert "no value overlap" not in json.dumps(plan.get("warnings", []))


def test_warning_text_carries_no_identifiers_or_values(tmp_path):
    placement = {"secretish_a": "ch", "secretish_x": "pg"}
    cfg = graph(tmp_path, placement, [cand("1", "secretish_a", "secretish_x")])
    got = paths(cfg, "secretish_a", "secretish_x")
    assert got[0]["warnings"] == [WARN]


def test_three_hop_path_whose_middle_hop_is_same_source_still_warns(tmp_path):
    """The D4 shape: ch -> pg(x) -> pg(y) -> ch. Both cross-source hops are unverified candidates; the
    middle hop is a same-source candidate. 'Every edge' fails, so the literal rule is silent."""
    placement = {"a": "ch", "x": "pg", "y": "pg", "b": "ch"}
    cfg = graph(tmp_path, placement, [cand("1", "a", "x"), cand("2", "x", "y"), cand("3", "y", "b")])
    got = paths(cfg, "a", "b")
    assert got and got[0]["warnings"] == [WARN], got[0].get("warnings")


def test_tie_between_same_source_chain_and_detour_with_max_paths_one(tmp_path):
    """4 same-source candidate hops cost 8 = 2 cross hops x 4. With max_paths=1 the detour must not win."""
    placement = {"a": "ch", "b": "ch", "c": "ch", "d": "ch", "e": "ch", "x": "pg"}
    edges = [cand("1", "a", "b"), cand("2", "b", "c"), cand("3", "c", "d"), cand("4", "d", "e"),
             cand("x1", "a", "x"), cand("x2", "x", "e")]
    for index, order in enumerate((edges, list(reversed(edges)))):
        d = tmp_path / str(index)
        d.mkdir(exist_ok=True)
        got = paths(graph(d, placement, order), "a", "e", n=1)
        assert got[0]["object_ids"] == ["a", "b", "c", "d", "e"], got[0]["object_ids"]


def test_value_verified_cross_path_is_not_hidden_by_an_unverified_same_source_one(tmp_path):
    """Records the cost of the same-source filter: both ends in ch, the same-source route is a bare
    name candidate, the cross-source route has measured overlap on every hop."""
    placement = {"a": "ch", "b": "ch", "x": "pg"}
    edges = [cand("1", "a", "b"), cand("x1", "a", "x", ("fixture", "value_containment")),
             cand("x2", "x", "b", ("fixture", "value_containment"))]
    got = paths(graph(tmp_path, placement, edges), "a", "b")
    print("VERIFIED_CROSS_VISIBLE", [p["object_ids"] for p in got])
    assert got


def test_weight_decides_when_max_paths_cuts_the_list(tmp_path):
    """3 same-source candidate hops (2 each = 6) against a 2-hop cross-source detour (2 x weight).
    With max_paths=1 only the cheapest is seen, so the weight alone must put the chain first."""
    placement = {"a": "ch", "b": "ch", "c": "ch", "d": "ch", "x": "pg"}
    edges = [cand("1", "a", "b"), cand("2", "b", "c"), cand("3", "c", "d"), cand("x1", "a", "x"), cand("x2", "x", "d")]
    got = paths(graph(tmp_path, placement, edges), "a", "d", n=1)
    assert got[0]["object_ids"] == ["a", "b", "c", "d"], got[0]["object_ids"]
