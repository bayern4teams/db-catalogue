# ruff: noqa: E501, S108, S603
"""Attack tests for A1/D2/D9 (phase 2): the measured-zero drop and the "not measured" label.

Not collected by default (the name does not match `test_*.py`); run by path:
    uv run pytest tests/catalogue_fixes/attack2_a1_overlap.py -p no:cacheprovider
A failing test is a defect, or, where its docstring says RISK, an unsound behaviour for the lead
to decide. Fixtures use invented names (`gadget`, `part`, `crate`). Thresholds are read from the
config default, never typed.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

import catalogue_fixture_kit as kit
import pytest
from catalogue_fixture_kit import (
    BUNDLE_KEY,
    builder_run,
    candidate_text,
    candidates,
    containment_metrics,
)
from dbig_builder.builder import assemble
from dbig_builder.config import MIN_TOKEN_SUPPORT, BuilderConfig, load_config
from dbig_builder.render import render_catalog
from dbig_contracts.artifact import read_artifact
from pydantic import ValidationError

N = MIN_TOKEN_SUPPORT
CHILD = ("part", "gadget_id", "gadget", "id")
REPO = Path(__file__).resolve().parents[2]
OLD_TREE = Path(os.environ.get("DBIG_OLD_TREE2", "/tmp/wt-old2"))


def _range(start, count):
    return list(range(start, start + count))


def _sources(child_values=None, parent_values=None, *, cross=False, extra_tables=None):
    parent = {"fields": [("id", "integer"), ("title", "text")], "key": ["id"]}
    child = {"fields": [("id", "integer"), ("gadget_id", "integer")], "key": ["id"]}
    if parent_values is not None:
        parent["values"] = {"id": parent_values}
    if child_values is not None:
        child["values"] = {"gadget_id": child_values}
    tables = {"gadget": parent, "part": child, **(extra_tables or {})}
    if cross:
        return {"alpha": {"tables": {"gadget": parent}}, "beta": {"tables": {"part": child}}}
    return {"alpha": {"tables": tables}}


def _build(tmp_path, monkeypatch, sources, *, name="snapshot", **config):
    monkeypatch.setenv("BUNDLE_KEY", BUNDLE_KEY)
    bundles = []
    for source_id, spec in sources.items():
        bundle = tmp_path / f"bundle-{source_id}-{name}"
        kit.write_bundle(bundle, source_id, spec.get("kind", "postgresql"), spec["tables"])
        bundles.append(bundle)
    snapshot = tmp_path / name
    assemble(bundles, snapshot, BuilderConfig(signing_keys={"test": "BUNDLE_KEY"}, **config))
    return snapshot


def _cap_tokens(monkeypatch, caps: dict[str, int]):
    """Make the collector's token cap: keep the lowest `cap` digests, `truncated` when more."""
    original = kit.token_set

    def capped(field_id, object_id, values, *, source_id, key_id="run-1"):
        full = original(field_id, object_id, values, source_id=source_id, key_id=key_id)
        cap = caps.get(source_id)
        if cap is None or full.distinct_count <= cap:
            return full
        return full.model_copy(update={"tokens": full.tokens[:cap], "truncated": True})

    monkeypatch.setattr(kit, "token_set", capped)


# --- boundary at the configured threshold --------------------------------------------------------


@pytest.mark.parametrize("support", [1, 2, N, N + 3])
def test_the_drop_boundary_follows_the_configured_threshold(tmp_path, monkeypatch, support):
    """Each side at `support - 1`, `support` and `support + 1` distinct values, disjoint. Only a
    side below `support` keeps the candidate, whatever the default is."""
    for child_n, parent_n, dropped in [
        (support - 1, support + 5, False),
        (support + 5, support - 1, False),
        (support, support, True),
        (support + 1, support + 1, True),
        (support, support + 40, True),
    ]:
        if child_n < 1 or parent_n < 1:
            continue
        name = f"s{support}-{child_n}-{parent_n}"
        snapshot = _build(
            tmp_path, monkeypatch,
            _sources(_range(0, child_n), _range(10_000, parent_n)),
            name=name, min_token_support=support,
        )
        assert (candidates(snapshot) == []) is dropped, (support, child_n, parent_n)
        if not dropped:
            assert "sample too small" in candidate_text(snapshot)


