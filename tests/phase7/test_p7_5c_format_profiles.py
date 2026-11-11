"""P7-5 option C (F11-b): value-free format profiles, numeric ranges kept local, builder rule.

Derived from `docs/plans/PHASE7_LOCAL_PLAN.md` section 4 "P7-5" option C and section 2, the F11-b
paragraph of `artifacts/run-20261020-pgch/research-f14-f17.md`, and owner decisions DEC-40, DEC-42
and DEC-43 in `docs/plans/phase7/HANDOFF.md`, not from the implementation. Invented generic names
only (plan 2.4). No database is touched: the drivers are fakes defined below.

Spec as checked here (the pinned names are the assumptions listed in `.handoff/P7-5C.acceptance.md`;
the lead may overrule any of them by editing the constants at the top of this file).

Collection, per source config flag `format_profiles` (default false):
* Off: nothing changes. No statement, no extra budget charge, no new field, no new file content.
* On, PostgreSQL and ClickHouse: ONE aggregate statement per column of the families the builder can
  link (text, integer, uuid), over the whole column (no row limit from `sample_rows`, no sampling
  clause), read-only, identifiers quoted as the collectors quote them, charged to the budget.
  Its output columns are aliased with the names in `ALIASES`, so the tests can answer by name.
  Numeric `value_min`/`value_max` exist for integer columns only; a text or uuid column never gets
  a min/max of its raw value.
* On, MongoDB: the same profile from the documents already sampled: no extra find, no extra
  budget charge, nothing when `sample_rows` is 0.
* The profile (in `profiles.jsonl`) holds `length_min`, `length_max`, `length_avg`, `digits_share`,
  `hex_share`, `uuid_share`, `alpha_share` and `formats` ({format token: share of values}); all
  value-free, all plain JSON numbers once written. A format token maps digits to `9` and letters
  to `a`, keeps punctuation, and may run-length compress; the exact grammar is the worker's, the
  properties are checked.
* Integer columns only: `value_min` and `value_max` go to the local-only bundle file
  `ranges.jsonl` (model `ValueRanges`: `field_id`, `object_id`, `minimum`, `maximum`, exact
  integers), a collector-bundle file like `tokens.jsonl`. It is never in the snapshot, a log or an
  error, and `.gitignore` treats it like `tokens.jsonl`.

Builder (`[builder] format_reject_share`, default 0.95, in (0, 1]; DEC-42). A REFERENCE_CANDIDATE
is dropped, before the top-k cut, and counted under its own `dropped_*` diagnostics key, when
* (a) the child's dominant format (largest share in `formats`) has share >= `format_reject_share`
  and that token is absent from the parent's `formats`; or
* (b) both are text columns with length ranges [length_min, length_max] that do not overlap
  (touching at one length overlaps); or
* (c) both have a `ranges.jsonl` row and the integer ranges do not overlap (touching overlaps).
A missing profile (or missing part of it) on either side: that rule does not apply. A declared FK
is never dropped. A kept candidate with both ranges carries a boolean `numeric_ranges_overlap`
(true) in the snapshot; the numbers themselves never reach the snapshot.
"""

# ruff: noqa: S603, S607, S106 # C5: reason=adversarial test file kept as its author wrote it; approved=user, 2026-11-08; review=2027-05-08
from __future__ import annotations

import ast
import json
import logging
import re
import subprocess
import sys
import types
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "catalogue_fixes"))

import catalogue_fixture_kit as kit
import pytest
from catalogue_fixture_kit import BUNDLE_KEY, candidate_text, candidates
from dbig_builder.builder import assemble
from dbig_builder.config import BuilderConfig
from dbig_builder.config import load_config as load_builder_config
from dbig_collectors.config import SourceConfig
from dbig_collectors.config import load_config as load_source_config
from dbig_collectors.runtime import (
    Budget,
    BudgetExhausted,
    collect,
    discover_clickhouse,
    discover_mongodb,
    discover_postgresql,
)
from dbig_contracts.artifact import (
    COLLECTOR_FILES,
    SNAPSHOT_FILES,
    read_artifact,
    write_artifact,
)
from dbig_contracts.errors import OperatorError
from dbig_contracts.ids import stable_id
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "packages"

# --- the pinned names (assumptions the lead may overrule) ----------------------------------------

FLAG = "format_profiles"
THRESHOLD = "format_reject_share"
RANGES_FILE = "ranges.jsonl"
# Output aliases of the one aggregate statement per column. A statement that carries any of them
# is a profile statement; the fake answers each alias by name, in select-list order.
ALIASES = (
    "length_min", "length_max", "length_avg", "digits_share", "hex_share", "uuid_share",
    "alpha_share", "format_token", "format_share", "value_min", "value_max",
)
REQUIRED_TEXT = ALIASES[:9]
PROFILE_FIELDS = (
    "length_min", "length_max", "length_avg", "digits_share", "hex_share", "uuid_share",
    "alpha_share", "formats",
)
EXISTING_DROP_KEYS = {
    "dropped_zero_overlap_candidates",
    "dropped_expected_overlap_candidates",
    "dropped_ordinary_name_candidates",
}

# Non-default values everywhere, so a constant typed into the implementation cannot match by luck.
SAMPLE_ROWS = 7
TIMEOUT = 9
MIN_CANARY = 41230987  # a real column minimum: must stay in the local-only file
MAX_CANARY = 58120934  # a real column maximum
CANARIES = (str(MIN_CANARY), str(MAX_CANARY))
TEXT_CANARY = "zqxjv-canary-4471"


# --- reading a statement: the aliases of its outermost select list -------------------------------


def _mask(text: str) -> tuple[str, list[int]]:
    """The statement with quoted spans blanked, and the parenthesis depth of each character."""
    masked, depths, depth, quote, index = [], [], 0, None, 0
    while index < len(text):
        char = text[index]
        if quote:
            if char == quote:
                if text[index + 1 : index + 2] == quote:  # a doubled quote stays inside
                    masked.append("xx")
                    depths.extend([depth, depth])
                    index += 2
                    continue
                quote = None
            masked.append("x")
        elif char in "'\"`":
            quote = char
            masked.append("x")
        else:
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            masked.append(char)
        depths.append(depth)
        index += 1
    return "".join(masked), depths


def select_list(statement: str) -> list[str]:
    """The elements of the first depth-0 SELECT list (what a CTE or subquery leaves outermost)."""
    text = " ".join(statement.split())
    masked, depths = _mask(text)
    start = next(
        (m.end() for m in re.finditer(r"\bselect\b", masked, re.I) if depths[m.start()] == 0), None
    )
    if start is None:
        return []
    end = next(
        (m.start() for m in re.finditer(r"\bfrom\b", masked[start:], re.I)
         if depths[start + m.start()] == 0),
        None,
    )
    stop = len(text) if end is None else start + end
    elements, begin = [], start
    for position in range(start, stop):
        if masked[position] == "," and depths[position] == 0:
            elements.append(text[begin:position].strip())
            begin = position + 1
    elements.append(text[begin:stop].strip())
    return elements


def aliases_of(statement: str) -> list[str]:
    found = []
    for element in select_list(statement):
        match = re.search(r'\bAS\s+["`]?([A-Za-z_]\w*)["`]?\s*$', element, re.I)
        found.append(match.group(1).lower() if match else "")
    return found


def is_profile_statement(statement: str) -> bool:
    return bool(set(aliases_of(statement)) & set(ALIASES))


# --- fakes ---------------------------------------------------------------------------------------

