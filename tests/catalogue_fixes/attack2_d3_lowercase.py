# ruff: noqa: E501, S108, S603 # C5: reason=adversarial test file kept as its author wrote it; approved=user, 2026-11-08; review=2027-05-08
"""Attack tests for D3 (phase 2): lowercase `companyid` is a reference only for a known stem.

Not collected by default; run by path:
    uv run pytest tests/catalogue_fixes/attack2_d3_lowercase.py -p no:cacheprovider
A failing test is a defect, or, where its docstring says RISK, a gap against the snake_case form
for the lead to decide. Invented names only (`vendor`, `shipment`, `gizmo`).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from catalogue_fixture_kit import build_snapshot, candidates

REPO = Path(__file__).resolve().parents[2]
OLD_TREE = Path(os.environ.get("DBIG_OLD_TREE2", "/tmp/wt-old2"))


def _pairs(tmp_path, monkeypatch, parent, column, *, name="s", child_type="integer", extra=None):
    """Object pairs (child, parent) proposed for one child column against a parent whose only
    unique column is `<stem>key`, an undeclared measured-unique integer."""
    tables = {
        parent: {"fields": [("rowkey", "integer"), ("label", "text")], "unique": {"rowkey": 1.0}},
        "shipment": {"fields": [("id", "integer"), (column, child_type)]},
        **(extra or {}),
    }
    found = candidates(build_snapshot(tmp_path, monkeypatch, {"alpha": {"tables": tables}}, name=name))
    return {(item[0], item[2]) for item in found if item[0] == "shipment"}


@pytest.mark.parametrize("column", ["valid", "paid", "xid", "androidid", "hid", "solid", "void"])
def test_names_whose_stem_is_not_a_table_propose_nothing(tmp_path, monkeypatch, column):
    assert _pairs(tmp_path, monkeypatch, "vendor", column) == set()


@pytest.mark.parametrize("column", ["ids", "id", "vendorids", "vendor", "vendoridx", "idvendor", "vendorid1"])
def test_ids_only_and_malformed_names_do_not_get_the_suffix_reading(tmp_path, monkeypatch, column):
    """`vendor` itself links only through the exact-name rule (a separate, older path)."""
    found = _pairs(tmp_path, monkeypatch, "vendor", column)
    if column == "vendor":
        assert found == {("shipment", "vendor")}
    else:
        assert found == set(), found


@pytest.mark.parametrize(
    "column",
    ["vendorid", "VENDORID", "Vendorid", "vendorno", "vendorref", "vendorkey", "vendorcode",
     "vendornum", "vendoruuid", "vendorfk", "VENDORNO"],
)
def test_every_bare_suffix_reads_when_the_stem_is_known(tmp_path, monkeypatch, column):
    assert _pairs(tmp_path, monkeypatch, "vendor", column) == {("shipment", "vendor")}


@pytest.mark.parametrize("parent", ["vendors", "Vendor", "VENDORS"])
def test_a_plural_or_differently_cased_parent_table_still_matches(tmp_path, monkeypatch, parent):
    assert _pairs(tmp_path, monkeypatch, parent, "vendorid") == {("shipment", parent)}


def test_a_short_table_name_is_the_only_way_a_suffix_lookalike_links(tmp_path, monkeypatch):
    """Characterisation (passes), the collision the rule accepts: `casino` reads as `casi` +
    `no` only if a table called `casi` exists. Without it, nothing; `cleaf` and `monkey` (which
    end in no suffix or in `key`) are unaffected unless a table `mon` exists."""
    assert _pairs(tmp_path, monkeypatch, "vendor", "casino") == set()
    assert _pairs(tmp_path, monkeypatch, "vendor", "cleaf", name="b") == set()
    assert _pairs(tmp_path, monkeypatch, "vendor", "monkey", name="c") == set()
    assert _pairs(tmp_path, monkeypatch, "casi", "casino", name="d") == {("shipment", "casi")}


def test_a_stem_equal_to_the_childs_own_table_never_links_to_itself(tmp_path, monkeypatch):
    """The child's table is `vendor`; its own `vendorid` (also unique) must not self-link."""
    tables = {
        "vendor": {"fields": [("vendorid", "integer"), ("parentvendorid", "integer")],
                   "unique": {"vendorid": 1.0}},
    }
    found = candidates(build_snapshot(tmp_path, monkeypatch, {"alpha": {"tables": tables}}))
    assert not [item for item in found if item[0] == item[2]], found


def test_two_tables_with_one_normalised_name_both_become_candidates(tmp_path, monkeypatch):
    """`vendor` in two sources, a child column `vendorid`: both are proposed, as the separated
    form already does; not one silently overwriting the other."""
    sources = {
        "alpha": {"tables": {"vendor": {"fields": [("rowkey", "integer")], "unique": {"rowkey": 1.0}}}},
        "beta": {"tables": {"vendor": {"fields": [("rowkey", "integer")], "unique": {"rowkey": 1.0}}}},
        "gamma": {"tables": {"shipment": {"fields": [("id", "integer"), ("vendorid", "integer")]}}},
    }
    found = candidates(build_snapshot(tmp_path, monkeypatch, sources))
    assert sorted(found) == [("shipment", "vendorid", "vendor", "rowkey")] * 2, found


def test_the_child_in_a_table_with_the_parents_name_in_another_source(tmp_path, monkeypatch):
    """`beta.vendor.vendorid` against `alpha.vendor`: different objects, so allowed, and the same
    as the snake_case form (parity)."""
    def run(column, name):
        sources = {
            "alpha": {"tables": {"vendor": {"fields": [("rowkey", "integer")], "unique": {"rowkey": 1.0}}}},
            "beta": {"tables": {"vendor": {"fields": [("id", "integer"), (column, "integer")]}}},
        }
        return [(i[0], i[2], i[3]) for i in candidates(build_snapshot(tmp_path, monkeypatch, sources, name=name))]

    assert run("vendorid", "lower") == run("vendor_id", "snake") != []


