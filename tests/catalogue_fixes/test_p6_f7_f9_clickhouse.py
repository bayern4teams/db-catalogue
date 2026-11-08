"""P6-3 + P6-6 (live findings F7, F9): ClickHouse value hashes, and an exact probe row cap.

Derived from plan section 9 Phase 6 (P6-3, P6-6) and `LEAD_REVIEW.md` rows F7, F9 and "F9: why not
B as proposed" only; the implementation was not read.

P6-3  New `_tokenize_clickhouse` beside `_tokenize_postgresql`. One bounded read per table,
      `SELECT <columns> FROM <db>.<table> LIMIT <sample_rows>` (no `SELECT *`), identifiers
      quoted with `_quoted`, `max_execution_time` from the config, the read charged to the
      budget, `sample_method = first_rows_limit:<n>`, the shared `_value_tokens` key and key id,
      `complete` set as in P6-1 (true when the read returned fewer rows than `sample_rows`),
      nested types (`_NESTED_TYPE`) skipped, off unless `sample_rows` and `value_token_key_env`
      are both set.
P6-6  `_probe_clickhouse` reads through
      `SELECT count(), uniq(...) FROM (SELECT <columns> FROM <db>.<table> LIMIT <cap>)` with
      settings `max_threads = 1` and `max_execution_time`; `max_rows_to_read` and
      `read_overflow_mode` are gone. `sample_count` is exactly the cap on a larger table.

Every behaviour test that fails on today's code is `xfail(strict=True)`; the lead removes the
marks after implementing. The `control_*` tests pass before and after and guard against an
over-fix. The fake client below evaluates the queries it is sent (a `LIMIT` is honoured, a
missing `LIMIT` reads the whole table), so the old probe query visibly counts every row.

The row limit may be a literal or a bound parameter (`{name:UInt64}` or `%(name)s`); the tests
accept both. Names are invented and generic (plan section 2.4); the corpus-name grep over this
directory is `test_no_corpus_literals.py`, which picks this file up automatically.
"""

from __future__ import annotations

import ast
import hashlib
import hmac
import re
from datetime import UTC, datetime
from pathlib import Path

import catalogue_fixture_kit as kit
import pytest
from catalogue_fixture_kit import TOKEN_MATERIAL, candidate_text, candidates, containment_metrics
from dbig_builder.builder import assemble
from dbig_builder.config import BuilderConfig
from dbig_collectors.config import SourceConfig
from dbig_collectors.runtime import Budget, BudgetExhausted, discover_clickhouse
from dbig_contracts.artifact import write_artifact
from dbig_contracts.models import Source

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "packages" / "collectors" / "src" / "dbig_collectors" / "runtime.py"
COLLECTOR_CONFIG = ROOT / "packages" / "collectors" / "src" / "dbig_collectors" / "config.py"


# Non-default values everywhere, so a constant the implementation typed in cannot match by luck.
SAMPLE_ROWS = 7
TIMEOUT = 9
PROBE_ROWS = 13
TOKEN_CAP = 3
KEY_ID = "run-1"
ROOMY = 50  # a row limit above every fixture table, so the read is whole


# --- a fake ClickHouse that evaluates what it is sent -------------------------------------------


