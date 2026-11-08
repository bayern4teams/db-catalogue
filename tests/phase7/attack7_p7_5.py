"""Attack tests for P7-5 (F11-b A + B + E). Not collected by default: run with the file path.

A test that fails here is a finding; the docstring says what was expected and why.
"""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
import unicodedata
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "catalogue_fixes"))
import catalogue_fixture_kit as kit
import pytest
import test_p7_5_tiers_and_conclusive_zero as base
from catalogue_fixture_kit import build_snapshot, builder_run, candidates
from dbig_builder.config import BuilderConfig, load_config
from dbig_requester.search import bridge_paths, index_snapshot, relationships_for

# the acceptance module's autouse fixture must run here too
_flagged_tokens = base._flagged_tokens
sample, _pair, _decisions, _decide = base.sample, base._pair, base._decisions, base._decide
VERIFIED, PLAUSIBLE, UNVERIFIED = base.VERIFIED, base.PLAUSIBLE, base.UNVERIFIED
CHILD_40, FILLER = base.CHILD_40, base.FILLER
ROOT = Path(__file__).resolve().parents[2]
NUMBER = re.compile(r"(?<![\w.\"'])(\d+\.\d+|\d{2,})(?![\w\"'])")
CANARY = "zebracanaryq7"


def _by_pair(snapshot: Path) -> dict[tuple[str, str], str | None]:
    return base._tiers(snapshot)


# --- tier boundary -------------------------------------------------------------------------------


@pytest.mark.parametrize("complete", [True, False], ids=["complete", "bound"])
def test_exactly_c_min_is_verified_even_when_the_fraction_does_not_terminate(
    tmp_path, monkeypatch, complete
):
    """4 shared of 12 child values is exactly 1/3; with c_min = 1/3 the rule is `>=` (DEC-37/38).
    The stored `contained_fraction` is rounded to 6 places before the comparison, so 0.333333 <
    0.3333333333333333 would wrongly demote it."""
    child = list(range(1000, 1012))
    parent = sample(child[:4] + FILLER, complete=complete)
    snapshot = build_snapshot(
        tmp_path, monkeypatch, _pair(sample(child), parent, cross=True),
        builder_extra={"c_min": 1 / 3})
    assert _by_pair(snapshot) == {("part", "gadget"): VERIFIED}


def test_c_min_one_requires_full_containment(tmp_path, monkeypatch):
    parent = sample(CHILD_40[:39] + FILLER)
    snap = build_snapshot(tmp_path, monkeypatch, _pair(sample(CHILD_40), parent, cross=True),
                          builder_extra={"c_min": 1.0})
    assert _by_pair(snap) == {("part", "gadget"): UNVERIFIED}
    other = base._sub(tmp_path, "f")
    snap2 = build_snapshot(other, monkeypatch,
                           _pair(sample(CHILD_40), sample(CHILD_40 + FILLER), cross=True),
                           builder_extra={"c_min": 1.0})
    assert _by_pair(snap2) == {("part", "gadget"): VERIFIED}


@pytest.mark.parametrize("bad", [0, 0.0, -0.5, 1.0001, float("nan")])
def test_c_min_out_of_range_is_refused(bad):
    with pytest.raises(ValueError, match=r"c_min"):
        BuilderConfig(signing_keys={"t": "K"}, c_min=bad)


@pytest.mark.parametrize("bad", [0, -1, float("nan")])
def test_min_expected_shared_out_of_range_is_refused(bad):
    with pytest.raises(ValueError, match=r"min_expected_shared"):
        BuilderConfig(signing_keys={"t": "K"}, min_expected_shared=bad)


# --- B arithmetic --------------------------------------------------------------------------------


def test_b_just_below_the_threshold_keeps(tmp_path, monkeypatch):
    """39 child values, f = 0.5, c_min = 0.25: E = 4.875 < 5."""
    snap = build_snapshot(tmp_path, monkeypatch, base._zero_world(n=39))
    assert base._kept(snap)


def test_b_float_boundary_is_counted_either_way(tmp_path, monkeypatch):
    """E = 30 * 0.5 * (1/3) is 5 in intent and 4.999999999999999 in floats: either outcome is
    defensible, but a drop must be counted and a keep must not be."""
    snap = build_snapshot(tmp_path, monkeypatch, base._zero_world(n=30, read=100, estimate=200.0),
                          builder_extra={"c_min": 1 / 3})
    dropped = builder_run(snap).get("dropped_expected_overlap_candidates", 0)
    assert bool(candidates(snap)) != bool(dropped)


