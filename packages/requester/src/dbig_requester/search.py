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
# The last line is schema words: a question about "which column holds ..." matches any collection
# that describes columns, so they cannot be what makes a question match. ponytail: a word list is
# the ceiling; the deferred alternative is to require a minimum share of the meaningful words to
# match.
STOPWORDS = frozenset(
    "a an and any are as at be by can do does for from have how i in into is it its me my "
    "of on or show tell that the their there these this to what where which who why with "
    "we you your all list find give get "
    "column table field number holds store stored".split()
)
# Relevance weights for bm25 over the indexed columns: name, context (owning table and schema),
# description. A hit on the entry's own name matters most, its table's name next.
WEIGHTS = (10.0, 4.0, 1.0)
# Bump when what `index_snapshot` writes into the full-text table changes meaning. An index
# written under another version is refused with STALE_INDEX instead of answering from words it
# does not hold (a search would return nothing, as if the catalogue lacked the column).
INDEX_VERSION = "4"


class StaleIndexError(OperatorError):
    """The index does not match the snapshot it was built from, or was written by another version.
    The message is the bare code, which the HTTP route returns as it is; the remedy is always
    `dbig-requester index`."""


def _broken(name: str) -> str:
    """The name with a space at every word break: lower to upper (`unitID`), the end of an
    upper-case run before a capitalised word (`HTTPServer`), and letters to digits and back
    (`In000s`). Case is read with `str.isupper`, so an accented or Cyrillic capital breaks too."""
    out: list[str] = []
    for index, char in enumerate(name):
        if index:
            before, after = name[index - 1], name[index + 1 : index + 2]
            if (
                (char.isupper() and before.islower())
                or (char.isupper() and before.isupper() and after.islower())
                or (char.isdigit() != before.isdigit() and char.isalnum() and before.isalnum())
            ):
                out.append(" ")
        out.append(char)
    return "".join(out)


def _words(name: str) -> list[str]:
    """The words of a name, lower-cased: `unitPriceIn000s` gives unit, price, in, 000, s;
    `HTTPServerID` gives http, server, id; `URL` stays one word."""
    return re.findall(r"[^\W_]+", _broken(name).casefold())


# Folding a plural to its singular. Candidates, not one answer, because English is ambiguous
# (`movies` is `movie`, `boxes` is `box`, `industries` is `industry`); each is tried as a whole
# word. A fold below MIN_SINGULAR_LENGTH is refused (`pos` is not `po`, `gas` is not `ga`), and so
# is a word in NON_PLURALS, which ends in `s` and is not one (`news` is not `new`).
# ponytail: a finite list and two rules, not a stemmer; the FTS5 `porter` tokenizer drops the
# exact form that ranks an exact name first. Ceilings: a two-letter singular (`ids`) is not
# folded; a non-plural word ending in `s` missing from the list merges with its shorter word.
MIN_SINGULAR_LENGTH = 3
NON_PLURALS = frozenset("news lens series species bias alias canvas atlas".split())
_NOT_PLURAL_ENDINGS = ("ss", "us", "is")  # address, status, analysis
_SIBILANT_ENDINGS = ("s", "x", "z", "ch", "sh")  # a plural adds `es` after these


def _ies_singular(word: str) -> str | None:
    if word.endswith("ies") and word not in NON_PLURALS:
        singular = word[: -len("ies")] + "y"
        return singular if len(singular) >= MIN_SINGULAR_LENGTH else None
    return None


def _singulars(word: str) -> list[str]:
    """The words `word` may be the plural of; empty when it is not a plural."""
    if word in NON_PLURALS or not word.endswith("s") or word.endswith(_NOT_PLURAL_ENDINGS):
        return []
    candidates = [word[:-1]]
    if word.endswith("ies"):
        candidates.append(_ies_singular(word) or "")
    elif word.endswith("es") and word[:-2].endswith(_SIBILANT_ENDINGS):
        candidates.append(word[:-2])
    return [item for item in candidates if len(item) >= MIN_SINGULAR_LENGTH]


def _indexed_text(text: str | None) -> str:
    """Text as indexed: whole, so `unitid` finds `unitID`, then its words when case or digits
    split it, then the `-y` singular of every `-ies` word (`industries` also holds `industry`,
    which a prefix match on `industry` cannot reach). Other plurals need nothing: the singular is
    a prefix of them, and adding it would make `invoices` rank with `invoice`."""
    if not text:
        return ""
    words = _words(text)
    pieces = [text]
    if _broken(text) != text:
        pieces.append(" ".join(words))
    pieces.extend(sorted({singular for word in words if (singular := _ies_singular(word))}))
    return " ".join(pieces)


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
                    _indexed_text(row[3]),
                    " ".join(_indexed_text(part) for part in extra[:2] if part),
                    _indexed_text(extra[2]),
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
            raise StaleIndexError("STALE_INDEX")
        if metadata.get("index_version") != INDEX_VERSION:
            # Same code and same remedy (`dbig-requester index`) as any other stale index. Refused
            # here, not rebuilt: a search must not write to the state directory.
            raise StaleIndexError("STALE_INDEX")
        terms = re.findall(r"\w+", request.query, flags=re.UNICODE)
        if request.match == "any":
            # Checked on the word as asked and on what it may be the plural of, so `tables` is a
            # schema word because `table` is.
            terms = [
                term
                for term in terms
                if len(term) > 1
                and term.casefold() not in STOPWORDS
                and not STOPWORDS.intersection(_singulars(term.casefold()))
            ]
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
            # quoting keeps FTS5 operators in user input from being interpreted. A plural also
            # matches each of its singulars as a whole word (`industries` finds `industry`); the
            # other way round, `industry` finds `industries`, the index holds the `-y` form.
            joiner = " AND " if request.match == "all" else " OR "
            parameters.append(joiner.join(_term_query(term) for term in terms))
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
        if terms:
            # An entry named exactly as asked comes first: a plural query also matches its
            # singular, and `invoices` must not rank below `invoice` for `invoices`.
            sql += " ORDER BY e.name = ? COLLATE NOCASE DESC, rank, e.name COLLATE NOCASE, e.id"
            parameters.append(request.query.strip())
        else:
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


def _term_query(term: str) -> str:
    # `term` is word characters only (`\w+`), so it cannot carry a quote or an FTS5 operator.
    alternatives = "".join(f' OR "{singular}"' for singular in _singulars(term.casefold()))
    return f'("{term}"*{alternatives})' if alternatives else f'"{term}"*'


def _empty(manifest: Any, digest: str, request: SearchRequest) -> dict:
    return {
        "ir_version": manifest.ir_version,
        "snapshot_digest": digest,
        "completeness": manifest.completeness,
        "results": [],
        "limit": request.limit,
        "offset": request.offset,
    }