PROFILE = {
    "length_min": 3, "length_max": 9, "length_avg": 5.5, "digits_share": 0.1, "hex_share": 0.2,
    "uuid_share": 0.0, "alpha_share": 0.6, "format_token": "aaa-9{4}", "format_share": 0.8,
}
INTEGER_TYPES = {"integer", "bigint", "smallint", "uint64", "int32", "int64", "uint8"}


def is_integer(type_name: str) -> bool:
    inner = re.sub(r"(?i)(nullable|lowcardinality)\(", "", type_name).rstrip(")")
    return inner.split("(")[0].strip().casefold() in INTEGER_TYPES


def answer_row(table, column, type_name, statement, answers):
    """One result row for a profile statement: each alias answered by name, None if unknown."""
    given = dict(PROFILE)
    if is_integer(type_name):
        given.update(value_min=MIN_CANARY, value_max=MAX_CANARY)
    given.update(answers.get((table, column), {}))
    names = aliases_of(statement)
    return names, tuple(given.get(name) for name in names)


def target_of(tables, statement):
    for table, columns in tables.items():
        if f'"{table}"' in statement:
            hit = [(c, t) for c, t in columns if f'"{c}"' in statement]
            assert len(hit) == 1, f"a profile statement must name exactly one column: {statement!r}"
            return table, hit[0][0], hit[0][1]
    raise AssertionError(f"profile statement names no known table: {statement!r}")


class FakePg:
    """A PostgreSQL connection answering catalogs from `tables` and profiles by alias.

    `tables`: {name: [(column, type), ...]}. `calls`: (kind, statement, params) with kind one of
    setting, catalog, sample, profile.
    """

    def __init__(self, tables, answers=None):
        self.tables = tables
        self.answers = answers or {}
        self.calls = []

    def cursor(self):
        return _PgCursor(self)

    def rollback(self):
        pass

    def close(self):
        pass

    def of_kind(self, kind):
        return [(s, p) for k, s, p in self.calls if k == kind]


class _PgCursor:
    def __init__(self, owner):
        self.owner = owner
        self.rows = []
        self.description = None

    def execute(self, statement, params=()):
        owner, low = self.owner, " ".join(statement.split()).lower()
        self.description = None
        kind, rows = "catalog", []
        if is_profile_statement(statement):
            kind = "profile"
            names, row = answer_row(*target_of(owner.tables, statement), statement, owner.answers)
            self.description = [(name, None, None, None, None, None, None) for name in names]
            rows = [row]
        elif low.startswith("set transaction") or "set_config" in low:
            kind = "setting"
        elif "pg_stats" in low:
            rows = []
        elif "pg_catalog.pg_class" in low and "join" in low:
            rows = [
                (16384 + n, "public", name, "r", 10.0, False, None, None)
                for n, name in enumerate(owner.tables)
            ]
        elif "pg_attribute" in low:
            rows = [
                (16384 + n, ordinal, column, type_name, False, None)
                for n, columns in enumerate(owner.tables.values())
                for ordinal, (column, type_name) in enumerate(columns, start=1)
            ]
        elif low.startswith('select "'):
            kind = "sample"
        owner.calls.append((kind, statement, params))
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def close(self):
        pass


class FakeClickHouse:
    """A ClickHouse client; `tables`: {name: [(column, type), ...]} in database `public`."""

    def __init__(self, tables, answers=None):
        self.tables = tables
        self.answers = answers or {}
        self.calls = []  # (kind, statement, parameters, settings)

    def of_kind(self, kind):
        return [(s, p, st) for k, s, p, st in self.calls if k == kind]

    def query(self, statement, parameters=None, settings=None, **extra):
        assert not extra, f"unexpected client arguments: {sorted(extra)}"
        kind, rows, names = "catalog", [], None
        if is_profile_statement(statement):
            kind = "profile"
            names, row = answer_row(*target_of(self.tables, statement), statement, self.answers)
            rows = [row]
        elif "system.tables" in statement:
            rows = [
                (name, f"uuid-{name}", "MergeTree", "", "", "", [], [], 10, "")
                for name in sorted(self.tables)
            ]
        elif "system.columns" in statement:
            rows = [
                (name, column, type_name, position, 0, "")
                for name, columns in sorted(self.tables.items())
                for position, (column, type_name) in enumerate(columns, start=1)
            ]
        elif "system.databases" in statement:
            rows = [("public",)]
        elif statement.lstrip().upper().startswith("SELECT"):
            kind = "sample"
        self.calls.append((kind, statement, parameters or {}, settings or {}))

        class Result:
            result_set = rows
            column_names = tuple(names or ())

        return Result()


class FakeMongo:
    """`documents`: {collection: [doc, ...]}; the client, database and collection in one object."""

    def __init__(self, documents, finds=None, current=None):
        self.documents = documents
        self.finds = [] if finds is None else finds
        self.current = current

    def __getitem__(self, name):
        return FakeMongo(self.documents, self.finds, name if name in self.documents else None)

    def list_collections(self):
        return [
            {"name": name, "type": "collection", "info": {"uuid": f"uuid-{name}"}}
            for name in self.documents
        ]

    def list_indexes(self):
        return [{"name": "_id_", "unique": True, "key": {"_id": 1}}]

    def estimated_document_count(self, **kwargs):
        return len(self.documents[self.current])

    def find(self, query):
        self.finds.append(query)
        documents = self.documents[self.current]

        class Cursor:
            limit_value = None

            def limit(self, count):
                self.limit_value = count
                return self

            def max_time_ms(self, timeout):
                return self

            def __iter__(self):
                chosen = documents if self.limit_value is None else documents[: self.limit_value]
                return iter(list(chosen))

            def close(self):
                pass

        return Cursor()


def source(kind, **overrides):
    return SourceConfig.model_validate(
        {
            "source_id": "site-b", "kind": kind, "connection_env": "DBIG_CONNECTION",
            "signing_key_env": "DBIG_SIGNING_KEY", "signing_key_id": "test",
            "namespaces": ["public"], "read_timeout_seconds": TIMEOUT, **overrides,
        }
    )


def profiled(kind, **overrides):
    return source(kind, **{FLAG: True, **overrides})


# tables for the SQL engines: (column, type). Eligible columns are the text, integer and uuid ones.
PG_TABLES = {
    "gizmo": [
        ("ident", "bigint"), ("label", "text"), ("code", "character varying(32)"),
        ("token_uuid", "uuid"), ("flag", "boolean"), ("seen_at", "timestamp with time zone"),
        ("price", "numeric(10,2)"), ("meta", "jsonb"),
    ],
    "sprocket": [("serial_no", "integer"), ("note", "text")],
}
PG_ELIGIBLE = {
    ("gizmo", "ident"), ("gizmo", "label"), ("gizmo", "code"), ("gizmo", "token_uuid"),
    ("sprocket", "serial_no"), ("sprocket", "note"),
}
CH_TABLES = {
    "linkrow": [
        ("rowkey", "UInt64"), ("label", "Nullable(String)"), ("code", "LowCardinality(String)"),
        ("tag", "LowCardinality(Nullable(String))"), ("ref", "UUID"), ("seen_at", "DateTime"),
        ("ratio", "Float64"), ("flag", "Bool"), ("tags", "Array(String)"),
        ("attrs", "Map(String, UInt64)"),
    ],
    "shipment": [("count_no", "Int32"), ("fixed", "FixedString(8)")],
}
CH_ELIGIBLE = {
    ("linkrow", "rowkey"), ("linkrow", "label"), ("linkrow", "code"), ("linkrow", "tag"),
    ("linkrow", "ref"), ("shipment", "count_no"), ("shipment", "fixed"),
}


def targets(tables, statements):
    """The (table, column) each profile statement names."""
    return [target_of(tables, statement)[:2] for statement in statements]


