"""What can become a reference candidate at all.

Evidence is worthless on a pair that never gets compared. Candidate generation used to require
the child field name to end in `_id` and the parent to carry a declared unique constraint, so a
column called `owner_ref`, or a perfectly unique key on a table that never declared one, could
not become a candidate however well its values contained.

Widening this trades precision for recall if done carelessly, so each rule here is deliberately
narrow and each test says which rule it is exercising. The bare-name rule in particular requires
an exact match rather than a fuzzy one: fuzzy-matching every field against every unique key would
admit a flood of pairs whose only support is a coincidence of spelling.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from dbig_builder.builder import MEASURED_UNIQUE, assemble
from dbig_builder.config import BuilderConfig
from dbig_contracts.artifact import read_artifact, write_artifact
from dbig_contracts.ids import stable_id
from dbig_contracts.models import Field, Object, Profile, Source

BUNDLE_MATERIAL = "synthetic-bundle-signing-material-1234567890"


def _common(source_id: str = "site-a") -> dict:
    return {
        "source_id": source_id,
        "observed_at": datetime.now(UTC),
        "collector_version": "test",
        "provenance": "synthetic",
    }


def _snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    child_name: str,
    parent_object_name: str = "customers",
    declare_unique: bool = True,
    parent_uniqueness: float | None = None,
    child_null_fraction: float | None = None,
) -> dict:
    """Assemble a two-table snapshot and return its edges and evidence.

    One child field on `orders`, one candidate key on `parent_object_name`, and whichever of a
    declared constraint or a measured profile the test is about.
    """
    monkeypatch.setenv("BUNDLE_KEY", BUNDLE_MATERIAL)
    common = _common()
    orders = Object(
        id=stable_id("site-a", "object", "1"), native_id="1", kind="table", name="orders", **common
    )
    parent_object = Object(
        id=stable_id("site-a", "object", "2"), native_id="2", kind="table",
        name=parent_object_name, **common,
    )
    child = Field(
        id=stable_id("site-a", "field", "1:1"), object_id=orders.id, native_id="1:1",
        name=child_name, data_type="integer", ordinal=1, **common,
    )
    parent = Field(
        id=stable_id("site-a", "field", "2:1"), object_id=parent_object.id, native_id="2:1",
        name="id", data_type="integer", ordinal=1, **common,
    )
    profiles = []
    if parent_uniqueness is not None:
        profiles.append(
            Profile(
                id=stable_id("site-a", "profile", f"{parent.id}:stat"), field_id=parent.id,
                object_id=parent_object.id, method="source_statistic",
                uniqueness=parent_uniqueness, **common,
            )
        )
    if child_null_fraction is not None:
        profiles.append(
            Profile(
                id=stable_id("site-a", "profile", f"{child.id}:stat"), field_id=child.id,
                object_id=orders.id, method="source_statistic",
                null_fraction=child_null_fraction, **common,
            )
        )
    constraints = []
    if declare_unique:
        constraints.append(
            {
                "record_type": "constraint",
                "id": stable_id("site-a", "constraint", "pk"),
                "native_id": "pk",
                "kind": "primary",
                "object_id": parent_object.id,
                "field_names": ["id"],
                **common,
            }
        )
    write_artifact(
        tmp_path / "bundle",
        "collector-bundle",
        {
            "sources.jsonl": [
                Source(id="site-a", kind="postgresql", name="site-a", **common)
            ],
            "objects.jsonl": [orders, parent_object],
            "fields.jsonl": [child, parent],
            "constraints.jsonl": constraints,
            "profiles.jsonl": profiles,
        },
        producer="test",
        source_run_ids=["run-a"],
        signing_key=BUNDLE_MATERIAL.encode(),
        key_id="test",
    )
    assemble(
        [tmp_path / "bundle"],
        tmp_path / "snapshot",
        BuilderConfig(signing_keys={"test": "BUNDLE_KEY"}),
    )
    _, data = read_artifact(tmp_path / "snapshot")
    return {
        "candidates": [
            edge for edge in data["edges.jsonl"]
            if edge.model_dump()["kind"] == "REFERENCE_CANDIDATE"
        ],
        "evidence": [item.model_dump() for item in data["evidence.jsonl"]],
    }


def _kinds(result: dict) -> set[str]:
    return {item["kind"] for item in result["evidence"]}


# --- which child names can point somewhere ----------------------------------------------


@pytest.mark.parametrize("child_name", ["customer_id", "customer_ref", "customer_key",
                                        "customer_code", "customer_uuid", "customer_fk",
                                        "customer_no", "customer_num"])
def test_a_reference_suffix_admits_a_candidate(tmp_path, monkeypatch, child_name):
    """Only `_id` was recognised before, so every other conventional suffix was invisible."""
    result = _snapshot(tmp_path, monkeypatch, child_name=child_name)
    assert len(result["candidates"]) == 1, f"{child_name} must be able to become a candidate"


def test_a_bare_name_matching_the_parent_table_admits_a_candidate(tmp_path, monkeypatch):
    """A column called `customer` pointing at `customers` has no suffix to announce itself."""
    result = _snapshot(tmp_path, monkeypatch, child_name="customer")
    assert len(result["candidates"]) == 1


def test_a_bare_name_that_does_not_match_is_refused(tmp_path, monkeypatch):
    """The bare-name rule is exact on purpose. A fuzzy one here would admit a flood of pairs
    whose only support is a coincidence of spelling."""
    result = _snapshot(tmp_path, monkeypatch, child_name="status")
    assert result["candidates"] == []


def test_a_near_miss_bare_name_is_still_refused(tmp_path, monkeypatch):
    """`custom` is one letter from `customer` and would pass a fuzzy threshold comfortably."""
    result = _snapshot(tmp_path, monkeypatch, child_name="custom")
    assert result["candidates"] == []


def test_the_blocking_rule_is_recorded(tmp_path, monkeypatch):
    suffixed = _snapshot(tmp_path / "a", monkeypatch, child_name="customer_ref")
    exact = _snapshot(tmp_path / "b", monkeypatch, child_name="customer")
    rules = {
        item["metrics"]["blocking_rule"]
        for result in (suffixed, exact)
        for item in result["evidence"]
        if item["kind"] == "name_similarity"
    }
    assert rules == {"suffix", "exact_name"}


def test_a_suffix_that_is_the_whole_name_is_not_stripped(tmp_path, monkeypatch):
    """A field actually called `id` must not become an empty stem that matches everything."""
    result = _snapshot(tmp_path, monkeypatch, child_name="id")
    assert result["candidates"] == []


# --- which parents can be pointed at ----------------------------------------------------


def test_a_measured_unique_field_can_be_a_parent_without_a_declaration(tmp_path, monkeypatch):
    """A table that never declared a primary key could not be referenced at all before."""
    result = _snapshot(
        tmp_path, monkeypatch, child_name="customer_id", declare_unique=False,
        parent_uniqueness=1.0,
    )
    assert len(result["candidates"]) == 1
    assert "measured_uniqueness" in _kinds(result)
    assert "catalog_key" not in _kinds(result), (
        "a measurement must not be recorded as a catalog assertion"
    )


def test_a_declared_key_is_still_recorded_as_declared(tmp_path, monkeypatch):
    result = _snapshot(tmp_path, monkeypatch, child_name="customer_id", parent_uniqueness=1.0)
    assert "catalog_key" in _kinds(result)
    assert "measured_uniqueness" not in _kinds(result)


def test_the_measured_uniqueness_threshold_is_pinned():
    """Pinned as an absolute value, not derived from the constant.

    The first version of the test below computed its input as `MEASURED_UNIQUE - 0.05`, so it moved
    with the constant and could never detect a change to it; mutation testing caught that by
    dropping the threshold to 0.5 with every test still passing. A threshold that decides which
    relationships get proposed is a contract, so changing it should require changing a test.
    """
    assert MEASURED_UNIQUE == 0.99


@pytest.mark.parametrize("uniqueness", [0.5, 0.9, 0.98])
def test_measured_uniqueness_below_the_threshold_is_not_a_key(tmp_path, monkeypatch, uniqueness):
    """Nearly unique is not unique: a duplicate in the parent breaks the reference it implies."""
    result = _snapshot(
        tmp_path, monkeypatch, child_name="customer_id", declare_unique=False,
        parent_uniqueness=uniqueness,
    )
    assert result["candidates"] == []


def test_the_measured_evidence_carries_its_number_and_threshold(tmp_path, monkeypatch):
    result = _snapshot(
        tmp_path, monkeypatch, child_name="customer_id", declare_unique=False,
        parent_uniqueness=1.0,
    )
    measured = next(
        item for item in result["evidence"] if item["kind"] == "measured_uniqueness"
    )
    assert measured["metrics"]["uniqueness"] == 1.0
    assert measured["metrics"]["threshold"] == MEASURED_UNIQUE


# --- which children are worth comparing -------------------------------------------------


def test_an_entirely_null_child_is_not_compared(tmp_path, monkeypatch):
    """A field with no values references nothing, so name evidence for it would describe a
    relationship with nothing behind it."""
    result = _snapshot(tmp_path, monkeypatch, child_name="customer_id", child_null_fraction=1.0)
    assert result["candidates"] == []


def test_a_mostly_null_child_is_still_compared(tmp_path, monkeypatch):
    """Sparse is not empty. An optional foreign key is mostly null by design."""
    result = _snapshot(tmp_path, monkeypatch, child_name="customer_id", child_null_fraction=0.99)
    assert len(result["candidates"]) == 1
