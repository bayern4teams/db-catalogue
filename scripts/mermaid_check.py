"""Render every Mermaid block in the given Markdown files; a block that fails fails the check.

Set PUPPETEER_EXECUTABLE_PATH to an installed Chrome/Chromium so mmdc needs no browser download.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

BLOCK = re.compile(r"^```mermaid\n(.*?)^```", re.MULTILINE | re.DOTALL)


def main(paths: list[str]) -> int:
    failures = checked = 0
    with tempfile.TemporaryDirectory() as tmp:
        for name in paths:
            for number, block in enumerate(BLOCK.findall(Path(name).read_text("utf-8")), 1):
                source = Path(tmp) / "block.mmd"
                source.write_text(block, "utf-8")
                result = subprocess.run(  # noqa: S603 # C5: reason=fixed argv, no shell; approved=user, 2026-11-08; review=2027-05-08
                    (
                        "mmdc",
                        "-p",
                        "scripts/puppeteer.json",
                        "-i",
                        str(source),
                        "-o",
                        str(Path(tmp) / "block.svg"),
                        "-q",
                    ),
                    capture_output=True,
                    text=True,
                )
                checked += 1
                if result.returncode:
                    failures += 1
                    print(f"{name}: Mermaid block {number} does not render:\n{result.stderr}")
    print(f"{checked} Mermaid block(s) checked, {failures} failed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
