"""Decision 6 (owner, 2026-11-21): OpenAPI is on for all five services, and the guard still
sits in front of every route, the OpenAPI document included (decision 16 keeps the per-caller
keys). The document is the source `docs/API.md` is written from."""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta

from dbig_builder.api import create_app as builder_app
from dbig_builder.config import BuilderConfig, ServiceConfig
from dbig_contracts.api import create_app as contracts_app
from dbig_planner.api import create_app as planner_app
from dbig_planner.config import PlannerConfig
from dbig_requester.api import create_app as requester_app
from dbig_requester.config import RequesterConfig
from fastapi.testclient import TestClient

SNAPSHOT_KEYS = ("nodes.jsonl", "edges.jsonl", "evidence.jsonl", "runs.jsonl")


def _keys(tmp_path, monkeypatch) -> tuple[object, str]:
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    path = tmp_path / "keys.json"
    path.write_text(json.dumps({"keys": [
        {"caller": "reader", "sha256": hashlib.sha256(token.encode()).hexdigest(),
         "scopes": ["*"], "expires_at": expires}
    ]}), encoding="utf-8")
    monkeypatch.setenv("DBIG_CALLER_KEYS_FILE", str(path))
    return path, f"Bearer {token}"


def _apps(tmp_path, monkeypatch):
    """One live app per service, built the way `serve` builds it."""
    key_path, header = _keys(tmp_path, monkeypatch)
    (tmp_path / "state").mkdir()
    apps = {
        "contracts": contracts_app(key_path),
        "planner": planner_app(
            PlannerConfig(graph_dir=tmp_path / "graph", api_key_env="DBIG_CALLER_KEYS_FILE")
        ),
        "builder": builder_app(
            BuilderConfig(
                signing_keys={"k": "DBIG_CALLER_KEYS_FILE"},
                service=ServiceConfig(
                    bundles_root=tmp_path,
                    snapshots_root=tmp_path / "state",
                    jobs_db=tmp_path / "state" / "jobs.sqlite3",
                ),
            )
        ),
    }
    snapshot = tmp_path / "snapshot"
    from dbig_contracts.artifact import write_artifact

    write_artifact(
        snapshot, "catalog-snapshot", {name: [] for name in SNAPSHOT_KEYS},
        producer="test/1", source_run_ids=[],
    )
    from dbig_requester.search import index_snapshot

    config = RequesterConfig(
        snapshot_dir=snapshot, state_dir=tmp_path / "state",
        api_key_env="DBIG_CALLER_KEYS_FILE",
    )
    index_snapshot(config.snapshot_dir, config.state_dir)
    apps["requester"] = requester_app(config)
    from dbig_collectors.api import create_app as collector_app
    from dbig_collectors.config import load_config as load_collector_config

    collector_toml = tmp_path / "collector.toml"
    collector_toml.write_text(
        "[source]\nsource_id = \"s\"\nkind = \"postgresql\"\n"
        "connection_env = \"DBIG_CALLER_KEYS_FILE\"\n"
        "signing_key_env = \"DBIG_CALLER_KEYS_FILE\"\nsigning_key_id = \"k\"\n"
        "namespaces = [\"public\"]\n",
        encoding="utf-8",
    )
    apps["collectors"] = collector_app(
        load_collector_config(collector_toml), tmp_path / "state" / "queue.sqlite3"
    )
    return apps, header


def test_all_five_services_serve_an_openapi_document_with_a_key(tmp_path, monkeypatch):
    apps, header = _apps(tmp_path, monkeypatch)
    assert set(apps) == {"collectors", "builder", "requester", "planner", "contracts"}
    for name, app in apps.items():
        client = TestClient(app)
        # The guard is in front of the document too: no key, no schema (decision 16).
        assert client.get("/openapi.json").status_code == 401, name
        assert client.get("/docs").status_code == 401, name
        response = client.get("/openapi.json", headers={"authorization": header})
        assert response.status_code == 200, name
        document = response.json()
        assert document["info"]["title"], name
        assert document["paths"], f"{name}: the document lists no route"
        # /healthz is mounted by the guard, not by the router, so the document never lists it.
        assert any(path.startswith("/v1/") for path in document["paths"]), name


def test_the_openapi_paths_are_the_routed_ones(tmp_path, monkeypatch):
    apps, header = _apps(tmp_path, monkeypatch)
    client = TestClient(apps["requester"])
    document = client.get("/openapi.json", headers={"authorization": header}).json()
    assert {"/v1/catalogue/search", "/v1/catalogue/questions", "/v1/catalogue/reindex"} <= set(
        document["paths"]
    )
