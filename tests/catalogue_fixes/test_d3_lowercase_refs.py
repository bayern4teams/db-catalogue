"""D3 + B5(2): a lowercase name with no separator is a reference when its stem is a known object.

User decision 2026-10-12 (plan section 9, Phase 2): read `vendorid` as `vendor` + `id` only when
the stem is a table or collection name in the catalogue, normalised the way the existing parent
name comparison does. Names whose stem is not a known object (`xid`, `valid`, `paid`) propose
nothing. The same-table exclusion, the type-family rule, the declared-key-first rule and the case
boundary rule behave as before.

The report's real case is a PostgreSQL schema with no declared keys and keys known only from
planner statistics. With a *declared* key the existing `key_name` rule already links a child named
exactly like the parent's key, so that variant is a control, not a fix: the report's example
over-claims for declared keys.

Tests marked xfail fail on the pre-change code; the others are controls.
"""

from __future__ import annotations

from catalogue_fixture_kit import build_snapshot, candidates

NEVER_REFERENCES = ["xid", "valid", "paid"]


def _found(tmp_path, monkeypatch, tables, name="snapshot"):
    snapshot = build_snapshot(tmp_path, monkeypatch, {"alpha": {"tables": tables}}, name=name)
    return candidates(snapshot)


def _undeclared(parent="vendor", other="buyer"):
    """Keys known only from measured uniqueness, as a PostgreSQL schema with no constraints."""
    return {
        parent: {"fields": [("vendorid", "integer"), ("label", "text"), ("code", "integer")],
                 "unique": {"vendorid": 1.0, "code": 1.0}},
        other: {"fields": [("buyerid", "integer"), ("label", "text")],
                "unique": {"buyerid": 1.0}},
        "shipment": {"fields": [
            ("id", "integer"), ("vendorid", "integer"), ("buyerid", "integer"),
            ("xid", "integer"), ("valid", "integer"), ("paid", "integer"),
            ("codeid", "integer"), ("labelid", "integer"),
        ]},
    }


# --- the fix ------------------------------------------------------------------------------------


def test_a_lowercase_name_links_when_its_stem_is_a_known_table(tmp_path, monkeypatch):
    found = _found(tmp_path, monkeypatch, _undeclared())
    assert ("shipment", "vendorid", "vendor", "vendorid") in found, found
    assert ("shipment", "buyerid", "buyer", "buyerid") in found, found


def test_the_stem_is_matched_to_a_plural_table_as_the_snake_case_form_already_is(
    tmp_path, monkeypatch
):
    found = _found(tmp_path, monkeypatch, _undeclared(parent="vendors", other="buyers"))
    assert ("shipment", "vendorid", "vendors", "vendorid") in found, found


def test_the_lowercase_rule_is_not_limited_to_one_table_pair(tmp_path, monkeypatch):
    """Exactly the two real references are proposed, and nothing else from the same child."""
    found = _found(tmp_path, monkeypatch, _undeclared())
    # Which parent column is chosen when a table has several unique ones is A2 (DEC-1), not D3:
    # the snake_case form `vendor_id` also reaches `vendor.code`. So compare the object pairs.
    shipment = {(item[0], item[1], item[2]) for item in found if item[0] == "shipment"}
    assert shipment == {
        ("shipment", "buyerid", "buyer"),
        ("shipment", "vendorid", "vendor"),
    }, shipment


# --- what must not change -----------------------------------------------------------------------


def test_a_stem_that_is_not_a_known_object_proposes_nothing(tmp_path, monkeypatch):
    """`xid`, `valid` and `paid` read as x/val/pa + id, none of which is a table. `codeid` and
    `labelid` name a column of another table, not an object, so they propose nothing either."""
    found = _found(tmp_path, monkeypatch, _undeclared())
    assert not [item for item in found if item[1] in NEVER_REFERENCES + ["codeid", "labelid"]]


def test_the_same_snake_case_names_already_link_today(tmp_path, monkeypatch):
    """Control that fixes the expectation for the lowercase form: the separated form works."""
    tables = _undeclared()
    tables["vendor"]["fields"][0] = ("vendor_id", "integer")
    tables["vendor"]["unique"] = {"vendor_id": 1.0}
    tables["shipment"]["fields"][1] = ("vendor_id", "integer")
    found = _found(tmp_path, monkeypatch, tables)
    assert ("shipment", "vendor_id", "vendor", "vendor_id") in found, found


