# ruff: noqa
"""Attack H1(c) for B2: telemetry per batch must not lose decisions or change the outcome.
Run: uv run pytest tests/catalogue_fixes/attack3_c_b2_telemetry.py -q -p no:cacheprovider"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from attack3_c_b2_batches import Script, child_tables
from catalogue_fixture_kit import MODEL_KEY, build_snapshot, builder_run, closed_port_url
from dbig_builder.config import ReviewConfig
from dbig_contracts.artifact import read_artifact


def test_an_unreachable_langfuse_does_not_lose_batches_or_flip_the_outcome(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_KEY", MODEL_KEY)
    monkeypatch.setenv("LF_PUBLIC", "pk-lf-test")
    monkeypatch.setenv("LF_SECRET", "sk-lf-test")
    with Script() as script:
        review = ReviewConfig(
            base_url=script.url, model="m", model_digest="d", api_key_env="MODEL_KEY",
            timeout_seconds=3, langfuse_url=closed_port_url().replace("/v1", ""),
            langfuse_public_key_env="LF_PUBLIC", langfuse_secret_key_env="LF_SECRET",
        )
        started = time.monotonic()
        snap = build_snapshot(tmp_path, monkeypatch, {"alpha": {"tables": child_tables(12)}}, review=review)
        elapsed = time.monotonic() - started
        calls = len(script.calls)
    run = builder_run(snap)
    _, data = read_artifact(snap)
    kept = [i for i in data["evidence.jsonl"] if i.model_dump()["kind"] == "model_suggestion"]
    assert calls == 3
    assert run["review_status"] == "OK", run
    assert len(kept) == 12
    assert elapsed < 30