class FakeClickHouse:
    """`tables` is {name: ([(column, type), ...], [row dict, ...])} in database `public`.

    Catalog queries answer from the declared tables. A value read is parsed: the quoted column
    list, the quoted `db.table`, and an optional `LIMIT`; it returns what a server would. A probe
    (`SELECT count(), uniq(...)`) returns the row count and distinct counts over the rows it was
    allowed to read. Anything unparseable raises, so a test cannot pass on a query the fake did
    not understand.
    """

    LIMIT = r"(\d+|\{\w+:\w+\}|%\(\w+\)s)"

    def __init__(self, tables, database="public", probe_error=False):
        self.tables = tables
        self.database = database
        self.probe_error = probe_error
        self.calls = []  # (statement, parameters, settings)

    @property
    def queries(self):
        return [statement for statement, _, _ in self.calls]

    def data_reads(self):
        return [call for call in self.calls if "system." not in call[0]]

    def sample_reads(self):
        return [call for call in self.data_reads() if "count()" not in call[0]]

    def probe_reads(self):
        return [call for call in self.data_reads() if "count()" in call[0]]

    def _limit(self, token, parameters):
        if token is None:
            return None
        if token.isdigit():
            return int(token)
        return int(parameters[re.search(r"\w+", token).group(0)])

    def query(self, statement, parameters=None, settings=None, **extra):
        assert not extra, f"unexpected client arguments: {sorted(extra)}"
        self.calls.append((statement, parameters or {}, settings or {}))
        outer = self

        class Result:
            result_set = []

        if "system.tables" in statement:
            Result.result_set = [
                (name, f"uuid-{name}", "MergeTree", "", "", "", [], [], len(rows), "")
                for name, (_, rows) in sorted(self.tables.items())
            ]
        elif "system.columns" in statement:
            Result.result_set = [
                (name, column, kind, position, 0, "")
                for name, (columns, _) in sorted(self.tables.items())
                for position, (column, kind) in enumerate(columns, start=1)
            ]
        elif "system.databases" in statement:
            Result.result_set = [(self.database,)]
        elif statement.lstrip().startswith("SELECT count()"):
            if outer.probe_error:
                raise RuntimeError("canary-secret")
            Result.result_set = [self._probe(statement, parameters or {})]
        else:
            Result.result_set = self._sample(statement, parameters or {})
        return Result()

    def _rows_of(self, table, limit):
        rows = self.tables[table][1]
        return rows if limit is None else rows[:limit]

    def _sample(self, statement, parameters):
        text = " ".join(statement.split())
        match = re.fullmatch(
            rf'SELECT (.+?) FROM "{self.database}"\."(\w+)"(?: LIMIT {self.LIMIT})?',  # noqa: S608 # C5: reason=SQL text for a test fixture built from constant names; approved=user, 2026-11-08; review=2027-05-08
            text,
        )
        assert match, f"unparsed value read: {statement!r}"
        selected, table, token = match.groups()
        if selected.strip() == "*":
            names = [column for column, _ in self.tables[table][0]]
        else:
            names = re.findall(r'"(\w+)"', selected)
        return [
            tuple(row[name] for name in names)
            for row in self._rows_of(table, self._limit(token, parameters))
        ]

    def _probe(self, statement, parameters):
        text = " ".join(statement.split())
        names = re.findall(r'uniq\("(\w+)"\)', text.split(" FROM ")[0])
        nested = re.fullmatch(
            rf'SELECT count\(\), .+? FROM \(SELECT .+? FROM "{self.database}"\."(\w+)" '  # noqa: S608 # C5: reason=SQL text for a test fixture built from constant names; approved=user, 2026-11-08; review=2027-05-08
            rf"LIMIT {self.LIMIT}\)",
            text,
        )
        if nested:
            table, token = nested.groups()
            limit = self._limit(token, parameters)
        else:
            plain = re.fullmatch(
                rf'SELECT count\(\), .+? FROM "{self.database}"\."(\w+)"',  # noqa: S608 # C5: reason=SQL text for a test fixture built from constant names; approved=user, 2026-11-08; review=2027-05-08
                text,
            )
            assert plain, f"unparsed probe: {statement!r}"
            table, limit = plain.group(1), None  # the old shape has no LIMIT
        rows = self._rows_of(table, limit)
        return (len(rows), *(len({row[name] for row in rows}) for name in names))


def config(**overrides):
    return SourceConfig.model_validate(
        {
            "source_id": "site-b", "kind": "clickhouse", "connection_env": "DBIG_CONNECTION",
            "signing_key_env": "DBIG_SIGNING_KEY", "signing_key_id": "test",
            "namespaces": ["public"], "read_timeout_seconds": TIMEOUT, **overrides,
        }
    )


def tokenising(**overrides):
    return config(
        **{
            "sample_rows": SAMPLE_ROWS,
            "value_token_key_env": "DBIG_TOKENISER",
            "value_token_key_id": KEY_ID,
            **overrides,
        }
    )


@pytest.fixture(autouse=True)
def _token_key(monkeypatch):
    monkeypatch.setenv("DBIG_TOKENISER", TOKEN_MATERIAL)


