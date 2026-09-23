"""Requester runtime configuration without embedded secrets."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal

from dbig_contracts.security import private_base_url
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AnswerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str
    model: str = Field(min_length=1)
    model_digest: str = Field(min_length=1)
    api_key_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    timeout_seconds: int = Field(default=20, ge=1)
    max_tokens: int = Field(default=512, ge=1)
    retrieval_limit: int = Field(default=10, ge=1, le=100)
    langfuse_url: str | None = None
    langfuse_public_key_env: str | None = None
    langfuse_secret_key_env: str | None = None

    @field_validator("base_url", "langfuse_url")
    @classmethod
    def private_url(cls, value: str | None) -> str | None:
        return private_base_url(value) if value is not None else None

    @model_validator(mode="after")
    def telemetry_credentials(self) -> AnswerConfig:
        if self.langfuse_url and not (
            self.langfuse_public_key_env and self.langfuse_secret_key_env
        ):
            raise ValueError("Langfuse URL requires public and secret key references")
        return self


class ScanConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signing_key_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    key_id: str = Field(min_length=1)
    allowed_sources: dict[str, list[str]] = Field(min_length=1)
    allowed_capabilities: list[Literal["metadata", "sample", "history"]] = ["metadata"]
    handoff_root: Path | None = None
    collector_urls: dict[str, str] = Field(default_factory=dict)
    http_token_env: str | None = None
    http_timeout_seconds: int = Field(default=10, ge=1)

    @field_validator("collector_urls")
    @classmethod
    def private_collectors(cls, value: dict[str, str]) -> dict[str, str]:
        return {source: private_base_url(url) for source, url in value.items()}


class RequesterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_dir: Path
    state_dir: Path
    api_key_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    max_requests_per_minute: int = Field(default=60, ge=1)
    answer: AnswerConfig | None = None
    scan: ScanConfig | None = None


def load_config(path: Path) -> RequesterConfig:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    if set(data) != {"requester"}:
        raise ValueError("configuration must contain only [requester]")
    return RequesterConfig.model_validate(data["requester"])
