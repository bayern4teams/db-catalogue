"""P7-5 (F11-b): evidence tiers (A), a conclusive zero by expected overlap (B), source pairs (E).

Derived from `docs/plans/PHASE7_LOCAL_PLAN.md` section 4 "P7-5" and section 2, the F11-b paragraph
of
`artifacts/run-20261020-pgch/research-f14-f17.md`, and owner decisions DEC-23, DEC-34..38 in
`docs/plans/phase7/HANDOFF.md`, not from the implementation. Invented generic names only (plan 2.4).

Spec as checked here.

A. Every REFERENCE_CANDIDATE `Edge` carries an optional additive `evidence_tier`
   ("verified" | "plausible" | "unverified_cross_source"; None for other kinds).
   * verified: measured containment >= `c_min` on a complete parent (DEC-37), or a lower bound
   (parent
     partly read) whose observed containment is already >= `c_min` (DEC-38). `c_min` is a builder
     config key, default 0.25. Containment is the contained fraction of the child's distinct values.
   * otherwise plausible when both ends are in one source, unverified_cross_source when not.
   * the catalogue page shows every candidate with its tier.
   * the requester keeps unverified_cross_source edges out of the model context (`relationships`)
   and
     out of the P7-3 path search unless `[requester.answer] include_unverified_cross_source = true`.
   * the planner keeps them out unless `[planner] include_unverified_cross_source = true`.
   * `[builder] decisions_file`: JSON list of {"child","parent","decision"}, names
     `<source_id>.<namespace>.<table>.<column>` (namespace omitted when the object has none).
     `confirmed` -> verified; `rejected` -> status rejected, out of the model context and the
     planner;
     an entry matching no proposed candidate is a warning counted in diagnostics (count only).
B. A zero against a partly read parent is a measurement when E = n_child * f_parent * c_min >=
   `min_expected_shared` (default 5): the candidate drops, counted under its own diagnostics key.
   n_child = child distinct values compared (in the common range when capped); f_parent = distinct
   values read / the parent profile's `distinct_estimate`, capped at 1. No estimate: B does not
   apply.
E. No new key (DEC-34): `forbidden_source_pairs` and `source_groups` already block pairs.

Tests that pass on the code before the item are named `control_*` or say so; they guard against an
over-fix. Nothing here prints a value.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
import sys
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "catalogue_fixes"))

import catalogue_fixture_kit as kit  # noqa: E402
import pytest  # noqa: E402
from catalogue_fixture_kit import (  # noqa: E402
    MODEL_KEY,
    TOKEN_MATERIAL,
    ModelStub,
    build_snapshot,
    builder_run,
    candidates,
)
from dbig_builder.config import B_TRUSTED_SAMPLE_METHODS, BuilderConfig, load_config  # noqa: E402
from dbig_builder.render import render_catalog  # noqa: E402
from dbig_contracts.artifact import read_artifact, write_artifact  # noqa: E402
from dbig_contracts.ids import stable_id  # noqa: E402
from dbig_contracts.models import Edge, Evidence, Object, Source, ValueTokens  # noqa: E402
from dbig_planner.config import PlannerConfig  # noqa: E402
from dbig_planner.config import load_config as load_planner_config  # noqa: E402
from dbig_planner.graph import build_graph  # noqa: E402
from dbig_planner.paths import PathRequest, find_paths  # noqa: E402
from dbig_requester.answer import answer_question  # noqa: E402
from dbig_requester.config import AnswerConfig  # noqa: E402
from dbig_requester.config import load_config as load_requester_config  # noqa: E402
from dbig_requester.search import index_snapshot  # noqa: E402
from jsonschema import Draft202012Validator  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
VERIFIED, PLAUSIBLE, UNVERIFIED = "verified", "plausible", "unverified_cross_source"
FLAG = "include_unverified_cross_source"
OLD_DROP_KEYS = {"dropped_zero_overlap_candidates", "dropped_ordinary_name_candidates"}


# --- token sets with an explicit `complete` and cap ----------------------------------------------


def _h(value) -> str:
    return hmac.new(TOKEN_MATERIAL.encode(), str(value).encode(), hashlib.sha256).hexdigest()


class Sample(list):
    complete: bool = True
    cap: int | None = None
    method: str = "first_rows_limit:10"


def sample(
    values, *, complete: bool = True, cap: int | None = None, method: str = "first_rows_limit:10"
) -> Sample:
    made = Sample(values)
    made.complete = complete
    made.cap = cap
    made.method = method
    return made


@pytest.fixture(autouse=True)
def _flagged_tokens(monkeypatch):
    original = kit.token_set

    def token_set(field_id, object_id, values, **kwargs):
        if not isinstance(values, Sample):
            return original(field_id, object_id, values, **kwargs)
        digests = sorted({_h(v) for v in values})
        kept = digests if values.cap is None else digests[: values.cap]
        return ValueTokens(
            id=stable_id(kwargs["source_id"], "tokens", field_id),
            field_id=field_id,
            object_id=object_id,
            key_id=kwargs.get("key_id", "run-1"),
            sample_count=len(values),
            sample_method=values.method,
            observed_count=len(values),
            distinct_count=len(digests),
            truncated=len(kept) < len(digests),
            complete=values.complete,
            tokens=kept,
            source_id=kwargs["source_id"],
            observed_at=datetime.now(UTC),
            collector_version="test",
            provenance="synthetic",
        )

    monkeypatch.setattr(kit, "token_set", token_set)


# --- readers --------------------------------------------------------------------------------------


def _tiers(snapshot: Path) -> dict[tuple[str, str], str | None]:
    """{(child object, parent object): evidence_tier} for REFERENCE_CANDIDATE edges."""
    _, data = read_artifact(snapshot)
    names = {n.id: n.name for n in data["nodes.jsonl"]}
    return {
        (names[e.from_id], names[e.to_id]): e.model_dump().get("evidence_tier")
        for e in data["edges.jsonl"]
        if e.kind == "REFERENCE_CANDIDATE"
    }


def _edge(snapshot: Path, child: str, parent: str):
    _, data = read_artifact(snapshot)
    names = {n.id: n.name for n in data["nodes.jsonl"]}
    [found] = [
        e
        for e in data["edges.jsonl"]
        if e.kind == "REFERENCE_CANDIDATE" and (names[e.from_id], names[e.to_id]) == (child, parent)
    ]
    return found


def _new_drop_count(snapshot: Path) -> int:
    """The sum of every `dropped_*` diagnostics count other than the two that existed before B."""
    run = builder_run(snapshot)
    return sum(v for k, v in run.items() if k.startswith("dropped_") and k not in OLD_DROP_KEYS)


def _decision_count(snapshot: Path) -> int:
    """The count of unmatched decisions: any integer key naming `decision` or `unmatched`."""
    run = builder_run(snapshot)
    found = {
        k: v for k, v in run.items() if isinstance(v, int) and ("decision" in k or "unmatched" in k)
    }
    assert len(found) <= 1, f"more than one decisions count on the builder run: {sorted(found)}"
    return next(iter(found.values()), 0)


# --- 1. tiers in the builder ----------------------------------------------------------------------

CHILD_40 = list(range(1000, 1040))  # 40 distinct child values
FILLER = list(range(50_000, 50_100))  # parent values that are not the child's


def _pair(child, parent, *, cross, estimate=None, uniqueness_only=False):
    """part.gadget_id -> gadget.id; `gadget` lives in `beta` when `cross`."""
    parent_table = {"fields": [("id", "integer"), ("title", "text")], "key": ["id"]}
    if parent is not None:
        parent_table["values"] = {"id": parent}
    if estimate is not None:
        parent_table["profiles"] = {
            "id": {"method": "source_statistic", "distinct_estimate": estimate}
        }
    if uniqueness_only:
        parent_table["profiles"] = {"id": {"method": "source_statistic", "uniqueness": 1.0}}
    part = {"fields": [("id", "integer"), ("gadget_id", "integer")], "key": ["id"]}
    if child is not None:
        part["values"] = {"gadget_id": child}
    if cross:
        return {"alpha": {"tables": {"part": part}}, "beta": {"tables": {"gadget": parent_table}}}
    return {"alpha": {"tables": {"gadget": parent_table, "part": part}}}


def _build(tmp_path, monkeypatch, sources, **extra):
    return build_snapshot(tmp_path, monkeypatch, sources, builder_extra=extra or None)


def _sub(tmp_path: Path, label: str) -> Path:
    """A fresh directory, so one test can build several snapshots under the default names."""
    path = tmp_path / label
    path.mkdir()
    return path


@pytest.mark.parametrize("cross", [False, True], ids=["same_source", "cross_source"])
@pytest.mark.parametrize(
    ("shared", "complete", "verified"),
    [
        (12, True, True),  # 0.30 measured on a complete parent
        (10, True, True),  # exactly c_min: >=
        (9, True, False),  # 0.225 measured: below c_min (DEC-37)
        (12, False, True),  # lower bound 0.30 already >= c_min (DEC-38)
        (10, False, True),  # lower bound exactly c_min
        (9, False, False),  # lower bound 0.225 < c_min: not verified
    ],
    ids=["complete_030", "complete_025", "complete_0225", "bound_030", "bound_025", "bound_0225"],
)
def test_candidate_tier_follows_measured_containment_against_c_min(
    tmp_path, monkeypatch, cross, shared, complete, verified
):
    parent = sample(CHILD_40[:shared] + FILLER, complete=complete)
    snapshot = _build(tmp_path, monkeypatch, _pair(sample(CHILD_40), parent, cross=cross))
    want = VERIFIED if verified else (UNVERIFIED if cross else PLAUSIBLE)
    assert _tiers(snapshot) == {("part", "gadget"): want}


@pytest.mark.parametrize("cross", [False, True], ids=["same_source", "cross_source"])
def test_an_unmeasured_candidate_is_plausible_or_unverified_cross_source(
    tmp_path, monkeypatch, cross
):
    """No tokens at all, and a sample too small to compare, are both 'no positive measurement'."""
    want = UNVERIFIED if cross else PLAUSIBLE
    no_values = build_snapshot(tmp_path, monkeypatch, _pair(None, None, cross=cross))
    assert _tiers(no_values) == {("part", "gadget"): want}
    tiny = build_snapshot(
        tmp_path, monkeypatch, _pair(sample([1, 2]), sample([1, 2, 3]), cross=cross), name="tiny"
    )
    assert _tiers(tiny) == {("part", "gadget"): want}


@pytest.mark.parametrize("cross", [False, True], ids=["same_source", "cross_source"])
def test_a_tiny_positive_overlap_on_a_complete_parent_is_not_verified(tmp_path, monkeypatch, cross):
    """DEC-37 replay: one shared value in 160 (~0.6%) is a positive measurement but far below
    c_min."""
    child = list(range(2000, 2160))
    parent = sample([child[0], *range(80_000, 80_300)])
    snapshot = _build(tmp_path, monkeypatch, _pair(sample(child), parent, cross=cross))
    assert _tiers(snapshot) == {("part", "gadget"): UNVERIFIED if cross else PLAUSIBLE}


def test_a_partly_read_parent_with_a_zero_and_no_estimate_stays_a_candidate_without_a_verdict(
    tmp_path, monkeypatch
):
    """The zero says nothing (B does not apply): kept, and not verified."""
    parent = sample(range(90_000, 90_300), complete=False)
    snapshot = _build(tmp_path, monkeypatch, _pair(sample(CHILD_40), parent, cross=True))
    assert _tiers(snapshot) == {("part", "gadget"): UNVERIFIED}


def test_c_min_comes_from_the_config(tmp_path, monkeypatch):
    assert "c_min" in BuilderConfig.model_fields, "c_min is not a builder config key yet"
    assert BuilderConfig.model_fields["c_min"].default == 0.25
    parent = lambda n: sample(CHILD_40[:n] + FILLER)  # noqa: E731
    # 9 of 40 (0.225): verified once c_min is lowered
    low = _build(tmp_path, monkeypatch, _pair(sample(CHILD_40), parent(9), cross=True), c_min=0.1)
    assert _tiers(low) == {("part", "gadget"): VERIFIED}
    # 12 of 40 (0.30): no longer verified once c_min is raised
    high = build_snapshot(
        tmp_path,
        monkeypatch,
        _pair(sample(CHILD_40), parent(12), cross=True),
        name="high",
        builder_extra={"c_min": 0.5},
    )
    assert _tiers(high) == {("part", "gadget"): UNVERIFIED}
    # 22 of 40 (0.55): verified at 0.5, a boundary away from the default
    mid = build_snapshot(
        tmp_path,
        monkeypatch,
        _pair(sample(CHILD_40), parent(22), cross=True),
        name="mid",
        builder_extra={"c_min": 0.5},
    )
    assert _tiers(mid) == {("part", "gadget"): VERIFIED}


def test_builder_config_defaults_and_validation():
    fields = BuilderConfig.model_fields
    assert "c_min" in fields and "min_expected_shared" in fields
    assert fields["c_min"].default == 0.25
    assert fields["min_expected_shared"].default == 5
    base = {"signing_keys": {"test": "BUNDLE_KEY"}}
    for bad in ({"c_min": -0.1}, {"c_min": 1.5}, {"min_expected_shared": -1}):
        with pytest.raises(ValueError):
            BuilderConfig(**base, **bad)


def test_declared_keys_and_other_edges_keep_a_non_candidate_tier(tmp_path, monkeypatch):
    """A declared foreign key is verified by definition (or carries no tier); nothing else has
    one."""
    sources = {
        "alpha": {
            "tables": {
                "gizmo": {"fields": [("gid", "integer")], "key": ["gid"]},
                "linkrow": {
                    "fields": [("lid", "integer"), ("lref", "integer")],
                    "key": ["lid"],
                    "foreign": [("lref", "gizmo", "gid")],
                },
            }
        }
    }
    snapshot = build_snapshot(tmp_path, monkeypatch, sources)
    _, data = read_artifact(snapshot)
    kinds = {e.kind for e in data["edges.jsonl"]}
    assert "DECLARES_FK" in kinds
    for edge in data["edges.jsonl"]:
        tier = edge.model_dump().get("evidence_tier")
        if edge.kind == "DECLARES_FK":
            assert tier in (None, VERIFIED), tier
        elif edge.kind != "REFERENCE_CANDIDATE":
            assert tier is None, (edge.kind, tier)
    assert "evidence_tier" in Edge.model_fields, "the contracts Edge has no evidence_tier field"


def test_a_composite_key_candidate_has_a_tier(tmp_path, monkeypatch):
    """No tuple measurement exists for it, so it is plausible (same source)."""
    sources = {
        "alpha": {
            "tables": {
                "orders": {
                    "fields": [("number", "integer"), ("region", "text")],
                    "key": ["number", "region"],
                },
                "shipment": {
                    "fields": [("sid", "integer"), ("order_number", "integer"), ("region", "text")],
                    "key": ["sid"],
                },
            }
        }
    }
    snapshot = build_snapshot(tmp_path, monkeypatch, sources)
    assert candidates(snapshot), "fixture fault: no composite candidate was proposed"
    assert set(_tiers(snapshot).values()) == {PLAUSIBLE}


# --- 2. the contracts and the published schema ----------------------------------------------------


def test_edge_has_an_optional_evidence_tier_with_three_values():
    field = Edge.model_fields["evidence_tier"]
    assert field.is_required() is False and field.default is None
    base = {
        "id": "e",
        "source_id": "s",
        "kind": "REFERENCE_CANDIDATE",
        "from_id": "a",
        "to_id": "b",
        "status": "tentative",
        "evidence_ids": ["ev"],
        "observed_at": datetime.now(UTC),
        "collector_version": "t",
        "provenance": "p",
    }
    for tier in (VERIFIED, PLAUSIBLE, UNVERIFIED, None):
        assert Edge.model_validate({**base, "evidence_tier": tier}).evidence_tier == tier
    with pytest.raises(ValueError):
        Edge.model_validate({**base, "evidence_tier": "somewhat_sure"})
    assert Edge.model_validate(base).evidence_tier is None  # additive: an old row still reads


def test_the_published_edge_schema_is_regenerated():
    path = ROOT / "schemas" / "ir-v1" / "edge.schema.json"
    committed = json.loads(path.read_text(encoding="utf-8"))
    assert "evidence_tier" in committed["properties"], "schemas/ir-v1/edge.schema.json not exported"
    assert "evidence_tier" not in committed.get("required", [])
    text = json.dumps(committed["properties"]["evidence_tier"])
    assert all(tier in text for tier in (VERIFIED, PLAUSIBLE, UNVERIFIED))
    assert committed == Edge.model_json_schema(), "the committed schema differs from the model"


def test_the_schema_rejects_an_unknown_tier_and_accepts_the_builder_rows(tmp_path, monkeypatch):
    schema = json.loads((ROOT / "schemas/ir-v1/edge.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    snapshot = _build(tmp_path, monkeypatch, _pair(None, None, cross=True))
    rows = [
        json.loads(line)
        for line in (snapshot / "edges.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert rows
    for row in rows:
        validator.validate(row)
    bogus = {**rows[0], "evidence_tier": "somewhat_sure"}
    assert not validator.is_valid(bogus), "the schema must restrict evidence_tier to three values"


# --- 3. the catalogue page shows every candidate with its tier ------------------------------------

G10 = list(range(300, 310))


def _page_sources():
    return {
        "alpha": {
            "tables": {
                "gizmo": {"fields": [("gid", "integer")], "key": ["gid"], "values": {"gid": G10}},
                "sprocket": {"fields": [("sid", "integer")], "key": ["sid"]},
                "plain": {"fields": [("pid", "integer"), ("gid", "integer")], "key": ["pid"]},
            }
        },
        "beta": {
            "tables": {
                "shipment": {
                    "fields": [("hid", "integer"), ("gid", "integer"), ("sid", "integer")],
                    "key": ["hid"],
                    "values": {"gid": G10},
                },
            }
        },
    }


def test_the_three_tiers_in_one_snapshot(tmp_path, monkeypatch):
    snapshot = build_snapshot(tmp_path, monkeypatch, _page_sources())
    assert _tiers(snapshot) == {
        ("plain", "gizmo"): PLAUSIBLE,
        ("shipment", "gizmo"): VERIFIED,
        ("shipment", "sprocket"): UNVERIFIED,
    }


def test_the_catalogue_page_lists_every_candidate_with_its_tier(tmp_path, monkeypatch):
    snapshot = build_snapshot(tmp_path, monkeypatch, _page_sources())
    out = tmp_path / "page.html"
    counts = render_catalog(snapshot, [], out, None)
    html = out.read_text(encoding="utf-8")
    assert counts["candidates"] == 3, "the page must still list the unverified candidate"
    assert UNVERIFIED in html and PLAUSIBLE in html
    assert re.search(r"(?<![A-Za-z_])verified(?![A-Za-z_])", html), "the verified tier is not shown"


# --- 4. the owner decisions file ------------------------------------------------------------------


def _decisions(tmp_path, entries, name="decisions.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


def _decide(child, parent, decision):
    return {"child": child, "parent": parent, "decision": decision}


PAIR = ("alpha.part.gadget_id", "beta.gadget.id")


def test_a_confirmed_decision_makes_the_candidate_verified(tmp_path, monkeypatch):
    sources = _pair(sample(CHILD_40), sample(CHILD_40[:5] + FILLER), cross=True)  # 5/40: low
    assert "decisions_file" in BuilderConfig.model_fields
    plain = build_snapshot(tmp_path, monkeypatch, sources, name="plain")
    assert _tiers(plain) == {("part", "gadget"): UNVERIFIED}
    path = _decisions(tmp_path, [_decide(*PAIR, "confirmed")])
    decided = build_snapshot(
        tmp_path, monkeypatch, sources, name="decided", builder_extra={"decisions_file": path}
    )
    assert _tiers(decided) == {("part", "gadget"): VERIFIED}
    assert _decision_count(decided) == 0


def test_a_rejected_decision_sets_the_status_and_keeps_the_edge_on_the_page(tmp_path, monkeypatch):
    sources = _pair(None, None, cross=True)
    path = _decisions(tmp_path, [_decide(*PAIR, "rejected")])
    snapshot = build_snapshot(
        tmp_path, monkeypatch, sources, builder_extra={"decisions_file": path}
    )
    assert _edge(snapshot, "part", "gadget").status == "rejected"
    counts = render_catalog(snapshot, [], tmp_path / "p.html", None)
    assert counts["candidates"] == 1


def test_a_swapped_or_unknown_entry_is_a_counted_warning_and_changes_nothing(
    tmp_path, monkeypatch, caplog, capsys
):
    sources = _pair(None, None, cross=True)
    canary = "zebracanary"
    entries = [
        _decide(
            PAIR[1], PAIR[0], "confirmed"
        ),  # child and parent swapped: not a proposed candidate
        _decide(f"alpha.{canary}.col", f"beta.{canary}.col", "rejected"),
        _decide(*PAIR, "confirmed"),
    ]
    caplog.set_level("DEBUG")
    snapshot = build_snapshot(
        tmp_path,
        monkeypatch,
        sources,
        builder_extra={"decisions_file": _decisions(tmp_path, entries)},
    )
    assert _decision_count(snapshot) == 2, builder_run(snapshot)
    assert _tiers(snapshot) == {("part", "gadget"): VERIFIED}  # the matching entry still applies
    assert candidates(snapshot) == [("part", "gadget_id", "gadget", "id")]  # no edge invented
    # a count only: no name from the file in any log, in the run record or in the snapshot
    seen = (
        caplog.text + "".join(capsys.readouterr()) + json.dumps(builder_run(snapshot), default=str)
    )
    seen += "".join(
        p.read_text(encoding="utf-8", errors="ignore") for p in snapshot.iterdir() if p.is_file()
    )
    assert canary not in seen


def test_the_namespace_segment_is_used_when_the_object_has_one(tmp_path, monkeypatch):
    original = kit.Object

    def with_namespace(**kwargs):
        return original(namespace="public", **kwargs)

    monkeypatch.setattr(kit, "Object", with_namespace)
    path = _decisions(
        tmp_path, [_decide("alpha.public.part.gadget_id", "beta.public.gadget.id", "confirmed")]
    )
    snapshot = build_snapshot(
        tmp_path, monkeypatch, _pair(None, None, cross=True), builder_extra={"decisions_file": path}
    )
    assert _tiers(snapshot) == {("part", "gadget"): VERIFIED}
    assert _decision_count(snapshot) == 0


@pytest.mark.parametrize(
    "content",
    [
        "{not json",  # bad JSON
        json.dumps({"child": "a.b.c", "parent": "d.e.f", "decision": "confirmed"}),  # not a list
        json.dumps([_decide("a.b.c", "d.e.f", "maybe")]),  # bad decision value
        json.dumps([{"child": "a.b.c", "decision": "confirmed"}]),  # parent missing
        json.dumps([{"child": "a.b.c", "parent": "d.e.f"}]),  # decision missing
    ],
    ids=["bad_json", "not_a_list", "bad_decision", "no_parent", "no_decision"],
)
def test_a_bad_decisions_file_is_refused_by_the_config(tmp_path, content):
    assert "decisions_file" in BuilderConfig.model_fields
    path = tmp_path / "bad.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        BuilderConfig(signing_keys={"test": "BUNDLE_KEY"}, decisions_file=path)


def test_a_good_decisions_file_and_the_toml_key(tmp_path):
    good = _decisions(tmp_path, [_decide(*PAIR, "confirmed"), _decide(*PAIR[::-1], "rejected")])
    assert BuilderConfig(signing_keys={"test": "BUNDLE_KEY"}, decisions_file=good)
    toml = tmp_path / "builder.toml"
    toml.write_text(
        f'[builder]\nsigning_keys = {{ test = "BUNDLE_KEY" }}\ndecisions_file = "{good}"\n'
        "c_min = 0.3\nmin_expected_shared = 7\n",
        encoding="utf-8",
    )
    config = load_config(toml)
    assert (config.c_min, config.min_expected_shared) == (0.3, 7)
    bad = tmp_path / "bad.json"
    bad.write_text("[", encoding="utf-8")
    toml.write_text(
        f'[builder]\nsigning_keys = {{ test = "BUNDLE_KEY" }}\ndecisions_file = "{bad}"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_config(toml)


# --- 5. B: a conclusive zero by expected overlap --------------------------------------------------


def _zero_world(
    *,
    n=40,
    read=100,
    estimate=200.0,
    complete=False,
    cap=None,
    cross=True,
    uniqueness_only=False,
    shared=0,
):
    child = sample(range(5000, 5000 + n), cap=cap)
    # DEC-39: B trusts only a random or hash-coordinated parent read, so these parents say so.
    parent = sample(
        [*range(5000, 5000 + shared), *range(90_000, 90_000 + read)],
        complete=complete,
        method=f"{B_TRUSTED_SAMPLE_METHODS[0]}:10",
    )
    return _pair(child, parent, cross=cross, estimate=estimate, uniqueness_only=uniqueness_only)


def _kept(snapshot) -> bool:
    return candidates(snapshot) == [("part", "gadget_id", "gadget", "id")]


@pytest.mark.parametrize("cross", [True, False], ids=["cross_source", "same_source"])
def test_b_drops_a_zero_when_the_expected_overlap_reaches_the_threshold(
    tmp_path, monkeypatch, cross
):
    """E = 40 * (100 / 200) * 0.25 = 5.0 >= 5, exactly on the boundary."""
    snapshot = _build(tmp_path, monkeypatch, _zero_world(cross=cross))
    assert candidates(snapshot) == [], "a zero at E = 5 against a partly read parent must drop"
    assert _new_drop_count(snapshot) == 1, builder_run(snapshot)
    assert builder_run(snapshot).get("dropped_zero_overlap_candidates", 0) == 0  # its own count


@pytest.mark.parametrize(
    ("estimate", "why"),
    [(210.0, "E = 4.76"), (400.0, "E = 2.5")],
)
def test_b_keeps_a_zero_when_the_expected_overlap_is_below_the_threshold(
    tmp_path, monkeypatch, estimate, why
):
    snapshot = _build(tmp_path, monkeypatch, _zero_world(estimate=estimate))
    assert _kept(snapshot), why
    assert _new_drop_count(snapshot) == 0
    assert _tiers(snapshot) == {("part", "gadget"): UNVERIFIED}


def test_b_drops_when_the_parent_is_read_in_full_by_the_estimate(tmp_path, monkeypatch):
    snapshot = _build(tmp_path, monkeypatch, _zero_world(read=300, estimate=300.0))  # E = 10
    assert candidates(snapshot) == [] and _new_drop_count(snapshot) == 1


def test_b_caps_the_parent_fraction_at_one(tmp_path, monkeypatch):
    """More distinct values read than estimated is f = 1, not more.

    40 child values: E = 40 * 1 * 0.25 = 10 >= 5, drops. 12 child values: f = 2 would give E = 6
    and drop, f = 1 gives E = 3 and keeps."""
    drop = _build(tmp_path, monkeypatch, _zero_world(n=40, read=100, estimate=50.0))
    assert candidates(drop) == [] and _new_drop_count(drop) == 1
    keep = build_snapshot(
        tmp_path, monkeypatch, _zero_world(n=12, read=100, estimate=50.0), name="keep"
    )
    assert _kept(keep)
    assert _new_drop_count(keep) == 0


@pytest.mark.parametrize("method", ["first_rows_limit:10", "first_documents_limit:10"])
def test_b_does_not_drop_a_zero_against_a_first_rows_parent(tmp_path, monkeypatch, method):
    """DEC-39: E = 5 as in the drop case, but the parent was read as its first rows."""
    world = _zero_world()
    world["beta"]["tables"]["gadget"]["values"]["id"].method = method
    snapshot = _build(tmp_path, monkeypatch, world)
    assert _kept(snapshot)
    assert _new_drop_count(snapshot) == 0


def test_b_does_not_apply_without_a_distinct_estimate(tmp_path, monkeypatch):
    for name, world in (
        ("none", _zero_world(read=1000, estimate=None)),
        ("no_estimate_in_profile", _zero_world(read=1000, estimate=None, uniqueness_only=True)),
    ):
        snapshot = build_snapshot(tmp_path, monkeypatch, world, name=name)
        assert _kept(snapshot), name
        assert _new_drop_count(snapshot) == 0, name


def test_b_a_zero_estimate_is_not_a_crash(tmp_path, monkeypatch):
    """Division by the estimate: the build completes whatever the candidate's fate."""
    snapshot = _build(tmp_path, monkeypatch, _zero_world(estimate=0.0))
    assert snapshot.exists()


