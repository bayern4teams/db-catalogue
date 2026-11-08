"""Attack P7-5 option C (F11-b): value-free format profiles. Not collected by default.

Run: systemd-run --user --scope -p MemoryMax=3G -q uv run --python 3.12 pytest -q \
  -p no:cacheprovider --basetemp=.tmp/pt tests/phase7/attack7_p7_5c.py

Every `xfail(strict=True)` is a FINDING: the property is what a correct implementation must have,
and the test fails today. When the finding is fixed the strict mark turns the test red, so the
mark must then be deleted. Invented generic names only (plan 2.4); no database is touched.
"""

from __future__ import annotations

import json
import re
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_p7_5c_format_profiles as base
from dbig_builder.builder import _format_conflict
from dbig_builder.render import render_catalog
from dbig_collectors.runtime import (
    Budget,
    BudgetExhausted,
    _format_family,
    _format_records,
    _Shape,
    _shape_token,
    discover_clickhouse,
    discover_postgresql,
)
from dbig_contracts.artifact import read_artifact
from dbig_contracts.models import ValueRanges

ROOT = Path(__file__).resolve().parents[2]
CAP = 64


# =================================================================================================
# 1. The shape token: value-free for everything
# =================================================================================================

GRAMMAR = re.compile(r"(?:[9a]\{\d+\}|[^0-9A-Za-z{}])*")


@pytest.mark.parametrize(
    "value",
    ["QK-4821", "zqxjv-canary-4471", "john.doe@example.test", "A1b2C3", "x" * 5000,
     "0123456789" * 40, "mixed Case 12 words, here!"],
)
def test_ascii_values_leave_only_runs_and_punctuation(value):
    token = _shape_token(value, CAP)
    assert GRAMMAR.fullmatch(token), token
    letters_and_digits = re.sub(r"[9a]\{\d+\}", "", token)
    assert not re.search(r"[0-9A-Za-z]", letters_and_digits)


def test_the_token_is_cut_at_the_cap_and_nothing_after_it_is_used():
    head = "AB-12" * 20  # 100 characters
    assert _shape_token(head + "SECRETTAIL", CAP) == _shape_token(head + "other-9999", CAP)
    assert _shape_token("z" * 10_000, CAP) == "a{64}"


@pytest.mark.parametrize("limit", [1, 2, 5, 64, 256])
def test_a_long_value_never_makes_a_token_longer_than_five_times_the_cap(limit):
    assert len(_shape_token("a1" * 5000, limit)) <= 5 * limit
    assert len(_shape_token("a-" * 5000, limit)) <= 5 * limit


@pytest.mark.parametrize(
    "value",
    [
        "١٢٣٤٥٦",  # Arabic-Indic digits
        "１２３４",  # fullwidth digits
        "१२३",  # Devanagari digits
        "²³¹",  # superscript digits
        "\U0001f600\U0001f4a9\U0001f680",  # emoji
        "€£¥✓✗",  # currency and check marks
    ],
    ids=["arabic-indic", "fullwidth", "devanagari", "superscript", "emoji", "symbols"],
)
def test_no_value_made_of_other_characters_is_kept_whole(value):
    token = _shape_token(value, CAP)
    assert not set(value) & set(token)


def test_unicode_letters_are_runs_not_characters():
    # CJK, Cyrillic and accented letters are letters: only a count is kept (control)
    assert _shape_token("東京都", CAP) == "a{3}"
    assert _shape_token("Москва", CAP) == "a{6}"
    assert _shape_token("café", CAP) == "a{4}"


def test_a_token_that_looks_like_a_shape_is_not_confused_with_one():
    assert _shape_token("9{3}", CAP) != _shape_token("999", CAP)
    assert _shape_token("a{3}", CAP) != _shape_token("abc", CAP)


def test_a_nul_byte_and_the_empty_string_do_not_crash():
    assert _shape_token("", CAP) == ""
    # F1 (fixed): a character outside the punctuation whitelist maps to the class char.
    assert _shape_token("\x00", CAP) == "*"


def test_the_mongo_walk_keeps_no_sampled_value_in_the_profile_ascii_canaries():
    shape = _Shape()
    canaries = ["zqxjv-canary-4471", "QK-4821-ZED", "member@example.test", "000987654321"]
    for value in canaries * 3:
        shape.add(value, CAP)
    dump = json.dumps(
        {"formats": dict(shape.formats), "lo": shape.length_min, "hi": shape.length_max}
    )
    for value in canaries:
        for chunk in re.findall(r"[A-Za-z0-9]{3,}", value):
            assert chunk not in dump, chunk


