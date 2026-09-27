"""Collector site command line."""

import json
from ipaddress import ip_address
from pathlib import Path

import click
from dbig_contracts.artifact import read_artifact
from dbig_contracts.scan import SignedScanRequest
from dbig_contracts.security import SecretReferenceError, secret_from_env

from .config import load_config
from .runtime import collect
from .scan import accept_scan, process_next


class SecretAwareGroup(click.Group):
    """Report a missing or blank secret reference as a command error, not a traceback.

    Converting at the group covers every command in it, including ones added later. Only
    `SecretReferenceError` is converted, so validation failures and programming errors keep their
    full traceback.
    """

    def invoke(self, ctx: click.Context) -> object:
        try:
            return super().invoke(ctx)
        except SecretReferenceError as error:
            raise click.ClickException(str(error)) from error


@click.group(cls=SecretAwareGroup)
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
    # SecretAwareGroup converts a bad `--key-env` into a command error.
    key = secret_from_env(key_env, purpose="collector bundle signature verification")
    manifest, _ = read_artifact(path, signing_key=key.encode(), require_signature=True)
    click.echo(manifest.model_dump_json())


@main.command("accept-scan")
@click.option(
    "--config", "config_path", type=click.Path(path_type=Path, exists=True), required=True
)
@click.option(
    "--request", "request_path", type=click.Path(path_type=Path, exists=True), required=True
)
@click.option("--queue-db", type=click.Path(path_type=Path), required=True)
def accept_scan_command(config_path: Path, request_path: Path, queue_db: Path) -> None:
    envelope = SignedScanRequest.model_validate_json(request_path.read_bytes())
    receipt = accept_scan(envelope, load_config(config_path), queue_db)
    click.echo(receipt.model_dump_json())


@main.command("process-scans")
@click.option(
    "--config", "config_path", type=click.Path(path_type=Path, exists=True), required=True
)
@click.option("--queue-db", type=click.Path(path_type=Path), required=True)
@click.option("--out-root", type=click.Path(path_type=Path), required=True)
def process_scans_command(config_path: Path, queue_db: Path, out_root: Path) -> None:
    receipt = process_next(load_config(config_path), queue_db, out_root)
    click.echo(receipt.model_dump_json() if receipt else json.dumps({"status": "empty"}))


@main.command("serve")
@click.option(
    "--config", "config_path", type=click.Path(path_type=Path, exists=True), required=True
)
@click.option("--queue-db", type=click.Path(path_type=Path), required=True)
@click.option("--host", required=True)
@click.option("--port", type=int, required=True)
def serve(config_path: Path, queue_db: Path, host: str, port: int) -> None:
    if not ip_address(host).is_loopback:
        raise click.UsageError("collector API binds only to loopback; use a private TLS proxy")
    import uvicorn

    from .api import create_app

    uvicorn.run(create_app(load_config(config_path), queue_db), host=host, port=port)