def column_of(records, field_id):
    fields = {item.id: item for item in records["fields.jsonl"]}
    objects = {item.id: item.name for item in records["objects.jsonl"]}
    field = fields[field_id]
    return objects[field.object_id], field.name


def profiles_by_column(records):
    return {
        column_of(records, item.model_dump()["field_id"]): item.model_dump()
        for item in records["profiles.jsonl"]
        if item.model_dump().get("length_min") is not None
    }


@pytest.mark.parametrize(
    ("statement", "expected"),
    [
        ('SELECT min(length("c")) AS length_min, avg(x)::float AS length_avg FROM "s"."t"',
         ["length_min", "length_avg"]),
        ("WITH f AS (SELECT a AS inner_alias FROM t) SELECT count(*) FILTER (WHERE c ~ '^[0-9,]+$')"
         ' AS digits_share, (SELECT x FROM f ORDER BY 1 LIMIT 1) AS "format_token" FROM f',
         ["digits_share", "format_token"]),
        ("SELECT 1", [""]),
    ],
)
def test_control_the_alias_reader_used_by_the_fakes(statement, expected):
    assert aliases_of(statement) == expected


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    monkeypatch.setenv("DBIG_TOKENISER", kit.TOKEN_MATERIAL)
    monkeypatch.setenv("BUNDLE_KEY", BUNDLE_KEY)


# =================================================================================================
# 1. Configuration and the scan capability
# =================================================================================================


def test_the_flag_is_off_by_default():
    assert getattr(source("postgresql"), FLAG, "absent") is False, (
        f"SourceConfig.{FLAG} must exist, default off"
    )


def test_the_flag_loads_from_toml_and_from_the_environment(tmp_path, monkeypatch):
    path = tmp_path / "source.toml"
    base = (
        "[source]\n"
        'source_id = "site-b"\nkind = "postgresql"\nconnection_env = "DBIG_CONNECTION"\n'
        'signing_key_env = "DBIG_SIGNING_KEY"\nsigning_key_id = "test"\n'
        'namespaces = ["public"]\n'
    )
    path.write_text(base + f"{FLAG} = true\n", encoding="utf-8")
    assert getattr(load_source_config(path), FLAG, None) is True
    path.write_text(base, encoding="utf-8")
    monkeypatch.setenv("DBIG_" + FLAG.upper(), "true")
    assert getattr(load_source_config(path), FLAG, None) is True


def test_a_scan_runs_the_profiles_only_when_it_asked_for_the_sample_capability(
    tmp_path, monkeypatch
):
    """The aggregates read data, so they follow the `sample` capability like the probe does."""
    from dbig_collectors.scan import accept_scan, process_next
    from dbig_contracts.scan import ScanRequest, sign_scan

    key = "synthetic-key-with-more-than-thirty-two-bytes"
    monkeypatch.setenv("DBIG_SIGNING_KEY", key)
    seen = []
    monkeypatch.setattr("dbig_collectors.scan.collect", lambda cfg, path: seen.append(cfg))
    base = profiled("postgresql", authorized_requesters=["catalogue"])
    queue = tmp_path / "queue.sqlite3"
    for index, capabilities in enumerate((["metadata"], ["metadata", "sample"])):
        now = datetime.now(UTC)
        request = ScanRequest(
            id=f"request-{index}", source_id="site-b", scopes=["public"],
            capabilities=capabilities, reason="refresh", requester_id="catalogue",
            idempotency_key=f"idem-{index}", issued_at=now - timedelta(seconds=1),
            expires_at=now + timedelta(minutes=10), reply_location="local-receipt",
        )
        # With only this flag on (no sample_rows, no probe), `sample` is still grantable.
        accept_scan(sign_scan(request, "test", key.encode()), base, queue)
        process_next(base, queue, tmp_path / "out")
    assert [getattr(cfg, FLAG) for cfg in seen] == [False, True]


# =================================================================================================
# 2. PostgreSQL: one whole-column aggregate per eligible column
# =================================================================================================



def _write_bundle(path, records, kind):
    """Lead: a bundle needs the one Source row that `collect()` adds after discovery."""
    from dbig_collectors.runtime import _common
    from dbig_contracts.models import Source

    source_id = next(item.source_id for rows in records.values() for item in rows)
    config = type("C", (), {"source_id": source_id})()
    source = Source(id=source_id, kind=kind, name=source_id, transport="plaintext-loopback",
                    **_common(config))
    write_artifact(path, "collector-bundle", {**records, "sources.jsonl": [source]},
                   producer="test", source_run_ids=["r"])

def discover_pg(flag, tables=None, answers=None, budget=1000, **overrides):
    connection = FakePg(tables or PG_TABLES, answers)
    config = (profiled if flag else source)("postgresql", **overrides)
    spent = Budget(budget)
    return connection, discover_postgresql(connection, config, spent), spent


def test_control_pg_with_the_flag_off_reads_no_user_data_and_writes_no_profile_fields():
    connection, records, _ = discover_pg(False)
    assert connection.of_kind("profile") == [] and connection.of_kind("sample") == []
    for item in records["profiles.jsonl"]:
        dumped = item.model_dump()
        assert not any(dumped.get(name) is not None for name in PROFILE_FIELDS)
    assert records.get(RANGES_FILE, []) == []


def test_control_pg_sampling_on_and_flag_off_adds_only_the_token_reads():
    connection, _, _ = discover_pg(
        False, sample_rows=SAMPLE_ROWS, value_token_key_env="DBIG_TOKENISER", value_token_key_id="k"
    )
    assert len(connection.of_kind("sample")) == len(PG_TABLES)
    assert connection.of_kind("profile") == []


def test_pg_one_aggregate_statement_per_eligible_column_and_none_for_the_rest():
    connection, _, _ = discover_pg(True)
    found = targets(PG_TABLES, [statement for statement, _ in connection.of_kind("profile")])
    assert sorted(found) == sorted(PG_ELIGIBLE), "exactly one statement per eligible column"


def test_pg_profiles_need_neither_sampling_nor_a_token_key():
    connection, _, _ = discover_pg(True, sample_rows=0)
    assert len(connection.of_kind("profile")) == len(PG_ELIGIBLE)
    assert connection.of_kind("sample") == []


def test_pg_the_statement_is_read_only_whole_column_quoted_and_aliased():
    connection, _, _ = discover_pg(True, sample_rows=SAMPLE_ROWS)
    assert connection.calls[0][1].lower().startswith("set transaction read only")
    assert connection.of_kind("profile"), "the flag is on, so profile statements exist"
    for statement, params in connection.of_kind("profile"):
        flat = " ".join(statement.split())
        assert re.match(r"(?is)^\s*(select|with)\b", flat), flat
        assert not re.search(
            r"(?i)\b(insert|update|delete|drop|create|alter|truncate|grant|revoke|copy|merge)\b",
            flat,
        ), flat
        assert re.search(r'"public"\."(gizmo|sprocket)"', flat), "quoted the way the collectors do"
        assert not re.search(r"(?i)\btablesample\b|\bsample\b", flat), "whole column, no sampling"
        assert not re.search(rf"(?i)\blimit\s+{SAMPLE_ROWS}\b", flat)
        assert SAMPLE_ROWS not in tuple(params or ()), "sample_rows must not bound the aggregate"
        present = set(aliases_of(statement))
        assert set(REQUIRED_TEXT) <= present, f"missing aliases {set(REQUIRED_TEXT) - present}"


