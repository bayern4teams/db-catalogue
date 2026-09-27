"""CLI contract tests for the inspection commands operators and other components rely on.

`validate-graph`, `explain-edge` and `validate-bundle` are the read-only commands whose output
is consumed outside the process that produced it, so their contract matters: what they accept,
what they refuse, and that a refusal never emits a manifest or evidence an operator could
mistake for a verified result. These tests pin the behaviour that exists today; where a command
fails closed through an uncaught exception rather than a formatted message, the test asserts the
security property (non-zero exit, nothing published on stdout) rather than the message text.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner, Result
from dbig_builder.builder import assemble
from dbig_builder.cli import main as builder_main
from dbig_builder.config import BuilderConfig
from dbig_collectors.cli import main as collector_main
from dbig_contracts.artifact import read_artifact, write_artifact
from dbig_contracts.models import Source
from test_builder import source_bundle

BUNDLE_KEY = "synthetic-key-12345678901234567890"
WRONG_KEY = "wrong-key-1234567890123456789012345"


@pytest.fixture
def artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """A signed collector bundle and the catalog snapshot assembled from it."""
    monkeypatch.setenv("BUNDLE_KEY", BUNDLE_KEY)
    bundle = tmp_path / "bundle"
    source_bundle(bundle)
    snapshot = tmp_path / "snapshot"
    assemble([bundle], snapshot, BuilderConfig(signing_keys={"test": "BUNDLE_KEY"}))
    return {"bundle": bundle, "snapshot": snapshot}


@pytest.fixture
def unsigned_bundle(tmp_path: Path) -> Path:
    """A readable collector bundle with no signature, to reach the content-type guard."""
    path = tmp_path / "unsigned"
    common = {
        "source_id": "site-a",
        "observed_at": datetime.now(UTC),
        "collector_version": "synthetic",
        "provenance": "synthetic",
    }
    write_artifact(
        path,
        "collector-bundle",
        {"sources.jsonl": [Source(id="site-a", kind="postgresql", name="test", **common)]},
        producer="synthetic",
        source_run_ids=["r"],
    )
    return path


def failed_without_publishing(result: Result) -> bool:
    """A refusal must exit non-zero and leave no manifest or record on stdout."""
    return result.exit_code != 0 and '"ir_version"' not in result.stdout


# --- builder validate-graph -----------------------------------------------------------


def test_validate_graph_accepts_a_catalog_snapshot(artifacts: dict[str, Path]) -> None:
    result = CliRunner().invoke(builder_main, ["validate-graph", str(artifacts["snapshot"])])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["content_type"] == "catalog-snapshot"


def test_validate_graph_refuses_a_collector_bundle(unsigned_bundle: Path) -> None:
    """A source bundle is not a snapshot; accepting one would skip the assembler entirely."""
    result = CliRunner().invoke(builder_main, ["validate-graph", str(unsigned_bundle)])
    assert failed_without_publishing(result)
    assert "expected catalog snapshot" in result.stderr


def test_validate_graph_refuses_a_signed_bundle_it_cannot_verify(
    artifacts: dict[str, Path],
) -> None:
    """`validate-graph` passes no signing key, so a signed bundle fails before any output."""
    result = CliRunner().invoke(builder_main, ["validate-graph", str(artifacts["bundle"])])
    assert failed_without_publishing(result)


def test_validate_graph_refuses_a_missing_path(tmp_path: Path) -> None:
    result = CliRunner().invoke(builder_main, ["validate-graph", str(tmp_path / "absent")])
    assert result.exit_code != 0


# --- builder explain-edge -------------------------------------------------------------


def test_explain_edge_returns_the_edge_with_exactly_its_cited_evidence(
    artifacts: dict[str, Path],
) -> None:
    _, records = read_artifact(artifacts["snapshot"])
    edges = records["edges.jsonl"]
    assert edges, "the synthetic snapshot must contain at least one edge to explain"
    for edge in edges:
        result = CliRunner().invoke(
            builder_main, ["explain-edge", edge.id, "--graph", str(artifacts["snapshot"])]
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["edge"]["id"] == edge.id
        cited = [item["id"] for item in payload["evidence"]]
        assert cited == list(edge.evidence_ids), "evidence must match the edge's citations"


def test_explain_edge_evidence_is_resolvable_in_the_snapshot(
    artifacts: dict[str, Path],
) -> None:
    """Every returned evidence record must come from the snapshot, not be synthesised."""
    _, records = read_artifact(artifacts["snapshot"])
    known = {record.id for record in records["evidence.jsonl"]}
    edge = records["edges.jsonl"][0]
    result = CliRunner().invoke(
        builder_main, ["explain-edge", edge.id, "--graph", str(artifacts["snapshot"])]
    )
    assert result.exit_code == 0, result.output
    returned = {item["id"] for item in json.loads(result.stdout)["evidence"]}
    assert returned <= known
    assert returned, "an edge carrying citations must explain them"


def test_explain_edge_rejects_an_unknown_edge_id(artifacts: dict[str, Path]) -> None:
    result = CliRunner().invoke(
        builder_main, ["explain-edge", "edge:does-not-exist", "--graph", str(artifacts["snapshot"])]
    )
    assert failed_without_publishing(result)
    assert "edge not found" in result.stderr


def test_explain_edge_requires_the_graph_option(artifacts: dict[str, Path]) -> None:
    result = CliRunner().invoke(builder_main, ["explain-edge", "edge:any"])
    assert result.exit_code != 0


def test_explain_edge_output_is_deterministic(artifacts: dict[str, Path]) -> None:
    """Operators diff these payloads, so repeated calls must be byte-identical."""
    _, records = read_artifact(artifacts["snapshot"])
    edge = records["edges.jsonl"][0]
    command = ["explain-edge", edge.id, "--graph", str(artifacts["snapshot"])]
    first = CliRunner().invoke(builder_main, command)
    second = CliRunner().invoke(builder_main, command)
    assert first.exit_code == 0 and second.exit_code == 0
    assert first.stdout == second.stdout


# --- collector validate-bundle --------------------------------------------------------


def test_validate_bundle_accepts_a_correctly_signed_bundle(artifacts: dict[str, Path]) -> None:
    result = CliRunner().invoke(
        collector_main, ["validate-bundle", str(artifacts["bundle"]), "--key-env", "BUNDLE_KEY"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["content_type"] == "collector-bundle"


def test_validate_bundle_refuses_a_wrong_signing_key(
    artifacts: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bundle that does not verify must never be reported as valid."""
    monkeypatch.setenv("WRONG_KEY", WRONG_KEY)
    result = CliRunner().invoke(
        collector_main, ["validate-bundle", str(artifacts["bundle"]), "--key-env", "WRONG_KEY"]
    )
    assert failed_without_publishing(result)


