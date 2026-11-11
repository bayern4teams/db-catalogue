"""T7 compares the installed `DBIG` chain with the committed list. The installed lines below are
`iptables -S DBIG` (v1.8.13, nf_tables) after `deploy/lan_firewall.py --apply` with
`deploy/.env.example`, captured 2026-11-11 in a throwaway user and network namespace: iptables
reorders the matches, reorders `--ctstate`, adds `-m tcp` and writes `/32`."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("lan_boundary_check",
                                              ROOT / "scripts" / "lan_boundary_check.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)

INSTALLED = """-N DBIG
-A DBIG -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN
-A DBIG ! -s 192.0.2.0/24 -i eth0 -j DROP
-A DBIG -s 192.0.2.0/24 -d 172.30.0.0/24 -i eth0 -j RETURN
-A DBIG -s 172.30.0.0/24 -d 172.30.0.0/24 -j RETURN
-A DBIG -s 172.30.0.0/24 -d 192.0.2.20/32 -p tcp -m tcp --dport 5432 -j RETURN
-A DBIG -s 172.30.0.0/24 -d 192.0.2.21/32 -p tcp -m tcp --dport 9440 -j RETURN
-A DBIG -s 172.30.0.0/24 -d 192.0.2.30/32 -p tcp -m tcp --dport 8000 -j RETURN
-A DBIG -s 172.30.0.0/24 -j DROP
"""
ENV = check._firewall().read_env(ROOT / "deploy" / ".env.example")


def test_the_chain_iptables_prints_after_apply_matches_the_committed_list():
    assert check.chain_problems(ENV, INSTALLED) == []


def test_a_changed_or_extra_rule_is_reported():
    extra = INSTALLED + "-A DBIG -j ACCEPT\n"
    negated = INSTALLED.replace("! -s 192.0.2.0/24 -i eth0", "-s 192.0.2.0/24 -i eth0")
    port = INSTALLED.replace("--dport 5432", "--dport 5433")
    for listed in (extra, negated, port, INSTALLED.replace("-j DROP\n", "-j RETURN\n")):
        assert check.chain_problems(ENV, listed), listed
