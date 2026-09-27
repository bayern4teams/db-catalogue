"""Enforce repository rules that are testable before the application exists."""

from __future__ import annotations

import re
import subprocess
import tomllib
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


SETUP_UV_STEP = re.compile(r"^(?P<indent>\s*)-\s+uses:\s*astral-sh/setup-uv@\S+")
STEP_VERSION = re.compile(r"^\s*version:\s*['\"]?(?P<version>[^'\"\s#]+)", re.MULTILINE)
EXACT_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
FLOOR_SPECIFIER = re.compile(r"^>=(?P<floor>\d+\.\d+\.\d+)$")


def _parts(version: str) -> tuple[int, ...]:
    return tuple(int(piece) for piece in version.split("."))


def _uv_floor() -> str:
    """Return the `>=X.Y.Z` floor that uv itself enforces on every invocation."""
    settings = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    required = settings.get("tool", {}).get("uv", {}).get("required-version")
    if not isinstance(required, str):
        raise SystemExit(
            "pyproject.toml must declare [tool.uv] required-version so every uv invocation "
            "fails closed on a uv too old for the supply-chain SBOM export"
        )
    match = FLOOR_SPECIFIER.match(required.strip())
    if match is None:
        raise SystemExit(
            "[tool.uv] required-version must be an exact '>=X.Y.Z' floor that scripts and "
            f"workflows can compare against; found {required!r}"
        )
    return match.group("floor")


def _setup_uv_pins(text: str) -> list[str | None]:
    """Return the pinned `version:` of every setup-uv step, or None when a step omits one."""
    lines = text.splitlines()
    pins: list[str | None] = []
    for number, line in enumerate(lines):
        step = SETUP_UV_STEP.match(line)
        if step is None:
            continue
        depth = len(step.group("indent"))
        body = []
        for following in lines[number + 1 :]:
            if following.strip() and len(following) - len(following.lstrip()) <= depth:
                break
            body.append(following)
        found = STEP_VERSION.search("\n".join(body))
        pins.append(found.group("version") if found else None)
    return pins


def _check_uv_pins() -> None:
    """Keep the CI uv pin explicit, reproducible and at or above the enforced floor."""
    floor = _uv_floor()
    workflows = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
    pinned: dict[str, str] = {}
    for workflow in workflows:
        pins = _setup_uv_pins(workflow.read_text(encoding="utf-8"))
        for index, pin in enumerate(pins, start=1):
            where = f"{workflow.relative_to(ROOT)} setup-uv step {index}"
            if pin is None:
                raise SystemExit(
                    f"{where} must pin an explicit uv 'version:'; an unpinned setup-uv installs "
                    "whatever uv is latest, so the SBOM and license gate is not reproducible"
                )
            if not EXACT_VERSION.match(pin):
                raise SystemExit(f"{where} must pin an exact uv X.Y.Z version, not {pin!r}")
            if _parts(pin) < _parts(floor):
                raise SystemExit(
                    f"{where} pins uv {pin}, below the [tool.uv] required-version floor {floor}"
                )
            pinned[where] = pin
    if not pinned:
        raise SystemExit("No setup-uv step found; the uv pin guard would silently pass")
    distinct = sorted(set(pinned.values()))
    if len(distinct) != 1:
        raise SystemExit(
            "All workflows must install the same uv version so every gate sees one resolver; "
            f"found {', '.join(distinct)}"
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

    _check_uv_pins()

    subprocess.run(("git", "diff", "--check"), cwd=ROOT, check=True)
    subprocess.run(("git", "diff", "--cached", "--check"), cwd=ROOT, check=True)
    print("Repository policy checks passed.")


if __name__ == "__main__":
    main()
