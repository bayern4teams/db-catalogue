"""Small strict boundary models; additive minor fields remain readable."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, field_validator
from pydantic import Field as PydanticField

IR_VERSION = "1.0"
# Longest source comment carried; a longer one is cut by the collector and says so.
DESCRIPTION_MAX_LENGTH = 1000


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
    # How the collector reached the source. Optional and additive: a bundle from an older
    # collector has none, and `None` is excluded from canonical bytes so its signature still holds.
    transport: Literal["tls-verified", "plaintext-loopback"] | None = None


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
    # The comment a database owner wrote on the object. Free text from the source, so untrusted:
    # escaped wherever it is shown and marked as data wherever a model sees it. Optional and
    # additive; `None` is excluded from canonical bytes, so older artifacts keep their digests.
    description: str | None = PydanticField(default=None, max_length=DESCRIPTION_MAX_LENGTH)


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
    # The column's comment; see `Object.description`.
    description: str | None = PydanticField(default=None, max_length=DESCRIPTION_MAX_LENGTH)


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
    # Strength of the measured evidence for a REFERENCE_CANDIDATE (F11-b); None on other kinds and
    # on snapshots written before it existed.
    evidence_tier: Literal["verified", "plausible", "unverified_cross_source"] | None = None


class Profile(Record):
    """A bounded statistical description of one field, carrying its own provenance.

    Every measure is optional, because a source may expose some and not others, and a missing
    measure must stay distinguishable from a measured zero. `method` names where the numbers came
    from so a reader can judge them: a planner statistic is an estimate that may be stale, and a
    sample is bounded and possibly biased. No raw value ever appears here.
    """

    record_type: Literal["profile"] = "profile"
    field_id: str = PydanticField(min_length=1)
    object_id: str = PydanticField(min_length=1)
    # How these numbers were obtained. Source statistics read no user data at all, which is why
    # they are allowed under the metadata-only default; a sample reads data and is opt-in.
    method: Literal["source_statistic", "sample"]
    # Freshness matters for a planner statistic: unchanged DDL does not mean unchanged data, and
    # a statistic is only as good as the last analyze.
    stats_updated_at: datetime | None = None
    sample_count: int | None = PydanticField(default=None, ge=0)
    null_fraction: float | None = PydanticField(default=None, ge=0.0, le=1.0)
    # Absolute estimated distinct values. A source that reports a ratio is converted here, and
    # `distinct_is_ratio_derived` records that the absolute number came from a row-count estimate
    # and inherits its error.
    distinct_estimate: float | None = PydanticField(default=None, ge=0.0)
    distinct_is_ratio_derived: bool = False
    # Distinct values per row, in [0, 1]. A value near 1 is the uniqueness cue that makes a field
    # a plausible parent key in reference inference.
    uniqueness: float | None = PydanticField(default=None, ge=0.0, le=1.0)
    average_width_bytes: int | None = PydanticField(default=None, ge=0)
    # Set when policy excludes this field from value-based comparison.
    value_comparison_excluded: bool = False

    @field_validator("stats_updated_at")
    @classmethod
    def aware_stats_time(cls, value: datetime | None) -> datetime | None:
        return Record.aware_time(value) if value is not None else None


class ValueTokens(Record):
    """Keyed one-way tokens for one field's sampled values, for cross-source comparison only.

    A plain hash is not enough: low-entropy values such as small integers or country codes are
    trivially recovered from an unkeyed digest by trying every candidate. The tokens here are
    HMAC-SHA256 under a key that exists for one run, so they are comparable within that run and
    meaningless outside it, and the key itself never enters the artifact.

    Tokens live in a bundle because a bundle is a controlled transfer, not the published graph.
    They are their own file so an operator can see from the manifest whether a bundle carries
    them at all, and so a deployment can choose not to transport it. Nothing derived from them
    reaches a snapshot except aggregate metrics.
    """

    record_type: Literal["value_tokens"] = "value_tokens"
    field_id: str = PydanticField(min_length=1)
    object_id: str = PydanticField(min_length=1)
    algorithm: Literal["hmac-sha256"] = "hmac-sha256"
    # Identifies the run key. Two token sets are comparable only when this matches, so the
    # assembler refuses to compare across different key IDs rather than reporting a meaningless
    # zero overlap.
    key_id: str = PydanticField(min_length=1)
    # How a value was turned into bytes before keying. Recorded because a comparison between two
    # differently normalised sides is not a comparison at all.
    normalization: Literal["exact_text"] = "exact_text"
    # Rows or documents examined, and how they were chosen, so a reader can judge the sample.
    sample_count: int = PydanticField(ge=0)
    sample_method: str = PydanticField(min_length=1)
    # Non-null values seen, and how many were distinct, both before any cap was applied. These
    # are the denominators for containment, so they must describe the sample and not the cap.
    observed_count: int = PydanticField(ge=0)
    distinct_count: int = PydanticField(ge=0)
    # True when `tokens` holds fewer than `distinct_count` entries, which makes containment a
    # lower bound rather than a measurement.
    truncated: bool = False
    # True when the read saw the whole object: fewer rows came back than were asked for and no
    # byte budget cut it. Only then is a value missing from these tokens absent from the object,
    # so only then may a zero overlap against it be read as a measurement. Absent in a bundle
    # written before the field existed, which therefore counts as a sample.
    complete: bool = False
    tokens: list[str] = PydanticField(default_factory=list)

    @field_validator("tokens")
    @classmethod
    def sorted_hex_digests(cls, value: list[str]) -> list[str]:
        if any(len(item) != 64 or not all(c in "0123456789abcdef" for c in item) for item in value):
            raise ValueError("a value token must be a lowercase sha256 hex digest")
        if len(set(value)) != len(value):
            raise ValueError("value tokens must be distinct")
        # Sorted so the file's bytes, and therefore its digest, do not depend on scan order.
        return sorted(value)


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
    # Why a snapshot is `partial`, as code names. A snapshot carries no `diagnostics.jsonl`, so its
    # reasons cannot ride in `diagnostics` (the reader checks that against the file). Additive and
    # `None` when absent, so an older reader ignores it and a complete snapshot is unchanged.
    partial_reasons: list[str] | None = None
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
