"""Local, rebuildable receipt state for incremental bundle assembly."""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path

from dbig_contracts.artifact import canonical
from dbig_contracts.models import Manifest, Source

from .builder import _read_signed, assemble
from .config import BuilderConfig

SCHEMA = """
CREATE TABLE IF NOT EXISTS receipts (
    digest TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    bundle_path TEXT NOT NULL,
    completeness TEXT NOT NULL CHECK (completeness IN ('complete', 'partial'))
);
CREATE TABLE IF NOT EXISTS source_state (
    source_id TEXT PRIMARY KEY,
    active_path TEXT NOT NULL,
    active_digest TEXT NOT NULL REFERENCES receipts(digest),
    active_completeness TEXT NOT NULL CHECK (active_completeness IN ('complete', 'partial')),
    latest_digest TEXT NOT NULL REFERENCES receipts(digest),
    latest_completeness TEXT NOT NULL CHECK (latest_completeness IN ('complete', 'partial'))
);
"""


def assemble_incremental(
    bundle_paths: list[Path], state_path: Path, output: Path, config: BuilderConfig
) -> Manifest:
    """Publish from latest complete bundles, preserving them across partial rescans."""
    incoming: list[tuple[str, str, Path, str]] = []
    batch_sources: set[str] = set()
    for path in bundle_paths:
        manifest, records = _read_signed(path, config)
        bundle_sources = records["sources.jsonl"]
        if len(bundle_sources) != 1:
            raise ValueError("bundle must describe exactly one source")
        source = Source.model_validate(bundle_sources[0])
        if source.id in batch_sources:
            raise ValueError("duplicate source in incremental batch")
        batch_sources.add(source.id)
        digest = hashlib.sha256(canonical(manifest)).hexdigest()
        incoming.append((source.id, digest, path.resolve(strict=True), manifest.completeness))

    state_path = state_path.resolve()
    if not incoming and not state_path.exists():
        raise ValueError("no bundles or existing state")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(state_path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(SCHEMA)
        connection.execute("BEGIN IMMEDIATE")
        try:
            for source_id, digest, path, completeness in incoming:
                prior_receipt = connection.execute(
                    "SELECT source_id FROM receipts WHERE digest = ?", (digest,)
                ).fetchone()
                if prior_receipt is not None:
                    if prior_receipt[0] != source_id:
                        raise ValueError("receipt digest belongs to another source")
                    continue
                prior = connection.execute(
                    "SELECT active_path, active_digest, active_completeness "
                    "FROM source_state WHERE source_id = ?",
                    (source_id,),
                ).fetchone()
                connection.execute(
                    "INSERT INTO receipts VALUES (?, ?, ?, ?)",
                    (digest, source_id, str(path), completeness),
                )
                if prior is not None and completeness == "partial" and prior[2] == "complete":
                    active_path, active_digest, active_completeness = prior
                else:
                    active_path, active_digest, active_completeness = (
                        str(path), digest, completeness
                    )
                connection.execute(
                    "INSERT INTO source_state VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(source_id) DO UPDATE SET active_path=excluded.active_path, "
                    "active_digest=excluded.active_digest, "
                    "active_completeness=excluded.active_completeness, "
                    "latest_digest=excluded.latest_digest, "
                    "latest_completeness=excluded.latest_completeness",
                    (
                        source_id, active_path, active_digest, active_completeness,
                        digest, completeness,
                    ),
                )
            states = connection.execute(
                "SELECT source_id, active_path, latest_digest, latest_completeness "
                "FROM source_state ORDER BY source_id"
            ).fetchall()
            paths = [Path(row[1]) for row in states]
            partial_receipts = {
                row[0]: row[2] for row in states if row[3] == "partial"
            }
            manifest = assemble(
                paths, output, config, partial_source_receipts=partial_receipts
            )
            connection.commit()
            return manifest
        except BaseException:
            connection.rollback()
            raise
