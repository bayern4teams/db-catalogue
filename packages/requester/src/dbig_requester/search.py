"""Validated snapshot to rebuildable SQLite FTS5 catalogue index."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dbig_contracts.artifact import read_artifact
from dbig_contracts.errors import OperatorError
from dbig_contracts.models import Edge, Field, Object
from pydantic import BaseModel, ConfigDict
from pydantic import Field as PydanticField


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = PydanticField(default="", max_length=256)
    source_id: str | None = None
    kind: str | None = None
    data_type: str | None = None
    tag: str | None = None
    capability: str | None = None
    evidence_status: str | None = None
    max_age_seconds: int | None = PydanticField(default=None, ge=0)
    limit: int = PydanticField(default=20, ge=1, le=100)
    offset: int = PydanticField(default=0, ge=0)


def _database(state: Path) -> Path:
    return state / "catalog.sqlite3"


def index_snapshot(snapshot: Path, state: Path) -> str:
    manifest, tables = read_artifact(snapshot)
    if manifest.content_type != "catalog-snapshot":
        raise ValueError("expected catalog snapshot")
    digest = hashlib.sha256((snapshot / "manifest.json").read_bytes()).hexdigest()
    state.mkdir(parents=True, exist_ok=True)
    temporary = state / "catalog.new.sqlite3"
    if temporary.exists():
        temporary.unlink()
    with closing(sqlite3.connect(temporary)) as connection:
        try:
            connection.execute("CREATE VIRTUAL TABLE fts USING fts5(id UNINDEXED, name)")
        except sqlite3.OperationalError as error:
            raise RuntimeError("FTS5_UNAVAILABLE") from error
        connection.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute(
            "CREATE TABLE entries(id TEXT PRIMARY KEY, source_id TEXT, kind TEXT, name TEXT, "
            "data_type TEXT, object_id TEXT, observed_at TEXT, tags TEXT, "
            "capabilities TEXT, evidence_status TEXT)"
        )
        statuses: dict[str, set[str]] = {}
        for record in tables["edges.jsonl"]:
            edge = Edge.model_validate(record.model_dump(mode="json"))
            for node_id in (edge.from_id, edge.to_id):
                statuses.setdefault(node_id, set()).add(edge.status)
        for record in tables["nodes.jsonl"]:
            row: tuple[str, str, str, str, str | None, str | None, str, str, str, str]
            if record.record_type == "object":
                object_item = Object.model_validate(record.model_dump(mode="json"))
                row = (
                    object_item.id,
                    object_item.source_id,
                    object_item.kind,
                    object_item.name,
                    None,
                    None,
                    object_item.observed_at.isoformat(),
                    json.dumps(object_item.tags),
                    json.dumps(object_item.capabilities),
                    json.dumps(sorted(statuses.get(object_item.id, set()))),
                )
            elif record.record_type == "field":
                field_item = Field.model_validate(record.model_dump(mode="json"))
                row = (
                    field_item.id,
                    field_item.source_id,
                    "field",
                    field_item.name,
                    field_item.data_type,
                    field_item.object_id,
                    field_item.observed_at.isoformat(),
                    json.dumps(field_item.tags),
                    json.dumps(field_item.capabilities),
                    json.dumps(sorted(statuses.get(field_item.object_id, set()))),
                )
            else:
                continue
            connection.execute("INSERT INTO entries VALUES (?,?,?,?,?,?,?,?,?,?)", row)
            connection.execute("INSERT INTO fts(id,name) VALUES (?,?)", (row[0], row[3]))
        connection.executemany(
            "INSERT INTO meta VALUES (?,?)",
            (
                ("snapshot", str(snapshot.resolve(strict=True))),
                ("digest", digest),
                ("completeness", manifest.completeness),
            ),
        )
        connection.commit()
    os.replace(temporary, _database(state))
    return digest


def search(state: Path, request: SearchRequest) -> dict:
    database = _database(state)
    if not database.is_file():
        # Searching before indexing is an ordinary first-run mistake. Without this the sqlite
        # driver reports "unable to open database file" as a traceback, which tells the operator
        # neither which path was tried nor what to do about it.
        raise OperatorError(
            f"no search index at {database}; run `dbig-requester index` against the snapshot first"
        )
    with closing(sqlite3.connect(database)) as connection:
        connection.row_factory = sqlite3.Row
        metadata = dict(connection.execute("SELECT key,value FROM meta"))
        snapshot = Path(metadata["snapshot"])
        manifest, _ = read_artifact(snapshot)
        digest = hashlib.sha256((snapshot / "manifest.json").read_bytes()).hexdigest()
        if digest != metadata["digest"]:
            raise ValueError("STALE_INDEX")
        terms = re.findall(r"\w+", request.query, flags=re.UNICODE)
        sql = "SELECT e.* FROM entries e"
        conditions = []
        parameters: list[str | int] = []
        if terms:
            sql += " JOIN fts ON fts.id=e.id"
            conditions.append("fts MATCH ?")
            parameters.append(" AND ".join('"' + term + '"' for term in terms))
        for condition, value in (
            ("e.source_id=?", request.source_id),
            ("e.kind=?", request.kind),
            ("e.data_type=?", request.data_type),
        ):
            if value is not None:
                conditions.append(condition)
                parameters.append(value)
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY e.name COLLATE NOCASE, e.id"
        rows = []
        cutoff = (
            datetime.now(UTC) - timedelta(seconds=request.max_age_seconds)
            if request.max_age_seconds is not None
            else None
        )
        for row in connection.execute(sql, parameters):
            item = dict(row)
            if request.tag is not None and request.tag not in json.loads(item["tags"]):
                continue
            if request.capability is not None and request.capability not in json.loads(
                item["capabilities"]
            ):
                continue
            if request.evidence_status is not None and request.evidence_status not in json.loads(
                item["evidence_status"]
            ):
                continue
            if cutoff is not None and datetime.fromisoformat(item["observed_at"]) < cutoff:
                continue
            for key in ("tags", "capabilities", "evidence_status"):
                item[key] = json.loads(item[key])
            rows.append(item)
        rows = rows[request.offset : request.offset + request.limit]
    return {
        "ir_version": manifest.ir_version,
        "snapshot_digest": digest,
        "completeness": manifest.completeness,
        "results": rows,
        "limit": request.limit,
        "offset": request.offset,
    }