def test_b_never_applies_to_a_positive_overlap(tmp_path, monkeypatch):
    snap = build_snapshot(tmp_path, monkeypatch,
                          base._zero_world(n=400, read=900, estimate=1000.0, shared=1))
    assert base._kept(snap)


def test_b_huge_estimate_means_f_zero_and_keeps(tmp_path, monkeypatch):
    snap = build_snapshot(tmp_path, monkeypatch, base._zero_world(estimate=1e18))
    assert base._kept(snap)


def test_nan_distinct_estimate_cannot_reach_the_builder():
    """`min(1.0, x / nan)` is 1.0 in Python, which would make any NaN estimate a conclusive zero."""
    from dbig_contracts.models import Profile
    with pytest.raises(ValueError, match=r"distinct_estimate"):
        Profile(id="p", field_id="f", object_id="o", method="source_statistic",
                distinct_estimate=float("nan"), source_id="s", observed_at="2026-01-01T00:00:00Z",
                collector_version="t", provenance="x")


def test_characterise_b_assumes_the_parent_sample_is_random(tmp_path, monkeypatch):
    """A true link whose parent was read as the FIRST rows (not random) with the child's values in
    the unread rows shows a zero; B drops it (E = 5). That is the plan's formula (DEC-23): it holds
    only for a random read. Documented risk, not a defect of the worker."""
    child = list(range(1000, 1040))
    parent_read = sample(range(0, 100), complete=False,
                         method=f"{base.B_TRUSTED_SAMPLE_METHODS[0]}:10")  # DEC-39
    snap = build_snapshot(tmp_path, monkeypatch,
                          _pair(sample(child), parent_read, cross=True, estimate=200.0))
    assert candidates(snap) == []


# --- decisions: matching -------------------------------------------------------------------------


def _two_children_one_key(monkeypatch):
    """`alpha` holds table `a.b` and table `b` in namespace `a`, both with `gadget_id`; the parent
    is in beta. Both qualify to `alpha.a.b.gadget_id`."""
    original = kit.Object

    def with_namespace(**kwargs):
        return original(**({**kwargs, "namespace": "a"} if kwargs.get("name") == "b" else kwargs))

    monkeypatch.setattr(kit, "Object", with_namespace)
    cols = [("id", "integer"), ("gadget_id", "integer")]
    return {
        "alpha": {"tables": {"a.b": {"fields": cols, "key": ["id"]},
                             "b": {"fields": cols, "key": ["id"]}}},
        "beta": {"tables": {"gadget": {"fields": [("id", "integer")], "key": ["id"]}}},
    }


def test_a_dotted_name_cannot_collide_with_a_namespace_split(tmp_path, monkeypatch):
    """Two different columns share one qualified name when a dot is part of a table name. A
    `confirmed` for one must not verify the other."""
    sources = _two_children_one_key(monkeypatch)
    path = _decisions(tmp_path, [_decide("alpha.a.b.gadget_id", "beta.gadget.id", "confirmed")])
    snap = build_snapshot(tmp_path, monkeypatch, sources, builder_extra={"decisions_file": path})
    tiers = _by_pair(snap)
    assert sorted(tiers) == [("a.b", "gadget"), ("b", "gadget")], tiers
    assert list(tiers.values()).count(VERIFIED) <= 1, f"one decision verified both: {tiers}"


def test_a_dotted_table_name_is_matched_by_its_full_name(tmp_path, monkeypatch):
    sources = {
        "alpha": {"tables": {"part": {"fields": [("id", "integer"), ("gadget_id", "integer")],
                                      "key": ["id"]}}},
        "beta": {"tables": {"gadget.v2": {"fields": [("id", "integer")], "key": ["id"]}}},
    }
    path = _decisions(tmp_path, [_decide("alpha.part.gadget_id", "beta.gadget.v2.id", "confirmed")])
    snap = build_snapshot(tmp_path, monkeypatch, sources, builder_extra={"decisions_file": path})
    assert _by_pair(snap) == {("part", "gadget.v2"): VERIFIED}


