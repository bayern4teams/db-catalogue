# ruff: noqa: E501, S101, S608
"""Attack tests for P6-3 (F7, ClickHouse value hashes) and P6-6 (F9, probe through a LIMIT subquery).

Not collected by default; run by path (chdb optional, see below):
    PYTHONPATH=<site-packages with chdb> .venv/bin/python -m pytest -q tests/catalogue_fixes/attack6_f7_f9.py -p no:cacheprovider

Two kinds of client:

* `FakeClickHouse` from the derive file (evaluates the statements it is sent), and
* `ChdbClient` below: a real ClickHouse engine (chdb, embedded) behind the REAL clickhouse_connect
  query binding (`bind_query`) and the REAL Native-format deserialiser (`NativeTransform`), so the
  Python types a value takes are exactly what the driver returns. The tests that need it skip when
  `chdb` is not importable. Nothing here edits `packages/*/src`.

`test_finding_*` pin a behaviour the lead may want to change: they pass today and are named so a
future fix flips them on purpose. `xfail(strict=True)` marks an unsound behaviour whose fix would
make the test pass (the xfail then becomes an error, prompting removal of the mark).
All names are invented and generic.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import sys
import traceback
import uuid
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, date, datetime
from decimal import Decimal
from io import StringIO
from pathlib import Path

import pytest
from catalogue_fixture_kit import TOKEN_MATERIAL
from click.testing import CliRunner
from dbig_collectors import runtime
from dbig_collectors.cli import main as collector_main
from dbig_collectors.config import SourceConfig
from dbig_collectors.runtime import (
    Budget,
    BudgetExhausted,
    _token_text,
    discover_clickhouse,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_p6_f7_f9_clickhouse import FakeClickHouse  # noqa: E402

CANARY = "cnry_ch_value_91c3"
SECRET_ERROR = "cnry_ch_error_text_27ad"  # noqa: S105
SIGNING = "k" * 40  # noqa: S105


def digest(text: str) -> str:
    return hmac.new(TOKEN_MATERIAL.encode(), text.encode(), hashlib.sha256).hexdigest()


@pytest.fixture(autouse=True)
def _token_key(monkeypatch):
    monkeypatch.setenv("DBIG_TOKENISER", TOKEN_MATERIAL)


def cfg(**overrides):
    base = {
        "source_id": "site-b", "kind": "clickhouse", "connection_env": "DBIG_CONNECTION",
        "signing_key_env": "DBIG_SIGNING_KEY", "signing_key_id": "test",
        "namespaces": ["public"], "sample_rows": 5,
        "value_token_key_env": "DBIG_TOKENISER", "value_token_key_id": "run-1",
    }
    return SourceConfig.model_validate({**base, **overrides})


def no_tokens(**overrides):
    return cfg(sample_rows=0, value_token_key_env=None, value_token_key_id=None, **overrides)


# --- a real ClickHouse engine behind the real driver's binding and deserialiser -----------------

try:  # pragma: no cover - environment dependent
    import chdb  # noqa: F401
    from chdb import session as chdb_session
    from clickhouse_connect.driver.binding import bind_query
    from clickhouse_connect.driver.ctypes import RespBuffCls
    from clickhouse_connect.driver.query import QueryContext
    from clickhouse_connect.driver.transform import NativeTransform

    HAVE_CHDB = True
except Exception:  # noqa: BLE001
    HAVE_CHDB = False

needs_chdb = pytest.mark.skipif(not HAVE_CHDB, reason="chdb (embedded ClickHouse) not importable")


class _Source:
    def __init__(self, data: bytes) -> None:
        self.gen = iter([data])
        self.exception_tag = None

    def close(self) -> None:
        pass


class ChdbClient:
    """`query(statement, parameters, settings)` like clickhouse_connect's client, on real ClickHouse."""

    def __init__(self, path: str) -> None:
        self.session = chdb_session.Session(path)
        self.calls: list[tuple[str, object, object]] = []

    def run(self, sql: str) -> None:
        self.session.query(sql)

    def query(self, statement, parameters=None, settings=None, **extra):
        assert not extra, sorted(extra)
        self.calls.append((statement, parameters, settings))
        sql, bound = bind_query(statement, parameters, "UTC")  # the driver's own server-side binding
        params = {key.removeprefix("param_"): value for key, value in bound.items()}
        if settings:
            sql += " SETTINGS " + ", ".join(f"{k}={v}" for k, v in settings.items())
        result = self.session.query(sql, "Native", params=params) if params else self.session.query(sql, "Native")
        context = QueryContext(query=statement, parameters=parameters, settings=settings, use_none=True, server_tz=UTC)
        return NativeTransform.parse_response(RespBuffCls(_Source(result.bytes())), context)

    def close(self) -> None:
        pass

    def data_reads(self):
        return [call for call in self.calls if "system." not in call[0]]


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    if not HAVE_CHDB:
        pytest.skip("chdb not importable")
    client = ChdbClient(str(tmp_path_factory.mktemp("chdb")))
    client.run("CREATE DATABASE public")
    client.run(
        "CREATE TABLE public.typed (id UInt64, u UUID, d Date, dt DateTime, d64 DateTime64(3), "
        "dec Decimal(10,2), ip IPv4, ip6 IPv6, en Enum8('a'=1,'b'=2), fs FixedString(4), "
        "ns Nullable(String), lc LowCardinality(String), lcn LowCardinality(Nullable(String)), "
        "b Bool, f Float64, f32 Float32, i256 Int256, s String) ENGINE=MergeTree ORDER BY id"
    )
    client.run(
        "INSERT INTO public.typed VALUES (1,'61f0c404-5cb3-11e7-907b-a6006ad3dba0','2024-01-02',"
        "'2024-01-02 03:04:05','2024-01-02 03:04:05.678',12.50,'192.168.0.1','::1','a','abcd',NULL,"
        "'low','lown',true,1.5,1.1,123456789012345678901234567890,'plain')"
    )
    client.run(
        "CREATE TABLE public.`Page Views` (id UInt64, `my col` String, ok String) ENGINE=MergeTree ORDER BY id"
    )
    client.run("INSERT INTO public.`Page Views` VALUES (1,'a','b')")
    client.run("CREATE TABLE public.steady (rowkey UInt64, ref String) ENGINE=MergeTree ORDER BY rowkey")
    client.run("INSERT INTO public.steady SELECT number, concat('r', toString(number)) FROM numbers(30)")
    client.run("CREATE TABLE public.base (id UInt64, v String) ENGINE=MergeTree ORDER BY id")
    client.run("CREATE MATERIALIZED VIEW public.mv ENGINE=MergeTree ORDER BY id AS SELECT id, v FROM public.base")
    client.run("CREATE VIEW public.vw AS SELECT id, v FROM public.base")
    client.run("INSERT INTO public.base VALUES (1,'a'),(2,'b'),(3,'c')")
    client.run(
        "CREATE TABLE public.eph (id UInt64, secret String EPHEMERAL, shown String DEFAULT secret) "
        "ENGINE=MergeTree ORDER BY id"
    )
    client.run("INSERT INTO public.eph (id, secret) VALUES (1,'s1')")
    client.run("CREATE TABLE public.big (id UInt64) ENGINE=MergeTree ORDER BY id")
    for part in range(4):  # four parts, so several streams would read in parallel
        client.run(f"INSERT INTO public.big SELECT number + {part * 50000} FROM numbers(50000)")
    client.run("CREATE TABLE public.broken (a UInt8) ENGINE=File(CSV)")  # backing file does not exist
    return client