# =================================================================================================
# 2. SQL text and collection (fake drivers)
# =================================================================================================


def _eligible(tables):
    return sum(
        1 for columns in tables.values() for _, type_name in columns if _format_family(type_name)
    )


def test_every_profile_statement_is_one_read_only_select_with_no_parameter_marker():
    sqlglot = pytest.importorskip(  # C5: reason=extra; approved=user, 2026-11-08; review=2027-05-08
    "sqlglot"
)
    pg, _, _ = base.discover_pg(True)
    ch, _, _ = base.discover_ch(True)
    batches = (
        ([s for s, _ in pg.of_kind("profile")], "postgres", _eligible(base.PG_TABLES)),
        ([s for s, _, _ in ch.of_kind("profile")], "clickhouse", len(base.CH_ELIGIBLE)),
    )
    for statements, dialect, expected in batches:
        assert len(statements) == expected
        for statement in statements:
            parsed = sqlglot.parse(statement, read=dialect)
            assert len(parsed) == 1
            assert type(parsed[0]).__name__ == "Select"
            for forbidden in (";", "%", "?"):
                assert forbidden not in statement
            assert not re.search(r"\{\w+:\w+\}", statement)  # a ClickHouse query parameter
            assert not re.search(
                r"(?i)\b(insert|update|delete|drop|alter|create|grant|truncate)\b", statement
            )


@pytest.mark.parametrize("name", ["select", "user", "order", "from", "Group", "a$b", "_x", "T"])
def test_reserved_words_and_dollar_names_are_always_double_quoted(name):
    client, _, _ = base.discover_pg(True, tables={"gizmo": [(name, "text")]})
    statement = client.of_kind("profile")[0][0]
    assert f'"{name}"' in statement


def _odd_names_scan(flag):
    tables = {"Order Items": [("item id", "bigint"), ("note", "text")],
              'say"hi': [("a.b", "text")], "naïve": [("col", "text")]}
    return base.discover_pg(flag, tables=tables)


def test_control_odd_names_scan_fine_with_the_flag_off():
    _, records, _ = _odd_names_scan(False)
    assert {item.name for item in records["objects.jsonl"]} == {
        "Order Items", 'say"hi', "naïve"}


def test_odd_names_skip_their_profile_and_never_fail_the_scan():
    client, records, _ = _odd_names_scan(True)
    assert {item.name for item in records["objects.jsonl"]} == {
        "Order Items", 'say"hi', "naïve"}
    assert not any("Order" in s or "hi" in s for s, _ in client.of_kind("profile"))


def test_a_hyphenated_database_name_does_not_abort_the_clickhouse_scan():
    client = base.FakeClickHouse(base.CH_TABLES)
    config = base.profiled("clickhouse", namespaces=["my-db"])
    tables = discover_clickhouse(client, config, Budget(1000))
    assert isinstance(tables, dict) and tables


def test_clickhouse_odd_column_names_are_skipped_without_a_statement_or_a_crash():
    tables = {"linkrow": [("rowkey", "UInt64"), ("odd name", "String"), ("a.b", "String")]}
    client, _, _ = base.discover_ch(True, tables=tables)
    statements = [s for s, _, _ in client.of_kind("profile")]
    assert len(statements) == 1 and '"rowkey"' in statements[0]


def test_budget_is_charged_exactly_one_per_statement_and_nothing_when_the_flag_is_off():
    _, _, off = base.discover_pg(False)
    client, _, on = base.discover_pg(True)
    assert on.used - off.used == len(client.of_kind("profile")) == _eligible(base.PG_TABLES)
    _, _, ch_off = base.discover_ch(False)
    ch, _, ch_on = base.discover_ch(True)
    assert ch_on.used - ch_off.used == len(ch.of_kind("profile"))


def test_flag_off_issues_no_regexp_or_aggregate_text_on_any_engine():
    pg, _, _ = base.discover_pg(False)
    ch, _, _ = base.discover_ch(False)
    texts = [s for _, s, _ in pg.calls] + [s for _, s, _, _ in ch.calls]
    assert not any(
        word in text.lower()
        for text in texts
        for word in ("regexp_matches", "extractall", "format_token", "group by")
    )


