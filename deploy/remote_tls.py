"""Turn TLS on for the project databases on the remote host (owner ruling D2, 2026-11-11: "Turn
TLS on remotely (Recommended)", "Keep plaintext too (Recommended)", "Create ~/dbig-ca
(Recommended)"). Run on the operator's machine, one stack at a time:

    python3 deploy/remote_tls.py --env .env --stacks-dir <dir> issue      # certificates
    python3 deploy/remote_tls.py --env .env --stacks-dir <dir> apply bench-pg
    python3 deploy/remote_tls.py --env .env --stacks-dir <dir> --foreign-ca <crt> verify bench-pg
    python3 deploy/remote_tls.py --env .env --stacks-dir <dir> restore bench-pg

`apply` backs up, installs and restarts; `verify` needs the private CA to pass and a foreign CA
to be refused; `restore` undoes `apply`.

`--env` holds USER and IP of the key-only SSH route; neither is ever printed. `--stacks-dir` is the
remote folder that holds one folder per stack with its compose.yaml. Each stack gets
`deploy/remote-tls/<stack>/compose.override.yaml`, which Docker Compose merges automatically, and a
`tls/` folder with its certificate. The CA lives in `--ca` (default ~/dbig-ca, mode 0700); its
key never leaves this machine. Keys travel only inside the SSH stream, never through a log.
"""

from __future__ import annotations

import argparse
import io
import re
import shutil
import socket
import ssl
import struct
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = ROOT / "deploy" / "remote-tls"
# Stack -> (engine, TLS ports published on the host). PostgreSQL and MongoDB serve TLS on their
# existing port; ClickHouse gains HTTPS and native TLS ports.
STACKS = {
    "diffbot-pg": ("postgresql", [5401]),
    "bench-pg": ("postgresql", [5405]),
    "zoominfo-ch": ("clickhouse", [8401, 9401]),
    "bench-ch": ("clickhouse", [8402, 9402]),
    "bench-mongo": ("mongodb", [27102]),
}
UP = "docker compose up -d --wait --wait-timeout 120 2>&1 | tail -3"


def read_route(path: Path) -> tuple[str, str]:
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"\s*(?:export\s+)?(USER|IP)\s*=\s*(.*)", line)
        if match:
            values[match.group(1)] = match.group(2).strip().strip("'\"")
    if set(values) != {"USER", "IP"}:
        raise SystemExit(f"{path} must set USER and IP for the SSH route")
    return values["USER"], values["IP"]


def ssh(route: tuple[str, str], command: str, stdin: bytes | None = None) -> str:
    user, ip = route
    executable = shutil.which("ssh")
    if executable is None:
        raise SystemExit("ssh not found; the key-only SSH route needs the OpenSSH client")
    result = subprocess.run(  # noqa: S603 # C5: reason=fixed ssh argv, the remote command is built here from the stack table; approved=owner, 2026-11-11; review=2027-05-11
        [executable, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", f"{user}@{ip}", command],
        input=stdin, capture_output=True, check=False, timeout=600,
    )
    output = (result.stdout + result.stderr).decode(errors="replace")
    output = output.replace(ip, "<host>").replace(user, "<user>")
    if result.returncode != 0:
        raise SystemExit(f"remote step failed (exit {result.returncode}):\n{output}")
    return output


def issue(route: tuple[str, str], ca: Path) -> None:
    ca.mkdir(mode=0o700, exist_ok=True)
    ca.chmod(0o700)
    for stack in STACKS:
        subprocess.run(  # noqa: S603 # C5: reason=fixed argv to this repository's certificate script; approved=owner, 2026-11-11; review=2027-05-11
            [sys.executable, str(ROOT / "scripts" / "make_ci_certs.py"), "--out", str(ca),
             "--name", stack, "--ip", route[1], "--days", "397"],
            check=True, stdout=subprocess.DEVNULL,
        )
    print(f"issued {len(STACKS)} certificates from the CA in {ca}")


def _bundle(stack: str, ca: Path) -> bytes:
    """The files one stack needs, as a tar stream: its override and its tls/ folder."""
    engine = STACKS[stack][0]
    override = (FILES / stack / "compose.override.yaml").read_bytes()
    members = {"compose.override.yaml": (override, 0o644),
               "tls/ca.crt": ((ca / "ca.crt").read_bytes(), 0o644)}
    cert, key = (ca / f"{stack}.crt").read_bytes(), (ca / f"{stack}.key").read_bytes()
    if engine == "mongodb":
        members["tls/server.pem"] = (cert + key, 0o600)
    else:
        members["tls/server.crt"] = (cert, 0o644)
        members["tls/server.key"] = (key, 0o600)
    if engine == "clickhouse":
        members["tls/clickhouse-tls.xml"] = ((FILES / "clickhouse-tls.xml").read_bytes(), 0o644)
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name, (data, mode) in members.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), mode
            archive.addfile(info, io.BytesIO(data))
    return stream.getvalue()


