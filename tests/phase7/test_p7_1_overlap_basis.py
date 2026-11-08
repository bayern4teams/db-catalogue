"""P7-1 (live finding F14): say "at least" for a lower bound, give a Wilson interval otherwise.

Derived from `docs/plans/PHASE7_LOCAL_PLAN.md` section 4 "P7-1" and the lead's brief only; the
implementation does not exist. Spec as checked here, for a `value_containment` with shared > 0:

* basis `lower_bound` when `parent_complete` is false (or `lower_bound_only` without the common
  hash range). Sentence "value overlap at least X% of N sampled child values (parent only partly
  read; the true overlap may be much higher)". Metrics `estimate` = `lower` = shared/N, `upper`
  None.
* basis `measured` otherwise. Sentence "value overlap ≈X% (95% CI a-b%) of N sampled child
  values",
  a-b the Wilson score interval on shared/N, N = child distinct sampled. The level is a new
  builder config field (default 0.95); the sentence follows the configured level.
* the zero-against-partly-read-parent path (`reason: parent_sampled`) and
  `value_overlap_not_measured` keep their wording.
* requester: the relationship body gains `basis`, `estimate`, `lower`, `upper` for edges that have a
  containment measurement, equal to the builder metrics; `INDEX_VERSION` is bumped past 7.

No test pins the new config field's name: it is found as the one BuilderConfig field whose default
is 0.95. Tests are plain (not xfail): on the current code they fail. `control_*` pass before and
after. Names are invented and generic (plan section 2.4).
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
from math import sqrt
from pathlib import Path
from statistics import NormalDist

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "catalogue_fixes"))
import catalogue_fixture_kit as kit
from catalogue_fixture_kit import (
    MODEL_KEY,
    TOKEN_MATERIAL,
    ModelStub,
    build_snapshot,
    candidates,
    containment_metrics,
)
from dbig_builder.config import MIN_TOKEN_SUPPORT, BuilderConfig
from dbig_contracts.artifact import read_artifact
from dbig_contracts.ids import stable_id
from dbig_contracts.models import ValueTokens
from dbig_requester import search as search_module
from dbig_requester.answer import answer_question
from dbig_requester.config import AnswerConfig
from dbig_requester.search import index_snapshot
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]
CHILD = ("part", "gadget_id", "gadget", "id")
PARENT_KEYS = list(range(100_000, 103_940))
DISJOINT_BASE = 500_000
CAP = 256
AT_LEAST = re.compile(
    r"value overlap at least (?P<x>[\d.]+)% of (?P<n>\d+) sampled child values "
    r"\(parent only partly read; the true overlap may be much higher\)"
)
MEASURED = re.compile(
    r"value overlap ≈(?P<x>[\d.]+)% \((?P<level>[\d.]+)% CI (?P<a>[\d.]+)-(?P<b>[\d.]+)%\) "
    r"of (?P<n>\d+) sampled child values"
)


# --- an independent Wilson score interval --------------------------------------------------------


def wilson(shared: int, total: int, level: float) -> tuple[float, float]:
    z = NormalDist().inv_cdf(0.5 + level / 2)
    p = shared / total
    d = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / d
    half = z * sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / d
    return max(0.0, centre - half), min(1.0, centre + half)


# --- fixtures: a token set with an explicit `complete` and cap (as test_p6_f5 builds them) -------


def _h(value) -> str:
    return hmac.new(TOKEN_MATERIAL.encode(), str(value).encode(), hashlib.sha256).hexdigest()


class Sample(list):
    complete: bool = True
    cap: int | None = None


def sample(values, *, complete: bool = True, cap: int | None = None) -> Sample:
    made = Sample(values)
    made.complete = complete
    made.cap = cap
    return made


def _bound(values, cap: int) -> str:
    return sorted({_h(v) for v in values})[cap - 1]


def _pick(pool, count: int, *, bound: str, inside: bool) -> list:
    found = [v for v in pool if (_h(v) <= bound) == inside]
    assert len(found) >= count, "the pool is too small for this scenario"
    return found[:count]


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
            field_id=field_id, object_id=object_id, key_id=kwargs.get("key_id", "run-1"),
            sample_count=len(values), sample_method="first_rows_limit:10",
            observed_count=len(values), distinct_count=len(digests),
            truncated=len(kept) < len(digests), complete=values.complete, tokens=kept,
            source_id=kwargs["source_id"], observed_at=datetime.now(UTC),
            collector_version="test", provenance="synthetic",
        )

    monkeypatch.setattr(kit, "token_set", token_set)


def _sources(child, parent) -> dict:
    return {"alpha": {"tables": {
        "gadget": {"fields": [("id", "integer"), ("title", "text")], "key": ["id"],
                   "values": {"id": parent}},
        "part": {"fields": [("id", "integer"), ("gadget_id", "integer")], "key": ["id"],
                 "values": {"gadget_id": child}},
    }}}


def _build(tmp_path, monkeypatch, child, parent, **extra) -> Path:
    return build_snapshot(tmp_path, monkeypatch, _sources(child, parent), **extra)


def _explanation(snapshot: Path) -> str:
    _, data = read_artifact(snapshot)
    [edge] = [e for e in data["edges.jsonl"] if e.kind == "REFERENCE_CANDIDATE"]
    return edge.explanation


def _one_metrics(snapshot: Path) -> dict:
    assert candidates(snapshot) == [CHILD]
    [metrics] = containment_metrics(snapshot)
    return metrics


def _level_field() -> str:
    # Lead: pinned by name once P7-5 option C added a second 0.95 default (format_reject_share).
    assert BuilderConfig.model_fields["overlap_confidence_level"].default == 0.95
    return "overlap_confidence_level"


# 40 distinct child values, 30 of them in the parent (so shared / N = 30 / 40).
CHILD_40 = list(range(1000, 1040))
PARENT_30_OF_40 = list(range(1000, 1030)) + list(range(7000, 7200))


# --- 1. a partly read parent: a lower bound ------------------------------------------------------


def test_partly_read_parent_says_at_least_and_has_no_upper(tmp_path, monkeypatch):
    parent = sample(PARENT_30_OF_40, complete=False)
    snapshot = _build(tmp_path, monkeypatch, sample(CHILD_40), parent)
    metrics = _one_metrics(snapshot)
    assert metrics["shared_tokens"] == 30 and metrics["child_distinct_sampled"] == 40
    text = _explanation(snapshot)
    found = AT_LEAST.search(text)
    assert found, text
    assert abs(float(found["x"]) - 75) < 0.51 and found["n"] == "40"
    assert "CI" not in text and "≈" not in text
    assert metrics["basis"] == "lower_bound"
    assert metrics["estimate"] == pytest.approx(0.75, abs=1e-6)
    assert metrics["lower"] == pytest.approx(0.75, abs=1e-6)
    assert metrics["upper"] is None
    assert metrics["contained_fraction"] == pytest.approx(0.75, abs=1e-6)  # kept as before


def test_an_old_bundle_without_the_complete_flag_is_a_lower_bound(tmp_path, monkeypatch):
    """Spec P6-1: a missing flag is false, so the overlap is a lower bound."""
    parent = sample(PARENT_30_OF_40)
    original = kit.token_set

    def without_flag(field_id, object_id, values, **kwargs):
        made = original(field_id, object_id, values, **kwargs)
        return ValueTokens.model_validate(
            {k: v for k, v in made.model_dump().items() if k != "complete"}
        )

    monkeypatch.setattr(kit, "token_set", without_flag)
    snapshot = _build(tmp_path, monkeypatch, sample(CHILD_40), parent)
    assert _one_metrics(snapshot)["basis"] == "lower_bound"
    assert AT_LEAST.search(_explanation(snapshot))


def test_capped_incomplete_parent_in_the_common_range_is_still_a_lower_bound(tmp_path, monkeypatch):
    bound = _bound(PARENT_KEYS, CAP)
    child = _pick(PARENT_KEYS, 24, bound=bound, inside=True) + _pick(
        range(DISJOINT_BASE, DISJOINT_BASE + 5000), 6, bound=bound, inside=True
    )
    snapshot = _build(
        tmp_path, monkeypatch, sample(child), sample(PARENT_KEYS, complete=False, cap=CAP)
    )
    metrics = _one_metrics(snapshot)
    assert metrics["basis"] == "lower_bound" and metrics["upper"] is None
    assert AT_LEAST.search(_explanation(snapshot))


# --- 2. a complete parent: a measurement with a Wilson interval ----------------------------------


def test_complete_parent_gives_a_wilson_interval(tmp_path, monkeypatch):
    snapshot = _build(tmp_path, monkeypatch, sample(CHILD_40), sample(PARENT_30_OF_40))
    metrics = _one_metrics(snapshot)
    assert metrics["basis"] == "measured"
    low, high = wilson(30, 40, 0.95)
    assert metrics["estimate"] == pytest.approx(0.75, abs=1e-6)
    assert metrics["lower"] == pytest.approx(low, abs=1e-6)
    assert metrics["upper"] == pytest.approx(high, abs=1e-6)
    assert metrics["lower"] <= metrics["estimate"] <= metrics["upper"]
    text = _explanation(snapshot)
    found = MEASURED.search(text)
    assert found, text
    assert "at least" not in text
    assert float(found["level"]) == 95 and found["n"] == "40"
    assert abs(float(found["x"]) - 75) < 0.51
    # Lead: CI lower is floored and upper ceiled (never overclaims), so each is within 1 point.
    assert 0 <= 100 * low - float(found["a"]) < 1 and 0 <= float(found["b"]) - 100 * high < 1


def test_the_interval_uses_the_child_distinct_count_not_the_sample_count(tmp_path, monkeypatch):
    """Duplicates in the sample must not change N: it is the child's distinct sampled values."""
    child = sample(CHILD_40 * 3)  # 120 sampled rows, 40 distinct values
    snapshot = _build(tmp_path, monkeypatch, child, sample(PARENT_30_OF_40))
    metrics = _one_metrics(snapshot)
    low, high = wilson(30, 40, 0.95)
    assert (metrics["lower"], metrics["upper"]) == (
        pytest.approx(low, abs=1e-6), pytest.approx(high, abs=1e-6)
    )
    assert MEASURED.search(_explanation(snapshot))["n"] == "40"


