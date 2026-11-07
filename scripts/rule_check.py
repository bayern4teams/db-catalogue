"""Cheap CI rule checks (AGENTS.md K4): C4 no new TODO/FIXME, J13 a `Docs:` line per source commit.

Only what the change adds is checked, so existing lines and commits never block CI.
Usage: python scripts/rule_check.py [--base REF]   (default REF: origin/main, else main)
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys

SOURCE = ("packages", "scripts", ":(exclude)**/tests/**", ":(exclude)scripts/rule_check.py")
STUB = re.compile(r"\b(TODO|FIXME)\b(?!\.md)")
DOCS_LINE = re.compile(r"^Docs:", re.MULTILINE)


def git(*args: str) -> str:
    return subprocess.run(("git", *args), check=True, capture_output=True, text=True).stdout  # noqa: S603


def default_base() -> str:
    for ref in ("origin/main", "main"):
        probe = ("git", "rev-parse", "--verify", "-q", ref)
        if subprocess.run(probe, capture_output=True).returncode == 0:  # noqa: S603
            return ref
    raise SystemExit("No origin/main or main to compare with; pass --base REF (fetch main first).")


def check_stubs(base: str) -> list[str]:
    """C4: lines this change adds (base to working tree) that hold TODO or FIXME."""
    found, path = [], "?"
    for line in git("diff", "-U0", base, "--", *SOURCE).splitlines():
        if line.startswith("+++ "):
            path = line[6:]
        elif line.startswith("+") and STUB.search(line) and "ponytail:" not in line:
            found.append(f"{path}: {line[1:].strip()}")
    return found


def check_docs_lines(base: str) -> list[str]:
    """J13: every non-merge commit from base to HEAD that touches source needs a `Docs:` line."""
    bad = []
    for sha in git("log", "--no-merges", "--format=%H", f"{base}..HEAD").split():
        if git("diff-tree", "--no-commit-id", "--name-only", "-r", sha, "--", *SOURCE).strip():
            if not DOCS_LINE.search(git("log", "-1", "--format=%B", sha)):
                bad.append(f"{sha[:10]} {git('log', '-1', '--format=%s', sha).strip()}")
    return bad


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=None)
    base = parser.parse_args().base or default_base()
    base = git("merge-base", base, "HEAD").strip()

    stubs, missing = check_stubs(base), check_docs_lines(base)
    for item in stubs:
        print(f"C4: added TODO/FIXME in source: {item}")
    if stubs:
        print("Fix: implement the behaviour now, or name the known limit in a `ponytail:` comment.")
    for item in missing:
        print(f"J13: commit has no `Docs:` line: {item}")
    if missing:
        print(
            "Fix: add a line such as `Docs: none, internal refactor` or "
            "`Docs: TECHNICAL_IMPLEMENTATION.md, Workflows` to the message "
            "(`git commit --amend` before pushing)."
        )
    if not (stubs or missing):
        print(f"Rule checks passed against {base[:10]}.")
    return 1 if stubs or missing else 0


if __name__ == "__main__":
    sys.exit(main())