def only(engine, *names):
    """The engine's catalog restricted to some tables, so one hostile table can be isolated."""
    listed = ",".join(f"'{n}'" for n in names)

    class Restricted:
        def query(self, statement, parameters=None, settings=None, **extra):
            if "system.tables" in statement:
                statement = statement.replace("WHERE database = {db:String}", f"WHERE database = {{db:String}} AND name IN ({listed})")
            elif "system.columns" in statement:
                statement = statement.replace("WHERE database = {db:String}", f"WHERE database = {{db:String}} AND table IN ({listed})")
            return engine.query(statement, parameters, settings, **extra)

    return Restricted()


def token_for(records, table, column):
    objects = {item.name: item.id for item in records["objects.jsonl"]}
    fields = {(item.object_id, item.name): item.id for item in records["fields.jsonl"]}
    field_id = fields[(objects[table], column)]
    found = [item for item in records["tokens.jsonl"] if item.field_id == field_id]
    return found[0] if found else None


# --- (1) the Python types the real driver returns ------------------------------------------------


@needs_chdb
def test_real_driver_types_returned_for_each_clickhouse_type(engine):
    """Control for the matrix below: these are the objects `_token_text` actually receives."""
    result = engine.query('SELECT * FROM "public"."typed" LIMIT {lim:UInt64}', {"lim": 5})
    got = {name: type(value).__name__ for name, value in zip(result.column_names, result.result_set[0], strict=True)}
    assert got == {
        "id": "int", "u": "UUID", "d": "date", "dt": "datetime", "d64": "datetime",
        "dec": "Decimal", "ip": "IPv4Address", "ip6": "IPv6Address", "en": "str", "fs": "bytes",
        "ns": "NoneType", "lc": "str", "lcn": "str", "b": "bool", "f": "float", "f32": "float",
        "i256": "int", "s": "str",
    }


