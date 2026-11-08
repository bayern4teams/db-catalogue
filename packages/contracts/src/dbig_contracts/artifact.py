"""Sealed, versioned JSONL artifact I/O."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, TypeAdapter
from pydantic import Field as PydanticField

from .models import (
    Constraint,
    Diagnostic,
    Edge,
    Evidence,
    Field,
    FileEntry,
    Manifest,
    Object,
    Profile,
    Record,
    Signature,
    SnapshotNode,
    Source,
    ValueRanges,
    ValueTokens,
    utc_now,
)

COLLECTOR_FILES = (
    "sources.jsonl",
    "objects.jsonl",
    "fields.jsonl",
    "constraints.jsonl",
    "profiles.jsonl",
    "observations.jsonl",
    "diagnostics.jsonl",
    "tokens.jsonl",
    "ranges.jsonl",
)
# Files a bundle may lack: one written before they existed, or one with no rows to put in them.
# Local-only like `tokens.jsonl`, so a reader treats a missing one as empty.
OPTIONAL_COLLECTOR_FILES = ("ranges.jsonl",)
SNAPSHOT_FILES = ("nodes.jsonl", "edges.jsonl", "evidence.jsonl", "runs.jsonl")
MODELS: dict[str, type[Record]] = {
    "sources.jsonl": Source,
    "objects.jsonl": Object,
    "fields.jsonl": Field,
    "constraints.jsonl": Constraint,
    "edges.jsonl": Edge,
    "evidence.jsonl": Evidence,
    "diagnostics.jsonl": Diagnostic,
    "profiles.jsonl": Profile,
    "tokens.jsonl": ValueTokens,
    "ranges.jsonl": ValueRanges,
}
# `nodes.jsonl` carries three record types, so it validates against the discriminated union
# rather than one model. `runs.jsonl` intentionally has no entry and stays a base `Record`.
NODE_ADAPTER: TypeAdapter[Source | Object | Field] = TypeAdapter(SnapshotNode)


def validate_row(name: str, data: Any) -> Record:
    """Validate one artifact row against the strictest model that file's contract allows."""
    if name == "nodes.jsonl":
        return NODE_ADAPTER.validate_python(data)
    return MODELS.get(name, Record).model_validate(data)


class ArtifactLimits(BaseModel):
    """Defensive reader ceilings; callers may lower them for a deployment."""

    max_manifest_bytes: int = PydanticField(default=1_048_576, gt=0)
    max_file_bytes: int = PydanticField(default=67_108_864, gt=0)
    max_record_bytes: int = PydanticField(default=1_048_576, gt=0)
    max_records: int = PydanticField(default=100_000, gt=0)


def canonical(value: BaseModel | dict[str, Any]) -> bytes:
    data = (
        value.model_dump(mode="json", exclude_none=True) if isinstance(value, BaseModel) else value
    )
    return json.dumps(
        data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _parse(data: bytes) -> Any:
    return json.loads(
        data,
        object_pairs_hook=_pairs,
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError(f"invalid JSON constant: {value}")
        ),
    )


def _diagnostic_counts(records: Sequence[Record | dict[str, Any]]) -> dict[str, int]:
    """Count diagnostics by code, in sorted order so the manifest bytes stay deterministic."""
    counts: dict[str, int] = {}
    for record in records:
        data = record.model_dump(mode="json") if isinstance(record, BaseModel) else record
        code = validate_row("diagnostics.jsonl", data).model_dump()["code"]
        counts[code] = counts.get(code, 0) + 1
    return dict(sorted(counts.items()))


def _unsigned(manifest: Manifest) -> bytes:
    return canonical(manifest.model_dump(mode="json", exclude={"signature"}, exclude_none=True))


def _validate_object_parents(objects: dict[str, Object]) -> None:
    visited: dict[str, int] = {}
    for object_id in objects:
        chain: list[str] = []
        current_id: str | None = object_id
        while current_id is not None:
            state = visited.get(current_id, 0)
            if state == 2:
                break
            if state == 1:
                raise ValueError(f"cyclic object parent: {current_id}")
            item = objects[current_id]
            visited[current_id] = 1
            chain.append(current_id)
            parent_id = item.parent_id
            if parent_id is not None:
                parent = objects.get(parent_id)
                if parent is None or parent.source_id != item.source_id:
                    raise ValueError(f"invalid object parent: {item.id}")
            current_id = parent_id
        for item_id in chain:
            visited[item_id] = 2