def test_unicode_name_matches(tmp_path, monkeypatch):
    sources = {
        "alpha": {"tables": {"part": {"fields": [("id", "integer"), ("gädget_id", "integer")],
                                      "key": ["id"]}}},
        "beta": {"tables": {"gädget": {"fields": [("id", "integer")], "key": ["id"]}}},
    }
    nfc = unicodedata.normalize("NFC", "gädget")
    path = _decisions(tmp_path, [_decide("alpha.part.gädget_id", f"beta.{nfc}.id", "rejected")])
    snap = build_snapshot(tmp_path, monkeypatch, sources, builder_extra={"decisions_file": path})
    assert base._edge(snap, "part", "gädget").status == "rejected"


@pytest.mark.parametrize("order", [["confirmed", "rejected"], ["rejected", "confirmed"]])
def test_conflicting_decisions_last_wins_and_nothing_is_unmatched(tmp_path, monkeypatch, order):
    path = _decisions(tmp_path, [_decide(*base.PAIR, d) for d in order])
    snap = build_snapshot(tmp_path, monkeypatch, base._pair(None, None, cross=True),
                          builder_extra={"decisions_file": path})
    edge = base._edge(snap, "part", "gadget")
    if order[-1] == "confirmed":
        assert edge.evidence_tier == VERIFIED and edge.status == "tentative"
    else:
        assert edge.status == "rejected"
    assert base._decision_count(snap) == 0


def test_a_confirmed_decision_does_not_bring_back_a_candidate_dropped_by_b(tmp_path, monkeypatch):
    """Characterisation: B drops before any decision is looked at; the confirm is only counted."""
    path = _decisions(tmp_path, [_decide(*base.PAIR, "confirmed")])
    snap = build_snapshot(tmp_path, monkeypatch, base._zero_world(),
                          builder_extra={"decisions_file": path})
    assert candidates(snap) == []
    assert base._decision_count(snap) == 1


def test_a_confirmed_decision_on_a_complete_parent_zero_is_counted_not_applied(
    tmp_path, monkeypatch
):
    path = _decisions(tmp_path, [_decide(*base.PAIR, "confirmed")])
    snap = build_snapshot(tmp_path, monkeypatch, base._zero_world(complete=True, estimate=None),
                          builder_extra={"decisions_file": path})
    assert candidates(snap) == [] and base._decision_count(snap) == 1


def test_a_decision_on_a_candidate_cut_by_top_k_is_counted(tmp_path, monkeypatch):
    sources = {
        "alpha": {"tables": {
            "part": {"fields": [("id", "integer"), ("gadget_id", "integer")], "key": ["id"]},
            "gadget": {"fields": [("id", "integer")], "key": ["id"]}}},
        "beta": {"tables": {"gadget2": {"fields": [("id", "integer")], "key": ["id"]}}},
    }
    both = [_decide("alpha.part.gadget_id", "alpha.gadget.id", "rejected"),
            _decide("alpha.part.gadget_id", "beta.gadget2.id", "rejected")]
    snap = build_snapshot(tmp_path, monkeypatch, sources, builder_extra={
        "decisions_file": _decisions(tmp_path, both), "top_k_per_field": 1})
    if len(candidates(snap)) != 1:
        pytest.skip(  # C5: reason=see message; approved=user, 2026-11-08; review=2027-05-08
            "fixture admits one parent only; nothing to cut"
        )
    assert base._decision_count(snap) == 1


def test_a_composite_candidate_takes_no_decision_and_stays_hidden(tmp_path, monkeypatch):
    """Characterisation: composite edges are never matched by a decision, so a true composite
    cross-source link can only come back through the flag."""
    sources = {
        "alpha": {"tables": {"orders": {"fields": [("number", "integer"), ("region", "text")],
                                        "key": ["number", "region"]}}},
        "beta": {"tables": {"shipment": {"fields": [("sid", "integer"),
                                                    ("order_number", "integer"),
                                                    ("region", "text")], "key": ["sid"]}}},
    }
    snap = build_snapshot(tmp_path, monkeypatch, sources)
    assert candidates(snap)
    assert set(_by_pair(snap).values()) == {UNVERIFIED}


# --- decisions file: robustness and privacy ------------------------------------------------------


def _toml(tmp_path: Path, decisions: Path) -> Path:
    toml = tmp_path / "builder.toml"
    toml.write_text(
        f'[builder]\nsigning_keys = {{ test = "BUNDLE_KEY" }}\ndecisions_file = "{decisions}"\n',
        encoding="utf-8")
    return toml


