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


def _object_fields(object_id: str, fields: dict) -> list[dict]:
    """An object's catalogued fields in column order."""
    return sorted(
        (item for item in fields.values() if item["object_id"] == object_id),
        key=lambda item: (item.get("ordinal") is None, item.get("ordinal") or 0, item["name"]),
    )


def _columns(
    run: list[str], objects: dict, fields: dict, warnings: list[str]
) -> tuple[list[exp.Expression], dict[tuple[int, str], str]]:
    """Every catalogued column by name, each with an output name unique in the result.

    `SELECT *` over a join returns one `id` per table, which a program reading the result
    cannot tell apart. Output names are `<table>__<column>`, with the path position added when
    the same table appears twice. An object with no catalogued fields falls back to its `*`,
    and the plan says so.
    """
    expressions: list[exp.Expression] = []
    names: dict[tuple[int, str], str] = {}
    used: set[str] = set()
    for index, object_id in enumerate(run):
        columns = _object_fields(object_id, fields)
        table = objects[object_id]["name"]
        if not columns:
            expressions.append(exp.Column(this=exp.Star(), table=exp.to_identifier(f"t{index}")))
            warnings.append(f"columns of {table} are not catalogued; selected with *")
            continue
        for column in columns:
            alias = f"{table}__{column['name']}"
            if alias in used:
                alias = f"{table}_{index}__{column['name']}"
            used.add(alias)
            names[(index, column["name"])] = alias
            expressions.append(
                exp.alias_(
                    exp.column(column["name"], table=f"t{index}", quoted=True), alias, quoted=True
                )
            )
    return expressions, names


def _key_filter(
    keys: list[tuple[str, str]], dialect: str, fields: dict, object_id: str
) -> exp.Expression:
    """`column = ANY(<parameter>)` per key column; never a value written into the SQL.

    PostgreSQL gets a named `%(name)s` placeholder bound to an array (psycopg and pg8000's
    `pyformat` style); ClickHouse gets its own server-side `{name:Array(T)}` parameter. A
    composite key filters each column separately: a superset of the matching rows, which the
    final cross-source join then narrows exactly.
    """
    types = {item["name"]: item.get("data_type") for item in _object_fields(object_id, fields)}
    conditions: list[exp.Expression] = []
    for column, parameter in keys:
        target = exp.column(column, table="t0", quoted=True)
        if dialect == "clickhouse":
            if not types.get(column):
                raise ValueError("UNKNOWN_KEY_TYPE")
            kind = exp.DataType.build(f"Array({types[column]})", dialect="clickhouse")
            conditions.append(exp.In(this=target, field=exp.Placeholder(this=parameter, kind=kind)))
        else:
            conditions.append(
                exp.EQ(
                    this=target,
                    expression=exp.Anonymous(
                        this="ANY", expressions=[exp.Placeholder(this=parameter)]
                    ),
                )
            )
    return conditions[0] if len(conditions) == 1 else exp.and_(*conditions)


def _checked(query: exp.Select, dialect: str, names: set[str]) -> str:
    sql = query.sql(dialect=dialect)
    parsed = parse_one(sql, read=dialect)
    if not isinstance(parsed, exp.Select) or {
        table.name for table in parsed.find_all(exp.Table)
    } != names:
        raise ValueError("UNSAFE_PLAN_AST")
    return sql


def _sql_join(
    run: list[str],
    links: list[dict],
    objects: dict,
    fields: dict,
    dialect: str,
    rows: int,
    warnings: list[str],
    keys: list[tuple[str, str]] | None = None,
) -> tuple[str, dict[tuple[int, str], str]]:
    """One read-only SELECT over consecutive objects of one SQL source, AST-checked.

    `keys` restricts the first object to the join keys an earlier read returned, as bound
    parameters, so a later read in a cross-source plan fetches the matching rows rather than an
    unrelated sample.
    """
    columns, names = _columns(run, objects, fields, warnings)
    query = exp.select(*columns).from_(_table(objects[run[0]], "t0"))
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
    if keys:
        query = query.where(_key_filter(keys, dialect, fields, run[0]))
    sql = _checked(query.limit(rows), dialect, {objects[node]["name"] for node in run})
    return sql, names


