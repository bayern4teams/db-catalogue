"""P6-1 + P6-2 (live finding F5): a zero overlap is a measurement only against a complete parent.

Derived from the plan (section 9, Phase 6) and `LEAD_REVIEW.md` "F5: the set recommendation", not
from the implementation.

P6-1  `ValueTokens.complete`: true when the read returned fewer rows than `sample_rows`; MongoDB
      also false when `max_sample_bytes` cut the read. A missing flag (an old bundle) is false.
P6-2  (a) zero shared drops a candidate only against a parent whose `complete` is true; otherwise
      the zero is kept as evidence and says "a zero is not a measurement" under a reason code of
      its own. (b) when either side is `truncated`, compare only hashes at or below the smaller
      of the two sides' largest kept hash (each truncated side's own, no bound for an untruncated
      one) and count support inside that range. (c) the decision follows `complete`, not
      `truncated`.

Every behaviour test is `xfail(strict=True)` while P6-1/P6-2 are unbuilt; the lead removes the
marks after implementing. The `control_*` tests pass before and after and guard against an
over-fix (a rule that never drops, or one that drops a measured non-zero).

Hash ranges are computed here with the fixture's own keyed digest, so each scenario plants an
exact number of child values inside or outside the common range; nothing depends on luck.
Names are invented and generic (plan section 2.4); the corpus-name grep over this directory is
`test_no_corpus_literals.py`, which picks this file up automatically.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import catalogue_fixture_kit as kit
import pytest
from catalogue_fixture_kit import (
    TOKEN_MATERIAL,
    build_snapshot,
    candidates,
    containment_metrics,
)
from dbig_builder.builder import MIN_TOKEN_SUPPORT
from dbig_collectors.runtime import Budget, discover_mongodb, discover_postgresql
from dbig_contracts.artifact import read_artifact
from dbig_contracts.ids import stable_id
from dbig_contracts.models import ValueTokens

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_collectors import PgConnection, PgCursor  # noqa: E402
from test_value_evidence import (  # noqa: E402
    SYNTHETIC_MATERIAL,
    _mongo_client,
    _mongo_config,
    _tokenising,
)

ROOT = Path(__file__).resolve().parents[2]
UNBUILT = pytest.mark.xfail(strict=True, reason="P6-1/P6-2 not built")

N = MIN_TOKEN_SUPPORT
CAP = 256  # the live cap; the fixture sets it per scenario, the builder must not know it
CHILD = ("part", "gadget_id", "gadget", "id")
# The reason codes that exist before P6-2. The new "parent sampled" reason must be a code of its
# own, so it may not reuse one of these.
OLD_REASONS = {"no_value_tokens", "key_ids_differ", "normalisation_differs", "sample_too_small"}

PARENT_KEYS = list(range(100_000, 103_940))  # 3,940 keys, as in the live evidence
DISJOINT_BASE = 500_000  # no overlap with PARENT_KEYS


# --- a token set with an explicit `complete` and cap --------------------------------------------


def _h(value) -> str:
    return hmac.new(TOKEN_MATERIAL.encode(), str(value).encode(), hashlib.sha256).hexdigest()


class Sample(list):
    """Values for one column plus how the read went: `complete` (None means the flag is absent,
    as in an old bundle) and `cap` (None means no cap)."""

    complete: bool | None = None
    cap: int | None = None


def sample(values, *, complete: bool | None, cap: int | None = None) -> Sample:
    made = Sample(values)
    made.complete = complete
    made.cap = cap
    return made


def _bound(values, cap: int) -> str:
    """The largest kept hash of a set capped at `cap`."""
    return sorted({_h(v) for v in values})[cap - 1]


def _pick(pool, count: int, *, bound: str, inside: bool) -> list:
    """The first `count` values of `pool` whose hash is at or below `bound` (`inside`) or above."""
    found = []
    for value in pool:
        if (_h(value) <= bound) == inside:
            found.append(value)
            if len(found) == count:
                return found
    raise AssertionError("the pool is too small for this scenario")


def _with_flags(field_id, object_id, values, *, source_id, key_id="run-1"):
    digests = sorted({_h(v) for v in values})
    cap = getattr(values, "cap", None)
    kept = digests if cap is None else digests[:cap]
    flags = {} if getattr(values, "complete", None) is None else {"complete": values.complete}
    return ValueTokens(
        id=stable_id(source_id, "tokens", field_id),
        field_id=field_id,
        object_id=object_id,
        key_id=key_id,
        sample_count=len(values),
        sample_method="first_rows_limit:10",
        observed_count=len(values),
        distinct_count=len(digests),
        truncated=len(kept) < len(digests),
        tokens=kept,
        source_id=source_id,
        observed_at=datetime.now(UTC),
        collector_version="test",
        provenance="synthetic",
        **flags,
    )


@pytest.fixture(autouse=True)
def _tokens_with_flags(monkeypatch):
    original = kit.token_set

    def token_set(field_id, object_id, values, **kwargs):
        if isinstance(values, Sample):
            return _with_flags(field_id, object_id, values, **kwargs)
        return original(field_id, object_id, values, **kwargs)

    monkeypatch.setattr(kit, "token_set", token_set)


def _snapshot(tmp_path, monkeypatch, *, child: Sample, parent: Sample, cross: bool = True):
    """Child `part.gadget_id`, parent `gadget.id` (declared key), as in test_a1_overlap."""
    gadget = {"fields": [("id", "integer"), ("title", "text")], "key": ["id"],
              "values": {"id": parent}}
    part = {"fields": [("id", "integer"), ("gadget_id", "integer")], "key": ["id"],
            "values": {"gadget_id": child}}
    sources = (
        {"alpha": {"tables": {"gadget": gadget}}, "beta": {"tables": {"part": part}}}
        if cross
        else {"alpha": {"tables": {"gadget": gadget, "part": part}}}
    )
    return build_snapshot(tmp_path, monkeypatch, sources)


def _cited_evidence(snapshot: Path) -> list[dict]:
    """Every evidence row a REFERENCE_CANDIDATE edge cites, as plain dicts."""
    _, data = read_artifact(snapshot)
    rows = {item.id: item.model_dump(mode="json") for item in data["evidence.jsonl"]}
    cited = []
    for edge in data["edges.jsonl"]:
        if edge.kind == "REFERENCE_CANDIDATE":
            cited.extend(rows[key] for key in edge.evidence_ids if key in rows)
    return cited


def _text(snapshot: Path) -> str:
    return json.dumps(_cited_evidence(snapshot), sort_keys=True).lower()


def _live_shaped_parent(*, complete: bool | None, cap: int | None = CAP) -> Sample:
    return sample(PARENT_KEYS, complete=complete, cap=cap)


def _bound_of_capped_parent() -> str:
    return _bound(PARENT_KEYS, CAP)


# --- P6-1 contract: the field exists, is additive, defaults to false ----------------------------


@UNBUILT
def test_value_tokens_has_a_complete_flag_that_defaults_to_false():
    """Spec: `ValueTokens` gains `complete: bool`; an old bundle without it counts as false."""
    made = ValueTokens(
        id="t1", source_id="s", observed_at=datetime.now(UTC), collector_version="test",
        provenance="synthetic", field_id="f", object_id="o", key_id="run-1", sample_count=1,
        sample_method="first_rows_limit:1", observed_count=1, distinct_count=1, tokens=["a" * 64],
    )
    assert "complete" in ValueTokens.model_fields
    assert made.complete is False


@UNBUILT
def test_the_published_schema_lists_complete_as_an_optional_property():
    """Spec: additive; schemas regenerated. An old reader must still validate a row without it."""
    schema = json.loads(
        (ROOT / "schemas" / "ir-v1" / "value_tokens.schema.json").read_text(encoding="utf-8")
    )
    assert "complete" in schema["properties"]
    assert "complete" not in schema["required"]


# --- P6-1 collector: PostgreSQL ------------------------------------------------------------------


def _pg_entry(monkeypatch, rows: int, **config):
    monkeypatch.setenv("DBIG_TOKENISER", SYNTHETIC_MATERIAL)

    class Rows(PgCursor):
        sample = [(value,) for value in range(rows)]

    connection = PgConnection()
    connection.fake_cursor = Rows()
    return discover_postgresql(connection, _tokenising(**config), Budget(50))["tokens.jsonl"][0]


@UNBUILT
def test_pg_fewer_rows_than_sample_rows_is_complete(monkeypatch):
    assert _pg_entry(monkeypatch, 3, sample_rows=10).complete is True


@UNBUILT
def test_pg_exactly_sample_rows_is_not_known_complete(monkeypatch):
    """`fewer than` is strict: a read that fills the limit may have stopped early."""
    assert _pg_entry(monkeypatch, 5, sample_rows=5).complete is False


@UNBUILT
def test_pg_more_rows_than_sample_rows_is_not_complete(monkeypatch):
    assert _pg_entry(monkeypatch, 9, sample_rows=5).complete is False


@UNBUILT
def test_pg_complete_is_independent_of_the_token_cap(monkeypatch):
    """A read of 20 rows under a limit of 50 is complete even though only 5 hashes are kept."""
    entry = _pg_entry(monkeypatch, 20, sample_rows=50, max_value_tokens=5)
    assert entry.truncated is True
    assert entry.complete is True


# --- P6-1 collector: MongoDB ---------------------------------------------------------------------


def _documents(count: int) -> list[dict]:
    return [{"_id": index, "ref": str(index)} for index in range(1, count + 1)]


def _mongo_entries(monkeypatch, documents, **config):
    monkeypatch.setenv("DBIG_TOKENISER", SYNTHETIC_MATERIAL)
    entries = discover_mongodb(
        _mongo_client(documents), _mongo_config(**config), Budget(50)
    )["tokens.jsonl"]
    assert entries, "the scenario must produce token sets"
    return entries


def _bytes(documents) -> int:
    return sum(len(str(document).encode("utf-8")) for document in documents)


@UNBUILT
def test_mongo_fewer_documents_than_sample_rows_is_complete(monkeypatch):
    entries = _mongo_entries(monkeypatch, _documents(3), sample_rows=10)
    assert [entry.complete for entry in entries] == [True] * len(entries)


@UNBUILT
def test_mongo_a_read_that_fills_sample_rows_is_not_complete(monkeypatch):
    entries = _mongo_entries(monkeypatch, _documents(5), sample_rows=5)
    assert [entry.complete for entry in entries] == [False] * len(entries)


@UNBUILT
def test_mongo_stopped_by_max_sample_bytes_is_not_complete(monkeypatch):
    """Three documents, room for two: fewer than `sample_rows`, yet the read was cut."""
    documents = _documents(3)
    entries = _mongo_entries(
        monkeypatch, documents, sample_rows=10, max_sample_bytes=_bytes(documents[:2])
    )
    assert {entry.sample_count for entry in entries} == {2}, "the byte budget did cut the read"
    assert [entry.complete for entry in entries] == [False] * len(entries)


@UNBUILT
def test_mongo_a_byte_budget_that_just_fits_is_complete(monkeypatch):
    """Control for the cut test: the same documents with room for all three are complete, so the
    flag follows the cut and not merely the presence of a byte limit."""
    documents = _documents(3)
    entries = _mongo_entries(
        monkeypatch, documents, sample_rows=10, max_sample_bytes=_bytes(documents)
    )
    assert {entry.sample_count for entry in entries} == {3}
    assert [entry.complete for entry in entries] == [True] * len(entries)


# --- P6-2 (a): a zero against a parent that is not complete is not a measurement -----------------

INCOMPLETE_PARENTS = {
    # The live case: a 3,940-key parent whose read stopped at 100 rows. The child's 17 values are
    # true references that simply were not among those 100 rows.
    "read-as-100-rows": lambda: sample(PARENT_KEYS[:100], complete=False),
    "incomplete-and-capped": lambda: _live_shaped_parent(complete=False),
    # An old bundle: no flag at all, which counts as false.
    "flag-absent-old-bundle": lambda: sample(PARENT_KEYS[:100], complete=None),
}


@pytest.mark.parametrize("make_parent", INCOMPLETE_PARENTS.values(), ids=INCOMPLETE_PARENTS)
@UNBUILT
def test_a_true_child_against_a_sampled_parent_is_not_dropped_and_says_why(
    tmp_path, monkeypatch, make_parent
):
    child = sample(PARENT_KEYS[2000:2017], complete=True)  # 17 true values, none in the sample
    snapshot = _snapshot(tmp_path, monkeypatch, child=child, parent=make_parent())
    assert candidates(snapshot) == [CHILD], "a zero against a sampled parent must not drop"
    zeros = [m for m in containment_metrics(snapshot) if m["shared_tokens"] == 0]
    assert zeros, "the evidence keeps the zero"
    assert "a zero is not a measurement" in _text(snapshot)


@UNBUILT
def test_the_sampled_parent_reason_is_a_code_of_its_own(tmp_path, monkeypatch):
    """Spec: 'under its own reason code'. Asserted as: some cited evidence carries a `reason`
    that none of the pre-existing codes use. The code's spelling is not prescribed."""
    snapshot = _snapshot(
        tmp_path, monkeypatch,
        child=sample(PARENT_KEYS[2000:2017], complete=True),
        parent=sample(PARENT_KEYS[:100], complete=False),
    )
    reasons = {
        row["metrics"]["reason"] for row in _cited_evidence(snapshot)
        if "reason" in row.get("metrics", {})
    }
    assert reasons - OLD_REASONS, f"no reason of its own among {sorted(reasons)}"


