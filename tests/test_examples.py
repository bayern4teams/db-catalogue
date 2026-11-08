"""The example configurations the runbook tells operators to copy must load as they are.

They are the first thing a new operator touches, so a renamed or removed field would otherwise
surface only as a validation error on someone else's machine.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from dbig_builder.config import load_config as load_builder
from dbig_collectors.config import load_config as load_source
from dbig_requester.config import load_config as load_requester

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


def test_the_requester_example_loads():
    config = load_requester(EXAMPLES / "requester-with-vllm.toml")
    assert config.answer is not None
    assert config.answer.base_url == "http://127.0.0.1:8000/v1"


def test_the_env_example_loads_as_the_guide_says_and_names_every_variable():
    """`set -a; . ./.env; set +a` must yield valid JSON connections, and every environment
    variable the example configurations refer to must be listed there."""
    import json
    import re
    import shutil
    import subprocess
    import tomllib

    bash = shutil.which("bash")
    if bash is None:
        pytest.skip(  # C5: reason=see message; approved=user, 2026-11-08; review=2027-05-08
            "bash is needed to load the file the way the guide says"
        )
    root = Path(__file__).resolve().parents[1]
    loaded = subprocess.run(  # noqa: S603 # C5: reason=fixed argv, repository file; approved=user, 2026-11-08; review=2027-05-08
        [bash, "-c", "set -a; . ./.env.example; set +a; env -0"],
        cwd=root, capture_output=True, check=True,
    ).stdout.decode()
    environment = dict(item.split("=", 1) for item in loaded.split("\0") if "=" in item)
    for name in ("PG_CONNECTION", "CLICKHOUSE_CONNECTION", "MONGODB_CONNECTION"):
        assert isinstance(json.loads(environment[name]), dict), name
    listed = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]+)=", (root / ".env.example").read_text(
        encoding="utf-8"), flags=re.MULTILINE))
    referenced = set()
    for config in (root / "examples" / "local").glob("*.toml"):
        def walk(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key.endswith("_env") and isinstance(item, str):
                        referenced.add(item)
                    walk(item)
        walk(tomllib.loads(config.read_text(encoding="utf-8")))
        builder = tomllib.loads(config.read_text(encoding="utf-8")).get("builder", {})
        referenced.update(builder.get("signing_keys", {}).values())
    assert referenced and referenced <= listed, referenced - listed
