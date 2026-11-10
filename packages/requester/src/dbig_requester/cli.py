"""Requester CLI using the same functions as its REST service."""

import json
from pathlib import Path
from typing import Literal

import click
from dbig_contracts.errors import OperatorError
from dbig_contracts.scan import ScanRequest
from dbig_contracts.security import serve_refusal

from .answer import answer_question
from .config import load_config
from .scan import dispatch_scan
from .search import SearchRequest, index_snapshot, search


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


@main.command("index")
@click.option(
    "--config", "config_path", type=click.Path(path_type=Path, exists=True), required=True
)
def index(config_path: Path) -> None:
    config = load_config(config_path)
    click.echo(index_snapshot(config.snapshot_dir, config.state_dir, config.exclude_objects))


@main.command("search")
@click.option(
    "--config", "config_path", type=click.Path(path_type=Path, exists=True), required=True
)
@click.option("--query", default="")
@click.option("--source-id")
@click.option("--kind")
@click.option("--data-type")
@click.option("--tag")
@click.option("--capability")
@click.option("--evidence-status")
@click.option("--max-age-seconds", type=int)
@click.option("--limit", type=int, default=20)
@click.option("--offset", type=int, default=0)
@click.option(
    "--match",
    type=click.Choice(["all", "any"]),
    default="all",
    show_default=True,
    help="all: every word must match. any: rank by how well entries match any word.",
)
def search_command(
    config_path: Path,
    query: str,
    source_id: str | None,
    kind: str | None,
    data_type: str | None,
    tag: str | None,
    capability: str | None,
    evidence_status: str | None,
    max_age_seconds: int | None,
    limit: int,
    offset: int,
    match: Literal["all", "any"],
) -> None:
    config = load_config(config_path)
    click.echo(
        json.dumps(
            search(
                config.state_dir,
                SearchRequest(
                    query=query,
                    source_id=source_id,
                    kind=kind,
                    data_type=data_type,
                    tag=tag,
                    capability=capability,
                    evidence_status=evidence_status,
                    max_age_seconds=max_age_seconds,
                    limit=limit,
                    offset=offset,
                    match=match,
                ),
            ),
            sort_keys=True,
        )
    )


@main.command("ask")
@click.option(
    "--config", "config_path", type=click.Path(path_type=Path, exists=True), required=True
)
@click.option("--question", required=True)
def ask(config_path: Path, question: str) -> None:
    config = load_config(config_path)
    if config.answer is None:
        raise click.ClickException("private answer model is not configured")
    click.echo(
        json.dumps(answer_question(config.state_dir, question, config.answer), sort_keys=True)
    )


@main.command("request-scan")
@click.option(
    "--config", "config_path", type=click.Path(path_type=Path, exists=True), required=True
)
@click.option(
    "--request", "request_path", type=click.Path(path_type=Path, exists=True), required=True
)
@click.option("--dispatch", "adapter", type=click.Choice(["bundle", "http"]), required=True)
def request_scan(config_path: Path, request_path: Path, adapter: str) -> None:
    config = load_config(config_path)
    if config.scan is None:
        raise click.ClickException("scan dispatch is not configured")
    request = ScanRequest.model_validate_json(request_path.read_bytes())
    click.echo(dispatch_scan(request, config.scan, config.state_dir, adapter).model_dump_json())


@main.command("serve")
@click.option(
    "--config", "config_path", type=click.Path(path_type=Path, exists=True), required=True
)
@click.option("--host", required=True)
@click.option("--port", type=int, required=True)
@click.option(
    "--no-access-log",
    is_flag=True,
    help="Do not log request lines; the GET search route carries the query in the URL.",
)
@click.option("--tls-cert", type=click.Path(path_type=Path, exists=True, dir_okay=False))
@click.option("--tls-key", type=click.Path(path_type=Path, exists=True, dir_okay=False))
def serve(
    config_path: Path,
    host: str,
    port: int,
    no_access_log: bool,
    tls_cert: Path | None,
    tls_key: Path | None,
) -> None:
    # The one bind rule, before any configuration is read.
    if refusal := serve_refusal(host, tls_cert, tls_key):
        raise click.UsageError(refusal)
    import uvicorn

    from .api import create_app

    uvicorn.run(
        create_app(load_config(config_path)),
        host=host,
        port=port,
        access_log=not no_access_log,
        ssl_certfile=tls_cert,
        ssl_keyfile=tls_key,
    )