# --- 3. the confidence level is config -----------------------------------------------------------


def test_the_default_level_is_a_validated_config_field():
    name = _level_field()
    assert BuilderConfig(signing_keys={"k": "E"}).model_dump()[name] == 0.95


@pytest.mark.parametrize("bad", [0, 1, 1.5, -0.1])
def test_a_level_outside_the_open_unit_interval_is_rejected(bad):
    with pytest.raises(ValidationError):
        BuilderConfig(signing_keys={"k": "E"}, **{_level_field(): bad})


def test_a_level_of_0_9_changes_the_bounds_and_the_sentence(tmp_path, monkeypatch):
    snapshot = _build(
        tmp_path, monkeypatch, sample(CHILD_40), sample(PARENT_30_OF_40),
        builder_extra={_level_field(): 0.9},
    )
    metrics = _one_metrics(snapshot)
    low, high = wilson(30, 40, 0.9)
    assert metrics["lower"] == pytest.approx(low, abs=1e-6)
    assert metrics["upper"] == pytest.approx(high, abs=1e-6)
    assert (low, high) != pytest.approx(wilson(30, 40, 0.95))
    found = MEASURED.search(_explanation(snapshot))
    assert found and float(found["level"]) == 90


def test_the_level_does_not_touch_a_lower_bound(tmp_path, monkeypatch):
    snapshot = _build(
        tmp_path, monkeypatch, sample(CHILD_40), sample(PARENT_30_OF_40, complete=False),
        builder_extra={_level_field(): 0.9},
    )
    assert _one_metrics(snapshot)["upper"] is None
    assert AT_LEAST.search(_explanation(snapshot))