def apply(route: tuple[str, str], stacks_dir: str, stack: str, ca: Path) -> None:
    folder = f"{stacks_dir}/{stack}"
    # Backup first: the stack's compose file and the running container's configuration, which
    # holds the stack's own environment, so the folder is private to the SSH user.
    print(ssh(route, f"set -e; cd {folder}; umask 077; test ! -e compose.override.yaml; "
                     "b=.dbig-tls-backup-$(date -u +%Y%m%dT%H%M%SZ); mkdir $b; "
                     f"cp -p compose.yaml $b/; docker inspect {stack} > $b/container.json; "
                     "echo backup $b"))
    ssh(route, f"set -e; cd {folder}; umask 077; tar -xf -", stdin=_bundle(stack, ca))
    state = "'{{.State.Status}} restarts={{.RestartCount}}'"
    print(ssh(route, f"set -e; cd {folder}; {UP}; docker inspect -f {state} {stack}"))


def restore(route: tuple[str, str], stacks_dir: str, stack: str) -> None:
    folder = f"{stacks_dir}/{stack}"
    print(ssh(route, f"set -e; cd {folder}; rm -f compose.override.yaml; rm -rf tls; {UP}; "
                     f"docker inspect -f '{{{{.State.Status}}}}' {stack}"))


def _handshake(ip: str, port: int, cafile: Path, postgres: bool) -> str:
    context = ssl.create_default_context(cafile=str(cafile))  # verifies the chain and the IP SAN
    try:
        with socket.create_connection((ip, port), timeout=10) as raw:
            if postgres:
                raw.sendall(struct.pack("!II", 8, 80877103))  # SSLRequest, before any login
                if raw.recv(1) != b"S":
                    return "no TLS offered"
            with context.wrap_socket(raw, server_hostname=ip) as tls:
                return f"verified {tls.version()}"
    except ssl.SSLCertVerificationError:
        return "refused: certificate not trusted"
    except (ssl.SSLError, OSError) as error:
        return f"failed ({type(error).__name__})"


def verify(route: tuple[str, str], stack: str, ca: Path, foreign: Path) -> bool:
    engine, ports = STACKS[stack]
    good = True
    for port in ports:
        trusted = _handshake(route[1], port, ca / "ca.crt", engine == "postgresql")
        other = _handshake(route[1], port, foreign, engine == "postgresql")
        good &= trusted.startswith("verified") and other.startswith("refused")
        print(f"{stack} port {port}: private CA {trusted}; foreign CA {other}")
    return good


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env", type=Path, required=True)
    parser.add_argument("--stacks-dir", required=True)
    parser.add_argument("--ca", type=Path, default=Path.home() / "dbig-ca")
    parser.add_argument("--foreign-ca", type=Path,
                        help="verify: a CA certificate that must be refused")
    parser.add_argument("step", choices=("issue", "apply", "verify", "restore"))
    parser.add_argument("stack", nargs="?", choices=sorted(STACKS))
    arguments = parser.parse_args()
    route = read_route(arguments.env)
    if arguments.step == "issue":
        issue(route, arguments.ca)
        return
    if arguments.stack is None:
        raise SystemExit(f"{arguments.step} needs a stack: one of {', '.join(STACKS)}")
    if arguments.step == "apply":
        apply(route, arguments.stacks_dir, arguments.stack, arguments.ca)
    elif arguments.step == "restore":
        restore(route, arguments.stacks_dir, arguments.stack)
    else:
        if arguments.foreign_ca is None:
            raise SystemExit("verify needs --foreign-ca, a CA certificate the database must refuse")
        passed = verify(route, arguments.stack, arguments.ca, arguments.foreign_ca)
        raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
