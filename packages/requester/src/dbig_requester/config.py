"""Requester runtime configuration without embedded secrets."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from dbig_contracts.errors import load_toml, load_toml_section, validate_configuration
from dbig_contracts.security import private_base_url
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AnswerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str
    model: str = Field(min_length=1)
    model_digest: str = Field(min_length=1)
    api_key_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    timeout_seconds: int = Field(default=20, ge=1)
    # Unset sends no `max_tokens`, so the server's own maximum applies; a reasoning model can
    # spend a small budget before it answers (user decision 2026-10-17, live run).
    max_tokens: int | None = Field(default=None, ge=1)
    # Longest answer text accepted, in characters; longer is `MODEL_ANSWER_TOO_LONG`. Unset accepts
    # any length (user decision 2026-10-18: list answers ran to 6243 characters in the live run).
    max_answer_chars: int | None = Field(default=None, ge=1)
    # Extra attempts after a reply that is only a verdict (F17); 0 makes a single attempt.
    answer_retries: int = Field(default=1, ge=0)
    retrieval_limit: int = Field(default=10, ge=1, le=100)
    # Entries retrieved for a list question ("list all ...") or a question about a type, instead
    # of `retrieval_limit` (B6, B7). Capped by the search route's own limit of 100.
    list_limit: int = Field(default=100, ge=1, le=100)
    # Most relationship edges, with their evidence status, passed to the model beside the
    # retrieved entries (DEC-5). 0 passes none.
    relationship_limit: int = Field(default=20, ge=0, le=200)
    # Path search between retrieved tables through bridge tables (F16, DEC-24): longest path in
    # edges, and most tables added only because they lie on one. 0 bridges turns the search off.
    path_max_hops: int = Field(default=4, ge=1)
    path_max_bridge_tables: int = Field(default=4, ge=0)
    # Candidates between two sources with no positive measurement (F11-b) are left out of the
    # model context and the path search unless this is true. A rejected one is always left out.
    include_unverified_cross_source: bool = False
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
    allowed_sources: dict[str, list[str]] = Field(default_factory=dict)
    allowed_capabilities: list[Literal["metadata", "sample", "history"]] = ["metadata"]
    handoff_root: Path | None = None
    collector_urls: dict[str, str] = Field(default_factory=dict)
    # The deployment's sources file (deploy/sources.toml, owner 2026-11-22, answer B1): every
    # `[[source]]` adds its source_id and namespaces to allowed_sources, and is sent to
    # `collector_url` unless collector_urls names another collector for it. Only those two keys
    # are read; the logins stay with the collector.
    sources_file: Path | None = None
    collector_url: str | None = None
    http_token_env: str | None = None
    http_timeout_seconds: int = Field(default=10, ge=1)

    @field_validator("collector_urls")
    @classmethod
    def private_collectors(cls, value: dict[str, str]) -> dict[str, str]:
        return {source: private_base_url(url) for source, url in value.items()}

    @field_validator("collector_url")
    @classmethod
    def private_collector(cls, value: str | None) -> str | None:
        return private_base_url(value) if value is not None else None

    @model_validator(mode="after")
    def sources_from_file(self) -> ScanConfig:
        if self.sources_file is not None:
            tables = load_toml(self.sources_file).get("source")
            tables = [tables] if isinstance(tables, dict) else tables or []
            for table in tables:
                source_id, namespaces = table.get("source_id"), table.get("namespaces")
                if not isinstance(source_id, str) or not isinstance(namespaces, list):
                    raise ValueError(
                        f"{self.sources_file}: every [[source]] needs source_id and namespaces"
                    )
                self.allowed_sources.setdefault(source_id, namespaces)
                if self.collector_url is not None:
                    self.collector_urls.setdefault(source_id, self.collector_url)
        if not self.allowed_sources:
            raise ValueError("scan needs allowed_sources or a sources_file that lists a source")
        return self


class RequesterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_dir: Path
    state_dir: Path
    api_key_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    max_request_bytes: int = Field(default=65_536, ge=1)
    max_requests_per_minute: int = Field(default=60, ge=1)
    # Per source ID, case-insensitive globs on an object name or `namespace.name` that search
    # leaves out of the index (DEC-8), for collections that describe a schema rather than hold
    # data. Default none. Re-run `dbig-requester index` after changing it.
    exclude_objects: dict[str, list[str]] = Field(default_factory=dict)
    answer: AnswerConfig | None = None
    scan: ScanConfig | None = None


def load_config(path: Path) -> RequesterConfig:
    return validate_configuration(path, RequesterConfig, load_toml_section(path, "requester"))
