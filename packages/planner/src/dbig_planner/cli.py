"""Planner CLI backed by the same functions as its REST API."""

import json
from pathlib import Path

import click
from dbig_contracts.errors import OperatorError, describe_validation
from dbig_contracts.security import serve_refusal
from pydantic import ValidationError

from .config import PlannerConfig, load_config
from .graph import build_graph
from .observations import DatasetMapping
from .paths import PathRequest, find_paths
from .plans import PlanRequest, render_plan


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


@main.command("build")
@click.option("--snapshot", type=click.Path(path_type=Path, exists=True), required=True)
@click.option("--observations", type=click.Path(path_type=Path, exists=True), required=True)
@click.option("--out", type=click.Path(path_type=Path), required=True)
@click.option("--mapping", type=click.Path(path_type=Path, exists=True))
def build(snapshot: Path, observations: Path, out: Path, mapping: Path | None) -> None:
    try:
        mapped = DatasetMapping.model_validate_json(mapping.read_bytes()) if mapping else None
    except ValidationError as error:
        raise OperatorError(
            f"dataset mapping {mapping} is invalid: {describe_validation(error)}"
        ) from error
    graph = build_graph(snapshot, observations, out, PlannerConfig(graph_dir=out), mapped)
    click.echo(
        json.dumps(
            {
                "snapshot_digest": graph["snapshot_digest"],
                "lineage_edges": len(graph["lineage"]),
                "reference_edges": len(graph["references"]),
                "diagnostics": graph["diagnostics"],
            },
            sort_keys=True,
        )
    )


def _paths(
    graph: Path, from_id: str, to_id: str, kind: str, include_unverified: bool = False
) -> None:
    config = PlannerConfig(graph_dir=graph, include_unverified_cross_source=include_unverified)
    result = find_paths(graph, PathRequest(from_id=from_id, to_id=to_id), kind, config)
    click.echo(json.dumps(result, sort_keys=True))


@main.command("lineage-paths")
@click.option("--graph", type=click.Path(path_type=Path, exists=True), required=True)
@click.option("--from", "from_id", required=True)
@click.option("--to", "to_id", required=True)
def lineage_paths(graph: Path, from_id: str, to_id: str) -> None:
    _paths(graph, from_id, to_id, "lineage")


@main.command("join-paths")
@click.option("--graph", type=click.Path(path_type=Path, exists=True), required=True)
@click.option("--from", "from_id", required=True)
@click.option("--to", "to_id", required=True)
@click.option(
    "--include-unverified-cross-source",
    is_flag=True,
    help="Also walk candidates between two sources with no positive measurement (F11-b)",
)
def join_paths(
    graph: Path, from_id: str, to_id: str, include_unverified_cross_source: bool
) -> None:
    _paths(graph, from_id, to_id, "join", include_unverified_cross_source)


@main.command("plan")
@click.option("--graph", type=click.Path(path_type=Path, exists=True), required=True)
@click.option("--path-id", required=True)
@click.option(
    "--max-rows",
    type=click.IntRange(1, 100_000),
    help="Row limit for each read (default 1000)",
)
def plan(graph: Path, path_id: str, max_rows: int | None) -> None:
    config = PlannerConfig(graph_dir=graph)
    if max_rows is not None:
        config = PlannerConfig(graph_dir=graph, max_plan_rows=max_rows)
    result = render_plan(graph, PlanRequest(path_id=path_id, max_rows=max_rows), config)
    click.echo(json.dumps(result, sort_keys=True))


@main.command("serve")
@click.option(
    "--config", "config_path", type=click.Path(path_type=Path, exists=True), required=True
)
@click.option("--host", required=True)
@click.option("--port", type=int, required=True)
@click.option("--tls-cert", type=click.Path(path_type=Path, exists=True, dir_okay=False))
@click.option("--tls-key", type=click.Path(path_type=Path, exists=True, dir_okay=False))
def serve(
    config_path: Path, host: str, port: int, tls_cert: Path | None, tls_key: Path | None
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
        ssl_certfile=tls_cert,
        ssl_keyfile=tls_key,
    )
