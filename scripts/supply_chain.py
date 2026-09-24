"""Export the locked SBOM and a reviewed, pinned-release license inventory."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "supply-chain" / "licenses.json"
OVERRIDES = ROOT / "supply-chain" / "license-overrides.json"


def _component_key(component: dict[str, object]) -> str:
    return f"{component['name']}=={component['version']}"


def _release_metadata(component: dict[str, object]) -> tuple[str, dict[str, object]]:
    name = str(component["name"])
    version = str(component["version"])
    url = f"https://pypi.org/pypi/{quote(name, safe='')}/{quote(version, safe='')}/json"
    request = Request(  # noqa: S310 - URL is constructed with a fixed HTTPS PyPI origin.
        url, headers={"Accept": "application/json", "User-Agent": "dbig-license-inventory/1.0"}
    )
    with urlopen(request, timeout=20) as response:  # noqa: S310
        data = json.load(response)
    info = data["info"]
    if info["version"] != version:
        raise ValueError(f"PyPI release mismatch for {name}")
    expression = info.get("license_expression") or ""
    legacy = info.get("license") or ""
    classifiers = sorted(
        value for value in info.get("classifiers", []) if value.startswith("License ::")
    )
    if expression:
        declaration, evidence = expression, "spdx-expression"
    elif legacy and len(legacy) <= 120 and "\n" not in legacy:
        declaration, evidence = legacy, "legacy-license-field"
    elif classifiers:
        declaration, evidence = "; ".join(classifiers), "classifier-review"
    elif legacy:
        declaration, evidence = "Legacy license text; manual review required", "legacy-text-review"
    else:
        declaration, evidence = "UNDECLARED", "missing"
    return _component_key(component), {
        "declaration": declaration,
        "evidence": evidence,
        "release_url": url,
    }


def _export_sbom(path: Path) -> list[dict[str, object]]:
    command = [
        "uv", "export", "--locked", "--all-packages", "--all-extras", "--all-groups",
        "--format", "cyclonedx1.5", "--output-file", str(path), "--quiet",
    ]
    subprocess.run(command, cwd=ROOT, capture_output=True, check=True)  # noqa: S603
    sbom = json.loads(path.read_text(encoding="utf-8"))
    if sbom.get("bomFormat") != "CycloneDX" or sbom.get("specVersion") != "1.5":
        raise ValueError("unexpected SBOM format")
    return sbom["components"]


def _workspace_license(component: dict[str, object]) -> dict[str, str]:
    properties = {
        item["name"]: item["value"] for item in component.get("properties", [])
    }
    relative = properties.get("uv:workspace:path", ".")
    path = (ROOT / relative / "pyproject.toml").resolve(strict=True)
    if not path.is_relative_to(ROOT):
        raise ValueError("workspace path escapes repository")
    project = tomllib.loads(path.read_text(encoding="utf-8"))["project"]
    if project["name"] != component["name"] or project["version"] != component["version"]:
        raise ValueError("workspace package metadata differs from SBOM")
    return {
        "declaration": project.get("license", "UNDECLARED"),
        "evidence": "workspace-pyproject",
        "release_url": relative,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--refresh", action="store_true", help="refresh pinned PyPI release metadata"
    )
    parser.add_argument("--output-dir", type=Path, default=ROOT / ".data" / "supply-chain")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    components = _export_sbom(output / "sbom.cdx.json")
    registry = [item for item in components if str(item.get("purl", "")).startswith("pkg:pypi/")]
    if args.refresh:
        with ThreadPoolExecutor(max_workers=6) as pool:
            releases = dict(pool.map(_release_metadata, registry))
        overrides = json.loads(OVERRIDES.read_text(encoding="utf-8"))
        for key, override in overrides.items():
            if key not in releases or releases[key]["evidence"] != "missing":
                raise ValueError(f"license override is stale or unnecessary: {key}")
            releases[key] = override
        INVENTORY.parent.mkdir(parents=True, exist_ok=True)
        INVENTORY.write_text(
            json.dumps(releases, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    releases = json.loads(INVENTORY.read_text(encoding="utf-8"))
    required = {_component_key(item) for item in registry}
    if set(releases) != required:
        missing = sorted(required - set(releases))
        stale = sorted(set(releases) - required)
        raise ValueError(
            f"license inventory differs from lockfile: missing={missing}, stale={stale}"
        )
    rows = []
    for item in components:
        data = releases[_component_key(item)] if item in registry else _workspace_license(item)
        rows.append({"name": item["name"], "version": item["version"], **data})
    rows.sort(key=lambda row: (row["name"], row["version"]))
    report = {
        "lock_sha256": hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest(),
        "component_count": len(rows),
        "licenses": rows,
    }
    (output / "licenses.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Locked dependency license inventory", "",
        f"Lock SHA-256: `{report['lock_sha256']}`", "",
        "| Package | Version | Declaration | Evidence |",
        "| --- | --- | --- | --- |",
    ]
    for row in rows:
        safe = [
            str(row[key]).replace("|", "\\|").replace("\n", " ")
            for key in ("name", "version", "declaration", "evidence")
        ]
        lines.append("| " + " | ".join(safe) + " |")
    (output / "licenses.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    unresolved = [
        row["name"] for row in rows if row["evidence"] in {"missing", "legacy-text-review"}
    ]
    print(f"SBOM: {len(rows)} components; unresolved license declarations: {len(unresolved)}")
    if unresolved:
        raise SystemExit("Manual license review required: " + ", ".join(unresolved))


if __name__ == "__main__":
    main()
