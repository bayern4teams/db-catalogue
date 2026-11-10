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
