"""Assembler policy and key references."""

import json
from pathlib import Path
from typing import Literal

from dbig_contracts.errors import load_toml_section, validate_configuration
from dbig_contracts.security import key_from_env, private_base_url
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    TypeAdapter,
    field_validator,
    model_validator,
)


class ReviewConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str
    model: str = Field(min_length=1)
    model_digest: str = Field(min_length=1)
    api_key_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    timeout_seconds: int = Field(default=20, ge=1)
    # Unset sends no limit (owner decision DEC-18); a set value must be positive.
    max_tokens: int | None = Field(default=None, ge=1)
    max_candidates: int = Field(default=20, ge=1)
    # Candidates per model call (user decision 2026-10-13, DEC-10): a reasoning model spends its
    # token budget thinking, and 20 in one call ran out at 8192 tokens where 5 completed.
    batch_size: int = Field(default=5, ge=1)
    langfuse_url: str | None = None
    langfuse_public_key_env: str | None = None
    langfuse_secret_key_env: str | None = None

    @field_validator("base_url", "langfuse_url")
    @classmethod
    def private_url(cls, value: str | None) -> str | None:
        return private_base_url(value) if value is not None else None

    @model_validator(mode="after")
    def telemetry_credentials(self) -> "ReviewConfig":
        if self.langfuse_url and not (
            self.langfuse_public_key_env and self.langfuse_secret_key_env
        ):
            raise ValueError("Langfuse URL requires public and secret key references")
        return self


# Fewest distinct sampled values a side needs before value overlap is measured. Below it a
# candidate is kept and marked "sample too small"; at or above it, 0 shared values drops the
# candidate.
MIN_TOKEN_SUPPORT = 4
# Prefixes of the `sample_method` of a parent read that may make a zero conclusive (option B,
# DEC-39).
# No collector emits such a sample yet (option D will), so B is dormant on today's collections.
B_TRUSTED_SAMPLE_METHODS = ("random_sample", "hash_range")


class Decision(BaseModel):
    """One owner decision on a proposed candidate, by qualified name (DEC-36)."""

    model_config = ConfigDict(extra="forbid")

    child: str = Field(min_length=1)
    parent: str = Field(min_length=1)
    decision: Literal["confirmed", "rejected"]


class BuilderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signing_keys: dict[str, str] = Field(min_length=1)
    name_similarity_threshold: int = Field(default=80, ge=0, le=100)
    top_k_per_field: int = Field(default=5, ge=1)
    min_token_support: int = Field(default=MIN_TOKEN_SUPPORT, ge=1)
    # Two-sided confidence level of the Wilson interval on a measured value overlap (F14).
    overlap_confidence_level: float = Field(default=0.95, gt=0, lt=1)
    # F11-b. `c_min` is the weakest containment the catalogue promises to find: a candidate is
    # `verified` from it up, and a zero against a partly read parent is conclusive when it would
    # have shown at least `min_expected_shared` shared values at that containment.
    c_min: float = Field(default=0.25, gt=0, le=1)
    min_expected_shared: float = Field(default=5, gt=0)
    # JSON list of {"child", "parent", "decision"}, names `<source_id>.<namespace>.<table>.<column>`
    # (namespace omitted when the object has none); `confirmed` or `rejected`.
    decisions_file: Path | None = None
    # Largest decisions file accepted, in bytes; at most this many plus one are ever read.
    decisions_file_max_bytes: int = Field(default=1_048_576, ge=1)
    # Operator knowledge of which sources hold the same dataset (user decision 2026-10-13, DEC-3).
    # Both default to none, which changes nothing. With groups set, two sources are paired only
    # when they share a group; a source in no group is unrestricted. A forbidden pair is never
    # paired, in either direction.
    source_groups: list[list[str]] = Field(default_factory=list)
    forbidden_source_pairs: list[tuple[str, str]] = Field(default_factory=list)
    review: ReviewConfig | None = None
    _decisions: list[Decision] = PrivateAttr(default_factory=list)

    @model_validator(mode="after")
    def read_decisions(self) -> "BuilderConfig":
        if self.decisions_file is not None:
            # Names are never echoed: the file may name tables the operator keeps private.
            try:
                with self.decisions_file.open("rb") as handle:
                    raw = handle.read(self.decisions_file_max_bytes + 1)
                if len(raw) > self.decisions_file_max_bytes:
                    raise ValueError("decisions_file is larger than decisions_file_max_bytes")
                # utf-8-sig: an editor's byte order mark is accepted.
                self._decisions = TypeAdapter(list[Decision]).validate_python(
                    json.loads(raw.decode("utf-8-sig"))
                )
            except (OSError, ValueError):  # a pydantic ValidationError is a ValueError
                raise ValueError(
                    "decisions_file must be a JSON list of {child, parent, decision} with "
                    "decision confirmed or rejected, no larger than decisions_file_max_bytes"
                ) from None
        return self

    @property
    def decisions(self) -> list[Decision]:
        return self._decisions

    @field_validator("source_groups")
    @classmethod
    def groups_have_ids(cls, value: list[list[str]]) -> list[list[str]]:
        if any(not group or any(not item for item in group) for group in value):
            raise ValueError("a source group needs at least one non-empty source ID")
        return value

    def pair_allowed(self, left: str, right: str) -> bool:
        """Whether candidates may link fields of these two sources; always true within one."""
        if left == right:
            return True
        if (left, right) in self.forbidden_source_pairs or (
            right,
            left,
        ) in self.forbidden_source_pairs:
            return False
        grouped = [set(group) for group in self.source_groups]
        in_left = [group for group in grouped if left in group]
        in_right = [group for group in grouped if right in group]
        if not (in_left and in_right):
            return True
        return any(left in group and right in group for group in grouped)

    def key(self, key_id: str) -> bytes:
        return key_from_env(
            self.signing_keys[key_id], purpose="collector bundle signature verification"
        )


def load_config(path: Path) -> BuilderConfig:
    return validate_configuration(path, BuilderConfig, load_toml_section(path, "builder"))
