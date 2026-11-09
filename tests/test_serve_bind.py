"""A5: each `serve` command, run as a process, binds a port and answers one authenticated request.

`create_app` and the bind refusal are tested apart elsewhere; this is the one served request."""

import os
import socket
import subprocess
import sys
import time

import httpx
import pytest
from dbig_contracts.artifact import write_artifact

BEARER = "synthetic-api-token"
TABLES = {"nodes.jsonl": [], "edges.jsonl": [], "evidence.jsonl": [], "runs.jsonl": []}


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _requester(tmp_path):
    snapshot = tmp_path / "snapshot"
    write_artifact(snapshot, "catalog-snapshot", TABLES, producer="test/1", source_run_ids=[])
    config = tmp_path / "requester.toml"
    config.write_text(
        f'[requester]\nsnapshot_dir = "{snapshot}"\nstate_dir = "{tmp_path / "state"}"\n'
        'api_key_env = "SERVE_TOKEN"\n',
        encoding="utf-8",
    )
    from dbig_requester.config import load_config
    from dbig_requester.search import index_snapshot

    loaded = load_config(config)
    index_snapshot(loaded.snapshot_dir, loaded.state_dir)
    return config, ("GET", "/v1/catalogue/search", {"params": {"query": "x"}}), 200


def _planner(tmp_path):
    config = tmp_path / "planner.toml"
    config.write_text(
        f'[planner]\ngraph_dir = "{tmp_path / "graph"}"\napi_key_env = "SERVE_TOKEN"\n',
        encoding="utf-8",
    )
    # An empty body passes the bearer check and is refused by the route's own validation.
    return config, ("POST", "/v1/join-paths", {"json": {}}), 422


@pytest.mark.parametrize(
    ("package", "setup"), [("dbig_requester", _requester), ("dbig_planner", _planner)]
)
def test_serve_binds_and_answers_one_authenticated_request(tmp_path, package, setup):
    config, (method, path, extra), expected = setup(tmp_path)
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    process = subprocess.Popen(  # noqa: S603 # C5: reason=fixed argv of this interpreter, no shell; approved=lead session for the owner, 2026-11-09; review=2027-05-09
        [
            sys.executable, "-c", f"from {package}.cli import main; main()",
            "serve", "--config", str(config), "--host", "127.0.0.1", "--port", str(port),
        ],
        env={**os.environ, "SERVE_TOKEN": BEARER},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                refused = httpx.request(method, base + path, **extra)
                break
            except httpx.TransportError:
                assert process.poll() is None, process.stderr.read().decode()
                assert time.monotonic() < deadline, "the server never bound its port"
                time.sleep(0.2)
        assert refused.status_code == 401
        reply = httpx.request(
            method, base + path, headers={"Authorization": f"Bearer {BEARER}"}, **extra
        )
        assert reply.status_code == expected, reply.text
        assert reply.json()
    finally:
        process.terminate()
        process.wait(timeout=10)
        process.stderr.close()