def test_pg_numeric_min_max_only_for_integer_columns_and_never_over_a_raw_text_value():
    connection, _, _ = discover_pg(True)
    assert connection.of_kind("profile")
    for statement, _ in connection.of_kind("profile"):
        flat = " ".join(statement.split())
        present = set(aliases_of(statement))
        integer = any(f'"{c}"' in flat for c in ("ident", "serial_no"))
        assert ({"value_min", "value_max"} <= present) == integer, flat
        if not integer:
            # Any min()/max() in a text or uuid statement is over a length, never the raw value.
            for match in re.finditer(r"(?i)\b(min|max)\s*\(", flat):
                depth, end = 1, match.end()
                while depth and end < len(flat):
                    depth += (flat[end] == "(") - (flat[end] == ")")
                    end += 1
                assert "len" in flat[match.end() : end].lower(), flat


def test_pg_the_budget_is_charged_one_per_profile_statement():
    _, _, off = discover_pg(False)
    _, _, on = discover_pg(True)
    assert on.used - off.used == len(PG_ELIGIBLE)
    connection = FakePg(PG_TABLES)
    tight = Budget(off.used + 2)
    try:
        discover_postgresql(connection, profiled("postgresql"), tight)
    except BudgetExhausted:
        pass
    assert tight.used <= tight.max_queries
    assert len(connection.of_kind("profile")) <= 2


def test_pg_results_become_value_free_profile_fields_and_integer_ranges_stay_separate():
    _, records, _ = discover_pg(True)
    by_column = profiles_by_column(records)
    assert set(by_column) == PG_ELIGIBLE
    label = by_column[("gizmo", "label")]
    assert label["length_min"] == 3 and label["length_max"] == 9
    assert label["length_avg"] == pytest.approx(5.5)
    assert label["digits_share"] == pytest.approx(0.1)
    assert label["alpha_share"] == pytest.approx(0.6)
    assert label["formats"] == {"aaa-9{4}": pytest.approx(0.8)}
    ranges = {column_of(records, r.model_dump()["field_id"]): r.model_dump()
              for r in records[RANGES_FILE]}
    assert set(ranges) == {("gizmo", "ident"), ("sprocket", "serial_no")}
    assert ranges[("gizmo", "ident")]["minimum"] == MIN_CANARY
    assert ranges[("gizmo", "ident")]["maximum"] == MAX_CANARY
    assert not any(c in json.dumps(item.model_dump(mode="json"))
                   for c in CANARIES for item in records["profiles.jsonl"])


def test_pg_decimal_results_are_written_and_read_back_as_numbers(tmp_path):
    """pg8000 returns numeric aggregates as Decimal; JSON mode would turn those into strings."""
    answers = {
        key: {"length_avg": Decimal("5.5"), "digits_share": Decimal("0.1"),
              "alpha_share": Decimal("0.6"), "hex_share": Decimal("0.2"),
              "uuid_share": Decimal("0.0"), "format_share": Decimal("0.8")}
        for key in PG_ELIGIBLE
    }
    _, records, _ = discover_pg(True, answers=answers)
    _write_bundle(tmp_path / "bundle", records, "postgresql")
    _, back = read_artifact(tmp_path / "bundle")
    seen = [item.model_dump() for item in back["profiles.jsonl"]
            if item.model_dump().get("length_min") is not None]
    assert len(seen) == len(PG_ELIGIBLE)
    for data in seen:
        for name in ("length_avg", "digits_share", "hex_share", "uuid_share", "alpha_share"):
            assert isinstance(data[name], int | float), f"{name} came back as {type(data[name])}"
        assert all(isinstance(v, int | float) for v in data["formats"].values())


def test_pg_an_empty_or_all_null_column_gives_no_values_and_no_crash(tmp_path):
    answers = {key: {name: None for name in ALIASES} for key in PG_ELIGIBLE}
    _, records, _ = discover_pg(True, answers=answers)
    for item in records["profiles.jsonl"]:
        data = item.model_dump()
        assert not data.get("formats"), "nothing was seen, so no format is claimed"
        assert data.get("length_min") is None
    assert records.get(RANGES_FILE, []) == []
    _write_bundle(tmp_path / "bundle", records, "postgresql")


def test_pg_a_column_name_that_is_not_an_identifier_is_never_put_into_sql():
    hostile = {"gizmo": [("ident", "bigint"), ('hostile"; DROP', "text")]}
    connection, config = FakePg(hostile), profiled("postgresql")
    try:
        discover_postgresql(connection, config, Budget(100))
    except ValueError:
        pass
    assert not any("hostile" in statement for _, statement, _ in connection.calls)


# =================================================================================================
# 3. ClickHouse
# =================================================================================================


def discover_ch(flag, answers=None, budget=1000, tables=None, **overrides):
    client = FakeClickHouse(tables or CH_TABLES, answers)
    config = (profiled if flag else source)("clickhouse", **overrides)
    spent = Budget(budget)
    return client, discover_clickhouse(client, config, spent), spent


def test_control_ch_with_the_flag_off_reads_no_user_data():
    client, records, _ = discover_ch(False)
    assert client.of_kind("profile") == [] and client.of_kind("sample") == []
    assert records.get(RANGES_FILE, []) == []


def test_ch_one_aggregate_statement_per_eligible_column_and_none_for_the_rest():
    client, _, _ = discover_ch(True)
    found = targets(CH_TABLES, [statement for statement, _, _ in client.of_kind("profile")])
    assert sorted(found) == sorted(CH_ELIGIBLE)


def test_ch_the_statement_is_read_only_whole_column_quoted_timed_and_aliased():
    client, _, _ = discover_ch(True, sample_rows=SAMPLE_ROWS)
    assert client.of_kind("profile")
    for statement, params, settings in client.of_kind("profile"):
        flat = " ".join(statement.split())
        assert re.match(r"(?is)^\s*(select|with)\b", flat), flat
        assert not re.search(
            r"(?i)\b(insert|alter|drop|create|truncate|optimize|attach|detach|rename|grant)\b", flat
        ), flat
        assert re.search(r'"public"\."(linkrow|shipment)"', flat)
        assert not re.search(r"(?i)\bsample\b", flat), "whole column, no SAMPLE clause"
        assert not re.search(rf"(?i)\blimit\s+{SAMPLE_ROWS}\b", flat)
        assert SAMPLE_ROWS not in tuple(params.values()), "sample_rows must not bound it"
        assert settings.get("max_execution_time") == TIMEOUT
        assert set(REQUIRED_TEXT) <= set(aliases_of(statement))


def test_ch_numeric_min_max_only_for_integer_columns():
    client, _, _ = discover_ch(True)
    assert client.of_kind("profile")
    for statement, _, _ in client.of_kind("profile"):
        flat = " ".join(statement.split())
        present = set(aliases_of(statement))
        integer = any(f'"{c}"' in flat for c in ("rowkey", "count_no"))
        assert ({"value_min", "value_max"} <= present) == integer, flat


def test_ch_the_budget_is_charged_one_per_profile_statement():
    _, _, off = discover_ch(False)
    _, _, on = discover_ch(True)
    assert on.used - off.used == len(CH_ELIGIBLE)
    client = FakeClickHouse(CH_TABLES)
    tight = Budget(off.used + 2)
    try:
        discover_clickhouse(client, profiled("clickhouse"), tight)
    except BudgetExhausted:
        pass
    assert tight.used <= tight.max_queries
    assert len(client.of_kind("profile")) <= 2


def test_ch_results_become_profile_fields_and_ranges_for_integers_only():
    _, records, _ = discover_ch(True)
    seen = profiles_by_column(records)
    assert set(seen) == CH_ELIGIBLE
    assert seen[("linkrow", "label")]["formats"] == {"aaa-9{4}": pytest.approx(0.8)}
    got = {column_of(records, r.model_dump()["field_id"]): r.model_dump()
           for r in records[RANGES_FILE]}
    assert set(got) == {("linkrow", "rowkey"), ("shipment", "count_no")}
    assert got[("linkrow", "rowkey")]["minimum"] == MIN_CANARY


