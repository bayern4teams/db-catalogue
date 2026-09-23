"""Conservative observation intake; raw SQL is parsed and then discarded."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Literal

import sqlglot
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


class OpenLineageDataset(BaseModel):
    model_config = ConfigDict(extra="allow")

    namespace: str = Field(min_length=1)
    name: str = Field(min_length=1)


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
    records: list[dict] = []
    if not directory.exists():
        return records
    for path in sorted(directory.glob("*.jsonl")):
        if path.is_symlink() or path.stat().st_size > max_bytes:
            raise ValueError("UNSAFE_OR_OVERSIZED_OBSERVATIONS")
        for line in path.read_bytes().splitlines():
            if len(line) > max_bytes or len(records) >= max_records:
                raise ValueError("OBSERVATION_LIMIT")
            record = json.loads(
                line,
                object_pairs_hook=_unique_pairs,
                parse_constant=lambda value: (_ for _ in ()).throw(ValueError("INVALID_NUMBER")),
            )
            if not isinstance(record, dict):
                raise ValueError("INVALID_OBSERVATION")
            records.append(record)
    return records


def sql_relations(
    statement: str, dialect: str
) -> tuple[str | None, list[str], list[tuple[str, str]]]:
    """Return target, reads and joins only for a single supported statement."""
    parsed = sqlglot.parse(statement, read=dialect)
    if len(parsed) != 1 or parsed[0] is None:
        raise ValueError("UNRESOLVED_SQL")
    tree = parsed[0]
    if tree.find(exp.CTE) or tree.find(exp.Subquery):
        raise ValueError("UNRESOLVED_NESTED_SQL")
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
    reads = [table for table in tree.find_all(exp.Table) if table is not target]
    names = list(dict.fromkeys(table.name for table in reads))
    joins: list[tuple[str, str]] = []
    if isinstance(tree, exp.Select) or tree.find(exp.Select):
        select = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
        if select is not None:
            primary = select.args.get("from_")
            left = primary.this if primary is not None else None
            for join in select.args.get("joins") or []:
                if (
                    isinstance(left, exp.Table)
                    and isinstance(join.this, exp.Table)
                    and join.args.get("on")
                ):
                    joins.append((left.name, join.this.name))
                left = join.this
    return (target.name if target is not None else None, names, joins)


def observation_digest(record: dict) -> str:
    return hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()
