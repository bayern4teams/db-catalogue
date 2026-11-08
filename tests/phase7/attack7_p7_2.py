"""Attack tests for P7-2 (F15, DEC-32/DEC-33). Invented names only; no value is printed."""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "catalogue_fixes"))
import catalogue_fixture_kit as kit
from catalogue_fixture_kit import build_snapshot, builder_run, candidates
from dbig_builder.builder import MIN_TOKEN_SUPPORT
from dbig_contracts.ids import stable_id

COL = "widgetid"
CHILD = "shipment"
CV = [f"c{i}" for i in range(10)]
FULL = list(CV)
HALF = CV[:5] + [f"x{i}" for i in range(5)]
TINY = CV[: MIN_TOKEN_SUPPORT - 2]
ZERO = [f"z{i}" for i in range(10)]

# shape -> (role, values, table extras, parent read partly?)
SHAPES = {
    "measured": ("measured", FULL, {}, False),
    "measured_half": ("measured", HALF, {}, False),
    "lower_bound": ("measured", FULL, {}, True),
    "parent_sampled_zero": ("measured", ZERO, {}, True),
    "no_tokens": ("measured", None, {}, False),
    "key_ids_differ": ("measured", FULL, {"token_key": "run-2"}, False),
    "sample_too_small": ("weak", TINY, {}, False),
    "complete_zero": ("measured", ZERO, {}, False),
}
# lower is better; equal numbers tie on (other_source, claim, id)
TIER = {
    "measured": 0,
    "measured_half": 0.5,
    "lower_bound": 1,
    "parent_sampled_zero": 1.5,
    "no_tokens": 2,
    "key_ids_differ": 2,
    "sample_too_small": 2,
}


def _table(role, values, extra):
    t = {"fields": [(COL, "integer")], **extra}
    if values is not None:
        t["values"] = {COL: values}
    if role == "declared":
        t["key"] = [COL]
    elif role == "weak":
        t["weak_key"] = COL
    else:
        t["unique"] = {COL: 1.0}
    return t


def _run(tmp_path, monkeypatch, placed, *, top_k, name="s", declared=()):
    """placed: {source: shape}; sources named `src<N>`; returns (kept table->shape, snapshot)."""
    partial = {s for s, shape in placed.items() if SHAPES[shape][3]}
    orig = kit.token_set

    def wrap(field_id, object_id, values, *, source_id, key_id="run-1"):
        item = orig(field_id, object_id, values, source_id=source_id, key_id=key_id)
        return item.model_copy(update={"complete": False}) if source_id in partial else item

    monkeypatch.setattr(kit, "token_set", wrap)
    sources = {
        "alpha": {
            "tables": {
                CHILD: {"fields": [("rowid", "integer"), (COL, "integer")], "values": {COL: CV}}
            }
        }
    }
    for s, shape in placed.items():
        role, values, extra, _ = SHAPES[shape]
        if s in declared:
            role = "declared"
        sources[s] = {
            "kind": "clickhouse" if role == "weak" else "postgresql",
            "tables": {f"t{s}": _table(role, values, extra)},
        }
    snap = build_snapshot(
        tmp_path, monkeypatch, sources, name=name, builder_extra={"top_k_per_field": top_k}
    )
    kept = {p.removeprefix("t") for c, _, p, _ in candidates(snap) if c == CHILD}
    return kept, snap


PAIRS = [
    ("measured", "lower_bound"),
    ("lower_bound", "parent_sampled_zero"),
    ("parent_sampled_zero", "no_tokens"),
    ("parent_sampled_zero", "key_ids_differ"),
    ("parent_sampled_zero", "sample_too_small"),
    ("measured", "no_tokens"),
    ("measured", "key_ids_differ"),
    ("measured", "sample_too_small"),
    ("measured", "measured_half"),
    ("lower_bound", "no_tokens"),
]


@pytest.mark.parametrize(("better", "worse"), PAIRS)
@pytest.mark.parametrize("swap", [False, True])
def test_tier_order_pairwise_at_top_k_1(tmp_path, monkeypatch, better, worse, swap):
    # swap flips which source (hence which field id) holds the better shape
    a, b = ("aaa", "zzz") if not swap else ("zzz", "aaa")
    kept, _ = _run(tmp_path, monkeypatch, {a: better, b: worse}, top_k=1)
    assert kept == {a}, (better, worse, kept)


@pytest.mark.parametrize("shape", ["no_tokens", "key_ids_differ", "sample_too_small"])
def test_unmeasured_reasons_are_kept_not_dropped_alone(tmp_path, monkeypatch, shape):
    kept, snap = _run(tmp_path, monkeypatch, {"aaa": shape}, top_k=1)
    assert kept == {"aaa"}
    assert builder_run(snap).get("dropped_zero_overlap_candidates", 0) == 0


def test_parent_sampled_zero_is_kept_alone_and_not_counted(tmp_path, monkeypatch):
    kept, snap = _run(tmp_path, monkeypatch, {"aaa": "parent_sampled_zero"}, top_k=1)
    assert kept == {"aaa"}
    assert builder_run(snap).get("dropped_zero_overlap_candidates", 0) == 0


