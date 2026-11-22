"""Build the five service images and save them as one tarball for the LAN host (owner D6,
2026-11-10: "Build here, copy tar (Recommended)").

Run on a machine with internet, from the repository root:

    python scripts/build_images.py --version 0.1.0 --save --out ../dbig-images-0.1.0.tar

Then copy the tarball to the LAN host and run `docker load -i dbig-images-0.1.0.tar` there. The
host never pulls or builds, so the no-outbound rule has no build exception.

With `--push` (optionally `--registry <prefix/>`) the images are pushed instead of saved — this
is what the tag release pipelines on both remotes run, so the image names, build arguments and
smoke test live only here (owner decision 10, 2026-11-21: five images on both remotes).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Image name: workspace package, the extras its service needs, and its command.
IMAGES = {
    "dbig-collectors": ("dbig-collectors", ["postgresql", "clickhouse", "mongodb", "api"], "dbig"),
    "dbig-catalog-builder": ("dbig-catalog-builder", ["api"], "dbig-builder"),
    "dbig-catalogue-requester": ("dbig-catalogue-requester", ["api"], "dbig-requester"),
    "dbig-lineage-planner": ("dbig-lineage-planner", ["api"], "dbig-planner"),
    "dbig-contracts": ("dbig-contracts", ["api"], "dbig-contracts"),
}


def _run(*arguments: str) -> None:
    result = subprocess.run(arguments, cwd=ROOT, check=False)  # noqa: S603 # C5: reason=fixed argv, no user input; approved=owner, 2026-11-10; review=2027-05-10
    if result.returncode != 0:
        raise SystemExit(f"{' '.join(arguments[:3])} ... failed (exit {result.returncode})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", required=True)
    parser.add_argument("--out", type=Path, help="tarball to write (with --save)")
    parser.add_argument("--save", action="store_true", help="save the images to --out")
    parser.add_argument("--push", action="store_true", help="push the images to --registry")
    parser.add_argument("--registry", default="", help="image prefix, ending with '/'")
    arguments = parser.parse_args()
    if arguments.save == arguments.push:
        raise SystemExit("choose exactly one of --save (needs --out) or --push")
    if arguments.save and arguments.out is None:
        raise SystemExit("--save needs --out <tarball>")
    if arguments.registry and not arguments.registry.endswith("/"):
        raise SystemExit("--registry must end with '/'")
    docker = shutil.which("docker")
    if docker is None:
        raise SystemExit("docker is required to build the images")
    tags = []
    for image, (package, extras, command) in IMAGES.items():
        tag = f"{arguments.registry}{image}:{arguments.version}"
        _run(
            docker, "build", "--file", "deploy/Dockerfile", "--tag", tag,
            "--build-arg", f"PACKAGE={package}",
            "--build-arg", "EXTRAS=" + " ".join(f"--extra {extra}" for extra in extras),
            ".",
        )
        # Smoke test: the command starts in the image as built (a wrong venv path fails here).
        _run(docker, "run", "--rm", "--read-only", tag, command, "serve", "--help")
        tags.append(tag)
    if arguments.push:
        for tag in tags:
            _run(docker, "push", tag)
        print(f"pushed {len(tags)} images to {arguments.registry or '<local>'}")
    else:
        _run(docker, "save", "--output", str(arguments.out.resolve()), *tags)
        print(f"saved {len(tags)} images to {arguments.out}")


if __name__ == "__main__":
    main()