def test_b_uses_the_child_values_compared_in_the_common_range(tmp_path, monkeypatch):
    """400 child values, a cap of 40: n = 40. f = 0.1 -> E = 1, kept (n = 400 would give E = 10)."""
    keep = _build(tmp_path, monkeypatch, _zero_world(n=400, cap=40, read=100, estimate=1000.0))
    assert _kept(keep), "B must count the child values compared, not all the child's values"
    assert _new_drop_count(keep) == 0
    # n = 40, f = 0.5 -> E = 5: drops
    drop = build_snapshot(
        tmp_path, monkeypatch, _zero_world(n=400, cap=40, read=100, estimate=200.0), name="drop"
    )
    assert candidates(drop) == [] and _new_drop_count(drop) == 1


def test_b_thresholds_come_from_the_config(tmp_path, monkeypatch):
    base = {"read": 100, "estimate": 400.0}  # E = 2.5 at the defaults
    harder = _build(tmp_path, monkeypatch, _zero_world(**base), c_min=0.5)  # E = 5.0
    assert candidates(harder) == [] and _new_drop_count(harder) == 1
    lower = build_snapshot(
        tmp_path,
        monkeypatch,
        _zero_world(**base),
        name="low",
        builder_extra={"min_expected_shared": 2},
    )
    assert candidates(lower) == [] and _new_drop_count(lower) == 1
    stricter = build_snapshot(
        tmp_path,
        monkeypatch,
        _zero_world(read=100, estimate=200.0),
        name="strict",
        builder_extra={"min_expected_shared": 10},
    )  # E = 5
    assert _kept(stricter) and _new_drop_count(stricter) == 0
    weaker = build_snapshot(
        tmp_path,
        monkeypatch,
        _zero_world(read=300, estimate=300.0),
        name="weak",
        builder_extra={"c_min": 0.1},
    )  # E = 4
    assert _kept(weaker)


