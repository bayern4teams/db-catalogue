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

from pydantic import BaseModel
from pydantic import Field as PydanticField

from .models import (
    Edge,
    Evidence,
    Field,
    FileEntry,
    Manifest,
    Object,
    Record,
    Signature,
    Source,
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
)
SNAPSHOT_FILES = ("nodes.jsonl", "edges.jsonl", "evidence.jsonl", "runs.jsonl")
MODELS: dict[str, type[Record]] = {
    "sources.jsonl": Source,
    "objects.jsonl": Object,
    "fields.jsonl": Field,
    "nodes.jsonl": Record,
    "edges.jsonl": Edge,
    "evidence.jsonl": Evidence,
}


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


def _unsigned(manifest: Manifest) -> bytes:
    return canonical(manifest.model_dump(mode="json", exclude={"signature"}, exclude_none=True))


def write_artifact(
    destination: Path,
    content_type: str,
    tables: Mapping[str, Sequence[Record | dict[str, Any]]],
    *,
    producer: str,
    source_run_ids: list[str],
    completeness: str = "complete",
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
            path = temporary / name
            digest = hashlib.sha256()
            size = 0
            with path.open("wb") as stream:
                for record in records:
                    model = MODELS.get(name, Record).model_validate(record)
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
        manifest = Manifest.model_validate(
            {
                "ir_version": "1.0",
                "content_type": content_type,
                "producer": producer,
                "source_run_ids": source_run_ids,
                "created_at": now,
                "as_of": now,
                "completeness": completeness,
                "files": entries,
            }
        )
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
    if [entry.path for entry in manifest.files] != list(expected):
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
            record = MODELS.get(entry.path, Record).model_validate(_parse(line))
            if record.id in all_ids:
                raise ValueError(f"duplicate record ID: {record.id}")
            all_ids.add(record.id)
            records.append(record)
        if len(records) != entry.count:
            raise ValueError(f"record count mismatch: {entry.path}")
        result[entry.path] = records
    node_ids = {record.id for record in result.get("nodes.jsonl", [])}
    evidence_ids = {record.id for record in result.get("evidence.jsonl", [])}
    for record in result.get("edges.jsonl", []):
        edge = Edge.model_validate(record)
        if edge.from_id not in node_ids or edge.to_id not in node_ids:
            raise ValueError(f"dangling edge: {edge.id}")
        if not set(edge.evidence_ids) <= evidence_ids:
            raise ValueError(f"dangling evidence: {edge.id}")
    object_ids = {record.id for record in result.get("objects.jsonl", [])}
    for record in result.get("fields.jsonl", []):
        field = Field.model_validate(record)
        if field.object_id not in object_ids:
            raise ValueError(f"dangling field: {field.id}")
    return manifest, result
