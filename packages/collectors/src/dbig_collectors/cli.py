"""Collector site command line."""

import json
import threading
from pathlib import Path

import click
from dbig_contracts.artifact import read_artifact
from dbig_contracts.errors import OperatorError
from dbig_contracts.scan import SignedScanRequest
from dbig_contracts.security import key_from_env, serve_refusal

from .config import load_config
from .runtime import collect
from .scan import accept_scan, process_next, run_worker


class SecretAwareGroup(click.Group):
    """Report an operator-fixable failure as a command error, not a traceback.

    Converting at the group covers every command in it, including ones added later. Only
    `OperatorError` is converted, which covers a missing secret reference, an unknown identifier
    and state that has not been built yet. An ordinary `ValueError` keeps its full traceback, so
    a validation failure or a genuine bug is never dressed up as something an operator can fix.
    """

    def invoke(self, ctx: click.Context) -> object:
        try:
            return super().invoke(ctx)
        except OperatorError as error:
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
    key = key_from_env(key_env, purpose="collector bundle signature verification")
    manifest, _ = read_artifact(path, signing_key=key, require_signature=True)
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
@click.option(
    "--bundle-root",
    type=click.Path(path_type=Path, file_okay=False),
    help="Run the scan worker in this process, writing bundles here.",
)
@click.option("--host", required=True)
@click.option("--port", type=int, required=True)
@click.option("--tls-cert", type=click.Path(path_type=Path, exists=True, dir_okay=False))
@click.option("--tls-key", type=click.Path(path_type=Path, exists=True, dir_okay=False))
def serve(
    config_path: Path,
    queue_db: Path,
    bundle_root: Path | None,
    host: str,
    port: int,
    tls_cert: Path | None,
    tls_key: Path | None,
) -> None:
    # The one bind rule, before any configuration is read.
    if refusal := serve_refusal(host, tls_cert, tls_key):
        raise click.UsageError(refusal)
    import uvicorn

    from .api import create_app

    config = load_config(config_path)
    app = create_app(config, queue_db)
    stop = threading.Event()
    if bundle_root is not None:
        threading.Thread(
            target=run_worker, args=(config, queue_db, bundle_root, stop), daemon=True
        ).start()
    uvicorn.run(
        app,
        host=host,
        port=port,
        ssl_certfile=tls_cert,
        ssl_keyfile=tls_key,
    )
