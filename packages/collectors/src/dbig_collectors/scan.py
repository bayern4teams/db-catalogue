"""Collector-side authorization, replay defense, and local scan queue."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from dbig_contracts.scan import ScanReceipt, ScanRequest, SignedScanRequest, verify_scan
from dbig_contracts.security import secret_from_env

from .config import SourceConfig
from .runtime import collect


def _open_queue(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS scans("
        "id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL, "
        "signature TEXT NOT NULL, request TEXT NOT NULL, status TEXT NOT NULL, "
        "received_at TEXT NOT NULL, bundle_path TEXT)"
    )
    connection.commit()
    return connection


def accept_scan(envelope: SignedScanRequest, config: SourceConfig, queue_path: Path) -> ScanReceipt:
    if envelope.key_id != config.signing_key_id:
        raise ValueError("UNKNOWN_SIGNING_KEY")
    verify_scan(
        envelope,
        secret_from_env(
            config.signing_key_env, purpose="scan request signature verification"
        ).encode("utf-8"),
    )
    request = envelope.request
    if request.expires_at.timestamp() - request.issued_at.timestamp() > (
        config.max_request_ttl_seconds
    ):
        raise ValueError("REQUEST_TTL_EXCEEDED")
    if (
        request.source_id != config.source_id
        or request.requester_id not in config.authorized_requesters
    ):
        raise ValueError("UNAUTHORIZED_REQUESTER_OR_SOURCE")
    if not set(request.scopes) <= set(config.namespaces):
        raise ValueError("UNAUTHORIZED_SCOPE")
    allowed = {"metadata"}
    if config.sample_rows:
        allowed.add("sample")
    if config.history_access:
        allowed.add("history")
    if not set(request.capabilities) <= allowed:
        raise ValueError("UNAUTHORIZED_CAPABILITY")
    with closing(_open_queue(queue_path)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT id,signature,status,received_at,bundle_path FROM scans WHERE idempotency_key=?",
            (request.idempotency_key,),
        ).fetchone()
        if row:
            if row[0] != request.id or row[1] != envelope.signature:
                raise ValueError("REPLAY_CONFLICT")
            return ScanReceipt(
                request_id=row[0],
                status=row[2],
                received_at=datetime.fromisoformat(row[3]),
                bundle_path=row[4],
            )
        count = connection.execute(
            "SELECT count(*) FROM scans WHERE status IN ('queued','running')"
        ).fetchone()[0]
        if count >= config.max_queued_scans:
            raise ValueError("COLLECTOR_QUEUE_FULL")
        now = datetime.now(UTC)
        connection.execute(
            "INSERT INTO scans VALUES (?,?,?,?,?,?,?)",
            (
                request.id,
                request.idempotency_key,
                envelope.signature,
                request.model_dump_json(),
                "queued",
                now.isoformat(),
                None,
            ),
        )
        connection.commit()
    return ScanReceipt(request_id=request.id, status="queued", received_at=now)


def scan_status(queue_path: Path, request_id: str) -> ScanReceipt | None:
    with closing(_open_queue(queue_path)) as connection:
        row = connection.execute(
            "SELECT id,status,received_at,bundle_path FROM scans WHERE id=?", (request_id,)
        ).fetchone()
    return (
        ScanReceipt(
            request_id=row[0],
            status=row[1],
            received_at=datetime.fromisoformat(row[2]),
            bundle_path=row[3],
        )
        if row
        else None
    )


def process_next(config: SourceConfig, queue_path: Path, output_root: Path) -> ScanReceipt | None:
    with closing(_open_queue(queue_path)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT id,request,received_at FROM scans WHERE status='queued' "
            "ORDER BY received_at,id LIMIT 1"
        ).fetchone()
        if row is None:
            connection.rollback()
            return None
        request = ScanRequest.model_validate_json(row[1])
        if request.expires_at <= datetime.now(UTC):
            connection.execute("UPDATE scans SET status='failed' WHERE id=?", (request.id,))
            connection.commit()
            return scan_status(queue_path, request.id)
        connection.execute("UPDATE scans SET status='running' WHERE id=?", (request.id,))
        connection.commit()
    destination = output_root.resolve() / request.id
    local_config = config.model_copy(
        update={
            "namespaces": request.scopes,
            "sample_rows": config.sample_rows if "sample" in request.capabilities else 0,
            "history_access": config.history_access and "history" in request.capabilities,
        }
    )
    try:
        collect(local_config, destination)
        status = "complete"
    except Exception:
        status = "failed"
    with closing(_open_queue(queue_path)) as connection:
        connection.execute(
            "UPDATE scans SET status=?,bundle_path=? WHERE id=?",
            (status, str(destination) if status == "complete" else None, request.id),
        )
        connection.commit()
    return scan_status(queue_path, request.id)
