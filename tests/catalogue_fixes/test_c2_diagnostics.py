"""C2: `partial` is explained in the machine-readable places (plan section 9, Phase 1).

Before the fix a snapshot whose review failed is `completeness: partial`, the page lists
`MODEL_REVIEW_FAILED`, but the manifest's `diagnostics` is null and `dbig-builder render` prints
`"diagnostics": 0`. Acceptance: with a failing stub the manifest lists the codes and render's
`diagnostics` count equals what the page shows; a complete snapshot still shows 0 or none.
"""

from __future__ import annotations

import json

import pytest
from catalogue_fixture_kit import MODEL_KEY, ModelStub, build_snapshot, support_everything
from click.testing import CliRunner
from dbig_builder.cli import main
from dbig_builder.config import ReviewConfig
from dbig_builder.render import render_catalog
from dbig_contracts.artifact import read_artifact

TABLES = {
    "vendor": {"fields": [("id", "integer")], "key": ["id"]},
    "shipment": {"fields": [("id", "integer"), ("vendor_id", "integer")], "key": ["id"]},
}


@pytest.fixture
def failed(tmp_path, monkeypatch):
    """A snapshot whose review failed: the model server answers 400 to every request."""
    monkeypatch.setenv("MODEL_KEY", MODEL_KEY)
    with ModelStub(support_everything, status=400) as stub:
        review = ReviewConfig(
            base_url=stub.url, model="m", model_digest="d", api_key_env="MODEL_KEY",
            timeout_seconds=5,
        )
        snapshot = build_snapshot(tmp_path, monkeypatch, {"alpha": {"tables": TABLES}},
                                  review=review)
    return snapshot


@pytest.fixture
def complete(tmp_path, monkeypatch):
    return build_snapshot(tmp_path, monkeypatch, {"alpha": {"tables": TABLES}}, name="complete")


def test_a_failed_review_is_partial_and_the_page_says_why(failed, tmp_path):
    """Control: the page already lists the partial reason."""
    manifest, _ = read_artifact(failed)
    assert manifest.completeness == "partial"
    out = tmp_path / "page.html"
    render_catalog(failed, [], out, None)
    assert "MODEL_REVIEW_FAILED" in out.read_text(encoding="utf-8")


@pytest.mark.xfail(strict=True, reason="C2 not implemented")
def test_the_manifest_lists_the_codes_of_a_partial_snapshot(failed):
    manifest, _ = read_artifact(failed)
    assert manifest.diagnostics, "a partial snapshot must not report a null summary"
    assert "MODEL_REVIEW_FAILED" in json.dumps(manifest.diagnostics)


@pytest.mark.xfail(strict=True, reason="C2 not implemented")
def test_render_reports_the_same_count_as_the_manifest_and_the_page(failed, tmp_path):
    manifest, _ = read_artifact(failed)
    out = tmp_path / "page.html"
    counts = render_catalog(failed, [], out, None)
    assert counts["diagnostics"] >= 1, counts
    assert manifest.diagnostics
    assert counts["diagnostics"] == sum(manifest.diagnostics.values())
    assert "MODEL_REVIEW_FAILED" in out.read_text(encoding="utf-8")


@pytest.mark.xfail(strict=True, reason="C2 not implemented")
def test_the_render_command_summary_carries_the_diagnostics(failed, tmp_path):
    result = CliRunner().invoke(
        main, ["render", "--snapshot", str(failed), "--out", str(tmp_path / "cli.html")]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["diagnostics"] >= 1


def test_a_complete_snapshot_still_shows_none(complete, tmp_path):
    """Control: no diagnostics, no summary or an empty one, count 0, no partial banner."""
    manifest, _ = read_artifact(complete)
    assert manifest.completeness == "complete"
    assert not manifest.diagnostics
    out = tmp_path / "page.html"
    counts = render_catalog(complete, [], out, None)
    assert counts["diagnostics"] == 0
    result = CliRunner().invoke(
        main, ["render", "--snapshot", str(complete), "--out", str(tmp_path / "cli.html")]
    )
    assert json.loads(result.output)["diagnostics"] == 0
