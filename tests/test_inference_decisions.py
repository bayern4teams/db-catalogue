"""Candidate rules decided by the user on 2026-10-05, after real-shaped schemas exposed them.

1. A table that declares a key is referenced through that key. Measured uniqueness is only for
   tables that declare none: on a two-row lookup table every column measures unique, and
   `regions.name` was proposed beside the declared `regions.code`.
2. camelCase and PascalCase names reach the suffix rule (`customerId` is `customer_id`), and a
   child named exactly like a parent's declared single-column key (`AccountID` beside a primary
   key `AccountID`) is admitted whatever the parent table is called, except a generic `id`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from dbig_builder.builder import _reference_stem, _snake, assemble
from dbig_builder.config import BuilderConfig
from dbig_contracts.artifact import read_artifact, write_artifact
from dbig_contracts.ids import stable_id
from dbig_contracts.models import Field, Object, Profile, Source

KEY = "synthetic-bundle-signing-material-1234567890"


def _assemble(tmp_path: Path, monkeypatch, tables: dict[str, dict]) -> list[tuple]:
    """Each table: `fields` [(name, type)], optional `key` [names], optional `unique` {field:
    measured uniqueness}. Returns candidates as (child table, child field, parent table, parent
    field, blocking rule)."""
    monkeypatch.setenv("BUNDLE_KEY", KEY)
    common = {"source_id": "site-a", "observed_at": datetime.now(UTC),
              "collector_version": "test", "provenance": "synthetic"}
    objects, fields, constraints, profiles = [], [], [], []
    for number, (table, spec) in enumerate(tables.items(), start=1):
        item = Object(id=stable_id("site-a", "object", str(number)), native_id=str(number),
                      kind="table", name=table, **common)
        objects.append(item)
        for ordinal, (name, data_type) in enumerate(spec["fields"], start=1):
            field = Field(id=stable_id("site-a", "field", f"{number}:{ordinal}"),
                          object_id=item.id, native_id=f"{number}:{ordinal}", name=name,
                          data_type=data_type, ordinal=ordinal, **common)
            fields.append(field)
            if name in spec.get("unique", {}):
                profiles.append(Profile(
                    id=stable_id("site-a", "profile", f"{field.id}:stat"), field_id=field.id,
                    object_id=item.id, method="source_statistic",
                    uniqueness=spec["unique"][name], **common,
                ))
        if spec.get("key"):
            constraints.append({
                "record_type": "constraint", "id": stable_id("site-a", "constraint", table),
                "native_id": table, "kind": "primary", "object_id": item.id,
                "field_names": spec["key"], **common,
            })
    write_artifact(
        tmp_path / "bundle", "collector-bundle",
        {"sources.jsonl": [Source(id="site-a", kind="postgresql", name="site-a", **common)],
         "objects.jsonl": objects, "fields.jsonl": fields,
         "constraints.jsonl": constraints, "profiles.jsonl": profiles},
        producer="test", source_run_ids=["run-a"], signing_key=KEY.encode(), key_id="test",
    )
    assemble([tmp_path / "bundle"], tmp_path / "snapshot",
             BuilderConfig(signing_keys={"test": "BUNDLE_KEY"}))
    _, data = read_artifact(tmp_path / "snapshot")
    names = {item.id: item.name for item in data["nodes.jsonl"]}
    owners = {item.id: names[item.object_id] for item in data["nodes.jsonl"]
              if item.record_type == "field"}
    rules = {item.id: item.metrics.get("blocking_rule") for item in data["evidence.jsonl"]
             if item.kind == "name_similarity"}
    found = []
    for edge in data["edges.jsonl"]:
        if edge.kind != "REFERENCE_CANDIDATE":
            continue
        (child, parent), = edge.model_extra["field_pairs"]
        rule = next(rules[key] for key in edge.evidence_ids if key in rules)
        found.append((owners[child], names[child], owners[parent], names[parent], rule))
    return sorted(found)


def test_a_declared_key_wins_over_columns_that_merely_measure_unique(tmp_path, monkeypatch):
    found = _assemble(tmp_path, monkeypatch, {
        "regions": {"fields": [("code", "text"), ("name", "text")], "key": ["code"],
                    "unique": {"code": 1.0, "name": 1.0}},
        "shops": {"fields": [("id", "integer"), ("region_code", "text"), ("region", "text")]},
    })
    assert ("shops", "region_code", "regions", "code", "suffix") in found
    assert not [item for item in found if item[3] == "name"], found


def test_measured_uniqueness_still_finds_a_key_on_a_table_that_declares_none(
    tmp_path, monkeypatch
):
    found = _assemble(tmp_path, monkeypatch, {
        "regions": {"fields": [("code", "text"), ("name", "text")], "unique": {"code": 1.0}},
        "shops": {"fields": [("id", "integer"), ("region_code", "text")]},
    })
    assert found == [("shops", "region_code", "regions", "code", "suffix")]


def test_a_composite_declared_key_also_blocks_measured_columns(tmp_path, monkeypatch):
    found = _assemble(tmp_path, monkeypatch, {
        "orders": {"fields": [("tenant_id", "integer"), ("order_no", "integer"),
                              ("ref", "text")], "key": ["tenant_id", "order_no"],
                   "unique": {"ref": 1.0}},
        "notes": {"fields": [("id", "integer"), ("order_ref", "text")]},
    })
    assert not [item for item in found if item[3] == "ref"], found


@pytest.mark.parametrize(
    ("name", "snake"),
    [("AccountID", "account_id"), ("customerId", "customer_id"),
     ("HTTPServerID", "http_server_id"), ("order_id", "order_id"), ("Region", "region"),
     ("e-mail", "e-mail"), ("ID", "id")],
)
def test_names_split_at_case_boundaries(name, snake):
    assert _snake(name) == snake


def test_a_camel_case_suffix_reaches_the_suffix_rule(tmp_path, monkeypatch):
    assert _reference_stem("customerId") == ("customer", True)
    found = _assemble(tmp_path, monkeypatch, {
        "customers": {"fields": [("id", "integer")], "key": ["id"]},
        "orders": {"fields": [("id", "integer"), ("customerId", "integer")], "key": ["id"]},
    })
    assert found == [("orders", "customerId", "customers", "id", "suffix")]


def test_a_child_named_like_the_parents_key_is_admitted(tmp_path, monkeypatch):
    """`AccountID` beside the primary key `"Customer Accounts"."AccountID"`: the stem `account`
    is too far from `customer account` for the fuzzy suffix rule, but the key name is exact."""
    found = _assemble(tmp_path, monkeypatch, {
        "Customer Accounts": {"fields": [("AccountID", "integer"), ("e-mail", "text")],
                              "key": ["AccountID"]},
        "Page Views": {"fields": [("id", "integer"), ("AccountID", "Nullable(Int32)")],
                       "key": ["id"]},
    })
    assert found == [("Page Views", "AccountID", "Customer Accounts", "AccountID", "key_name")]


def test_a_generic_key_name_is_not_a_reference(tmp_path, monkeypatch):
    """Every table has an `id`; a child `id` must not point at every primary key."""
    found = _assemble(tmp_path, monkeypatch, {
        "customers": {"fields": [("id", "integer")], "key": ["id"]},
        "orders": {"fields": [("id", "integer")], "key": ["id"]},
    })
    assert found == []


def test_the_key_name_rule_needs_a_declared_key(tmp_path, monkeypatch):
    found = _assemble(tmp_path, monkeypatch, {
        "accounts_raw": {"fields": [("AccountRef2", "integer")], "unique": {"AccountRef2": 1.0}},
        "events": {"fields": [("id", "integer"), ("AccountRef2", "integer")], "key": ["id"]},
    })
    assert not [item for item in found if item[4] == "key_name"], found
