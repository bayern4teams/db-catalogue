"""Signed scan dispatch via scheduler directory or private collector HTTP."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import HTTPRedirectHandler, Request, build_opener

from dbig_contracts.artifact import canonical
from dbig_contracts.errors import OperatorError
from dbig_contracts.scan import ScanReceipt, ScanRequest, sign_scan
from dbig_contracts.security import secret_from_env

from .config import ScanConfig


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _receipt_db(state: Path) -> Path:
    state.mkdir(parents=True, exist_ok=True)
    return state / "scan-receipts.sqlite3"


def _store(state: Path, receipt: ScanReceipt) -> None:
    with closing(sqlite3.connect(_receipt_db(state))) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY, receipt TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT OR REPLACE INTO receipts VALUES (?,?)",
            (receipt.request_id, receipt.model_dump_json()),
        )
        connection.commit()


def scan_status(state: Path, request_id: str) -> ScanReceipt | None:
    with closing(sqlite3.connect(_receipt_db(state))) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY, receipt TEXT NOT NULL)"
        )
        row = connection.execute(
            "SELECT receipt FROM receipts WHERE id=?", (request_id,)
        ).fetchone()
    return ScanReceipt.model_validate_json(row[0]) if row else None


def dispatch_scan(
    request: ScanRequest, config: ScanConfig, state: Path, adapter: str
) -> ScanReceipt:
    allowed_scopes = config.allowed_sources.get(request.source_id)
    if allowed_scopes is None or not set(request.scopes) <= set(allowed_scopes):
        raise OperatorError("UNAUTHORIZED_SCOPE")
    if not set(request.capabilities) <= set(config.allowed_capabilities):
        raise OperatorError("UNAUTHORIZED_CAPABILITY")
    if request.expires_at <= datetime.now(UTC):
        raise OperatorError("EXPIRED_REQUEST")
    key = secret_from_env(
        config.signing_key_env, purpose="scan request signing"
    ).encode("utf-8")
    envelope = sign_scan(request, config.key_id, key)
    payload = canonical(envelope)
    previous = scan_status(state, request.id)
    if previous is not None:
        if adapter == "bundle" and config.handoff_root is not None:
            existing = config.handoff_root / request.id / "request.json"
            if existing.is_file() and existing.read_bytes() == payload:
                return previous
        raise OperatorError("REPLAY_CONFLICT")
    if adapter == "bundle":
        if config.handoff_root is None:
            raise OperatorError("scheduler handoff is not configured")
        root = config.handoff_root.resolve(strict=True)
        destination = root / request.id
        if destination.exists():
            raise OperatorError("scan request already submitted")
        temporary = Path(tempfile.mkdtemp(prefix=".scan-", dir=root))
        try:
            with (temporary / "request.json").open("wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.rename(temporary, destination)
        except BaseException:
            (temporary / "request.json").unlink(missing_ok=True)
            temporary.rmdir()
            raise
        receipt = ScanReceipt(
            request_id=request.id, status="submitted", received_at=datetime.now(UTC)
        )
    elif adapter == "http":
        base = config.collector_urls.get(request.source_id)
        if base is None or config.http_token_env is None:
            raise OperatorError("collector HTTP route is not configured")
        token = secret_from_env(
            config.http_token_env, purpose="collector HTTP bearer token"
        )
        message = Request(  # noqa: S310 # C5: reason=URL is validated as a private HTTP(S) address; approved=user, 2026-11-08; review=2027-05-08
            base + "/v1/collector/scan-requests",
            data=payload,
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + token},
            method="POST",
        )
        with build_opener(_NoRedirect).open(
            message, timeout=config.http_timeout_seconds
        ) as response:
            receipt = ScanReceipt.model_validate_json(response.read(65_536))
        if receipt.request_id != request.id:
            raise OperatorError("mismatched collector receipt")
    else:
        raise OperatorError("unknown scan adapter")
    _store(state, receipt)
    return receipt
