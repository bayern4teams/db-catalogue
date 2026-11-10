"""Live checks of the LAN boundary on the deployment host (report Part 3.4, T4, T5, T7; T6 is
this script again after `systemctl restart docker` and after a reboot). Exits non-zero on any
failure (K4). Run on the LAN host, from the checkout, with the stack up:

    sudo python3 scripts/lan_boundary_check.py --env deploy/.env
    python3 scripts/lan_boundary_check.py --env deploy/.env --only inbound   # from a LAN host

Off-subnet testing (T4's second half) needs a second network position: run `--only inbound`
from there and expect no open port.
"""

from __future__ import annotations

import argparse
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ("collectors", "builder", "requester", "planner", "contracts")
PORT_KEYS = ("COLLECTOR_PORT", "BUILDER_PORT", "REQUESTER_PORT", "PLANNER_PORT", "CONTRACTS_PORT")
DEFAULT_PORTS = ("8441", "8442", "8443", "8444", "8445")
# Blocked targets for T5: a public address on 443, a public DNS server, an IPv6 address.
BLOCKED = (("1.1.1.1", 443), ("8.8.8.8", 53), ("2606:4700:4700::1111", 443))
PROBE = (
    "import socket,sys\n"
    "try:\n"
    "    socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=3).close()\n"
    "    print('open')\n"
    "except OSError:\n"
    "    print('blocked')\n"
)


def _firewall():
    spec = importlib.util.spec_from_file_location("lan_firewall", ROOT / "deploy/lan_firewall.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(*arguments: str) -> str:
    return subprocess.run(  # noqa: S603 # C5: reason=fixed tool names, arguments built here from deploy/.env; approved=owner via lead (Part 3 start), 2026-11-10; review=2027-05-10
        arguments, capture_output=True, text=True, check=False
    ).stdout


def _probe(container: str, host: str, port: int) -> str:
    return _run("docker", "exec", container, "python", "-c", PROBE, host, str(port)).strip()


def inbound(env: dict[str, str]) -> list[str]:
    """T4: from where this runs, only the five service ports are open on the LAN address."""
    if shutil.which("nmap") is None:
        return ["T4: nmap is not installed; install it to scan the published ports"]
    pairs = zip(PORT_KEYS, DEFAULT_PORTS, strict=True)
    expected = {env.get(key, default) for key, default in pairs}
    output = _run("nmap", "-Pn", "-p-", "--open", "-oG", "-", env["LAN_IP"])
    found = {item.split("/")[0] for line in output.splitlines() if "Ports:" in line
             for item in line.split("Ports:")[1].split(",") if "/open/" in item}
    return [] if found <= expected else [f"T4: unexpected open ports {sorted(found - expected)}"]


def egress(env: dict[str, str]) -> list[str]:
    """T5: inside each container, blocked targets time out and each allowed target connects."""
    firewall = _firewall()
    allowed = firewall._targets(env.get("DBIG_EGRESS", ""))
    problems = []
    for service in SERVICES:
        container = f"dbig-{service}-1"
        for host, port in BLOCKED:
            if _probe(container, host, port) != "blocked":
                problems.append(f"T5: {service} reached {host}:{port}")
    for host, port in allowed:
        if _probe("dbig-collectors-1", host, port) != "open":
            problems.append(f"T5: allowed target {host}:{port} did not connect from collectors")
    return problems


def rules(env: dict[str, str]) -> list[str]:
    """T7: the installed DBIG chain is exactly the committed rule list."""
    expected = [" ".join(["-A", "DBIG", *rule]) for rule in _firewall().rules(env)]
    listed = _run("iptables", "-S", "DBIG").splitlines()
    # iptables prints a single address as `/32`; the committed list writes it bare.
    installed = [line.replace("/32 ", " ") for line in listed if line.startswith("-A")]
    if installed == expected:
        return []
    return ["T7: iptables -S DBIG differs from deploy/lan_firewall.py --print; re-run --apply"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env", type=Path, required=True)
    parser.add_argument("--only", choices=("inbound", "egress", "rules"))
    arguments = parser.parse_args()
    env = _firewall().read_env(arguments.env)
    checks = {"inbound": inbound, "egress": egress, "rules": rules}
    selected = [arguments.only] if arguments.only else list(checks)
    problems = [problem for name in selected for problem in checks[name](env)]
    for problem in problems:
        print(problem, file=sys.stderr)
    print(f"{len(selected)} check(s), {len(problems)} problem(s)")
    raise SystemExit(1 if problems else 0)


if __name__ == "__main__":
    main()
