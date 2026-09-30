"""The example configurations the runbook tells operators to copy must load as they are.

They are the first thing a new operator touches, so a renamed or removed field would otherwise
surface only as a validation error on someone else's machine.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from dbig_builder.config import load_config as load_builder
from dbig_collectors.config import load_config as load_source

EXAMPLES = Path(__file__).parents[1] / "examples" / "local"


@pytest.mark.parametrize("name", ["postgresql", "clickhouse", "mongodb"])
def test_every_source_example_loads(name, monkeypatch):
    for key in ("DBIG_SAMPLE_ROWS", "DBIG_MAX_QUERIES", "DBIG_TLS", "DBIG_TLS_CA_FILE"):
        monkeypatch.delenv(key, raising=False)
    config = load_source(EXAMPLES / f"{name}.toml")
    assert config.kind == name
    assert config.tls is True, "the examples must never suggest plaintext"
    assert config.signing_key_id == "local"


def test_the_builder_examples_load_and_match_the_sources():
    plain = load_builder(EXAMPLES / "builder.toml")
    reviewed = load_builder(EXAMPLES / "builder-with-vllm.toml")
    assert set(plain.signing_keys) == {"local"}
    assert reviewed.review is not None
    assert reviewed.review.base_url == "http://127.0.0.1:8000/v1"
