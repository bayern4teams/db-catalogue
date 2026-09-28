"""Validated source-local configuration; credentials remain in process environment."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str = Field(min_length=1)
    kind: Literal["postgresql", "clickhouse", "mongodb"]
    connection_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    signing_key_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    signing_key_id: str = Field(min_length=1)
    namespaces: list[str] = Field(min_length=1)
    tls: Literal[True] = True
    max_queries: int = Field(default=50, ge=1)
    max_objects: int = Field(default=1000, ge=1)
    max_fields: int = Field(default=10000, ge=1)
    read_timeout_seconds: int = Field(default=10, ge=1)
    sample_rows: int = Field(default=0, ge=0)
    max_sample_bytes: int = Field(default=1_048_576, ge=1)
    max_array_elements: int = Field(default=16, ge=1, le=256)
    max_path_depth: int = Field(default=4, ge=1)
    history_access: bool = False
    # Cross-source value comparison. Off unless a key reference is given *and* sampling is on,
    # because a token can only be computed from a value that was read. The key is per run, so
    # tokens are comparable within one assembly and meaningless outside it.
    value_token_key_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]*$")
    value_token_key_id: str | None = None
    max_value_tokens: int = Field(default=256, ge=1, le=4096)
    authorized_requesters: list[str] = Field(default_factory=list)
    http_token_env: str | None = None
    max_request_bytes: int = Field(default=65_536, ge=1)
    max_queued_scans: int = Field(default=20, ge=1)
    max_request_ttl_seconds: int = Field(default=3600, ge=1)

    @model_validator(mode="after")
    def token_key_needs_an_id(self) -> SourceConfig:
        if bool(self.value_token_key_env) != bool(self.value_token_key_id):
            raise ValueError(
                "value tokenisation needs both value_token_key_env and value_token_key_id: "
                "a key with no ID cannot be matched against another source, and an ID with no "
                "key would claim a comparison that was never computed"
            )
        return self

    @field_validator("namespaces")
    @classmethod
    def nonempty_namespaces(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value) or len(value) != len(set(value)):
            raise ValueError("namespaces must be nonempty and unique")
        return value


def load_config(path: Path) -> SourceConfig:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    if set(raw) != {"source"}:
        raise ValueError("configuration must contain only [source]")
    source = raw["source"]
    string_fields = {
        "source_id",
        "kind",
        "connection_env",
        "signing_key_env",
        "signing_key_id",
        "http_token_env",
    }
    for key in SourceConfig.model_fields:
        env = os.getenv("DBIG_" + key.upper())
        if env is not None:
            source[key] = env if key in string_fields else tomllib.loads("value = " + env)["value"]
    return SourceConfig.model_validate(source)