@pytest.mark.parametrize(
    ("parent", "singular"),
    [("vendor", "vendor"), ("vendors", "vendor"), ("status", "status"),
     ("bus", "bus"), ("category", "category"), ("categories", "category"),
     ("addresses", "address"), ("companies", "company"), ("boxes", "box")],
)
def test_RISK_lowercase_reads_like_the_snake_case_form_for_the_same_parent(
    tmp_path, monkeypatch, parent, singular
):
    """RISK. Parity with the snake_case spelling: `<singular>id` should reach the same parents as
    `<singular>_id`. The rule compares the stem to the name with ALL trailing `s` removed, while
    the snake_case form is fuzzy (ratio >= 80), so singular-stem columns against `-ies` and `-es`
    plurals (`categoryid` -> `categories`, `addressid` -> `addresses`) are not read. The user's
    decision says 'normalised exactly as the existing parent-name comparison does', which the code
    does literally; this test shows the outcome is narrower than the separated form."""
    lower = _pairs(tmp_path, monkeypatch, parent, f"{singular}id", name="low")
    snake = _pairs(tmp_path, monkeypatch, parent, f"{singular}_id", name="snk")
    assert snake, f"fixture must link in the separated form: {parent} {singular}"
    assert lower == snake, (parent, singular, lower, snake)


@pytest.mark.parametrize("child_type", ["boolean", "text", "uuid"])
def test_a_mismatched_type_family_still_blocks_the_new_reading(tmp_path, monkeypatch, child_type):
    assert _pairs(tmp_path, monkeypatch, "vendor", "vendorid", child_type=child_type) == set()


def test_a_declared_key_wins_over_a_measured_unique_sibling_for_the_lowercase_form(tmp_path, monkeypatch):
    tables = {
        "vendor": {"fields": [("code", "integer"), ("alt", "integer")], "key": ["code"],
                   "unique": {"code": 1.0, "alt": 1.0}},
        "shipment": {"fields": [("id", "integer"), ("vendorid", "integer")]},
    }
    found = candidates(build_snapshot(tmp_path, monkeypatch, {"alpha": {"tables": tables}}))
    assert [item[3] for item in found if item[0] == "shipment"] == ["code"], found


def test_an_all_null_lowercase_column_still_proposes_nothing(tmp_path, monkeypatch):
    tables = {
        "vendor": {"fields": [("rowkey", "integer")], "unique": {"rowkey": 1.0}},
        "shipment": {"fields": [("id", "integer"), ("vendorid", "integer")],
                     "profiles": {"vendorid": {"method": "source_statistic", "null_fraction": 1.0}}},
    }
    assert candidates(build_snapshot(tmp_path, monkeypatch, {"alpha": {"tables": tables}})) == []


def test_old_code_negative_the_acceptance_file_fails_there(tmp_path):
    if not OLD_TREE.exists():
        pytest.skip("no old worktree at /tmp/wt-old2 (git worktree add /tmp/wt-old2 fbaaa7d)")  # C5: reason=no old worktree at /tmp/wt-old2 (git worktree add /tmp/wt-old2 fbaaa7d); approved=user, 2026-11-08; review=2027-05-08
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(
        str(OLD_TREE / "packages" / p / "src") for p in ("requester", "catalog-builder", "contracts")
    )}
    done = subprocess.run(
        [sys.executable, "-m", "pytest", str(REPO / "tests/catalogue_fixes/test_d3_lowercase_refs.py"),
         "-q", "-p", "no:cacheprovider"],
        capture_output=True, text=True, env=env, cwd=REPO, timeout=300, check=False,
    )
    assert "test_a_lowercase_name_links_when_its_stem_is_a_known_table" in done.stdout
    assert done.returncode != 0


def test_claim_that_the_snake_form_also_reaches_a_second_unique_column_on_old_code(tmp_path):
    """Judges the lead's edit of `test_the_lowercase_rule_is_not_limited_to_one_table_pair`: the
    claim is that `vendor_id` already reached `vendor.code` before any change (so choosing among
    several unique columns is A2, not D3). Run the snake form on the OLD tree."""
    if not OLD_TREE.exists():
        pytest.skip("no old worktree at /tmp/wt-old2")  # C5: reason=no old worktree at /tmp/wt-old2; approved=user, 2026-11-08; review=2027-05-08
    script = tmp_path / "probe.py"
    script.write_text(
        "import sys, pathlib, tempfile\n"
        f"sys.path.insert(0, {str(REPO / 'tests' / 'catalogue_fixes')!r})\n"
        "import pytest\n"
        "from catalogue_fixture_kit import build_snapshot, candidates\n"
        "class MP:\n"
        "    def setenv(self, k, v):\n"
        "        import os; os.environ[k] = v\n"
        "tmp = pathlib.Path(tempfile.mkdtemp())\n"
        "tables = {'vendor': {'fields': [('vendor_id','integer'),('label','text'),('code','integer')],\n"
        "          'unique': {'vendor_id':1.0,'code':1.0}},\n"
        "          'shipment': {'fields': [('id','integer'),('vendor_id','integer')]}}\n"
        "print(candidates(build_snapshot(tmp, MP(), {'alpha': {'tables': tables}})))\n"
    )
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(
        str(OLD_TREE / "packages" / p / "src") for p in ("requester", "catalog-builder", "contracts")
    )}
    done = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, env=env,
        cwd=tmp_path, timeout=120, check=False,
    )
    assert "('shipment', 'vendor_id', 'vendor', 'code')" in done.stdout, done.stdout + done.stderr