def test_a_budget_reached_in_the_profile_pass_costs_profiles_not_the_catalogue():
    tables = {"gizmo": [(f"c{n}", "text") for n in range(30)]}
    _, base_records, used = base.discover_pg(False, tables=tables)
    client = base.FakePg(tables)
    records = discover_postgresql(
        client, base.profiled("postgresql"), Budget(used.used + 5)
    )
    assert len(records["fields.jsonl"]) == len(base_records["fields.jsonl"]) == 30


def test_clickhouse_budget_reached_in_the_profile_pass_costs_profiles_not_the_catalogue():
    tables = {"linkrow": [(f"c{n}", "String") for n in range(30)]}
    _, _, used = base.discover_ch(False, tables=tables)
    client = base.FakeClickHouse(tables)
    records = discover_clickhouse(client, base.profiled("clickhouse"), Budget(used.used + 5))
    assert len(records["fields.jsonl"]) == 30


def test_budget_exhausted_is_still_the_type_the_collect_wrapper_catches():
    assert issubclass(BudgetExhausted, RuntimeError)


def test_the_failure_path_keeps_the_scan_and_leaks_no_database_message(tmp_path, caplog):
    canary = "value-canary-zqxjv-4471"

    class Boom(base.FakePg):
        def cursor(self):
            cursor = super().cursor()
            original = cursor.execute

            def execute(statement, params=()):
                if base.is_profile_statement(statement):
                    raise RuntimeError(f"invalid input near {canary}")
                return original(statement, params)

            cursor.execute = execute
            return cursor

    caplog.set_level("DEBUG")
    client = Boom(base.PG_TABLES)
    records = discover_postgresql(client, base.profiled("postgresql"), Budget(1000))
    codes = [item.code for item in records["diagnostics.jsonl"]]
    assert codes.count("STATS_UNAVAILABLE") == _eligible(base.PG_TABLES)
    assert len(records["fields.jsonl"]) == sum(len(c) for c in base.PG_TABLES.values())
    base._write_bundle(tmp_path / "b", records, "postgresql")
    everything = "".join(
        path.read_text(encoding="utf-8") for path in (tmp_path / "b").iterdir() if path.is_file()
    )
    assert canary not in everything + caplog.text


def test_clickhouse_failure_keeps_the_scan_and_leaks_no_message(tmp_path, caplog):
    canary = "value-canary-zqxjv-4471"

    class Boom(base.FakeClickHouse):
        def query(self, statement, parameters=None, settings=None, **extra):
            if base.is_profile_statement(statement):
                raise RuntimeError(f"Code: 6. cannot parse {canary}")
            return super().query(statement, parameters, settings, **extra)

    caplog.set_level("DEBUG")
    records = discover_clickhouse(Boom(base.CH_TABLES), base.profiled("clickhouse"), Budget(1000))
    assert [i.code for i in records["diagnostics.jsonl"]].count("STATS_UNAVAILABLE") == len(
        base.CH_ELIGIBLE
    )
    base._write_bundle(tmp_path / "b", records, "clickhouse")
    text = "".join(p.read_text(encoding="utf-8") for p in (tmp_path / "b").iterdir() if p.is_file())
    assert canary not in text + caplog.text


@pytest.mark.parametrize(
    "type_name", ["character varying(32)[]", "varchar(8)[]", "character(3)[]", "bpchar(3)[]"]
)
def test_a_parameterised_array_type_is_not_a_text_column(type_name):
    assert _format_family(type_name) is None


@pytest.mark.parametrize(
    ("type_name", "family"),
    [("text", "text"), ("Nullable(String)", "text"), ("LowCardinality(Nullable(String))", "text"),
     ("FixedString(8)", "text"), ("character varying(32)", "text"), ("uuid", "uuid"),
     ("UUID", "uuid"), ("Int128", "integer"), ("UInt256", "integer"), ("bigserial", "integer"),
     ("text[]", None), ("Array(String)", None), ("numeric(10,2)", None), ("boolean", None),
     ("timestamp with time zone", None), ("jsonb", None), ("Float64", None),
     ("Decimal(10,2)", None), ("Enum8('a' = 1)", None), ("name", None),
     ("Map(String, UInt64)", None), ("", None)],
)
def test_format_family_controls(type_name, family):
    assert _format_family(type_name) == family


# =================================================================================================
# 3. Empty, NULL-only, NaN, 0-for-empty, huge integers
# =================================================================================================