def test_validate_bundle_refuses_an_unsigned_artifact(artifacts: dict[str, Path]) -> None:
    """The assembled snapshot carries no collector signature, so it must be refused."""
    result = CliRunner().invoke(
        collector_main, ["validate-bundle", str(artifacts["snapshot"]), "--key-env", "BUNDLE_KEY"]
    )
    assert failed_without_publishing(result)


def test_validate_bundle_fails_closed_when_the_key_env_is_unset(
    artifacts: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mistyped or unset `--key-env` must not fall back to skipping verification."""
    monkeypatch.delenv("ABSENT_KEY_ENV", raising=False)
    result = CliRunner().invoke(
        collector_main, ["validate-bundle", str(artifacts["bundle"]), "--key-env", "ABSENT_KEY_ENV"]
    )
    assert failed_without_publishing(result)


def test_validate_bundle_reports_an_unset_key_env_as_a_command_error(
    artifacts: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An operator typed this reference, so they get a named message, not a traceback."""
    monkeypatch.delenv("ABSENT_KEY_ENV", raising=False)
    result = CliRunner().invoke(
        collector_main, ["validate-bundle", str(artifacts["bundle"]), "--key-env", "ABSENT_KEY_ENV"]
    )
    assert failed_without_publishing(result)
    assert "ABSENT_KEY_ENV, which is not set" in result.stderr
    assert not isinstance(result.exception, KeyError)


def test_validate_bundle_reports_a_blank_key_env_as_a_command_error(
    artifacts: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A variable set to whitespace must be refused rather than used as a signing key."""
    monkeypatch.setenv("BLANK_KEY_ENV", "   ")
    result = CliRunner().invoke(
        collector_main, ["validate-bundle", str(artifacts["bundle"]), "--key-env", "BLANK_KEY_ENV"]
    )
    assert failed_without_publishing(result)
    assert "BLANK_KEY_ENV" in result.stderr and "is empty" in result.stderr


def test_validate_bundle_refuses_a_missing_path(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        collector_main, ["validate-bundle", str(tmp_path / "absent"), "--key-env", "BUNDLE_KEY"]
    )
    assert result.exit_code != 0