@UNBUILT
def test_a_sampled_parent_leaks_no_token_into_the_snapshot(tmp_path, monkeypatch):
    """Counts only. The new evidence must not carry a digest or a sampled value."""
    snapshot = _snapshot(
        tmp_path, monkeypatch,
        child=sample(PARENT_KEYS[2000:2017], complete=True),
        parent=sample(PARENT_KEYS[:100], complete=False),
    )
    assert CHILD in candidates(snapshot), "the scenario must reach the kept branch first"
    digests = {_h(v) for v in PARENT_KEYS[:100]} | {_h(v) for v in PARENT_KEYS[2000:2017]}
    for path in sorted(snapshot.iterdir()):
        content = path.read_text(encoding="utf-8", errors="replace")
        assert not any(digest in content for digest in digests), f"a token leaked: {path.name}"


# --- P6-2 (a)/(c): controls that pass before and after -------------------------------------------


@pytest.mark.parametrize("cross", [True, False], ids=["cross-source", "same-source"])
def test_control_a_complete_uncapped_parent_still_drops_a_measured_zero(
    tmp_path, monkeypatch, cross
):
    snapshot = _snapshot(
        tmp_path, monkeypatch, cross=cross,
        child=sample(range(DISJOINT_BASE, DISJOINT_BASE + N + 2), complete=True),
        parent=sample(PARENT_KEYS[:50], complete=True),
    )
    assert candidates(snapshot) == []


