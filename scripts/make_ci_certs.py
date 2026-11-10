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

The same script issues the services' LAN certificates (owner decision D1, 2026-11-10: "TLS,
private CA"). Run it on the operator's machine, once per service, into one folder: the first run
makes the CA, later runs reuse it. Keep `ca.key` off the LAN host; copy each service's `.crt`
and `.key`, and `ca.crt` as the trust bundle, into `deploy/secrets/`.

    python scripts/make_ci_certs.py --out ../dbig-ca --name requester --ip 172.30.0.12 \
        --ip 192.0.2.10 --days 397
"""

from __future__ import annotations

import argparse
import shutil
import ssl
import subprocess
from ipaddress import ip_address
from pathlib import Path

# The fixtures are reached over loopback, so the certificate must carry an IP SAN. A DNS name
# would not match and `check_hostname` would reject it.
SUBJECT_ALT_NAME = "IP:127.0.0.1,DNS:localhost"
DAYS = "2"


def _openssl(*arguments: str) -> None:
    executable = shutil.which("openssl")
    if executable is None:
        raise SystemExit("openssl is required to generate CI fixture certificates")
    result = subprocess.run(  # noqa: S603 # C5: reason=fixed executable, arguments built here; approved=user, 2026-11-08; review=2027-05-08
        [executable, *arguments], capture_output=True, text=True
    )
    if result.returncode != 0:
        raise SystemExit(
            f"openssl {' '.join(arguments)} failed (exit {result.returncode}):\n{result.stderr}"
        )


def generate(
    out: Path, *, name: str = "server", ips: tuple[str, ...] = (), days: str = DAYS
) -> dict[str, Path]:
    """Write a CA (unless `out` holds one), a server key and certificate for `ips` (default
    loopback), a combined PEM and a trust bundle."""
    out.mkdir(parents=True, exist_ok=True)
    ca_key, ca_crt = out / "ca.key", out / "ca.crt"
    server_key, server_crt = out / f"{name}.key", out / f"{name}.crt"
    server_csr = out / f"{name}.csr"
    alt_names = ",".join(f"IP:{ip_address(ip)}" for ip in ips) if ips else SUBJECT_ALT_NAME
    common_name = str(ip_address(ips[0])) if ips else "127.0.0.1"

    if not (ca_key.is_file() and ca_crt.is_file()):
        # The CI CA keeps its throwaway name; one made for LAN addresses is the private CA.
        _make_ca(ca_key, ca_crt, days, "dbig-private-ca" if ips else "dbig-ci-throwaway-ca")
    _openssl("req", "-newkey", "rsa:2048", "-nodes",
             "-keyout", str(server_key), "-out", str(server_csr),
             "-subj", f"/CN={common_name}")
    # The SAN goes in an extension file: `openssl x509 -req` does not carry extensions over
    # from the request, so without this the certificate would have no SAN and `check_hostname`
    # would reject it however correct the CA chain was.
    extensions = out / f"{name}.ext"
    extensions.write_text(
        f"subjectAltName={alt_names}\nbasicConstraints=CA:FALSE\n"
        "keyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n"
        "authorityKeyIdentifier=keyid,issuer\nsubjectKeyIdentifier=hash\n",
        encoding="utf-8",
    )
    _openssl("x509", "-req", "-in", str(server_csr), "-days", days,
             "-CA", str(ca_crt), "-CAkey", str(ca_key), "-CAcreateserial",
             "-out", str(server_crt), "-extfile", str(extensions))
    server_csr.unlink()
    extensions.unlink()

    # MongoDB wants one file holding key and certificate together.
    combined = out / f"{name}.pem"
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


def _make_ca(ca_key: Path, ca_crt: Path, days: str, common_name: str) -> None:
    _openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", days,
             "-keyout", str(ca_key), "-out", str(ca_crt),
             "-subj", f"/CN={common_name}",
             # Python 3.13 verifies with VERIFY_X509_STRICT, which rejects a CA without
             # keyUsage and a leaf without an authority key identifier.
             "-addext", "basicConstraints=critical,CA:TRUE",
             "-addext", "keyUsage=critical,keyCertSign,cRLSign")
    ca_key.chmod(0o600)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--name", default="server", help="file stem of the server key and cert")
    parser.add_argument("--ip", action="append", default=[], help="an IP SAN; repeat for more")
    parser.add_argument("--days", default=DAYS, help="validity of a new CA and of the cert")
    arguments = parser.parse_args()
    written = generate(
        arguments.out, name=arguments.name, ips=tuple(arguments.ip), days=arguments.days
    )
    for name, path in written.items():
        print(f"{name}={path}")


if __name__ == "__main__":
    main()