# --- 4. capped sides compared in the common hash range, complete parent: measured ----------------


def test_capped_complete_parent_compared_in_range_is_measured(tmp_path, monkeypatch):
    bound = _bound(PARENT_KEYS, CAP)
    inside = _pick(PARENT_KEYS, 24, bound=bound, inside=True)
    disjoint_in = _pick(range(DISJOINT_BASE, DISJOINT_BASE + 5000), 6, bound=bound, inside=True)
    outside = _pick(PARENT_KEYS, 10, bound=bound, inside=False)  # outside the range: not counted
    snapshot = _build(
        tmp_path, monkeypatch,
        sample(inside + disjoint_in + outside),
        sample(PARENT_KEYS, complete=True, cap=CAP),
    )
    metrics = _one_metrics(snapshot)
    assert metrics["lower_bound_only"] is True and metrics["compared_in_common_hash_range"] is True
    assert metrics["child_distinct_sampled"] == 30 and metrics["shared_tokens"] == 24
    assert metrics["basis"] == "measured"
    low, high = wilson(24, 30, 0.95)
    assert metrics["lower"] == pytest.approx(low, abs=1e-6)
    assert metrics["upper"] == pytest.approx(high, abs=1e-6)
    found = MEASURED.search(_explanation(snapshot))
    assert found and found["n"] == "30"


