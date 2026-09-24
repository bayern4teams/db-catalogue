"""Enforce repository rules that are testable before the application exists."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = (
    "README.md",
    "LICENSE",
    "TECHNICAL_IMPLEMENTATION.md",
    "ARCHITECTURE_RESEARCH.md",
    "TODO.md",
    "AGENTS.md",
    "supply-chain/licenses.json",
    "CLAUDE.md",
    ".env.example",
    ".githooks/pre-commit",
    ".githooks/post-commit",
)


def main() -> None:
    missing = [name for name in REQUIRED if not (ROOT / name).is_file()]
    if missing:
        raise SystemExit("Missing required files: " + ", ".join(missing))

    tracked = subprocess.run(
        ("git", "ls-files", "-z"), cwd=ROOT, capture_output=True, check=True
    ).stdout.split(b"\0")
    tracked_names = {item.decode("utf-8", "replace") for item in tracked if item}
    forbidden = {
        name
        for name in tracked_names
        if name == ".env"
        or name.startswith("graphify-out/")
        or name.startswith("graft/")
        or name.startswith(".codebase-memory/")
    }
    if forbidden:
        raise SystemExit("Forbidden tracked files: " + ", ".join(sorted(forbidden)))

    if (ROOT / "AGENT.md").exists():
        raise SystemExit("Use only the canonical AGENTS.md; remove AGENT.md")

    for document in (
        "README.md",
        "TECHNICAL_IMPLEMENTATION.md",
        "TODO.md",
        "AGENTS.md",
        "CLAUDE.md",
    ):
        text = (ROOT / document).read_text(encoding="utf-8")
        for target in re.findall(r"\]\(([^)]+)\)", text):
            if target.startswith(("https://", "http://", "#", "mailto:")):
                continue
            path = target.split("#", 1)[0]
            if path and not (ROOT / path).exists():
                raise SystemExit(f"Broken local link in {document}: {target}")

    todo = (ROOT / "TODO.md").read_text(encoding="utf-8")
    if "## 13. Verification record" not in todo or "- [ ]" not in todo:
        raise SystemExit("TODO.md must retain a live verification record and open tasks")
    if (ROOT / "src").exists() and not (ROOT / "pyproject.toml").is_file():
        raise SystemExit(
            "Application source requires pyproject.toml and the application quality gate"
        )
    if (ROOT / "pyproject.toml").exists() and not (ROOT / "uv.lock").is_file():
        raise SystemExit("A deployable Python package requires uv.lock")

    subprocess.run(("git", "diff", "--check"), cwd=ROOT, check=True)
    subprocess.run(("git", "diff", "--cached", "--check"), cwd=ROOT, check=True)
    print("Repository policy checks passed.")


if __name__ == "__main__":
    main()