def digest(value) -> str:
    """The keyed digest of the text form, written out here so the tests do not borrow it."""
    return hmac.new(TOKEN_MATERIAL.encode(), str(value).encode(), hashlib.sha256).hexdigest()


def by_field(records, table, column):
    """The ValueTokens entry (or None) for one column, joined through the field and object ids."""
    objects = {item.name: item.id for item in records["objects.jsonl"]}
    fields = {
        (item.object_id, item.name): item.id for item in records["fields.jsonl"]
    }
    field_id = fields.get((objects[table], column))
    found = [item for item in records["tokens.jsonl"] if item.field_id == field_id]
    assert len(found) <= 1
    return found[0] if found else None


def rows_of(column, values):
    return [{column: value} for value in values]


def orders(count, *, extra=None):
    """A two-column table `orders (rowkey UInt64, ref String)` with `count` distinct rows."""
    rows = [{"rowkey": index, "ref": f"ref-{index}"} for index in range(1, count + 1)]
    return ([("rowkey", "UInt64"), ("ref", "String")], rows)


# --- P6-3: off unless both settings are given (controls: pass before and after) ----------------


@pytest.mark.parametrize(
    "overrides",
    [
        {},  # nothing set
        {"sample_rows": SAMPLE_ROWS},  # sampling but no key
        {"value_token_key_env": "DBIG_TOKENISER", "value_token_key_id": KEY_ID},  # key, no rows
    ],
    ids=["nothing", "rows-without-key", "key-without-rows"],
)
def test_control_no_value_read_and_no_tokens_unless_both_settings_are_given(overrides):
    client = FakeClickHouse({"orders": orders(3)})
    records = discover_clickhouse(client, config(**overrides), Budget(50))
    assert records["tokens.jsonl"] == []
    assert client.data_reads() == [], "no value may be read unless tokenising is fully enabled"


# --- P6-3: the query -----------------------------------------------------------------------------

TYPES = [
    ("rowkey", "UInt64"),
    ("note", "Nullable(String)"),
    ("label", "LowCardinality(String)"),
    ("code", "LowCardinality(Nullable(String))"),
    ("tags", "Array(String)"),
    ("attrs", "Map(String, UInt64)"),
    ("pair", "Tuple(UInt8, UInt8)"),
    ("inner", "Nested(x UInt8)"),
    ("doc", "JSON"),
    ("blob", "Object('json')"),
    ("maybe", "Nullable(Tuple(UInt8, UInt8))"),
]
PLAIN = ["rowkey", "note", "label", "code"]
NESTED = ["tags", "attrs", "pair", "inner", "doc", "blob", "maybe"]


def wide_table(count):
    rows = [
        {
            "rowkey": index, "note": f"n{index}", "label": f"l{index}", "code": f"c{index}",
            "tags": ["x"], "attrs": {"k": index}, "pair": (1, 2), "inner": [1], "doc": {"a": 1},
            "blob": {"a": 1}, "maybe": (1, 2),
        }
        for index in range(1, count + 1)
    ]
    return (TYPES, rows)


def limit_of(call) -> int:
    statement, parameters, _ = call
    token = re.search(FakeClickHouse.LIMIT, statement.split(" LIMIT ")[-1]).group(1)
    return FakeClickHouse(None)._limit(token, parameters)


def test_one_bounded_read_per_table_names_its_columns_and_skips_nested_types():
    client = FakeClickHouse({"orders": wide_table(3), "items": orders(3)})
    discover_clickhouse(client, tokenising(), Budget(50))
    reads = client.sample_reads()
    assert len(reads) == 2, "one value read per table, not one per column"
    by_table = {}
    for statement, parameters, settings in reads:
        text = " ".join(statement.split())
        assert "*" not in text, "columns are named, never selected with *"
        match = re.fullmatch(
            r'SELECT (?P<columns>.+?) FROM "public"\."(?P<table>\w+)" LIMIT \S+', text
        )
        assert match, f"unexpected shape: {statement!r}"
        by_table[match["table"]] = re.findall(r'"(\w+)"', match["columns"])
        assert limit_of((statement, parameters, settings)) == SAMPLE_ROWS
        assert settings["max_execution_time"] == TIMEOUT
    assert by_table["orders"] == PLAIN, "plain columns in catalog order, no nested type"
    assert by_table["items"] == ["rowkey", "ref"]


