"""Contract tests for the repository policy gate in `scripts/ci_check.py`.

The supply-chain SBOM and license gate calls `uv export --format cyclonedx1.5`, a preview
format that only uv 0.10.0 and later accept. CI therefore must install an exact uv version at
or above the floor declared in `[tool.uv] required-version`; an unpinned `astral-sh/setup-uv`
silently installs whatever uv is latest and makes that security gate irreproducible.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
SETUP_UV = "astral-sh/setup-uv@c18668ad3cf93ea998bef934396af7bb5c839dc7 # v10.2.0"


def load_ci_check() -> ModuleType:
    """Import `scripts/ci_check.py`, which is a standalone script rather than a package."""
    path = ROOT / "scripts" / "ci_check.py"
    spec = importlib.util.spec_from_file_location("dbig_ci_check", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def ci_check() -> ModuleType:
    return load_ci_check()


def workflow(*steps: str) -> str:
    body = "\n".join(steps)
    return f"""name: Example
on: [push]
jobs:
  example:
    runs-on: ubuntu-latest
    steps:
{body}
"""


def pinned_step(version: str, cache: bool = True) -> str:
    lines = [f"      - uses: {SETUP_UV}", "        with:"]
    if cache:
        lines.append("          enable-cache: 'false'")
    lines.append(f"          version: '{version}'")
    return "\n".join(lines)


def unpinned_step(cache: bool = True) -> str:
    lines = [f"      - uses: {SETUP_UV}"]
    if cache:
        lines.extend(["        with:", "          enable-cache: 'false'"])
    return "\n".join(lines)


def fake_repository(
    tmp_path: Path, floor: str | None, workflows: dict[str, str]
) -> Path:
    """Build a minimal tree with the pieces the uv pin guard reads."""
    uv_table = "[tool.uv]\npackage = false\n"
    if floor is not None:
        uv_table += f'required-version = "{floor}"\n'
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "example"\nversion = "0.1.0"\n\n' + uv_table, encoding="utf-8"
    )
    directory = tmp_path / ".github" / "workflows"
    directory.mkdir(parents=True)
    for name, text in workflows.items():
        (directory / name).write_text(text, encoding="utf-8")
    return tmp_path


# --- pin extraction -------------------------------------------------------------------


def test_pin_is_read_from_a_setup_uv_step(ci_check: ModuleType) -> None:
    assert ci_check._setup_uv_pins(workflow(pinned_step("0.12.19"))) == ["0.12.19"]


def test_step_without_a_version_reports_no_pin(ci_check: ModuleType) -> None:
    assert ci_check._setup_uv_pins(workflow(unpinned_step())) == [None]


def test_each_setup_uv_step_is_reported_separately(ci_check: ModuleType) -> None:
    text = workflow(pinned_step("0.12.19"), unpinned_step(), pinned_step("0.11.0"))
    assert ci_check._setup_uv_pins(text) == ["0.12.19", None, "0.11.0"]


def test_a_later_steps_version_is_not_credited_to_an_unpinned_step(
    ci_check: ModuleType,
) -> None:
    """A `version:` belonging to an unrelated following step must not mask a missing pin."""
    other = "\n".join(
        [
            "      - uses: actions/setup-node@abc",
            "        with:",
            "          version: '22'",
        ]
    )
    assert ci_check._setup_uv_pins(workflow(unpinned_step(), other)) == [None]


def test_a_workflow_without_setup_uv_yields_no_pins(ci_check: ModuleType) -> None:
    step = "      - uses: actions/checkout@abc"
    assert ci_check._setup_uv_pins(workflow(step)) == []


# --- the real repository --------------------------------------------------------------


def test_repository_pins_every_setup_uv_step_at_or_above_the_declared_floor(
    ci_check: ModuleType,
) -> None:
    ci_check._check_uv_pins()


def test_declared_floor_admits_cyclonedx_export(ci_check: ModuleType) -> None:
    """uv 0.10.0 is the first release whose `uv export --format` accepts `cyclonedx1.5`."""
    assert ci_check._parts(ci_check._uv_floor()) >= (0, 10, 0)


def test_every_workflow_setup_uv_step_is_pinned(ci_check: ModuleType) -> None:
    steps = 0
    for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        pins = ci_check._setup_uv_pins(path.read_text(encoding="utf-8"))
        steps += len(pins)
        assert None not in pins, f"{path.name} has an unpinned setup-uv step"
    assert steps >= 1, "the uv pin guard must have at least one step to check"


# --- rejected configurations ----------------------------------------------------------


def test_unpinned_setup_uv_is_rejected(
    ci_check: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = fake_repository(tmp_path, ">=0.10.0", {"ci.yml": workflow(unpinned_step())})
    monkeypatch.setattr(ci_check, "ROOT", root)
    with pytest.raises(SystemExit, match="must pin an explicit uv 'version:'"):
        ci_check._check_uv_pins()


def test_floating_latest_pin_is_rejected(
    ci_check: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = fake_repository(tmp_path, ">=0.10.0", {"ci.yml": workflow(pinned_step("latest"))})
    monkeypatch.setattr(ci_check, "ROOT", root)
    with pytest.raises(SystemExit, match="exact uv X.Y.Z version"):
        ci_check._check_uv_pins()


def test_pin_below_the_declared_floor_is_rejected(
    ci_check: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """uv 0.9.0 cannot export CycloneDX, so a workflow pinning it must fail the gate."""
    root = fake_repository(tmp_path, ">=0.10.0", {"ci.yml": workflow(pinned_step("0.9.0"))})
    monkeypatch.setattr(ci_check, "ROOT", root)
    with pytest.raises(SystemExit, match="below the \\[tool.uv\\] required-version floor"):
        ci_check._check_uv_pins()


def test_pin_equal_to_the_floor_is_accepted(
    ci_check: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = fake_repository(tmp_path, ">=0.10.0", {"ci.yml": workflow(pinned_step("0.10.0"))})
    monkeypatch.setattr(ci_check, "ROOT", root)
    ci_check._check_uv_pins()


def test_disagreeing_pins_across_workflows_are_rejected(
    ci_check: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = fake_repository(
        tmp_path,
        ">=0.10.0",
        {
            "ci.yml": workflow(pinned_step("0.12.19")),
            "release.yml": workflow(pinned_step("0.11.0")),
        },
    )
    monkeypatch.setattr(ci_check, "ROOT", root)
    with pytest.raises(SystemExit, match="same uv version"):
        ci_check._check_uv_pins()


def test_disagreeing_pins_inside_one_workflow_are_rejected(
    ci_check: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = fake_repository(
        tmp_path,
        ">=0.10.0",
        {"ci.yml": workflow(pinned_step("0.12.19"), pinned_step("0.11.0"))},
    )
    monkeypatch.setattr(ci_check, "ROOT", root)
    with pytest.raises(SystemExit, match="same uv version"):
        ci_check._check_uv_pins()


def test_missing_required_version_is_rejected(
    ci_check: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = fake_repository(tmp_path, None, {"ci.yml": workflow(pinned_step("0.12.19"))})
    monkeypatch.setattr(ci_check, "ROOT", root)
    with pytest.raises(SystemExit, match="must declare \\[tool.uv\\] required-version"):
        ci_check._check_uv_pins()


def test_non_floor_required_version_specifier_is_rejected(
    ci_check: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A range the guard cannot compare against must fail loudly, not be ignored."""
    root = fake_repository(tmp_path, ">=0.10", {"ci.yml": workflow(pinned_step("0.12.19"))})
    monkeypatch.setattr(ci_check, "ROOT", root)
    with pytest.raises(SystemExit, match="exact '>=X.Y.Z' floor"):
        ci_check._check_uv_pins()


def test_absent_setup_uv_step_does_not_silently_pass(
    ci_check: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    step = "      - uses: actions/checkout@abc"
    root = fake_repository(tmp_path, ">=0.10.0", {"ci.yml": workflow(step)})
    monkeypatch.setattr(ci_check, "ROOT", root)
    with pytest.raises(SystemExit, match="No setup-uv step found"):
        ci_check._check_uv_pins()
