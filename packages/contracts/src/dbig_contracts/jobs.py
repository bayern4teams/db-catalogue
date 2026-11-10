"""A small SQLite job queue for long work behind a service route (report Part 3; lead ruling D7
(a): extend the collector's SQLite queue pattern rather than add a job library).

One worker per queue file: the service that owns the file runs it in one thread. So a job left
`running` by a crash is only ever this worker's own, and `requeue_running` at start-up puts it
back. shortcut: no lease timestamps, which a second worker on the same file would need.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BUSY_TIMEOUT_MS = 5000


def open_queue(path: Path, table_sql: str) -> sqlite3.Connection:
    """A connection with WAL and a busy timeout, so a status read never fails while the worker
    writes (https://sqlite.org/wal.html)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=BUSY_TIMEOUT_MS / 1000)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    connection.execute(table_sql)
    connection.commit()
    return connection


_JOBS = (
    "CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, payload TEXT NOT NULL, "
    "status TEXT NOT NULL, created_at TEXT NOT NULL, result TEXT, error TEXT)"
)


def submit(path: Path, payload: dict[str, Any]) -> str:
    job_id = uuid.uuid4().hex
    with closing(open_queue(path, _JOBS)) as connection:
        connection.execute(
            "INSERT INTO jobs (id,payload,status,created_at) VALUES (?,?,'queued',?)",
            (job_id, json.dumps(payload), datetime.now(UTC).isoformat()),
        )
        connection.commit()
    return job_id


def claim(path: Path) -> tuple[str, dict[str, Any]] | None:
    """The oldest queued job, now `running`, or None."""
    with closing(open_queue(path, _JOBS)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT id,payload FROM jobs WHERE status='queued' ORDER BY created_at,id LIMIT 1"
        ).fetchone()
        if row is None:
            connection.rollback()
            return None
        connection.execute("UPDATE jobs SET status='running' WHERE id=?", (row[0],))
        connection.commit()
    return row[0], json.loads(row[1])


def finish(path: Path, job_id: str, *, result: dict[str, Any] | None, error: str | None) -> None:
    with closing(open_queue(path, _JOBS)) as connection:
        connection.execute(
            "UPDATE jobs SET status=?,result=?,error=? WHERE id=?",
            (
                "failed" if error else "complete",
                None if result is None else json.dumps(result),
                error,
                job_id,
            ),
        )
        connection.commit()


def status(path: Path, job_id: str) -> dict[str, Any] | None:
    with closing(open_queue(path, _JOBS)) as connection:
        row = connection.execute(
            "SELECT id,status,created_at,result,error FROM jobs WHERE id=?", (job_id,)
        ).fetchone()
    if row is None:
        return None
    return {
        "id": row[0],
        "status": row[1],
        "created_at": row[2],
        **({"result": json.loads(row[3])} if row[3] else {}),
        **({"error": {"code": row[4]}} if row[4] else {}),
    }


def requeue_running(path: Path) -> int:
    """Put back every `running` job: with one worker per file, at start-up it is a crashed run's."""
    with closing(open_queue(path, _JOBS)) as connection:
        cursor = connection.execute("UPDATE jobs SET status='queued' WHERE status='running'")
        connection.commit()
    return cursor.rowcount