def test_threshold_one_drops_a_single_disjoint_value(tmp_path, monkeypatch):
    snapshot = _build(
        tmp_path, monkeypatch, _sources([1], [2, 3]), min_token_support=1
    )
    assert candidates(snapshot) == []


def test_a_zero_distinct_side_is_not_called_a_zero_overlap(tmp_path, monkeypatch):
    """Both sides tokenised but the child column had only nulls: 0 distinct, nothing to compare."""
    snapshot = _build(tmp_path, monkeypatch, _sources([], _range(0, N + 2)))
    assert CHILD in candidates(snapshot), "an empty token set is ignorance, not a counterexample"
    assert "values not compared" in candidate_text(snapshot) or "not measured" in candidate_text(snapshot)


def test_one_shared_value_of_many_never_drops_at_any_threshold(tmp_path, monkeypatch):
    for support in (1, N, 50):
        snapshot = _build(
            tmp_path, monkeypatch,
            _sources(_range(0, 500), [0, *_range(50_000, 500)]),
            name=f"one-{support}", min_token_support=support,
        )
        assert candidates(snapshot) == [CHILD], support
        (metrics,) = containment_metrics(snapshot)
        assert metrics["shared_tokens"] == 1


def test_asymmetric_sample_sizes(tmp_path, monkeypatch):
    """The smaller side decides: 3 child values against 500 parent values is too small to drop
    (control), 500 child values against N parent values, disjoint, is dropped."""
    small_child = _build(
        tmp_path, monkeypatch, _sources(_range(0, N - 1), _range(9000, 500)), name="a"
    )
    assert candidates(small_child) == [CHILD]
    small_parent = _build(
        tmp_path, monkeypatch, _sources(_range(0, 500), _range(9000, N)), name="b"
    )
    assert candidates(small_parent) == []


# --- truncated token sets ------------------------------------------------------------------------


def test_RISK_a_truncated_parent_set_does_not_prove_the_child_is_not_contained(
    tmp_path, monkeypatch
):
    """RISK. The collector keeps the lowest-digest `cap` tokens of a parent with more distinct
    values than the cap. A child whose 8 values are ALL in the parent's full sample then shares
    nothing with the kept subset with probability ~ (1 - cap/distinct)^8, so a true relationship
    is dropped as a 'measured 0 of 8'. `lower_bound_only` is set on measured evidence, but the
    drop ignores it. Expected: a candidate whose measurement is only a lower bound is kept."""
    _cap_tokens(monkeypatch, {"alpha": 40})
    parent = _range(0, 2000)
    child = _range(100, 8)  # every child value is in the parent's full distinct set
    snapshot = _build(tmp_path, monkeypatch, _sources(child, parent, cross=False))
    # the child's source is alpha too here; cap applies to both, child (8) is below the cap
    assert candidates(snapshot) == [CHILD], "dropped on a truncated parent sample"


def test_RISK_a_truncated_child_set_zero_is_still_a_sample_of_the_child(tmp_path, monkeypatch):
    """Characterisation (passes): when only the CHILD is truncated, the kept child tokens are a
    hash-ordered subset of its values, so 0 shared of the kept ones is real evidence and the
    drop is statistically sound for a fully disjoint pair."""
    _cap_tokens(monkeypatch, {"beta": 40})
    snapshot = _build(
        tmp_path, monkeypatch,
        _sources(_range(0, 2000), _range(90_000, 60), cross=True),
    )
    assert candidates(snapshot) == []


def test_truncated_measured_overlap_is_still_labelled_lower_bound(tmp_path, monkeypatch):
    _cap_tokens(monkeypatch, {"beta": 40})
    snapshot = _build(
        tmp_path, monkeypatch,
        _sources(_range(0, 2000), _range(0, 2000), cross=True),
    )
    assert candidates(snapshot) == [CHILD]
    (metrics,) = containment_metrics(snapshot)
    assert metrics["lower_bound_only"] is True