def test_the_row_limit_and_timeout_follow_the_configuration_not_a_constant():
    for rows, timeout in ((5, 2), (11, 31)):
        client = FakeClickHouse({"orders": orders(3)})
        discover_clickhouse(
            client, tokenising(sample_rows=rows, read_timeout_seconds=timeout), Budget(50)
        )
        (read,) = client.sample_reads()
        assert limit_of(read) == rows
        assert read[2]["max_execution_time"] == timeout


def test_no_row_count_setting_replaces_the_limit():
    """The bound is the SQL `LIMIT`; a server setting that breaks the read is the F9 mistake."""
    client = FakeClickHouse({"orders": orders(30)})
    discover_clickhouse(client, tokenising(), Budget(50))
    (read,) = client.sample_reads()
    assert "max_rows_to_read" not in read[2] and "read_overflow_mode" not in read[2]


# --- P6-3: the budget ----------------------------------------------------------------------------


def test_every_value_read_is_charged_to_the_budget():
    tables = {"orders": orders(3), "items": orders(3)}
    plain = Budget(50)
    discover_clickhouse(FakeClickHouse(tables), config(), plain)
    tokenised = Budget(50)
    discover_clickhouse(FakeClickHouse(tables), tokenising(), tokenised)
    assert tokenised.used == plain.used + 2, "one charged read for each of the two tables"


@pytest.mark.parametrize("limit", [2, 3])
def test_control_an_exhausted_budget_stops_the_value_reads(limit):
    """Catalog queries cost 2. With 2 tables, a budget of 2 or 3 leaves room for 0 or 1 reads;
    an uncharged read would show as a second one. Whether the scan stops with
    `BudgetExhausted` or skips is not prescribed (PostgreSQL raises; the probe skips)."""
    client = FakeClickHouse({"orders": orders(3), "items": orders(3)})
    budget = Budget(limit)
    try:
        discover_clickhouse(client, tokenising(), budget)
    except BudgetExhausted:
        pass
    assert len(client.sample_reads()) <= limit - 2
    assert budget.used <= limit


def test_the_probe_and_the_value_read_each_cost_one_query_per_table():
    client = FakeClickHouse({"orders": orders(3)})
    budget = Budget(50)
    discover_clickhouse(client, tokenising(cardinality_probe=True), budget)
    assert len(client.sample_reads()) == 1 and len(client.probe_reads()) == 1
    assert budget.used == 2 + 2, "catalog (2) + probe (1) + value read (1)"


# --- P6-3: the tokens ----------------------------------------------------------------------------


def test_tokens_are_the_shared_keyed_digest_with_its_denominators():
    rows = [
        {"rowkey": 1, "ref": "alpha"},
        {"rowkey": 2, "ref": "alpha"},  # duplicate: observed 4, distinct 3
        {"rowkey": 3, "ref": None},  # null: neither observed nor tokenised
        {"rowkey": 4, "ref": "beta"},
        {"rowkey": 5, "ref": "gamma"},
    ]
    client = FakeClickHouse({"orders": ([("rowkey", "UInt64"), ("ref", "Nullable(String)")], rows)})
    records = discover_clickhouse(client, tokenising(), Budget(50))
    ref = by_field(records, "orders", "ref")
    assert ref is not None
    assert ref.record_type == "value_tokens" and ref.algorithm == "hmac-sha256"
    assert ref.key_id == KEY_ID
    assert ref.sample_method == f"first_rows_limit:{SAMPLE_ROWS}"
    assert ref.sample_count == 5
    assert (ref.observed_count, ref.distinct_count, ref.truncated) == (4, 3, False)
    assert ref.tokens == sorted({digest("alpha"), digest("beta"), digest("gamma")})
    key = by_field(records, "orders", "rowkey")
    assert key.observed_count == 5 and key.tokens == sorted(digest(n) for n in range(1, 6))
    objects = {item.name: item for item in records["objects.jsonl"]}
    assert ref.object_id == objects["orders"].id
    assert len({item.id for item in records["tokens.jsonl"]}) == len(records["tokens.jsonl"])


