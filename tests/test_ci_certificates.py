"""The CI TLS fixture certificates must satisfy a real verifying client.

`collect` connects with `ssl.create_default_context()`, which verifies the chain *and* the
hostname. The CI source containers were plaintext, so that path was never exercised; enabling
TLS on them is only worth anything if the certificates actually verify, and a test that turned
verification off would prove the opposite of what is wanted.

No container is involved here. A plain TLS socket is enough to prove the two things that can go
wrong independently of any database: the chain is trusted through the generated bundle, and the
certificate matches the loopback address the fixtures are reached on. Those are also the two
things that cannot be checked in this workspace any other way, since it has no container runtime.
"""

from __future__ import annotations

import socket
import ssl
import threading
from pathlib import Path

import pytest

MAKE_CERTS = Path(__file__).parents[1] / "scripts" / "make_ci_certs.py"


def _generate(out: Path) -> dict[str, Path]:
    import importlib.util

    spec = importlib.util.spec_from_file_location("make_ci_certs", MAKE_CERTS)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.generate(out)


class _Server:
    """A TLS listener on loopback presenting the generated server certificate."""

    def __init__(self, certificate: Path, key: Path) -> None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=str(certificate), keyfile=str(key))
        self._socket = socket.socket()
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(("127.0.0.1", 0))
        self._socket.listen(1)
        self.port = self._socket.getsockname()[1]
        self._context = context
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        try:
            connection, _ = self._socket.accept()
        except OSError:
            return
        try:
            with self._context.wrap_socket(connection, server_side=True) as stream:
                stream.recv(16)
                stream.sendall(b"ok")
        except (OSError, ssl.SSLError):
            pass

    def close(self) -> None:
        self._socket.close()


@pytest.fixture
def certificates(tmp_path: Path) -> dict[str, Path]:
    return _generate(tmp_path / "certs")


def test_the_generated_certificate_carries_a_loopback_address(certificates):
    """A DNS-only certificate would fail hostname checking against 127.0.0.1, which is the
    address every fixture is reached on."""
    text = certificates["cert"].read_bytes()
    loaded = ssl._ssl._test_decode_cert(str(certificates["cert"]))  # noqa: SLF001
    names = {value for kind, value in loaded.get("subjectAltName", ()) if kind == "IP Address"}
    assert "127.0.0.1" in names
    assert b"BEGIN CERTIFICATE" in text


def test_a_verifying_client_trusts_the_server_through_the_bundle(certificates):
    """The whole point: full verification succeeds, with hostname checking left on."""
    server = _Server(certificates["cert"], certificates["key"])
    try:
        context = ssl.create_default_context(cafile=str(certificates["bundle"]))
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname is True
        with socket.create_connection(("127.0.0.1", server.port), timeout=10) as raw:
            with context.wrap_socket(raw, server_hostname="127.0.0.1") as stream:
                stream.sendall(b"ping")
                assert stream.recv(16) == b"ok"
    finally:
        server.close()


def test_a_client_without_the_bundle_refuses_the_server(certificates):
    """Proves the test above is not passing because verification is somehow disabled."""
    server = _Server(certificates["cert"], certificates["key"])
    try:
        context = ssl.create_default_context()
        with socket.create_connection(("127.0.0.1", server.port), timeout=10) as raw:
            with pytest.raises(ssl.SSLCertVerificationError):
                context.wrap_socket(raw, server_hostname="127.0.0.1")
    finally:
        server.close()


def test_the_bundle_keeps_the_system_trust_store(certificates):
    """`SSL_CERT_FILE` replaces the whole store, so a bundle holding only the throwaway CA would
    break every other TLS connection in the CI job."""
    system = Path(ssl.get_default_verify_paths().cafile or "")
    if not system.is_file():
        pytest.skip("no system CA bundle to compare against")
    bundle = certificates["bundle"].read_bytes()
    assert bundle.count(b"BEGIN CERTIFICATE") > system.read_bytes().count(b"BEGIN CERTIFICATE")
    assert certificates["ca"].read_bytes().strip() in bundle


def test_the_private_key_is_not_world_readable(certificates):
    """PostgreSQL refuses to start with a group- or world-readable key file."""
    for name in ("key", "combined"):
        mode = certificates[name].stat().st_mode & 0o077
        assert mode == 0, f"{name} must not be readable by group or others"
