"""How `collect` connects: verified TLS, a private CA when given, plaintext only on loopback.

The drivers are replaced by stand-ins that record what `collect` hands them, because the unit job
installs no database driver. That makes these tests about this code's decisions: which TLS
options each driver receives, and which connections are refused before any socket opens. Real
handshakes against real servers, including the refusal of a certificate from an untrusted CA,
are in `test_live_pipeline.py`.
"""

from __future__ import annotations

import importlib.util
import json
import ssl
import sys
import types
from pathlib import Path

import pytest
from dbig_collectors import runtime
from dbig_collectors.config import SourceConfig, load_config
from dbig_contracts.artifact import read_artifact
from dbig_contracts.errors import OperatorError

SIGNING_KEY = "tls-test-signing-key-of-sufficient-length"
MAKE_CERTS = Path(__file__).parents[1] / "scripts" / "make_ci_certs.py"


class Connected(Exception):
    """Raised by a stand-in driver once it has recorded its arguments."""


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Install stand-in drivers for all three engines and return the argument log."""
    log: list[dict] = []

    def record(engine: str):
        def connect(*args, **kwargs):
            log.append({"engine": engine, "args": args, "kwargs": kwargs})
            raise Connected

        return connect

    dbapi = types.ModuleType("pg8000.dbapi")
    dbapi.connect = record("postgresql")  # type: ignore[attr-defined]  # C5: reason=mypy cannot see attributes set on a fake module; approved=user, 2026-11-08; review=2027-05-08
    pg8000 = types.ModuleType("pg8000")
    pg8000.dbapi = dbapi  # type: ignore[attr-defined]  # C5: reason=mypy cannot see attributes set on a fake module; approved=user, 2026-11-08; review=2027-05-08
    clickhouse = types.ModuleType("clickhouse_connect")
    clickhouse.get_client = record("clickhouse")  # type: ignore[attr-defined]  # C5: reason=mypy cannot see attributes set on a fake module; approved=user, 2026-11-08; review=2027-05-08

    def parse_uri(uri: str, **_: object) -> dict:
        if uri.startswith("mongodb+srv://"):
            raise AssertionError("an SRV URI must be refused before parsing, which does DNS")
        hosts = uri.split("://", 1)[1].split("/", 1)[0].split("@")[-1]
        nodes = []
        for host in hosts.split(","):
            name, _, port = host.rpartition(":") if host.count(":") == 1 or "]" in host else (
                host, "", ""
            )
            nodes.append((name.strip("[]") or host, int(port or 27017)))
        return {"nodelist": nodes}

    uri_parser = types.ModuleType("pymongo.uri_parser")
    uri_parser.parse_uri = parse_uri  # type: ignore[attr-defined]  # C5: reason=mypy cannot see attributes set on a fake module; approved=user, 2026-11-08; review=2027-05-08
    pymongo = types.ModuleType("pymongo")
    pymongo.MongoClient = record("mongodb")  # type: ignore[attr-defined]  # C5: reason=mypy cannot see attributes set on a fake module; approved=user, 2026-11-08; review=2027-05-08
    pymongo.uri_parser = uri_parser  # type: ignore[attr-defined]  # C5: reason=mypy cannot see attributes set on a fake module; approved=user, 2026-11-08; review=2027-05-08
    for name, module in {
        "pg8000": pg8000, "pg8000.dbapi": dbapi, "clickhouse_connect": clickhouse,
        "pymongo": pymongo, "pymongo.uri_parser": uri_parser,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setenv("SIGNING_KEY", SIGNING_KEY)
    return log


@pytest.fixture(scope="module")
def ca_file(tmp_path_factory) -> Path:
    spec = importlib.util.spec_from_file_location("make_ci_certs", MAKE_CERTS)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.generate(tmp_path_factory.mktemp("certs"))["ca"]


def _config(kind: str, **overrides) -> SourceConfig:
    return SourceConfig(
        source_id="s", kind=kind, connection_env="CONNECTION", signing_key_env="SIGNING_KEY",
        signing_key_id="k", namespaces=["n"], **overrides,
    )


def _collect(monkeypatch, tmp_path, kind: str, connection: dict, **overrides) -> None:
    monkeypatch.setenv("CONNECTION", json.dumps(connection))
    runtime.collect(_config(kind, **overrides), tmp_path / "bundle")


PG_REMOTE = {"host": "10.0.0.5", "port": 5432, "user": "reader", "database": "app"}
PG_LOOPBACK = {**PG_REMOTE, "host": "127.0.0.1"}
CH_REMOTE = {"host": "10.0.0.6", "port": 8443, "username": "reader"}
CH_LOOPBACK = {**CH_REMOTE, "host": "127.0.0.1", "port": 8123}


# --- verified TLS by default ----------------------------------------------------------------


def test_postgresql_verifies_the_chain_and_hostname_by_default(monkeypatch, tmp_path, calls):
    with pytest.raises(Connected):
        _collect(monkeypatch, tmp_path, "postgresql", PG_REMOTE)
    context = calls[0]["kwargs"]["ssl_context"]
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_clickhouse_uses_https_and_verifies_by_default(monkeypatch, tmp_path, calls):
    with pytest.raises(Connected):
        _collect(monkeypatch, tmp_path, "clickhouse", CH_REMOTE)
    options = calls[0]["kwargs"]
    assert options["secure"] is True
    assert options["verify"] is True
    assert "ca_cert" not in options, "no CA file configured means the operating system store"


def test_mongodb_uses_tls_by_default(monkeypatch, tmp_path, calls):
    with pytest.raises(Connected):
        _collect(monkeypatch, tmp_path, "mongodb", {"uri": "mongodb://10.0.0.7:27017"})
    assert calls[0]["kwargs"]["tls"] is True
    assert "tlsCAFile" not in calls[0]["kwargs"]
    assert "tlsInsecure" not in calls[0]["kwargs"]


# --- a private CA reaches every driver --------------------------------------------------------


def test_postgresql_trusts_the_configured_ca(monkeypatch, tmp_path, calls, ca_file):
    with pytest.raises(Connected):
        _collect(monkeypatch, tmp_path, "postgresql", PG_REMOTE, tls_ca_file=ca_file)
    context = calls[0]["kwargs"]["ssl_context"]
    subjects = [dict(item[0] for item in cert["subject"]) for cert in context.get_ca_certs()]
    assert any(subject.get("commonName") == "dbig-ci-throwaway-ca" for subject in subjects)
    assert context.verify_mode == ssl.CERT_REQUIRED


def test_clickhouse_trusts_the_configured_ca(monkeypatch, tmp_path, calls, ca_file):
    with pytest.raises(Connected):
        _collect(monkeypatch, tmp_path, "clickhouse", CH_REMOTE, tls_ca_file=ca_file)
    assert calls[0]["kwargs"]["ca_cert"] == str(ca_file)
    assert calls[0]["kwargs"]["verify"] is True


def test_mongodb_trusts_the_configured_ca(monkeypatch, tmp_path, calls, ca_file):
    with pytest.raises(Connected):
        _collect(monkeypatch, tmp_path, "mongodb", {"uri": "mongodb://10.0.0.7"},
                 tls_ca_file=ca_file)
    assert calls[0]["kwargs"]["tlsCAFile"] == str(ca_file)
    assert calls[0]["kwargs"]["tls"] is True


def test_a_missing_ca_file_is_refused_at_configuration(tmp_path):
    with pytest.raises(ValueError, match="not a readable file"):
        _config("postgresql", tls_ca_file=tmp_path / "absent.pem")


def test_a_ca_file_with_tls_off_is_refused(ca_file):
    """It would silently do nothing, which reads as protection that is not there."""
    with pytest.raises(ValueError, match="no effect with tls = false"):
        _config("postgresql", tls=False, tls_ca_file=ca_file)


def test_the_ca_file_can_come_from_the_environment(tmp_path, monkeypatch, ca_file):
    (tmp_path / "source.toml").write_text(
        '[source]\nsource_id = "s"\nkind = "postgresql"\nconnection_env = "C"\n'
        'signing_key_env = "K"\nsigning_key_id = "k"\nnamespaces = ["n"]\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("DBIG_TLS_CA_FILE", str(ca_file))
    assert load_config(tmp_path / "source.toml").tls_ca_file == ca_file


# --- plaintext only on loopback ---------------------------------------------------------------


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "127.0.0.2"])
def test_postgresql_plaintext_on_loopback(monkeypatch, tmp_path, calls, host):
    with pytest.raises(Connected):
        _collect(monkeypatch, tmp_path, "postgresql", {**PG_LOOPBACK, "host": host}, tls=False)
    assert calls[0]["kwargs"]["ssl_context"] is None


@pytest.mark.parametrize("host", ["10.0.0.5", "localhost", "db.internal", ""])
def test_postgresql_plaintext_elsewhere_is_refused_before_connecting(
    monkeypatch, tmp_path, calls, host
):
    """`localhost` included: it is whatever the resolver says, so only IP literals count."""
    with pytest.raises(OperatorError, match="only for loopback"):
        _collect(monkeypatch, tmp_path, "postgresql", {**PG_REMOTE, "host": host}, tls=False)
    assert calls == [], "no connection may be attempted"


def test_clickhouse_plaintext_on_loopback(monkeypatch, tmp_path, calls):
    with pytest.raises(Connected):
        _collect(monkeypatch, tmp_path, "clickhouse", CH_LOOPBACK, tls=False)
    assert calls[0]["kwargs"]["secure"] is False


def test_clickhouse_plaintext_elsewhere_is_refused(monkeypatch, tmp_path, calls):
    with pytest.raises(OperatorError, match="only for loopback"):
        _collect(monkeypatch, tmp_path, "clickhouse", CH_REMOTE, tls=False)
    assert calls == []


def test_mongodb_plaintext_on_loopback(monkeypatch, tmp_path, calls):
    with pytest.raises(Connected):
        _collect(monkeypatch, tmp_path, "mongodb", {"uri": "mongodb://127.0.0.1:27017"},
                 tls=False)
    assert calls[0]["kwargs"]["tls"] is False


def test_mongodb_plaintext_needs_every_host_on_loopback(monkeypatch, tmp_path, calls):
    """A replica set with one remote member would send that member plaintext."""
    with pytest.raises(OperatorError, match="only for loopback"):
        _collect(monkeypatch, tmp_path, "mongodb",
                 {"uri": "mongodb://127.0.0.1:27017,10.0.0.8:27017"}, tls=False)
    assert calls == []


def test_mongodb_srv_cannot_be_plaintext(monkeypatch, tmp_path, calls):
    with pytest.raises(OperatorError, match="mongodb\\+srv"):
        _collect(monkeypatch, tmp_path, "mongodb", {"uri": "mongodb+srv://cluster.internal"},
                 tls=False)
    assert calls == []


# --- the transport is recorded -----------------------------------------------------------------


@pytest.mark.parametrize(("tls", "expected"), [(True, "tls-verified"),
                                               (False, "plaintext-loopback")])
def test_the_bundle_records_how_the_source_was_reached(monkeypatch, tmp_path, tls, expected):
    class Client:
        def close(self) -> None:
            pass

    dbapi = types.ModuleType("pg8000.dbapi")
    dbapi.connect = lambda **_: Client()  # type: ignore[attr-defined]  # C5: reason=mypy cannot see attributes set on a fake module; approved=user, 2026-11-08; review=2027-05-08
    pg8000 = types.ModuleType("pg8000")
    pg8000.dbapi = dbapi  # type: ignore[attr-defined]  # C5: reason=mypy cannot see attributes set on a fake module; approved=user, 2026-11-08; review=2027-05-08
    monkeypatch.setitem(sys.modules, "pg8000", pg8000)
    monkeypatch.setitem(sys.modules, "pg8000.dbapi", dbapi)
    monkeypatch.setattr(runtime, "discover_postgresql", lambda *_: {"diagnostics.jsonl": []})
    monkeypatch.setenv("SIGNING_KEY", SIGNING_KEY)
    _collect(monkeypatch, tmp_path, "postgresql", PG_LOOPBACK, tls=tls)
    _, data = read_artifact(tmp_path / "bundle", signing_key=SIGNING_KEY.encode())
    assert data["sources.jsonl"][0].model_dump()["transport"] == expected


# --- a certificate that does not verify is reported, not dumped --------------------------------


def _failure(error: BaseException, **overrides) -> OperatorError | None:
    return runtime._certificate_failure(error, _config("clickhouse", **overrides))


def test_a_wrapped_verification_error_becomes_one_actionable_line():
    """ClickHouse Connect wraps the `ssl` error; the chain must still be recognised."""
    cause = ssl.SSLCertVerificationError(1, "certificate verify failed")
    cause.verify_message = "unable to get local issuer certificate"
    try:
        try:
            raise cause
        except ssl.SSLError as inner:
            leaked = "HTTPSConnectionPool(host='10.0.0.6', password='hunter2')"
            raise RuntimeError(leaked) from inner
    except RuntimeError as outer:
        converted = _failure(outer)
    assert converted is not None
    message = str(converted)
    assert "unable to get local issuer certificate" in message
    assert "set tls_ca_file" in message
    assert "hunter2" not in message and "10.0.0.6" not in message


def test_a_verification_failure_carried_only_as_text_is_recognised():
    """PyMongo reports it as the text of a server-selection timeout."""
    converted = _failure(
        TimeoutError("10.0.0.7:27017: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")
    )
    assert converted is not None
    assert "10.0.0.7" not in str(converted)


def test_with_a_ca_file_the_hint_says_to_check_it(ca_file):
    converted = _failure(ssl.SSLCertVerificationError(1, "x"), tls_ca_file=ca_file)
    assert converted is not None
    assert "tls_ca_file is set" in str(converted)


def test_any_other_failure_is_left_alone():
    assert _failure(ConnectionRefusedError("connection refused")) is None


@pytest.mark.parametrize(
    "option",
    ["tlsInsecure=true", "tlsAllowInvalidCertificates=TRUE", "tlsAllowInvalidHostnames=1",
     "tls=false", "ssl=false"],
)
def test_a_mongodb_uri_cannot_switch_verification_off(monkeypatch, tmp_path, calls, option):
    """Found in review: PyMongo merges URI options with ours, so without this a URI could turn
    verification off while the bundle still said `tls-verified`."""
    with pytest.raises(OperatorError, match="MongoDB URI sets"):
        _collect(monkeypatch, tmp_path, "mongodb",
                 {"uri": f"mongodb://10.0.0.7:27017/?authSource=app&{option}"})
    assert calls == []


def test_a_mongodb_uri_may_state_the_safe_values(monkeypatch, tmp_path, calls):
    with pytest.raises(Connected):
        _collect(monkeypatch, tmp_path, "mongodb",
                 {"uri": "mongodb://10.0.0.7/?tls=true&tlsAllowInvalidCertificates=false"})


def test_a_plaintext_config_with_a_tls_uri_is_a_contradiction(monkeypatch, tmp_path, calls):
    with pytest.raises(OperatorError, match="contradicts"):
        _collect(monkeypatch, tmp_path, "mongodb", {"uri": "mongodb://127.0.0.1/?tls=true"},
                 tls=False)


def test_a_wrapped_certificate_failure_in_a_runtime_error_is_still_explained(
    monkeypatch, tmp_path, calls
):
    """Found in review: RuntimeError used to be caught for the budget first and re-raised raw."""

    def fails(*_: object, **__: object) -> None:
        try:
            raise ssl.SSLCertVerificationError(1, "certificate verify failed")
        except ssl.SSLError as inner:
            raise RuntimeError("driver wrapper") from inner

    sys.modules["pg8000.dbapi"].connect = fails  # type: ignore[attr-defined]  # C5: reason=mypy cannot see attributes set on a fake module; approved=user, 2026-11-08; review=2027-05-08
    with pytest.raises(OperatorError, match="set tls_ca_file"):
        _collect(monkeypatch, tmp_path, "postgresql", PG_REMOTE)


def test_a_reached_limit_is_reported_with_the_limits_to_raise(monkeypatch, tmp_path):
    """Still no bundle, as before: only the report changes from a traceback to the fix."""

    class Client:
        def close(self) -> None:
            pass

    def exhausted(*_: object) -> dict:
        raise runtime.BudgetExhausted

    dbapi = types.ModuleType("pg8000.dbapi")
    dbapi.connect = lambda **_: Client()  # type: ignore[attr-defined]  # C5: reason=mypy cannot see attributes set on a fake module; approved=user, 2026-11-08; review=2027-05-08
    pg8000 = types.ModuleType("pg8000")
    pg8000.dbapi = dbapi  # type: ignore[attr-defined]  # C5: reason=mypy cannot see attributes set on a fake module; approved=user, 2026-11-08; review=2027-05-08
    monkeypatch.setitem(sys.modules, "pg8000", pg8000)
    monkeypatch.setitem(sys.modules, "pg8000.dbapi", dbapi)
    monkeypatch.setattr(runtime, "discover_postgresql", exhausted)
    monkeypatch.setenv("SIGNING_KEY", SIGNING_KEY)
    with pytest.raises(OperatorError, match="max_queries=50") as raised:
        _collect(monkeypatch, tmp_path, "postgresql", PG_LOOPBACK, tls=False)
    assert not (tmp_path / "bundle").exists()
    assert "MongoDB" not in str(raised.value), "the MongoDB cost hint is for MongoDB only"


def test_other_runtime_errors_are_not_disguised(monkeypatch, tmp_path, calls):
    def broken(*_: object, **__: object) -> None:
        raise RuntimeError("a genuine bug")

    sys.modules["pg8000.dbapi"].connect = broken  # type: ignore[attr-defined]  # C5: reason=mypy cannot see attributes set on a fake module; approved=user, 2026-11-08; review=2027-05-08
    with pytest.raises(RuntimeError, match="a genuine bug"):
        _collect(monkeypatch, tmp_path, "postgresql", PG_LOOPBACK, tls=False)
