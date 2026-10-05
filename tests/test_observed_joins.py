"""Joins seen in query logs become join paths (user decision 2026-10-05).

Before this, `dbig-planner build` parsed observed SELECT joins and stored them, but only at
table level and without columns, and `join-paths` never read them, so a join an application
runs every day could not be planned. Now each `ON a.x = b.y` with resolvable columns becomes a
`JOINS_OBSERVED` reference with status `observed`, and a path through it is labelled `observed`.
A SELECT join is still never lineage.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from dbig_contracts.artifact import write_artifact
from dbig_contracts.models import Field, Object, Source
from dbig_planner.config import PlannerConfig
from dbig_planner.graph import build_graph
from dbig_planner.observations import SQLRelation, observed_join_columns
from dbig_planner.paths import PathRequest, find_paths
from dbig_planner.plans import PlanRequest, render_plan

V = SQLRelation("visits", "public")
A = SQLRelation("accounts", "public")
R = SQLRelation("regions", "public")


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        (
            "SELECT 1 FROM public.visits v JOIN public.accounts a ON v.account_ref = a.acct_no",
            [(V, A, [("account_ref", "acct_no")])],
        ),
        (  # two columns in one ON, in written order
            "SELECT 1 FROM public.visits v JOIN public.accounts a "
            "ON v.account_ref = a.acct_no AND (v.region = a.region)",
            [(V, A, [("account_ref", "acct_no"), ("region", "region")])],
        ),
        (  # the pair comes from the columns, not from the order in FROM
            "SELECT 1 FROM public.visits v JOIN public.accounts a ON v.account_ref = a.acct_no "
            "JOIN public.regions r ON v.region = r.code",
            [(V, A, [("account_ref", "acct_no")]), (V, R, [("region", "code")])],
        ),
        (  # unaliased tables qualify by name
            "SELECT 1 FROM public.visits JOIN public.accounts "
            "ON visits.account_ref = accounts.acct_no",
            [(V, A, [("account_ref", "acct_no")])],
        ),
    ],
)
def test_on_clause_equalities_become_column_pairs(sql, expected):
    assert observed_join_columns(sql, "postgres") == expected


@pytest.mark.parametrize(
    "sql",
    [
        # OR: which columns join is not certain
        "SELECT 1 FROM visits v JOIN accounts a ON v.account_ref = a.acct_no OR v.id = a.id",
        # unqualified column: cannot tell which table it belongs to
        "SELECT 1 FROM visits v JOIN accounts a ON account_ref = a.acct_no",
        # not an equality
        "SELECT 1 FROM visits v JOIN accounts a ON v.account_ref > a.acct_no",
        # a CTE is not a catalogued table
        "WITH x AS (SELECT 1 AS k) SELECT 1 FROM x JOIN accounts a ON x.k = a.acct_no",
        # USING names no table per column
        "SELECT 1 FROM visits v JOIN accounts a USING (region)",
    ],
)
def test_uncertain_joins_are_skipped_not_guessed(sql):
    assert observed_join_columns(sql, "postgres") == []


def _snapshot(tmp_path):
    now = datetime.now(UTC)
    common = {"observed_at": now, "collector_version": "synthetic", "provenance": "fixture"}
    nodes = [
        Source(id="pg", source_id="pg", kind="postgresql", name="pg", **common),
        Object(id="visits", source_id="pg", native_id="1", kind="table", name="visits",
               namespace="public", **common),
        Object(id="accounts", source_id="pg", native_id="2", kind="table", name="accounts",
               namespace="public", **common),
    ]
    for owner, name, ordinal in (("visits", "id", 1), ("visits", "account_ref", 2),
                                 ("accounts", "acct_no", 1), ("accounts", "owner", 2)):
        nodes.append(Field(id=f"{owner}.{name}", object_id=owner, source_id="pg",
                           native_id=f"{owner}:{ordinal}", name=name, data_type="integer",
                           ordinal=ordinal, **common))
    write_artifact(tmp_path / "snapshot", "catalog-snapshot",
                   {"nodes.jsonl": nodes, "edges.jsonl": [], "evidence.jsonl": []},
                   producer="fixture", source_run_ids=[])
    return now


def _graph(tmp_path, statements: list[str]) -> PlannerConfig:
    now = _snapshot(tmp_path)
    observations = tmp_path / "observations"
    observations.mkdir()
    with (observations / "log.jsonl").open("w", encoding="utf-8") as stream:
        for number, sql in enumerate(statements, start=1):
            stream.write(json.dumps({
                "kind": "sql", "id": f"q{number}", "source_id": "pg", "dialect": "postgres",
                "sql": sql, "observed_at": now.isoformat(), "status": "executed",
            }) + "\n")
    config = PlannerConfig(graph_dir=tmp_path / "graph")
    build_graph(tmp_path / "snapshot", observations, config.graph_dir, config)
    return config


JOIN = (
    "SELECT v.id, a.owner FROM public.visits v "
    "JOIN public.accounts a ON v.account_ref = a.acct_no"
)


def test_an_observed_join_is_a_join_path_and_a_runnable_plan(tmp_path):
    config = _graph(tmp_path, [JOIN, JOIN.replace("v.id", "v.account_ref")])
    paths = find_paths(config.graph_dir, PathRequest(from_id="visits", to_id="accounts"),
                       "join", config)
    (path,) = paths["paths"]
    assert path["status"] == "observed"
    graph = json.loads((config.graph_dir / "graph.json").read_text(encoding="utf-8"))
    (reference,) = [item for item in graph["references"] if item["kind"] == "JOINS_OBSERVED"]
    assert reference["field_pairs"] == [["visits.account_ref", "accounts.acct_no"]]
    assert reference["observation_ids"] == ["q1", "q2"], "two queries, one join, both cited"
    plan = render_plan(config.graph_dir, PlanRequest(path_id=path["path_id"]), config)
    assert plan["status"] == "ready_for_review"
    assert '"t0"."account_ref" = "t1"."acct_no"' in plan["steps"][0]["sql"]
    assert any("query logs" in warning for warning in plan["warnings"])
    lineage = find_paths(config.graph_dir, PathRequest(from_id="visits", to_id="accounts"),
                         "lineage", config)
    assert lineage["paths"] == [], "a SELECT join is never lineage"


def test_a_join_on_an_unknown_column_is_reported_not_guessed(tmp_path):
    config = _graph(tmp_path, [JOIN.replace("a.acct_no", "a.no_such_column")])
    graph = json.loads((config.graph_dir / "graph.json").read_text(encoding="utf-8"))
    assert not [item for item in graph["references"] if item["kind"] == "JOINS_OBSERVED"]
    assert graph["diagnostics"] == [
        {"id": "q1", "code": "UNRESOLVED_SQL", "detail": "ValueError"}
    ]


def test_a_declared_key_is_preferred_over_an_observed_join_for_the_same_hop():
    from dbig_planner.paths import _strength

    assert _strength({"status": "declared"}) > _strength({"status": "observed"}) > _strength(
        {"status": "tentative"}
    )
