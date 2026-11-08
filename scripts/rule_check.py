"""Cheap CI rule checks (AGENTS.md K4): C4 no new TODO/FIXME, J13 a `Docs:` line per source commit,
C5 no exception without its reason, L1 no removed test without a `C5:` commit line, L4 no test
without an assertion.

Only what the change adds is checked, so existing lines and commits never block CI.
Usage: python scripts/rule_check.py [--base REF]   (default REF: origin/main, else main)
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from pathlib import Path

SOURCE = ("packages", "scripts", ":(exclude)**/tests/**", ":(exclude)scripts/rule_check.py")
STUB = re.compile(r"\b(TODO|FIXME)\b(?!\.md)")
DOCS_LINE = re.compile(r"^Docs:", re.MULTILINE)
# The two files below quote exceptions as test data, so they are not checked for them.
PYTHON = ("*.py", ":(exclude)scripts/rule_check.py", ":(exclude)tests/test_rule_check.py")
EXCEPTION = re.compile(
    r"\bnoqa\b|type:\s*ignore|pytest\.skip\(|\bimportorskip\(|mark\.(skip|skipif|xfail)\b"
)
C5_REASON = re.compile(
    r"C5: reason=[^;]+; approved=[^;]+, \d{4}-\d{2}-\d{2}; review=\d{4}-\d{2}-\d{2}"
)
TEST_DEF = re.compile(r"^[-+]\s*(?:async\s+)?def (test_\w+)")
HUNK = re.compile(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@")
CHECKS = {"raises", "warns", "fail", "skip"}


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


def added_lines(base: str, *paths: str):
    """Yield (path, line number, text) for each line the change adds from base to working tree."""
    path, number = "?", 0
    for line in git("diff", "-U0", base, "--", *paths).splitlines():
        if line.startswith("+++ "):
            path = line[6:]
        elif match := HUNK.match(line):
            number = int(match[1])
        elif line.startswith("+"):
            yield path, number, line[1:]
            number += 1


def check_exceptions(base: str) -> list[str]:
    """C5: an added noqa, type: ignore, skip or xfail needs reason, approval and review date."""
    return [
        f"{path}:{number}: {text.strip()}"
        for path, number, text in added_lines(base, *PYTHON)
        if EXCEPTION.search(text) and not C5_REASON.search(text)
    ]


def check_removed_tests(base: str) -> list[str]:
    """L1: a test function the change removes needs a commit-message line starting `C5:`."""
    removed, added = set(), set()
    for line in git("diff", "-U0", base, "--", "*.py").splitlines():
        if match := TEST_DEF.match(line):
            (removed if line[0] == "-" else added).add(match[1])
    gone = sorted(removed - added)
    if gone and not re.search(r"^C5:", git("log", "--format=%B", f"{base}..HEAD"), re.MULTILINE):
        return gone
    return []


def _asserts(function: ast.AST, helpers: frozenset[str] = frozenset()) -> bool:
    for node in ast.walk(function):
        if isinstance(node, ast.Assert):
            return True
        if isinstance(node, ast.Call):
            name = getattr(node.func, "attr", getattr(node.func, "id", ""))
            named = name.lstrip("_").startswith(("assert", "check", "verify"))
            if name in CHECKS | helpers or named:
                return True
    return False


def check_assertions(base: str) -> list[str]:
    """L4: a test function the change adds or edits must assert, raise-check, fail or skip."""
    touched: dict[str, set[int]] = {}
    for path, number, _ in added_lines(base, "tests/**/*.py", "tests/*.py"):
        touched.setdefault(path, set()).add(number)
    found = []
    for path, numbers in touched.items():
        tree = ast.parse(Path(path).read_text(encoding="utf-8"))
        # ponytail: one level only, a helper that merely calls another asserting helper is missed
        helpers = frozenset(
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and not node.name.startswith("test")
            and _asserts(node)
        )
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if node.name.startswith("test"):
                span = set(range(node.lineno, node.end_lineno + 1))
                if span & numbers and not _asserts(node, helpers):
                    found.append(f"{path}:{node.lineno}: {node.name}")
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
    exceptions, removed = check_exceptions(base), check_removed_tests(base)
    bare = check_assertions(base)
    for item in exceptions:
        print(f"C5: exception without a reason line: {item}")
    if exceptions:
        print(
            "Fix: end the line with `C5: reason=<text>; approved=<who, YYYY-MM-DD>; "
            "review=<YYYY-MM-DD>`, or remove the exception."
        )
    for item in removed:
        print(f"L1: test removed without a `C5:` commit line: {item}")
    if removed:
        print("Fix: put `C5: reason=<text>; approved=<who, date>` on its own line in the message.")
    for item in bare:
        print(f"L4: test with no assertion: {item}")
    if bare:
        print("Fix: assert the outcome the test name promises (assert or pytest.raises).")
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
    failed = stubs or missing or exceptions or removed or bare
    if not failed:
        print(f"Rule checks passed against {base[:10]}.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
