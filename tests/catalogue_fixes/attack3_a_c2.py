# ruff: noqa
"""Attack H1(a) for C2 (DEC-11b a): a `partial` snapshot explains itself in the machine-readable
places. Derived from engineer-fixes C2 ("make the CLI summary and the manifest carry the same
diagnostics as the page") and the DEC-11b (a) text: new OPTIONAL manifest field `partial_reasons:
list[str]`, additive, old readers ignore it; render/CLI count = page diagnostics + reasons.

Run by path:
    uv run pytest tests/catalogue_fixes/attack3_a_c2.py -q -p no:cacheprovider
Old-reader check reads the new snapshot with the BASE commit's packages (DBIG_BASE_SRC, a directory
holding a `git archive` of 2e87fa7; skipped when absent).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from catalogue_fixture_kit import MODEL_KEY, ModelStub, build_snapshot, support_everything
from click.testing import CliRunner
from dbig_builder.cli import main
from dbig_builder.config import ReviewConfig
from dbig_builder.render import render_catalog
from dbig_contracts.artifact import read_artifact

CANARY = "CANARY-7f3a-sample-value-do-not-leak"
TABLES = {
    "vendor": {"fields": [("id", "integer")], "key": ["id"]},
    "shipment": {"fields": [("id", "integer"), ("vendor_id", "integer")], "key": ["id"]},
}
REPO = Path(__file__).resolve().parents[2]
BASE_SRC = os.environ.get("DBIG_BASE_SRC")


def _review(stub):
    return ReviewConfig(base_url=stub.url, model="m", model_digest="d", api_key_env="MODEL_KEY", timeout_seconds=5)


def _partial(tmp_path, monkeypatch, *, name="p", **stub_kwargs):
    monkeypatch.setenv("MODEL_KEY", MODEL_KEY)
    stub_kwargs.setdefault("status", 400)
    with ModelStub(support_everything, **stub_kwargs) as stub:
        return build_snapshot(tmp_path, monkeypatch, {"alpha": {"tables": TABLES}}, review=_review(stub), name=name)


def _complete(tmp_path, monkeypatch, name="c"):
    return build_snapshot(tmp_path, monkeypatch, {"alpha": {"tables": TABLES}}, name=name)


def _page_codes(snapshot, tmp_path):
    out = tmp_path / "page.html"
    counts = render_catalog(snapshot, [], out, None)
    return counts, out.read_text(encoding="utf-8")


def test_reasons_are_listed_unique_sorted_and_codes_only(tmp_path, monkeypatch):
    snap = _partial(tmp_path, monkeypatch)
    manifest, _ = read_artifact(snap)
    assert manifest.completeness == "partial"
    reasons = manifest.partial_reasons
    assert reasons and reasons == sorted(set(reasons))
    assert all(re.fullmatch(r"[A-Z][A-Z0-9_]+", r) for r in reasons), reasons


@pytest.mark.parametrize("kwargs", [{"status": 400}, {"status": 500}, {"status": 200, "content": f"{CANARY} not json"},
                                    {"status": None, "finish_reason": "length", "content": ""}])
def test_each_failure_mode_is_partial_with_reasons_and_leaks_nothing(tmp_path, monkeypatch, kwargs):
    snap = _partial(tmp_path, monkeypatch, name="m", **kwargs)
    manifest, _ = read_artifact(snap)
    assert manifest.completeness == "partial" and manifest.partial_reasons, manifest
    counts, page = _page_codes(snap, tmp_path)
    result = CliRunner().invoke(main, ["render", "--snapshot", str(snap), "--out", str(tmp_path / "cli.html")])
    assert result.exit_code == 0, result.output
    blob = (snap / "manifest.json").read_text(encoding="utf-8") + page + result.output
    assert CANARY not in blob and MODEL_KEY not in blob and "127.0.0.1" not in (snap / "manifest.json").read_text(encoding="utf-8")


def test_cli_count_equals_render_count_equals_page_diagnostics(tmp_path, monkeypatch):
    snap = _partial(tmp_path, monkeypatch)
    manifest, _ = read_artifact(snap)
    counts, page = _page_codes(snap, tmp_path)
    cli = json.loads(CliRunner().invoke(main, ["render", "--snapshot", str(snap), "--out", str(tmp_path / "cli.html")]).output)
    assert cli["diagnostics"] == counts["diagnostics"] >= len(manifest.partial_reasons)
    for reason in manifest.partial_reasons:
        assert reason in page, f"page does not show reason {reason}"


def test_old_code_would_have_said_zero(tmp_path, monkeypatch):
    """Negative case (fails on the old behaviour): a partial snapshot must not report 0."""
    snap = _partial(tmp_path, monkeypatch)
    cli = json.loads(CliRunner().invoke(main, ["render", "--snapshot", str(snap), "--out", str(tmp_path / "cli.html")]).output)
    assert cli["diagnostics"] != 0


def test_complete_snapshot_carries_no_reasons_and_count_zero(tmp_path, monkeypatch):
    snap = _complete(tmp_path, monkeypatch)
    manifest, _ = read_artifact(snap)
    assert manifest.completeness == "complete" and not manifest.partial_reasons
    raw = json.loads((snap / "manifest.json").read_text(encoding="utf-8"))
    assert not raw.get("partial_reasons")
    counts, _page = _page_codes(snap, tmp_path)
    assert counts["diagnostics"] == 0


def test_the_reasons_are_deterministic_across_runs(tmp_path, monkeypatch):
    a = read_artifact(_partial(tmp_path, monkeypatch, name="a"))[0].partial_reasons
    b = read_artifact(_partial(tmp_path, monkeypatch, name="b"))[0].partial_reasons
    assert a == b


def test_manifest_diagnostics_field_keeps_the_reader_contract(tmp_path, monkeypatch):
    """DEC-11b (a): `diagnostics` is NOT reused (the reader requires it to equal the diagnostics
    file's counts, and snapshots carry none)."""
    snap = _partial(tmp_path, monkeypatch)
    raw = json.loads((snap / "manifest.json").read_text(encoding="utf-8"))
    assert raw.get("diagnostics") in (None, {}), raw.get("diagnostics")


def test_schema_marks_partial_reasons_optional():
    schema_dir = REPO / "schemas"
    found = False
    for path in schema_dir.rglob("*.json"):
        text = path.read_text(encoding="utf-8")
        if "partial_reasons" in text:
            found = True
            data = json.loads(text)
            assert "partial_reasons" not in data.get("required", []), path
    assert found, "schemas/ must describe the new optional field"


def test_tampering_with_partial_reasons_is_detected_or_harmless(tmp_path, monkeypatch):
    """Removing the reasons from a signed partial snapshot must not yield a 'complete' reading."""
    snap = _partial(tmp_path, monkeypatch)
    manifest_path = snap / "manifest.json"
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw["partial_reasons"] = []
    manifest_path.write_text(json.dumps(raw), encoding="utf-8")
    try:
        manifest, _ = read_artifact(snap)
    except Exception:  # noqa: BLE001 - signature or hash failure is the best outcome
        return
    assert manifest.completeness == "partial"


@pytest.mark.skipif(not BASE_SRC, reason="set DBIG_BASE_SRC to a git-archive of the base commit")
def test_old_reader_accepts_the_new_partial_snapshot(tmp_path, monkeypatch):
    snap = _partial(tmp_path, monkeypatch)
    code = (
        "import sys; from dbig_contracts.artifact import read_artifact; "
        "m,_=read_artifact(__import__('pathlib').Path(sys.argv[1])); print(m.completeness)"
    )
    paths = os.pathsep.join(str(Path(BASE_SRC) / "packages" / p / "src") for p in os.listdir(Path(BASE_SRC) / "packages"))
    env = {**os.environ, "PYTHONPATH": paths}
    out = subprocess.run([sys.executable, "-c", code, str(snap)], env=env, capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr[-600:]
    assert out.stdout.strip() == "partial"