def test_ch_an_empty_table_returns_nan_averages_that_must_not_reach_the_artifact(tmp_path):
    """ClickHouse avg() over zero rows is nan; canonical JSON refuses nan."""
    nan = float("nan")
    answers = {
        key: {"length_avg": nan, "digits_share": nan, "hex_share": nan, "uuid_share": nan,
              "alpha_share": nan, "format_share": nan, "length_min": None, "length_max": None,
              "format_token": None, "value_min": None, "value_max": None}
        for key in CH_ELIGIBLE
    }
    _, records, _ = discover_ch(True, answers=answers)
    for item in records["profiles.jsonl"]:
        data = item.model_dump()
        for name in ("length_avg", "digits_share", "hex_share", "uuid_share", "alpha_share"):
            value = data.get(name)
            assert value is None or value == value, f"{name} must not be nan"
    _write_bundle(tmp_path / "bundle", records, "clickhouse")


# =================================================================================================
# 4. MongoDB: the same profile from the documents it already samples
# =================================================================================================

DIGITS = ["48213907", "11992843", "70031288", "39018472", "56120094"]
UUIDS = [
    "3f2b8c1e-9d4a-4e6b-8a7c-1d2e3f4a5b6c", "9a8b7c6d-5e4f-4a3b-9c2d-1e0f9a8b7c6d",
    "c0ffee11-2233-4455-8677-8899aabbccdd",
]
LETTERS = ["wxyzq", "stuvw"]
SERIALS = [MIN_CANARY, 45_000_000, 47_000_001, 48_000_002, 49_000_003, 50_000_004, 51_000_005,
           52_000_006, 53_000_007, MAX_CANARY]


def mongo_documents():
    codes = DIGITS + UUIDS + LETTERS
    return {"ledger": [{"_id": n, "code": code, "serial": SERIALS[n - 1]}
                       for n, code in enumerate(codes, start=1)]}


def discover_mongo(flag, documents=None, budget=100, **overrides):
    client = FakeMongo(documents or mongo_documents())
    config = (profiled if flag else source)("mongodb", **{"sample_rows": 50, **overrides})
    spent = Budget(budget)
    return client, discover_mongodb(client, config, spent), spent


def mongo_profile(records, path):
    fields = {item.name: item.id for item in records["fields.jsonl"]}
    found = [
        item.model_dump() for item in records["profiles.jsonl"]
        if item.field_id == fields[path] and item.model_dump().get("length_min") is not None
    ]
    assert len(found) == 1, f"one format profile expected for {path}"
    return found[0]


def test_control_mongo_flag_off_adds_nothing():
    _, records, _ = discover_mongo(False)
    for item in records["profiles.jsonl"]:
        assert not any(item.model_dump().get(name) is not None for name in PROFILE_FIELDS)
    assert records.get(RANGES_FILE, []) == []


def test_mongo_costs_no_extra_find_and_no_extra_budget_charge():
    off_client, _, off = discover_mongo(False)
    on_client, _, on = discover_mongo(True)
    assert len(on_client.finds) == len(off_client.finds) == 1
    assert on.used == off.used


def test_mongo_with_no_sample_there_is_no_profile_and_no_read():
    client, records, _ = discover_mongo(True, sample_rows=0)
    assert client.finds == []
    assert not any(item.model_dump().get("formats") for item in records["profiles.jsonl"])


def test_mongo_profile_lengths_and_shares_come_from_the_sampled_documents():
    _, records, _ = discover_mongo(True)
    data = mongo_profile(records, "code")
    assert data["length_min"] == 5 and data["length_max"] == 36
    assert data["length_avg"] == pytest.approx((5 * 8 + 3 * 36 + 2 * 5) / 10)
    assert data["digits_share"] == pytest.approx(0.5)
    assert data["uuid_share"] == pytest.approx(0.3)
    assert data["alpha_share"] == pytest.approx(0.2)
    assert data["hex_share"] in (pytest.approx(0.0), pytest.approx(0.5))  # digits-only: either way
    shares = data["formats"].values()
    assert max(shares) == pytest.approx(0.5), "five equal-shape digit strings are one format"
    assert sum(shares) <= 1.0 + 1e-9


TOKEN_BODY = re.compile(r"^(?:[9a]|\{\d+\}|[^0-9A-Za-z{}\s])+$")


def test_mongo_format_tokens_are_value_free_and_distinguish_shapes():
    docs = ["QK-4821", "ZM-9035", "LT-1170", "4821-QK", "QK_4821"]
    _, records, _ = discover_mongo(
        True, {"ledger": [{"_id": n, "ref": value} for n, value in enumerate(docs, start=1)]}
    )
    formats = mongo_profile(records, "ref")["formats"]
    assert sorted(formats.values()) == pytest.approx([0.2, 0.2, 0.6])
    for token in formats:
        assert TOKEN_BODY.match(token), f"{token!r} is not digits->9, letters->a, punctuation kept"
    plain = re.sub(r"\{\d+\}", "", max(formats, key=formats.get))
    assert plain.startswith("a") and "-" in plain and "_" not in plain
    assert any("_" in token for token in formats)


def test_mongo_no_sampled_value_appears_in_the_profile_or_the_bundle(tmp_path):
    values = [TEXT_CANARY, "zqxjv-canary-4472", "zqxjv-canary-4473"]
    docs = [{"_id": n, "ref": value} for n, value in enumerate(values, start=1)]
    _, records, _ = discover_mongo(True, {"ledger": docs})
    assert any(item.model_dump().get("formats") for item in records["profiles.jsonl"])
    _write_bundle(tmp_path / "bundle", records, "mongodb")
    for path in (tmp_path / "bundle").iterdir():
        text = path.read_text(encoding="utf-8")
        for needle in ("zqxjv", "canary-447"):
            assert needle not in text, f"{needle} leaked into {path.name}"


def test_mongo_integer_range_is_a_local_only_row_and_the_profile_keeps_only_lengths():
    _, records, _ = discover_mongo(True)
    rows = {column_of_mongo(records, r.model_dump()["field_id"]): r.model_dump()
            for r in records[RANGES_FILE]}
    assert rows["serial"]["minimum"] == MIN_CANARY and rows["serial"]["maximum"] == MAX_CANARY
    assert "code" not in rows, "a text path never gets a min/max of its raw values"
    serial = mongo_profile(records, "serial")
    assert serial["length_min"] == 8 and serial["length_max"] == 8
    assert not any(c in json.dumps(item.model_dump(mode="json"))
                   for item in records["profiles.jsonl"] for c in CANARIES)


def column_of_mongo(records, field_id):
    return next(item.name for item in records["fields.jsonl"] if item.id == field_id)


# =================================================================================================
# 5. The local-only file: bundle contract, privacy, .gitignore, schemas
# =================================================================================================


def test_the_ranges_file_is_a_collector_bundle_file_and_never_a_snapshot_file():
    assert RANGES_FILE in COLLECTOR_FILES
    assert RANGES_FILE not in SNAPSHOT_FILES


def test_control_no_downstream_package_reads_the_local_only_file():
    """Requester, planner, reviewer and renderer must not even name it."""
    for path in [
        *(SRC / "requester" / "src").rglob("*.py"),
        *(SRC / "planner" / "src").rglob("*.py"),
        SRC / "catalog-builder" / "src" / "dbig_builder" / "reviewer.py",
        SRC / "catalog-builder" / "src" / "dbig_builder" / "render.py",
    ]:
        text = path.read_text(encoding="utf-8")
        assert RANGES_FILE not in text and "ValueRanges" not in text, path.name