def test_control_a_measured_non_zero_against_a_sampled_parent_is_kept_with_its_number(
    tmp_path, monkeypatch
):
    snapshot = _snapshot(
        tmp_path, monkeypatch,
        child=sample(PARENT_KEYS[:8], complete=True),
        parent=sample(PARENT_KEYS[:2] + list(range(DISJOINT_BASE, DISJOINT_BASE + 12)),
                      complete=False),
    )
    assert candidates(snapshot) == [CHILD]
    (metrics,) = containment_metrics(snapshot)
    assert metrics["contained_fraction"] == 0.25 and metrics["shared_tokens"] == 2


# --- P6-2 (b): capped sets are compared inside their common hash range ---------------------------
# The parent is the live shape: 3,940 keys, complete, kept to the CAP smallest hashes. The range
# bound is therefore the parent's largest kept hash; an untruncated child adds no bound.


def _child_for_range(*, members: bool, inside: int, outside: int) -> Sample:
    """`inside` child values with a hash in the parent's range and `outside` above it. Members of
    the parent's key set when `members`, otherwise disjoint from it."""
    bound = _bound_of_capped_parent()
    pool = PARENT_KEYS if members else range(DISJOINT_BASE, DISJOINT_BASE + 200_000)
    values = _pick(pool, inside, bound=bound, inside=True)
    values += _pick(pool, outside, bound=bound, inside=False)
    return sample(values, complete=True)


