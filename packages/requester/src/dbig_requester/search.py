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
from typing import Any, Literal

from dbig_contracts.artifact import read_artifact
from dbig_contracts.errors import OperatorError
from dbig_contracts.models import Edge, Field, Object
from pydantic import BaseModel, ConfigDict
from pydantic import Field as PydanticField

# Words that carry no catalogue meaning in a question. Dropped only for `match="any"`, which is
# how `ask` retrieves: as a prefix match, "is" would otherwise hit every `is_active` column.
STOPWORDS = frozenset(
    "a an and any are as at be by can do does for from have how i in into is it its me my "
    "of on or show tell that the their there these this to what where which who why with "
    "we you your all list find give get".split()
)
# Relevance weights for bm25 over the indexed columns: name, context (owning table and schema),
# description. A hit on the entry's own name matters most, its table's name next.
WEIGHTS = (10.0, 4.0, 1.0)
# Bump when what `index_snapshot` writes into the full-text table changes meaning. An index
# written under another version is refused with STALE_INDEX instead of answering from words it
# does not hold (a search would return nothing, as if the catalogue lacked the column).
INDEX_VERSION = "2"


# Where a name breaks into words: lower to upper (`unitID`), the end of an upper-case run before
# a capitalised word (`HTTPServer`), and between letters and digits (`In000s`).
_WORD_BREAK = re.compile(
    r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|(?<=[^\W\d_])(?=\d)|(?<=\d)(?=[^\W\d_])"
)


def _words(name: str) -> list[str]:
    """The words of a name, lower-cased: `unitPriceIn000s` gives unit, price, in, 000,
    s; `HTTPServerID` gives http, server, id; `URL` stays one word.

    ponytail: case is read for the ASCII letters only; a name in another script is split at
    separators and digits but not by case. Ceiling: add a per-script rule when a catalogue needs it.
    """
    return re.findall(r"[^\W_]+", _WORD_BREAK.sub(" ", name).casefold())


def _indexed_name(name: str | None) -> str:
    """The name as indexed: whole, so `companyid` finds it, followed by its words when case or
    digits split it. A name the tokenizer already splits (snake_case) is left as it is."""
    if not name:
        return ""
    if _WORD_BREAK.search(name):
        return f"{name} {' '.join(_words(name))}"
    return name


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
    # "all": every term must match, for an operator's explicit query. "any": entries are ranked
    # by how well they match any term, for a question in natural language, where requiring every
    # word ("which table stores ...") would match nothing.
    match: Literal["all", "any"] = "all"


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
            connection.execute(
                "CREATE VIRTUAL TABLE fts USING fts5(id UNINDEXED, name, context, description)"
            )
        except sqlite3.OperationalError as error:
            raise RuntimeError("FTS5_UNAVAILABLE") from error
        connection.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute(
            "CREATE TABLE entries(id TEXT PRIMARY KEY, source_id TEXT, kind TEXT, name TEXT, "
            "data_type TEXT, object_id TEXT, observed_at TEXT, tags TEXT, "
            "capabilities TEXT, evidence_status TEXT, object_name TEXT, namespace TEXT, "
            "description TEXT)"
        )
        statuses: dict[str, set[str]] = {}
        for record in tables["edges.jsonl"]:
            edge = Edge.model_validate(record.model_dump(mode="json"))
            for node_id in (edge.from_id, edge.to_id):
                statuses.setdefault(node_id, set()).add(edge.status)
        owners = {
            record.id: Object.model_validate(record.model_dump(mode="json"))
            for record in tables["nodes.jsonl"]
            if record.record_type == "object"
        }
        for record in tables["nodes.jsonl"]:
            row: tuple[str, str, str, str, str | None, str | None, str, str, str, str]
            extra: tuple[str | None, str | None, str | None]
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
                extra = (None, object_item.namespace, object_item.description)
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
                owner = owners.get(field_item.object_id)
                extra = (
                    owner.name if owner else None,
                    owner.namespace if owner else None,
                    field_item.description,
                )
            else:
                continue
            connection.execute(
                "INSERT INTO entries VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (*row, *extra)
            )
            connection.execute(
                "INSERT INTO fts(id,name,context,description) VALUES (?,?,?,?)",
                (
                    row[0],
                    _indexed_name(row[3]),
                    " ".join(_indexed_name(part) for part in extra[:2] if part),
                    extra[2] or "",
                ),
            )
        connection.executemany(
            "INSERT INTO meta VALUES (?,?)",
            (
                ("snapshot", str(snapshot.resolve(strict=True))),
                ("digest", digest),
                ("completeness", manifest.completeness),
                ("index_version", INDEX_VERSION),
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
        if metadata.get("index_version") != INDEX_VERSION:
            # Same code and same remedy (`dbig-requester index`) as any other stale index. Refused
            # here, not rebuilt: a search must not write to the state directory.
            raise ValueError("STALE_INDEX")
        terms = re.findall(r"\w+", request.query, flags=re.UNICODE)
        if request.match == "any":
            terms = [term for term in terms if term.casefold() not in STOPWORDS and len(term) > 1]
            if not terms:
                # A question made only of common words has nothing to look up. Returning every
                # entry instead would hand a model an arbitrary slice of the catalogue.
                return _empty(manifest, digest, request)
        sql = "SELECT e.* FROM entries e"
        conditions = []
        parameters: list[str | float | int] = []
        if terms:
            sql = "SELECT e.*, bm25(fts, ?, ?, ?) AS rank FROM entries e JOIN fts ON fts.id=e.id"
            parameters.extend(WEIGHTS)
            conditions.append("fts MATCH ?")
            # Each term as a quoted prefix: `customer` finds `customers` and `customer_id`, and
            # quoting keeps FTS5 operators in user input from being interpreted.
            joiner = " AND " if request.match == "all" else " OR "
            parameters.append(joiner.join('"' + term.replace('"', "") + '"*' for term in terms))
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
        sql += (
            " ORDER BY rank, e.name COLLATE NOCASE, e.id"
            if terms
            else " ORDER BY e.name COLLATE NOCASE, e.id"
        )
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
            item.pop("rank", None)
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


def _empty(manifest: Any, digest: str, request: SearchRequest) -> dict:
    return {
        "ir_version": manifest.ir_version,
        "snapshot_digest": digest,
        "completeness": manifest.completeness,
        "results": [],
        "limit": request.limit,
        "offset": request.offset,
    }