def test_a_pg_bundle_keeps_the_real_range_only_in_the_local_only_file(
    tmp_path, monkeypatch, caplog, capsys
):
    fake_pg = types.ModuleType("pg8000")
    fake_pg.dbapi = types.SimpleNamespace(connect=lambda **kwargs: FakePg(PG_TABLES))
    monkeypatch.setitem(sys.modules, "pg8000", fake_pg)
    # C5: reason=the database host must now be private (L4), so the placeholder name became a
    # private IP; approved=owner, 2026-11-11; review=2027-05-11
    monkeypatch.setenv("DBIG_CONNECTION", '{"host":"10.0.0.5"}')
    monkeypatch.setenv("DBIG_SIGNING_KEY", "synthetic-signing-key-with-at-least-32-bytes")
    caplog.set_level(logging.DEBUG)
    destination = tmp_path / "bundle"
    collect(profiled("postgresql"), destination)
    manifest, tables = read_artifact(
        destination, signing_key=b"synthetic-signing-key-with-at-least-32-bytes",
        require_signature=True,
    )
    assert RANGES_FILE in [entry.path for entry in manifest.files]
    assert len(tables[RANGES_FILE]) == 2
    holders = {
        path.name for path in destination.iterdir()
        if any(c.encode() in path.read_bytes() for c in CANARIES)
    }
    assert holders == {RANGES_FILE}, f"a real bound is outside the local-only file: {holders}"
    out = capsys.readouterr()
    assert not any(c in caplog.text + out.out + out.err for c in CANARIES)


@pytest.mark.parametrize("run", sorted(set(re.findall(
    r"^artifacts/(run-[\w-]+)/\*\*/tokens\.jsonl$",
    (ROOT / ".gitignore").read_text(encoding="utf-8"), re.M))))
def test_gitignore_treats_the_local_only_file_like_tokens(run):
    def ignored(name):
        return subprocess.run(
            ["git", "check-ignore", "-q", f"artifacts/{run}/bundle-pg/{name}"],
            cwd=ROOT, check=False,
        ).returncode == 0

    assert ignored("tokens.jsonl"), "premise: tokens.jsonl is ignored in this run"
    assert ignored(RANGES_FILE), f".gitignore does not ignore {RANGES_FILE} under {run}"


def test_control_no_ranges_file_is_tracked():
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    assert not [name for name in tracked if name.endswith(RANGES_FILE)]


@pytest.mark.parametrize("name", ["value_ranges", "profile", "edge"])
def test_published_schemas_match_the_models(name):
    from dbig_contracts import models

    model = getattr(models, {"value_ranges": "ValueRanges", "profile": "Profile",
                             "edge": "Edge"}[name], None)
    assert model is not None, f"dbig_contracts.models has no model for {name}"
    path = ROOT / "schemas" / "ir-v1" / f"{name}.schema.json"
    assert path.is_file(), f"{path.name} not exported"
    assert json.loads(path.read_text(encoding="utf-8")) == model.model_json_schema()


# =================================================================================================
# 6. The builder rule
# =================================================================================================


def ranges_row(source_id, number, ordinal, low, high):
    from dbig_contracts.models import ValueRanges  # pinned name, see the acceptance note

    return ValueRanges(
        id=stable_id(source_id, "ranges", f"{number}:{ordinal}"),
        field_id=stable_id(source_id, "field", f"{number}:{ordinal}"),
        object_id=stable_id(source_id, "object", str(number)),
        minimum=low, maximum=high,
        source_id=source_id, observed_at=datetime.now(UTC), collector_version="test",
        provenance="synthetic",
    )


def make_bundle(path, source_id, tables, ranges):
    """A signed bundle; `ranges` {(table, field): (low, high)} goes in the local-only file."""
    kit.write_bundle(path, source_id, "postgresql", tables)
    if not ranges:
        return
    _, data = read_artifact(path, signing_key=BUNDLE_KEY.encode(), require_signature=True)
    names = list(tables)
    rows = [
        ranges_row(
            source_id, names.index(table) + 1,
            [n for n, _ in tables[table]["fields"]].index(field) + 1, low, high,
        )
        for (table, field), (low, high) in ranges.items()
    ]
    rebuilt = path.with_name(path.name + "-with-ranges")
    write_artifact(
        rebuilt, "collector-bundle", {**data, RANGES_FILE: rows}, producer="test",
        source_run_ids=[f"run-{source_id}"], signing_key=BUNDLE_KEY.encode(), key_id="test",
    )
    for item in path.iterdir():
        item.unlink()
    path.rmdir()
    rebuilt.rename(path)


def pair(kind="text", cross=True, child=None, parent=None, child_range=None, parent_range=None,
         declared=False):
    """Sources for one child column `shipment.gadget_id` and one parent key `gadget.id`."""
    other = "beta" if cross else "alpha"
    child_table = {
        "fields": [("shipment_no", kind), ("gadget_id", kind)], "key": ["shipment_no"],
        "profiles": {"gadget_id": {"method": "source_statistic", **child}} if child else {},
    }
    parent_table = {
        "fields": [("id", kind)], "key": ["id"],
        "profiles": {"id": {"method": "source_statistic", **parent}} if parent else {},
    }
    if declared:
        child_table["foreign"] = [("gadget_id", "gadget", "id")]
    sources = {"alpha": {"tables": {"shipment": child_table}}}
    ranges = {}
    if cross:
        sources["beta"] = {"tables": {"gadget": parent_table}}
    else:
        sources["alpha"]["tables"]["gadget"] = parent_table
    if child_range:
        ranges.setdefault("alpha", {})[("shipment", "gadget_id")] = child_range
    if parent_range:
        ranges.setdefault(other, {})[("gadget", "id")] = parent_range
    return sources, ranges


def snapshot_of(tmp_path, monkeypatch, sources, ranges=None, name="snap", **builder_extra):
    monkeypatch.setenv("BUNDLE_KEY", BUNDLE_KEY)
    bundles = []
    for source_id, spec in sources.items():
        path = tmp_path / f"bundle-{source_id}-{name}"
        make_bundle(path, source_id, spec["tables"], (ranges or {}).get(source_id))
        bundles.append(path)
    output = tmp_path / name
    assemble(bundles, output, BuilderConfig(signing_keys={"test": "BUNDLE_KEY"}, **builder_extra))
    return output


LINK = ("shipment", "gadget_id", "gadget", "id")


def kept(tmp_path, monkeypatch, sources, ranges=None, name="snap", **builder_extra):
    snap = snapshot_of(tmp_path, monkeypatch, sources, ranges, name, **builder_extra)
    return LINK in candidates(snap), snap


def new_drops(snapshot):
    run = kit.builder_run(snapshot)
    return {
        k: v for k, v in run.items() if k.startswith("dropped_") and k not in EXISTING_DROP_KEYS
    }


def fmt(token, share, *more):
    return {"formats": {token: share, **dict(more)}}


def test_control_without_any_profile_the_candidate_is_kept(tmp_path, monkeypatch):
    found, snap = kept(tmp_path, monkeypatch, pair()[0])
    assert found, "premise: the fixture yields the candidate"
    assert new_drops(snap) == {}


# --- (a) the child's dominant format is absent from the parent's ---------------------------------


