"""Validated snapshot to rebuildable SQLite FTS5 catalogue index."""

from __future__ import annotations

import fnmatch
import hashlib
import itertools
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
from dbig_contracts.models import Edge, Evidence, Field, Object, Source
from dbig_contracts.types import type_family
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
INDEX_VERSION = "9"


class StaleIndexError(OperatorError):
    """The index does not match the snapshot it was built from, or was written by another version.
    The message is the bare code, which the HTTP route returns as it is; the remedy is always
    `dbig-requester index`."""


def _read_snapshot(snapshot: Path) -> tuple[Any, dict[str, Any]]:
    try:
        return read_artifact(snapshot)
    except ValueError as error:
        # The requester holds no snapshot key: the builder writes snapshots unsigned.
        if str(error) == "signature key unavailable":
            raise OperatorError(
                f"snapshot {snapshot} is signed, and the requester reads only unsigned snapshots; "
                "point snapshot_dir at the output of `dbig-builder assemble`"
            ) from error
        raise


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
    # Several sources at once, set by `ask` when a question names an engine or a source.
    source_ids: list[str] | None = None
    # Only tables, collections and views, set by `ask` for a list question.
    objects_only: bool = False
    kind: str | None = None
    data_type: str | None = None
    # Fields whose type falls in this category of `type_categories`, set by `ask` for a question
    # about a type (B6).
    type_category: str | None = None
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


def _excluded(source_id: str, object_item: Object, exclude: dict[str, list[str]]) -> bool:
    """Whether an operator's per-source exclude pattern (DEC-8) hides this object from search.
    Patterns are case-insensitive globs on the object name or `namespace.name`."""
    names = {object_item.name.casefold()}
    if object_item.namespace:
        names.add(f"{object_item.namespace}.{object_item.name}".casefold())
    return any(
        fnmatch.fnmatchcase(name, pattern.casefold())
        for pattern in exclude.get(source_id, [])
        for name in names
    )