@needs_chdb
def test_every_driver_type_scans_without_crashing_and_text_types_publish_matching_tokens(engine):
    records = discover_clickhouse(only(engine, "typed"), cfg(), Budget(50))
    expected = {  # text form the PostgreSQL and MongoDB paths also key: the same `_token_text`
        "id": "1", "dec": "12.5", "en": "a", "lc": "low", "lcn": "lown", "f": "1.5",
        "i256": "123456789012345678901234567890", "s": "plain",
    }
    for column, text in expected.items():
        entry = token_for(records, "typed", column)
        assert entry is not None, f"{column} published no tokens"
        assert entry.tokens == [digest(text)], column
        assert entry.complete is True and entry.sample_count == 1


@needs_chdb
def test_finding_uuid_date_datetime_ip_fixedstring_bool_publish_no_tokens(engine):
    """FINDING (decision for the lead). `_token_text` returns None for uuid.UUID, date, datetime,
    IPv4/IPv6Address, bytes (FixedString) and bool, so these columns publish no entry at all.
    Cross-engine consequence: not a wrong match, an absent one. A ClickHouse UUID key against a
    PostgreSQL uuid column (pg8000 returns uuid.UUID too) or a MongoDB string uuid stays
    "value overlap not measured". UUID is the commonest ClickHouse join key after integers, and
    `str(uuid)` (lower-case hyphenated) equals PostgreSQL `::text` and a Mongo string uuid.
    A ClickHouse String column holding uuid text DOES tokenise, so a String-vs-uuid pair is
    one-sided (no overlap either way). Flip this test if `_token_text` learns these types."""
    records = discover_clickhouse(only(engine, "typed"), cfg(), Budget(50))
    for column in ["u", "d", "dt", "d64", "ip", "ip6", "fs", "b", "ns"]:
        assert token_for(records, "typed", column) is None, column
    assert _token_text(uuid.UUID("61f0c404-5cb3-11e7-907b-a6006ad3dba0")) is None
    assert _token_text(date(2024, 1, 2)) is None
    assert _token_text(datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)) is None
    assert _token_text(ipaddress.IPv4Address("192.168.0.1")) is None
    assert _token_text(b"abcd") is None


