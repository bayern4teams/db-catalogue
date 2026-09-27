"""Regenerate the committed golden artifacts.

The golden artifacts are the compatibility fixture: an artifact produced by one version of this
repository, committed, and read back by every later version. Because the requester and planner
install independently of the builder and collectors, a consumer in the field can be older than
the snapshot it reads, so the reader contract has to be pinned by a real artifact rather than by
a test that writes and reads in the same process.

Run this only to record a deliberate contract change, and review the resulting diff: an
unexpected change here is the point of the fixture. The clock is fixed so a regeneration with no
contract change produces no diff at all, and every value is synthetic.

    uv run python scripts/make_golden.py
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

from dbig_contracts import artifact as artifact_module
from dbig_contracts.artifact import write_artifact
from dbig_contracts.ids import stable_id
from dbig_contracts.models import (
    Diagnostic,
    Edge,
    Evidence,
    Field,
    Object,
    Profile,
    Record,
    Source,
)

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "golden" / "ir-v1"

# Fixed so a regeneration without a contract change is a no-op diff.
FROZEN = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
# Synthetic, published on purpose: it signs nothing real and exists so the committed fixture can
# exercise the signature path. Never reuse it for anything.
GOLDEN_KEY = b"golden-fixture-key-not-a-credential"
SOURCE_ID = "golden-site"


def _common() -> dict[str, object]:
    return {
        "source_id": SOURCE_ID,
        "observed_at": FROZEN,
        "collector_version": "golden",
        "provenance": "synthetic",
    }


def bundle_tables() -> dict[str, list[Record]]:
    """A collector bundle covering every file a bundle can carry."""
    common = _common()
    source = Source(id=SOURCE_ID, kind="postgresql", name="golden", **common)
    customers = Object(
        id=stable_id(SOURCE_ID, "object", "16400"),
        native_id="16400",
        kind="table",
        name="customers",
        namespace="public",
        **common,
    )
    identifier = Field(
        id=stable_id(SOURCE_ID, "field", "16400:1"),
        object_id=customers.id,
        native_id="16400:1",
        name="id",
        data_type="integer",
        nullable=False,
        ordinal=1,
        **common,
    )
    return {
        "sources.jsonl": [source],
        "objects.jsonl": [customers],
        "fields.jsonl": [identifier],
        "profiles.jsonl": [
            Profile(
                id=stable_id(SOURCE_ID, "profile", f"{identifier.id}:source_statistic"),
                field_id=identifier.id,
                object_id=customers.id,
                method="source_statistic",
                null_fraction=0.0,
                distinct_estimate=100.0,
                distinct_is_ratio_derived=True,
                uniqueness=1.0,
                average_width_bytes=4,
                **common,
            )
        ],
        "diagnostics.jsonl": [
            Diagnostic(
                id=stable_id(SOURCE_ID, "diagnostic", "public:legacy_view"),
                code="IDENTITY_UNAVAILABLE",
                message="synthetic",
                **common,
            )
        ],
    }


def snapshot_tables() -> dict[str, list[Record]]:
    """A published snapshot carrying all three node types and one declared edge."""
    common = _common()
    bundle = bundle_tables()
    source = bundle["sources.jsonl"][0]
    obj = bundle["objects.jsonl"][0]
    field = bundle["fields.jsonl"][0]
    evidence = Evidence(
        id=stable_id(SOURCE_ID, "evidence", "catalog_key"),
        kind="catalog_key",
        subject_id=field.id,
        **common,
    )
    edge = Edge(
        id=stable_id(SOURCE_ID, "edge", "declares_fk"),
        kind="DECLARES_FK",
        from_id=obj.id,
        to_id=obj.id,
        status="declared",
        evidence_ids=[evidence.id],
        field_pairs=[[field.id, field.id]],
        **common,
    )
    run = Record(id=stable_id(SOURCE_ID, "run", "golden"), record_type="run", **common)
    return {
        "nodes.jsonl": [source, obj, field],
        "edges.jsonl": [edge],
        "evidence.jsonl": [evidence],
        "runs.jsonl": [run],
    }


def main() -> None:
    original = artifact_module.utc_now
    artifact_module.utc_now = lambda: FROZEN  # type: ignore[assignment]
    try:
        shutil.rmtree(GOLDEN, ignore_errors=True)
        GOLDEN.mkdir(parents=True)
        write_artifact(
            GOLDEN / "collector-bundle",
            "collector-bundle",
            bundle_tables(),
            producer="golden",
            source_run_ids=[stable_id(SOURCE_ID, "run", "golden")],
            completeness="partial",
            signing_key=GOLDEN_KEY,
            key_id="golden",
        )
        write_artifact(
            GOLDEN / "catalog-snapshot",
            "catalog-snapshot",
            snapshot_tables(),
            producer="golden",
            source_run_ids=[stable_id(SOURCE_ID, "run", "golden")],
            signing_key=GOLDEN_KEY,
            key_id="golden",
        )
    finally:
        artifact_module.utc_now = original  # type: ignore[assignment]
    print(f"golden artifacts written to {GOLDEN.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