def test_an_integer_and_its_text_form_give_the_same_token_as_on_the_other_engines():
    """The cross-source case the product exists for: a string id in one engine, an integer in
    another. Same normaliser as PostgreSQL and MongoDB, so 123 and "123" agree."""
    tables = {
        "numeric": ([("ident", "UInt64")], rows_of("ident", [123, 456])),
        "textual": ([("ident", "String")], rows_of("ident", ["123", "456"])),
    }
    records = discover_clickhouse(FakeClickHouse(tables), tokenising(), Budget(50))
    assert by_field(records, "numeric", "ident").tokens == by_field(
        records, "textual", "ident"
    ).tokens
    assert by_field(records, "numeric", "ident").tokens == sorted([digest(123), digest(456)])


def test_an_all_null_or_empty_column_has_no_entry_and_nested_columns_have_none():
    rows = [{"rowkey": index, "note": None, "label": "l", "code": "c", "tags": ["x"],
             "attrs": {}, "pair": (1, 2), "inner": [1], "doc": {}, "blob": {}, "maybe": None}
            for index in range(1, 4)]
    client = FakeClickHouse({"orders": (TYPES, rows), "empty": orders(0)})
    records = discover_clickhouse(client, tokenising(), Budget(50))
    assert by_field(records, "orders", "rowkey") is not None
    assert by_field(records, "orders", "note") is None, "all null: nothing to publish"
    assert by_field(records, "empty", "rowkey") is None, "an empty table publishes no tokens"
    for name in NESTED:
        assert by_field(records, "orders", name) is None
    statement = next(c[0] for c in client.sample_reads() if '"public"."orders"' in c[0])
    for name in PLAIN:
        assert f'"{name}"' in statement, "plain types are read"


# --- P6-3: complete follows P6-1 -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("rows", "sample_rows", "cap", "complete", "truncated", "sample_count"),
    [
        (5, SAMPLE_ROWS, 256, True, False, 5),  # fewer rows than the limit: the whole table
        (SAMPLE_ROWS, SAMPLE_ROWS, 256, False, False, SAMPLE_ROWS),  # fills it: unknown
        (SAMPLE_ROWS + 5, SAMPLE_ROWS, 256, False, False, SAMPLE_ROWS),  # a larger table
        (20, 50, TOKEN_CAP, True, True, 20),  # token cap is not the row limit
        (1, SAMPLE_ROWS, 256, True, False, 1),
    ],
    ids=["fewer", "exactly-the-limit", "larger", "cap-independent", "single-row"],
)
def test_complete_is_true_only_when_the_read_returned_fewer_rows_than_the_limit(
    rows, sample_rows, cap, complete, truncated, sample_count
):
    client = FakeClickHouse({"orders": orders(rows)})
    records = discover_clickhouse(
        client, tokenising(sample_rows=sample_rows, max_value_tokens=cap), Budget(50)
    )
    entry = by_field(records, "orders", "ref")
    assert (entry.complete, entry.truncated, entry.sample_count) == (
        complete, truncated, sample_count
    )
    if truncated:
        assert entry.tokens == sorted(entry.tokens) and len(entry.tokens) == cap
        assert entry.distinct_count == rows, "the denominators describe the sample, not the cap"


# --- P6-3: privacy and failure -------------------------------------------------------------------


def test_no_sampled_value_reaches_the_records():
    canary = "canary-value-9f2c41"
    rows = [{"rowkey": index, "ref": f"{canary}-{index}"} for index in range(1, 4)]
    client = FakeClickHouse({"orders": ([("rowkey", "UInt64"), ("ref", "String")], rows)})
    records = discover_clickhouse(client, tokenising(), Budget(50))
    assert by_field(records, "orders", "ref") is not None, "tokens exist, so the check is real"
    dumped = repr(records)
    assert canary not in dumped and "canary-value" not in dumped


def test_a_short_token_key_is_refused_on_this_path_too(monkeypatch):
    monkeypatch.setenv("DBIG_TOKENISER", "too-short")
    with pytest.raises(ValueError, match="at least 32 bytes"):
        discover_clickhouse(FakeClickHouse({"orders": orders(3)}), tokenising(), Budget(50))