def index_snapshot(
    snapshot: Path, state: Path, exclude: dict[str, list[str]] | None = None
) -> str:
    manifest, tables = _read_snapshot(snapshot)
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
        connection.execute("CREATE TABLE sources(id TEXT PRIMARY KEY, kind TEXT, name TEXT)")
        connection.execute(
            "CREATE TABLE relationships(id TEXT PRIMARY KEY, from_id TEXT, to_id TEXT, "
            "rank INTEGER, body TEXT)"
        )
        exclude = exclude or {}
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
        skipped = {
            object_id
            for object_id, item in owners.items()
            if _excluded(item.source_id, item, exclude)
        }
        # What a source is called is searchable on every one of its entries, so "ClickHouse
        # tables" finds the tables of the source whose engine kind is `clickhouse` (DEC-7).
        source_words: dict[str, str] = {}
        for record in tables["nodes.jsonl"]:
            if record.record_type == "source":
                source_item = Source.model_validate(record.model_dump(mode="json"))
                connection.execute(
                    "INSERT INTO sources VALUES (?,?,?)",
                    (source_item.id, source_item.kind, source_item.name),
                )
                source_words[source_item.id] = " ".join(
                    {source_item.id, source_item.kind, source_item.name}
                )
        names = {
            record.id: str(record.model_dump()["name"])
            for record in tables["nodes.jsonl"]
            if record.record_type in {"object", "field"}
        }
        # Where each end lives, so the model can see a relationship crosses two sources (F11).
        # A field's namespace is its object's; an object without one stays None.
        places = {}
        for record in tables["nodes.jsonl"]:
            if record.record_type in {"object", "field"}:
                owner = owners.get(getattr(record, "object_id", record.id))
                places[record.id] = (record.source_id, owner.namespace if owner else None)
        # The containment measurement an edge cites, handed on unchanged (F14).
        evidence = (
            Evidence.model_validate(item.model_dump(mode="json"))
            for item in tables["evidence.jsonl"]
        )
        containment = {
            item.id: item.metrics for item in evidence if item.kind == "value_containment"
        }
        for record in tables["edges.jsonl"]:
            edge = Edge.model_validate(record.model_dump(mode="json"))
            if edge.kind not in RELATIONSHIP_KINDS or {edge.from_id, edge.to_id} & skipped:
                continue
            body: dict[str, Any] = {
                "id": edge.id,
                "kind": edge.kind,
                "status": edge.status,
                "evidence_tier": edge.evidence_tier,
                "from": names.get(edge.from_id),
                "to": names.get(edge.to_id),
                "from_source": places.get(edge.from_id, (None, None))[0],
                "from_namespace": places.get(edge.from_id, (None, None))[1],
                "to_source": places.get(edge.to_id, (None, None))[0],
                "to_namespace": places.get(edge.to_id, (None, None))[1],
                "columns": [
                    [names.get(left), names.get(right)]
                    for left, right in edge.model_dump().get("field_pairs", [])
                ],
                "evidence": edge.explanation,
            }
            for evidence_id in edge.evidence_ids:
                if "basis" in containment.get(evidence_id, {}):
                    keys = ("basis", "estimate", "lower", "upper")
                    body.update({key: containment[evidence_id].get(key) for key in keys})
                    break
            connection.execute(
                "INSERT INTO relationships VALUES (?,?,?,?,?)",
                (
                    edge.id,
                    edge.from_id,
                    edge.to_id,
                    STATUS_RANK.get(edge.status, len(STATUS_RANK)),
                    json.dumps(body, ensure_ascii=False),
                ),
            )
        for record in tables["nodes.jsonl"]:
            if record.id in skipped or getattr(record, "object_id", None) in skipped:
                continue
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
                    " ".join(
                        _indexed_text(part)
                        for part in (*extra[:2], source_words.get(row[1]))
                        if part
                    ),
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
        manifest, _ = _read_snapshot(snapshot)
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
            if not terms and request.type_category is None and not (
                request.objects_only and request.source_ids
            ):
                # A question made only of common words has nothing to look up. Returning every
                # entry instead would hand a model an arbitrary slice of the catalogue. A list
                # question scoped to a named source is the exception: its answer is that
                # source's objects, and a type question's answer is the fields of that type.
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
        if request.source_ids:
            conditions.append(f"e.source_id IN ({','.join('?' * len(request.source_ids))})")
            parameters.extend(request.source_ids)
        if request.objects_only:
            conditions.append("e.kind != 'field'")
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        if terms and request.match == "any":
            # A question that names a table or a topic gets that object before any column that
            # merely mentions it (DEC-7): the object's own name is one of the question's words.
            named = sorted(
                {*(term.casefold() for term in terms)}
                | {single for term in terms for single in _singulars(term.casefold())}
            )
            sql += (
                f" ORDER BY (e.kind != 'field' AND lower(e.name) IN ({','.join('?' * len(named))}))"
                " DESC, e.name = ? COLLATE NOCASE DESC, rank, e.name COLLATE NOCASE, e.id"
            )
            parameters.extend([*named, request.query.strip()])
        elif terms:
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
            if request.type_category is not None and (
                item["data_type"] is None
                or request.type_category not in type_categories(item["data_type"])
            ):
                continue
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


UNVERIFIED_CROSS_SOURCE = "unverified_cross_source"  # an `Edge.evidence_tier` value
RELATIONSHIP_KINDS = frozenset({"DECLARES_FK", "REFERENCE_CANDIDATE"})
# Strongest claim first, so a bounded list of relationships keeps the declared ones.
STATUS_RANK = {"declared": 0, "executed": 0, "observed": 1, "tentative": 2}
# Which relationship rows answer time may use (F11-b): never a rejected one, and a candidate
# between two sources with no positive measurement only when the operator asked for it. Applied in
# SQL so a left-out line never takes a slot of the limit. One `?`: the flag as 0 or 1.
_VISIBLE = (
    "json_extract(body, '$.status') IS NOT 'rejected' AND (? OR "
    f"json_extract(body, '$.evidence_tier') IS NOT '{UNVERIFIED_CROSS_SOURCE}')"
)