@needs_chdb
def test_finding_float32_token_carries_single_precision_noise(engine):
    """FINDING (minor). Float32 1.1 reaches Python as 1.100000023841858, so its token never equals the
    token of the text "1.1" or of a PostgreSQL real / MongoDB double 1.1."""
    records = discover_clickhouse(only(engine, "typed"), cfg(), Budget(50))
    entry = token_for(records, "typed", "f32")
    assert entry is not None and entry.tokens == [digest("1.100000023841858")]
    assert entry.tokens != [digest("1.1")]


def test_token_text_never_raises_on_odd_numbers():
    """Silent-crash hunt on values a driver can return: NaN, +-inf, huge or tiny decimals, -0."""
    for value in (float("nan"), float("inf"), float("-inf"), -0.0, 1e300, 5e-324, Decimal("1E+400"),
                  Decimal("NaN"), Decimal("-0"), Decimal("1.50"), 2**200, "", "é́"):
        text = _token_text(value)
        assert text is None or isinstance(text, str)
    assert _token_text(Decimal("12.50")) == _token_text(12.5) == "12.5"


def test_finding_decimal_wider_than_28_digits_loses_digits_before_keying():
    """FINDING (minor, shared with the PostgreSQL and MongoDB paths). `Decimal.normalize()` rounds to
    the 28-digit context, so two ClickHouse Decimal256 values that differ after digit 28 key the same."""
    a = Decimal("12345678901234567890123456789012345678.1234567890")
    b = Decimal("12345678901234567890123456789012345679.1234567890")
    assert _token_text(a) == _token_text(b)


# --- (2) names that are not plain identifiers ----------------------------------------------------


@needs_chdb
def test_a_table_that_is_not_a_plain_name_does_not_abort_the_scan_or_get_read(engine):
    before = len(engine.calls)
    for probe in (False, True):
        records = discover_clickhouse(only(engine, "Page Views", "steady"), cfg(cardinality_probe=probe), Budget(50))
        assert token_for(records, "steady", "rowkey") is not None
        assert {item.name for item in records["objects.jsonl"]} == {"Page Views", "steady"}
        assert token_for(records, "Page Views", "ok") is None
    reads = [call[0] for call in engine.calls[before:] if "system." not in call[0]]
    assert reads and not any("Page" in statement for statement in reads)


def test_a_plain_table_with_one_odd_column_still_tokenises_the_plain_ones():
    client = FakeClickHouse(
        {"orders": ([("rowkey", "UInt64"), ("my col", "String"), ("ref", "String")], [{"rowkey": 1, "my col": "x", "ref": "r1"}])}
    )
    records = discover_clickhouse(client, cfg(), Budget(50))
    (read,) = client.sample_reads()
    assert "my col" not in read[0]
    assert len(records["tokens.jsonl"]) == 2


def test_finding_hyphenated_database_name_no_longer_aborts_the_scan_when_tokens_are_on():
    """FINDING (low; same as the PostgreSQL path's schema). The table name is filtered with
    `_PLAIN_COLUMN` but `database` goes straight to `_quoted`, which raises ValueError for `my-db`;
    `collect` does not convert it, so the operator gets a traceback and no bundle. With only the
    probe on, the same name is swallowed into a STATS_UNAVAILABLE diagnostic, and with neither on
    the scan works (bound parameter)."""
    tables = {"orders": ([("rowkey", "UInt64")], [{"rowkey": 1}])}
    # Flipped by the lead after this attack: the value read is caught into a code-only diagnostic.
    sampled = discover_clickhouse(FakeClickHouse(tables, database="my-db"), cfg(namespaces=["my-db"]), Budget(50))
    assert [item.code for item in sampled["diagnostics.jsonl"]] == ["STATS_UNAVAILABLE"]
    discover_clickhouse(FakeClickHouse(tables, database="my-db"), no_tokens(namespaces=["my-db"]), Budget(50))
    records = discover_clickhouse(FakeClickHouse(tables, database="my-db"), no_tokens(namespaces=["my-db"], cardinality_probe=True), Budget(50))
    assert [item.code for item in records["diagnostics.jsonl"]] == ["STATS_UNAVAILABLE"]