def test_capped_child_against_a_complete_parent_is_measured(tmp_path, monkeypatch):
    child_pool = list(range(200_000, 200_600))
    child = sample(child_pool, cap=CAP)
    bound = _bound(child_pool, CAP)
    parent = sample(
        _pick(child_pool, 40, bound=bound, inside=True)
        + _pick(range(DISJOINT_BASE, DISJOINT_BASE + 5000), 40, bound=bound, inside=True),
        complete=True,
    )
    snapshot = _build(tmp_path, monkeypatch, child, parent)
    metrics = _one_metrics(snapshot)
    assert metrics["lower_bound_only"] is True and metrics["parent_complete"] is True
    assert metrics["basis"] == "measured" and metrics["upper"] is not None
    assert MEASURED.search(_explanation(snapshot))


# --- 6. edges of the interval --------------------------------------------------------------------


def test_everything_shared_gives_upper_one_and_lower_below_one(tmp_path, monkeypatch):
    child = list(range(1000, 1020))
    snapshot = _build(tmp_path, monkeypatch, sample(child), sample(child + list(range(7000, 7050))))
    metrics = _one_metrics(snapshot)
    assert metrics["shared_tokens"] == metrics["child_distinct_sampled"] == 20
    low, _ = wilson(20, 20, 0.95)
    assert metrics["basis"] == "measured" and metrics["estimate"] == pytest.approx(1.0)
    assert metrics["upper"] == pytest.approx(1.0, abs=1e-6)
    assert metrics["lower"] < 1 and metrics["lower"] == pytest.approx(low, abs=1e-6)
    found = MEASURED.search(_explanation(snapshot))
    assert found and float(found["x"]) == 100 and float(found["b"]) == 100
    assert float(found["a"]) < 100


def test_everything_shared_with_a_partly_read_parent_is_at_least_100(tmp_path, monkeypatch):
    child = list(range(1000, 1020))
    parent = sample(child + list(range(7000, 7050)), complete=False)
    snapshot = _build(tmp_path, monkeypatch, sample(child), parent)
    metrics = _one_metrics(snapshot)
    assert metrics["basis"] == "lower_bound" and metrics["upper"] is None
    assert metrics["estimate"] == pytest.approx(1.0)
    found = AT_LEAST.search(_explanation(snapshot))
    assert found and float(found["x"]) == 100


@pytest.mark.parametrize("shared", [1, MIN_TOKEN_SUPPORT - 1, MIN_TOKEN_SUPPORT])
def test_a_child_at_the_minimum_support_gets_a_wide_valid_interval(tmp_path, monkeypatch, shared):
    total = MIN_TOKEN_SUPPORT
    child = list(range(1000, 1000 + total))
    parent = child[:shared] + list(range(7000, 7020))
    snapshot = _build(tmp_path, monkeypatch, sample(child), sample(parent))
    metrics = _one_metrics(snapshot)
    low, high = wilson(shared, total, 0.95)
    assert metrics["child_distinct_sampled"] == total and metrics["basis"] == "measured"
    assert 0 <= metrics["lower"] <= metrics["estimate"] <= metrics["upper"] <= 1
    assert metrics["lower"] == pytest.approx(low, abs=1e-6)
    assert metrics["upper"] == pytest.approx(high, abs=1e-6)
    assert metrics["upper"] - metrics["lower"] > 0.3  # four values cannot be precise
    assert MEASURED.search(_explanation(snapshot))["n"] == str(total)