def relationships_for(
    state: Path, object_ids: set[str], limit: int, include_unverified: bool = False
) -> list[dict]:
    """Relationships touching these objects, strongest claim first, at most `limit`: metadata
    only (names, status and the builder's evidence sentence), for the answering model (DEC-5)."""
    if not object_ids or limit < 1:
        return []
    with closing(sqlite3.connect(_database(state))) as connection:
        marks = ",".join("?" * len(object_ids))
        query = (
            f"SELECT body FROM relationships WHERE (from_id IN ({marks}) "  # noqa: S608 # C5: reason=only ? placeholders are interpolated, values are bound; approved=user, 2026-11-08; review=2027-05-08
            f"OR to_id IN ({marks})) AND {_VISIBLE} "
            f"ORDER BY (from_id IN ({marks}) AND to_id IN ({marks})) DESC, rank, id LIMIT ?"
        )
        ordered = sorted(object_ids)
        # An edge between two retrieved objects is what the question is about; a hub's other
        # links must not push it past the limit.
        rows = connection.execute(
            query,
            [*ordered, *ordered, int(include_unverified), *ordered, *ordered, limit],
        ).fetchall()
    return [json.loads(row[0]) for row in rows]


def _distances(
    adjacent: dict[str, list[tuple[str, str]]], start: str, limit: int
) -> dict[str, int]:
    """Breadth-first edge counts from `start`, no further than `limit` edges."""
    seen, frontier = {start: 0}, [start]
    for depth in range(1, limit + 1):
        frontier = [
            node
            for current in frontier
            for node, _ in adjacent.get(current, ())
            if seen.setdefault(node, depth) == depth
        ]
    return seen


def _visible_edges(
    connection: sqlite3.Connection, include_unverified: bool
) -> tuple[dict[str, tuple[str, str, str]], dict[str, int], dict[str, list[tuple[str, str]]]]:
    """The relationships answer time may use, by id, their ranks, and the adjacency between them."""
    edges: dict[str, tuple[str, str, str]] = {}
    ranks: dict[str, int] = {}
    for edge_id, left, right, body, rank in connection.execute(
        "SELECT id, from_id, to_id, body, rank FROM relationships "  # noqa: S608 # C5: reason=only the constant _VISIBLE clause is interpolated, values are bound; approved=user, 2026-11-08; review=2027-05-08
        f"WHERE {_VISIBLE} ORDER BY rank, id",
        (int(include_unverified),),
    ):
        edges[edge_id] = (left, right, body)
        ranks[edge_id] = rank
    adjacent: dict[str, list[tuple[str, str]]] = {}
    for edge_id, (left, right, _) in edges.items():
        adjacent.setdefault(left, []).append((right, edge_id))
        adjacent.setdefault(right, []).append((left, edge_id))
    return edges, ranks, adjacent


def _shortest_paths(
    adjacent: dict[str, list[tuple[str, str]]],
    reach: dict[str, dict[str, int]],
    ranks: dict[str, int],
    start: str,
    goal: str,
) -> list[tuple[tuple[str, ...], tuple[str, ...]]]:
    """Every shortest path as (nodes after `start`, edge ids), strongest first: its sorted edge
    ranks, then its edge ids (D1)."""

    def walk(node: str, hops: int, taken: tuple[str, ...], via: tuple[str, ...]):
        # Steps that stay on a shortest path: one edge nearer the goal, one further from here.
        if node == goal:
            yield taken, via
            return
        for other, edge_id in adjacent[node]:
            if reach[goal].get(other) == hops - 1 and reach[start].get(other) == len(via) + 1:
                yield from walk(other, hops - 1, (*taken, other), (*via, edge_id))

    return sorted(
        walk(start, reach[start][goal], (), ()),
        key=lambda path: (sorted(ranks[e] for e in path[1]), path[1]),
    )