@pytest.mark.parametrize("cross", [True, False], ids=["cross-source", "same-source"])
def test_a_dominant_child_format_absent_from_the_parent_drops_the_candidate(
    tmp_path, monkeypatch, cross
):
    sources, _ = pair(
        cross=cross, child=fmt("9{8}", 0.96, ("aaa", 0.04)), parent=fmt("aaa-9{4}", 0.9)
    )
    found, snap = kept(tmp_path, monkeypatch, sources)
    assert not found
    assert sum(new_drops(snap).values()) == 1
    assert "dropped_zero_overlap_candidates" not in kit.builder_run(snap)


@pytest.mark.parametrize(
    ("share", "expect_drop"),
    [(0.95, True), (0.9500001, True), (1.0, True), (0.949, False), (0.9, False)],
)
def test_the_default_share_boundary_is_inclusive(tmp_path, monkeypatch, share, expect_drop):
    sources, _ = pair(child=fmt("9{8}", share), parent=fmt("aaa-9{4}", 0.9))
    found, _ = kept(tmp_path, monkeypatch, sources)
    assert found is (not expect_drop)


def test_a_child_format_the_parent_also_records_is_kept(tmp_path, monkeypatch):
    """Any recorded parent format counts, not only its dominant one."""
    sources, _ = pair(child=fmt("9{8}", 0.97), parent=fmt("aaa-9{4}", 0.7, ("9{8}", 0.2)))
    assert kept(tmp_path, monkeypatch, sources)[0]


def test_the_same_dominant_format_is_kept(tmp_path, monkeypatch):
    sources, _ = pair(child=fmt("9{8}", 0.99), parent=fmt("9{8}", 0.99))
    assert kept(tmp_path, monkeypatch, sources)[0]


def test_the_threshold_comes_from_config_and_is_inclusive(tmp_path, monkeypatch):
    sources, _ = pair(child=fmt("9{8}", 0.6), parent=fmt("aaa-9{4}", 0.9))
    assert kept(tmp_path, monkeypatch, sources, name="default")[0]
    assert not kept(tmp_path, monkeypatch, sources, name="half", **{THRESHOLD: 0.5})[0]
    assert not kept(tmp_path, monkeypatch, sources, name="exact", **{THRESHOLD: 0.6})[0]
    assert kept(tmp_path, monkeypatch, sources, name="above", **{THRESHOLD: 0.61})[0]


def test_a_threshold_of_one_drops_only_a_wholly_uniform_child(tmp_path, monkeypatch):
    uniform, _ = pair(child=fmt("9{8}", 1.0), parent=fmt("aaa-9{4}", 0.9))
    nearly, _ = pair(child=fmt("9{8}", 0.999), parent=fmt("aaa-9{4}", 0.9))
    assert not kept(tmp_path, monkeypatch, uniform, name="u", **{THRESHOLD: 1.0})[0]
    assert kept(tmp_path, monkeypatch, nearly, name="n", **{THRESHOLD: 1.0})[0]


@pytest.mark.parametrize("value", [0, 0.0, -0.1, 1.01, 2, "high"])
def test_the_threshold_refuses_values_outside_zero_exclusive_to_one(value):
    assert BuilderConfig(signing_keys={"test": "BUNDLE_KEY"}, **{THRESHOLD: 0.5})
    with pytest.raises((ValidationError, ValueError)):
        BuilderConfig(signing_keys={"test": "BUNDLE_KEY"}, **{THRESHOLD: value})


def test_the_threshold_defaults_to_095_accepts_one_and_loads_from_toml(tmp_path):
    assert getattr(BuilderConfig(signing_keys={"test": "BUNDLE_KEY"}), THRESHOLD, None) == 0.95
    assert BuilderConfig(signing_keys={"test": "BUNDLE_KEY"}, **{THRESHOLD: 1.0})
    path = tmp_path / "builder.toml"
    path.write_text(f'[builder]\n{THRESHOLD} = 0.8\n[builder.signing_keys]\ntest = "BUNDLE_KEY"\n')
    assert getattr(load_builder_config(path), THRESHOLD, None) == 0.8
    path.write_text(f'[builder]\n{THRESHOLD} = 0.0\n[builder.signing_keys]\ntest = "BUNDLE_KEY"\n')
    with pytest.raises(OperatorError):
        load_builder_config(path)


@pytest.mark.parametrize(
    ("child", "parent"),
    [
        (None, fmt("aaa-9{4}", 0.9)),
        (fmt("9{8}", 0.99), None),
        (fmt("9{8}", 0.99), {"length_min": 5, "length_max": 9}),  # a profile with no formats
        ({"length_min": 8, "length_max": 8}, fmt("aaa-9{4}", 0.9)),
        ({"formats": {}}, fmt("aaa-9{4}", 0.9)),
        (fmt("9{8}", 0.99), {"formats": {}}),
    ],
    ids=["no-child", "no-parent", "parent-no-formats", "child-no-formats", "child-empty",
         "parent-empty"],
)
def test_a_missing_profile_on_either_side_applies_no_format_rule(
    tmp_path, monkeypatch, child, parent
):
    sources, _ = pair(child=child, parent=parent)
    assert kept(tmp_path, monkeypatch, sources)[0]


def test_control_a_declared_foreign_key_is_never_dropped_by_the_format_or_length_rules(
    tmp_path, monkeypatch
):
    sources, _ = pair(
        cross=False, declared=True,
        child={**fmt("9{8}", 1.0), "length_min": 8, "length_max": 8},
        parent={**fmt("aaa-9{4}", 1.0), "length_min": 30, "length_max": 40},
    )
    snap = snapshot_of(tmp_path, monkeypatch, sources)
    assert "DECLARES_FK" in kit.edge_kinds(snap)
    assert new_drops(snap) == {}


def test_a_declared_foreign_key_is_never_dropped_by_disjoint_ranges(tmp_path, monkeypatch):
    sources, ranges = pair(
        kind="bigint", cross=False, declared=True, child_range=(1, 5), parent_range=(100, 200)
    )
    snap = snapshot_of(tmp_path, monkeypatch, sources, ranges)
    assert "DECLARES_FK" in kit.edge_kinds(snap)
    assert new_drops(snap) == {}


# --- (b) text length ranges that do not overlap --------------------------------------------------


@pytest.mark.parametrize(
    ("child", "parent", "expect_drop"),
    [
        ((8, 8), (36, 36), True),
        ((3, 9), (10, 20), True),  # one apart
        ((21, 30), (10, 20), True),
        ((3, 10), (10, 20), False),  # touching at 10 overlaps
        ((20, 30), (10, 20), False),
        ((12, 14), (10, 20), False),  # inside
        ((1, 99), (10, 20), False),  # around
    ],
)
def test_text_length_ranges_that_do_not_overlap_drop_the_candidate(
    tmp_path, monkeypatch, child, parent, expect_drop
):
    sources, _ = pair(
        child={"length_min": child[0], "length_max": child[1]},
        parent={"length_min": parent[0], "length_max": parent[1]},
    )
    found, snap = kept(tmp_path, monkeypatch, sources)
    assert found is (not expect_drop)
    assert (sum(new_drops(snap).values()) == 1) is expect_drop


def test_a_half_known_length_range_applies_no_length_rule(tmp_path, monkeypatch):
    sources, _ = pair(child={"length_min": 8, "length_max": 8}, parent={"length_min": 36})
    assert kept(tmp_path, monkeypatch, sources)[0]


# --- (c) integer ranges that do not overlap ------------------------------------------------------