# --- (3) budget ----------------------------------------------------------------------------------


def two_tables():
    return {
        "orders": ([("rowkey", "UInt64")], [{"rowkey": i} for i in range(3)]),
        "items": ([("rowkey", "UInt64")], [{"rowkey": i} for i in range(3)]),
    }


def test_budget_exhaustion_mid_tokenise_raises_like_postgresql_and_each_read_is_charged_first():
    plain = Budget(50)
    discover_clickhouse(FakeClickHouse(two_tables()), no_tokens(), plain)
    for allowed in range(plain.used, plain.used + 2):  # 0 or 1 of the 2 value reads fit
        client = FakeClickHouse(two_tables())
        with pytest.raises(BudgetExhausted):
            discover_clickhouse(client, cfg(), Budget(allowed))
        assert len(client.sample_reads()) == allowed - plain.used, "a read was sent that the budget did not allow"
    exact = Budget(plain.used + 2)
    discover_clickhouse(FakeClickHouse(two_tables()), cfg(), exact)
    assert exact.used == plain.used + 2


def _write_source(tmp_path, monkeypatch, extra=""):
    monkeypatch.setenv("DBIG_CONNECTION", json.dumps({"host": "127.0.0.1", "port": 8123}))
    monkeypatch.setenv("DBIG_SIGNING_KEY", SIGNING)
    source = tmp_path / "source.toml"
    source.write_text(
        '[source]\nsource_id = "site-b"\nkind = "clickhouse"\nconnection_env = "DBIG_CONNECTION"\n'
        'signing_key_env = "DBIG_SIGNING_KEY"\nsigning_key_id = "test"\nnamespaces = ["public"]\n'
        'sample_rows = 5\nvalue_token_key_env = "DBIG_TOKENISER"\nvalue_token_key_id = "run-1"\n' + extra,
        encoding="utf-8",
    )
    return source


def _patch_client(monkeypatch, factory):
    import clickhouse_connect

    monkeypatch.setattr(clickhouse_connect, "get_client", lambda **_: factory(), raising=False)


class Closable(FakeClickHouse):
    def close(self) -> None:
        pass


def test_budget_exhaustion_through_collect_is_an_operator_error_and_writes_no_bundle(tmp_path, monkeypatch):
    plain = Budget(50)
    discover_clickhouse(FakeClickHouse(two_tables()), no_tokens(), plain)
    _patch_client(monkeypatch, lambda: Closable(two_tables()))
    source = _write_source(tmp_path, monkeypatch, f"max_queries = {plain.used + 1}\n")
    out = tmp_path / "bundle"
    result = CliRunner().invoke(collector_main, ["collect", "--config", str(source), "--out", str(out)])
    assert result.exit_code != 0 and "configured limit" in result.output
    assert "Traceback" not in result.output and not out.exists()


def test_finding_tokens_are_charged_before_the_probe_so_a_tight_budget_drops_the_probe_silently():
    """FINDING (low). Tokenising is charged first. With a budget that fitted catalog + probe, the
    token reads take the probe's share and `_probe_clickhouse` returns on `budget.used >= max_queries`
    without a diagnostic: tokens appear, probe profiles vanish, and no code says why. (Pre-existing
    behaviour of the probe guard; the new tokenizer makes it reachable. PostgreSQL has the same order.)"""
    plain_probe = Budget(50)
    discover_clickhouse(FakeClickHouse(two_tables()), no_tokens(cardinality_probe=True), plain_probe)
    records = discover_clickhouse(FakeClickHouse(two_tables()), cfg(cardinality_probe=True), Budget(plain_probe.used))
    assert len(records["tokens.jsonl"]) == 2
    assert not [item for item in records["profiles.jsonl"] if getattr(item, "method", "") == "sample"]
    assert records["diagnostics.jsonl"] == []
    exact = Budget(plain_probe.used + 2)
    full = discover_clickhouse(FakeClickHouse(two_tables()), cfg(cardinality_probe=True), exact)
    assert exact.used == plain_probe.used + 2
    assert len([item for item in full["profiles.jsonl"] if getattr(item, "method", "") == "sample"]) == 2