def bridge_paths(
    state: Path, ends: set[str], max_hops: int, max_bridges: int, include_unverified: bool = False
) -> tuple[list[dict], list[dict]]:
    """F16, DEC-24: the tables on a shortest path of at most `max_hops` edges between two of the
    `ends`, through tables that are not themselves ends, at most `max_bridges` of them, as index
    rows with `role: path_only` (the table, then only the columns its path lines name), and the
    path's relationship lines as stored (a candidate edge keeps its status). One read of the table.
    shortcut: every shortest path of a pair is walked and sorted, so a dense hub of equal-length
    paths is slow; the upgrade is to stop after a visit cap behind config.
    A path is kept whole or not at all, and walks only the edges `relationships_for` may show."""
    if len(ends) < 2 or max_hops < 2 or max_bridges < 1:
        return [], []
    with closing(sqlite3.connect(_database(state))) as connection:
        edges, ranks, adjacent = _visible_edges(connection, include_unverified)
        reach = {end: _distances(adjacent, end, max_hops) for end in sorted(ends & adjacent.keys())}
        kept: list[str] = []
        used: dict[str, None] = {}
        for start, goal in itertools.combinations(reach, 2):
            hops = reach[start].get(goal, 0)
            if hops < 2 or hops - 1 > max_bridges:  # no path, or a direct line (already shown)
                continue
            for taken, via in _shortest_paths(adjacent, reach, ranks, start, goal):
                inner = taken[:-1]
                new = [n for n in inner if n not in kept]
                # A path through another end is two shorter paths, each found on its own.
                if ends.intersection(inner) or len(kept) + len(new) > max_bridges:
                    continue
                kept += dict.fromkeys(new)
                used.update(dict.fromkeys(via))
        keys: dict[str, set[str]] = {}
        for edge_id in used:
            left, right, body = edges[edge_id]
            for column_pair in json.loads(body)["columns"]:
                for node, column in zip((left, right), column_pair, strict=True):
                    keys.setdefault(node, set()).add(column)
        connection.row_factory = sqlite3.Row
        rows = {
            row["id"]: dict(row)
            for row in connection.execute(
                f"SELECT * FROM entries WHERE id IN ({','.join('?' * len(kept))})",  # noqa: S608 # C5: reason=only ? placeholders are interpolated, values are bound; approved=user, 2026-11-08; review=2027-05-08
                kept,
            )
        }
    fields = fields_of(state, set(kept))
    items = [
        {**item, "role": "path_only"}
        for node in kept
        if node in rows
        for item in (
            rows[node],
            *(f for f in fields if f["object_id"] == node and f["name"] in keys.get(node, ())),
        )
    ]
    return items, [json.loads(edges[edge_id][2]) for edge_id in used]


def chained_paths(
    state: Path,
    ends: set[str],
    tables: set[str],
    shown: set[str],
    max_hops: int,
    include_unverified: bool = False,
) -> list[dict]:
    """B4 (N-I10): for each two of the `ends` with no direct line, the strongest shortest path of
    at most `max_hops` edges over the `shown` relationships (`relationship_limit` still bounds what
    the model sees, DEC-5) that stays inside `tables`, as table names and relationship ids. A chain
    contained in a longer one is left out."""
    if len(ends) < 2 or max_hops < 2:
        return []
    with closing(sqlite3.connect(_database(state))) as connection:
        edges, ranks, adjacent = _visible_edges(connection, include_unverified)
    adjacent = {
        node: [step for step in steps if step[1] in shown] for node, steps in adjacent.items()
    }
    names = {
        node: name
        for left, right, body in edges.values()
        for node, name in ((left, json.loads(body)["from"]), (right, json.loads(body)["to"]))
    }
    reach = {end: _distances(adjacent, end, max_hops) for end in sorted(ends & adjacent.keys())}
    chains = []
    for start, goal in itertools.combinations(reach, 2):
        if reach[start].get(goal, 0) < 2:
            continue
        for taken, via in _shortest_paths(adjacent, reach, ranks, start, goal):
            if tables.issuperset(taken):
                chains.append({"tables": [names[start], *map(names.get, taken)], "via": via})
                break
    return [
        {"tables": chain["tables"], "relationships": list(chain["via"])}
        for chain in chains
        if not any(set(chain["via"]) < set(other["via"]) for other in chains)
    ]


