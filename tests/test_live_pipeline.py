"""The whole pipeline on real source data, starting from the real `collect` command.

`tests/test_end_to_end.py` proves the chain from a synthetic bundle onward. It has to, because
`collect` needs a database. This file closes the remaining gap: it runs `dbig-collectors collect`
against a live PostgreSQL over verified TLS and feeds that genuine bundle through assemble,
index, search and the planner.

The TLS part is the reason this could not exist before. `collect` connects with
`ssl.create_default_context()` and the CI fixtures were plaintext, so its connection path was
the one step of the pipeline no test reached. The job now starts PostgreSQL with a throwaway CA
and exports `SSL_CERT_FILE`, so verification is real rather than switched off.

Opt-in, like the other live tests: skipped unless `DBIG_RUN_INTEGRATION=1`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("DBIG_RUN_INTEGRATION") != "1", reason="live source containers unavailable"
)

SCRIPTS = Path(sys.executable).parent
NAMESPACE = "dbig_pipeline"
SIGNING_KEY = "synthetic-ci-signing-key-well-over-32-bytes"
CONNECTION = json.dumps(
    {"host": "127.0.0.1", "port": 5432, "database": "postgres", "user": "postgres"}
)


# Fixture DDL and DML. NAMESPACE is a constant in this file, not input, and nothing the
# application ships builds SQL this way: the collectors bind every value and interpolate only
# placeholder tokens. Kept as constants so the statements read as data, not as query building.
SEED_STATEMENTS = (
    f"DROP SCHEMA IF EXISTS {NAMESPACE} CASCADE",
    f"CREATE SCHEMA {NAMESPACE}",
    f"CREATE TABLE {NAMESPACE}.customers (id integer PRIMARY KEY)",  # noqa: S608
    f"INSERT INTO {NAMESPACE}.customers VALUES (1), (2), (3)",  # noqa: S608
    f"CREATE TABLE {NAMESPACE}.orders (id integer PRIMARY KEY, "  # noqa: S608
    f"customer_id integer REFERENCES {NAMESPACE}.customers(id))",
    f"INSERT INTO {NAMESPACE}.orders VALUES (1, 1), (2, 1), (3, 2)",  # noqa: S608
    f"ANALYZE {NAMESPACE}.customers",
    f"ANALYZE {NAMESPACE}.orders",
)


def _run(command: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    executable = SCRIPTS / command[0]
    if not executable.exists():
        pytest.skip(f"{command[0]} console script is not installed")
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        [str(executable), *command[1:]], capture_output=True, text=True, env=env, timeout=240
    )


def _seed() -> None:
    """Create a small schema with a declared foreign key, and analyze it so statistics exist."""
    from pg8000 import dbapi

    deadline = time.monotonic() + 120
    while True:
        try:
            connection = dbapi.connect(
                host="127.0.0.1", port=5432, user="postgres", database="postgres"
            )
            break
        except Exception:
            if time.monotonic() >= deadline:
                raise
            time.sleep(2)
    try:
        cursor = connection.cursor()
        for statement in SEED_STATEMENTS:
            cursor.execute(statement)
        connection.commit()
    finally:
        connection.close()


@pytest.fixture(scope="module")
def collected(tmp_path_factory) -> dict:
    """Run the real collector against the live server, then the rest of the chain."""
    _seed()
    root = tmp_path_factory.mktemp("pipeline")
    env = {
        **os.environ,
        "DBIG_CONNECTION": CONNECTION,
        "DBIG_SIGNING_KEY": SIGNING_KEY,
    }
    (root / "source.toml").write_text(
        "[source]\n"
        'source_id = "ci-source"\n'
        'kind = "postgresql"\n'
        'connection_env = "DBIG_CONNECTION"\n'
        'signing_key_env = "DBIG_SIGNING_KEY"\n'
        'signing_key_id = "ci"\n'
        f'namespaces = ["{NAMESPACE}"]\n'
        "max_queries = 20\n",
        encoding="utf-8",
    )
    (root / "builder.toml").write_text(
        '[builder]\nsigning_keys = { ci = "DBIG_SIGNING_KEY" }\n', encoding="utf-8"
    )
    (root / "requester.toml").write_text(
        "[requester]\n"
        f'snapshot_dir = "{(root / "snapshot").as_posix()}"\n'
        f'state_dir = "{(root / "state").as_posix()}"\n'
        'api_key_env = "REQUESTER_TOKEN"\n',
        encoding="utf-8",
    )

    steps: dict[str, subprocess.CompletedProcess[str]] = {}
    steps["collect"] = _run(
        ["dbig-collectors", "collect", "--config", str(root / "source.toml"),
         "--out", str(root / "bundle")],
        env,
    )
    steps["validate-bundle"] = _run(
        ["dbig-collectors", "validate-bundle", str(root / "bundle"),
         "--key-env", "DBIG_SIGNING_KEY"],
        env,
    )
    steps["assemble"] = _run(
        ["dbig-builder", "assemble", "--config", str(root / "builder.toml"),
         "--bundle", str(root / "bundle"), "--out", str(root / "snapshot")],
        env,
    )
    steps["index"] = _run(
        ["dbig-requester", "index", "--config", str(root / "requester.toml")], env
    )
    steps["search"] = _run(
        ["dbig-requester", "search", "--config", str(root / "requester.toml"),
         "--query", "orders"],
        env,
    )
    observations = root / "observations"
    observations.mkdir()
    steps["build"] = _run(
        ["dbig-planner", "build", "--snapshot", str(root / "snapshot"),
         "--observations", str(observations), "--out", str(root / "graph")],
        env,
    )
    return {"steps": steps, "root": root, "env": env}


def test_every_stage_of_the_real_pipeline_succeeds(collected):
    for name, result in collected["steps"].items():
        assert result.returncode == 0, f"{name} failed:\n{result.stdout}\n{result.stderr}"


def test_collect_reached_the_server_over_verified_tls(collected):
    """The step this file exists for. `collect` uses create_default_context(), so a bundle can
    only exist if the chain and the hostname both verified against the throwaway CA."""
    assert collected["steps"]["collect"].returncode == 0
    assert (collected["root"] / "bundle" / "manifest.json").is_file()


def test_the_real_bundle_is_signed_and_verifies(collected):
    assert collected["steps"]["validate-bundle"].returncode == 0
    manifest = json.loads(
        (collected["root"] / "bundle" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["signature"]["algorithm"] == "hmac-sha256"
    assert manifest["signature"]["key_id"] == "ci"


def test_the_real_bundle_carries_no_credential(collected):
    """The connection string and signing key are in the environment, never in the artifact."""
    for path in sorted((collected["root"] / "bundle").iterdir()):
        content = path.read_text(encoding="utf-8", errors="replace")
        assert SIGNING_KEY not in content
        assert "postgres" not in content or path.name == "sources.jsonl"


def test_real_source_metadata_reached_the_snapshot(collected):
    from dbig_contracts.artifact import read_artifact

    _, data = read_artifact(collected["root"] / "snapshot")
    names = {
        node.name for node in data["nodes.jsonl"] if node.record_type == "object"
    }
    assert {"customers", "orders"} <= names


def test_a_declared_foreign_key_from_the_real_server_becomes_an_edge(collected):
    """The declared FK is the one relationship a real source states outright, so it must survive
    the whole chain from catalog read to published edge."""
    from dbig_contracts.artifact import read_artifact

    _, data = read_artifact(collected["root"] / "snapshot")
    kinds = {edge.model_dump()["kind"] for edge in data["edges.jsonl"]}
    assert "DECLARES_FK" in kinds


def test_real_statistics_became_profiles(collected):
    """The fixture runs ANALYZE, so pg_stats has numbers and profiles must carry them."""
    from dbig_contracts.artifact import read_artifact

    _, bundle = read_artifact(collected["root"] / "bundle", signing_key=SIGNING_KEY.encode())
    profiles = bundle["profiles.jsonl"]
    assert profiles, "an analyzed schema must yield profiles from a real server"
    assert all(profile.model_dump()["method"] == "source_statistic" for profile in profiles)
    unique = [
        profile for profile in profiles if profile.model_dump().get("uniqueness") == 1.0
    ]
    assert unique, "the primary keys are unique, so at least one profile must say so"


def test_search_finds_the_real_objects(collected):
    payload = json.loads(collected["steps"]["search"].stdout)
    assert any(row["name"] == "orders" for row in payload["results"])
