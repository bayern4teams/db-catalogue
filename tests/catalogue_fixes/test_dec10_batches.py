"""DEC-10 (user, 2026-10-13): the review sends candidates in batches of a configured size."""

from __future__ import annotations

import json
import math

import pytest
from catalogue_fixture_kit import (
    MODEL_KEY,
    ModelStub,
    build_snapshot,
    builder_run,
    candidates,
    support_everything,
)
from dbig_builder.config import ReviewConfig
from dbig_contracts.artifact import read_artifact


def _tables(pairs):
    tables = {}
    for number in range(pairs):
        tables[f"parent{number}"] = {
            "fields": [(f"parent{number}_id", "integer")], "key": [f"parent{number}_id"],
        }
        tables[f"child{number}"] = {
            "fields": [("id", "integer"), (f"parent{number}_id", "integer")],
        }
    return tables


def _review(stub, **kwargs):
    return ReviewConfig(
        base_url=stub.url, model="m", model_digest="d", api_key_env="MODEL_KEY",
        timeout_seconds=5, **kwargs,
    )


def _suggestions(snapshot):
    _, records = read_artifact(snapshot)
    return [
        item for item in records["evidence.jsonl"]
        if item.model_dump().get("kind") == "model_suggestion"
    ]


def test_the_default_batch_size_is_five():
    assert ReviewConfig.model_fields["batch_size"].default == 5


@pytest.mark.parametrize("pairs", [1, 3, 6])
def test_candidates_are_sent_in_batches_and_all_are_reviewed(tmp_path, monkeypatch, pairs):
    monkeypatch.setenv("MODEL_KEY", MODEL_KEY)
    with ModelStub(support_everything) as stub:
        snapshot = build_snapshot(
            tmp_path, monkeypatch, {"alpha": {"tables": _tables(pairs)}},
            review=_review(stub, max_candidates=100),
        )
        sizes = [
            len(json.loads(item["body"]["messages"][-1]["content"])) for item in stub.requests
        ]
    total = len(candidates(snapshot))
    assert total >= pairs
    assert len(stub.requests) == math.ceil(total / 5)
    assert max(sizes) <= 5 and sum(sizes) == total
    assert len(_suggestions(snapshot)) == total
    assert read_artifact(snapshot)[0].completeness == "complete"


def test_batch_size_is_configurable(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_KEY", MODEL_KEY)
    with ModelStub(support_everything) as stub:
        snapshot = build_snapshot(
            tmp_path, monkeypatch, {"alpha": {"tables": _tables(4)}},
            review=_review(stub, batch_size=2, max_candidates=100),
        )
    assert len(stub.requests) == math.ceil(len(candidates(snapshot)) / 2)


def test_a_failing_server_is_asked_once_not_once_per_batch(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_KEY", MODEL_KEY)
    with ModelStub(support_everything, status=400) as stub:
        snapshot = build_snapshot(
            tmp_path, monkeypatch, {"alpha": {"tables": _tables(12)}},
            review=_review(stub, max_candidates=100),
        )
    assert len(stub.requests) == 1
    assert read_artifact(snapshot)[0].completeness == "partial"
    assert "MODEL_REJECTED_REQUEST" in builder_run(snapshot)["partial_reasons"]