@UNBUILT
def test_complete_capped_parent_drops_a_disjoint_child_when_the_range_holds_enough(
    tmp_path, monkeypatch
):
    """93 disjoint child values, `N` of them inside the parent's range. Under the old rule a
    capped parent never drops; here the range is a coordinated sample, so the zero is real."""
    snapshot = _snapshot(
        tmp_path, monkeypatch,
        child=_child_for_range(members=False, inside=N, outside=93 - N),
        parent=_live_shaped_parent(complete=True),
    )
    assert candidates(snapshot) == []


@UNBUILT
def test_complete_capped_parent_does_not_drop_when_the_range_holds_too_few(
    tmp_path, monkeypatch
):
    """The same, with `N - 1` child values inside the range: too thin to be evidence, so the
    candidate stays and says the overlap was not measured."""
    snapshot = _snapshot(
        tmp_path, monkeypatch,
        child=_child_for_range(members=False, inside=N - 1, outside=93 - (N - 1)),
        parent=_live_shaped_parent(complete=True),
    )
    assert candidates(snapshot) == [CHILD]
    assert "not measured" in _text(snapshot)


@UNBUILT
def test_complete_capped_parent_keeps_a_true_child_with_a_number(tmp_path, monkeypatch):
    """93 true child values, 10 inside the range. Inside the range every one is in the parent's
    kept hashes, so the number is 1.0 over 10; a count over all 93 child values would give
    about 0.11, which is what the old comparison reported."""
    snapshot = _snapshot(
        tmp_path, monkeypatch,
        child=_child_for_range(members=True, inside=10, outside=83),
        parent=_live_shaped_parent(complete=True),
    )
    assert candidates(snapshot) == [CHILD]
    (metrics,) = containment_metrics(snapshot)
    assert metrics["shared_tokens"] >= N
    assert metrics["contained_fraction"] == 1.0


