"""Attack P7-1 (F14): sentence vs metrics at the edges. Not collected by default (attack* name).

Run: uv run --python 3.12 pytest -q -p no:cacheprovider tests/phase7/attack7_p7_1.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_p7_1_overlap_basis import (  # noqa: F401 # C5: reason=imported so pytest sees its fixtures; approved=user, 2026-11-08; review=2027-05-08
    CHILD,
    _build,
    _explanation,
    _flagged_tokens,
    _level_field,
    _one_metrics,
    sample,
    wilson,
)

N = 2000  # distinct child values; the live sample is up to 25,000
CHILD_N = list(range(1000, 1000 + N))


def _parent(shared: int, pad: int = 300) -> list[int]:
    return CHILD_N[:shared] + list(range(900_000, 900_000 + pad))


def _run(tmp_path, monkeypatch, shared, complete=True, **extra):
    child, parent = sample(CHILD_N), sample(_parent(shared), complete=complete)
    snap = _build(tmp_path, monkeypatch, child, parent, builder_extra=extra or None)
    return _explanation(snap), _one_metrics(snap)


def _pcts(text):
    return [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)%", text)]


@pytest.mark.parametrize("shared", [N - 1, N - 5])
def test_measured_sentence_never_claims_100_when_a_value_is_missing(tmp_path, monkeypatch, shared):
    text, m = _run(tmp_path, monkeypatch, shared)
    assert m["basis"] == "measured" and m["estimate"] < 1
    assert "≈100%" not in text, text
    assert not re.search(r"CI (\d+)-\1%", text), text  # no degenerate "100-100%"


@pytest.mark.parametrize("shared", [N - 1])
def test_at_least_sentence_never_rounds_a_floor_up_to_100(tmp_path, monkeypatch, shared):
    text, m = _run(tmp_path, monkeypatch, shared, complete=False)
    assert m["basis"] == "lower_bound" and m["estimate"] < 1
    assert "at least 100%" not in text, text


def test_small_share_is_not_shown_as_zero(tmp_path, monkeypatch):
    text, m = _run(tmp_path, monkeypatch, 1)
    assert m["estimate"] > 0
    assert "≈0%" not in text and not re.search(r"CI 0-0%", text), text


def test_sentence_agrees_with_metrics_ordering(tmp_path, monkeypatch):
    for shared in (1, 7, 1000, N - 1, N):
        (tmp_path / str(shared)).mkdir()
        text, m = _run(tmp_path / str(shared), monkeypatch, shared)
        # Lead: "<1%" / ">99%" stand for a share strictly inside (0, 1) and (99, 100).
        g = re.search(r"≈([<>]?)(\d+)% \(95% CI (\d+)-(\d+)%\)", text)
        sign, x, a, b = g[1], *(float(v) for v in g.groups()[1:])
        x += {"<": -0.5, ">": 0.5}.get(sign, 0)
        assert a <= x <= b, (shared, text)
        lo, up = wilson(shared, N, 0.95)
        assert m["lower"] == pytest.approx(lo, abs=1e-6)
        assert m["upper"] == pytest.approx(up, abs=1e-6)


def test_extreme_levels_validated(tmp_path, monkeypatch):
    from dbig_builder.config import BuilderConfig
    field = _level_field()
    keys = {"signing_keys": {"k": "E"}}
    for ok in (0.01, 0.999, 0.9999999):
        BuilderConfig(**keys, **{field: ok})
    for bad in (0, 1, float("nan"), float("inf"), -1):
        with pytest.raises(ValidationError):
            BuilderConfig(**keys, **{field: bad})


def test_level_printed_faithfully(tmp_path, monkeypatch):
    field = _level_field()
    text, _ = _run(tmp_path, monkeypatch, 1000, **{field: 0.9999999})
    assert "(100% CI" not in text, text  # a level below 1 must not print as 100%