def test_control_b_leaves_the_other_zero_rules_alone(tmp_path, monkeypatch):
    """A zero against a complete parent still drops under the old count; a positive overlap and an
    unmeasured sample never drop, whatever E is."""
    complete = _build(tmp_path, monkeypatch, _zero_world(complete=True, estimate=None))
    assert candidates(complete) == []
    assert builder_run(complete).get("dropped_zero_overlap_candidates") == 1
    assert _new_drop_count(complete) == 0
    positive = build_snapshot(
        tmp_path, monkeypatch, _zero_world(read=300, estimate=300.0, shared=3), name="positive"
    )
    assert _kept(positive) and _new_drop_count(positive) == 0
    tiny = build_snapshot(
        tmp_path,
        monkeypatch,
        _pair(sample([1, 2]), sample([5, 6, 7], complete=False), cross=True, estimate=3.0),
        name="tiny",
    )
    assert _kept(tiny) and _new_drop_count(tiny) == 0


# --- 6. E: DEC-3 config already blocks source pairs (DEC-34, no new key) -------------------------


def _three_sources():
    """alpha.part.gadget_id -> beta.gadget.id and beta.bolt.part_id -> alpha.part.id."""
    return {
        "alpha": {
            "tables": {
                "part": {"fields": [("id", "integer"), ("gadget_id", "integer")], "key": ["id"]}
            }
        },
        "beta": {
            "tables": {
                "gadget": {"fields": [("id", "integer")], "key": ["id"]},
                "bolt": {"fields": [("id", "integer"), ("part_id", "integer")], "key": ["id"]},
            }
        },
    }