@UNBUILT
def test_complete_capped_parent_keeps_a_quarter_overlap_with_its_number(tmp_path, monkeypatch):
    """Inside the range: 8 child values, 2 are parent keys, 6 are not (25%). Outside the range:
    20 non-parent values that must not dilute the number."""
    bound = _bound_of_capped_parent()
    inside_members = _pick(PARENT_KEYS, 2, bound=bound, inside=True)
    disjoint = range(DISJOINT_BASE, DISJOINT_BASE + 200_000)
    inside_others = _pick(disjoint, 6, bound=bound, inside=True)
    outside_others = _pick(disjoint, 20, bound=bound, inside=False)
    snapshot = _snapshot(
        tmp_path, monkeypatch,
        child=sample(inside_members + inside_others + outside_others, complete=True),
        parent=_live_shaped_parent(complete=True),
    )
    assert candidates(snapshot) == [CHILD], "a non-zero overlap never drops"
    (metrics,) = containment_metrics(snapshot)
    assert metrics["shared_tokens"] == 2
    assert metrics["contained_fraction"] == 0.25


@UNBUILT
def test_two_capped_sides_are_compared_up_to_the_smaller_largest_kept_hash(
    tmp_path, monkeypatch
):
    """Parent: 3,940 keys capped at CAP. Child: 3,000 of those same keys, also capped at CAP, so
    its kept hashes reach higher than the parent's. Beyond the parent's bound the child's hashes
    are not in the parent's kept set, which says nothing; inside it every one is."""
    child_keys = PARENT_KEYS[:3000]
    assert _bound(child_keys, CAP) > _bound(PARENT_KEYS, CAP), "fixture premise"
    snapshot = _snapshot(
        tmp_path, monkeypatch,
        child=sample(child_keys, complete=True, cap=CAP),
        parent=_live_shaped_parent(complete=True),
    )
    assert candidates(snapshot) == [CHILD]
    (metrics,) = containment_metrics(snapshot)
    assert metrics["shared_tokens"] >= N
    assert metrics["contained_fraction"] == 1.0


@UNBUILT
def test_two_capped_disjoint_sides_drop_when_the_common_range_holds_enough(
    tmp_path, monkeypatch
):
    child_keys = list(range(DISJOINT_BASE, DISJOINT_BASE + 3000))
    child = sample(child_keys, complete=True, cap=CAP)
    bound = min(_bound(child_keys, CAP), _bound(PARENT_KEYS, CAP))
    assert sum(_h(v) <= bound for v in child_keys) >= N, "fixture premise"
    snapshot = _snapshot(
        tmp_path, monkeypatch, child=child, parent=_live_shaped_parent(complete=True)
    )
    assert candidates(snapshot) == []


@UNBUILT
def test_a_capped_parent_that_is_not_complete_still_never_drops_a_disjoint_child(
    tmp_path, monkeypatch
):
    """(c): the decision is the parent's `complete`. Same hashes as the drop case above, but the
    parent's read was cut, so the range holds a sample of a sample."""
    snapshot = _snapshot(
        tmp_path, monkeypatch,
        child=_child_for_range(members=False, inside=N + 5, outside=93 - N - 5),
        parent=_live_shaped_parent(complete=False),
    )
    assert candidates(snapshot) == [CHILD]
    assert "a zero is not a measurement" in _text(snapshot)
