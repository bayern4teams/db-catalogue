"""Refresh the three developer code graphs; fail closed on missing/mismatched tools or errors."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ("graphify", "graft", "codebase-memory-mcp")
VERSION_IN_OUTPUT = re.compile(r"(?<![\w.])(\d+\.\d+\.\d+)(?![\w.])")


def executable(name: str) -> str:
    """Resolve a tool command, accounting for npm's Windows shim."""
    return "graft.cmd" if os.name == "nt" and name == "graft" else name


def pinned_tools() -> dict[str, dict[str, str]]:
    """Read the single source of truth for graph tool versions."""
    settings = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    pins = settings.get("tool", {}).get("dbig", {}).get("graph-tools", {})
    missing = [name for name in TOOLS if name not in pins]
    if missing:
        raise SystemExit(
            "pyproject.toml [tool.dbig.graph-tools] must pin every graph tool; missing: "
            + ", ".join(missing)
        )
    return pins


def reported_version(name: str) -> str:
    """Return the X.Y.Z a tool reports, ignoring any upgrade notice it prints alongside."""
    result = subprocess.run(  # noqa: S603 # C5: reason=fixed argv, no shell; approved=user, 2026-11-08; review=2027-05-08
        (shutil.which(executable(name)) or executable(name), "--version"),
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(
            f"{name} --version failed (exit {result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip() or '(no output)'}"
        )
    found = VERSION_IN_OUTPUT.search(result.stdout) or VERSION_IN_OUTPUT.search(result.stderr)
    if found is None:
        raise SystemExit(f"could not read a version from `{name} --version`")
    return found.group(1)


def check_versions() -> None:
    """Refuse to index unless every graph tool is exactly the pinned version."""
    pins = pinned_tools()
    wrong = []
    for name in TOOLS:
        expected = str(pins[name]["version"])
        actual = reported_version(name)
        if actual != expected:
            ecosystem = str(pins[name]["ecosystem"])
            distribution = str(pins[name]["distribution"])
            install = (
                f"npm install --global {distribution}@{expected}"
                if ecosystem == "npm"
                else f"python -m pip install {distribution}=={expected}"
            )
            wrong.append(f"{name} is {actual}, expected {expected} (install with: {install})")
    if wrong:
        raise SystemExit(
            "Graph tool versions do not match the pins in pyproject.toml "
            "[tool.dbig.graph-tools]; a differently versioned tool would produce a graph that "
            "CI cannot reproduce:\n  " + "\n  ".join(wrong)
        )


def run(*args: str) -> None:
    print("+", *args, flush=True)
    command = executable(args[0])
    if os.name == "nt" and args[0] == "graft":
        command = shutil.which("graft.cmd") or command
    subprocess.run((command, *args[1:]), cwd=ROOT, check=True)  # noqa: S603 # C5: reason=fixed argv, no shell; approved=user, 2026-11-08; review=2027-05-08


def refresh_codebase_memory() -> None:
    command = (
        "codebase-memory-mcp",
        "cli",
        "index_repository",
        "--repo-path",
        str(ROOT),
        "--mode",
        "fast",
        "--persistence",
        "false",
    )
    for attempt in range(3):
        print("+", *command, flush=True)
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)  # noqa: S603 # C5: reason=fixed argv, no shell; approved=user, 2026-11-08; review=2027-05-08
        print(result.stdout, end="")
        print(result.stderr, end="", file=sys.stderr)
        if result.returncode == 0:
            return
        if (
            "another index operation for this project is active"
            not in result.stdout + result.stderr
            or attempt == 2
        ):
            raise subprocess.CalledProcessError(result.returncode, command)
        time.sleep(2)


def staged_paths() -> set[str]:
    result = subprocess.run(
        ("git", "diff", "--cached", "--name-only", "-z"),
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return {p.decode("utf-8", errors="surrogateescape") for p in result.stdout.split(b"\0") if p}


def unstaged_paths() -> set[str]:
    result = subprocess.run(
        ("git", "diff", "--name-only", "-z"),
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return {p.decode("utf-8", errors="surrogateescape") for p in result.stdout.split(b"\0") if p}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-tools", action="store_true")
    parser.add_argument("--pre-commit", action="store_true")
    parser.add_argument(
        "--post-commit", action="store_true", help="fast refresh; warn on failure, exit 0"
    )
    parser.add_argument(
        "--session-start",
        action="store_true",
        help="refresh at session start; same as --post-commit",
    )
    args = parser.parse_args()

    if args.post_commit or args.session_start:
        if os.environ.get("SKIP_INDEX_HOOK"):
            return 0
        # Warn-only mode: a stale graph must never block a commit or a session.
        try:
            strict = [sys.executable, str(Path(__file__).resolve())]
            result = subprocess.run(strict, cwd=ROOT, capture_output=True, text=True)  # noqa: S603 # C5: reason=fixed argv, no shell; approved=user, 2026-11-08; review=2027-05-08
        except OSError as error:
            print(f"warning: graph refresh did not start: {error}", file=sys.stderr)
            return 0
        if result.returncode != 0:
            detail = (result.stderr.strip() or result.stdout.strip()).splitlines()
            print(
                "warning: graph refresh failed; fix with `python scripts/graph_sync.py` "
                f"(strict mode shows the tool): {detail[-1] if detail else 'no output'}",
                file=sys.stderr,
            )
        else:
            print(result.stdout.strip().splitlines()[-1])
        return 0

    missing = [name for name in TOOLS if shutil.which(executable(name)) is None]
    if missing:
        print("Required graph tools missing: " + ", ".join(missing), file=sys.stderr)
        return 1
    check_versions()
    if args.check_tools:
        print("All three graph tools are available at their pinned versions.")
        return 0

    if args.pre_commit:
        partial = staged_paths() & unstaged_paths()
        if partial:
            print(
                "Commit blocked: staged files also have unstaged edits; graph tools "
                "would index a different tree. Stage or discard the unstaged edits: "
                + ", ".join(sorted(partial)),
                file=sys.stderr,
            )
            return 1

    try:
        # Graphify's incremental update is structural; do not request semantic/deep passes.
        run("graphify", "update", ".")
        run("graft", "build", ".")
        run("graft", "check", ".")
        refresh_codebase_memory()
    except subprocess.CalledProcessError as error:
        print(f"Graph refresh failed (exit {error.returncode}).", file=sys.stderr)
        return error.returncode or 1
    print("Graphify, Graft and Codebase Memory refreshed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
