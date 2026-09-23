"""Small strict boundary models; additive minor fields remain readable."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator
from pydantic import Field as PydanticField

IR_VERSION = "1.0"


class Record(BaseModel):
    model_config = ConfigDict(extra="allow")

    ir_version: str = IR_VERSION
    record_type: str
    id: str = PydanticField(min_length=1, max_length=256)
    source_id: str = PydanticField(min_length=1, max_length=256)
    observed_at: datetime
    collector_version: str = PydanticField(min_length=1)
    provenance: str = PydanticField(min_length=1)

    @field_validator("ir_version")
    @classmethod
    def compatible_version(cls, value: str) -> str:
        major, separator, minor = value.partition(".")
        if not separator or not major.isdecimal() or not minor.isdecimal() or int(major) != 1:
            raise ValueError("unsupported IR major version")
        return value

    @field_validator("observed_at")
    @classmethod
    def aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value


class Source(Record):
    record_type: Literal["source"] = "source"
    kind: Literal["postgresql", "clickhouse", "mongodb"]
    name: str


class Object(Record):
    record_type: Literal["object"] = "object"
    native_id: str = PydanticField(min_length=1)
    kind: str
    name: str
    namespace: str | None = None
    parent_id: str | None = None
    attributes: dict[str, str | int | float | bool | None] = PydanticField(default_factory=dict)


class Field(Record):
    record_type: Literal["field"] = "field"
    object_id: str
    native_id: str = PydanticField(min_length=1)
    name: str
    data_type: str
    nullable: bool | None = None
    ordinal: int | None = None
    attributes: dict[str, str | int | float | bool | None] = PydanticField(default_factory=dict)


class Evidence(Record):
    record_type: Literal["evidence"] = "evidence"
    kind: str
    subject_id: str
    object_id: str | None = None
    metrics: dict[str, str | int | float | bool | None] = PydanticField(default_factory=dict)
    explanation: str = ""


class Edge(Record):
    record_type: Literal["edge"] = "edge"
    kind: Literal[
        "CONTAINS",
        "DECLARES_FK",
        "REFERENCE_CANDIDATE",
        "JOINS_OBSERVED",
        "WRITES",
        "READS",
        "DERIVES_FROM",
        "RUNS_JOB",
    ]
    from_id: str
    to_id: str
    status: Literal["declared", "strong", "tentative", "rejected", "observed"]
    evidence_ids: list[str] = PydanticField(min_length=1)
    explanation: str = ""
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    limitations: list[str] = PydanticField(default_factory=list)
    confidence: float | None = None


class FileEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    bytes: int = PydanticField(ge=0)
    sha256: str = PydanticField(pattern=r"^[0-9a-f]{64}$")
    count: int = PydanticField(ge=0)


class Signature(BaseModel):
    algorithm: Literal["hmac-sha256"] = "hmac-sha256"
    key_id: str
    value: str = PydanticField(pattern=r"^[0-9a-f]{64}$")


class Manifest(BaseModel):
    model_config = ConfigDict(extra="allow")

    ir_version: str = IR_VERSION
    content_type: Literal["collector-bundle", "catalog-snapshot"]
    producer: str
    source_run_ids: list[str]
    created_at: datetime
    as_of: datetime
    completeness: Literal["complete", "partial"]
    files: list[FileEntry]
    classification: str = "metadata"
    signature: Signature | None = None

    @field_validator("ir_version")
    @classmethod
    def compatible_version(cls, value: str) -> str:
        return Record.compatible_version(value)

    @field_validator("created_at", "as_of")
    @classmethod
    def aware_time(cls, value: datetime) -> datetime:
        return Record.aware_time(value)


def utc_now() -> datetime:
    return datetime.now(UTC)