def test_RISK_an_empty_kept_token_set_is_not_zero_overlap(tmp_path, monkeypatch):
    """A parent whose token list is empty although its distinct count says 50 (cap 0 / lost
    tokens) must not read as 'measured 0 of N'."""
    original = kit.token_set

    def hollow(field_id, object_id, values, *, source_id, key_id="run-1"):
        full = original(field_id, object_id, values, source_id=source_id, key_id=key_id)
        if source_id == "alpha":  # the parent
            return full.model_copy(update={"tokens": [], "truncated": True})
        return full

    monkeypatch.setattr(kit, "token_set", hollow)
    snapshot = _build(
        tmp_path, monkeypatch, _sources(_range(0, 20), _range(0, 50), cross=True)
    )
    assert candidates(snapshot) == [CHILD]


# --- top_k interaction ---------------------------------------------------------------------------


def test_a_dropped_top_candidate_does_not_hide_a_lower_ranked_true_one(tmp_path, monkeypatch):
    """With top_k_per_field=1 the best name match (disjoint values) is dropped after the slice,
    and the second-best (a real overlap) was never considered. Expected: the true parent still
    appears, because top-k should count only candidates that survive the overlap check."""
    parent_a = {"fields": [("id", "integer")], "key": ["id"], "values": {"id": _range(9000, N + 2)}}
    parent_b = {"fields": [("id", "integer")], "key": ["id"], "values": {"id": _range(0, N + 2)}}
    child = {"fields": [("id", "integer"), ("gadget_id", "integer")], "key": ["id"],
             "values": {"gadget_id": _range(0, N)}}
    tables = {"gadget": parent_a, "gadgetz": parent_b, "part": child}
    snapshot = _build(
        tmp_path, monkeypatch, {"alpha": {"tables": tables}}, top_k_per_field=1
    )
    found = candidates(snapshot)
    wide = _build(
        tmp_path, monkeypatch, {"alpha": {"tables": tables}}, name="wide", top_k_per_field=5
    )
    assert candidates(wide) == [("part", "gadget_id", "gadgetz", "id")], "control"
    assert ("part", "gadget_id", "gadgetz", "id") in found, found


# --- the record of a drop ------------------------------------------------------------------------


def test_the_run_counts_drops_and_is_absent_when_nothing_dropped(tmp_path, monkeypatch):
    dropped = _build(
        tmp_path, monkeypatch,
        _sources(_range(0, N), _range(9000, N), extra_tables={
            "crate": {"fields": [("id", "integer"), ("gadget_id", "integer")], "key": ["id"],
                      "values": {"gadget_id": _range(5000, N)}}}),
        name="two",
    )
    assert builder_run(dropped).get("dropped_zero_overlap_candidates") == 2
    kept = _build(tmp_path, monkeypatch, _sources(_range(0, N), _range(0, N)), name="none")
    assert "dropped_zero_overlap_candidates" not in builder_run(kept)
    unsampled = _build(tmp_path, monkeypatch, _sources(), name="off")
    assert "dropped_zero_overlap_candidates" not in builder_run(unsampled)


def test_a_dropped_candidate_leaves_no_orphan_evidence_or_edge(tmp_path, monkeypatch):
    snapshot = _build(
        tmp_path, monkeypatch, _sources(_range(0, N), _range(9000, N))
    )
    _, data = read_artifact(snapshot)
    kinds = {item.kind for item in data["evidence.jsonl"]}
    leftovers = kinds & {
        "name_similarity", "compatible_type", "catalog_key", "measured_uniqueness",
        "value_containment", "value_overlap_not_measured",
    }
    assert not leftovers, leftovers
    assert not [e for e in data["edges.jsonl"] if e.kind == "REFERENCE_CANDIDATE"]
    # every evidence row that remains is cited by some edge (no unreferenced evidence)
    cited = {key for edge in data["edges.jsonl"] for key in edge.evidence_ids}
    assert {item.id for item in data["evidence.jsonl"]} <= cited