# --- (4) views and materialized views ------------------------------------------------------------


@needs_chdb
def test_a_materialized_view_is_read_and_a_view_is_not(engine):
    # Flipped by the lead after this attack: a MaterializedView holds stored rows and is read, as
    # PostgreSQL reads matviews; a View still runs its query and is not read.
    before = len(engine.calls)
    records = discover_clickhouse(only(engine, "base", "mv", "vw"), cfg(), Budget(50))
    reads = [call[0] for call in engine.calls[before:] if "LIMIT" in call[0] and "system." not in call[0]]
    assert len(reads) == 2 and '"base"' in reads[0] + reads[1] and '"mv"' in reads[0] + reads[1], reads
    assert token_for(records, "base", "id") is not None
    assert token_for(records, "mv", "id") is not None and token_for(records, "vw", "id") is None


@needs_chdb
def test_finding_materialized_view_holds_stored_rows_that_a_limit_read_returns(engine):
    """FINDING / RECOMMENDATION. A MaterializedView without `TO` stores its rows in a hidden
    `.inner_id.<uuid>` table (listed by system.tables; its name fails `_PLAIN_COLUMN`, so it is skipped
    too). `SELECT ... FROM mv LIMIT n` reads stored rows, like a table (it does not run the view query);
    a `View` does run its query. The PostgreSQL path reads kind `m`. Skipping the MV loses its data
    entirely when it has no TO table. Reading `MaterializedView` (keep skipping `View`/`LiveView`) is
    the PostgreSQL-equivalent choice; a MaterializedView with `TO` duplicates the target table."""
    rows = engine.query('SELECT "id","v" FROM "public"."mv" LIMIT {lim:UInt64}', {"lim": 5}).result_set
    assert sorted(rows) == [(1, "a"), (2, "b"), (3, "c")]
    names = [r[0] for r in engine.query("SELECT name FROM system.tables WHERE database = 'public'").result_set]
    assert any(name.startswith(".inner_id.") for name in names)


# --- (5) the probe and the sample limit, on a real engine ----------------------------------------


@needs_chdb
def test_probe_binds_limit_in_a_subquery_and_counts_exactly_the_cap_on_a_multi_part_table(engine):
    for cap in (1, 1000, 123456, 200000, 250000):
        records = discover_clickhouse(only(engine, "big"), no_tokens(cardinality_probe=True, cardinality_probe_max_rows=cap), Budget(50))
        (profile,) = [item for item in records["profiles.jsonl"] if getattr(item, "method", "") == "sample"]
        assert profile.sample_count == min(cap, 200000), cap
    statement, parameters, settings = [call for call in engine.calls if call[0].startswith("SELECT count()")][-1]
    assert "LIMIT {lim:UInt64})" in statement and parameters == {"lim": 250000}
    assert settings["max_threads"] == 1 and isinstance(settings["max_threads"], int)
    assert "max_rows_to_read" not in settings and "read_overflow_mode" not in settings


@needs_chdb
def test_observation_uniq_is_approximate_so_a_unique_column_can_read_over_the_row_count(engine):
    """OBSERVATION (pre-existing, not introduced here). `uniq` is a sketch: 100,000 distinct ids gave
    100,315 on a real engine; `_probe_profile` clamps uniqueness to 1.0, so a column with up to about
    1-2 percent duplicates can still read as fully unique. `uniqExact` would be exact but costs memory."""
    result = engine.query(
        'SELECT count(), uniq("id") FROM (SELECT "id" FROM "public"."big" LIMIT {lim:UInt64})',
        {"lim": 100000}, {"max_threads": 1},
    ).result_set[0]
    assert result[0] == 100000 and result[1] != 100000


