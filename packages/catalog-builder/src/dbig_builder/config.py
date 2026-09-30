"""Assembler policy and key references."""

import tomllib
from pathlib import Path

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


class BuilderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signing_keys: dict[str, str] = Field(min_length=1)
    name_similarity_threshold: int = Field(default=80, ge=0, le=100)
    top_k_per_field: int = Field(default=5, ge=1)
    review: ReviewConfig | None = None

    def key(self, key_id: str) -> bytes:
        env_name = self.signing_keys[key_id]
        value = secret_from_env(
            env_name, purpose="collector bundle signature verification"
        ).encode("utf-8")
        if len(value) < 32:
            raise ValueError("bundle signing key too short")
        return value


def load_config(path: Path) -> BuilderConfig:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    if set(data) != {"builder"}:
        raise ValueError("configuration must contain only [builder]")
    return BuilderConfig.model_validate(data["builder"])