def write_artifact(
    destination: Path,
    content_type: str,
    tables: Mapping[str, Sequence[Record | dict[str, Any]]],
    *,
    producer: str,
    source_run_ids: list[str],
    completeness: str = "complete",
    partial_reasons: list[str] | None = None,
    signing_key: bytes | None = None,
    key_id: str | None = None,
) -> Manifest:
    """Publish to a new directory; a manifest appears only after all files are durable."""
    expected = COLLECTOR_FILES if content_type == "collector-bundle" else SNAPSHOT_FILES
    if content_type not in ("collector-bundle", "catalog-snapshot"):
        raise ValueError("unknown content type")
    if set(tables) - set(expected):
        raise ValueError("unknown artifact table")
    if signing_key is not None and not key_id:
        raise ValueError("signed artifacts require a key ID")
    destination = destination.resolve()
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent))
    try:
        entries: list[FileEntry] = []
        for name in expected:
            records = tables.get(name, ())
            if name in OPTIONAL_COLLECTOR_FILES and not records:
                # Left out when empty, so a bundle that has none stays readable by a reader that
                # predates the file.
                continue
            path = temporary / name
            digest = hashlib.sha256()
            size = 0
            with path.open("wb") as stream:
                for record in records:
                    data = (
                        record.model_dump(mode="json") if isinstance(record, BaseModel) else record
                    )
                    model = validate_row(name, data)
                    line = canonical(model) + b"\n"
                    stream.write(line)
                    digest.update(line)
                    size += len(line)
                stream.flush()
                os.fsync(stream.fileno())
            entries.append(
                FileEntry(path=name, bytes=size, sha256=digest.hexdigest(), count=len(records))
            )
        now = utc_now()
        fields: dict[str, Any] = {
            "ir_version": "1.0",
            "content_type": content_type,
            "producer": producer,
            "source_run_ids": source_run_ids,
            "created_at": now,
            "as_of": now,
            "completeness": completeness,
            "files": entries,
        }
        if partial_reasons:
            # Code names only, never content; absent when complete so those bytes are unchanged.
            fields["partial_reasons"] = sorted(partial_reasons)
        if "diagnostics.jsonl" in expected:
            # Derived here rather than passed in, so a producer cannot under-report what it
            # dropped. Snapshots carry no diagnostics file and so report no summary at all.
            fields["diagnostics"] = _diagnostic_counts(tables.get("diagnostics.jsonl", ()))
        manifest = Manifest.model_validate(fields)
        if signing_key is not None:
            manifest.signature = Signature(
                key_id=key_id or "",
                value=hmac.new(signing_key, _unsigned(manifest), hashlib.sha256).hexdigest(),
            )
        with (temporary / "manifest.json").open("wb") as stream:
            stream.write(canonical(manifest))
            stream.flush()
            os.fsync(stream.fileno())
        read_artifact(temporary, signing_key=signing_key)
        os.rename(temporary, destination)
        return manifest
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def read_artifact(
    directory: Path,
    *,
    limits: ArtifactLimits | None = None,
    signing_key: bytes | None = None,
    require_signature: bool = False,
) -> tuple[Manifest, dict[str, list[Record]]]:
    """Read only a complete, digest-verified, referentially valid artifact."""
    limits = limits or ArtifactLimits()
    directory = directory.resolve(strict=True)
    manifest_path = directory / "manifest.json"
    if manifest_path.is_symlink() or manifest_path.stat().st_size > limits.max_manifest_bytes:
        raise ValueError("unsafe or oversized manifest")
    manifest = Manifest.model_validate(_parse(manifest_path.read_bytes()))
    if require_signature and manifest.signature is None:
        raise ValueError("missing artifact signature")
    if manifest.signature is not None:
        if signing_key is None:
            raise ValueError("signature key unavailable")
        expected_signature = hmac.new(signing_key, _unsigned(manifest), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected_signature, manifest.signature.value):
            raise ValueError("invalid artifact signature")
    expected = COLLECTOR_FILES if manifest.content_type == "collector-bundle" else SNAPSHOT_FILES
    listed = [entry.path for entry in manifest.files]
    older = [name for name in expected if name not in OPTIONAL_COLLECTOR_FILES]
    if listed != list(expected) and not (
        manifest.content_type == "collector-bundle" and listed == older
    ):
        raise ValueError("artifact file set or order mismatch")
    result: dict[str, list[Record]] = {}
    all_ids: set[str] = set()
    for entry in manifest.files:
        path = directory / entry.path
        if path.is_symlink() or not path.is_file() or path.stat().st_size != entry.bytes:
            raise ValueError(f"missing, linked, or size-mismatched file: {entry.path}")
        if entry.bytes > limits.max_file_bytes or entry.count > limits.max_records:
            raise ValueError("artifact exceeds configured limits")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != entry.sha256:
            raise ValueError(f"artifact digest mismatch: {entry.path}")
        records = []
        for line in raw.splitlines():
            if len(line) > limits.max_record_bytes:
                raise ValueError("oversized artifact record")
            record = validate_row(entry.path, _parse(line))
            if record.id in all_ids:
                raise ValueError(f"duplicate record ID: {record.id}")
            all_ids.add(record.id)
            records.append(record)
        if len(records) != entry.count:
            raise ValueError(f"record count mismatch: {entry.path}")
        result[entry.path] = records
    if manifest.diagnostics is not None:
        # An older producer reports no summary at all, which stays readable; one that does report
        # must report the truth, so a manifest cannot hide objects a run dropped. The counts are
        # inside the signed bytes, so this catches an unsigned producer's error rather than a
        # tampered signed bundle, which fails verification first.
        actual = _diagnostic_counts(result.get("diagnostics.jsonl", []))
        if manifest.diagnostics != actual:
            raise ValueError("manifest diagnostic summary disagrees with diagnostics.jsonl")
    nodes = {record.id: record for record in result.get("nodes.jsonl", [])}
    node_ids = set(nodes)
    evidence_ids = {record.id for record in result.get("evidence.jsonl", [])}
    object_records = (
        (record for record in nodes.values() if record.record_type == "object")
        if manifest.content_type == "catalog-snapshot"
        else result["objects.jsonl"]
    )
    _validate_object_parents(
        {record.id: Object.model_validate(record.model_dump()) for record in object_records}
    )
    if manifest.content_type == "catalog-snapshot":
        for node in nodes.values():
            # `record_type` is already constrained to source/object/field by NODE_ADAPTER.
            if node.record_type == "field":
                field = Field.model_validate(node.model_dump())
                owner = nodes.get(field.object_id)
                if owner is None or owner.record_type != "object":
                    raise ValueError(f"dangling snapshot field: {field.id}")
            if node.record_type != "source" and node.source_id not in nodes:
                raise ValueError(f"dangling snapshot source: {node.id}")
        for record in result["evidence.jsonl"]:
            item = Evidence.model_validate(record.model_dump())
            if item.object_id is not None and item.object_id not in nodes:
                raise ValueError(f"dangling evidence target: {item.id}")
        run_ids = {record.id for record in result["runs.jsonl"]}
        if set(manifest.source_run_ids) != run_ids:
            raise ValueError("snapshot run references mismatch")
    for record in result.get("edges.jsonl", []):
        edge = Edge.model_validate(record)
        if edge.from_id not in node_ids or edge.to_id not in node_ids:
            raise ValueError(f"dangling edge: {edge.id}")
        if not set(edge.evidence_ids) <= evidence_ids:
            raise ValueError(f"dangling evidence: {edge.id}")
        if manifest.content_type == "catalog-snapshot":
            for pair in getattr(edge, "field_pairs", []):
                if not isinstance(pair, list) or len(pair) != 2:
                    raise ValueError(f"invalid edge field pair: {edge.id}")
                left = nodes.get(pair[0]) if isinstance(pair[0], str) else None
                right = nodes.get(pair[1]) if isinstance(pair[1], str) else None
                if (
                    left is None
                    or right is None
                    or left.record_type != "field"
                    or right.record_type != "field"
                    or Field.model_validate(left.model_dump()).object_id != edge.from_id
                    or Field.model_validate(right.model_dump()).object_id != edge.to_id
                ):
                    raise ValueError(f"dangling edge field pair: {edge.id}")
    object_ids = {record.id for record in result.get("objects.jsonl", [])}
    for record in result.get("fields.jsonl", []):
        field = Field.model_validate(record)
        if field.object_id not in object_ids:
            raise ValueError(f"dangling field: {field.id}")
    for record in result.get("constraints.jsonl", []):
        constraint = Constraint.model_validate(record)
        if constraint.object_id not in object_ids or (
            constraint.target_object_id is not None
            and constraint.target_object_id not in object_ids
        ):
            raise ValueError(f"dangling constraint: {constraint.id}")
    if manifest.content_type == "collector-bundle":
        sources = result["sources.jsonl"]
        if len(sources) != 1:
            raise ValueError("collector bundle must have exactly one source")
        source_id = sources[0].id
        if any(record.source_id != source_id for table in result.values() for record in table):
            raise ValueError("collector bundle mixes source IDs")
    return manifest, result