@needs_chdb
def test_sample_rows_limit_is_bound_exactly_on_a_large_table_and_complete_follows_the_boundary(engine):
    big = discover_clickhouse(only(engine, "big"), cfg(sample_rows=7), Budget(50))
    entry = token_for(big, "big", "id")
    assert entry.sample_count == 7 and entry.complete is False and entry.sample_method == "first_rows_limit:7"
    steady = discover_clickhouse(only(engine, "steady"), cfg(sample_rows=31), Budget(50))
    assert token_for(steady, "steady", "rowkey").complete is True  # 30 rows < 31
    exact = discover_clickhouse(only(engine, "steady"), cfg(sample_rows=30), Budget(50))
    assert token_for(exact, "steady", "rowkey").complete is False  # 30 == 30: cannot know, says no


# --- columns the catalog lists but a SELECT cannot read; tables that fail ------------------------


@needs_chdb
def test_a_table_with_an_ephemeral_column_does_not_abort_the_scan(engine):
    records = discover_clickhouse(only(engine, "eph", "steady"), cfg(), Budget(50))
    assert token_for(records, "steady", "rowkey") is not None


@needs_chdb
def test_one_unreadable_table_does_not_abort_the_scan(engine):
    records = discover_clickhouse(only(engine, "broken", "steady"), cfg(), Budget(50))
    assert token_for(records, "steady", "rowkey") is not None


class Failing(FakeClickHouse):
    """Fails every probe and every value read with an error that quotes content."""

    def __init__(self, tables, text):
        super().__init__(tables)
        self.text = text

    def query(self, statement, parameters=None, settings=None, **extra):
        if "system." not in statement:
            raise RuntimeError(self.text)
        return super().query(statement, parameters, settings, **extra)

    def close(self) -> None:
        pass


def test_a_failing_probe_and_a_failing_value_read_are_both_diagnostics():
    """Flipped by the lead after this attack: same fault, same code-only handling."""
    records = discover_clickhouse(Failing(two_tables(), SECRET_ERROR), no_tokens(cardinality_probe=True), Budget(50))
    assert [item.code for item in records["diagnostics.jsonl"]] == ["STATS_UNAVAILABLE"] * 2
    assert SECRET_ERROR not in json.dumps([item.model_dump(mode="json") for item in records["diagnostics.jsonl"]])
    sampled = discover_clickhouse(Failing(two_tables(), SECRET_ERROR), cfg(), Budget(50))
    assert [item.code for item in sampled["diagnostics.jsonl"]] == ["STATS_UNAVAILABLE"] * 2
    assert SECRET_ERROR not in json.dumps([item.model_dump(mode="json") for item in sampled["diagnostics.jsonl"]])


def test_a_server_error_text_from_a_value_read_does_not_reach_the_console(tmp_path, monkeypatch):
    _patch_client(monkeypatch, lambda: Failing(two_tables(), f"Cannot parse input: expected ',' before: '{CANARY}'"))
    source = _write_source(tmp_path, monkeypatch)
    result = CliRunner().invoke(collector_main, ["collect", "--config", str(source), "--out", str(tmp_path / "o")])
    shown = result.output + repr(result.exception) + "".join(traceback.format_exception(*result.exc_info))
    assert CANARY not in shown


# --- (6) + (7) privacy canaries on the new ClickHouse token path ---------------------------------