@pytest.mark.parametrize(
    "content",
    [
        json.dumps([{"child": f"x.{CANARY}.c", "parent": "d.e.f", "decision": "maybe"}]),
        json.dumps([{"child": f"x.{CANARY}.c", "parent": "d.e.f", "decision": "confirmed",
                     "note": CANARY}]),
        json.dumps({CANARY: 1}),
        f"{{{CANARY}",
        json.dumps([CANARY]),
        json.dumps([{"child": 1, "parent": CANARY, "decision": "confirmed"}]),
    ],
    ids=["bad_decision", "extra_key", "dict", "bad_json", "string_item", "int_child"],
)
def test_no_decision_name_in_a_config_error(tmp_path, content):
    path = tmp_path / "d.json"
    path.write_text(content, encoding="utf-8")
    for build in (lambda: BuilderConfig(signing_keys={"test": "K"}, decisions_file=path),
                  lambda: load_config(_toml(tmp_path, path))):
        with pytest.raises(ValueError, match=r"decisions_file must be a JSON list") as caught:
            build()
        assert CANARY not in f"{caught.value!s}{caught.value!r}"


def test_a_directory_a_missing_file_and_binary_are_refused_cleanly(tmp_path):
    binary = tmp_path / "b.json"
    binary.write_bytes(b"\xff\xfe\x00\x00" * 10)
    for target in (tmp_path, tmp_path / "missing.json", binary):
        with pytest.raises(ValueError, match=r"decisions_file must be a JSON list"):
            BuilderConfig(signing_keys={"test": "K"}, decisions_file=target)


def test_an_empty_list_and_a_bom_file(tmp_path):
    empty = tmp_path / "e.json"
    empty.write_text("[]", encoding="utf-8")
    assert BuilderConfig(signing_keys={"test": "K"}, decisions_file=empty).decisions == []
    bom = tmp_path / "bom.json"
    bom.write_bytes(b"\xef\xbb\xbf[]")  # some Windows editors write this
    assert BuilderConfig(signing_keys={"test": "K"}, decisions_file=bom).decisions == []


def test_an_unbounded_device_file_is_refused_not_read_to_memory_exhaustion():
    """The config reads the whole file with no size limit. `/dev/zero` stands for a huge file; a
    bounded read must end in a clean ValueError (the planner bounds its inputs the same way)."""
    code = (
        "import resource\n"
        "resource.setrlimit(resource.RLIMIT_AS, (1_000_000_000, 1_000_000_000))\n"
        "from dbig_builder.config import BuilderConfig\n"
        "try:\n"
        "    BuilderConfig(signing_keys={'t':'K'}, decisions_file='/dev/zero')\n"
        "except ValueError:\n"
        "    print('REFUSED')\n"
        "except MemoryError:\n"
        "    print('MEMORYERROR')\n"
    )
    out = subprocess.run(  # noqa: S603 # C5: reason=fixed argv, no shell; approved=user, 2026-11-08; review=2027-05-08
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=False
    )
    assert "REFUSED" in out.stdout, (out.stdout, out.stderr[-300:])


def test_no_decision_name_in_logs_or_the_run_record(tmp_path, monkeypatch, caplog):
    entries = [_decide(f"alpha.{CANARY}.c", "beta.q.id", "confirmed"),
               _decide(*base.PAIR, "confirmed")]
    caplog.set_level("DEBUG")
    snap = build_snapshot(tmp_path, monkeypatch, base._pair(None, None, cross=True),
                          builder_extra={"decisions_file": _decisions(tmp_path, entries)})
    seen = caplog.text + json.dumps(builder_run(snap), default=str)
    assert CANARY not in seen and "gadget_id" not in caplog.text


# --- requester -----------------------------------------------------------------------------------


@pytest.mark.parametrize("names", [("gadget", "widget", "sprocket"), ("zeta", "alpha1", "mu"),
                                    ("aaa", "bbb", "ccc"), ("ccc", "bbb", "aaa")])
