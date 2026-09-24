"""Install requester and planner wheels in separate clean environments."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import zipfile
from email.parser import BytesParser
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    distribution = root / "dist"
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv is required for the clean-wheel verification job")
    for wheel_path in distribution.glob("dbig_*.whl"):
        with zipfile.ZipFile(wheel_path) as wheel_file:
            names = wheel_file.namelist()
            metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
            metadata = BytesParser().parsebytes(wheel_file.read(metadata_name))
            if metadata["License-Expression"] != "MIT" or not any(
                name.endswith(".dist-info/licenses/LICENSE") for name in names
            ):
                raise ValueError(f"missing MIT license metadata or text: {wheel_path.name}")
    contracts = next(distribution.glob("dbig_contracts-*.whl"))
    for name, module, command, other in (
        ("dbig_catalogue_requester", "dbig_requester", "dbig-requester", "dbig_planner"),
        ("dbig_lineage_planner", "dbig_planner", "dbig-planner", "dbig_requester"),
    ):
        wheel = next(distribution.glob(f"{name}-*.whl"))
        with tempfile.TemporaryDirectory(prefix=f"{name}-") as temporary:
            env = Path(temporary) / "env"
            subprocess.run([uv, "venv", str(env), "--python", sys.executable], check=True)  # noqa: S603
            python = env / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
            executable = env / (
                f"Scripts/{command}.exe" if sys.platform == "win32" else f"bin/{command}"
            )
            subprocess.run(  # noqa: S603
                [
                    uv,
                    "pip",
                    "install",
                    "--python",
                    str(python),
                    "--find-links",
                    str(distribution),
                    str(contracts),
                    f"{wheel}[api]",
                ],
                check=True,
            )
            source = (
                "import importlib, importlib.util, pathlib, sys; "
                "module = importlib.import_module(sys.argv[1]); "
                "importlib.import_module(sys.argv[1] + '.api'); "
                "assert pathlib.Path(module.__file__).resolve().is_relative_to("
                "pathlib.Path(sys.argv[2]).resolve()); "
                "assert all(importlib.util.find_spec(name) is None for name in "
                "(sys.argv[3], 'dbig_collectors', 'pg8000', 'clickhouse_connect', 'pymongo'))"
            )
            subprocess.run([str(python), "-c", source, module, str(env), other], check=True)  # noqa: S603
            subprocess.run([str(executable), "--help"], check=True, capture_output=True)  # noqa: S603
            print(f"{name}: clean wheel, isolated imports, and CLI passed")


if __name__ == "__main__":
    main()