def test_attack_an_identifier_that_is_not_a_plain_name_does_not_abort_the_scan():
    """Beyond the letter of the spec, from 'as the probe does': the probe filters names with
    `_PLAIN_COLUMN`, while `_quoted` raises ValueError on anything else. A ClickHouse column or
    table may legally be called `Page Views`, and one such name must not fail the whole scan or be
    placed in a query."""
    # A plain name must not reach the odd one's query, and the odd one must not be queried.
    tables = {
        "orders": ([("rowkey", "UInt64"), ("odd column", "UInt64")],
                   [{"rowkey": 1, "odd column": 2}, {"rowkey": 3, "odd column": 4}]),
        "odd table": ([("rowkey", "UInt64")], [{"rowkey": 1}]),
    }
    # The fake's sample parser only understands word identifiers, so it would raise on an odd
    # one: the scan surviving proves the implementation never sent it.
    records = discover_clickhouse(FakeClickHouse(tables), tokenising(), Budget(50))
    assert by_field(records, "orders", "rowkey") is not None
    assert by_field(records, "orders", "odd column") is None


# --- P6-3 -> builder: a ClickHouse child's number against a PostgreSQL parent -------------------

CHILD = ("part", "gadget_id", "gadget", "id")


def _bundle_from(records, path, *, source_id="site-b", kind="clickhouse"):
    data = dict(records)
    for name in (
        "objects.jsonl", "fields.jsonl", "constraints.jsonl", "profiles.jsonl",
        "diagnostics.jsonl", "tokens.jsonl",
    ):
        data.setdefault(name, [])
    data["sources.jsonl"] = [
        Source(
            id=source_id, kind=kind, name=source_id, source_id=source_id,
            observed_at=datetime.now(UTC),
            collector_version="test", provenance="synthetic",
        )
    ]
    write_artifact(
        path, "collector-bundle", {name: list(items) for name, items in data.items()},
        producer="test", source_run_ids=["run-ch"], signing_key=kit.BUNDLE_KEY.encode(),
        key_id="test",
    )


def _snapshot_with_clickhouse_child(tmp_path, monkeypatch, records, *, parent_values):
    """A PostgreSQL parent `gadget.id` (kit bundle, declared key) and the given ClickHouse
    collector output as child source `site-b`."""
    monkeypatch.setenv("BUNDLE_KEY", kit.BUNDLE_KEY)
    parent = tmp_path / "bundle-alpha"
    kit.write_bundle(
        parent, "alpha", "postgresql",
        {"gadget": {"fields": [("id", "integer"), ("title", "text")], "key": ["id"],
                    "values": {"id": parent_values}}},
    )
    child = tmp_path / "bundle-site-b"
    _bundle_from(records, child)
    snapshot = tmp_path / "snapshot"
    assemble([parent, child], snapshot, BuilderConfig(signing_keys={"test": "BUNDLE_KEY"}))
    return snapshot


def _child_table(values):
    return {"part": ([("id", "UInt64"), ("gadget_id", "UInt64")],
                     [{"id": n, "gadget_id": v} for n, v in enumerate(values, start=1)])}


def test_control_a_clickhouse_child_without_sampling_keeps_its_candidate_unmeasured(
    tmp_path, monkeypatch
):
    records = discover_clickhouse(
        FakeClickHouse(_child_table(range(8))), config(), Budget(50)
    )
    snapshot = _snapshot_with_clickhouse_child(
        tmp_path, monkeypatch, records, parent_values=list(range(2)) + list(range(1000, 1012))
    )
    assert CHILD in candidates(snapshot)
    assert "value overlap not measured" in candidate_text(snapshot)
    assert containment_metrics(snapshot) == []


def test_a_quarter_overlap_clickhouse_child_shows_its_number_against_a_postgresql_parent(
    tmp_path, monkeypatch
):
    """The live F7 case in miniature: 8 child values, 2 in the parent (25%). Before P6-3 the
    ClickHouse side has no tokens, so there is no number at all."""
    child_values = list(range(8))
    parent_values = list(range(2)) + list(range(1000, 1012))
    records = discover_clickhouse(
        FakeClickHouse(_child_table(child_values)), tokenising(sample_rows=ROOMY), Budget(50)
    )
    snapshot = _snapshot_with_clickhouse_child(
        tmp_path, monkeypatch, records, parent_values=parent_values
    )
    assert candidates(snapshot) == [CHILD], "a non-zero overlap is kept"
    (metrics,) = containment_metrics(snapshot)
    assert metrics["contained_fraction"] == 0.25
    assert metrics["child_distinct_sampled"] == 8 and metrics["shared_tokens"] == 2


