"""Planner configuration and bounded input limits."""

from __future__ import annotations

from pathlib import Path

from dbig_contracts.errors import load_toml_section, validate_configuration
from pydantic import BaseModel, ConfigDict, Field


class PlannerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    graph_dir: Path
    api_key_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]*$")
    max_request_bytes: int = Field(default=65_536, ge=1)
    max_hops: int = Field(default=4, ge=1, le=10)
    max_paths: int = Field(default=20, ge=1, le=100)
    max_plan_rows: int = Field(default=1000, ge=1, le=100_000)
    max_observation_bytes: int = Field(default=1_048_576, ge=1)
    max_observations: int = Field(default=10_000, ge=1)
    max_requests_per_minute: int = Field(default=60, ge=1)


def load_config(path: Path) -> PlannerConfig:
    return validate_configuration(path, PlannerConfig, load_toml_section(path, "planner"))
