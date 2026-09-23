"""Collector site command line."""

import os
from pathlib import Path

import click
from dbig_contracts.artifact import read_artifact

from .config import load_config
from .runtime import collect


@click.group()
def main() -> None:
    pass


@main.command("collect")
@click.option(
    "--config", "config_path", type=click.Path(path_type=Path, exists=True), required=True
)
@click.option("--out", type=click.Path(path_type=Path), required=True)
def collect_command(config_path: Path, out: Path) -> None:
    collect(load_config(config_path), out)
    click.echo(out)


@main.command("validate-bundle")
@click.argument("path", type=click.Path(path_type=Path, exists=True))
@click.option("--key-env", required=True, help="Environment variable holding the signing key")
def validate_bundle(path: Path, key_env: str) -> None:
    manifest, _ = read_artifact(
        path, signing_key=os.environ[key_env].encode(), require_signature=True
    )
    click.echo(manifest.model_dump_json())