def _rows(**given):
    row = {"value_count": 10, "length_min": 3, "length_max": 9, "length_avg": 5.5,
           "digits_share": 0.1, "hex_share": 0.2, "uuid_share": 0.0, "alpha_share": 0.6,
           "format_token": "a{3}", "format_share": 0.8, "value_min": 5, "value_max": 99}
    row.update(given)
    return list(row), [tuple(row.values())]


def _make(**given):
    names, rows = _rows(**given)
    config = types.SimpleNamespace(source_id="s")
    common = {"source_id": "s", "observed_at": base.datetime.now(base.UTC),
              "collector_version": "t", "provenance": "synthetic"}
    return _format_records(config, common, "f", "o", names, rows)


@pytest.mark.parametrize(
    "given",
    [{"value_count": 0}, {"value_count": None, "length_min": None, "length_max": None},
     {"length_min": None}, {"length_max": None}, {"length_min": -1},
     {"value_count": 0, "length_min": 0, "length_max": 0, "value_min": 0, "value_max": 0}],
    ids=["zero-count", "all-null", "no-min", "no-max", "negative", "clickhouse-zero-for-empty"],
)
def test_an_empty_or_all_null_column_gives_no_profile_and_no_range(given):
    assert _make(**given) == []


def test_nan_and_infinite_numbers_never_reach_the_record():
    nan, inf = float("nan"), float("inf")
    [profile, *_] = _make(length_avg=nan, digits_share=nan, hex_share=inf, uuid_share=-inf)
    data = profile.model_dump(mode="json")
    json.dumps(data, allow_nan=False)
    assert data["length_avg"] is None and data["digits_share"] is None
    assert data["hex_share"] is None and data["uuid_share"] is None


def test_shares_are_clamped_and_a_nan_format_share_is_dropped():
    [profile, *_] = _make(digits_share=1.0000000002, alpha_share=-1e-12, format_share=float("nan"))
    data = profile.model_dump(mode="json")
    assert data["digits_share"] == 1.0 and data["alpha_share"] == 0.0
    assert not data["formats"]


def test_ranges_are_dropped_when_inverted_fractional_or_text_and_kept_at_64_bits():
    assert [r for r in _make(value_min=9, value_max=3) if isinstance(r, ValueRanges)] == []
    assert [r for r in _make(value_min="10", value_max="9") if isinstance(r, ValueRanges)] == []
    assert [r for r in _make(value_min=1.5, value_max=3) if isinstance(r, ValueRanges)] == []
    huge = 2**64 - 1
    [*_, rng] = _make(value_min=-(2**63), value_max=huge)
    assert (rng.minimum, rng.maximum) == (-(2**63), huge)


def test_a_64_bit_range_survives_write_and_read(tmp_path):
    answers = {("gizmo", "ident"): {"value_min": -(2**63), "value_max": 2**64 - 1}}
    _, records, _ = base.discover_pg(
        True, tables={"gizmo": [("ident", "bigint")]}, answers=answers
    )
    base._write_bundle(tmp_path / "b", records, "postgresql")
    _, data = read_artifact(tmp_path / "b")
    [rng] = data["ranges.jsonl"]
    assert (rng.minimum, rng.maximum) == (-(2**63), 2**64 - 1)


def test_empty_string_column_gives_the_empty_token_and_a_zero_length_range():
    [profile] = _make(length_min=0, length_max=0, format_token="", format_share=1.0,
                      value_min=None, value_max=None)
    assert profile.length_min == 0 and profile.formats == {"": 1.0}


# =================================================================================================
# 4. The builder rule: false drops
# =================================================================================================


def _shapes(values, keep):
    shape = _Shape()
    for value in values:
        shape.add(value, CAP)
    return {t: c / shape.count for t, c in shape.formats.most_common(keep)}


def _twelve_shapes_parent():
    """A lookup column of 12 equally common shapes (lengths 2..13), as the collector keeps them."""
    return _shapes([("x" * length) for length in range(2, 14) for _ in range(10)], 8)


def test_control_the_collector_keeps_eight_of_twelve_shapes():
    formats = _twelve_shapes_parent()
    assert len(formats) == 8 and abs(sum(formats.values()) - 8 / 12) < 1e-9


def test_a_child_shape_that_is_a_minority_parent_shape_beyond_the_top_n_is_kept(
    tmp_path, monkeypatch
):
    parent = _twelve_shapes_parent()
    child = "a{13}"
    assert child not in parent  # premise: it is one of the four shapes cut off
    sources, _ = base.pair(child=base.fmt(child, 1.0), parent={"formats": parent})
    found, _ = base.kept(tmp_path, monkeypatch, sources)
    assert found, "a real link to a parent value was dropped for a truncated shape list"


