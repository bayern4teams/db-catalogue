"""Collector-side authorization, replay defense, and local scan queue."""

from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from dbig_contracts.errors import OperatorError
from dbig_contracts.jobs import open_queue
from dbig_contracts.scan import ScanReceipt, ScanRequest, SignedScanRequest, verify_scan
from dbig_contracts.security import key_from_env

from .config import SourceConfig
from .runtime import collect


def _open_queue(path: Path) -> sqlite3.Connection:
    # WAL and a busy timeout: the HTTP routes read status while the worker thread writes.
    connection = open_queue(
        path,
        "CREATE TABLE IF NOT EXISTS scans("
        "id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL, "
        "signature TEXT NOT NULL, request TEXT NOT NULL, status TEXT NOT NULL, "
        "received_at TEXT NOT NULL, bundle_path TEXT, error TEXT)",
    )
    # A queue created before failure reasons were recorded gains the column in place.
    columns = {row[1] for row in connection.execute("PRAGMA table_info(scans)")}
    if "error" not in columns:
        connection.execute("ALTER TABLE scans ADD COLUMN error TEXT")
    connection.commit()
    return connection


def accept_scan(envelope: SignedScanRequest, config: SourceConfig, queue_path: Path) -> ScanReceipt:
    if envelope.key_id != config.signing_key_id:
        raise OperatorError("UNKNOWN_SIGNING_KEY")
    verify_scan(
        envelope,
        key_from_env(
            config.signing_key_env, purpose="scan request signature verification"
        ),
    )
    request = envelope.request
    if request.expires_at.timestamp() - request.issued_at.timestamp() > (
        config.max_request_ttl_seconds
    ):
        raise OperatorError("REQUEST_TTL_EXCEEDED")
    if (
        request.source_id != config.source_id
        or request.requester_id not in config.authorized_requesters
    ):
        raise OperatorError("UNAUTHORIZED_REQUESTER_OR_SOURCE")
    if not set(request.scopes) <= set(config.namespaces):
        raise OperatorError("UNAUTHORIZED_SCOPE")
    allowed = {"metadata"}
    if config.sample_rows or config.cardinality_probe or config.format_profiles:
        allowed.add("sample")
    if config.history_access:
        allowed.add("history")
    if not set(request.capabilities) <= allowed:
        raise OperatorError("UNAUTHORIZED_CAPABILITY")
    with closing(_open_queue(queue_path)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT id,signature,status,received_at,bundle_path,error FROM scans "
            "WHERE idempotency_key=?",
            (request.idempotency_key,),
        ).fetchone()
        if row:
            if row[0] != request.id or row[1] != envelope.signature:
                raise OperatorError("REPLAY_CONFLICT")
            return ScanReceipt(
                request_id=row[0],
                status=row[2],
                received_at=datetime.fromisoformat(row[3]),
                bundle_path=row[4],
                error=row[5],
            )
        count = connection.execute(
            "SELECT count(*) FROM scans WHERE status IN ('queued','running')"
        ).fetchone()[0]
        if count >= config.max_queued_scans:
            raise OperatorError("COLLECTOR_QUEUE_FULL")
        now = datetime.now(UTC)
        connection.execute(
            "INSERT INTO scans (id,idempotency_key,signature,request,status,received_at,"
            "bundle_path) VALUES (?,?,?,?,?,?,?)",
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
            "SELECT id,status,received_at,bundle_path,error FROM scans WHERE id=?",
            (request_id,),
        ).fetchone()
    return (
        ScanReceipt(
            request_id=row[0],
            status=row[1],
            received_at=datetime.fromisoformat(row[2]),
            bundle_path=row[3],
            error=row[4],
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
            connection.execute(
                "UPDATE scans SET status='failed',error=? WHERE id=?",
                ("EXPIRED_REQUEST: the request expired before it was processed", request.id),
            )
            connection.commit()
            return scan_status(queue_path, request.id)
        connection.execute("UPDATE scans SET status='running' WHERE id=?", (request.id,))
        connection.commit()
    destination = output_root.resolve() / request.id
    local_config = config.model_copy(
        update={
            "namespaces": request.scopes,
            "sample_rows": config.sample_rows if "sample" in request.capabilities else 0,
            "cardinality_probe": config.cardinality_probe and "sample" in request.capabilities,
            "format_profiles": config.format_profiles and "sample" in request.capabilities,
            "history_access": config.history_access and "history" in request.capabilities,
        }
    )
    error: str | None = None
    try:
        collect(local_config, destination)
        status = "complete"
    except OperatorError as failure:
        status, error = "failed", str(failure)[:1024]
    except Exception as failure:
        status, error = "failed", type(failure).__name__
    with closing(_open_queue(queue_path)) as connection:
        connection.execute(
            "UPDATE scans SET status=?,bundle_path=?,error=? WHERE id=?",
            (status, str(destination) if status == "complete" else None, error, request.id),
        )
        connection.commit()
    return scan_status(queue_path, request.id)


def requeue_running(queue_path: Path) -> int:
    """Put back scans left `running` by a crash. Only for start-up of the one worker that owns
    this queue file (report Part 3: the collector runs one worker per container)."""
    with closing(_open_queue(queue_path)) as connection:
        cursor = connection.execute("UPDATE scans SET status='queued' WHERE status='running'")
        connection.commit()
    return cursor.rowcount


def run_worker(
    config: SourceConfig, queue_path: Path, output_root: Path, stop: threading.Event
) -> None:
    """Process queued scans until `stop` is set, waiting `scan_poll_seconds` when idle."""
    requeue_running(queue_path)
    while not stop.is_set():
        try:
            busy = process_next(config, queue_path, output_root) is not None
        except Exception:
            # A queue the worker cannot read must not end the service; the cause stays in the
            # scan row where `process_next` could record it.
            logging.getLogger(__name__).error("scan worker step failed")
            busy = False
        if not busy:
            stop.wait(config.scan_poll_seconds)
