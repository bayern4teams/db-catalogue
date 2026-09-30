"""Candidates against declared multi-column keys.

A two-column natural key such as `(number, region)` could never be proposed before, because
candidate generation compared single fields only. Each rule here exists to keep that widening
precision-first, and each test names the rule it exercises:

- every component must match, since a partial tuple says nothing about the missing component;
- at least one component must be named for the parent, as `<singular parent>_<component>`;
- the source's declared component order is kept, since a reordered tuple is a different tuple;
- no value evidence is attached, since per-field tokens cannot measure tuple containment.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from pathlib import Path

import pytest
from dbig_builder.builder import assemble
from dbig_builder.config import BuilderConfig
from dbig_contracts.artifact import read_artifact, write_artifact
from dbig_contracts.ids import stable_id
from dbig_contracts.models import Field, Object, Profile, Source, ValueTokens

BUNDLE_MATERIAL = "synthetic-bundle-signing-material-1234567890"
TOKEN_MATERIAL = b"synthetic-token-material-not-a-credential"

# The parent `orders` declares a composite key; its singular form is `order`.
PARENT_COLUMNS = {
    "number": "integer", "region": "text", "id": "integer", "code": "text", "order_id": "integer",
}


def _common() -> dict:
    return {
        "source_id": "site-a",
        "observed_at": datetime.now(UTC),
        "collector_version": "test",
        "provenance": "synthetic",
    }


def _field(object_id: str, table: int, ordinal: int, name: str, data_type: str) -> Field:
    native = f"{table}:{ordinal}"
    return Field(
        id=stable_id("site-a", "field", native), object_id=object_id, native_id=native,
        name=name, data_type=data_type, ordinal=ordinal, **_common(),
    )


def _tokens(field: Field, values: list[str]) -> ValueTokens:
    digests = sorted(
        {hmac.new(TOKEN_MATERIAL, value.encode(), hashlib.sha256).hexdigest() for value in values}
    )
    return ValueTokens(
        id=stable_id("site-a", "tokens", field.id), field_id=field.id, object_id=field.object_id,
        key_id="run-1", sample_count=len(values), sample_method="first_rows_limit:10",
        observed_count=len(values), distinct_count=len(digests), truncated=False,
        tokens=digests, **_common(),
    )


def _snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    child: dict[str, str],
    keys: list[list[str]] | None = None,
    by_ordinal: bool = False,
    declare_fk: bool = False,
    null_child: str | None = None,
    with_tokens: bool = False,
) -> dict:
    """Assemble `invoices` (the child, with columns `child`) against `orders` (the parent).

    `keys` lists the parent's composite keys by column name, in declared order; the first is the
    primary key and any others are unique constraints.
    """
    monkeypatch.setenv("BUNDLE_KEY", BUNDLE_MATERIAL)
    common = _common()
    orders = Object(
        id=stable_id("site-a", "object", "1"), native_id="1", kind="table", name="orders",
        **common,
    )
    invoices = Object(
        id=stable_id("site-a", "object", "2"), native_id="2", kind="table", name="invoices",
        **common,
    )
    parent_fields = {
        name: _field(orders.id, 1, ordinal, name, data_type)
        for ordinal, (name, data_type) in enumerate(PARENT_COLUMNS.items(), start=1)
    }
    child_fields = {
        name: _field(invoices.id, 2, ordinal, name, data_type)
        for ordinal, (name, data_type) in enumerate(child.items(), start=1)
    }
    constraints = []
    for position, key in enumerate(keys if keys is not None else [["number", "region"]]):
        members: dict = (
            {"field_ordinals": [parent_fields[name].ordinal for name in key]}
            if by_ordinal
            else {"field_names": key}
        )
        constraints.append(
            {
                "record_type": "constraint",
                "id": stable_id("site-a", "constraint", f"key-{position}"),
                "native_id": f"key-{position}",
                "kind": "primary" if position == 0 else "unique",
                "object_id": orders.id,
                **members,
                **common,
            }
        )
    if declare_fk:
        constraints.append(
            {
                "record_type": "constraint",
                "id": stable_id("site-a", "constraint", "fk"),
                "native_id": "fk",
                "kind": "foreign",
                "object_id": invoices.id,
                "target_object_id": orders.id,
                "field_ordinals": [1, 2],
                "target_field_ordinals": [1, 2],
                **common,
            }
        )
    profiles = []
    if null_child is not None:
        profiles.append(
            Profile(
                id=stable_id("site-a", "profile", child_fields[null_child].id),
                field_id=child_fields[null_child].id, object_id=invoices.id,
                method="source_statistic", null_fraction=1.0, **common,
            )
        )
    tokens = []
    if with_tokens:
        # Perfect per-field containment on every component. It still says nothing about which
        # combinations occur, so it must not become tuple evidence.
        tokens = [
            _tokens(parent_fields["number"], ["1", "2", "3", "4", "5"]),
            _tokens(parent_fields["region"], ["eu", "us", "ap", "sa", "af"]),
            _tokens(child_fields["order_number"], ["1", "2", "3", "4"]),
            _tokens(child_fields["region"], ["eu", "us", "ap", "sa"]),
        ]
    write_artifact(
        tmp_path / "bundle",
        "collector-bundle",
        {
            "sources.jsonl": [Source(id="site-a", kind="postgresql", name="site-a", **common)],
            "objects.jsonl": [orders, invoices],
            "fields.jsonl": [*parent_fields.values(), *child_fields.values()],
            "constraints.jsonl": constraints,
            "profiles.jsonl": profiles,
            "tokens.jsonl": tokens,
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
    evidence = {item.id: item.model_dump() for item in data["evidence.jsonl"]}
    names = {
        field.id: field.name for field in [*parent_fields.values(), *child_fields.values()]
    }
    # Identified by the evidence they cite, not by pair count: selecting on "more than one pair"
    # let a wrongly emitted one-component partial tuple slip past every assertion here, which
    # mutation testing caught.
    composite = [
        edge.model_dump()
        for edge in data["edges.jsonl"]
        if edge.model_dump()["kind"] == "REFERENCE_CANDIDATE"
        and any(
            evidence[item]["kind"] == "composite_name_match"
            for item in edge.model_dump()["evidence_ids"]
        )
    ]
    return {
        "composite": composite,
        "pairs": [
            [[names[left], names[right]] for left, right in edge["field_pairs"]]
            for edge in composite
        ],
        "cited": [[evidence[item] for item in edge["evidence_ids"]] for edge in composite],
        "evidence": evidence,
    }


def test_a_full_match_named_for_the_parent_becomes_one_candidate(tmp_path, monkeypatch):
    result = _snapshot(
        tmp_path, monkeypatch, child={"order_number": "integer", "region": "text"}
    )
    assert result["pairs"] == [[["order_number", "number"], ["region", "region"]]]
    edge = result["composite"][0]
    assert edge["status"] == "tentative"
    assert edge["confidence"] is None


def test_a_key_given_by_ordinal_is_matched_the_same_way(tmp_path, monkeypatch):
    result = _snapshot(
        tmp_path, monkeypatch, child={"order_number": "integer", "region": "text"},
        by_ordinal=True,
    )
    assert result["pairs"] == [[["order_number", "number"], ["region", "region"]]]


def test_the_count_of_components_named_for_the_parent_is_recorded(tmp_path, monkeypatch):
    one = _snapshot(
        tmp_path / "a", monkeypatch, child={"order_number": "integer", "region": "text"}
    )
    both = _snapshot(
        tmp_path / "b", monkeypatch, child={"order_number": "integer", "order_region": "text"}
    )
    counts = [
        next(item for item in result["cited"][0] if item["kind"] == "composite_name_match")[
            "metrics"
        ]["components_named_for_parent"]
        for result in (one, both)
    ]
    assert counts == [1, 2]


# --- every component must match -----------------------------------------------------------


def test_a_partial_tuple_is_not_a_candidate(tmp_path, monkeypatch):
    """Matching `number` says nothing about `region`, so a tuple reference is not implied."""
    result = _snapshot(tmp_path, monkeypatch, child={"order_number": "integer"})
    assert result["composite"] == []


def test_a_component_of_the_wrong_type_family_breaks_the_tuple(tmp_path, monkeypatch):
    result = _snapshot(
        tmp_path, monkeypatch, child={"order_number": "integer", "region": "integer"}
    )
    assert result["composite"] == []


def test_an_entirely_null_component_breaks_the_tuple(tmp_path, monkeypatch):
    """The single-field path's rule, applied per component: no values, no reference."""
    result = _snapshot(
        tmp_path, monkeypatch, child={"order_number": "integer", "region": "text"},
        null_child="region",
    )
    assert result["composite"] == []


