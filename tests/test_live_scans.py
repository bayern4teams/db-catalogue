"""A scan requested by the requester, accepted and run by the collector against real PostgreSQL,
through both approved routes: a signed request file handed over in a directory, and private HTTP
to the collector's `serve` API. Every step goes through the shipped commands.

Also checked against the real server: the requester refuses a scope it is not allowed, the
collector refuses a request whose signature does not verify, and a scan that cannot run ends as
`failed` with a reason rather than silently.

Opt-in like the other live tests (`DBIG_RUN_INTEGRATION=1` and `DBIG_TEST_CA_FILE`).
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("DBIG_RUN_INTEGRATION") != "1", reason="live source containers unavailable"
)

SCRIPTS = Path(sys.executable).parent
SIGNING_KEY = "synthetic-scan-signing-key-well-over-32-bytes"
COLLECTOR_TOKEN = "synthetic-collector-http-token"  # noqa: S105 - fixture token
NAMESPACE = "dbig_scans"
SOURCE = "scan-postgresql"


def _ca_file() -> Path:
    value = os.environ.get("DBIG_TEST_CA_FILE")
    if not value:
        pytest.fail("DBIG_TEST_CA_FILE must name the fixture CA when DBIG_RUN_INTEGRATION=1")
    return Path(value)


@pytest.fixture(scope="module", autouse=True)
def seeded() -> None:
    from pg8000 import dbapi

    connection = dbapi.connect(host="127.0.0.1", port=5432, user="postgres", database="postgres")
    try:
        cursor = connection.cursor()
        for statement in (
            f"DROP SCHEMA IF EXISTS {NAMESPACE} CASCADE",
            f"CREATE SCHEMA {NAMESPACE}",
            f"CREATE TABLE {NAMESPACE}.accounts (id integer PRIMARY KEY, owner text)",
            f"CREATE TABLE {NAMESPACE}.payments (id integer PRIMARY KEY, "
            f"account_id integer REFERENCES {NAMESPACE}.accounts(id))",
        ):
            cursor.execute(statement)
        connection.commit()
    finally:
        connection.close()


@pytest.fixture
def site(tmp_path: Path) -> dict:
    """One collector site and one requester, sharing the scan signing key as configured."""
    collector = tmp_path / "collector.toml"
    collector.write_text(
        "\n".join(
            [
                "[source]",
                f'source_id = "{SOURCE}"',
                'kind = "postgresql"',
                'connection_env = "DBIG_CONNECTION"',
                'signing_key_env = "DBIG_SIGNING_KEY"',
                'signing_key_id = "ci"',
                f'namespaces = ["{NAMESPACE}"]',
                f'tls_ca_file = "{_ca_file().as_posix()}"',
                'authorized_requesters = ["catalogue-requester"]',
                'http_token_env = "COLLECTOR_TOKEN"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    port = _free_port()
    handoff = tmp_path / "handoff"
    handoff.mkdir()
    requester = tmp_path / "requester.toml"
    requester.write_text(
        "\n".join(
            [
                "[requester]",
                f'snapshot_dir = "{(tmp_path / "snapshot").as_posix()}"',
                f'state_dir = "{(tmp_path / "requester-state").as_posix()}"',
                'api_key_env = "REQUESTER_TOKEN"',
                "[requester.scan]",
                'signing_key_env = "DBIG_SIGNING_KEY"',
                'key_id = "ci"',
                f'allowed_sources = {{ "{SOURCE}" = ["{NAMESPACE}"] }}',
                f'handoff_root = "{handoff.as_posix()}"',
                f'collector_urls = {{ "{SOURCE}" = "http://127.0.0.1:{port}" }}',
                'http_token_env = "COLLECTOR_TOKEN"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    env = {key: value for key, value in os.environ.items() if key != "SSL_CERT_FILE"}
    env.update(
        {
            "DBIG_CONNECTION": json.dumps(
                {"host": "127.0.0.1", "port": 5432, "database": "postgres", "user": "postgres"}
            ),
            "DBIG_SIGNING_KEY": SIGNING_KEY,
            "COLLECTOR_TOKEN": COLLECTOR_TOKEN,
        }
    )
    return {
        "collector": collector, "requester": requester, "env": env, "port": port,
        "handoff": handoff, "queue": tmp_path / "queue.sqlite3", "out": tmp_path / "scans",
        "root": tmp_path,
    }


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _request(site: dict, name: str, *, scopes: list[str] | None = None) -> Path:
    now = datetime.now(UTC)
    path = site["root"] / f"{name}.json"
    path.write_text(
        json.dumps(
            {
                "id": name,
                "source_id": SOURCE,
                "scopes": scopes or [NAMESPACE],
                "capabilities": ["metadata"],
                "reason": "live scan test",
                "requester_id": "catalogue-requester",
                "idempotency_key": f"key-{name}",
                "issued_at": (now - timedelta(seconds=5)).isoformat(),
                "expires_at": (now + timedelta(minutes=10)).isoformat(),
                "reply_location": "catalogue",
            }
        ),
        encoding="utf-8",
    )
    return path


def _run(site: dict, name: str, *arguments: str, ok: bool = True):
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [str(SCRIPTS / name), *arguments],
        capture_output=True, text=True, env=site["env"], timeout=120,
    )
    if ok:
        assert result.returncode == 0, f"{name} {arguments[0]}: {result.stderr}"
        return json.loads(result.stdout)
    return result


def _process(site: dict) -> dict:
    return _run(
        site, "dbig", "process-scans", "--config", str(site["collector"]),
        "--queue-db", str(site["queue"]), "--out-root", str(site["out"]),
    )


def _bundle_objects(path: str) -> set[str]:
    from dbig_contracts.artifact import read_artifact

    _, tables = read_artifact(Path(path), signing_key=SIGNING_KEY.encode())
    return {item.name for item in tables["objects.jsonl"]}


def test_a_scan_handed_over_as_a_signed_file_runs_on_the_real_server(site):
    receipt = _run(
        site, "dbig-requester", "request-scan", "--config", str(site["requester"]),
        "--request", str(_request(site, "file-1")), "--dispatch", "bundle",
    )
    assert receipt["status"] == "submitted"
    handed_over = site["handoff"] / "file-1" / "request.json"
    assert handed_over.is_file()
    queued = _run(
        site, "dbig", "accept-scan", "--config", str(site["collector"]),
        "--request", str(handed_over), "--queue-db", str(site["queue"]),
    )
    assert queued["status"] == "queued"
    done = _process(site)
    assert done["status"] == "complete", done
    assert _bundle_objects(done["bundle_path"]) == {"accounts", "payments"}
    assert _process(site) == {"status": "empty"}


def test_a_scan_sent_over_private_http_runs_on_the_real_server(site):
    server = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        [str(SCRIPTS / "dbig"), "serve", "--config", str(site["collector"]),
         "--queue-db", str(site["queue"]), "--host", "127.0.0.1", "--port", str(site["port"])],
        env=site["env"], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                socket.create_connection(("127.0.0.1", site["port"]), timeout=1).close()
                break
            except OSError:
                assert server.poll() is None, server.stderr.read().decode()
                assert time.monotonic() < deadline, "collector API did not start"
                time.sleep(0.3)
        receipt = _run(
            site, "dbig-requester", "request-scan", "--config", str(site["requester"]),
            "--request", str(_request(site, "http-1")), "--dispatch", "http",
        )
        assert receipt["status"] == "queued"
        done = _process(site)
        assert done["status"] == "complete", done
        assert _bundle_objects(done["bundle_path"]) == {"accounts", "payments"}
        status = urllib.request.Request(  # noqa: S310 - loopback fixture URL
            f"http://127.0.0.1:{site['port']}/v1/collector/scan-requests/http-1",
            headers={"Authorization": f"Bearer {COLLECTOR_TOKEN}"},
        )
        with urllib.request.urlopen(status, timeout=10) as response:  # noqa: S310
            assert json.loads(response.read())["status"] == "complete"
    finally:
        server.terminate()
        server.wait(timeout=10)


def test_the_requester_refuses_a_scope_it_is_not_allowed(site):
    result = _run(
        site, "dbig-requester", "request-scan", "--config", str(site["requester"]),
        "--request", str(_request(site, "scope-1", scopes=["pg_catalog"])),
        "--dispatch", "bundle", ok=False,
    )
    assert result.returncode != 0
    assert "UNAUTHORIZED_SCOPE" in result.stderr
    assert not (site["handoff"] / "scope-1").exists()


def test_the_collector_refuses_a_request_with_a_forged_signature(site):
    _run(
        site, "dbig-requester", "request-scan", "--config", str(site["requester"]),
        "--request", str(_request(site, "forged-1")), "--dispatch", "bundle",
    )
    handed_over = site["handoff"] / "forged-1" / "request.json"
    envelope = json.loads(handed_over.read_text(encoding="utf-8"))
    envelope["request"]["scopes"] = ["pg_catalog"]  # widened after signing
    handed_over.write_text(json.dumps(envelope), encoding="utf-8")
    result = _run(
        site, "dbig", "accept-scan", "--config", str(site["collector"]),
        "--request", str(handed_over), "--queue-db", str(site["queue"]), ok=False,
    )
    assert result.returncode != 0
    assert "invalid scan signature" in result.stderr
    assert _process(site) == {"status": "empty"}


def test_a_scan_that_cannot_run_fails_with_a_reason(site):
    _run(
        site, "dbig-requester", "request-scan", "--config", str(site["requester"]),
        "--request", str(_request(site, "fails-1")), "--dispatch", "bundle",
    )
    _run(
        site, "dbig", "accept-scan", "--config", str(site["collector"]),
        "--request", str(site["handoff"] / "fails-1" / "request.json"),
        "--queue-db", str(site["queue"]),
    )
    site["env"]["DBIG_CONNECTION"] = json.dumps(
        {"host": "127.0.0.1", "port": 5432, "database": "postgres", "user": "no_such_role"}
    )
    done = _process(site)
    assert done["status"] == "failed"
    assert done["bundle_path"] is None
    assert "the server refused the login" in done.get("error", ""), done