BOTH = [("part", "gadget_id", "gadget", "id"), ("bolt", "part_id", "part", "id")]


def test_e_default_proposes_candidates_between_the_two_sources(tmp_path, monkeypatch):
    assert sorted(candidates(build_snapshot(tmp_path, monkeypatch, _three_sources()))) == sorted(
        BOTH
    )


@pytest.mark.parametrize("pairs", [[("alpha", "beta")], [("beta", "alpha")]])
def test_e_forbidden_source_pairs_block_every_candidate_between_two_sources(
    tmp_path, monkeypatch, pairs
):
    snapshot = _build(tmp_path, monkeypatch, _three_sources(), forbidden_source_pairs=pairs)
    assert candidates(snapshot) == []


def test_e_source_groups_block_the_same_pair(tmp_path, monkeypatch):
    snapshot = _build(tmp_path, monkeypatch, _three_sources(), source_groups=[["alpha"], ["beta"]])
    assert candidates(snapshot) == []


def test_e_adds_no_new_config_key():
    assert "allowed_source_pairs" not in BuilderConfig.model_fields


# --- 7. the requester ----------------------------------------------------------------------------

QUESTION = "How are gizmo and sprocket connected?"
DIRECT_QUESTION = "How are shipment and gizmo connected?"


def _bridge_sources(*, gid_values=None, sid_values=None):
    """gizmo and sprocket in alpha, a link table in beta whose two candidates reach them."""
    link = {"fields": [("lid", "integer"), ("gid", "integer"), ("sid", "integer")], "key": ["lid"]}
    gizmo = {"fields": [("gid", "integer")], "key": ["gid"]}
    sprocket = {"fields": [("sid", "integer")], "key": ["sid"]}
    if gid_values:
        gizmo["values"], link["values"] = {"gid": gid_values}, {"gid": gid_values}
    if sid_values:
        sprocket["values"], link["values"] = (
            {"sid": sid_values},
            {**link.get("values", {}), "sid": sid_values},
        )
    return {
        "alpha": {"tables": {"gizmo": gizmo, "sprocket": sprocket}},
        "beta": {"tables": {"linkrow": link}},
    }