def fields_of(state: Path, object_ids: set[str]) -> list[dict]:
    """Every field of these objects as stored index rows (list columns stay JSON text; the answer
    context keeps only its own keys), by name. Not capped (DEC-18).
    ponytail: a very wide table can exceed the model context; the upgrade path is a cap behind
    config or indexing type text (LEAD_REVIEW F13 option C)."""
    if not object_ids:
        return []
    with closing(sqlite3.connect(_database(state))) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            f"SELECT * FROM entries WHERE kind='field' "  # noqa: S608 # C5: reason=only ? placeholders are interpolated, values are bound; approved=user, 2026-11-08; review=2027-05-08
            f"AND object_id IN ({','.join('?' * len(object_ids))}) "
            "ORDER BY name COLLATE NOCASE, id",
            sorted(object_ids),
        ).fetchall()
    return [dict(row) for row in rows]


# Words that ask for a list of objects when plural ("which tables ...", "list the collections").
OBJECT_WORDS = frozenset("table collection view".split())


# B6 (owner 2026-11-10, "Map type words (Recommended)"): the SQL standard's type categories. DATE,
# TIME and TIMESTAMP are datetime types; exact and approximate numerics beyond the integer family
# are listed here in PostgreSQL and ClickHouse spelling, and `float` as MongoDB sampling records it.
NUMERIC_TYPES = frozenset(
    {"numeric", "decimal", "real", "float", "double precision", "double", "float4", "float8",
     "float32", "float64", "bfloat16", "decimal32", "decimal64", "decimal128", "decimal256"}
)
BOOLEAN_TYPES = frozenset({"boolean", "bool"})
# A question word, in singular form, and the category it names.
TYPE_WORDS = {
    "timestamp": "datetime", "datetime": "datetime", "date": "datetime",
    "numeric": "numeric", "number": "numeric", "decimal": "numeric", "float": "numeric",
    "integer": "integer", "text": "text", "string": "text",
    "boolean": "boolean", "bool": "boolean", "uuid": "uuid",
}
# A type word counts only beside one of these: "what date did it run" is not about a type.
TYPE_QUESTION_WORDS = frozenset({"type", "typed"})
# Words that ask for every match, not the best few (B7, owner 2026-11-10: "List mode").
LIST_WORDS = frozenset({"all", "every", "list"})


def type_categories(data_type: str) -> set[str]:
    """The engine type's family (`dbig_contracts.types`) and the SQL categories it belongs to."""
    family = type_family(data_type)
    found = {family}
    if family == "integer" or family in NUMERIC_TYPES:
        found.add("numeric")
    if family.startswith(("date", "time")):
        found.add("datetime")
    if family in BOOLEAN_TYPES:
        found.add("boolean")
    return found


def question_type(question: str) -> str | None:
    """The type category a question asks about, or None (B6)."""
    forms = [
        {word, *_singulars(word)}
        for word in (item.casefold() for item in re.findall(r"\w+", question, flags=re.UNICODE))
    ]
    if not any(TYPE_QUESTION_WORDS & alternatives for alternatives in forms):
        return None
    return next(
        (TYPE_WORDS[form] for alternatives in forms for form in sorted(alternatives)
         if form in TYPE_WORDS),
        None,
    )


def question_scope(state: Path, question: str) -> tuple[list[str], bool, set[str]]:
    """What a natural-language question names: the sources it mentions by ID, engine kind or
    name, whether it asks for a list of objects, and the words that named the sources, which
    filter the search and so need not also match (DEC-7). Reads the index only."""
    database = _database(state)
    if not database.is_file():
        return [], False, set()
    words = [word.casefold() for word in re.findall(r"\w+", question, flags=re.UNICODE)]
    forms = {word: {word, *_singulars(word)} for word in words}
    with closing(sqlite3.connect(database)) as connection:
        try:
            rows = connection.execute("SELECT id, kind, name FROM sources").fetchall()
        except sqlite3.OperationalError:
            return [], False, set()  # an old index: `search` refuses it with STALE_INDEX
    named = {word for word, alternatives in forms.items() if alternatives & {
        value.casefold() for row in rows for value in row
    }}
    sources = sorted(
        row[0] for row in rows if {value.casefold() for value in row} & named
    )
    listing = any(
        word.endswith("s") and OBJECT_WORDS.intersection(_singulars(word)) for word in words
    )
    return sources, listing, named if sources else set()


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
