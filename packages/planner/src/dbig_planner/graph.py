"""Build a derived, digest-bound graph from sealed catalog and observations."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from dbig_contracts.artifact import read_artifact
from dbig_contracts.models import Edge, Field, Object, Source

from .config import PlannerConfig
from .observations import (
    DatasetMapping,
    OpenLineageEvent,
    PipelineObservation,
    SQLObservation,
    observation_digest,
    read_lines,
    sql_relations,
)


def _snapshot_digest(snapshot: Path) -> str:
    return hashlib.sha256((snapshot / "manifest.json").read_bytes()).hexdigest()


def build_graph(
    snapshot: Path,
    observations: Path,
    output: Path,
    config: PlannerConfig,
    mapping: DatasetMapping | None = None,
) -> dict:
    manifest, tables = read_artifact(snapshot)
    if manifest.content_type != "catalog-snapshot":
        raise ValueError("EXPECTED_CATALOG_SNAPSHOT")
    objects = {
        item.id: Object.model_validate(item.model_dump(mode="json"))
        for item in tables["nodes.jsonl"]
        if item.record_type == "object"
    }
    fields = {
        item.id: Field.model_validate(item.model_dump(mode="json"))
        for item in tables["nodes.jsonl"]
        if item.record_type == "field"
    }
    sources = {
        item.id: Source.model_validate(item.model_dump(mode="json"))
        for item in tables["nodes.jsonl"]
        if item.record_type == "source"
    }
    references = [
        Edge.model_validate(item.model_dump(mode="json")).model_dump(mode="json")
        for item in tables["edges.jsonl"]
        if item.record_type == "edge"
        and Edge.model_validate(item.model_dump(mode="json")).kind
        in {"DECLARES_FK", "REFERENCE_CANDIDATE"}
    ]
    by_name: dict[tuple[str, str], list[str]] = {}
    for item in objects.values():
        by_name.setdefault((item.source_id, item.name), []).append(item.id)
    lineage: list[dict] = []
    joins: list[dict] = []
    diagnostics: list[dict] = []
    mapped = mapping or DatasetMapping()

    def append_lineage(inputs: list[str], output_id: str, record: dict, status: str) -> None:
        if output_id not in objects or any(item not in objects for item in inputs):
            diagnostics.append({"id": record.get("id", "unknown"), "code": "UNRESOLVED_OBJECT"})
            return
        digest = observation_digest(record)
        for input_id in sorted(set(inputs)):
            if input_id == output_id:
                continue
            lineage.append(
                {
                    "id": hashlib.sha256(f"{digest}:{input_id}:{output_id}".encode()).hexdigest(),
                    "from_id": input_id,
                    "to_id": output_id,
                    "status": status,
                    "observed_at": record.get("observed_at") or record.get("eventTime"),
                    "provenance_id": record.get("id") or digest,
                    "evidence_digest": digest,
                    "limitations": ["multi_input_column_mapping_unresolved"]
                    if len(inputs) > 1
                    else [],
                }
            )

    for record in read_lines(observations, config.max_observation_bytes, config.max_observations):
        kind = record.get("kind")
        if kind == "sql":
            observed = SQLObservation.model_validate(record)
            try:
                target_name, read_names, join_names = sql_relations(observed.sql, observed.dialect)

                source_id = observed.source_id

                def resolve(name: str, source_id: str = source_id) -> str:
                    ids = by_name.get((source_id, name), [])
                    if len(ids) != 1:
                        raise ValueError("UNRESOLVED_OR_AMBIGUOUS_SQL_OBJECT")
                    return ids[0]

                read_ids = [resolve(name) for name in read_names]
                if target_name is not None:
                    append_lineage(
                        read_ids,
                        resolve(target_name),
                        {
                            "id": observed.id,
                            "observed_at": observed.observed_at.isoformat(),
                            "digest": observation_digest(record),
                        },
                        observed.status,
                    )
                for left, right in join_names:
                    joins.append(
                        {
                            "from_id": resolve(left),
                            "to_id": resolve(right),
                            "provenance_id": observed.id,
                            "evidence_digest": observation_digest(record),
                            "observed_at": observed.observed_at.isoformat(),
                        }
                    )
            except Exception as error:
                diagnostics.append(
                    {"id": observed.id, "code": "UNRESOLVED_SQL", "detail": type(error).__name__}
                )
        elif kind == "pipeline":
            pipeline = PipelineObservation.model_validate(record)
            append_lineage(
                pipeline.input_object_ids,
                pipeline.output_object_id,
                {
                    "id": pipeline.id,
                    "observed_at": pipeline.observed_at.isoformat(),
                    "digest": observation_digest(record),
                },
                pipeline.status,
            )
        elif record.get("eventType") or kind == "openlineage_job":
            event = OpenLineageEvent.model_validate(record)
            event_type = event.eventType
            if event_type not in {"COMPLETE", None}:
                continue
            if len(event.outputs) != 1:
                diagnostics.append(
                    {"id": record.get("id", "unknown"), "code": "AMBIGUOUS_OPENLINEAGE_OUTPUT"}
                )
                continue

            def map_dataset(value) -> str | None:
                return mapped.dataset_to_object_id.get(f"{value.namespace}/{value.name}")

            input_ids = [map_dataset(value) for value in event.inputs]
            output_id = map_dataset(event.outputs[0])
            if not input_ids or any(value is None for value in input_ids) or output_id is None:
                diagnostics.append(
                    {"id": record.get("id", "unknown"), "code": "UNMAPPED_OPENLINEAGE_DATASET"}
                )
                continue
            append_lineage(
                [value for value in input_ids if value is not None],
                output_id,
                record,
                "executed" if event_type == "COMPLETE" else "defined",
            )
        else:
            diagnostics.append({"id": record.get("id", "unknown"), "code": "UNKNOWN_OBSERVATION"})

    graph = {
        "ir_version": manifest.ir_version,
        "snapshot": str(snapshot.resolve(strict=True)),
        "snapshot_digest": _snapshot_digest(snapshot),
        "completeness": manifest.completeness,
        "sources": {key: value.model_dump(mode="json") for key, value in sources.items()},
        "objects": {key: value.model_dump(mode="json") for key, value in objects.items()},
        "fields": {key: value.model_dump(mode="json") for key, value in fields.items()},
        "references": sorted(references, key=lambda item: item["id"]),
        "lineage": sorted(lineage, key=lambda item: item["id"]),
        "observed_joins": sorted(joins, key=lambda item: (item["from_id"], item["to_id"])),
        "diagnostics": diagnostics,
    }
    output.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix="graph-", suffix=".json", dir=output)
    os.close(descriptor)
    temporary = Path(name)
    try:
        temporary.write_text(json.dumps(graph, sort_keys=True), encoding="utf-8")
        os.replace(temporary, output / "graph.json")
    finally:
        temporary.unlink(missing_ok=True)
    return graph


def load_graph(directory: Path) -> dict:
    graph = json.loads((directory / "graph.json").read_text(encoding="utf-8"))
    snapshot = Path(graph["snapshot"])
    read_artifact(snapshot)
    if _snapshot_digest(snapshot) != graph["snapshot_digest"]:
        raise ValueError("STALE_GRAPH")
    return graph
