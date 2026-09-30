"""Generate a throwaway CA and server certificates for the CI source containers.

`collect` requires verified TLS by contract, so a plaintext fixture container can never exercise
it. These certificates let the CI job start PostgreSQL, ClickHouse and MongoDB with TLS that a
real `ssl.create_default_context()` will actually verify, which is the point: a test that
disabled verification would prove nothing about the path a deployment takes.

Everything here is ephemeral and public by construction. The CA exists for the lifetime of one
CI job, signs only `127.0.0.1`, and is written into the workspace, never committed. It is not a
secret and must never be reused anywhere.

The trust bundle is the system CA bundle with this CA appended, rather than this CA alone,
because `SSL_CERT_FILE` replaces the whole trust store: pointing it at a lone throwaway CA would
break every other TLS connection in the job.

    uv run python scripts/make_ci_certs.py --out /tmp/certs
"""

from __future__ import annotations

import argparse
import shutil
import ssl
import subprocess
from pathlib import Path

# The fixtures are reached over loopback, so the certificate must carry an IP SAN. A DNS name
# would not match and `check_hostname` would reject it.
SUBJECT_ALT_NAME = "IP:127.0.0.1,DNS:localhost"
DAYS = "2"


def _openssl(*arguments: str) -> None:
    executable = shutil.which("openssl")
    if executable is None:
        raise SystemExit("openssl is required to generate CI fixture certificates")
    result = subprocess.run(  # noqa: S603 - fixed executable, arguments built here
        [executable, *arguments], capture_output=True, text=True
    )
    if result.returncode != 0:
        raise SystemExit(
            f"openssl {' '.join(arguments)} failed (exit {result.returncode}):\n{result.stderr}"
        )


def generate(out: Path) -> dict[str, Path]:
    """Write a CA, a server key and certificate, a combined PEM and a trust bundle."""
    out.mkdir(parents=True, exist_ok=True)
    ca_key, ca_crt = out / "ca.key", out / "ca.crt"
    server_key, server_csr, server_crt = out / "server.key", out / "server.csr", out / "server.crt"

    _openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", DAYS,
             "-keyout", str(ca_key), "-out", str(ca_crt),
             "-subj", "/CN=dbig-ci-throwaway-ca")
    _openssl("req", "-newkey", "rsa:2048", "-nodes",
             "-keyout", str(server_key), "-out", str(server_csr),
             "-subj", "/CN=127.0.0.1")
    # The SAN goes in an extension file: `openssl x509 -req` does not carry extensions over
    # from the request, so without this the certificate would have no SAN and `check_hostname`
    # would reject it however correct the CA chain was.
    extensions = out / "server.ext"
    extensions.write_text(
        f"subjectAltName={SUBJECT_ALT_NAME}\nbasicConstraints=CA:FALSE\n"
        "keyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n",
        encoding="utf-8",
    )
    _openssl("x509", "-req", "-in", str(server_csr), "-days", DAYS,
             "-CA", str(ca_crt), "-CAkey", str(ca_key), "-CAcreateserial",
             "-out", str(server_crt), "-extfile", str(extensions))
    server_csr.unlink()
    extensions.unlink()

    # MongoDB wants one file holding key and certificate together.
    combined = out / "server.pem"
    combined.write_bytes(server_key.read_bytes() + server_crt.read_bytes())

    # The system store plus this CA, so other TLS in the job keeps working.
    bundle = out / "trust-bundle.crt"
    system = Path(ssl.get_default_verify_paths().cafile or "")
    parts = [system.read_bytes()] if system.is_file() else []
    parts.append(ca_crt.read_bytes())
    bundle.write_bytes(b"\n".join(parts))

    # PostgreSQL refuses a key readable by anyone else, and the container runs as its own user.
    server_key.chmod(0o600)
    combined.chmod(0o600)
    return {
        "ca": ca_crt,
        "key": server_key,
        "cert": server_crt,
        "combined": combined,
        "bundle": bundle,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    written = generate(parser.parse_args().out)
    for name, path in written.items():
        print(f"{name}={path}")


if __name__ == "__main__":
    main()
