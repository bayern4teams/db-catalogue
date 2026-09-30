"""Read-only query rendering from catalog IDs; never opens a source connection."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from dbig_contracts.errors import OperatorError
from pydantic import BaseModel, ConfigDict, Field
from sqlglot import exp, parse_one

from .config import PlannerConfig
from .graph import load_graph
from .paths import load_path


class PlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    max_rows: int | None = Field(default=None, ge=1, le=100_000)


def _table(item: dict, alias: str) -> exp.Table:
    table = exp.Table(this=exp.to_identifier(item["name"], quoted=True))
    if item.get("namespace"):
        table.set("db", exp.to_identifier(item["namespace"], quoted=True))
    table.set("alias", exp.TableAlias(this=exp.to_identifier(alias, quoted=True)))
    return table


def _pairs(edge: dict, fields: dict, left_id: str, right_id: str) -> list[tuple[str, str]]:
    pairs = []
    for pair in edge.get("field_pairs", []):
        if len(pair) != 2 or pair[0] not in fields or pair[1] not in fields:
            raise ValueError("UNRESOLVED_JOIN_FIELDS")
        first, second = fields[pair[0]], fields[pair[1]]
        if first["object_id"] == left_id and second["object_id"] == right_id:
            pairs.append((first["name"], second["name"]))
        elif first["object_id"] == right_id and second["object_id"] == left_id:
            pairs.append((second["name"], first["name"]))
        else:
            raise ValueError("JOIN_FIELD_OBJECT_MISMATCH")
    if not pairs:
        raise ValueError("UNRESOLVED_JOIN_FIELDS")
    return pairs


def _mongo_name(value: str) -> str:
    if not value or "\x00" in value or any(part.startswith("$") for part in value.split(".")):
        raise ValueError("UNSAFE_MONGO_IDENTIFIER")
    return value


def _sql_read(item: dict, dialect: str, rows: int) -> str:
    query = exp.select(exp.Star()).from_(_table(item, "t0")).limit(rows)
    sql = query.sql(dialect=dialect)
    parsed = parse_one(sql, read=dialect)
    if not isinstance(parsed, exp.Select) or {
        table.name for table in parsed.find_all(exp.Table)
    } != {item["name"]}:
        raise ValueError("UNSAFE_PLAN_AST")
    return sql


def _sql_join(
    run: list[str], links: list[dict], objects: dict, fields: dict, dialect: str, rows: int
) -> str:
    """One read-only SELECT joining consecutive objects of one SQL source, AST-checked."""
    query = exp.select(exp.Star()).from_(_table(objects[run[0]], "t0"))
    for index, (right, edge) in enumerate(zip(run[1:], links, strict=True), start=1):
        pairs = _pairs(edge, fields, run[index - 1], right)
        conditions = [
            exp.EQ(
                this=exp.column(left_field, table=f"t{index - 1}", quoted=True),
                expression=exp.column(right_field, table=f"t{index}", quoted=True),
            )
            for left_field, right_field in pairs
        ]
        predicate = conditions[0] if len(conditions) == 1 else exp.and_(*conditions)
        query = query.join(_table(objects[right], f"t{index}"), on=predicate)
    sql = query.limit(rows).sql(dialect=dialect)
    parsed = parse_one(sql, read=dialect)
    if not isinstance(parsed, exp.Select) or {
        table.name for table in parsed.find_all(exp.Table)
    } != {objects[node]["name"] for node in run}:
        raise ValueError("UNSAFE_PLAN_AST")
    return sql


def _mongo_pipeline(
    run: list[str], links: list[dict], objects: dict, fields: dict, rows: int
) -> list[dict]:
    """A `$lookup` chain over consecutive collections of one MongoDB source."""
    _mongo_name(objects[run[0]]["name"])
    pipeline: list[dict] = []
    for index, (right, edge) in enumerate(zip(run[1:], links, strict=True), start=1):
        pairs = _pairs(edge, fields, run[index - 1], right)
        if len(pairs) != 1:
            raise ValueError("MONGO_COMPOSITE_LOOKUP_UNSUPPORTED")
        pipeline.append(
            {
                "$lookup": {
                    "from": _mongo_name(objects[right]["name"]),
                    "localField": _mongo_name(pairs[0][0]),
                    "foreignField": _mongo_name(pairs[0][1]),
                    "as": f"joined_{index}",
                }
            }
        )
    pipeline.append({"$limit": rows})
    return pipeline


def _runs(nodes: list[str], source_ids: list[str]) -> list[tuple[int, int]]:
    """Maximal stretches of consecutive path objects in one source, as [start, end) indexes.

    Only consecutive: a path that leaves a source and comes back is two reads, because the
    objects in between belong to another system and cannot be joined inside either.
    """
    runs: list[tuple[int, int]] = []
    start = 0
    for index in range(1, len(nodes) + 1):
        if index == len(nodes) or source_ids[index] != source_ids[start]:
            runs.append((start, index))
            start = index
    return runs


def render_plan(directory: Path, request: PlanRequest, config: PlannerConfig) -> dict:
    graph = load_graph(directory)
    path = load_path(directory, request.path_id)
    if path["snapshot_digest"] != graph["snapshot_digest"]:
        # Two distinct causes reached the operator as one opaque code. A stale path is a
        # rebuild; a lineage path is a category error no rebuild will fix.
        raise OperatorError(
            f"STALE_PATH: path {request.path_id} was produced from a different snapshot than "
            "this graph; re-run the path query against the current graph"
        )
    if path["kind"] != "join":
        raise OperatorError(
            f"NON_JOIN_PATH: path {request.path_id} is a {path['kind']} path, and only a join "
            "path can be rendered as a query plan. Observed lineage records what already "
            "happened; it is not something to execute"
        )
    objects, fields = graph["objects"], graph["fields"]
    edges = {item["id"]: item for item in graph["references"]}
    nodes = path["object_ids"]
    links = [edges[edge_id] for edge_id in path["edge_ids"]]
    expected_id = hashlib.sha256(
        json.dumps(["join", nodes, path["edge_ids"]], separators=(",", ":")).encode()
    ).hexdigest()
    if request.path_id != expected_id:
        raise ValueError("INVALID_PATH_ID")
    if len(links) != len(nodes) - 1 or any(node not in objects for node in nodes):
        raise ValueError("INVALID_PATH")
    for left, right, edge in zip(nodes, nodes[1:], links, strict=False):
        if {left, right} != {edge["from_id"], edge["to_id"]}:
            raise ValueError("INVALID_PATH_EDGE")
    rows = min(request.max_rows or config.max_plan_rows, config.max_plan_rows)
    source_ids = [objects[node]["source_id"] for node in nodes]
    kinds = [graph["sources"][source]["kind"] for source in source_ids]
    dialects = {"postgresql": "postgres", "clickhouse": "clickhouse"}
    steps: list[dict] = []
    warnings: list[str] = []
    unresolved: list[str] = []
    if len(set(source_ids)) > 1:
        # Consecutive objects in one source become one read that joins them there, so a path
        # through three PostgreSQL tables is one query, not three reads joined later. Only the
        # hops that cross sources are left as logical joins for a staging target to perform.
        runs = _runs(nodes, source_ids)
        for start, end in runs:
            run, run_links = nodes[start:end], links[start : end - 1]
            kind = kinds[start]
            step: dict = {
                "kind": "source_read",
                "source_id": source_ids[start],
                "object_ids": run,
            }
            if len(run) == 1:
                step["object_id"] = run[0]
            if kind == "mongodb":
                step["collection"] = _mongo_name(objects[run[0]]["name"])
                step["pipeline"] = _mongo_pipeline(run, run_links, objects, fields, rows)
            elif kind in dialects:
                step["dialect"] = dialects[kind]
                step["sql"] = (
                    _sql_read(objects[run[0]], dialects[kind], rows)
                    if len(run) == 1
                    else _sql_join(run, run_links, objects, fields, dialects[kind], rows)
                )
                step["parameters"] = []
            else:
                raise ValueError("UNSUPPORTED_SOURCE_KIND")
            steps.append(step)
        for (_, end), (next_start, _) in zip(runs, runs[1:], strict=False):
            left, right, edge = nodes[end - 1], nodes[next_start], links[end - 1]
            _pairs(edge, fields, left, right)
            steps.append(
                {
                    "kind": "logical_join",
                    "left_object_id": left,
                    "right_object_id": right,
                    "field_pairs": edge.get("field_pairs", []),
                    "evidence_ids": edge["evidence_ids"],
                }
            )
        unresolved = ["approved_staging_or_federated_target", "final_cross_source_join"]
        status = "unresolved"
    elif kinds[0] == "mongodb":
        steps.append(
            {
                "kind": "mongo_aggregation",
                "source_id": source_ids[0],
                "collection": objects[nodes[0]]["name"],
                "pipeline": _mongo_pipeline(nodes, links, objects, fields, rows),
            }
        )
        status = "review_required" if path["status"] == "tentative" else "ready_for_review"
    elif kinds[0] in dialects:
        dialect = dialects[kinds[0]]
        steps.append(
            {
                "kind": "sql_read",
                "source_id": source_ids[0],
                "dialect": dialect,
                "sql": _sql_join(nodes, links, objects, fields, dialect, rows),
                "parameters": [],
            }
        )
        status = "review_required" if path["status"] == "tentative" else "ready_for_review"
    else:
        raise ValueError("UNSUPPORTED_SOURCE_KIND")
    if path["status"] == "tentative":
        warnings.append("candidate relationship requires human review")
    if graph["completeness"] == "partial":
        warnings.append("catalog snapshot is partial")
    return {
        "ir_version": graph["ir_version"],
        "plan_id": hashlib.sha256(
            json.dumps([request.path_id, rows], separators=(",", ":")).encode()
        ).hexdigest(),
        "snapshot_digest": graph["snapshot_digest"],
        "path_id": request.path_id,
        "object_ids": nodes,
        "edge_ids": path["edge_ids"],
        "evidence_ids": path["evidence_ids"],
        "source_dialects": {source: kind for source, kind in zip(source_ids, kinds, strict=True)},
        "status": status,
        "steps": steps,
        "unresolved_prerequisites": unresolved,
        "warnings": warnings,
    }