def _direct_sources(*, cross=True, values=None):
    gizmo = {"fields": [("gid", "integer")], "key": ["gid"]}
    shipment = {"fields": [("hid", "integer"), ("gid", "integer")], "key": ["hid"]}
    if values:
        gizmo["values"], shipment["values"] = {"gid": values}, {"gid": values}
    if cross:
        return {"alpha": {"tables": {"gizmo": gizmo}}, "beta": {"tables": {"shipment": shipment}}}
    return {"alpha": {"tables": {"gizmo": gizmo, "shipment": shipment}}}


def _state(tmp_path, monkeypatch, sources, **builder):
    snapshot = build_snapshot(tmp_path, monkeypatch, sources, builder_extra=builder or None)
    index_snapshot(snapshot, tmp_path / "state")
    return tmp_path / "state"


def _reply(payload: dict) -> str:
    first = payload["items"][0]
    return json.dumps(
        {
            "checked": ["the retrieved tables and their relationship lines"],
            "answer": f"{first['name']} is catalogued and linked as listed.",
            "citations": [first["id"]],
            "outcome": "answered",
        }
    )


def _ask(state, monkeypatch, question, **overrides):
    monkeypatch.setenv("MODEL_KEY", MODEL_KEY)
    with ModelStub(_reply) as stub:
        config = AnswerConfig(
            base_url=stub.url,
            model="m",
            model_digest="d",
            api_key_env="MODEL_KEY",
            timeout_seconds=5,
            **overrides,
        )
        result = answer_question(state, question, config)
    assert stub.requests, f"the model was never called: {result}"
    user = next(m for m in stub.requests[0]["body"]["messages"] if m["role"] == "user")
    return json.loads(user["content"])


