"""Report Part 3.3, layers L2 and L3: the committed firewall rules, checked without a host.

Expected rules are written from the design (accept established; drop LAN-interface traffic from
outside the LAN; let the LAN reach the services; keep service-to-service traffic; allow each
listed target by address and port; drop everything else from the services)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("lan_firewall", ROOT / "deploy" / "lan_firewall.py")
firewall = importlib.util.module_from_spec(spec)
spec.loader.exec_module(firewall)

ENV = {
    "LAN_CIDR": "10.1.0.0/16", "DBIG_SUBNET": "172.30.0.0/24", "LAN_INTERFACE": "eno1",
    "DBIG_EGRESS": "10.1.2.3:5432, 10.1.2.4:8000",
}


def test_the_chain_is_the_design_in_order():
    assert [" ".join(rule) for rule in firewall.rules(ENV)] == [
        "-m conntrack --ctstate ESTABLISHED,RELATED -j RETURN",
        "-i eno1 ! -s 10.1.0.0/16 -j DROP",
        "-i eno1 -s 10.1.0.0/16 -d 172.30.0.0/24 -j RETURN",
        "-s 172.30.0.0/24 -d 172.30.0.0/24 -j RETURN",
        "-s 172.30.0.0/24 -d 10.1.2.3 -p tcp --dport 5432 -j RETURN",
        "-s 172.30.0.0/24 -d 10.1.2.4 -p tcp --dport 8000 -j RETURN",
        "-s 172.30.0.0/24 -j DROP",
    ]


def test_the_example_env_renders():
    env = firewall.read_env(ROOT / "deploy" / ".env.example")
    assert firewall.rules(env)[-1] == ["-s", env["DBIG_SUBNET"], "-j", "DROP"]


@pytest.mark.parametrize(
    "changes",
    [{"DBIG_EGRESS": "8.8.8.8:53"}, {"DBIG_EGRESS": "10.1.2.3"}, {"LAN_CIDR": "0.0.0.0/0"},
     {"DBIG_SUBNET": "fd00::/64"}, {"DBIG_EGRESS": "db.lan:5432"}],
)
def test_a_public_or_malformed_address_is_refused(changes):
    with pytest.raises((SystemExit, ValueError)):
        firewall.rules({**ENV, **changes})


# Review findings 1 and 2 (owner "Yes, fix all", 2026-11-12). The iptables programs are the
# boundary this project does not own, so fakes on PATH record what `--apply` sends them.
FAKE = """#!/usr/bin/env python3
import json, os, sys
stdin = "" if sys.stdin.isatty() else sys.stdin.read()
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps({"tool": os.path.basename(sys.argv[0]), "args": sys.argv[1:],
                          "stdin": stdin}) + "\\n")
sys.exit(1 if "-C" in sys.argv else 0)  # no jump installed yet
"""


def _apply_with_fakes(tmp_path, monkeypatch, env):
    import json
    import sys

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("iptables", "ip6tables", "iptables-restore", "ip6tables-restore"):
        path = bin_dir / tool
        path.write_text(FAKE.replace("/usr/bin/env python3", sys.executable, 1))
        path.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "calls.jsonl"))
    firewall._apply(env)
    return [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]


APPLY_ENV = {**ENV, "DBIG_BRIDGE": "dbig0"}


def test_apply_rebuilds_each_chain_in_one_transaction(tmp_path, monkeypatch):
    """Finding 2: a flush followed by appends leaves an open chain when an append fails; one
    `iptables-restore --noflush` per family either installs the whole chain or nothing."""
    calls = _apply_with_fakes(tmp_path, monkeypatch, APPLY_ENV)
    changing = [c for c in calls if "-C" not in c["args"]]
    assert {c["tool"] for c in changing} == {"iptables-restore", "ip6tables-restore"}
    ipv4 = next(c for c in changing if c["tool"] == "iptables-restore")
    assert "--noflush" in ipv4["args"]
    lines = ipv4["stdin"].splitlines()
    assert lines[0] == "*filter" and lines[-1] == "COMMIT"
    assert f"-A DBIG -s {ENV['DBIG_SUBNET']} -j DROP" in lines
    assert "-I DOCKER-USER 1 -j DBIG" in lines


def test_ipv6_rules_touch_only_our_bridge(tmp_path, monkeypatch):
    """Finding 1: an unscoped IPv6 DROP under DOCKER-USER cut every container on the host."""
    calls = _apply_with_fakes(tmp_path, monkeypatch, APPLY_ENV)
    ipv6 = [c for c in calls if c["tool"].startswith("ip6tables") and "-C" not in c["args"]]
    rules = [line for c in ipv6 for line in c["stdin"].splitlines() if line.startswith("-A DBIG")]
    assert rules and all(" -i dbig0 " in f"{r} " or " -o dbig0 " in f"{r} " for r in rules)