def test_a_plural_parent_already_links_through_the_separated_form(tmp_path, monkeypatch):
    tables = _undeclared(parent="vendors")
    tables["vendors"]["fields"][0] = ("vendor_id", "integer")
    tables["vendors"]["unique"] = {"vendor_id": 1.0}
    tables["shipment"]["fields"][1] = ("vendor_id", "integer")
    found = _found(tmp_path, monkeypatch, tables)
    assert ("shipment", "vendor_id", "vendors", "vendor_id") in found, found


def test_camel_case_still_links(tmp_path, monkeypatch):
    """The case-boundary rule: `vendorId` is `vendor_id`, which the existing rules handle."""
    tables = _undeclared()
    tables["shipment"]["fields"][1] = ("vendorId", "integer")
    found = _found(tmp_path, monkeypatch, tables)
    assert ("shipment", "vendorId", "vendor", "vendorid") in found, found


def test_a_declared_key_already_admits_a_child_named_like_it(tmp_path, monkeypatch):
    """Control, and the over-claim: with a declared key the existing `key_name` rule already
    links `shipment.vendorid` to `vendor.vendorid`. D3's fix matters only where keys are not
    declared, which is what the report's PostgreSQL schema is."""
    tables = _undeclared()
    tables["vendor"]["key"] = ["vendorid"]
    found = _found(tmp_path, monkeypatch, tables)
    assert ("shipment", "vendorid", "vendor", "vendorid") in found, found


def test_the_parent_table_never_references_itself(tmp_path, monkeypatch):
    """The same-table exclusion: `vendor.vendorid` is unique and its stem is `vendor`, but a
    table does not reference itself."""
    tables = _undeclared()
    tables["gizmo"] = {"fields": [("id", "integer"), ("gizmo_id", "integer")], "key": ["id"]}
    tables["log"] = {"fields": [("id", "integer"), ("gizmo_id", "integer")]}
    found = _found(tmp_path, monkeypatch, tables)
    assert ("log", "gizmo_id", "gizmo", "id") in found, "the fixture links across tables"
    assert not [item for item in found if item[0] == item[2]], found


def test_the_type_family_rule_still_blocks_a_mismatched_type(tmp_path, monkeypatch):
    """The snake-case child is blocked today and the lowercase child must be blocked once D3
    lets it through (the lowercase half is vacuous until then). The integer child proves the
    fixture would link if the types agreed."""
    found = _found(tmp_path, monkeypatch, {
        "gizmo": {"fields": [("id", "integer"), ("vendorid", "integer")], "key": ["id"],
                  "unique": {"vendorid": 1.0}},
        "audit": {"fields": [("id", "integer"), ("gizmo_id", "boolean"), ("vendorid", "boolean")]},
        "log": {"fields": [("id", "integer"), ("gizmo_id", "integer")]},
        "vendor": {"fields": [("vendorid", "integer")], "unique": {"vendorid": 1.0}},
    })
    assert ("log", "gizmo_id", "gizmo", "id") in found, found
    assert not [item for item in found if item[0] == "audit"], found


def test_a_declared_key_still_beats_a_column_that_only_measures_unique(tmp_path, monkeypatch):
    """The declared-key-first rule: `region.code` is declared, `region.name` merely measures
    unique, and only the declared key is referenced."""
    found = _found(tmp_path, monkeypatch, {
        "region": {"fields": [("code", "text"), ("name", "text")], "key": ["code"],
                   "unique": {"code": 1.0, "name": 1.0}},
        "shop": {"fields": [("id", "integer"), ("region_code", "text"), ("region", "text")]},
    })
    assert [item for item in found if item[3] == "name"] == []
    assert ("shop", "region_code", "region", "code") in found


def test_a_stem_that_names_a_table_only_by_a_longer_name_stays_unproposed_until_dec12(
    tmp_path, monkeypatch
):
    """`classificationid` has stem `classification`, but the table is `tradeclassification`.
    The D3 rule (stem must be a known object) does not fix it; that is DEC-12, user-gated. When
    DEC-12 lands this control must be flipped by the lead on purpose, not by accident."""
    found = _found(tmp_path, monkeypatch, {
        "tradeclassification": {"fields": [("classificationid", "integer"), ("title", "text")],
                                   "unique": {"classificationid": 1.0}},
        "vendortrade": {"fields": [("id", "integer"), ("classificationid", "integer")]},
    })
    assert found == [], found