# --- controls: paths the spec leaves alone -------------------------------------------------------


def test_control_zero_against_a_partly_read_parent_keeps_its_wording(tmp_path, monkeypatch):
    snapshot = _build(
        tmp_path, monkeypatch, sample(range(900_000, 900_020)),
        sample(PARENT_30_OF_40, complete=False),
    )
    assert candidates(snapshot) == [CHILD]
    metrics = _one_metrics(snapshot)
    assert metrics["reason"] == "parent_sampled" and metrics["shared_tokens"] == 0
    text = _explanation(snapshot)
    assert "a zero is not a measurement" in text and "at least" not in text and "CI" not in text


def test_control_zero_against_a_complete_parent_is_still_dropped(tmp_path, monkeypatch):
    snapshot = _build(
        tmp_path, monkeypatch, sample(range(900_000, 900_020)), sample(PARENT_30_OF_40)
    )
    assert candidates(snapshot) == []


def test_control_unmeasured_values_keep_their_wording_and_have_no_basis(tmp_path, monkeypatch):
    snapshot = build_snapshot(
        tmp_path, monkeypatch,
        {"alpha": {"tables": {
            "gadget": {"fields": [("id", "integer")], "key": ["id"]},
            "part": {"fields": [("id", "integer"), ("gadget_id", "integer")], "key": ["id"]},
        }}},
    )
    assert candidates(snapshot) == [CHILD]
    assert containment_metrics(snapshot) == []
    text = _explanation(snapshot)
    assert "values not compared" in text and "CI" not in text and "at least" not in text


def test_the_basis_values_are_exactly_two_words_and_metrics_are_numbers(tmp_path, monkeypatch):
    for number, complete in enumerate((True, False)):
        sub = tmp_path / str(number)
        sub.mkdir()
        metrics = _one_metrics(
            _build(sub, monkeypatch, sample(CHILD_40), sample(PARENT_30_OF_40, complete=complete))
        )
        assert metrics["basis"] in {"measured", "lower_bound"}
        for key in ("estimate", "lower", "upper"):
            assert metrics[key] is None or isinstance(metrics[key], float)


# --- 5. the requester hands the basis to the model ------------------------------------------------

OLD_BODY = {"id", "kind", "status", "from", "to", "columns", "evidence", "evidence_tier"}
F11_BODY = {"from_source", "from_namespace", "to_source", "to_namespace"}
NEW_BODY = {"basis", "estimate", "lower", "upper"}
QUESTION = "How does shipment relate to vendor?"
VENDOR = list(range(1000, 1040))


def _req_sources(*, tokens: bool, complete: bool) -> dict:
    return {"alpha": {"tables": {
        "vendor": {"fields": [("id", "integer")], "key": ["id"],
                   **({"values": {"id": sample(VENDOR[:30] + list(range(7000, 7100)),
                                               complete=complete)}} if tokens else {})},
        "shipment": {"fields": [("id", "integer"), ("vendor_id", "integer")], "key": ["id"],
                     **({"values": {"vendor_id": sample(VENDOR)}} if tokens else {})},
    }}}


def _relationship_body(tmp_path, monkeypatch, **flags) -> tuple[dict, Path, Path]:
    snapshot = build_snapshot(tmp_path, monkeypatch, _req_sources(**flags))
    index_snapshot(snapshot, tmp_path / "state")
    monkeypatch.setenv("MODEL_KEY", MODEL_KEY)
    config = None
    with ModelStub(
        lambda payload: json.dumps(
            {"checked": ["x"],
                "answer": "x", "citations": [payload["items"][0]["id"]], "outcome": "answered"}
        )
    ) as stub:
        config = AnswerConfig(
            base_url=stub.url, model="m", model_digest="d", api_key_env="MODEL_KEY",
            timeout_seconds=5,
        )
        result = answer_question(tmp_path / "state", QUESTION, config)
    assert "error" not in result, result
    payload = json.loads(stub.requests[-1]["body"]["messages"][-1]["content"])
    [edge] = payload["relationships"]
    return edge, snapshot, tmp_path / "state"


