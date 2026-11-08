"""Install the pinned gitleaks and actionlint binaries, verifying each SHA-256 (AGENTS.md B6, K4).

Usage: python scripts/install_check_tools.py [BIN_DIR]   (default: ~/.local/bin)
Bump a version by changing VERSION and SHA256 together, from the release's checksums file.
zizmor, deptry and vulture are Python packages: they are pinned in pyproject.toml instead.
"""

from __future__ import annotations

import hashlib
import io
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

TOOLS = {
    "gitleaks": {
        "version": "8.30.1",
        "url": "https://github.com/gitleaks/gitleaks/releases/download/v{v}/gitleaks_{v}_linux_x64.tar.gz",
        "sha256": "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb",
    },
    "actionlint": {
        "version": "1.7.12",
        "url": "https://github.com/rhysd/actionlint/releases/download/v{v}/actionlint_{v}_linux_amd64.tar.gz",
        "sha256": "8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8",
    },
}


def install(name: str, bin_dir: Path) -> None:
    tool = TOOLS[name]
    url = tool["url"].format(v=tool["version"])
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310 # C5: reason=pinned https URL, checksum verified; approved=user, 2026-11-08; review=2027-05-08
                data = response.read()
            break
        except OSError as error:  # the release CDN times out now and then
            if attempt == 3:
                raise SystemExit(f"{name}: download failed ({error}). Re-run the job.") from error
            time.sleep(10 * attempt)
    digest = hashlib.sha256(data).hexdigest()
    if digest != tool["sha256"]:
        raise SystemExit(f"{name}: checksum mismatch ({digest}). Not installed; re-check the pin.")
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        binary = archive.extractfile(name)
        if binary is None:
            raise SystemExit(f"{name}: not a file in the archive. Re-check the pinned URL.")
        target = bin_dir / name
        target.write_bytes(binary.read())
    target.chmod(0o755)
    print(f"installed {name} {tool['version']} to {target}")


def main() -> None:
    default = Path.home() / ".local" / "bin"
    bin_dir = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else default
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name in TOOLS:
        install(name, bin_dir)


if __name__ == "__main__":
    main()
