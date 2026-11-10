"""LAN boundary firewall for the five services, as code (report Part 3.3, layers L2 and L3; B10).

Rules go in a `DBIG` chain that `DOCKER-USER` jumps to. The chain is flushed and rebuilt each
run, so the installed rules are exactly the printed list (test T7). Docker's `iptables` backend
is required: the `nftables` backend has no `DOCKER-USER` chain
(https://docs.docker.com/engine/network/firewall-nftables/).

    python3 deploy/lan_firewall.py --env deploy/.env --print     # show the rules, change nothing
    sudo python3 deploy/lan_firewall.py --env deploy/.env --apply  # install (owner yes first)

`deploy/dbig-firewall.service` runs `--apply` after Docker starts. Rule survival after a Docker
restart is not stated in Docker's docs, so `scripts/lan_boundary_check.py` tests it (T6).
"""

from __future__ import annotations

import argparse
import ipaddress
import shutil
import subprocess
import sys
from pathlib import Path

CHAIN = "DBIG"


def read_env(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"')
    return values


def _network(text: str, name: str) -> ipaddress.IPv4Network:
    network = ipaddress.ip_network(text, strict=True)
    if not isinstance(network, ipaddress.IPv4Network) or not network.is_private:
        raise SystemExit(f"{name}={text} must be a private IPv4 network; fix deploy/.env")
    return network


def _targets(text: str) -> list[tuple[str, int]]:
    """`DBIG_EGRESS`: comma-separated `ip:port` pairs, each a private IPv4 literal."""
    found = []
    for item in filter(None, (part.strip() for part in text.split(","))):
        host, _, port = item.rpartition(":")
        address = ipaddress.ip_address(host)
        if address.version != 4 or not address.is_private or not port.isdecimal():
            raise SystemExit(f"DBIG_EGRESS entry {item} must be a private IPv4 ip:port")
        found.append((str(address), int(port)))
    return found


def rules(env: dict[str, str]) -> list[list[str]]:
    """The `DBIG` chain, in order. Inbound: only the LAN reaches the published ports. Outbound:
    the compose subnet reaches only the listed database, model and Langfuse targets."""
    lan = _network(env["LAN_CIDR"], "LAN_CIDR")
    subnet = _network(env["DBIG_SUBNET"], "DBIG_SUBNET")
    interface = env["LAN_INTERFACE"]
    chain: list[list[str]] = [
        ["-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED", "-j", "RETURN"],
        # L2: a new connection arriving on the LAN interface from outside the LAN is dropped.
        ["-i", interface, "!", "-s", str(lan), "-j", "DROP"],
        ["-i", interface, "-s", str(lan), "-d", str(subnet), "-j", "RETURN"],
        # Traffic between the services stays on their bridge.
        ["-s", str(subnet), "-d", str(subnet), "-j", "RETURN"],
    ]
    # L3: each allowed target by exact address and port; DNS is not among them (IP literals only).
    for host, port in _targets(env.get("DBIG_EGRESS", "")):
        chain.append(
            ["-s", str(subnet), "-d", host, "-p", "tcp", "--dport", str(port), "-j", "RETURN"]
        )
    chain.append(["-s", str(subnet), "-j", "DROP"])
    return chain


def commands(env: dict[str, str]) -> list[list[str]]:
    return [
        ["-N", CHAIN],
        ["-F", CHAIN],
        *(["-A", CHAIN, *rule] for rule in rules(env)),
        ["-D", "DOCKER-USER", "-j", CHAIN],
        ["-I", "DOCKER-USER", "1", "-j", CHAIN],
    ]


def _apply(env: dict[str, str]) -> None:
    for tool, table in (("iptables", commands(env)), ("ip6tables", None)):
        executable = shutil.which(tool)
        if executable is None:
            raise SystemExit(f"{tool} not found; install it, Docker's iptables backend needs it")
        steps = table if table is not None else [
            # IPv6: the compose network has none, so every forwarded IPv6 packet is dropped.
            ["-N", CHAIN], ["-F", CHAIN], ["-A", CHAIN, "-j", "DROP"],
            ["-D", "DOCKER-USER", "-j", CHAIN], ["-I", "DOCKER-USER", "1", "-j", CHAIN],
        ]
        for step in steps:
            # `-N` fails on an existing chain and `-D` on a missing jump: both are expected.
            result = subprocess.run(  # noqa: S603 # C5: reason=fixed executable, arguments built here from validated addresses; approved=owner via lead (Part 3 start), 2026-11-10; review=2027-05-10
                [executable, *step], capture_output=True, text=True
            )
            if result.returncode != 0 and step[0] not in {"-N", "-D"}:
                raise SystemExit(f"{tool} {' '.join(step)} failed: {result.stderr.strip()}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--print", action="store_true", help="print the DBIG chain, change nothing")
    mode.add_argument("--apply", action="store_true", help="install the rules (needs root)")
    arguments = parser.parse_args()
    env = read_env(arguments.env)
    if arguments.print:
        for rule in rules(env):
            print(" ".join(["-A", CHAIN, *rule]))
        return
    _apply(env)
    print(f"{CHAIN} chain installed and jumped to from DOCKER-USER", file=sys.stderr)


if __name__ == "__main__":
    main()
