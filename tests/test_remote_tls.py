"""Owner ruling D2, 2026-11-11: TLS on the remote project databases with plaintext kept. Checked
without the host: what each stack receives, and that the overrides keep the stack's own start-up
flags and plaintext. The remote run itself is recorded in TODO.md."""

from __future__ import annotations

import importlib.util
import io
import tarfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("remote_tls", ROOT / "deploy" / "remote_tls.py")
remote_tls = importlib.util.module_from_spec(spec)
spec.loader.exec_module(remote_tls)


@pytest.fixture
def ca(tmp_path):
    (tmp_path / "ca.crt").write_bytes(b"CA CERT\n")
    for stack in remote_tls.STACKS:
        (tmp_path / f"{stack}.crt").write_bytes(b"CERT\n")
        (tmp_path / f"{stack}.key").write_bytes(b"KEY\n")
    return tmp_path


def _members(stack, ca):
    with tarfile.open(fileobj=io.BytesIO(remote_tls._bundle(stack, ca))) as archive:
        return {m.name: (m.mode, archive.extractfile(m).read()) for m in archive.getmembers()}


@pytest.mark.parametrize("stack", sorted(remote_tls.STACKS))
def test_each_stack_gets_its_override_and_keys_stay_private(stack, ca):
    members = _members(stack, ca)
    engine = remote_tls.STACKS[stack][0]
    expected = {"compose.override.yaml", "tls/ca.crt"} | (
        {"tls/server.pem"} if engine == "mongodb" else {"tls/server.crt", "tls/server.key"}
    ) | ({"tls/clickhouse-tls.xml"} if engine == "clickhouse" else set())
    assert set(members) == expected
    assert all(mode == 0o600 for name, (mode, data) in members.items() if b"KEY" in data)
    assert not any(b"KEY" in data for name, (_, data) in members.items() if name == "tls/ca.crt")


def _service(stack):
    return next(iter(yaml.safe_load(
        (remote_tls.FILES / stack / "compose.override.yaml").read_text())["services"].values()))


def test_postgres_keeps_its_own_flags_and_plaintext():
    # From docker inspect, 2026-11-11: bench-pg starts with `-c max_connections=200`,
    # diffbot-pg with no flags.
    assert _service("bench-pg")["command"][:3] == ["postgres", "-c", "max_connections=200"]
    assert _service("diffbot-pg")["command"][:3] == ["postgres", "-c", "ssl=on"]
    for stack in ("bench-pg", "diffbot-pg"):
        assert "hostssl" not in str(_service(stack))  # pg_hba is untouched, plaintext stays


def test_mongodb_prefers_tls_and_clickhouse_adds_ports():
    command = _service("bench-mongo")["command"]
    assert command[command.index("--tlsMode") + 1] == "preferTLS"
    for stack in ("zoominfo-ch", "bench-ch"):
        targets = {port.split(":")[1] for port in _service(stack)["ports"]}
        assert targets == {"8443", "9440"}


@pytest.mark.parametrize("stack", sorted(remote_tls.STACKS))
def test_the_database_process_reads_only_the_copies_made_at_start(stack):
    """On the host, tls/ is mode 0700 and owned by the SSH user, so only the root start step can
    read /dbig-tls; a database process that opened it directly failed on bench-mongo, 2026-11-11
    ("cannot read certificate authority file ... Permission denied")."""
    service = _service(stack)
    assert service["volumes"] == ["./tls:/dbig-tls:ro"]  # no file the database reads in place
    assert "/dbig-tls" not in " ".join(service.get("command", []))
