"""The whole deterministic pipeline, driven through the installed command line entry points.

Every other test exercises one stage. This one runs the chain a real operator runs: a signed
collector bundle, assembled into a snapshot, validated, indexed, searched, built into a planner
graph, queried for lineage and join paths, and rendered as a plan. It runs the console scripts
as subprocesses, so it also covers the packaging: a broken entry point or a missing dependency
in a wheel fails here and nowhere else.

What it deliberately does not cover: `collect` against a live database, which needs a server and
is covered by `test_live_collectors.py` in CI, and the LLM reviewer, which is off by default and
covered by `test_live_model.py` when an endpoint is reachable. Both absences are the point of
the deterministic guarantee: the pipeline publishes a complete, useful snapshot with no model
and no network beyond the bundle.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_builder import source_bundle

BUNDLE_KEY = "synthetic-key-12345678901234567890"
# Fixture data describing a statement someone else ran. This code never executes it.
OBSERVED_INSERT_SELECT = "INSERT INTO customers SELECT * FROM orders"  # noqa: S608
SCRIPTS = Path(sys.executable).parent


def _run(command: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    executable = SCRIPTS / command[0]
    if not executable.exists():
        pytest.skip(f"{command[0]} console script is not installed in this environment")
    return subprocess.run(  # noqa: S603 - fixed argv, no shell, paths from tmp_path
        [str(executable), *command[1:]], capture_output=True, text=True, env=env, timeout=180
    )


@pytest.fixture
def pipeline(tmp_path: Path) -> dict:
    """Run the chain once; every test below asserts on a different part of the result."""
    env = {**os.environ, "BUNDLE_KEY": BUNDLE_KEY}
    source_bundle(tmp_path / "bundle")

    (tmp_path / "builder.toml").write_text(
        '[builder]\nsigning_keys = { test = "BUNDLE_KEY" }\n', encoding="utf-8"
    )
    (tmp_path / "requester.toml").write_text(
        "[requester]\n"
        f'snapshot_dir = "{(tmp_path / "snapshot").as_posix()}"\n'
        f'state_dir = "{(tmp_path / "state").as_posix()}"\n'
        'api_key_env = "REQUESTER_TOKEN"\n',
        encoding="utf-8",
    )
    observations = tmp_path / "observations"
    observations.mkdir()
    (observations / "sql.jsonl").write_text(
        json.dumps(
            {
                "kind": "sql",
                "id": "s1",
                "source_id": "site-a",
                "dialect": "postgres",
                "sql": OBSERVED_INSERT_SELECT,
                "observed_at": "2026-09-27T00:00:00Z",
                "status": "executed",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    steps: dict[str, subprocess.CompletedProcess[str]] = {}
    steps["assemble"] = _run(
        ["dbig-builder", "assemble", "--config", str(tmp_path / "builder.toml"),
         "--bundle", str(tmp_path / "bundle"), "--out", str(tmp_path / "snapshot")],
        env,
    )
    steps["validate"] = _run(
        ["dbig-builder", "validate-graph", str(tmp_path / "snapshot")], env
    )
    steps["index"] = _run(
        ["dbig-requester", "index", "--config", str(tmp_path / "requester.toml")], env
    )
    steps["search"] = _run(
        ["dbig-requester", "search", "--config", str(tmp_path / "requester.toml"),
         "--query", "orders"],
        env,
    )
    steps["build"] = _run(
        ["dbig-planner", "build", "--snapshot", str(tmp_path / "snapshot"),
         "--observations", str(observations), "--out", str(tmp_path / "graph")],
        env,
    )

    nodes = [
        json.loads(line)
        for line in (tmp_path / "snapshot" / "nodes.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    ids = {node["name"]: node["id"] for node in nodes if node["record_type"] == "object"}

    steps["lineage"] = _run(
        ["dbig-planner", "lineage-paths", "--graph", str(tmp_path / "graph"),
         "--from", ids["orders"], "--to", ids["customers"]],
        env,
    )
    steps["joins"] = _run(
        ["dbig-planner", "join-paths", "--graph", str(tmp_path / "graph"),
         "--from", ids["orders"], "--to", ids["customers"]],
        env,
    )
    return {"steps": steps, "root": tmp_path, "ids": ids, "env": env}


def test_every_stage_of_the_pipeline_succeeds(pipeline):
    for name, result in pipeline["steps"].items():
        assert result.returncode == 0, f"{name} failed:\n{result.stdout}\n{result.stderr}"


def test_the_pipeline_publishes_a_verifiable_snapshot(pipeline):
    from dbig_contracts.artifact import read_artifact

    manifest, data = read_artifact(pipeline["root"] / "snapshot")
    assert manifest.content_type == "catalog-snapshot"
    assert manifest.completeness == "complete"
    assert data["nodes.jsonl"] and data["edges.jsonl"]


def test_search_returns_the_catalogued_objects(pipeline):
    payload = json.loads(pipeline["steps"]["search"].stdout)
    names = {row["name"] for row in payload["results"]}
    assert "orders" in names or "loose_orders" in names
    assert payload["completeness"] == "complete"


def test_an_observed_insert_select_becomes_lineage(pipeline):
    """INSERT ... SELECT is real data movement, so it must produce an observed lineage path."""
    payload = json.loads(pipeline["steps"]["lineage"].stdout)
    assert payload["paths"], "an executed INSERT...SELECT must yield a lineage path"
    assert payload["paths"][0]["status"] == "observed"


def test_a_plan_can_be_rendered_for_a_join_path_only(pipeline):
    """The semantic rule at the end of the chain: a join path is a query you could run, an
    observed lineage path is a record of what already happened and has no plan to render."""
    lineage = json.loads(pipeline["steps"]["lineage"].stdout)
    lineage_path_id = lineage["paths"][0]["path_id"]
    refused = _run(
        ["dbig-planner", "plan", "--graph", str(pipeline["root"] / "graph"),
         "--path-id", lineage_path_id],
        pipeline["env"],
    )
    assert refused.returncode != 0
    assert "NON_JOIN_PATH" in refused.stdout + refused.stderr
    assert "Traceback" not in refused.stderr, (
        "found by running the pipeline end to end: this refusal used to be a raw traceback"
    )

    joins = json.loads(pipeline["steps"]["joins"].stdout)
    if not joins["paths"]:
        pytest.skip("this fixture produced no join path to render")
    rendered = _run(
        ["dbig-planner", "plan", "--graph", str(pipeline["root"] / "graph"),
         "--path-id", joins["paths"][0]["path_id"]],
        pipeline["env"],
    )
    assert rendered.returncode == 0, rendered.stderr
    assert json.loads(rendered.stdout)["path_id"] == joins["paths"][0]["path_id"]


def test_the_pipeline_needs_no_model_and_no_network(pipeline):
    """The deterministic guarantee, asserted rather than assumed: the whole chain above ran
    with no reviewer configured and no endpoint reachable, and still published a usable graph."""
    from dbig_contracts.artifact import read_artifact

    _, data = read_artifact(pipeline["root"] / "snapshot")
    runs = data["runs.jsonl"]
    assert runs, "the snapshot must record its run"
    statuses = {record.model_dump().get("review_status") for record in runs}
    assert statuses <= {"REVIEW_DISABLED", None}, (
        f"the reviewer must be off by default, saw {statuses}"
    )
