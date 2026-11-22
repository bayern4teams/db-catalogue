"""The plain-HTTP LAN opt-in (owner 2026-11-21, ADR-001) and `${NAME}` config settings.

The opt-in is read from `DBIG_ALLOW_PLAIN_LAN` at call time, so one variable turns it on for
the deployment and off everywhere else; every rule it relaxes keeps the rest of its check
(a public address, an all-interfaces bind and a name are still refused with the flag set).
`load_toml_section` expands `${NAME}` from the environment inside quoted strings only
(owner decision 11: plain settings live in `deploy/.env`, committed files keep placeholders).
"""

from __future__ import annotations

from ipaddress import IPv4Address

import pytest
from dbig_contracts.errors import OperatorError, load_toml_section
from dbig_contracts.security import bind_refusal, private_base_url

PLAIN = "DBIG_ALLOW_PLAIN_LAN"


@pytest.fixture
def plain_on(monkeypatch):
    monkeypatch.setenv(PLAIN, "1")


@pytest.fixture
def plain_off(monkeypatch):
    monkeypatch.delenv(PLAIN, raising=False)


def test_without_the_flag_a_private_bind_still_needs_tls(plain_off):
    assert "--tls-cert" in bind_refusal("172.30.0.10", tls=False)


def test_with_the_flag_a_private_bind_may_serve_plain_http(plain_on):
    assert bind_refusal("172.30.0.10", tls=False) is None


def test_with_the_flag_public_and_all_interfaces_still_never_bind(plain_on):
    assert "private" in bind_refusal("8.8.8.8", tls=False)
    assert "all interfaces" in bind_refusal(str(IPv4Address(0)), tls=False)


def test_without_the_flag_a_private_http_endpoint_is_refused_by_name(plain_off):
    with pytest.raises(ValueError, match=PLAIN):
        private_base_url("http://10.10.9.45:8000/v1")


def test_with_the_flag_a_private_http_endpoint_passes(plain_on):
    assert private_base_url("http://10.10.9.45:8000/v1") == "http://10.10.9.45:8000/v1"


def test_with_the_flag_https_and_loopback_still_pass(plain_on):
    assert private_base_url("https://10.10.9.45:8000/v1") == "https://10.10.9.45:8000/v1"
    assert private_base_url("http://127.0.0.1:8000/v1") == "http://127.0.0.1:8000/v1"


def test_with_the_flag_a_public_http_endpoint_is_still_refused(plain_on):
    with pytest.raises(ValueError, match="private address"):
        private_base_url("http://8.8.8.8:8000/v1")


def _toml(tmp_path, text: str):
    path = tmp_path / "svc.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_placeholder_reads_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("DBIG_TEST_URL", "http://10.10.9.45:8000/v1")
    values = load_toml_section(
        _toml(tmp_path, '[s]\nbase_url = "${DBIG_TEST_URL}"\nport = 8000\n'), "s"
    )
    assert values == {"base_url": "http://10.10.9.45:8000/v1", "port": 8000}


def test_a_dollar_dollar_writes_a_literal_placeholder(tmp_path):
    values = load_toml_section(_toml(tmp_path, '[s]\nk = "$${KEEP_ME}"\n'), "s")
    assert values["k"] == "${KEEP_ME}"


def test_a_value_needing_escapes_cannot_break_its_string(tmp_path, monkeypatch):
    monkeypatch.setenv("DBIG_TEST_ODD", 'a"b\\c\nd')
    values = load_toml_section(_toml(tmp_path, '[s]\nk = "${DBIG_TEST_ODD}"\n'), "s")
    assert values["k"] == 'a"b\\c\nd'


def test_an_unset_variable_names_the_setting_and_the_fix(tmp_path, monkeypatch):
    monkeypatch.delenv("DBIG_TEST_MISSING", raising=False)
    with pytest.raises(OperatorError, match="DBIG_TEST_MISSING") as caught:
        load_toml_section(_toml(tmp_path, '[s]\nk = "${DBIG_TEST_MISSING}"\n'), "s")
    assert "deploy/.env" in str(caught.value)