def test_a_disjoint_clickhouse_child_against_a_complete_parent_is_measured_and_dropped(
    tmp_path, monkeypatch
):
    """Negative: the number cuts both ways. 0 of 8 against a complete parent is a measurement
    (P6-2), which needs ClickHouse tokens to exist."""
    records = discover_clickhouse(
        FakeClickHouse(_child_table(range(5000, 5008))), tokenising(sample_rows=ROOMY),
        Budget(50),
    )
    snapshot = _snapshot_with_clickhouse_child(
        tmp_path, monkeypatch, records, parent_values=list(range(1000, 1012))
    )
    assert candidates(snapshot) == []


def test_no_raw_value_or_digest_leaks_into_the_clickhouse_bundle_text(tmp_path, monkeypatch):
    """Privacy canary extended to the new hashes (P10): values never, digests only in tokens."""
    canary = "canary-value-77b1e0"
    tables = {"part": ([("id", "UInt64"), ("gadget_id", "String")],
                       [{"id": n, "gadget_id": f"{canary}-{n}"} for n in range(1, 6)])}
    records = discover_clickhouse(FakeClickHouse(tables), tokenising(), Budget(50))
    assert by_field(records, "part", "gadget_id") is not None
    snapshot = _snapshot_with_clickhouse_child(
        tmp_path, monkeypatch, records, parent_values=list(range(10))
    )
    for path in [*tmp_path.rglob("*")]:
        if path.is_file():
            assert canary.encode() not in path.read_bytes(), path.name
    snapshot_text = "".join(
        p.read_text(encoding="utf-8", errors="ignore") for p in snapshot.rglob("*") if p.is_file()
    )
    digests = [d for d in by_field(records, "part", "gadget_id").tokens]
    assert digests and not any(d in snapshot_text for d in digests), "tokens stay in the bundle"


# --- P6-6: the probe -----------------------------------------------------------------------------

PROBE_TABLE = (
    [("rowkey", "UInt64"), ("kind", "String"), ("tags", "Array(String)")],
    lambda count: [
        {"rowkey": n, "kind": f"k{n % 4}", "tags": ["x"]} for n in range(1, count + 1)
    ],
)


def probe_client(count, **kwargs):
    columns, make = PROBE_TABLE
    return FakeClickHouse({"t": (columns, make(count))}, **kwargs)


def probe_config(**overrides):
    return config(cardinality_probe=True, cardinality_probe_max_rows=PROBE_ROWS, **overrides)


def test_the_probe_reads_through_a_limited_subquery_of_the_chosen_columns():
    client = probe_client(40)
    discover_clickhouse(client, probe_config(), Budget(50))
    ((statement, parameters, _),) = client.probe_reads()
    text = " ".join(statement.split())
    match = re.fullmatch(
        r'SELECT count\(\), uniq\("rowkey"\), uniq\("kind"\) FROM '
        r'\(SELECT "rowkey", ?"kind" FROM "public"\."t" LIMIT (\S+?)\)',
        text,
    )
    assert match, f"unexpected probe shape: {statement!r}"
    assert limit_of((statement, parameters, {})) == PROBE_ROWS
    assert "tags" not in text and "*" not in text, "array columns are still skipped"