def test_the_model_context_carries_the_basis_of_a_lower_bound(tmp_path, monkeypatch):
    edge, snapshot, _ = _relationship_body(tmp_path, monkeypatch, tokens=True, complete=False)
    assert set(edge) == OLD_BODY | F11_BODY | NEW_BODY, set(edge) ^ (OLD_BODY | F11_BODY | NEW_BODY)
    [metrics] = containment_metrics(snapshot)
    assert edge["basis"] == "lower_bound" == metrics["basis"]
    assert edge["estimate"] == metrics["estimate"] == pytest.approx(0.75, abs=1e-6)
    assert edge["lower"] == metrics["lower"] and edge["upper"] is None
    assert "at least" in edge["evidence"]  # the sentence is still there, unchanged in place


def test_the_model_context_carries_the_interval_of_a_measurement(tmp_path, monkeypatch):
    edge, snapshot, _ = _relationship_body(tmp_path, monkeypatch, tokens=True, complete=True)
    [metrics] = containment_metrics(snapshot)
    assert edge["basis"] == "measured"
    # unchanged from the builder, not recomputed or re-rounded
    assert (edge["estimate"], edge["lower"], edge["upper"]) == (
        metrics["estimate"], metrics["lower"], metrics["upper"]
    )
    low, high = wilson(30, 40, 0.95)
    assert edge["lower"] == pytest.approx(low, abs=1e-6)
    assert edge["upper"] == pytest.approx(high, abs=1e-6)
    assert "CI" in edge["evidence"]


def test_an_edge_without_a_containment_measurement_has_no_basis(tmp_path, monkeypatch):
    edge, _, _ = _relationship_body(tmp_path, monkeypatch, tokens=False, complete=True)
    for key in NEW_BODY:
        assert edge.get(key) is None, key


def test_the_index_version_is_bumped_and_a_version_seven_index_is_stale(tmp_path, monkeypatch):
    assert int(search_module.INDEX_VERSION) > 7
    _, _, state = _relationship_body(tmp_path, monkeypatch, tokens=True, complete=True)
    with closing(sqlite3.connect(search_module._database(state))) as connection:
        connection.execute("UPDATE meta SET value = '7' WHERE key = 'index_version'")
        connection.commit()
    monkeypatch.setenv("MODEL_KEY", MODEL_KEY)
    with ModelStub(lambda _: "{}") as stub:
        config = AnswerConfig(
            base_url=stub.url, model="m", model_digest="d", api_key_env="MODEL_KEY",
            timeout_seconds=5,
        )
        result = answer_question(state, QUESTION, config)
    assert result == {"error": {"code": "STALE_INDEX"}} and stub.requests == []


# --- plan 2.4: no corpus names, no hardcoded threshold -------------------------------------------

CORPUS = re.compile(
    r"zoominfo|diffbot|enrichment_engine|testzoominfo|companyrevenue|companyticker|"
    r"companyemployees|companyexperience|industryclassification|companyindustry",
    re.IGNORECASE,
)


def test_no_corpus_name_and_no_literal_z_or_level_in_application_source():
    sources = sorted(ROOT.glob("packages/*/src/**/*.py"))
    assert sources
    corpus, literals = [], []
    for path in sources:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            where = f"{path.relative_to(ROOT)}:{number}"
            if CORPUS.search(line):
                corpus.append(where)
            # the normal quantile for 0.95 and the level itself: both come from config
            if re.search(r"\b1\.96\b|\b1\.959", line) or (
                path.name != "config.py" and re.search(r"(?<![\d.])0\.95(?!\d)", line)
            ):
                literals.append(where)
    assert corpus == [] and literals == []


def test_the_wilson_interval_is_computed_not_tabulated():
    """The builder must derive z from the level (e.g. NormalDist), not from a lookup table."""
    text = "".join(
        p.read_text("utf-8") for p in (ROOT / "packages/catalog-builder/src").rglob("*.py")
    )
    assert "NormalDist" in text or "inv_cdf" in text, "no inverse-normal call in the builder"