def test_one_child_field_cannot_stand_for_two_components(tmp_path, monkeypatch):
    """For a parent key `(id, order_id)`, the child's `order_id` is both `<order>_id` and a bare
    `order_id`. Letting it fill both slots would propose a tuple whose components are one column.
    """
    result = _snapshot(
        tmp_path, monkeypatch, child={"order_id": "integer"}, keys=[["id", "order_id"]],
    )
    assert result["composite"] == []


# --- at least one component must be named for the parent ----------------------------------


def test_bare_shared_column_names_are_not_a_reference(tmp_path, monkeypatch):
    """Two unrelated tables that both have `number` and `region` columns are just that."""
    result = _snapshot(tmp_path, monkeypatch, child={"number": "integer", "region": "text"})
    assert result["composite"] == []


# --- declared order is kept ---------------------------------------------------------------


def test_the_declared_component_order_is_kept(tmp_path, monkeypatch):
    """The key is declared `(region, number)` and the child's columns are in the other order;
    the pairs must follow the declaration, not either table's column order."""
    result = _snapshot(
        tmp_path, monkeypatch, child={"order_number": "integer", "region": "text"},
        keys=[["region", "number"]],
    )
    assert result["pairs"] == [[["region", "region"], ["order_number", "number"]]]


# --- no invented tuple measurement ----------------------------------------------------------