def test_complete_zero_never_consumes_a_slot_at_any_position(tmp_path, monkeypatch):
    for zero_src in ("aaa", "mmm", "zzz"):
        others = [s for s in ("aaa", "mmm", "zzz") if s != zero_src]
        placed = {zero_src: "complete_zero", others[0]: "no_tokens", others[1]: "sample_too_small"}
        for k in (1, 2):
            kept, _ = _run(tmp_path, monkeypatch, placed, top_k=k, name=f"z{zero_src}{k}")
            assert zero_src not in {s for s in kept}
            assert len(kept) == k, (zero_src, k, kept)


def test_declared_beats_measured_at_equal_name_score_even_with_worse_tier(tmp_path, monkeypatch):
    kept, _ = _run(
        tmp_path,
        monkeypatch,
        {"aaa": "measured", "zzz": "sample_too_small"},
        top_k=1,
        declared={"zzz"},
    )
    assert kept == {"zzz"}
    kept, _ = _run(
        tmp_path,
        monkeypatch,
        {"aaa": "measured", "zzz": "lower_bound"},
        top_k=1,
        declared={"zzz"},
        name="s2",
    )
    assert kept == {"zzz"}


def test_higher_name_score_beats_declared_and_measured(tmp_path, monkeypatch):
    """`vendor_id`: table `vendors` is exact (100, unmeasured); `vendork` 92."""
    sources = {
        "alpha": {
            "tables": {
                CHILD: {
                    "fields": [("rowid", "integer"), ("vendor_id", "integer")],
                    "values": {"vendor_id": CV},
                }
            }
        },
        "beta": {
            "kind": "clickhouse",
            "tables": {
                "vendors": {"fields": [("id", "integer")], "weak_key": "id", "values": {"id": TINY}}
            },
        },
        "gamma": {
            "tables": {
                "vendork": {"fields": [("id", "integer")], "key": ["id"], "values": {"id": FULL}}
            }
        },
    }
    snap = build_snapshot(tmp_path, monkeypatch, sources, builder_extra={"top_k_per_field": 1})
    got = {p for c, _, p, _ in candidates(snap) if c == CHILD}
    assert got == {"vendors"}, got


# --- property: kept set == drop-then-cut, count independent of k (DEC-33) -----------------------


def _expected_order_ok(shapes_by_src, kept_k, kept_all):
    """Every kept parent is at least as good (tier) as every cut one."""
    cut = kept_all - kept_k
    worst_kept = max((TIER[shapes_by_src[s]] for s in kept_k), default=-1)
    best_cut = min((TIER[shapes_by_src[s]] for s in cut), default=99)
    return worst_kept <= best_cut


@pytest.mark.parametrize("seed", range(10))
def test_property_kept_equals_drop_then_cut_and_count_is_k_independent(tmp_path, monkeypatch, seed):
    rng = random.Random(seed)  # noqa: S311 # C5: reason=seeded test data, not a key; approved=user, 2026-11-08; review=2027-05-08
    pool = list(SHAPES)
    srcs = [f"s{i}{rng.choice('abcxyz')}" for i in range(rng.randint(3, 6))]
    placed = {s: rng.choice(pool) for s in srcs}
    declared = {s for s in srcs if rng.random() < 0.0}  # none: pure tier order
    kept_all, snap_all = _run(
        tmp_path, monkeypatch, placed, top_k=50, name="all", declared=declared
    )
    zero_srcs = {s for s, sh in placed.items() if sh == "complete_zero"}
    assert not (kept_all & zero_srcs)
    assert kept_all == set(placed) - zero_srcs
    count_all = builder_run(snap_all).get("dropped_zero_overlap_candidates", 0)
    prev: set[str] = set()
    for k in range(1, 5):
        kept_k, snap_k = _run(tmp_path, monkeypatch, placed, top_k=k, name=f"k{k}")
        assert kept_k <= kept_all
        assert len(kept_k) == min(k, len(kept_all)), (placed, k, kept_k)
        assert prev <= kept_k  # one ordering: prefixes are nested
        assert _expected_order_ok(placed, kept_k, kept_all), (placed, k, kept_k)
        assert builder_run(snap_k).get("dropped_zero_overlap_candidates", 0) == count_all
        prev = kept_k


def test_determinism_same_input_same_order_and_source_order_does_not_matter(tmp_path, monkeypatch):
    placed = {
        "s1": "no_tokens",
        "s2": "sample_too_small",
        "s3": "key_ids_differ",
        "s4": "measured_half",
        "s5": "lower_bound",
    }
    outs = []
    for i, order in enumerate([list(placed), list(reversed(placed)), list(placed)]):
        kept, snap = _run(
            tmp_path, monkeypatch, {s: placed[s] for s in order}, top_k=3, name=f"d{i}"
        )
        outs.append((kept, candidates(snap)))
    assert outs[0] == outs[1] == outs[2]


def test_ties_end_on_the_smallest_field_id(tmp_path, monkeypatch):
    srcs = ["ta", "tb", "tc", "td"]
    placed = {s: "no_tokens" for s in srcs}
    kept, _ = _run(tmp_path, monkeypatch, placed, top_k=1)
    want = min(srcs, key=lambda s: stable_id(s, "field", "1:1"))
    assert kept == {want}, (kept, want)