def test_every_evidence_row_in_a_kept_snapshot_is_cited(tmp_path, monkeypatch):
    for index, (child, parent) in enumerate(
        [(None, None), (_range(0, N), _range(0, N)), (_range(0, N - 1), _range(0, N))]
    ):
        snapshot = _build(tmp_path, monkeypatch, _sources(child, parent), name=f"k{index}")
        _, data = read_artifact(snapshot)
        cited = {key for edge in data["edges.jsonl"] for key in edge.evidence_ids}
        assert {item.id for item in data["evidence.jsonl"]} <= cited, index


def test_ids_are_identical_across_runs_and_bundle_order(tmp_path, monkeypatch):
    def ids(snapshot):
        _, data = read_artifact(snapshot)
        return (
            sorted(e.id for e in data["edges.jsonl"]),
            sorted(e.id for e in data["evidence.jsonl"]),
        )

    for index, (child, parent) in enumerate(
        [(None, None), (_range(0, N), _range(0, N)), (_range(0, N - 1), _range(0, N + 1)),
         (_range(0, N), _range(9000, N))]
    ):
        first = _build(tmp_path, monkeypatch, _sources(child, parent, cross=True), name=f"r{index}a")
        second = _build(tmp_path, monkeypatch, _sources(child, parent, cross=True), name=f"r{index}b")
        assert ids(first) == ids(second), index


def test_unmeasured_and_measured_evidence_for_one_pair_do_not_share_an_id_with_different_kind(
    tmp_path, monkeypatch
):
    """The label and the measurement use the same id suffix; they must never both exist."""
    snapshot = _build(tmp_path, monkeypatch, _sources(_range(0, N - 1), _range(0, N)))
    _, data = read_artifact(snapshot)
    kinds = [e.kind for e in data["evidence.jsonl"] if "containment" in e.kind or "overlap" in e.kind]
    assert kinds == ["value_overlap_not_measured"], kinds


def test_no_digest_value_or_reason_text_reaches_snapshot_page_or_logs(
    tmp_path, monkeypatch, caplog, capsys
):
    caplog.set_level(logging.DEBUG)
    values = [f"zq-private-{i}-token" for i in range(N + 2)]
    parent = {"fields": [("id", "text")], "key": ["id"], "values": {"id": values}}
    child = {"fields": [("id", "integer"), ("gadget_id", "text")], "key": ["id"],
             "values": {"gadget_id": [f"zq-other-{i}-token" for i in range(N + 2)]}}
    snapshot = _build(tmp_path, monkeypatch, {"alpha": {"tables": {"gadget": parent, "part": child}}})
    page = tmp_path / "page.html"
    render_catalog(snapshot, [], page, None)
    digests = []
    for bundle in kit.bundle_dirs(tmp_path):
        for line in (bundle / "tokens.jsonl").read_text(encoding="utf-8").splitlines():
            digests.extend(json.loads(line)["tokens"])
    assert digests
    out = capsys.readouterr()
    haystacks = [page.read_text(encoding="utf-8"), caplog.text, out.out, out.err]
    haystacks += [p.read_text(encoding="utf-8", errors="replace") for p in snapshot.iterdir()]
    for text in haystacks:
        assert "zq-private" not in text and "zq-other" not in text
        assert not any(d in text for d in digests)


# --- composite candidates, declared keys ---------------------------------------------------------


def test_composite_candidates_are_unaffected_by_disjoint_tokens(tmp_path, monkeypatch):
    parent = {"fields": [("region", "text"), ("number", "integer")], "key": ["region", "number"],
              "values": {"region": [f"r{i}" for i in range(N)], "number": _range(0, N)}}
    child = {"fields": [("id", "integer"), ("gadget_region", "text"), ("gadget_number", "integer")],
             "key": ["id"],
             "values": {"gadget_region": [f"x{i}" for i in range(N)], "gadget_number": _range(900, N)}}
    snapshot = _build(tmp_path, monkeypatch, {"alpha": {"tables": {"gadget": parent, "part": child}}})
    found = candidates(snapshot)
    assert ("part", "gadget_number", "gadget", "number") in found, found
    assert ("part", "gadget_region", "gadget", "region") in found, found
    assert "dropped_zero_overlap_candidates" not in builder_run(snapshot)