def test_control_a_complete_parent_list_without_the_shape_still_drops(tmp_path, monkeypatch):
    parent = _shapes([("x" * length) for length in range(2, 6) for _ in range(10)], 8)
    assert abs(sum(parent.values()) - 1.0) < 1e-9
    sources, _ = base.pair(child=base.fmt("a{13}", 1.0), parent={"formats": parent})
    assert not base.kept(tmp_path, monkeypatch, sources)[0]


def test_the_boundary_is_exact_for_every_whole_count_up_to_2000():
    for total in range(1, 2001):
        if (95 * total) % 100:
            continue
        hits = 95 * total // 100
        conflict = _format_conflict(
            [types.SimpleNamespace(formats={"a{1}": hits / total})],
            [types.SimpleNamespace(formats={"9{1}": 1.0})],
            0.95,
        )
        assert conflict, (hits, total)


def _mongo_sample_parent():
    """First 50 of 300 string ids: the old ones are short, the later ones grew a digit."""
    documents = {
        "ledger": [
            {"_id": n, "code": f"ORD-{n:05d}" if n <= 50 else f"ORD-{100000 + n}"}
            for n in range(1, 301)
        ]
    }
    _, records, _ = base.discover_mongo(True, documents=documents, sample_rows=50)
    return base.mongo_profile(records, "code")


# F6 (fixed): a MongoDB sample carries `values_complete`, and rules (a) and (b) skip a sample.
# Lead: the parent passes on the marker exactly as the collector set it.
def test_a_mongo_sample_format_does_not_drop_a_candidate_for_a_value_it_never_read(
    tmp_path, monkeypatch
):
    parent = _mongo_sample_parent()
    assert set(parent["formats"]) == {"a{3}-9{5}"}  # premise: the sample saw one shape
    sources, _ = base.pair(
        child=base.fmt("a{3}-9{6}", 1.0),
        parent={"formats": parent["formats"], "values_complete": parent["values_complete"]},
    )
    assert base.kept(tmp_path, monkeypatch, sources, name="fmt")[0]


def test_a_mongo_sample_length_range_does_not_drop_a_candidate(tmp_path, monkeypatch):
    parent = _mongo_sample_parent()
    assert (parent["length_min"], parent["length_max"]) == (9, 9)
    sources, _ = base.pair(
        child={"length_min": 10, "length_max": 10},
        parent={"length_min": parent["length_min"], "length_max": parent["length_max"],
                "values_complete": parent["values_complete"]},
    )
    assert base.kept(tmp_path, monkeypatch, sources, name="len")[0]


def test_a_child_that_is_mostly_a_placeholder_is_dropped_by_the_row_weighted_rule(
    tmp_path, monkeypatch
):
    """By design (DEC-42 counts values, not distinct values): 96 percent of rows hold the empty
    string for 'no reference', 4 percent hold real keys. Recorded as a known limit."""
    child = base.fmt("", 0.96, ("9{6}", 0.04))
    sources, _ = base.pair(child=child, parent=base.fmt("9{6}", 1.0))
    assert not base.kept(tmp_path, monkeypatch, sources)[0]


def test_owner_confirmed_pair_dropped_by_the_format_rule_is_counted_unmatched(
    tmp_path, monkeypatch
):
    path = tmp_path / "d.json"
    path.write_text(
        json.dumps([{"child": "alpha.shipment.gadget_id", "parent": "beta.gadget.id",
                     "decision": "confirmed"}]),
        encoding="utf-8",
    )
    sources, _ = base.pair(child=base.fmt("9{8}", 1.0), parent=base.fmt("aaa-9{4}", 1.0))
    found, snap = base.kept(tmp_path, monkeypatch, sources, decisions_file=path)
    run = base.kit.builder_run(snap)
    # same treatment as the measured-zero drops of P7-5 B; reported, not a defect of this item
    assert not found and run.get("unmatched_decisions") == 1


def test_declared_foreign_key_survives_every_rule_at_once(tmp_path, monkeypatch):
    sources, ranges = base.pair(
        cross=False,
        declared=True,
        child={"length_min": 8, "length_max": 8, "formats": {"9{8}": 1.0}},
        parent={"length_min": 36, "length_max": 36, "formats": {"a{36}": 1.0}},
        child_range=(1, 5),
        parent_range=(100, 200),
    )
    snap = base.snapshot_of(tmp_path, monkeypatch, sources, ranges)
    assert base.new_drops(snap) == {}


