import json
from datetime import UTC, datetime, timedelta

import pytest
from dbig_collectors.api import create_app as collector_app
from dbig_collectors.config import SourceConfig
from dbig_collectors.scan import accept_scan, process_next
from dbig_contracts.scan import ScanReceipt, ScanRequest, SignedScanRequest, sign_scan
from dbig_requester.config import ScanConfig
from dbig_requester.scan import dispatch_scan
from fastapi.testclient import TestClient

KEY = "synthetic-key-with-more-than-thirty-two-bytes"


def request(request_id="request-1", scopes=None):
    now = datetime.now(UTC)
    return ScanRequest(
        id=request_id,
        source_id="site-a",
        scopes=scopes or ["public"],
        capabilities=["metadata"],
        reason="refresh catalog",
        requester_id="catalogue",
        idempotency_key="idem-1",
        issued_at=now - timedelta(seconds=1),
        expires_at=now + timedelta(minutes=10),
        reply_location="local-receipt",
    )


def site_config():
    return SourceConfig(
        source_id="site-a",
        kind="postgresql",
        connection_env="DBIG_CONNECTION",
        signing_key_env="DBIG_SIGNING_KEY",
        signing_key_id="test",
        namespaces=["public"],
        authorized_requesters=["catalogue"],
        http_token_env="SITE_TOKEN",  # noqa: S106 - environment variable name
    )


def test_scheduler_bundle_authorization_replay_and_local_queue(tmp_path, monkeypatch):
    monkeypatch.setenv("DBIG_SIGNING_KEY", KEY)
    handoff = tmp_path / "handoff"
    handoff.mkdir()
    config = ScanConfig(
        signing_key_env="DBIG_SIGNING_KEY",
        key_id="test",
        allowed_sources={"site-a": ["public"]},
        handoff_root=handoff,
    )
    first_request = request()
    submitted = dispatch_scan(first_request, config, tmp_path / "requester-state", "bundle")
    assert submitted.status == "submitted"
    assert dispatch_scan(first_request, config, tmp_path / "requester-state", "bundle") == submitted
    envelope = SignedScanRequest.model_validate_json(
        (handoff / "request-1" / "request.json").read_bytes()
    )
    queued = accept_scan(envelope, site_config(), tmp_path / "site-queue.sqlite3")
    assert queued.status == "queued"
    assert accept_scan(envelope, site_config(), tmp_path / "site-queue.sqlite3") == queued
    changed = sign_scan(request("request-2"), "test", KEY.encode())
    with pytest.raises(ValueError, match="REPLAY_CONFLICT"):
        accept_scan(changed, site_config(), tmp_path / "site-queue.sqlite3")
    with pytest.raises(ValueError, match="UNAUTHORIZED_SCOPE"):
        dispatch_scan(request(scopes=["private"]), config, tmp_path / "requester-state", "bundle")
    monkeypatch.setattr("dbig_collectors.scan.collect", lambda config, path: None)
    done = process_next(site_config(), tmp_path / "site-queue.sqlite3", tmp_path / "out")
    assert done.status == "complete"


def test_collector_http_auth_and_signature(tmp_path, monkeypatch):
    monkeypatch.setenv("DBIG_SIGNING_KEY", KEY)
    monkeypatch.setenv("SITE_TOKEN", "synthetic-bearer")
    app = collector_app(site_config(), tmp_path / "queue.sqlite3")
    envelope = sign_scan(request(), "test", KEY.encode())
    with TestClient(app) as client:
        assert (
            client.post(
                "/v1/collector/scan-requests", json=envelope.model_dump(mode="json")
            ).status_code
            == 401
        )
        response = client.post(
            "/v1/collector/scan-requests",
            json=envelope.model_dump(mode="json"),
            headers={"Authorization": "Bearer synthetic-bearer"},
        )
        assert response.status_code == 200 and response.json()["status"] == "queued"
        tampered = envelope.model_dump(mode="json")
        tampered["signature"] = "0" * 64
        assert (
            client.post(
                "/v1/collector/scan-requests",
                json=tampered,
                headers={"Authorization": "Bearer synthetic-bearer"},
            ).status_code
            == 403
        )


def test_collector_http_requires_secret_at_startup_and_enforces_body_limit(
    tmp_path, monkeypatch
):
    config = SourceConfig.model_validate(
        {**site_config().model_dump(), "max_request_bytes": 16}
    )
    monkeypatch.delenv("SITE_TOKEN", raising=False)
    with pytest.raises(ValueError, match="HTTP token reference is required"):
        collector_app(config, tmp_path / "queue.sqlite3")
    monkeypatch.setenv("SITE_TOKEN", " ")
    with pytest.raises(ValueError, match="HTTP token reference is required"):
        collector_app(config, tmp_path / "queue.sqlite3")
    monkeypatch.setenv("SITE_TOKEN", "synthetic-bearer")
    with TestClient(collector_app(config, tmp_path / "queue.sqlite3")) as client:
        response = client.post(
            "/v1/collector/scan-requests",
            json={"request": "larger than sixteen bytes"},
            headers={"Authorization": "Bearer synthetic-bearer"},
        )
        assert response.status_code == 413


def test_private_http_dispatch_uses_signed_body(tmp_path, monkeypatch):
    monkeypatch.setenv("DBIG_SIGNING_KEY", KEY)
    monkeypatch.setenv("SITE_TOKEN", "synthetic-bearer")
    calls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, limit):
            return (
                ScanReceipt(request_id="request-1", status="queued", received_at=datetime.now(UTC))
                .model_dump_json()
                .encode()
            )

    class Opener:
        def open(self, message, timeout):
            calls.append(message)
            return Response()

    monkeypatch.setattr("dbig_requester.scan.build_opener", lambda *args: Opener())
    config = ScanConfig(
        signing_key_env="DBIG_SIGNING_KEY",
        key_id="test",
        allowed_sources={"site-a": ["public"]},
        collector_urls={"site-a": "https://10.0.0.2"},
        http_token_env="SITE_TOKEN",  # noqa: S106 - environment variable name
    )
    receipt = dispatch_scan(request(), config, tmp_path / "state", "http")
    assert receipt.status == "queued"
    assert calls[0].full_url == "https://10.0.0.2/v1/collector/scan-requests"
    assert json.loads(calls[0].data)["signature"]
