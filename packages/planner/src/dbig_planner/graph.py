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
    OpenLineageColumnFacet,
    OpenLineageDatasetFacet,
    OpenLineageEvent,
    OpenLineageLineageEntry,
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
    fields_by_name: dict[tuple[str, str], list[str]] = {}
    for field_item in fields.values():
        fields_by_name.setdefault((field_item.object_id, field_item.name), []).append(
            field_item.id
        )
    lineage: list[dict] = []
    lineage_ids: set[str] = set()
    field_lineage: list[dict] = []
    field_lineage_ids: set[str] = set()
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
            edge_id = hashlib.sha256(f"{digest}:{input_id}:{output_id}".encode()).hexdigest()
            if edge_id in lineage_ids:
                continue
            lineage_ids.add(edge_id)
            lineage.append(
                {
                    "id": edge_id,
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

    def append_field_lineage(
        input_ref, output_id: str, output_name: str, record: dict, status: str
    ) -> None:
        source_object_id = mapped.dataset_to_object_id.get(
            f"{input_ref.namespace}/{input_ref.name}"
        )
        source_name = input_ref.field
        output_fields = fields_by_name.get((output_id, output_name), [])
        source_fields = (
            fields_by_name.get((source_object_id, source_name), [])
            if source_object_id is not None and source_name is not None
            else []
        )
        if len(output_fields) != 1 or len(source_fields) != 1 or source_object_id is None:
            diagnostics.append(
                {"id": record.get("id", "unknown"), "code": "UNMAPPED_OPENLINEAGE_FIELD"}
            )
            return
        append_lineage([source_object_id], output_id, record, status)
        digest = observation_digest(record)
        field_edge_id = hashlib.sha256(
            f"{digest}:{source_fields[0]}:{output_fields[0]}".encode()
        ).hexdigest()
        if field_edge_id in field_lineage_ids:
            return
        field_lineage_ids.add(field_edge_id)
        field_lineage.append(
            {
                "id": field_edge_id,
                "from_id": source_fields[0],
                "to_id": output_fields[0],
                "status": status,
                "observed_at": record["eventTime"],
                "provenance_id": record.get("id") or digest,
                "evidence_digest": digest,
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

            def map_dataset(value) -> str | None:
                return mapped.dataset_to_object_id.get(f"{value.namespace}/{value.name}")

            job_facets = event.job.get("facets") or {}
            if not isinstance(job_facets, dict):
                diagnostics.append(
                    {"id": record.get("id", "unknown"), "code": "INVALID_OPENLINEAGE_JOB_FACETS"}
                )
                continue
            if "lineage" in job_facets:
                lineage_facet = job_facets["lineage"]
                entries = lineage_facet.get("entries") if isinstance(lineage_facet, dict) else None
                if not isinstance(entries, list):
                    diagnostics.append(
                        {
                            "id": record.get("id", "unknown"),
                            "code": "INVALID_OPENLINEAGE_LINEAGE_FACET",
                        }
                    )
                    continue
                for raw_entry in entries:
                    try:
                        entry = OpenLineageLineageEntry.model_validate(raw_entry)
                    except ValueError:
                        diagnostics.append(
                            {
                                "id": record.get("id", "unknown"),
                                "code": "UNSUPPORTED_OPENLINEAGE_LINEAGE_ENTRY",
                            }
                        )
                        continue
                    output_id = map_dataset(entry)
                    input_ids = [map_dataset(value) for value in entry.inputs]
                    if output_id is None or any(value is None for value in input_ids):
                        diagnostics.append(
                            {
                                "id": record.get("id", "unknown"),
                                "code": "UNMAPPED_OPENLINEAGE_DATASET",
                            }
                        )
                        continue
                    append_lineage(
                        [value for value in input_ids if value is not None],
                        output_id,
                        record,
                        "executed" if event_type == "COMPLETE" else "defined",
                    )
                    for output_name, field_entry in entry.fields.items():
                        for input_ref in field_entry.inputs:
                            append_field_lineage(
                                input_ref, output_id, output_name, record,
                                "executed" if event_type == "COMPLETE" else "defined",
                            )
                continue
            explicit_outputs = [
                value for value in event.outputs
                if "lineage" in value.facets or "columnLineage" in value.facets
            ]
            if explicit_outputs:
                for output_dataset in event.outputs:
                    if output_dataset not in explicit_outputs:
                        diagnostics.append(
                            {
                                "id": record.get("id", "unknown"),
                                "code": "OPENLINEAGE_OUTPUT_WITHOUT_EXPLICIT_LINEAGE",
                            }
                        )
                        continue
                    output_id = map_dataset(output_dataset)
                    if output_id is None:
                        diagnostics.append(
                            {
                                "id": record.get("id", "unknown"),
                                "code": "UNMAPPED_OPENLINEAGE_DATASET",
                            }
                        )
                        continue
                    if "lineage" in output_dataset.facets:
                        try:
                            exact = OpenLineageDatasetFacet.model_validate(
                                output_dataset.facets["lineage"]
                            )
                        except ValueError:
                            diagnostics.append(
                                {
                                    "id": record.get("id", "unknown"),
                                    "code": "INVALID_DATASET_LINEAGE_FACET",
                                }
                            )
                            continue
                        input_ids = [map_dataset(value) for value in exact.inputs]
                        if any(value is None for value in input_ids):
                            diagnostics.append(
                                {
                                    "id": record.get("id", "unknown"),
                                    "code": "UNMAPPED_OPENLINEAGE_DATASET",
                                }
                            )
                        else:
                            append_lineage(
                                [value for value in input_ids if value is not None],
                                output_id, record,
                                "executed" if event_type == "COMPLETE" else "defined",
                            )
                        for output_name, exact_entry in exact.fields.items():
                            for exact_input in exact_entry.inputs:
                                append_field_lineage(
                                    exact_input, output_id, output_name, record,
                                    "executed" if event_type == "COMPLETE" else "defined",
                                )
                        continue
                    try:
                        facet = OpenLineageColumnFacet.model_validate(
                            output_dataset.facets["columnLineage"]
                        )
                    except ValueError:
                        diagnostics.append(
                            {"id": record.get("id", "unknown"), "code": "INVALID_COLUMN_FACET"}
                        )
                        continue
                    for column_input in facet.dataset:
                        column_source_id = map_dataset(column_input)
                        if column_source_id is None:
                            diagnostics.append(
                                {
                                    "id": record.get("id", "unknown"),
                                    "code": "UNMAPPED_OPENLINEAGE_DATASET",
                                }
                            )
                        else:
                            append_lineage(
                                [column_source_id], output_id, record,
                                "executed" if event_type == "COMPLETE" else "defined",
                            )
                    for output_name, column_entry in facet.fields.items():
                        for column_input in column_entry.inputFields:
                            append_field_lineage(
                                column_input, output_id, output_name, record,
                                "executed" if event_type == "COMPLETE" else "defined",
                            )
                continue
            if len(event.outputs) != 1:
                diagnostics.append(
                    {"id": record.get("id", "unknown"), "code": "AMBIGUOUS_OPENLINEAGE_OUTPUT"}
                )
                continue

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
        "field_lineage": sorted(field_lineage, key=lambda item: item["id"]),
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
