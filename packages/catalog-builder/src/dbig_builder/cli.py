"""Assemble and inspect sealed catalog snapshots."""

import json
from pathlib import Path

import click
from dbig_contracts.artifact import read_artifact
from dbig_contracts.errors import OperatorError
from dbig_contracts.models import Edge

from .builder import assemble
from .config import load_config
from .state import assemble_incremental


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


@main.command("assemble")
@click.option(
    "--config", "config_path", type=click.Path(path_type=Path, exists=True), required=True
)
@click.option(
    "--bundle",
    "bundles",
    type=click.Path(path_type=Path, exists=True),
    multiple=True,
    required=True,
)
@click.option("--out", type=click.Path(path_type=Path), required=True)
def assemble_command(config_path: Path, bundles: tuple[Path, ...], out: Path) -> None:
    manifest = assemble(list(bundles), out, load_config(config_path))
    click.echo(manifest.model_dump_json())


@main.command("assemble-incremental")
@click.option(
    "--config", "config_path", type=click.Path(path_type=Path, exists=True), required=True
)
@click.option("--state", "state_path", type=click.Path(path_type=Path), required=True)
@click.option("--bundle", "bundles", type=click.Path(path_type=Path, exists=True), multiple=True)
@click.option("--out", type=click.Path(path_type=Path), required=True)
def assemble_incremental_command(
    config_path: Path, state_path: Path, bundles: tuple[Path, ...], out: Path
) -> None:
    manifest = assemble_incremental(list(bundles), state_path, out, load_config(config_path))
    click.echo(manifest.model_dump_json())


@main.command("validate-graph")
@click.argument("path", type=click.Path(path_type=Path, exists=True))
def validate_graph(path: Path) -> None:
    manifest, _ = read_artifact(path)
    if manifest.content_type != "catalog-snapshot":
        raise click.ClickException("expected catalog snapshot")
    click.echo(manifest.model_dump_json())


@main.command("explain-edge")
@click.argument("edge_id")
@click.option("--graph", type=click.Path(path_type=Path, exists=True), required=True)
def explain_edge(edge_id: str, graph: Path) -> None:
    _, records = read_artifact(graph)
    edges = {record.id: Edge.model_validate(record) for record in records["edges.jsonl"]}
    if edge_id not in edges:
        raise click.ClickException("edge not found")
    edge = edges[edge_id]
    evidence = {record.id: record for record in records["evidence.jsonl"]}
    click.echo(
        json.dumps(
            {
                "edge": edge.model_dump(mode="json"),
                "evidence": [evidence[key].model_dump(mode="json") for key in edge.evidence_ids],
            },
            sort_keys=True,
        )
    )
