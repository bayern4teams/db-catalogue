"""Refresh the three developer code graphs; fail closed on missing tools/errors."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ("graphify", "graft", "codebase-memory-mcp")


def run(*args: str) -> None:
    print("+", *args, flush=True)
    executable = args[0]
    if os.name == "nt" and executable == "graft":
        executable = shutil.which("graft.cmd") or executable
    subprocess.run((executable, *args[1:]), cwd=ROOT, check=True)


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
    args = parser.parse_args()

    missing = [
        name
        for name in TOOLS
        if shutil.which("graft.cmd" if os.name == "nt" and name == "graft" else name)
        is None
    ]
    if missing:
        print("Required graph tools missing: " + ", ".join(missing), file=sys.stderr)
        return 1
    if args.check_tools:
        print("All three graph tools are available.")
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
        run(
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
    except subprocess.CalledProcessError as error:
        print(f"Graph refresh failed (exit {error.returncode}).", file=sys.stderr)
        return error.returncode or 1
    print("Graphify, Graft and Codebase Memory refreshed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
