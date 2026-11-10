"""Report Part 3: the service routes and the shared guard, through each app's own HTTP surface.

Per-caller keys (layer L5, owner ruling D4): a request without a key is 401, a key without the
route's scope is 403, and `GET /healthz` answers without a key. The flow (Part 3.5): the builder
assembles a snapshot as a job and points `current` at it, the requester reindexes, the planner
rebuilds its graph. Invented names only.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta

import pytest
from catalogue_fixture_kit import BUNDLE_KEY, write_bundle
from dbig_builder.api import create_app as builder_app
from dbig_builder.api import run_one
from dbig_builder.config import BuilderConfig, ServiceConfig
from dbig_contracts.api import create_app as contracts_app
from dbig_contracts.schemas import ADAPTERS
from dbig_planner.api import create_app as planner_app
from dbig_planner.config import PlannerConfig
from dbig_requester.api import create_app as requester_app
from dbig_requester.config import RequesterConfig
from fastapi.testclient import TestClient

TABLES = {"vendor": {"fields": [("id", "integer"), ("label", "text")], "key": ["id"]}}


def _keys(tmp_path, monkeypatch, **scopes_by_caller) -> dict[str, str]:
    """A caller key file with one key per caller; returns each caller's bearer header."""
    tokens = {caller: secrets.token_urlsafe(32) for caller in scopes_by_caller}
    expires = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    path = tmp_path / "keys.json"
    path.write_text(json.dumps({"keys": [
        {"caller": caller, "sha256": hashlib.sha256(tokens[caller].encode()).hexdigest(),
         "scopes": scopes, "expires_at": expires}
        for caller, scopes in scopes_by_caller.items()
    ]}), encoding="utf-8")
    monkeypatch.setenv("DBIG_CALLER_KEYS_FILE", str(path))
    return {caller: f"Bearer {token}" for caller, token in tokens.items()}


def test_health_needs_no_key_and_says_only_ok(tmp_path, monkeypatch):
    _keys(tmp_path, monkeypatch, reader=["validate"])
    client = TestClient(contracts_app(tmp_path / "keys.json"))
    response = client.get("/healthz")
    assert (response.status_code, response.json()) == (200, {"status": "ok"})


def test_no_key_is_401_and_a_key_without_the_scope_is_403(tmp_path, monkeypatch):
    headers = _keys(tmp_path, monkeypatch, reader=["validate"], other=["search"])
    client = TestClient(contracts_app(tmp_path / "keys.json"))
    assert client.get("/v1/contracts/schemas").status_code == 401
    wrong = client.get("/v1/contracts/schemas", headers={"authorization": headers["other"]})
    assert (wrong.status_code, wrong.json()) == (403, {"error": {"code": "FORBIDDEN_SCOPE"}})
    right = client.get("/v1/contracts/schemas", headers={"authorization": headers["reader"]})
    assert right.status_code == 200
    assert set(right.json()["schemas"]) == set(ADAPTERS)


def test_the_contracts_service_checks_a_record_without_echoing_it(tmp_path, monkeypatch):
    headers = {"authorization": _keys(tmp_path, monkeypatch, reader=["validate"])["reader"]}
    client = TestClient(contracts_app(tmp_path / "keys.json"))
    schema = client.get("/v1/contracts/schemas/edge", headers=headers)
    assert schema.status_code == 200 and schema.json()["title"] == "Edge"
    canary = "canary-value-7f3a"
    bad = client.post("/v1/contracts/validate/edge", headers=headers, json={"kind": canary})
    assert bad.status_code == 422
    assert bad.json()["detail"]["code"] == "INVALID_RECORD"
    assert canary not in bad.text
    unknown = client.post("/v1/contracts/validate/nothing", headers=headers, json={})
    assert unknown.status_code == 404


def _builder_config(tmp_path, monkeypatch) -> BuilderConfig:
    monkeypatch.setenv("BUNDLE_KEY", BUNDLE_KEY)
    write_bundle(tmp_path / "bundles" / "alpha-1", "alpha", "postgresql", TABLES)
    return BuilderConfig(
        signing_keys={"test": "BUNDLE_KEY"},
        service=ServiceConfig(
            bundles_root=tmp_path / "bundles",
            snapshots_root=tmp_path / "snapshots",
            jobs_db=tmp_path / "builder-jobs.sqlite3",
        ),
    )


