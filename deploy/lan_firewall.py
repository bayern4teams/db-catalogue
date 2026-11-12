"""LAN boundary firewall for the five services, as code (report Part 3.3, layers L2 and L3; B10).

Rules go in a `DBIG` chain that `DOCKER-USER` jumps to. Each run rebuilds the chain in one
`iptables-restore --noflush` transaction per address family, so the installed rules are exactly
the printed list (test T7) and a failed run leaves the previous rules in place. Docker's
`iptables` backend is required: the `nftables` backend has no `DOCKER-USER` chain
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


def _restore_input(chain: list[list[str]], add_jump: bool) -> str:
    """One `iptables-restore --noflush` transaction: declaring the chain empties it, the rules
    refill it, and the jump from DOCKER-USER is added only when it is missing. The kernel applies
    the whole table or nothing, so a failed rule leaves the previous chain in place."""
    lines = ["*filter", f":{CHAIN} - [0:0]"]
    lines += [" ".join(["-A", CHAIN, *rule]) for rule in chain]
    if add_jump:
        lines.append(f"-I DOCKER-USER 1 -j {CHAIN}")
    return "\n".join([*lines, "COMMIT"]) + "\n"


def ipv6_rules(env: dict[str, str]) -> list[list[str]]:
    """The compose network has no IPv6 (`enable_ipv6: false`), so IPv6 to or from our bridge is
    dropped; other containers on the host are not touched (B4)."""
    bridge = env["DBIG_BRIDGE"]
    return [["-i", bridge, "-j", "DROP"], ["-o", bridge, "-j", "DROP"]]


def _run(arguments: list[str], stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 # C5: reason=fixed argv, no user input; approved=owner, 2026-11-10; review=2027-05-10
        arguments, input=stdin, capture_output=True, text=True, check=False
    )


def _apply(env: dict[str, str]) -> None:
    for tool, chain in (("iptables", rules(env)), ("ip6tables", ipv6_rules(env))):
        executable, restore = shutil.which(tool), shutil.which(f"{tool}-restore")
        if executable is None or restore is None:
            raise SystemExit(f"{tool} not found; install it, Docker's iptables backend needs it")
        # `-C` exits non-zero when the jump is missing, including on a first run with no chain.
        has_jump = _run([executable, "-C", "DOCKER-USER", "-j", CHAIN]).returncode == 0
        result = _run([restore, "--noflush"], _restore_input(chain, not has_jump))
        if result.returncode != 0:
            raise SystemExit(f"{tool}-restore failed, previous rules kept: {result.stderr.strip()}")


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