def test_a_declared_foreign_key_survives_disjoint_tokens(tmp_path, monkeypatch):
    sources = _sources(_range(0, N), _range(9000, N))
    sources["alpha"]["tables"]["part"]["foreign"] = [("gadget_id", "gadget", "id")]
    snapshot = _build(tmp_path, monkeypatch, sources)
    _, data = read_artifact(snapshot)
    assert any(edge.kind == "DECLARES_FK" for edge in data["edges.jsonl"])


# --- config --------------------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [0, -1, -100])
def test_min_token_support_rejects_less_than_one(bad):
    with pytest.raises(ValidationError):
        BuilderConfig(signing_keys={"k": "E"}, min_token_support=bad)


def test_min_token_support_rejects_non_integers_and_unknown_siblings():
    with pytest.raises(ValidationError):
        BuilderConfig(signing_keys={"k": "E"}, min_token_support="many")  # type: ignore[arg-type]  # noqa: S106
    with pytest.raises(ValidationError):
        BuilderConfig(signing_keys={"k": "E"}, min_token_supports=4)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        BuilderConfig(signing_keys={"k": "E"}, min_token_support=2.5)  # type: ignore[arg-type]


def test_min_token_support_default_is_the_named_constant_and_loads_from_toml(tmp_path):
    assert BuilderConfig(signing_keys={"k": "E"}).min_token_support == MIN_TOKEN_SUPPORT
    path = tmp_path / "builder.toml"
    path.write_text('[builder]\nmin_token_support = 7\n[builder.signing_keys]\nk = "E"\n')
    assert load_config(path).min_token_support == 7
    path.write_text('[builder]\nmin_token_support = 0\n[builder.signing_keys]\nk = "E"\n')
    with pytest.raises(Exception) as error:  # noqa: PT011
        load_config(path)
    assert "min_token_support" in str(error.value)


# --- mutation and old-code negatives -------------------------------------------------------------


def test_mutation_removing_the_drop_fails_the_acceptance_tests(tmp_path):
    """Copy the builder, neutralise the drop, run the lead's acceptance file against the copy."""
    source = REPO / "packages" / "catalog-builder" / "src" / "dbig_builder"
    tree = tmp_path / "mut" / "dbig_builder"
    shutil.copytree(source, tree, ignore=shutil.ignore_patterns("__pycache__"))
    text = (tree / "builder.py").read_text(encoding="utf-8")
    assert 'overlap.metrics["shared_tokens"] == 0' in text
    (tree / "builder.py").write_text(
        text.replace('overlap.metrics["shared_tokens"] == 0', 'overlap.metrics["shared_tokens"] < 0'),
        encoding="utf-8",
    )
    env = {**os.environ, "PYTHONPATH": f"{tmp_path / 'mut'}{os.pathsep}{os.environ.get('PYTHONPATH', '')}"}
    done = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pytest", str(REPO / "tests/catalogue_fixes/test_a1_overlap.py"),
         "-q", "-p", "no:cacheprovider", "-x"],
        capture_output=True, text=True, env=env, cwd=REPO, timeout=300, check=False,
    )
    assert done.returncode != 0, done.stdout[-800:]
    assert "test_disjoint_columns_give_no_candidate" in done.stdout


def test_the_old_code_neither_labels_nor_drops(tmp_path):
    if not OLD_TREE.exists():
        pytest.skip("no old worktree at /tmp/wt-old2 (git worktree add /tmp/wt-old2 fbaaa7d)")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(
        str(OLD_TREE / "packages" / p / "src") for p in ("requester", "catalog-builder", "contracts")
    )}
    done = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pytest", str(REPO / "tests/catalogue_fixes/test_a1_overlap.py"),
         "-q", "-p", "no:cacheprovider"],
        capture_output=True, text=True, env=env, cwd=REPO, timeout=300, check=False,
    )
    assert done.returncode != 0
    assert "test_disjoint_columns_give_no_candidate" in done.stdout
    assert "test_sampling_off_says_values_were_not_compared" in done.stdout