def test_an_assembly_job_writes_a_snapshot_and_points_current_at_it(tmp_path, monkeypatch):
    headers = {"authorization": _keys(tmp_path, monkeypatch, pipeline=["assemble"])["pipeline"]}
    config = _builder_config(tmp_path, monkeypatch)
    client = TestClient(builder_app(config))
    started = client.post("/v1/builder/assemblies", headers=headers, json={"bundles": ["alpha-1"]})
    assert started.status_code == 202, started.text
    job = started.json()["id"]
    assert client.get(f"/v1/builder/assemblies/{job}", headers=headers).json()["status"] == "queued"
    assert run_one(config) is True
    done = client.get(f"/v1/builder/assemblies/{job}", headers=headers).json()
    assert done["status"] == "complete", done
    current = tmp_path / "snapshots" / "current"
    assert current.is_symlink() and current.resolve() == (tmp_path / "snapshots" / job).resolve()
    assert (current / "manifest.json").is_file()
    assert run_one(config) is False, "control: the queue is empty after one job"


@pytest.mark.parametrize("name", ["missing", "../bundles/alpha-1", "/etc", ".hidden"])
def test_an_assembly_names_only_a_bundle_under_the_bundles_root(tmp_path, monkeypatch, name):
    headers = {"authorization": _keys(tmp_path, monkeypatch, pipeline=["assemble"])["pipeline"]}
    client = TestClient(builder_app(_builder_config(tmp_path, monkeypatch)))
    response = client.post("/v1/builder/assemblies", headers=headers, json={"bundles": [name]})
    assert (response.status_code, response.json()["detail"]) == (404, {"code": "BUNDLE_NOT_FOUND"})


def test_reindex_and_graph_rebuild_follow_the_new_snapshot(tmp_path, monkeypatch):
    headers = _keys(tmp_path, monkeypatch, pipeline=["assemble", "reindex", "search", "graph"])
    auth = {"authorization": headers["pipeline"]}
    config = _builder_config(tmp_path, monkeypatch)
    builder = TestClient(builder_app(config))
    builder.post("/v1/builder/assemblies", headers=auth, json={"bundles": ["alpha-1"]})
    assert run_one(config)
    current = tmp_path / "snapshots" / "current"
    requester = TestClient(requester_app(RequesterConfig(
        snapshot_dir=current, state_dir=tmp_path / "requester", api_key_env="UNUSED_TOKEN",
    )))
    reindexed = requester.post("/v1/catalogue/reindex", headers=auth)
    assert reindexed.status_code == 200, reindexed.text
    found = requester.post("/v1/catalogue/search", headers=auth, json={"query": "vendor"})
    assert any(item["name"] == "vendor" for item in found.json()["results"])
    observations = tmp_path / "observations"
    observations.mkdir()
    planner = TestClient(planner_app(PlannerConfig(
        graph_dir=tmp_path / "graph", snapshot_dir=current, observations_path=observations,
    )))
    built = planner.post("/v1/planner/graph", headers=auth)
    assert built.status_code == 200, built.text
    assert built.json()["snapshot_digest"] == reindexed.json()["snapshot_digest"]


def test_the_rate_limit_and_body_limit_hold_for_every_service(tmp_path, monkeypatch):
    headers = _keys(tmp_path, monkeypatch, pipeline=["plan"])
    auth = {"authorization": headers["pipeline"]}
    client = TestClient(planner_app(PlannerConfig(
        graph_dir=tmp_path / "graph", max_request_bytes=64, max_requests_per_minute=2,
    )))
    too_big = client.post("/v1/plans", headers=auth, content=b"x" * 65)
    assert too_big.status_code == 413
    codes = [client.post("/v1/plans", headers=auth, json={}).status_code for _ in range(3)]
    assert codes[-1] == 429, codes
