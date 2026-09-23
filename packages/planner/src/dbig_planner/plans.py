"""Read-only query rendering from catalog IDs; never opens a source connection."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

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


def render_plan(directory: Path, request: PlanRequest, config: PlannerConfig) -> dict:
    graph = load_graph(directory)
    path = load_path(directory, request.path_id)
    if path["snapshot_digest"] != graph["snapshot_digest"] or path["kind"] != "join":
        raise ValueError("STALE_OR_NON_JOIN_PATH")
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
        for node, kind in zip(nodes, kinds, strict=True):
            item = objects[node]
            if kind == "mongodb":
                steps.append(
                    {
                        "kind": "source_read",
                        "source_id": item["source_id"],
                        "object_id": node,
                        "collection": _mongo_name(item["name"]),
                        "pipeline": [{"$limit": rows}],
                    }
                )
            elif kind in dialects:
                steps.append(
                    {
                        "kind": "source_read",
                        "source_id": item["source_id"],
                        "object_id": node,
                        "dialect": dialects[kind],
                        "sql": _sql_read(item, dialects[kind], rows),
                        "parameters": [],
                    }
                )
            else:
                raise ValueError("UNSUPPORTED_SOURCE_KIND")
        for left, right, edge in zip(nodes, nodes[1:], links, strict=False):
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
        first = objects[nodes[0]]
        _mongo_name(first["name"])
        pipeline: list[dict] = []
        for index, (right, edge) in enumerate(zip(nodes[1:], links, strict=True), start=1):
            pairs = _pairs(edge, fields, nodes[index - 1], right)
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
        steps.append(
            {
                "kind": "mongo_aggregation",
                "source_id": source_ids[0],
                "collection": first["name"],
                "pipeline": pipeline,
            }
        )
        status = "review_required" if path["status"] == "tentative" else "ready_for_review"
    elif kinds[0] in dialects:
        dialect = dialects[kinds[0]]
        query = exp.select(exp.Star()).from_(_table(objects[nodes[0]], "t0"))
        for index, (right, edge) in enumerate(zip(nodes[1:], links, strict=True), start=1):
            pairs = _pairs(edge, fields, nodes[index - 1], right)
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
        } != {objects[node]["name"] for node in nodes}:
            raise ValueError("UNSAFE_PLAN_AST")
        steps.append(
            {
                "kind": "sql_read",
                "source_id": source_ids[0],
                "dialect": dialect,
                "sql": sql,
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