def _lines(payload):
    return {frozenset({e["from"], e["to"]}): e for e in payload["relationships"]}


def _path_only(payload):
    return {
        i["name"]
        for i in payload["items"]
        if i.get("role") == "path_only" and i.get("kind") != "field"
    }


def _tables(payload):
    return {i["name"] for i in payload["items"] if i.get("kind") != "field"}


def test_requester_fixture_premises(tmp_path, monkeypatch):
    """The candidates exist and retrieval finds the ends only; a fixture fault shows up here."""
    state = _state(tmp_path, monkeypatch, _bridge_sources())
    snapshot = tmp_path / "snapshot"
    assert sorted(candidates(snapshot)) == [
        ("linkrow", "gid", "gizmo", "gid"),
        ("linkrow", "sid", "sprocket", "sid"),
    ]
    assert _tables(_ask(state, monkeypatch, QUESTION, path_max_bridge_tables=0)) == {
        "gizmo",
        "sprocket",
    }
    direct = _state(_sub(tmp_path, "d"), monkeypatch, _direct_sources())
    assert _tables(_ask(direct, monkeypatch, DIRECT_QUESTION)) == {"gizmo", "shipment"}


def test_requester_config_flag_defaults_to_false_and_loads_from_toml(tmp_path):
    assert FLAG in AnswerConfig.model_fields, "no include_unverified_cross_source on AnswerConfig"
    assert AnswerConfig.model_fields[FLAG].default is False
    toml = tmp_path / "requester.toml"
    toml.write_text(
        '[requester]\nsnapshot_dir = "s"\nstate_dir = "t"\napi_key_env = "KEY"\n'
        '[requester.answer]\nbase_url = "http://127.0.0.1:1/v1"\nmodel = "m"\n'
        f'model_digest = "d"\napi_key_env = "KEY"\n{FLAG} = true\n',
        encoding="utf-8",
    )
    assert getattr(load_requester_config(toml).answer, FLAG) is True


def test_unverified_cross_source_candidates_stay_out_of_the_model_context_by_default(
    tmp_path, monkeypatch
):
    state = _state(tmp_path, monkeypatch, _direct_sources())
    assert _tiers(tmp_path / "snapshot") == {("shipment", "gizmo"): UNVERIFIED}
    payload = _ask(state, monkeypatch, DIRECT_QUESTION)
    assert payload["relationships"] == [], payload["relationships"]


def test_the_flag_puts_them_back_with_their_candidate_status(tmp_path, monkeypatch):
    assert FLAG in AnswerConfig.model_fields
    state = _state(tmp_path, monkeypatch, _direct_sources())
    payload = _ask(state, monkeypatch, DIRECT_QUESTION, **{FLAG: True})
    line = _lines(payload)[frozenset({"shipment", "gizmo"})]
    assert line["kind"] == "REFERENCE_CANDIDATE" and line["status"] == "tentative"