# =================================================================================================
# 5. Privacy end to end: ranges only in ranges.jsonl
# =================================================================================================


def test_ranges_reach_no_snapshot_file_log_page_or_stream(tmp_path, monkeypatch, caplog, capsys):
    low, high = 41230987, 58120934
    caplog.set_level("DEBUG")
    sources, ranges = base.pair(
        kind="bigint", child_range=(low, low + 7), parent_range=(high, high + 9),
    )
    snap = base.snapshot_of(tmp_path, monkeypatch, sources, ranges)
    page = tmp_path / "page.html"
    render_catalog(snap, [], page, None)
    seen = caplog.text + "".join(capsys.readouterr())
    for path in [*snap.iterdir(), page]:
        seen += path.read_text(encoding="utf-8")
    for canary in (str(low), str(high), str(low + 7), str(high + 9)):
        assert canary not in seen, canary
    bundle_text = "".join(
        p.read_text(encoding="utf-8")
        for b in tmp_path.glob("bundle-*")
        for p in b.iterdir()
        if p.is_file()
    )
    assert str(low) in bundle_text  # control: the bounds really are in the local bundle
    assert base.new_drops(snap) == {"dropped_format_mismatch_candidates": 1}


def test_ranges_are_in_no_downstream_package():
    for package in ("requester", "planner"):
        for path in (ROOT / "packages" / package / "src").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert "ranges.jsonl" not in text and "ValueRanges" not in text, path


def test_gitignore_ignores_ranges_wherever_tokens_are_ignored():
    lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    tokens = {line.replace("tokens.jsonl", "") for line in lines if line.endswith("tokens.jsonl")}
    ranges = {line.replace("ranges.jsonl", "") for line in lines if line.endswith("ranges.jsonl")}
    assert tokens and tokens == ranges


# =================================================================================================
# 6. Old bundles, other versions
# =================================================================================================


def test_a_profile_row_written_before_the_new_fields_still_validates():
    from dbig_contracts.models import Profile

    old = {"record_type": "profile", "id": "p", "field_id": "f", "object_id": "o",
           "method": "source_statistic", "source_id": "s", "provenance": "synthetic",
           "observed_at": base.datetime.now(base.UTC).isoformat(), "collector_version": "t",
           "null_fraction": 0.1}
    profile = Profile.model_validate(old)
    assert profile.formats is None and profile.length_min is None


def test_the_golden_collector_bundle_without_ranges_is_still_read():
    golden = ROOT / "tests" / "golden" / "ir-v1" / "collector-bundle"
    manifest = json.loads((golden / "manifest.json").read_text(encoding="utf-8"))
    assert "ranges.jsonl" not in [entry["path"] for entry in manifest["files"]]
    _, data = read_artifact(golden, signing_key=b"golden-fixture-key-not-a-credential")
    assert "ranges.jsonl" not in data


def test_a_bundle_with_the_flag_off_does_not_list_ranges(tmp_path):
    # Lead (F4 fix): `ranges.jsonl` is written only when it has rows, so a bundle from a source
    # with the flag off keeps the file set an older builder reads.
    _, records, _ = base.discover_pg(False)
    base._write_bundle(tmp_path / "b", records, "postgresql")
    manifest = json.loads((tmp_path / "b" / "manifest.json").read_text(encoding="utf-8"))
    assert "ranges.jsonl" not in {item["path"] for item in manifest["files"]}


# =================================================================================================
# 7. Plan 2.4 and package isolation
# =================================================================================================


def test_no_corpus_name_in_the_packages():
    names = re.compile(r"(?i)\b(pagila|chinook|tpc-?h|sakila|northwind|adventureworks)\b")
    for path in (ROOT / "packages").glob("*/src/**/*.py"):
        assert not names.search(path.read_text(encoding="utf-8")), path


def test_builder_and_collectors_do_not_import_each_other():
    for path in (ROOT / "packages" / "catalog-builder" / "src").rglob("*.py"):
        assert "dbig_collectors" not in path.read_text(encoding="utf-8"), path
    for path in (ROOT / "packages" / "collectors" / "src").rglob("*.py"):
        assert "dbig_builder" not in path.read_text(encoding="utf-8"), path