@pytest.mark.parametrize(
    ("child", "parent", "expect_drop"),
    [
        ((100, 200), (300, 400), True),
        ((500, 600), (300, 400), True),
        ((100, 300), (300, 400), False),  # touching overlaps
        ((1, 1000), (1, 10), False),  # the child exceeds the parent: still overlapping
        ((5, 10), (1, 1_000_000_000), False),
        ((-50, -10), (10, 20), True),
        ((2**60, 2**60 + 5), (2**60 + 6, 2**61), True),  # beyond float precision
        ((2**60, 2**60 + 6), (2**60 + 6, 2**61), False),
    ],
)
def test_integer_ranges_that_do_not_overlap_drop_the_candidate(
    tmp_path, monkeypatch, child, parent, expect_drop
):
    sources, ranges = pair(kind="bigint", child_range=child, parent_range=parent)
    found, snap = kept(tmp_path, monkeypatch, sources, ranges)
    assert found is (not expect_drop)
    assert (sum(new_drops(snap).values()) == 1) is expect_drop


@pytest.mark.parametrize("side", ["child", "parent", "neither"])
def test_one_missing_range_applies_no_range_rule_and_sets_no_overlap_flag(
    tmp_path, monkeypatch, side
):
    sources, ranges = pair(
        kind="bigint",
        child_range=(100, 200) if side == "child" else None,
        parent_range=(300, 400) if side == "parent" else None,
    )
    assert side == "neither" or ranges, "premise: the fixture carries the one range"
    found, snap = kept(tmp_path, monkeypatch, sources, ranges)
    assert found
    text = candidate_text(snap)
    assert '"numeric_ranges_overlap": true' not in text
    assert '"numeric_ranges_overlap": false' not in text


def test_a_kept_candidate_with_both_ranges_says_only_that_they_overlap(tmp_path, monkeypatch):
    sources, ranges = pair(kind="bigint", child_range=(MIN_CANARY, MAX_CANARY),
                           parent_range=(MIN_CANARY - 10, MAX_CANARY + 10))
    found, snap = kept(tmp_path, monkeypatch, sources, ranges)
    assert found
    assert '"numeric_ranges_overlap": true' in candidate_text(snap)
    for path in snap.iterdir():
        text = path.read_text(encoding="utf-8")
        assert not any(c in text for c in CANARIES), f"a real bound is in the snapshot: {path.name}"
    assert RANGES_FILE not in {path.name for path in snap.iterdir()}


def test_a_range_never_reaches_a_log_even_when_a_candidate_is_dropped(
    tmp_path, monkeypatch, caplog
):
    caplog.set_level(logging.DEBUG)
    sources, ranges = pair(kind="bigint", child_range=(MIN_CANARY, MIN_CANARY + 5),
                           parent_range=(MAX_CANARY - 5, MAX_CANARY))
    found, snap = kept(tmp_path, monkeypatch, sources, ranges)
    assert not found
    assert not any(c in caplog.text for c in CANARIES)
    texts = [path.read_text(encoding="utf-8") for path in snap.iterdir()]
    assert not any(c in text for text in texts for c in CANARIES)


# --- a dropped candidate takes no top-k slot ------------------------------------------------------


def test_a_dropped_candidate_does_not_take_the_top_k_slot(tmp_path, monkeypatch):
    """The same-source parent ranks first and mismatches in format: the cross-source one stays."""

    def parent_table(profile):
        return {"fields": [("id", "text")], "key": ["id"],
                "profiles": {"id": {"method": "source_statistic", **profile}}}

    sources = {
        "alpha": {"tables": {
            "shipment": {"fields": [("shipment_no", "text"), ("gadget_id", "text")],
                         "key": ["shipment_no"],
                         "profiles": {"gadget_id": {"method": "source_statistic",
                                                    **fmt("9{8}", 0.99)}}},
            "gadget": parent_table(fmt("aaa-9{4}", 0.9)),
        }},
        "beta": {"tables": {"gadget": parent_table(fmt("9{8}", 0.99))}},
    }
    snap = snapshot_of(tmp_path, monkeypatch, sources, top_k_per_field=1)
    _, data = read_artifact(snap)
    fields = {n.id: n for n in data["nodes.jsonl"] if n.record_type == "field"}
    parents = [
        fields[e.model_extra["field_pairs"][0][1]].source_id
        for e in data["edges.jsonl"]
        if e.kind == "REFERENCE_CANDIDATE"
        and fields[e.model_extra["field_pairs"][0][0]].name == "gadget_id"
    ]
    assert parents == ["beta"]


# --- diagnostics are counts only ------------------------------------------------------------------


def test_the_drop_is_counted_under_its_own_key_and_never_names_a_value(tmp_path, monkeypatch):
    sources, _ = pair(child=fmt("9{8}", 0.96), parent=fmt("aaa-9{4}", 0.9))
    snap = snapshot_of(tmp_path, monkeypatch, sources)
    keys = new_drops(snap)
    assert keys and all(isinstance(v, int) for v in keys.values())
    blob = json.dumps(kit.builder_run(snap), default=str)  # lead: the run record holds datetimes
    assert "9{8}" not in blob and "aaa" not in blob


# =================================================================================================
# 7. Plan 2.4: no corpus names, no hardcoded thresholds in packages/*/src; documented
# =================================================================================================


def _sources():
    return list(SRC.glob("*/src/**/*.py"))


def test_no_corpus_name_appears_in_the_packages():
    pattern = re.compile(
        r"zoominfo|diffbot|enrichment_engine|testzoominfo|companyrevenue|companyticker|"
        r"companyemployees|companyexperience|industryclassification", re.I,
    )
    assert not [p.name for p in _sources() if pattern.search(p.read_text(encoding="utf-8"))]


def test_the_reject_share_is_read_by_the_builder_and_typed_in_config_only():
    builder = (SRC / "catalog-builder/src/dbig_builder/builder.py").read_text(encoding="utf-8")
    assert THRESHOLD in builder, "the builder must read the configured share"
    literal = re.compile(r"(?<![\w.])0?\.95(?!\d)")
    offenders = [
        p.relative_to(ROOT).as_posix() for p in _sources()
        if p.name != "config.py" and literal.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == [], f"a 0.95 literal outside config.py: {offenders}"


def _float_literals(source: str) -> set[float]:
    return {n.value for n in ast.walk(ast.parse(source))
            if isinstance(n, ast.Constant) and isinstance(n.value, float)}


@pytest.mark.parametrize(
    ("path", "structural"),
    [
        ("packages/catalog-builder/src/dbig_builder/builder.py", set()),
        ("packages/collectors/src/dbig_collectors/runtime.py", {0.0, 1.0}),
    ],
)
def test_no_new_numeric_threshold_literal_in_the_builder_or_the_collector(path, structural):
    """A float that is not 0.0/1.0 and was not there at HEAD is a hardcoded threshold or cap."""
    head = subprocess.run(
        ["git", "show", f"HEAD:{path}"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    new = _float_literals((ROOT / path).read_text(encoding="utf-8")) - _float_literals(head)
    assert new <= structural, f"new float literals in {path}: {sorted(new - structural)}"


def test_the_design_document_names_the_new_settings_and_the_local_only_file():
    text = (ROOT / "TECHNICAL_IMPLEMENTATION.md").read_text(encoding="utf-8")
    for name in (FLAG, THRESHOLD, RANGES_FILE, "numeric_ranges_overlap"):
        assert name in text, f"TECHNICAL_IMPLEMENTATION.md does not mention {name}"


def test_c2_the_skipped_message_names_the_setting_and_the_count_needed():
    """J11: the query budget message tells the operator what to set, not only what happened."""
    _, records, spent = discover_pg(True, budget=4)
    (skipped,) = [r for r in records["diagnostics.jsonl"] if r.code == "FORMAT_PROFILES_SKIPPED"]
    count = int(skipped.message.split()[0])
    assert count > 0
    assert f"max_queries to at least {spent.used + count}" in skipped.message
