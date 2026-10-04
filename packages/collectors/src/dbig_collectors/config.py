"""Validated source-local configuration; credentials remain in process environment."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Literal

from dbig_contracts.errors import OperatorError, load_toml_section, validate_configuration
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str = Field(min_length=1)
    kind: Literal["postgresql", "clickhouse", "mongodb"]
    connection_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    signing_key_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    signing_key_id: str = Field(min_length=1)
    namespaces: list[str] = Field(min_length=1)
    # Verified TLS is the default and the only option for any host other than loopback. `false`
    # is accepted only when every host in the connection reference is a loopback IP literal,
    # checked at connect time because the host lives in the secret, not in this file.
    tls: bool = True
    # A PEM file of CA certificates, for servers whose certificates come from a private CA. Passed
    # explicitly to every driver, because they otherwise find trust roots differently: the
    # PostgreSQL and ClickHouse drivers use the operating system store, and PyMongo's handling of
    # `SSL_CERT_FILE` differs by platform. Unset means the operating system store.
    tls_ca_file: Path | None = None
    max_queries: int = Field(default=50, ge=1)
    max_objects: int = Field(default=1000, ge=1)
    max_fields: int = Field(default=10000, ge=1)
    read_timeout_seconds: int = Field(default=10, ge=1)
    sample_rows: int = Field(default=0, ge=0)
    max_sample_bytes: int = Field(default=1_048_576, ge=1)
    max_array_elements: int = Field(default=16, ge=1, le=256)
    max_path_depth: int = Field(default=4, ge=1)
    history_access: bool = False
    # Table and column comments written by the database's owners. They are catalog metadata, not
    # row data, and cost no extra query. On by default by user decision on 2026-09-30.
    include_comments: bool = True
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

    @model_validator(mode="after")
    def ca_file_needs_tls(self) -> SourceConfig:
        if self.tls_ca_file is not None:
            if not self.tls:
                raise ValueError("tls_ca_file has no effect with tls = false")
            if not self.tls_ca_file.is_file():
                raise ValueError(f"tls_ca_file is not a readable file: {self.tls_ca_file}")
        return self

    @field_validator("namespaces")
    @classmethod
    def nonempty_namespaces(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value) or len(value) != len(set(value)):
            raise ValueError("namespaces must be nonempty and unique")
        return value


def load_config(path: Path) -> SourceConfig:
    source = load_toml_section(path, "source")
    string_fields = {
        "source_id",
        "kind",
        "connection_env",
        "signing_key_env",
        "signing_key_id",
        "http_token_env",
        "tls_ca_file",
    }
    for key in SourceConfig.model_fields:
        env = os.getenv("DBIG_" + key.upper())
        if env is not None:
            try:
                source[key] = (
                    env if key in string_fields else tomllib.loads("value = " + env)["value"]
                )
            except tomllib.TOMLDecodeError as error:
                raise OperatorError(
                    f"environment override DBIG_{key.upper()} is not a valid value: {error}"
                ) from error
    return validate_configuration(path, SourceConfig, source)