class _Records(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


def _canary_world():
    rows = [{"rowkey": index, "ref": f"{CANARY}-{index}", "note": CANARY if index == 1 else None} for index in range(1, 5)]
    return {"orders": ([("rowkey", "UInt64"), ("ref", "String"), ("note", "Nullable(String)")], rows)}


def test_h4_canary_the_clickhouse_token_path_leaks_no_value_key_or_digest_outside_the_token_file(tmp_path, monkeypatch):
    """H4 ran the real collector only against PostgreSQL (its ClickHouse bundle is hand-written), so the
    new `_tokenize_clickhouse` and the rewritten probe were not covered there. Here the real `collect`
    runs on a ClickHouse client holding canary values, probe and tokens on. The raw value, the token
    key and the signing key are absent from every console line, every log record at DEBUG and every
    bundle file; keyed digests are absent from all of those except `tokens.jsonl`."""
    _patch_client(monkeypatch, lambda: Closable(_canary_world()))
    source = _write_source(tmp_path, monkeypatch, "cardinality_probe = true\n")
    handler, out, err = _Records(), StringIO(), StringIO()
    root = logging.getLogger()
    previous = root.level
    root.setLevel(logging.DEBUG)
    root.addHandler(handler)
    try:
        with redirect_stdout(out), redirect_stderr(err):
            result = CliRunner().invoke(collector_main, ["collect", "--config", str(source), "--out", str(tmp_path / "bundle")])
    finally:
        root.removeHandler(handler)
        root.setLevel(previous)
    assert result.exit_code == 0, result.output
    bundle = tmp_path / "bundle"
    token_file = (bundle / "tokens.jsonl").read_text(encoding="utf-8")
    assert digest(f"{CANARY}-1") in token_file, "control: the canary was keyed"
    assert any(path.name == "profiles.jsonl" for path in bundle.iterdir()), "control: probe ran"
    needles = {"value": CANARY, "token key": TOKEN_MATERIAL, "signing key": SIGNING}
    digests = [digest(f"{CANARY}-{index}") for index in range(1, 5)] + [digest(CANARY)]
    console = result.output + out.getvalue() + err.getvalue() + "\n".join(handler.lines)
    for label, needle in needles.items():
        assert needle not in console, f"{label} in console or logs"
    assert not any(item in console for item in digests), "a token digest in console or logs"
    for path in sorted(bundle.rglob("*")):
        if not path.is_file():
            continue
        text = path.read_bytes().decode("latin-1")
        for label, needle in needles.items():
            assert needle not in text, f"{label} in {path.name}"
        if path.name != "tokens.jsonl":
            assert not any(item in text for item in digests), f"a digest in {path.name}"


def test_h4_canary_statements_sent_to_the_server_hold_identifiers_and_bound_numbers_only():
    client = FakeClickHouse(_canary_world())
    discover_clickhouse(client, cfg(cardinality_probe=True), Budget(50))
    for statement, parameters, settings in client.calls:
        assert CANARY not in statement + json.dumps(parameters, default=str) + json.dumps(settings, default=str)
    for statement, parameters, _ in client.data_reads():
        assert set(parameters) == {"lim"} and isinstance(parameters["lim"], int)
        assert "'" not in statement and "%" not in statement  # no string literal in a value read


def test_the_new_code_makes_no_logging_or_print_call():
    source = Path(runtime.__file__).read_text(encoding="utf-8")
    block = source[source.index("def _tokenize_clickhouse"): source.index("def _average_width")]
    assert "logging" not in block and "print(" not in block and "logger" not in block


def test_the_failed_probe_diagnostic_and_the_records_hold_no_error_text():
    class FailingProbe(FakeClickHouse):
        def query(self, statement, parameters=None, settings=None, **extra):
            if statement.startswith("SELECT count()"):
                raise RuntimeError(f"{SECRET_ERROR} {CANARY}")
            return super().query(statement, parameters, settings, **extra)

    records = discover_clickhouse(FailingProbe(_canary_world()), cfg(cardinality_probe=True), Budget(50))
    blob = json.dumps({k: [item.model_dump(mode="json") for item in v] for k, v in records.items()}, default=str)
    assert SECRET_ERROR not in blob and CANARY not in blob
    assert [item.code for item in records["diagnostics.jsonl"]] == ["STATS_UNAVAILABLE"]
