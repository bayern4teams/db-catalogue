"""Small strict boundary models; additive minor fields remain readable."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

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
        if value.utcoffset() != timedelta(0):
            # Canonical bytes are the unit of digests, signatures and diffs, and the same
            # instant written at a different offset serialises differently. Refusing a non-UTC
            # offset keeps one instant to one encoding. Normalising instead would accept such a
            # record and then fail its signature check, which reports the wrong cause.
            raise ValueError("timestamp must be UTC so one instant has one canonical encoding")
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
    tags: list[str] = PydanticField(default_factory=list)
    capabilities: list[str] = PydanticField(default_factory=list)


class Field(Record):
    record_type: Literal["field"] = "field"
    object_id: str
    native_id: str = PydanticField(min_length=1)
    name: str
    data_type: str
    nullable: bool | None = None
    ordinal: int | None = None
    attributes: dict[str, str | int | float | bool | None] = PydanticField(default_factory=dict)
    tags: list[str] = PydanticField(default_factory=list)
    capabilities: list[str] = PydanticField(default_factory=list)


# A snapshot node row is exactly one of the three source-derived record types the assembler
# carries into `nodes.jsonl`. Validating against this union instead of the permissive base
# `Record` refuses an unknown `record_type` and enforces each type's own required fields.
SnapshotNode = Annotated[
    Source | Object | Field, PydanticField(discriminator="record_type")
]


class Constraint(Record):
    record_type: Literal["constraint"] = "constraint"
    native_id: str
    kind: Literal["primary", "unique", "foreign", "index"]
    object_id: str
    target_object_id: str | None = None
    field_ordinals: list[int] = PydanticField(default_factory=list)
    target_field_ordinals: list[int] = PydanticField(default_factory=list)
    field_names: list[str] = PydanticField(default_factory=list)


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


class Diagnostic(Record):
    """A machine-readable reason the collector could not carry something into the bundle.

    `code` is required and constrained so a bundle's diagnostic summary is well defined: the
    per-code counts in the manifest can only add up to the row count if every row has exactly one
    stable code. `message` stays free text for an operator and is never required to parse.
    """

    record_type: Literal["diagnostic"] = "diagnostic"
    code: str = PydanticField(min_length=1, max_length=64, pattern=r"^[A-Z][A-Z0-9_]*$")
    message: str | None = None


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
    # Counts of `diagnostics.jsonl` rows by code, so a reader sees what a run could not carry
    # without parsing the file. The writer derives it and a reader verifies it against the actual
    # rows, so it cannot disagree with them. It defaults to `None` rather than `{}` because
    # `_unsigned` excludes `None`: an absent summary keeps an older producer's signature valid,
    # and a reader that finds none treats the summary as unreported rather than as zero.
    diagnostics: dict[str, int] | None = None
    signature: Signature | None = None

    @field_validator("diagnostics")
    @classmethod
    def sane_counts(cls, value: dict[str, int] | None) -> dict[str, int] | None:
        if value is not None and any(count < 0 for count in value.values()):
            raise ValueError("diagnostic counts cannot be negative")
        return value

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