def test_a_filtered_line_never_takes_a_limit_slot(tmp_path, monkeypatch, names):
    """Several candidates touch the same objects (one rejected by decision, others cross-source and
    unmeasured); with the flag off every limit from 1 up must return visible lines only and as
    many as exist."""
    a, b, c = names
    ref = [("id", "integer"), (f"{a}_id", "integer"), (f"{b}_id", "integer")]
    sources = {
        "alpha": {"tables": {
            a: {"fields": [("id", "integer")], "key": ["id"]},
            b: {"fields": [("id", "integer")], "key": ["id"]},
            "hub": {"fields": ref, "key": ["id"]},
        }},
        "beta": {"tables": {c: {"fields": ref, "key": ["id"]}}},
    }
    path = _decisions(tmp_path, [_decide(f"alpha.hub.{b}_id", f"alpha.{b}.id", "rejected")])
    snap = build_snapshot(tmp_path, monkeypatch, sources, builder_extra={"decisions_file": path})
    index_snapshot(snap, tmp_path / "state")
    _, data = kit.read_artifact(snap)
    ids = {n.id for n in data["nodes.jsonl"] if n.record_type == "object"}
    full = relationships_for(tmp_path / "state", ids, 100, True)
    shown = relationships_for(tmp_path / "state", ids, 100, False)
    assert len(full) > len(shown) >= 1, "fixture fault: nothing is filtered"
    assert all(r["status"] != "rejected" and r["evidence_tier"] != UNVERIFIED for r in shown)
    for limit in range(1, len(shown) + 2):
        got = relationships_for(tmp_path / "state", ids, limit, False)
        assert len(got) == min(limit, len(shown)), (limit, names)


def test_an_old_index_body_without_evidence_tier_is_shown_and_does_not_crash(
    tmp_path, monkeypatch
):
    """A body that predates the key (hand-stripped here) reads as 'no tier': shown by default."""
    snap = build_snapshot(tmp_path, monkeypatch, base._direct_sources())
    index_snapshot(snap, tmp_path / "state")
    with closing(sqlite3.connect(tmp_path / "state" / "catalog.sqlite3")) as connection:
        connection.execute("UPDATE relationships SET body = json_remove(body, '$.evidence_tier')")
        connection.commit()
        ids = {row[0] for row in connection.execute("SELECT from_id FROM relationships")}
        ids |= {row[0] for row in connection.execute("SELECT to_id FROM relationships")}
    lines = relationships_for(tmp_path / "state", ids, 10, False)
    assert len(lines) == 1 and "evidence_tier" not in lines[0]


def test_a_malformed_body_does_not_break_the_filter(tmp_path, monkeypatch):
    snap = build_snapshot(tmp_path, monkeypatch, base._direct_sources())
    index_snapshot(snap, tmp_path / "state")
    with closing(sqlite3.connect(tmp_path / "state" / "catalog.sqlite3")) as connection:
        connection.execute("UPDATE relationships SET body = '{\"status\": null}'")
        connection.commit()
        ids = {r[0] for r in connection.execute("SELECT from_id FROM relationships")} | {
            r[0] for r in connection.execute("SELECT to_id FROM relationships")}
    assert len(relationships_for(tmp_path / "state", ids, 5, False)) == 1
    assert bridge_paths(tmp_path / "state", ids, 4, 4)[0] == []


# --- plan 2.4 and package isolation ---------------------------------------------------------------


def test_no_new_numeric_literal_in_the_added_source_lines():
    """The only decimals / multi-digit numbers on added lines under packages/*/src are the config
    default 0.25 (on a `c_min` line) and the f = 1 cap."""
    out = subprocess.run(
        ["git", "diff", "-U0", "--", "packages/*/src"],  # noqa: S607 # C5: reason=fixed argv, no shell; approved=user, 2026-11-08; review=2027-05-08
        cwd=ROOT, capture_output=True, text=True, check=False).stdout
    found = []
    for line in out.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            code = line[1:].split("#")[0]
            found += [(n, line) for n in re.findall(NUMBER, code)]
    bad = [
        (n, line) for n, line in found if n != "1.0" and not (n == "0.25" and "c_min" in line)
    ]
    assert not bad, bad


def test_package_imports_stay_isolated():
    for package, forbidden in (("requester", ("dbig_builder", "dbig_planner")),
                               ("planner", ("dbig_builder", "dbig_requester")),
                               ("catalog-builder", ("dbig_requester", "dbig_planner"))):
        for source in (ROOT / "packages" / package / "src").rglob("*.py"):
            text = source.read_text(encoding="utf-8")
            for name in forbidden:
                assert f"import {name}" not in text and f"from {name}" not in text, (source, name)