def test_verified_and_plausible_candidates_stay_in_by_default(tmp_path, monkeypatch):
    verified = _state(tmp_path, monkeypatch, _direct_sources(values=G10))
    assert _tiers(tmp_path / "snapshot") == {("shipment", "gizmo"): VERIFIED}
    assert frozenset({"shipment", "gizmo"}) in _lines(_ask(verified, monkeypatch, DIRECT_QUESTION))
    base = _sub(tmp_path, "s")
    same = _state(base, monkeypatch, _direct_sources(cross=False))
    assert _tiers(base / "snapshot") == {("shipment", "gizmo"): PLAUSIBLE}
    assert frozenset({"shipment", "gizmo"}) in _lines(_ask(same, monkeypatch, DIRECT_QUESTION))


def test_a_declared_key_is_untouched_by_the_flag(tmp_path, monkeypatch):
    sources = {
        "alpha": {
            "tables": {
                "gizmo": {"fields": [("gid", "integer")], "key": ["gid"]},
                "shipment": {
                    "fields": [("hid", "integer"), ("lref", "integer")],
                    "key": ["hid"],
                    "foreign": [("lref", "gizmo", "gid")],
                },
            }
        }
    }
    state = _state(tmp_path, monkeypatch, sources)
    line = _lines(_ask(state, monkeypatch, DIRECT_QUESTION))[frozenset({"shipment", "gizmo"})]
    assert line["status"] == "declared"


def test_a_bridge_resting_on_unverified_cross_source_edges_is_not_searched_by_default(
    tmp_path, monkeypatch
):
    state = _state(tmp_path, monkeypatch, _bridge_sources())
    assert set(_tiers(tmp_path / "snapshot").values()) == {UNVERIFIED}
    payload = _ask(state, monkeypatch, QUESTION)
    assert _path_only(payload) == set(), "the path search walked unverified cross-source edges"
    assert payload["relationships"] == []


def test_the_flag_lets_the_bridge_search_use_them_and_nothing_upgrades_them(tmp_path, monkeypatch):
    assert FLAG in AnswerConfig.model_fields
    state = _state(tmp_path, monkeypatch, _bridge_sources())
    payload = _ask(state, monkeypatch, QUESTION, **{FLAG: True})
    assert _path_only(payload) == {"linkrow"}
    lines = _lines(payload)
    assert {lines[frozenset({"linkrow", end})]["status"] for end in ("gizmo", "sprocket")} == {
        "tentative"
    }


def test_a_path_is_whole_or_nothing_when_only_one_of_its_edges_is_unverified(tmp_path, monkeypatch):
    """gid side verified by values, sid side not: with the flag off there is no bridge."""
    state = _state(tmp_path, monkeypatch, _bridge_sources(gid_values=G10))
    assert sorted(map(str, _tiers(tmp_path / "snapshot").values())) == sorted(
        [VERIFIED, UNVERIFIED]
    )
    assert _path_only(_ask(state, monkeypatch, QUESTION)) == set()
    assert _path_only(_ask(state, monkeypatch, QUESTION, **{FLAG: True})) == {"linkrow"}


def test_a_bridge_of_verified_cross_source_edges_is_found_by_default(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch, _bridge_sources(gid_values=G10, sid_values=G10))
    assert set(_tiers(tmp_path / "snapshot").values()) == {VERIFIED}
    assert _path_only(_ask(state, monkeypatch, QUESTION)) == {"linkrow"}


def test_a_confirmed_decision_brings_the_edge_back_without_the_flag(tmp_path, monkeypatch):
    path = _decisions(tmp_path, [_decide("beta.shipment.gid", "alpha.gizmo.gid", "confirmed")])
    state = _state(tmp_path, monkeypatch, _direct_sources(), decisions_file=path)
    assert frozenset({"shipment", "gizmo"}) in _lines(_ask(state, monkeypatch, DIRECT_QUESTION))


def test_a_rejected_decision_keeps_the_edge_out_even_with_the_flag(tmp_path, monkeypatch):
    path = _decisions(tmp_path, [_decide("beta.shipment.gid", "alpha.gizmo.gid", "rejected")])
    state = _state(tmp_path, monkeypatch, _direct_sources(), decisions_file=path)
    assert _edge(tmp_path / "snapshot", "shipment", "gizmo").status == "rejected"
    assert FLAG in AnswerConfig.model_fields
    for flag in (False, True):
        payload = _ask(state, monkeypatch, DIRECT_QUESTION, **{FLAG: flag})
        assert frozenset({"shipment", "gizmo"}) not in _lines(payload), flag


def test_the_index_state_still_holds_the_relationship(tmp_path, monkeypatch):
    """The filter is applied when answering (the flag lives in the answer config), so the same
    state serves both settings."""
    state = _state(tmp_path, monkeypatch, _direct_sources())
    with closing(sqlite3.connect(state / "catalog.sqlite3")) as db:
        assert db.execute("SELECT COUNT(*) FROM relationships").fetchone()[0] == 1


# --- 8. the planner -------------------------------------------------------------------------------


def _planner_graph(tmp_path, edges, placement, **config):
    """edges: [(id, from, to, tier or None)], all tentative candidates; placement: {object:
    source}."""
    now = datetime.now(UTC)
    common = {"observed_at": now, "collector_version": "synthetic", "provenance": "fixture"}
    sources = [
        Source(id=s, source_id=s, kind="postgresql", name=s, **common)
        for s in sorted(set(placement.values()))
    ]
    objects = [
        Object(id=o, source_id=s, native_id=o, kind="table", name=o, **common)
        for o, s in placement.items()
    ]
    evidence = [Evidence(id="ev", source_id="pg", kind="fixture", subject_id="a", **common)]
    rows = []
    for name, left, right, tier in edges:
        row = {
            "id": name,
            "source_id": placement[left],
            "kind": "REFERENCE_CANDIDATE",
            "from_id": left,
            "to_id": right,
            "status": "tentative",
            "evidence_ids": ["ev"],
            "field_pairs": [],
            **common,
        }
        if tier is not None:
            row["evidence_tier"] = tier
        rows.append(Edge.model_validate(row))
    snapshot = tmp_path / "snapshot"
    write_artifact(
        snapshot,
        "catalog-snapshot",
        {"nodes.jsonl": [*sources, *objects], "edges.jsonl": rows, "evidence.jsonl": evidence},
        producer="fixture",
        source_run_ids=[],
    )
    (tmp_path / "observations").mkdir()
    planner_config = PlannerConfig(graph_dir=tmp_path / "graph", **config)
    build_graph(snapshot, tmp_path / "observations", planner_config.graph_dir, planner_config)
    return planner_config


def _join(config, start, end, **kwargs):
    found = find_paths(
        config.graph_dir, PathRequest(from_id=start, to_id=end, **kwargs), "join", config
    )
    return [item["object_ids"] for item in found["paths"]]