def test_per_field_tokens_do_not_become_tuple_evidence(tmp_path, monkeypatch):
    """Perfect containment on every component separately is still no measurement of the
    combinations, so nothing value-based may be cited and the evidence must say so."""
    result = _snapshot(
        tmp_path, monkeypatch, child={"order_number": "integer", "region": "text"},
        with_tokens=True,
    )
    assert len(result["composite"]) == 1
    kinds = sorted(item["kind"] for item in result["cited"][0])
    assert kinds == ["catalog_key", "composite_name_match"]
    name_match = next(item for item in result["cited"][0] if item["kind"] == "composite_name_match")
    assert name_match["metrics"]["tuple_containment_measured"] is False


# --- interaction with declarations and with other keys -------------------------------------


def test_a_declared_foreign_key_suppresses_the_candidate(tmp_path, monkeypatch):
    result = _snapshot(
        tmp_path, monkeypatch, child={"order_number": "integer", "region": "text"},
        declare_fk=True,
    )
    assert result["composite"] == []


def test_two_matched_keys_on_one_parent_are_two_candidates(tmp_path, monkeypatch):
    """Identifiers used to be derived from the two tables alone, so the second key's candidate
    silently overwrote the first."""
    result = _snapshot(
        tmp_path, monkeypatch,
        child={"order_number": "integer", "region": "text", "order_code": "text"},
        keys=[["number", "region"], ["code", "region"]],
    )
    assert sorted(result["pairs"]) == [
        [["order_code", "code"], ["region", "region"]],
        [["order_number", "number"], ["region", "region"]],
    ]
    cited = {
        evidence_id for edge in result["composite"] for evidence_id in edge["evidence_ids"]
    }
    assert len(cited) == 4, "each candidate must cite its own evidence, not share overwritten ids"
