"""Install this repository's versioned hooks through Git core.hooksPath."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    if not (ROOT / ".git").exists():
        raise SystemExit("Run inside a normal Git checkout of this project.")
    for name in ("pre-commit", "post-commit"):
        if not (ROOT / ".githooks" / name).is_file():
            raise SystemExit(f"Missing versioned hook: {name}")
    subprocess.run(
        ("git", "config", "--local", "core.hooksPath", ".githooks"), cwd=ROOT, check=True
    )
    print("Installed .githooks as the local Git hook path.")


if __name__ == "__main__":
    main()
