# ruff: noqa: E501 # C5: reason=adversarial test file kept as its author wrote it; approved=user, 2026-11-08; review=2027-05-08
"""Attack tests for P6-1 / P6-2 (F5, complete flag + bottom-k range) and P6-5 (F8).

Not collected by default; run by path:
    uv run pytest -q tests/catalogue_fixes/attack6_f5_f8.py -p no:cacheprovider
A failing test is a defect, or, where its docstring says RISK, an unsound behaviour for the lead
to decide. Fixtures use invented names.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import random
import sys
from datetime import UTC, datetime
from pathlib import Path

import catalogue_fixture_kit as kit
import pytest
from catalogue_fixture_kit import TOKEN_MATERIAL, build_snapshot, candidates
from dbig_builder.builder import MIN_TOKEN_SUPPORT, _overlap
from dbig_builder.config import BuilderConfig, ReviewConfig
from dbig_collectors.runtime import Budget, _capped_tokens, _value_tokens, discover_mongodb
from dbig_contracts.artifact import read_artifact
from dbig_contracts.models import Field, ValueTokens

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_value_evidence import (
    SYNTHETIC_MATERIAL,
    _mongo_client,
    _mongo_config,
)

ROOT = Path(__file__).resolve().parents[2]
N = MIN_TOKEN_SUPPORT
KEY = TOKEN_MATERIAL.encode()


def _h(value) -> str:
    return hmac.new(KEY, str(value).encode(), hashlib.sha256).hexdigest()


def _common(source: str = "s") -> dict:
    return {
        "source_id": source,
        "observed_at": datetime.now(UTC),
        "collector_version": "t",
        "provenance": "synthetic",
    }


def _tokens(values, *, cap=None, complete=True, key_id="run-1", field="f", via="pg") -> ValueTokens:
    """A token set built by the REAL collector cap functions (both engines' rule is under test)."""
    values = list(values)
    limit = cap if cap else 10**9
    if via == "pg":
        kept, observed, distinct, truncated = _value_tokens(values, key=KEY, limit=limit)
    else:
        kept, observed, distinct, truncated = _capped_tokens({_h(v) for v in values}, len(values), limit)
    return ValueTokens(
        id=f"t-{field}", field_id=field, object_id="o", key_id=key_id,
        sample_count=len(values), sample_method="first_rows_limit:1", observed_count=observed,
        distinct_count=distinct, truncated=truncated, complete=complete, tokens=kept,
        **_common(),
    )


def _field(name="gadget_id") -> Field:
    return Field(
        id="fc", object_id="o", native_id="n", name=name, data_type="integer", ordinal=1, **_common()
    )


def _ov(child: ValueTokens, parent: ValueTokens, support: int = N):
    # P7-1 (F14) added the level argument; the config's documented default.
    level = BuilderConfig.model_fields["overlap_confidence_level"].default
    return _overlap(_field(), "pid", child, parent, support, level)


# --- 1. is the bottom-k reasoning sound? --------------------------------------------------------


def test_both_engines_keep_the_smallest_sorted_hex():
    values = list(range(500))
    for via in ("pg", "mongo"):
        made = _tokens(values, cap=50, via=via)
        assert made.tokens == sorted({_h(v) for v in values})[:50]
        assert made.truncated is True


@pytest.mark.parametrize("seed", range(60))
def test_range_comparison_is_exact_against_ground_truth(seed):
    """Random sets and caps: shared and support must equal the exact value over the FULL sets
    restricted to hashes at or below the bound."""
    rng = random.Random(seed)  # noqa: S311 # C5: reason=test data, not a key; approved=user, 2026-11-08; review=2027-05-08
    universe = list(range(5000))
    parent_vals = rng.sample(universe, rng.choice([5, 20, 300, 1500]))
    child_vals = rng.sample(universe, rng.choice([5, 20, 200, 900]))
    if rng.random() < 0.4:
        child_vals = rng.sample(parent_vals, min(len(parent_vals), rng.choice([5, 20, 100])))
    child = _tokens(child_vals, cap=rng.choice([None, 8, 40, 128]), field="c", via=rng.choice(["pg", "mongo"]))
    parent = _tokens(parent_vals, cap=rng.choice([None, 8, 40, 128]), field="p", via=rng.choice(["pg", "mongo"]))
    out = _ov(child, parent)
    if out.kind == "value_overlap_not_measured":
        return
    caps = [max(s.tokens) for s in (child, parent) if s.truncated]
    bound = min(caps) if caps else "g"
    full_child = {_h(v) for v in child_vals if _h(v) <= bound}
    full_parent = {_h(v) for v in parent_vals}
    assert out.metrics["shared_tokens"] == len(full_child & full_parent)
    assert out.metrics["child_distinct_sampled"] == len(full_child)
    assert out.metrics["contained_fraction"] == round(len(full_child & full_parent) / len(full_child), 6)


def test_a_true_subset_never_reads_as_zero_when_either_side_is_capped():
    rng = random.Random(7)  # noqa: S311 # C5: reason=test data, not a key; approved=user, 2026-11-08; review=2027-05-08
    parent_vals = list(range(3940))
    seen = 0
    for _ in range(60):
        child_vals = rng.sample(parent_vals, rng.choice([5, 17, 80]))
        out = _ov(_tokens(child_vals, cap=rng.choice([None, 16, 64])), _tokens(parent_vals, cap=256, field="p"))
        if out.kind == "value_containment":
            seen += 1
            assert out.metrics["shared_tokens"] > 0, "true child read as disjoint"
            assert out.metrics["contained_fraction"] == 1.0
    assert seen


def test_key_id_mismatch_with_truncated_sides_is_unmeasured():
    out = _ov(_tokens(range(400), cap=32, key_id="a"), _tokens(range(400), cap=32, key_id="b", field="p"))
    assert out.kind == "value_overlap_not_measured"
    assert out.metrics["reason"] == "key_ids_differ"


def test_child_truncated_and_parent_incomplete_zero_is_kept_not_dropped():
    out = _ov(_tokens(range(10_000, 10_400), cap=32), _tokens(range(0, 300), complete=False, field="p"))
    assert out.kind == "value_containment"
    assert out.metrics["shared_tokens"] == 0
    assert out.metrics["parent_complete"] is False
    assert out.metrics["reason"] == "parent_sampled"
    assert "not a measurement" in out.explanation


def test_parent_truncated_with_distinct_below_support_is_unmeasured():
    out = _ov(_tokens(range(100), cap=64), _tokens([1, 2], cap=1, field="p"))
    assert out.kind == "value_overlap_not_measured"
    assert out.metrics["reason"] == "sample_too_small"


def test_capped_side_with_no_tokens_does_not_crash_or_read_as_a_zero():
    empty = ValueTokens(
        id="t", field_id="p", object_id="o", key_id="run-1", sample_count=9,
        sample_method="x", observed_count=9, distinct_count=9, truncated=True, complete=True,
        tokens=[], **_common(),
    )
    assert _ov(_tokens(range(50)), empty).kind == "value_overlap_not_measured"
    assert _ov(empty, _tokens(range(50), field="p")).kind == "value_overlap_not_measured"


def test_range_that_holds_fewer_than_support_child_values_is_unmeasured_not_dropped():
    out = _ov(_tokens(range(100_000, 100_400), cap=N - 1), _tokens(range(0, 400), field="p"))
    assert out.kind == "value_overlap_not_measured"


def test_min_support_is_read_from_the_argument_not_a_constant():
    child = _tokens(range(100_000, 100_030), cap=12)
    parent = _tokens(range(0, 400), field="p")
    assert _ov(child, parent, support=12).kind == "value_containment"
    assert _ov(child, parent, support=13).kind == "value_overlap_not_measured"


def test_complete_parent_zero_with_capped_child_is_the_drop_shape():
    out = _ov(_tokens(range(100_000, 100_400), cap=32), _tokens(range(0, 300), complete=True, field="p"))
    assert out.metrics["shared_tokens"] == 0 and out.metrics["parent_complete"] is True
    assert "reason" not in out.metrics


def test_parent_truncated_but_complete_with_disjoint_child_is_a_drop_shape():
    out = _ov(_tokens(range(100_000, 102_000)), _tokens(range(0, 3000), cap=64, complete=True, field="p"))
    assert out.metrics["shared_tokens"] == 0 and out.metrics["parent_complete"] is True


# --- 2. consumers of a value_containment row that carries a `reason` ----------------------------


def _sampled_zero_sources(col="sku"):
    parent = {"fields": [("rowkey", "integer"), (col, "text")], "unique": {col: 1.0},
              "values": {col: [f"p{i}" for i in range(60)]}}
    child = {"fields": [("rowid", "integer"), (col, "text")], "values": {col: [f"c{i}" for i in range(20)]}}
    return {"alpha": {"tables": {"catalogkind": parent}}, "beta": {"tables": {"itempart": child}}}


@pytest.fixture
def incomplete_parent(monkeypatch):
    original = kit.token_set

    def token_set(field_id, object_id, values, **kwargs):
        made = original(field_id, object_id, values, **kwargs)
        return made.model_copy(update={"complete": not str(values[0]).startswith("p")})

    monkeypatch.setattr(kit, "token_set", token_set)


def test_dec16_name_only_candidate_needs_a_real_measurement(tmp_path, monkeypatch, incomplete_parent):
    """RISK. DEC-16: a measured-unique parent with an ordinary name needs measured value overlap.
    A parent_sampled zero is declared 'not a measurement', so it must not satisfy that need;
    `needs_overlap` only tests `overlap.kind != 'value_containment'`."""
    snapshot = build_snapshot(tmp_path, monkeypatch, _sampled_zero_sources())
    assert candidates(snapshot) == [], candidates(snapshot)


def test_edge_explanation_does_not_headline_a_sampled_zero_as_a_percentage(tmp_path, monkeypatch, incomplete_parent):
    """RISK: edge text says 'value overlap 0% of 20 sampled child values' for an unmeasured zero."""
    snapshot = build_snapshot(tmp_path, monkeypatch, _sampled_zero_sources(col="catalogkindid"))
    _, data = read_artifact(snapshot)
    edges = [e for e in data["edges.jsonl"] if e.kind == "REFERENCE_CANDIDATE"]
    assert edges, "reference-style name keeps the candidate"
    for edge in edges:
        assert "0%" not in edge.explanation, edge.explanation


def test_planner_does_not_count_a_sampled_zero_as_value_evidence(tmp_path, monkeypatch, incomplete_parent):
    """RISK: packages/planner graph.py sets `value_evidence` for ANY value_containment row."""
    snapshot = build_snapshot(tmp_path, monkeypatch, _sampled_zero_sources(col="catalogkindid"))
    _, data = read_artifact(snapshot)
    rows = {e.id: e.model_dump() for e in data["evidence.jsonl"]}
    sampled = {i for i, r in rows.items() if r["kind"] == "value_containment" and r["metrics"].get("reason") == "parent_sampled"}
    assert sampled
    # Lead rewrite: ask the planner itself rather than re-implementing its old rule inline.
    from dbig_planner.config import PlannerConfig
    from dbig_planner.graph import build_graph

    observations = tmp_path / "observations"
    observations.mkdir()
    graph = build_graph(snapshot, observations, tmp_path / "graph", PlannerConfig(graph_dir=tmp_path / "graph"))
    citing = [ref for ref in graph["references"] if sampled & set(ref.get("evidence_ids", []))]
    assert citing, "the scenario must produce a reference citing the sampled zero"
    assert not any(ref["value_evidence"] for ref in citing), "a parent_sampled zero counted as value evidence"


def test_no_token_digest_reaches_snapshot_files_or_logs(tmp_path, monkeypatch, caplog, incomplete_parent):
    caplog.set_level(logging.DEBUG)
    snapshot = build_snapshot(tmp_path, monkeypatch, _sampled_zero_sources(col="catalogkindid"))
    blob = "".join(p.read_text(encoding="utf-8", errors="ignore") for p in snapshot.iterdir() if p.is_file())
    blob += caplog.text
    digests = {_h(f"p{i}") for i in range(60)} | {_h(f"c{i}") for i in range(20)}
    assert not [d for d in digests if d in blob]


# --- 3. collectors ------------------------------------------------------------------------------


def _mongo(monkeypatch, documents, **config):
    monkeypatch.setenv("DBIG_TOKENISER", SYNTHETIC_MATERIAL)
    return discover_mongodb(_mongo_client(documents), _mongo_config(**config), Budget(50))


def test_mongo_sample_rows_zero_publishes_no_tokens(monkeypatch):
    assert _mongo(monkeypatch, [{"_id": i} for i in range(5)], sample_rows=0)["tokens.jsonl"] == []


def test_mongo_collection_after_the_byte_budget_is_spent_has_no_tokens(monkeypatch):
    docs = [{"_id": i, "ref": str(i)} for i in range(3)]
    out = _mongo(monkeypatch, docs, sample_rows=10, max_sample_bytes=1)
    assert out["tokens.jsonl"] == []
    assert any(d.code == "SAMPLE_BYTE_LIMIT" for d in out["diagnostics.jsonl"])


def test_mongo_first_document_fits_second_does_not_is_incomplete(monkeypatch):
    docs = [{"_id": i, "ref": str(i)} for i in range(3)]
    out = _mongo(monkeypatch, docs, sample_rows=10, max_sample_bytes=len(str(docs[0]).encode()) + 1)
    assert out["tokens.jsonl"] and all(not t.complete for t in out["tokens.jsonl"])


def test_mongo_array_longer_than_the_element_limit_is_not_complete(monkeypatch):
    """RISK: `_mongo_paths` tokenises only the first `max_array_elements` of an array, so `tags[]`
    lacks values although every document was read; `complete` ignores that."""
    monkeypatch.setenv("DBIG_TOKENISER", SYNTHETIC_MATERIAL)
    docs = [{"_id": 1, "tags": [str(i) for i in range(40)]}]
    out = discover_mongodb(
        _mongo_client(docs), _mongo_config(sample_rows=10, max_array_elements=4), Budget(50)
    )
    entry = [t for t in out["tokens.jsonl"] if t.distinct_count == 4]
    assert entry, [t.distinct_count for t in out["tokens.jsonl"]]
    assert entry[0].complete is False, "values past max_array_elements were never read"


def test_old_bundle_without_complete_reads_with_digest_and_signature_checks(tmp_path):
    """Rewrite a bundle's token rows without `complete` (as an old collector wrote them), refresh the
    manifest digest and signature, and read it: additive field, so it must validate as incomplete."""
    from catalogue_fixture_kit import BUNDLE_KEY, write_bundle
    from dbig_contracts.artifact import _unsigned

    bundle = tmp_path / "old"
    table = {"fields": [("id", "integer")], "key": ["id"], "values": {"id": list(range(8))}}
    write_bundle(bundle, "alpha", "postgresql", {"gadget": table})
    path = bundle / "tokens.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines() if line]
    assert rows and rows[0]["complete"] is True
    for row in rows:
        del row["complete"]
    data = ("\n".join(json.dumps(r, sort_keys=True, separators=(",", ":")) for r in rows) + "\n").encode()
    path.write_bytes(data)
    manifest = json.loads((bundle / "manifest.json").read_text())
    entry = next(f for f in manifest["files"] if f["path"] == "tokens.jsonl")
    entry["bytes"], entry["sha256"] = len(data), hashlib.sha256(data).hexdigest()
    from dbig_contracts.models import Manifest

    model = Manifest.model_validate(manifest)
    manifest["signature"]["value"] = hmac.new(BUNDLE_KEY.encode(), _unsigned(model), hashlib.sha256).hexdigest()
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    _, tables = read_artifact(bundle, signing_key=BUNDLE_KEY.encode(), require_signature=True)
    assert [t.complete for t in tables["tokens.jsonl"]] == [False]


# --- 4. P6-5 ------------------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [0, -1, -512])
def test_reviewer_max_tokens_zero_or_negative_is_refused(bad):
    with pytest.raises(ValueError, match="greater than or equal to 1"):
        ReviewConfig(base_url="http://127.0.0.1:1/v1", model="m", model_digest="d", api_key_env="K", max_tokens=bad)


def test_reviewer_max_tokens_zero_in_toml_is_refused(tmp_path):
    from dbig_builder.config import load_config

    text = (ROOT / "examples" / "local" / "builder-with-vllm.toml").read_text(encoding="utf-8")
    assert "\nmax_tokens" not in text
    (tmp_path / "c.toml").write_text(text.replace("max_candidates = 20", "max_candidates = 20\nmax_tokens = 0"), encoding="utf-8")
    with pytest.raises(Exception, match="max_tokens|greater"):
        load_config(tmp_path / "c.toml")


def test_example_config_loads_unset_max_tokens_and_900():
    from dbig_builder.config import load_config

    config = load_config(ROOT / "examples" / "local" / "builder-with-vllm.toml")
    assert config.review.max_tokens is None
    assert config.review.timeout_seconds == 900
