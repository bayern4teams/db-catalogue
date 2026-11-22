"""One collector serves any number of databases (owner 2026-11-22, answer B1).

`deploy/sources.toml` lists them as `[[source]]` tables with shared `[defaults]`; each login is
its own file (`connection_file`). The collector routes a scan request to the source it names,
and the requester takes its allowed sources and namespaces from the same file.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from dbig_collectors.config import load_sources
from dbig_collectors.scan import accept_scan, process_next
from dbig_contracts.errors import OperatorError
from dbig_contracts.scan import ScanRequest, sign_scan
from dbig_requester.config import ScanConfig

KEY = "synthetic-key-with-more-than-thirty-two-bytes"

SOURCES = """
[defaults]
signing_key_env = "DBIG_SIGNING_KEY"
signing_key_id = "local"
authorized_requesters = ["catalogue"]
http_token_env = "DBIG_COLLECTOR_TOKEN"

[[source]]
source_id = "diffbot-pg"
kind = "postgresql"
connection_file = "{secrets}/diffbot-pg.json"
namespaces = ["public", "sales"]
tls = false

[[source]]
source_id = "zoominfo-ch"
kind = "clickhouse"
connection_file = "{secrets}/zoominfo-ch.json"
namespaces = ["default"]
"""


def _write(tmp_path, text=SOURCES):
    secrets = tmp_path / "secrets"
    secrets.mkdir(exist_ok=True)
    (secrets / "diffbot-pg.json").write_text('{"host": "10.0.0.5"}', encoding="utf-8")
    (secrets / "zoominfo-ch.json").write_text('{"host": "10.0.0.6"}', encoding="utf-8")
    path = tmp_path / "sources.toml"
    path.write_text(text.replace("{secrets}", str(secrets)), encoding="utf-8")
    return path


def _request(source_id, scopes, request_id="r1"):
    now = datetime.now(UTC)
    return ScanRequest(
        id=request_id, source_id=source_id, scopes=scopes, capabilities=["metadata"],
        reason="refresh", requester_id="catalogue", idempotency_key=f"idem-{request_id}",
        issued_at=now - timedelta(seconds=1), expires_at=now + timedelta(minutes=10),
        reply_location="local-receipt",
    )


def test_every_source_is_read_with_the_shared_defaults(tmp_path):
    sources = load_sources(_write(tmp_path))
    assert [s.source_id for s in sources] == ["diffbot-pg", "zoominfo-ch"]
    assert sources[0].namespaces == ["public", "sales"]
    assert sources[0].tls is False and sources[1].tls is True
    assert {s.signing_key_id for s in sources} == {"local"}
    assert all(s.authorized_requesters == ["catalogue"] for s in sources)


def test_a_duplicate_source_id_is_refused_by_name(tmp_path):
    doubled = SOURCES.replace('source_id = "zoominfo-ch"', 'source_id = "diffbot-pg"')
    with pytest.raises(OperatorError, match="diffbot-pg"):
        load_sources(_write(tmp_path, doubled))


def test_a_source_needs_exactly_one_login_reference(tmp_path):
    both = SOURCES.replace(
        'kind = "postgresql"\n', 'kind = "postgresql"\nconnection_env = "DBIG_X"\n'
    )
    with pytest.raises(OperatorError, match="connection_env or connection_file"):
        load_sources(_write(tmp_path, both))


def test_an_empty_list_is_refused(tmp_path):
    with pytest.raises(OperatorError, match=r"\[\[source\]\]"):
        load_sources(_write(tmp_path, '[defaults]\nsigning_key_id = "local"\n'))


def test_a_scan_is_queued_for_and_run_on_the_source_it_names(tmp_path, monkeypatch):
    monkeypatch.setenv("DBIG_SIGNING_KEY", KEY)
    sources = load_sources(_write(tmp_path))
    queue = tmp_path / "queue.sqlite3"
    envelope = sign_scan(_request("zoominfo-ch", ["default"]), "local", KEY.encode())
    assert accept_scan(envelope, sources, queue).status == "queued"
    seen = []
    monkeypatch.setattr(
        "dbig_collectors.scan.collect", lambda config, path: seen.append(config.source_id)
    )
    assert process_next(sources, queue, tmp_path / "out").status == "complete"
    assert seen == ["zoominfo-ch"]


def test_a_scan_for_an_unlisted_source_or_namespace_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("DBIG_SIGNING_KEY", KEY)
    sources = load_sources(_write(tmp_path))
    queue = tmp_path / "queue.sqlite3"
    unknown = sign_scan(_request("other-db", ["public"]), "local", KEY.encode())
    with pytest.raises(ValueError, match="UNAUTHORIZED_REQUESTER_OR_SOURCE"):
        accept_scan(unknown, sources, queue)
    wrong_scope = sign_scan(_request("zoominfo-ch", ["public"], "r2"), "local", KEY.encode())
    with pytest.raises(ValueError, match="UNAUTHORIZED_SCOPE"):
        accept_scan(wrong_scope, sources, queue)


def test_the_login_is_read_from_its_own_file(tmp_path):
    from dbig_collectors.runtime import connection_settings

    sources = load_sources(_write(tmp_path))
    assert json.loads(connection_settings(sources[0])) == {"host": "10.0.0.5"}


def test_the_requester_takes_sources_and_namespaces_from_the_same_file(tmp_path, monkeypatch):
    monkeypatch.setenv("DBIG_ALLOW_PLAIN_LAN", "1")  # plain HTTP to the collector (decision 14)
    config = ScanConfig(
        signing_key_env="DBIG_SIGNING_KEY", key_id="local",
        sources_file=_write(tmp_path), collector_url="http://10.0.0.10:8443",
    )
    assert config.allowed_sources == {
        "diffbot-pg": ["public", "sales"], "zoominfo-ch": ["default"],
    }
    assert config.collector_urls == {
        "diffbot-pg": "http://10.0.0.10:8443", "zoominfo-ch": "http://10.0.0.10:8443",
    }


def test_the_committed_template_loads():
    """deploy/sources.example.toml is what an operator copies; it must stay valid."""
    from pathlib import Path

    template = Path(__file__).parents[1] / "deploy" / "sources.example.toml"
    sources = load_sources(template)
    assert {s.kind for s in sources} == {"postgresql", "clickhouse", "mongodb"}
    assert all(s.tls is False and s.connection_file is not None for s in sources)


def _http_config(monkeypatch):
    monkeypatch.setenv("DBIG_SIGNING_KEY", KEY)
    monkeypatch.setenv("SITE_TOKEN", "synthetic-bearer")
    return ScanConfig(
        signing_key_env="DBIG_SIGNING_KEY", key_id="local",
        allowed_sources={"zoominfo-ch": ["default"]},
        collector_urls={"zoominfo-ch": "https://10.0.0.2"},
        http_token_env="SITE_TOKEN",  # noqa: S106 # C5: reason=environment variable name; approved=user, 2026-11-08; review=2027-05-08
    )


@pytest.mark.parametrize(
    ("status", "body", "code"),
    [
        (401, b"Unauthorized", "COLLECTOR_REFUSED_401"),
        (403, b'{"detail": {"code": "UNAUTHORIZED_SCOPE"}}', "UNAUTHORIZED_SCOPE"),
    ],
)
def test_a_collector_refusal_is_reported_with_its_reason(tmp_path, monkeypatch, status, body, code):
    """Laptop rehearsal 2026-11-22: a 401 from the collector surfaced as a bare 500."""
    import io
    from urllib.error import HTTPError

    from dbig_requester.scan import CollectorUnavailable, dispatch_scan

    class Opener:
        def open(self, message, timeout):
            raise HTTPError(message.full_url, status, "refused", {}, io.BytesIO(body))

    monkeypatch.setattr("dbig_requester.scan.build_opener", lambda *args: Opener())
    with pytest.raises(CollectorUnavailable, match=code):
        dispatch_scan(_request("zoominfo-ch", ["default"]), _http_config(monkeypatch),
                      tmp_path / "state", "http")


def test_an_unreachable_collector_is_reported(tmp_path, monkeypatch):
    from urllib.error import URLError

    from dbig_requester.scan import CollectorUnavailable, dispatch_scan

    class Opener:
        def open(self, message, timeout):
            raise URLError("connection refused")

    monkeypatch.setattr("dbig_requester.scan.build_opener", lambda *args: Opener())
    with pytest.raises(CollectorUnavailable, match="COLLECTOR_UNREACHABLE"):
        dispatch_scan(_request("zoominfo-ch", ["default"]), _http_config(monkeypatch),
                      tmp_path / "state", "http")