def _mongo_pipeline(
    run: list[str],
    links: list[dict],
    objects: dict,
    fields: dict,
    rows: int,
    keys: list[tuple[str, str]] | None = None,
) -> list[dict]:
    """A `$lookup` chain over consecutive collections of one MongoDB source.

    `keys` restricts the first collection to join keys from an earlier read. MongoDB binds
    them through the aggregate command's `let` option, read here as `$$<name>`, so no value is
    written into the pipeline.
    """
    _mongo_name(objects[run[0]]["name"])
    pipeline: list[dict] = []
    for field_name, parameter in keys or []:
        pipeline.append(
            {"$match": {"$expr": {"$in": [f"${_mongo_name(field_name)}", f"$${parameter}"]}}}
        )
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
        # Each read after the first is restricted to the join keys the read before it returned,
        # bound as parameters, so the final cross-source join is over matching rows rather than
        # unrelated samples. The planner still executes nothing.
        runs = _runs(nodes, source_ids)
        outputs: list[dict[tuple[int, str], str]] = []
        for position, (start, end) in enumerate(runs):
            run, run_links = nodes[start:end], links[start : end - 1]
            kind = kinds[start]
            step: dict = {
                "kind": "source_read",
                "source_id": source_ids[start],
                "object_ids": run,
            }
            if len(run) == 1:
                step["object_id"] = run[0]
            keys: list[tuple[str, str]] = []
            parameters: list[dict] = []
            if position:
                left, edge = nodes[start - 1], links[start - 1]
                previous_start, previous_end = runs[position - 1]
                for number, (left_field, right_field) in enumerate(
                    _pairs(edge, fields, left, run[0]), start=1
                ):
                    name = f"keys_{position}_{number}"
                    keys.append((right_field, name))
                    left_index = previous_end - 1 - previous_start
                    parameters.append(
                        {
                            "name": name,
                            "filters_field": right_field,
                            "values_from_step": position - 1,
                            "values_from_column": outputs[position - 1].get(
                                (left_index, left_field), left_field
                            ),
                        }
                    )
            if kind == "mongodb":
                step["collection"] = _mongo_name(objects[run[0]]["name"])
                step["pipeline"] = _mongo_pipeline(run, run_links, objects, fields, rows, keys)
                outputs.append(
                    {
                        (index, item["name"]): (
                            item["name"] if index == 0 else f"joined_{index}.{item['name']}"
                        )
                        for index, object_id in enumerate(run)
                        for item in _object_fields(object_id, fields)
                    }
                )
                if parameters:
                    step["parameter_binding"] = "aggregate let"
            elif kind in dialects:
                step["dialect"] = dialects[kind]
                step["sql"], names = _sql_join(
                    run, run_links, objects, fields, dialects[kind], rows, warnings, keys
                )
                outputs.append(names)
                if parameters:
                    step["parameter_binding"] = (
                        "server-side query parameters"
                        if kind == "clickhouse"
                        else "named pyformat placeholders, each bound to an array"
                    )
            else:
                raise ValueError("UNSUPPORTED_SOURCE_KIND")
            step["parameters"] = parameters
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
        warnings.append(
            f"each read returns at most {rows} rows; a later read that returns exactly that many "
            "may be incomplete, so raise max_rows"
        )
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
                "sql": _sql_join(nodes, links, objects, fields, dialect, rows, warnings)[0],
                "parameters": [],
            }
        )
        status = "review_required" if path["status"] == "tentative" else "ready_for_review"
    else:
        raise ValueError("UNSUPPORTED_SOURCE_KIND")
    if path["status"] == "tentative":
        warnings.append("candidate relationship requires human review")
    if any(link["status"] == "observed" for link in links):
        warnings.append(
            "a hop comes from a join seen in query logs, not from a declared key; check that "
            "the join is meant, not incidental"
        )
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
