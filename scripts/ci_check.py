"""Enforce repository rules that are testable before the application exists."""

from __future__ import annotations

import ast
import importlib.metadata
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = (
    "README.md",
    "LICENSE",
    "TECHNICAL_IMPLEMENTATION.md",
    "docs/ARCHITECTURE_RESEARCH.md",
    "TODO.md",
    "AGENTS.md",
    "supply-chain/licenses.json",
    "CLAUDE.md",
    ".env.example",
    ".githooks/pre-commit",
    ".githooks/post-commit",
)

# M1: the only files that may sit in the repository root (folders are not checked).
ROOT_FILES = frozenset(
    {
        "AGENTS.md", "CLAUDE.md", "README.md", "ARCHITECTURE.md", "TECHNICAL_IMPLEMENTATION.md",
        "GLOSSARY.md", "TODO.md", "LICENSE", "pyproject.toml", "uv.lock", ".python-version",
        ".gitignore", ".gitattributes", ".ignore", ".gitlab-ci.yml", ".env.example", ".mcp.json",
        ".markdownlint-cli2.jsonc", ".vale.ini",
    }
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


def _graph_tool_pins() -> dict[str, dict[str, str]]:
    settings = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    pins = settings.get("tool", {}).get("dbig", {}).get("graph-tools", {})
    if not pins:
        raise SystemExit(
            "pyproject.toml must declare [tool.dbig.graph-tools] so the graph tool versions CI "
            "installs cannot drift from the versions scripts/graph_sync.py enforces"
        )
    return pins


def _check_graph_tool_pins() -> None:
    """Keep every workflow installing exactly the pinned graph tool versions."""
    workflows = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
    texts = {path: path.read_text(encoding="utf-8") for path in workflows}
    for name, pin in _graph_tool_pins().items():
        version = str(pin["version"])
        distribution = str(pin["distribution"])
        separator = "@" if str(pin["ecosystem"]) == "npm" else "=="
        pattern = re.compile(
            re.escape(distribution) + re.escape(separator) + r"(?P<version>[^\s'\"]+)"
        )
        seen = 0
        for path, text in texts.items():
            for match in pattern.finditer(text):
                seen += 1
                if match.group("version") != version:
                    raise SystemExit(
                        f"{path.relative_to(ROOT)} installs {distribution}{separator}"
                        f"{match.group('version')}, but pyproject.toml [tool.dbig.graph-tools] "
                        f"pins {name} at {version}"
                    )
        if seen == 0:
            raise SystemExit(
                f"No workflow installs the pinned graph tool {distribution}; the graph-index job "
                "would run against whatever version the runner happens to provide"
            )


def _distribution(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirement_names(requirements: list[str]) -> set[str]:
    return {_distribution(re.split(r"[\s<>=!~;\[(]", item, maxsplit=1)[0]) for item in requirements}


def _import_dependency_problems(package: Path) -> list[str]:
    """M4 for one package: imports and declared dependencies must match, in both directions."""
    project = tomllib.loads((package / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    declared = _requirement_names(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        declared |= _requirement_names(extra)
    source = package / "src"
    own = {entry.name for entry in source.iterdir()}
    # ponytail: an import name that no installed distribution provides is matched to the
    # distribution of the same name; add an alias map if CI runs this outside `uv run`
    # and a package's import name differs from its distribution name.
    providers = importlib.metadata.packages_distributions()
    imported: set[str] = set()
    for path in source.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules = [node.module]
            else:
                continue
            imported |= {module.split(".")[0] for module in modules}
    imported -= sys.stdlib_module_names | own
    matched: set[str] = set()
    problems = []
    for top in sorted(imported):
        # Namespace-style modules such as `langgraph` have several providers; one is enough.
        hit = {_distribution(item) for item in providers.get(top, [top])} & declared
        matched |= hit
        if not hit:
            problems.append(f"{package.name}: imports {top} but pyproject.toml does not declare it")
    problems += [
        f"{package.name}: declares {item} but no source file imports it"
        for item in sorted(declared - matched)
    ]
    return problems


def _check_import_dependencies() -> None:
    """Keep each package's declared dependencies equal to what its source imports (M4)."""
    problems = [
        problem
        for manifest in sorted((ROOT / "packages").glob("*/pyproject.toml"))
        for problem in _import_dependency_problems(manifest.parent)
    ]
    if problems:
        raise SystemExit(
            "\n".join(problems)
            + "\nFix: add the missing dependency to the package's pyproject.toml (then `uv lock`), "
            "or remove the unused one."
        )


# Test T1 (report Part 3.4): what every service in deploy/compose.yaml must carry.
HARDENING = {"read_only": True, "cap_drop": ["ALL"], "security_opt": ["no-new-privileges:true"]}
HARDENING_PRESENT = ("user", "mem_limit", "pids_limit", "tmpfs")


def compose_problems(config: dict) -> list[str]:
    """T1 on a parsed compose file (`tests/test_repository_policy.py` loads `deploy/compose.yaml`
    with PyYAML, a dev-only dependency, owner 2026-11-11): every published port binds a private
    `host_ip`, no host network or privileged container, and the hardening keys."""
    import ipaddress

    problems = []
    for name, service in config.get("services", {}).items():
        if service.get("privileged"):
            problems.append(f"{name}: privileged")
        if service.get("network_mode") == "host":
            problems.append(f"{name}: network_mode host")
        for key, value in HARDENING.items():
            if service.get(key) != value:
                problems.append(f"{name}: {key} must be {value}")
        problems += [f"{name}: {key} missing" for key in HARDENING_PRESENT if not service.get(key)]
        for port in service.get("ports", []):
            try:
                address = ipaddress.ip_address(port.get("host_ip", ""))
            except ValueError:
                address = None
            if address is None or address.is_unspecified or not address.is_private:
                problems.append(f"{name}: port {port.get('target')} needs a private host_ip")
    return problems


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

    stray = sorted(name for name in tracked_names if "/" not in name and name not in ROOT_FILES)
    if stray:
        raise SystemExit(
            "Files not allowed in the root (M1): " + ", ".join(stray)
            + "\nFix: move each into its home (docs/, scripts/, ...), or add it to ROOT_FILES in "
            "scripts/ci_check.py with the reason."
        )

    ignored = subprocess.run(
        ("git", "ls-files", "-ci", "--exclude-standard"), cwd=ROOT, capture_output=True, text=True,
        check=True,
    ).stdout.split()
    if ignored:
        raise SystemExit(
            "Tracked files that .gitignore covers (M2): " + ", ".join(ignored)
            + "\nFix: `git rm --cached <file>`, or remove the .gitignore rule."
        )

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
    _check_graph_tool_pins()
    _check_import_dependencies()

    subprocess.run(("git", "diff", "--check"), cwd=ROOT, check=True)
    subprocess.run(("git", "diff", "--cached", "--check"), cwd=ROOT, check=True)
    print("Repository policy checks passed.")


if __name__ == "__main__":
    main()
