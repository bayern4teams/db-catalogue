"""Assembler policy and key references."""

from pathlib import Path

from dbig_contracts.errors import load_toml_section, validate_configuration
from dbig_contracts.security import private_base_url, secret_from_env
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ReviewConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str
    model: str = Field(min_length=1)
    model_digest: str = Field(min_length=1)
    api_key_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    timeout_seconds: int = Field(default=20, ge=1)
    max_tokens: int = Field(default=512, ge=1)
    max_candidates: int = Field(default=20, ge=1)
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


class BuilderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signing_keys: dict[str, str] = Field(min_length=1)
    name_similarity_threshold: int = Field(default=80, ge=0, le=100)
    top_k_per_field: int = Field(default=5, ge=1)
    min_token_support: int = Field(default=MIN_TOKEN_SUPPORT, ge=1)
    # Operator knowledge of which sources hold the same dataset (user decision 2026-10-13, DEC-3).
    # Both default to none, which changes nothing. With groups set, two sources are paired only
    # when they share a group; a source in no group is unrestricted. A forbidden pair is never
    # paired, in either direction.
    source_groups: list[list[str]] = Field(default_factory=list)
    forbidden_source_pairs: list[tuple[str, str]] = Field(default_factory=list)
    review: ReviewConfig | None = None

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
        env_name = self.signing_keys[key_id]
        value = secret_from_env(
            env_name, purpose="collector bundle signature verification"
        ).encode("utf-8")
        if len(value) < 32:
            raise ValueError("bundle signing key too short")
        return value


def load_config(path: Path) -> BuilderConfig:
    return validate_configuration(path, BuilderConfig, load_toml_section(path, "builder"))
