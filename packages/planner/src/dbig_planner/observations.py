"""Conservative observation intake; raw SQL is parsed and then discarded."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, NamedTuple

import sqlglot
from dbig_contracts.errors import OperatorError
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlglot import exp


class SQLObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["sql"]
    id: str = Field(min_length=1)
    source_id: str
    dialect: Literal["postgres", "clickhouse"]
    sql: str = Field(min_length=1, max_length=65_536)
    observed_at: datetime
    status: Literal["executed", "defined"]

    @field_validator("observed_at")
    @classmethod
    def aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observation timestamp must have a timezone")
        return value


class PipelineObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["pipeline"]
    id: str = Field(min_length=1)
    input_object_ids: list[str] = Field(min_length=1)
    output_object_id: str
    observed_at: datetime
    status: Literal["executed", "defined"]

    @field_validator("observed_at")
    @classmethod
    def aware_time(cls, value: datetime) -> datetime:
        return SQLObservation.aware_time(value)


class DatasetMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_to_object_id: dict[str, str] = Field(default_factory=dict)


class SQLRelation(NamedTuple):
    name: str
    namespace: str | None


class OpenLineageDataset(BaseModel):
    model_config = ConfigDict(extra="allow")

    namespace: str = Field(min_length=1)
    name: str = Field(min_length=1)
    facets: dict[str, Any] = Field(default_factory=dict)


class OpenLineageDatasetRef(OpenLineageDataset):
    type: Literal["DATASET"]
    field: str | None = Field(default=None, min_length=1)


class OpenLineageFieldEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    inputs: list[OpenLineageDatasetRef] = Field(default_factory=list)


class OpenLineageLineageEntry(OpenLineageDatasetRef):
    inputs: list[OpenLineageDatasetRef] = Field(default_factory=list)
    fields: dict[str, OpenLineageFieldEntry] = Field(default_factory=dict)


class OpenLineageDatasetFacet(BaseModel):
    model_config = ConfigDict(extra="allow")

    inputs: list[OpenLineageDatasetRef] = Field(default_factory=list)
    fields: dict[str, OpenLineageFieldEntry] = Field(default_factory=dict)


class OpenLineageColumnInput(OpenLineageDataset):
    field: str = Field(min_length=1)


class OpenLineageColumnEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    inputFields: list[OpenLineageColumnInput] = Field(default_factory=list)


class OpenLineageColumnFacet(BaseModel):
    model_config = ConfigDict(extra="allow")

    fields: dict[str, OpenLineageColumnEntry] = Field(default_factory=dict)
    dataset: list[OpenLineageColumnInput] = Field(default_factory=list)


class OpenLineageEvent(BaseModel):
    model_config = ConfigDict(extra="allow")

    eventType: Literal["START", "RUNNING", "COMPLETE", "ABORT", "FAIL", "OTHER"] | None = None
    eventTime: datetime
    job: dict
    run: dict | None = None
    inputs: list[OpenLineageDataset] = Field(default_factory=list)
    outputs: list[OpenLineageDataset] = Field(default_factory=list)

    @field_validator("eventTime")
    @classmethod
    def aware_time(cls, value: datetime) -> datetime:
        return SQLObservation.aware_time(value)

    @model_validator(mode="after")
    def required_identifiers(self) -> OpenLineageEvent:
        if not self.job.get("namespace") or not self.job.get("name"):
            raise ValueError("OpenLineage job identity required")
        if self.eventType is not None and (not self.run or not self.run.get("runId")):
            raise ValueError("OpenLineage run identity required")
        return self


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("DUPLICATE_OBSERVATION_KEY")
        result[key] = value
    return result


def read_lines(directory: Path, max_bytes: int, max_records: int) -> list[dict]:
    return [record for _, record in read_located_lines(directory, max_bytes, max_records)]


def read_located_lines(
    directory: Path, max_bytes: int, max_records: int
) -> list[tuple[str, dict]]:
    """Each record with `file:line`, so a bad record can be named without repeating it."""
    records: list[tuple[str, dict]] = []
    if directory.is_symlink():
        raise ValueError("UNSAFE_OBSERVATION_DIRECTORY")
    if not directory.exists():
        return records
    if not directory.is_dir():
        raise ValueError("UNSAFE_OBSERVATION_DIRECTORY")
    total_bytes = 0
    file_count = 0
    for path in sorted(directory.glob("*.jsonl")):
        file_count += 1
        if file_count > max_records:
            raise ValueError("OBSERVATION_LIMIT")
        remaining = max_bytes - total_bytes
        if path.is_symlink() or not path.is_file() or path.stat().st_size > remaining:
            raise ValueError("UNSAFE_OR_OVERSIZED_OBSERVATIONS")
        with path.open("rb") as stream:
            raw = stream.read(remaining + 1)
        if len(raw) > remaining:
            raise ValueError("UNSAFE_OR_OVERSIZED_OBSERVATIONS")
        total_bytes += len(raw)
        for number, line in enumerate(raw.splitlines(), start=1):
            if len(line) > max_bytes or len(records) >= max_records:
                raise ValueError("OBSERVATION_LIMIT")
            location = f"{path.name}:{number}"
            try:
                record = json.loads(
                    line,
                    object_pairs_hook=_unique_pairs,
                    parse_constant=lambda value: (_ for _ in ()).throw(
                        ValueError("INVALID_NUMBER")
                    ),
                )
            except ValueError as error:
                # JSONDecodeError's text quotes only a position; the other two are our codes.
                raise OperatorError(
                    f"observation {location} is not a valid JSON record: {error}"
                ) from error
            if not isinstance(record, dict):
                raise OperatorError(
                    f"INVALID_OBSERVATION: observation {location} is not a JSON object"
                )
            records.append((location, record))
    return records


def sql_relations(
    statement: str, dialect: str
) -> tuple[SQLRelation | None, list[SQLRelation], list[tuple[SQLRelation, SQLRelation]]]:
    """Return target, reads and joins only for a single supported statement."""
    parsed = sqlglot.parse(statement, read=dialect)
    if len(parsed) != 1 or parsed[0] is None:
        raise ValueError("UNRESOLVED_SQL")
    tree = parsed[0]
    target: exp.Table | None = None
    if isinstance(tree, exp.Insert):
        node = tree.this
        target = node if isinstance(node, exp.Table) else node.find(exp.Table)
    elif isinstance(tree, exp.Create) and str(tree.args.get("kind", "")).upper() in {
        "VIEW",
        "MATERIALIZED VIEW",
        "TABLE",
    }:
        target = tree.this if isinstance(tree.this, exp.Table) else None
    elif not isinstance(tree, exp.Select):
        raise ValueError("UNSUPPORTED_SQL_KIND")
    if isinstance(tree, exp.Create) and tree.find(exp.ToTableProperty):
        raise ValueError("UNRESOLVED_CLICKHOUSE_MV_TARGET")
    if any(
        node is not tree
        for node in tree.find_all(exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create)
    ):
        raise ValueError("UNSUPPORTED_NESTED_WRITE")
    cte_names = {cte.alias_or_name for cte in tree.find_all(exp.CTE)}

    def base_relation(table: exp.Table, *, target_table: bool = False) -> SQLRelation | None:
        if table.catalog:
            raise ValueError("UNRESOLVED_SQL_CATALOG")
        if not target_table and not table.db and table.name in cte_names:
            return None
        return SQLRelation(table.name, table.db or None)

    reads = [
        relation
        for table in tree.find_all(exp.Table)
        if table is not target
        for relation in [base_relation(table)]
        if relation is not None
    ]
    names = list(dict.fromkeys(reads))
    joins: list[tuple[SQLRelation, SQLRelation]] = []
    for select in tree.find_all(exp.Select):
        primary = select.args.get("from_")
        left = primary.this if primary is not None else None
        for join in select.args.get("joins") or []:
            if (
                isinstance(left, exp.Table)
                and isinstance(join.this, exp.Table)
                and join.args.get("on")
            ):
                left_ref = base_relation(left)
                right_ref = base_relation(join.this)
                if left_ref is not None and right_ref is not None:
                    joins.append((left_ref, right_ref))
            left = join.this
    return (base_relation(target, target_table=True) if target is not None else None, names, joins)


def observed_join_columns(
    statement: str, dialect: str
) -> list[tuple[SQLRelation, SQLRelation, list[tuple[str, str]]]]:
    """Each pair of tables an `ON` clause joins, with the column pairs it equates.

    Read from `ON a.x = b.y [AND ...]` only, with every column qualified by a table or its alias
    in the same SELECT: the two tables come from the columns, not from the order in `FROM`, so
    `A JOIN B ON .. JOIN C ON a.k = c.k` links A and C. An `ON` holding an OR, a non-equality or
    an unqualified column says nothing certain about which columns join, so it is skipped
    rather than guessed. Raises like `sql_relations` for SQL it cannot read.
    """
    parsed = sqlglot.parse(statement, read=dialect)
    if len(parsed) != 1 or parsed[0] is None:
        raise ValueError("UNRESOLVED_SQL")
    tree = parsed[0]
    cte_names = {cte.alias_or_name for cte in tree.find_all(exp.CTE)}
    found: dict[tuple[SQLRelation, SQLRelation], list[tuple[str, str]]] = {}
    for select in tree.find_all(exp.Select):
        sources: list[exp.Expression] = []
        primary = select.args.get("from_")
        if primary is not None:
            sources.append(primary.this)
        joins = select.args.get("joins") or []
        sources.extend(join.this for join in joins)
        aliases: dict[str, SQLRelation] = {}
        for table in sources:
            if not isinstance(table, exp.Table) or table.catalog:
                continue
            if not table.db and table.name in cte_names:
                continue
            aliases[table.alias_or_name] = SQLRelation(table.name, table.db or None)
        for join in joins:
            condition = join.args.get("on")
            if condition is None or condition.find(exp.Or):
                continue
            terms = list(condition.flatten()) if isinstance(condition, exp.And) else [condition]
            pairs: dict[tuple[SQLRelation, SQLRelation], list[tuple[str, str]]] = {}
            for term in terms:
                if isinstance(term, exp.Paren):
                    term = term.this
                if not (
                    isinstance(term, exp.EQ)
                    and isinstance(term.this, exp.Column)
                    and isinstance(term.expression, exp.Column)
                ):
                    continue
                left = aliases.get(term.this.table)
                right = aliases.get(term.expression.table)
                if left is None or right is None or left == right:
                    continue
                pairs.setdefault((left, right), []).append(
                    (term.this.name, term.expression.name)
                )
            for key, columns in pairs.items():
                existing = found.setdefault(key, [])
                existing.extend(column for column in columns if column not in existing)
    return [(left, right, columns) for (left, right), columns in found.items()]


def observation_digest(record: dict) -> str:
    return hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()
