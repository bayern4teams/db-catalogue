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


def test_serve_over_tls_with_a_caller_key_file(tmp_path):
    """Report Part 3 (owner D1 and D4): `serve --tls-cert --tls-key` answers HTTPS that verifies
    against the private CA, `/healthz` needs no key, and a key from the mounted key file is
    accepted. Plain HTTP on the same port gets no answer."""
    import hashlib
    import importlib.util
    import json
    import ssl
    from datetime import UTC, datetime, timedelta
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "make_ci_certs", Path(__file__).parents[1] / "scripts" / "make_ci_certs.py"
    )
    certs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(certs)
    made = certs.generate(tmp_path / "certs")
    keys = tmp_path / "keys.json"
    keys.write_text(json.dumps({"keys": [{
        "caller": "pipeline", "sha256": hashlib.sha256(BEARER.encode()).hexdigest(),
        "scopes": ["search"], "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
    }]}), encoding="utf-8")
    config, (method, path, extra), expected = _requester(tmp_path)
    port = _free_port()
    process = subprocess.Popen(  # noqa: S603 # C5: reason=fixed argv of this interpreter, no shell; approved=owner, 2026-11-12; review=2027-05-12
        [
            sys.executable, "-c", "from dbig_requester.cli import main; main()",
            "serve", "--config", str(config), "--host", "127.0.0.1", "--port", str(port),
            "--tls-cert", str(made["cert"]), "--tls-key", str(made["key"]),
        ],
        env={**os.environ, "DBIG_CALLER_KEYS_FILE": str(keys)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    secure = httpx.Client(verify=ssl.create_default_context(cafile=made["ca"]), base_url=f"https://127.0.0.1:{port}")
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                health = secure.get("/healthz")
                break
            except httpx.TransportError:
                assert process.poll() is None, process.stderr.read().decode()
                assert time.monotonic() < deadline, "the server never bound its port"
                time.sleep(0.2)
        assert (health.status_code, health.json()) == (200, {"status": "ok"})
        assert secure.request(method, path, **extra).status_code == 401
        reply = secure.request(method, path, headers={"Authorization": f"Bearer {BEARER}"}, **extra)
        assert reply.status_code == expected, reply.text
        with pytest.raises(httpx.TransportError):
            httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=5)
    finally:
        secure.close()
        process.terminate()
        process.wait(timeout=10)
        process.stderr.close()
