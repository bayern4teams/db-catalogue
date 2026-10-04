"""Command-level coverage for the CLI surface.

`test_cli_contracts.py` covers how commands fail: a missing secret reference, a wrong key, an
unreadable artifact. This file covers what they do when they succeed, and the semantic contracts
that only the command surface exposes. Both matter now that the user's 2026-09-27 consumer
decision makes the CLIs and their HTTP services the product surface rather than scaffolding.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner
from dbig_builder.cli import main as builder_main
from dbig_collectors.cli import main as collector_main
from dbig_contracts.artifact import read_artifact
from dbig_contracts.scan import ScanRequest, sign_scan
from dbig_planner.cli import main as planner_main
from dbig_requester.cli import main as requester_main
from test_builder import source_bundle

BUNDLE_KEY = "synthetic-key-12345678901234567890"
SCAN_KEY = "synthetic-key-with-more-than-thirty-two-bytes"
# Fixture data describing a query someone else ran. This code never builds or executes it.
OBSERVED_JOIN = "SELECT * FROM orders JOIN customers ON orders.customer_id = customers.id"  # noqa: S608


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def signed_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("BUNDLE_KEY", BUNDLE_KEY)
    bundle = tmp_path / "bundle"
    source_bundle(bundle)
    return bundle


@pytest.fixture
def builder_config(tmp_path: Path) -> Path:
    return _write(
        tmp_path / "builder.toml", '[builder]\nsigning_keys = { test = "BUNDLE_KEY" }\n'
    )


# --- builder assemble -------------------------------------------------------------------


def test_assemble_publishes_a_readable_snapshot(signed_bundle, builder_config, tmp_path):
    """The central command had coverage only for its secret-failure path."""
    out = tmp_path / "snapshot"
    result = CliRunner().invoke(
        builder_main,
        ["assemble", "--config", str(builder_config), "--bundle", str(signed_bundle),
         "--out", str(out)],
    )
    assert result.exit_code == 0, result.output
    manifest, data = read_artifact(out)
    assert manifest.content_type == "catalog-snapshot"
    assert data["nodes.jsonl"], "a snapshot with no nodes would be a silent failure"


def test_assemble_refuses_to_overwrite_an_existing_snapshot(
    signed_bundle, builder_config, tmp_path
):
    """Publication is atomic and never destructive; a rerun must not clobber a prior snapshot."""
    out = tmp_path / "snapshot"
    arguments = [
        "assemble", "--config", str(builder_config), "--bundle", str(signed_bundle),
        "--out", str(out),
    ]
    assert CliRunner().invoke(builder_main, arguments).exit_code == 0
    first = (out / "manifest.json").read_bytes()
    assert CliRunner().invoke(builder_main, arguments).exit_code != 0
    assert (out / "manifest.json").read_bytes() == first, "the first snapshot must be intact"


def test_assemble_refuses_a_bundle_it_cannot_verify(builder_config, tmp_path, monkeypatch):
    monkeypatch.setenv("BUNDLE_KEY", BUNDLE_KEY)
    bundle = tmp_path / "bundle"
    source_bundle(bundle)
    monkeypatch.setenv("BUNDLE_KEY", "a-different-key-1234567890123456789")
    out = tmp_path / "snapshot"
    result = CliRunner().invoke(
        builder_main,
        ["assemble", "--config", str(builder_config), "--bundle", str(bundle), "--out", str(out)],
    )
    assert result.exit_code != 0
    assert not out.exists(), "a refused assemble must publish nothing"


# --- planner build, lineage-paths, join-paths -------------------------------------------


@pytest.fixture
def planner_graph(signed_bundle, builder_config, tmp_path):
    """A built planner graph, produced through the CLI rather than the library."""
    snapshot = tmp_path / "snapshot"
    assert (
        CliRunner()
        .invoke(
            builder_main,
            ["assemble", "--config", str(builder_config), "--bundle", str(signed_bundle),
             "--out", str(snapshot)],
        )
        .exit_code
        == 0
    )
    observations = tmp_path / "observations"
    observations.mkdir()
    (observations / "sql.jsonl").write_text(
        json.dumps(
            {
                "kind": "sql",
                "id": "sql-join",
                "source_id": "site-a",
                "dialect": "postgres",
                "sql": OBSERVED_JOIN,
                "observed_at": datetime.now(UTC).isoformat(),
                "status": "executed",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    graph = tmp_path / "graph"
    result = CliRunner().invoke(
        planner_main,
        ["build", "--snapshot", str(snapshot), "--observations", str(observations),
         "--out", str(graph)],
    )
    assert result.exit_code == 0, result.output
    _, data = read_artifact(snapshot)
    ids = {
        node.name: node.id for node in data["nodes.jsonl"] if node.record_type == "object"
    }
    return graph, ids


def test_planner_build_produces_a_usable_graph(planner_graph):
    graph, ids = planner_graph
    assert graph.exists()
    assert any(graph.iterdir()), "an empty graph directory would fail silently later"
    assert ids, "the snapshot must contain objects for the path commands to address"


def test_join_paths_and_lineage_paths_are_different_questions(planner_graph):
    """The core semantic rule at the command surface: a SELECT join is not data movement, so
    the same pair of objects can have a join path and no lineage path at all."""
    graph, ids = planner_graph
    joins = CliRunner().invoke(
        planner_main,
        ["join-paths", "--graph", str(graph), "--from", ids["orders"], "--to", ids["customers"]],
    )
    lineage = CliRunner().invoke(
        planner_main,
        ["lineage-paths", "--graph", str(graph), "--from", ids["orders"],
         "--to", ids["customers"]],
    )
    assert joins.exit_code == 0, joins.output
    assert lineage.exit_code == 0, lineage.output
    assert json.loads(lineage.output)["paths"] == [], (
        "an observed SELECT join must never be reported as lineage"
    )


def test_lineage_paths_emits_parsable_json(planner_graph):
    graph, ids = planner_graph
    result = CliRunner().invoke(
        planner_main,
        ["lineage-paths", "--graph", str(graph), "--from", ids["orders"],
         "--to", ids["customers"]],
    )
    assert isinstance(json.loads(result.output)["paths"], list)


def test_an_unknown_object_is_a_clean_command_error_naming_it(planner_graph):
    """Found by adding this coverage: an unknown identifier raised a bare
    ValueError('UNKNOWN_OBJECT') traceback that named neither the identifier nor the fix."""
    graph, _ = planner_graph
    result = CliRunner().invoke(
        planner_main,
        ["join-paths", "--graph", str(graph), "--from", "no-such-object", "--to", "also-missing"],
    )
    assert result.exit_code != 0
    assert "no-such-object" in result.output
    assert "Traceback" not in result.output


# --- requester index, search, ask, request-scan -----------------------------------------


@pytest.fixture
def requester_config(signed_bundle, builder_config, tmp_path):
    snapshot = tmp_path / "snapshot"
    CliRunner().invoke(
        builder_main,
        ["assemble", "--config", str(builder_config), "--bundle", str(signed_bundle),
         "--out", str(snapshot)],
    )
    return _write(
        tmp_path / "requester.toml",
        "[requester]\n"
        f'snapshot_dir = "{snapshot.as_posix()}"\n'
        f'state_dir = "{(tmp_path / "state").as_posix()}"\n'
        'api_key_env = "REQUESTER_TOKEN"\n',
    )


def test_index_then_search_finds_a_catalogued_object(requester_config):
    indexed = CliRunner().invoke(requester_main, ["index", "--config", str(requester_config)])
    assert indexed.exit_code == 0, indexed.output
    result = CliRunner().invoke(
        requester_main, ["search", "--config", str(requester_config), "--query", "orders"]
    )
    assert result.exit_code == 0, result.output
    assert "orders" in result.output


def test_search_before_index_does_not_crash(requester_config):
    """A first-run operator ordering mistake must produce a clean error, not a traceback."""
    result = CliRunner().invoke(
        requester_main, ["search", "--config", str(requester_config), "--query", "orders"]
    )
    assert result.exit_code != 0
    assert "run `dbig-requester index`" in result.output
    assert "Traceback" not in result.output


def test_ask_without_a_configured_model_is_a_clean_command_error(requester_config):
    """`ask` must never reach for a model that was not configured, and must say so plainly."""
    result = CliRunner().invoke(
        requester_main,
        ["ask", "--config", str(requester_config), "--question", "where are customers?"],
    )
    assert result.exit_code != 0
    assert "private answer model is not configured" in result.output
    assert "Traceback" not in result.output


def test_request_scan_without_scan_configured_is_a_clean_command_error(
    requester_config, tmp_path
):
    request_path = _write(tmp_path / "request.json", _scan_request().model_dump_json())
    result = CliRunner().invoke(
        requester_main,
        ["request-scan", "--config", str(requester_config), "--request", str(request_path),
         "--dispatch", "bundle"],
    )
    assert result.exit_code != 0
    assert "scan dispatch is not configured" in result.output


# --- collector accept-scan and process-scans --------------------------------------------


def _scan_request(request_id: str = "request-1") -> ScanRequest:
    now = datetime.now(UTC)
    return ScanRequest(
        id=request_id,
        source_id="site-a",
        scopes=["public"],
        capabilities=["metadata"],
        reason="refresh catalog",
        requester_id="catalogue",
        idempotency_key="idem-1",
        issued_at=now - timedelta(seconds=1),
        expires_at=now + timedelta(minutes=10),
        reply_location="local-receipt",
    )


@pytest.fixture
def collector_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DBIG_SIGNING_KEY", SCAN_KEY)
    monkeypatch.setenv("DBIG_CONNECTION", '{"host": "127.0.0.1"}')
    return _write(
        tmp_path / "source.toml",
        "[source]\n"
        'source_id = "site-a"\n'
        'kind = "postgresql"\n'
        'connection_env = "DBIG_CONNECTION"\n'
        'signing_key_env = "DBIG_SIGNING_KEY"\n'
        'signing_key_id = "test"\n'
        'namespaces = ["public"]\n'
        'authorized_requesters = ["catalogue"]\n',
    )


def test_accept_scan_queues_an_authorized_request(collector_config, tmp_path):
    envelope = sign_scan(_scan_request(), "test", SCAN_KEY.encode())
    request_path = _write(tmp_path / "request.json", envelope.model_dump_json())
    queue = tmp_path / "queue.sqlite3"
    result = CliRunner().invoke(
        collector_main,
        ["accept-scan", "--config", str(collector_config), "--request", str(request_path),
         "--queue-db", str(queue)],
    )
    assert result.exit_code == 0, result.output
    assert "queued" in result.output
    assert queue.exists()


def test_accept_scan_refuses_an_unsigned_request(collector_config, tmp_path):
    """An unsigned scan request must never be queued; the collector holds real credentials."""
    request_path = _write(tmp_path / "request.json", _scan_request().model_dump_json())
    result = CliRunner().invoke(
        collector_main,
        ["accept-scan", "--config", str(collector_config), "--request", str(request_path),
         "--queue-db", str(tmp_path / "queue.sqlite3")],
    )
    assert result.exit_code != 0


def test_accept_scan_refuses_a_request_signed_with_the_wrong_key(collector_config, tmp_path):
    envelope = sign_scan(_scan_request(), "test", b"a-different-key-123456789012345678")
    request_path = _write(tmp_path / "request.json", envelope.model_dump_json())
    result = CliRunner().invoke(
        collector_main,
        ["accept-scan", "--config", str(collector_config), "--request", str(request_path),
         "--queue-db", str(tmp_path / "queue.sqlite3")],
    )
    assert result.exit_code != 0


def test_process_scans_on_an_empty_queue_is_not_an_error(collector_config, tmp_path):
    """Nothing to do is a normal scheduler outcome, not a failure."""
    result = CliRunner().invoke(
        collector_main,
        ["process-scans", "--config", str(collector_config),
         "--queue-db", str(tmp_path / "queue.sqlite3"), "--out-root", str(tmp_path / "out")],
    )
    assert result.exit_code == 0, result.output


# --- serve: the private-network posture -------------------------------------------------
#
# User decision on 2026-09-27: the HTTP adapters target a trusted private network, bind
# loopback, and public exposure is unsupported rather than partially hardened. All three
# commands already refuse a non-loopback bind; none of that was covered by a test.


@pytest.mark.parametrize(
    ("command", "extra"),
    [
        ("collector", ["--queue-db", "queue.sqlite3"]),
        ("planner", []),
        ("requester", []),
    ],
)
@pytest.mark.parametrize("host", ["0.0.0.0", "10.0.0.5", "192.168.1.10"])  # noqa: S104
def test_serve_refuses_a_non_loopback_bind(command, extra, host, tmp_path, monkeypatch):
    mains = {"collector": collector_main, "planner": planner_main, "requester": requester_main}
    config = _write(tmp_path / "any.toml", "[unused]\n")
    arguments = ["serve", "--config", str(config), *extra, "--host", host, "--port", "8080"]
    result = CliRunner().invoke(mains[command], arguments)
    assert result.exit_code != 0
    assert "loopback" in result.output, (
        f"{command} serve must refuse {host} before reading any configuration"
    )


@pytest.mark.parametrize(
    ("command", "extra"),
    [
        ("collector", ["--queue-db", "queue.sqlite3"]),
        ("planner", []),
        ("requester", []),
    ],
)
@pytest.mark.parametrize("host", ["not-an-address", "example.com", "localhost", ""])
def test_serve_refuses_a_hostname_rather_than_resolving_it(command, extra, host, tmp_path):
    """A name could resolve anywhere, including off-host, so it is refused outright, with the
    same one-line refusal as a non-loopback address. It used to reach `ip_address` unguarded
    and fail with a ValueError traceback."""
    mains = {"collector": collector_main, "planner": planner_main, "requester": requester_main}
    config = _write(tmp_path / "any.toml", "[unused]\n")
    result = CliRunner().invoke(
        mains[command],
        ["serve", "--config", str(config), *extra, "--host", host, "--port", "8080"],
    )
    assert result.exit_code == 2, result.exception
    assert "loopback" in result.output


def test_plan_row_limit_is_settable_from_the_command_line(planner_graph):
    """The runbook tells an operator to re-plan with a larger limit when a read may have been
    cut off; the CLI capped every plan at the configured default of 1000 rows."""
    graph, ids = planner_graph
    joins = CliRunner().invoke(
        planner_main,
        ["join-paths", "--graph", str(graph), "--from", ids["orders"], "--to", ids["customers"]],
    )
    path_id = json.loads(joins.output)["paths"][0]["path_id"]

    def limit(*extra: str) -> str:
        result = CliRunner().invoke(
            planner_main, ["plan", "--graph", str(graph), "--path-id", path_id, *extra]
        )
        assert result.exit_code == 0, result.output
        return json.loads(result.output)["steps"][0]["sql"].rsplit("LIMIT ", 1)[1]

    assert limit() == "1000"
    assert limit("--max-rows", "5000") == "5000"
    assert limit("--max-rows", "7") == "7"
    refused = CliRunner().invoke(
        planner_main, ["plan", "--graph", str(graph), "--path-id", path_id, "--max-rows", "0"]
    )
    assert refused.exit_code == 2
