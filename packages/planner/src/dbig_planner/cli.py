"""Planner CLI backed by the same functions as its REST API."""

import json
from ipaddress import ip_address
from pathlib import Path

import click
from dbig_contracts.errors import OperatorError, describe_validation
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


def _paths(graph: Path, from_id: str, to_id: str, kind: str) -> None:
    result = find_paths(
        graph, PathRequest(from_id=from_id, to_id=to_id), kind, PlannerConfig(graph_dir=graph)
    )
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
def join_paths(graph: Path, from_id: str, to_id: str) -> None:
    _paths(graph, from_id, to_id, "join")


@main.command("plan")
@click.option("--graph", type=click.Path(path_type=Path, exists=True), required=True)
@click.option("--path-id", required=True)
def plan(graph: Path, path_id: str) -> None:
    result = render_plan(graph, PlanRequest(path_id=path_id), PlannerConfig(graph_dir=graph))
    click.echo(json.dumps(result, sort_keys=True))


@main.command("serve")
@click.option(
    "--config", "config_path", type=click.Path(path_type=Path, exists=True), required=True
)
@click.option("--host", required=True)
@click.option("--port", type=int, required=True)
def serve(config_path: Path, host: str, port: int) -> None:
    if not ip_address(host).is_loopback:
        raise click.UsageError("planner API binds only to loopback; use a private TLS proxy")
    import uvicorn

    from .api import create_app

    uvicorn.run(create_app(load_config(config_path)), host=host, port=port)