PLACE = {"a": "pg", "b": "ch", "c": "pg", "d": "pg"}


def test_planner_config_flag_defaults_to_false_and_loads_from_toml(tmp_path):
    assert FLAG in PlannerConfig.model_fields, "no include_unverified_cross_source on PlannerConfig"
    assert PlannerConfig.model_fields[FLAG].default is False
    toml = tmp_path / "planner.toml"
    toml.write_text(f'[planner]\ngraph_dir = "g"\n{FLAG} = true\n', encoding="utf-8")
    assert getattr(load_planner_config(toml), FLAG) is True


def test_planner_leaves_unverified_cross_source_edges_out_by_default(tmp_path):
    config = _planner_graph(tmp_path, [("e1", "a", "b", UNVERIFIED)], PLACE)
    assert _join(config, "a", "b") == []


def test_planner_flag_puts_them_back(tmp_path):
    assert FLAG in PlannerConfig.model_fields
    config = _planner_graph(tmp_path, [("e1", "a", "b", UNVERIFIED)], PLACE, **{FLAG: True})
    assert _join(config, "a", "b") == [["a", "b"]]


def test_planner_routes_around_an_excluded_edge_and_takes_it_when_allowed(tmp_path):
    """a-c is the short cross-source guess; a-d and d-c exist as plausible and verified edges."""
    place = {"a": "pg", "c": "ch", "d": "pg"}
    edges = [
        ("direct", "a", "c", UNVERIFIED),
        ("h1", "a", "d", PLAUSIBLE),
        ("h2", "d", "c", VERIFIED),
    ]
    config = _planner_graph(_sub(tmp_path, "off"), edges, place)
    assert _join(config, "a", "c", max_paths=5) == [["a", "d", "c"]]
    assert FLAG in PlannerConfig.model_fields
    allowed = _planner_graph(_sub(tmp_path, "on"), edges, place, **{FLAG: True})
    assert ["a", "c"] in _join(allowed, "a", "c", max_paths=5)


@pytest.mark.parametrize(
    ("tier", "left", "right"),
    [(VERIFIED, "a", "b"), (PLAUSIBLE, "a", "c"), (None, "a", "b")],
    ids=["verified_cross_source", "plausible_same_source", "old_snapshot_without_a_tier"],
)
def test_control_planner_keeps_every_other_edge_by_default(tmp_path, tier, left, right):
    config = _planner_graph(tmp_path, [("e1", left, right, tier)], PLACE)
    assert _join(config, left, right) == [[left, right]]


def test_planner_on_a_builder_snapshot_honours_a_decision(tmp_path, monkeypatch):
    """End to end: the builder's rejected decision and confirmed decision reach the planner."""
    for decision, found in (("rejected", False), ("confirmed", True)):
        base = tmp_path / decision
        base.mkdir()
        path = _decisions(base, [_decide("beta.shipment.gid", "alpha.gizmo.gid", decision)])
        snapshot = build_snapshot(
            base, monkeypatch, _direct_sources(), builder_extra={"decisions_file": path}
        )
        (base / "observations").mkdir()
        config = PlannerConfig(graph_dir=base / "graph")
        build_graph(snapshot, base / "observations", config.graph_dir, config)
        objects = {
            o["name"]: i
            for i, o in json.loads((config.graph_dir / "graph.json").read_text())["objects"].items()
        }
        paths = _join(config, objects["shipment"], objects["gizmo"])
        assert bool(paths) is found, decision


# --- 9. documentation and plan 2.4 ----------------------------------------------------------------


def test_the_architecture_note_names_the_new_keys_and_field():
    text = (ROOT / "TECHNICAL_IMPLEMENTATION.md").read_text(encoding="utf-8")
    for name in (
        "evidence_tier",
        "c_min",
        "min_expected_shared",
        "decisions_file",
        FLAG,
        UNVERIFIED,
    ):
        assert name in text, f"TECHNICAL_IMPLEMENTATION.md does not mention {name}"


def _src(package: str) -> list[Path]:
    return sorted((ROOT / "packages").glob(f"{package}/src/**/*.py"))


def test_plan_2_4_no_hard_coded_threshold_outside_the_config():
    """c_min, min_expected_shared and the flags are set in config modules only, and read
    elsewhere."""
    literal = re.compile(
        r"\b(?:c_min|min_expected_shared|include_unverified_cross_source)\s*[:=]\s*"
        r"(?:Field\()?\s*(?:default=)?\s*(?:\d|True|False)"
    )
    hits = [
        f"{p.relative_to(ROOT)}"
        for p in sorted(ROOT.glob("packages/*/src/**/*.py"))
        if p.name != "config.py" and literal.search(p.read_text(encoding="utf-8"))
    ]
    assert hits == []
    # no bare 0.25 / 5 stand-in for the thresholds in the builder's decision code
    builder = ROOT / "packages/catalog-builder/src/dbig_builder/builder.py"
    assert not re.search(r"\b0\.25\b", builder.read_text(encoding="utf-8"))


def test_plan_2_4_the_settings_are_read_by_the_code_that_uses_them():
    def reads(package: str, name: str) -> bool:
        return any(
            name in p.read_text(encoding="utf-8") for p in _src(package) if p.name != "config.py"
        )

    assert reads("catalog-builder", "c_min"), "no builder module outside config.py reads c_min"
    assert reads("catalog-builder", "min_expected_shared")
    assert reads("catalog-builder", "decisions_file") or reads("catalog-builder", "decisions")
    assert reads("requester", FLAG), "the requester never reads include_unverified_cross_source"
    assert reads("planner", FLAG), "the planner never reads include_unverified_cross_source"


def test_plan_2_4_no_corpus_name_in_application_source_or_these_fixtures():
    names = [
        "zoom" + "info",
        "diff" + "bot",
        "enrichment" + "_engine",
        "company" + "revenue",
        "company" + "ticker",
        "industry" + "classification",
    ]
    pattern = re.compile("|".join(names), re.IGNORECASE)
    files = [*sorted(ROOT.glob("packages/*/src/**/*.py")), Path(__file__)]
    assert [p.name for p in files if pattern.search(p.read_text(encoding="utf-8"))] == []


def test_the_requester_and_the_builder_stay_independent_of_the_planner():
    pattern = re.compile(r"^\s*(?:from|import)\s+dbig_planner\b", re.MULTILINE)
    for package in ("requester", "catalog-builder"):
        assert [
            p.name for p in _src(package) if pattern.search(p.read_text(encoding="utf-8"))
        ] == []