def test_the_probe_settings_are_one_thread_and_the_timeout_and_nothing_that_breaks_the_read():
    client = probe_client(40)
    discover_clickhouse(client, probe_config(), Budget(50))
    ((_, _, settings),) = client.probe_reads()
    assert str(settings["max_threads"]) == "1"
    assert settings["max_execution_time"] == TIMEOUT
    assert "max_rows_to_read" not in settings
    assert "read_overflow_mode" not in settings


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        pytest.param(40, PROBE_ROWS, id="larger"),
        pytest.param(PROBE_ROWS + 1, PROBE_ROWS, id="one-over-the-cap"),
        # Boundary controls: the old query counts these correctly too, so they pass before and
        # after and guard an over-fix (an off-by-one in the new LIMIT).
        pytest.param(PROBE_ROWS, PROBE_ROWS, id="exactly-the-cap"),
        pytest.param(5, 5, id="smaller"),
    ],
)
def test_the_probe_counts_exactly_the_cap_or_the_whole_smaller_table(rows, expected):
    """`sample_count` equals the cap on a larger table and the row count on a smaller one. The
    fake evaluates the `LIMIT`; the old query has none and counts every row."""
    client = probe_client(rows)
    records = discover_clickhouse(client, probe_config(), Budget(50))
    probes = [p for p in records["profiles.jsonl"] if p.method == "sample"]
    assert {p.sample_count for p in probes} == {expected}
    seen = [p.distinct_estimate for p in probes]
    first = probe_client(rows).tables["t"][1][:expected]
    assert sorted(seen) == sorted(
        [float(len({r["rowkey"] for r in first})), float(len({r["kind"] for r in first}))]
    ), "distinct counts cover the same rows that were counted"


def test_the_cap_follows_the_configuration():
    for cap in (3, 25):
        client = probe_client(40)
        records = discover_clickhouse(
            client, config(cardinality_probe=True, cardinality_probe_max_rows=cap), Budget(50)
        )
        assert {p.sample_count for p in records["profiles.jsonl"] if p.method == "sample"} == {cap}


def test_the_config_comment_says_about_not_at_most():
    """Plan: change 'at most' to 'about: the count covers exactly this many rows; the server may
    read up to about one block per thread more'."""
    lines = COLLECTOR_CONFIG.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip().startswith("cardinality_probe:"))
    block = []
    for line in reversed(lines[:start]):
        if not line.strip().startswith("#"):
            break
        block.append(line.strip().lower())
    comment = " ".join(reversed(block))
    assert "about" in comment and "block" in comment


def test_control_the_probe_is_still_off_by_default_and_a_failure_is_a_leak_free_diagnostic():
    off = probe_client(40)
    discover_clickhouse(off, config(), Budget(50))
    assert off.probe_reads() == []
    failing = probe_client(40, probe_error=True)
    records = discover_clickhouse(failing, probe_config(), Budget(50))
    assert [d.code for d in records["diagnostics.jsonl"]] == ["STATS_UNAVAILABLE"]
    assert "canary-secret" not in repr(records)


# --- plan section 2.4: no hardcoded thresholds in the two functions ------------------------------


def _function_numbers(name: str) -> list[tuple[int, object]]:
    """Numeric literals in a function, leaving out subscripts and slices (a position, not a
    threshold) and booleans."""
    tree = ast.parse(RUNTIME.read_text(encoding="utf-8"))
    function = next(
        (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name),
        None,
    )
    assert function is not None, f"{name} does not exist yet"
    positions = {
        id(inner)
        for node in ast.walk(function)
        if isinstance(node, ast.Subscript)
        for inner in ast.walk(node.slice)
    }
    return [
        (node.lineno, node.value)
        for node in ast.walk(function)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, int | float)
        and not isinstance(node.value, bool)
        and id(node) not in positions
    ]


def _stray(name: str) -> list[tuple[int, object]]:
    """The question for every constant: would a count over the catalogue or the config produce
    it? Only 0, 1 and -1 (`max_threads = 1`, a comparison with zero) may appear as literals; the
    row cap, the timeout, the token cap and every limit come from the config."""
    return [(line, value) for line, value in _function_numbers(name) if value not in (0, 1, -1)]


def test_no_numeric_threshold_is_typed_into_the_clickhouse_tokenizer():
    assert _stray("_tokenize_clickhouse") == []


def test_control_no_numeric_threshold_is_typed_into_the_clickhouse_probe():
    """Passes before and after: the rewrite must not introduce a typed-in row cap or timeout."""
    assert _stray("_probe_clickhouse") == []


def test_control_no_corpus_name_in_application_source():
    names = ["zoom" + "info", "diff" + "bot", "enrichment" + "_engine", "company" + "revenue"]
    pattern = re.compile("|".join(names), re.IGNORECASE)
    hits = [
        str(path.relative_to(ROOT))
        for path in sorted(ROOT.glob("packages/*/src/**/*.py"))
        if pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert hits == []
