"""`scripts/rule_check.py` rules C5, L1 and L4, run against a real throwaway git repository."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "rule_check.py"
C5 = "C5: reason=fixture needs it; approved=test, 2026-11-08; review=2027-05-08"
BASE_TEST = "def test_adds():\n    assert 1 + 1 == 2\n"


def git(repo: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603 # C5: reason=fixed argv, no shell, throwaway repo; approved=user, 2026-11-08; review=2027-05-08
        ("git", "-c", "user.name=t", "-c", "user.email=t@t", *args),
        cwd=repo, check=True, capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_base.py").write_text(BASE_TEST, encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "base")
    git(tmp_path, "checkout", "-q", "-b", "change")
    return tmp_path


def change(
    repo: Path, files: dict[str, str], message: str = "change"
) -> subprocess.CompletedProcess[str]:
    for name, text in files.items():
        (repo / name).write_text(text, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return subprocess.run(  # noqa: S603 # C5: reason=fixed argv, no shell, throwaway repo; approved=user, 2026-11-08; review=2027-05-08
        (sys.executable, str(SCRIPT), "--base", "main"), cwd=repo, capture_output=True, text=True,
    )


def test_an_added_noqa_without_a_c5_line_fails(repo: Path) -> None:
    result = change(repo, {"tool.py": "import os  # noqa: F401\n"})
    assert result.returncode == 1
    assert "C5: exception without a reason line: tool.py:1" in result.stdout


def test_an_added_noqa_with_a_c5_line_passes(repo: Path) -> None:
    result = change(repo, {"tool.py": f"import os  # noqa: F401 # {C5}\n"})
    assert result.returncode == 0, result.stdout


def test_an_added_skip_without_a_c5_line_fails(repo: Path) -> None:
    text = "import pytest\n\n@pytest.mark.skip\ndef test_x():\n    assert True\n"
    result = change(repo, {"tests/test_new.py": text})
    assert "C5: exception without a reason line: tests/test_new.py:3" in result.stdout


def test_a_removed_test_without_a_c5_commit_line_fails(repo: Path) -> None:
    result = change(repo, {"tests/test_base.py": "VALUE = 1\n"})
    assert result.returncode == 1
    assert "L1: test removed without a `C5:` commit line: test_adds" in result.stdout


def test_a_removed_test_with_a_c5_commit_line_passes(repo: Path) -> None:
    result = change(repo, {"tests/test_base.py": "VALUE = 1\n"}, message=f"drop it\n\n{C5}")
    assert result.returncode == 0, result.stdout


def test_a_moved_test_is_not_a_removal(repo: Path) -> None:
    git(repo, "mv", "tests/test_base.py", "tests/test_moved.py")
    result = change(repo, {})
    assert result.returncode == 0, result.stdout


def test_a_new_test_with_no_assertion_fails(repo: Path) -> None:
    result = change(repo, {"tests/test_new.py": "def test_runs():\n    print('ok')\n"})
    assert result.returncode == 1
    assert "L4: test with no assertion: tests/test_new.py:1: test_runs" in result.stdout


def test_an_edited_test_that_only_prints_fails(repo: Path) -> None:
    result = change(repo, {"tests/test_base.py": "def test_adds():\n    print(1 + 1)\n"})
    assert "L4: test with no assertion: tests/test_base.py:1: test_adds" in result.stdout


def test_tests_that_assert_or_expect_an_error_pass(repo: Path) -> None:
    text = (
        "import pytest\n\n"
        "def test_a():\n    assert 1\n\n"
        "def test_b():\n    with pytest.raises(ZeroDivisionError):\n        1 / 0\n"
    )
    result = change(repo, {"tests/test_new.py": text})
    assert result.returncode == 0, result.stdout


def test_a_test_that_calls_a_helper_which_asserts_passes(repo: Path) -> None:
    text = (
        "def _expect_even(number):\n    assert number % 2 == 0\n\n"
        "def test_even():\n    _expect_even(4)\n\n"
        "def _just_prints(number):\n    print(number)\n\n"
        "def test_print():\n    _just_prints(4)\n"
    )
    result = change(repo, {"tests/test_new.py": text})
    assert "tests/test_new.py:5: test_even" not in result.stdout
    assert "L4: test with no assertion: tests/test_new.py:10: test_print" in result.stdout
