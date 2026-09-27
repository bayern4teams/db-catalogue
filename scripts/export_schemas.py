"""Export the versioned JSON Schema documents from boundary models."""

from __future__ import annotations

import json
from pathlib import Path

from dbig_contracts.models import (
    Constraint,
    Diagnostic,
    Edge,
    Evidence,
    Field,
    Manifest,
    Object,
    SnapshotNode,
    Source,
)
from dbig_contracts.scan import ScanReceipt, ScanRequest, SignedScanRequest
from jsonschema import Draft202012Validator
from pydantic import TypeAdapter

ROOT = Path(__file__).resolve().parents[1]
MODELS = {
    "source": Source,
    "object": Object,
    "field": Field,
    "constraint": Constraint,
    "diagnostic": Diagnostic,
    "evidence": Evidence,
    "edge": Edge,
    "manifest": Manifest,
    "scan-request": ScanRequest,
    "signed-scan-request": SignedScanRequest,
    "scan-receipt": ScanReceipt,
}


def main() -> None:
    destination = ROOT / "schemas" / "ir-v1"
    destination.mkdir(parents=True, exist_ok=True)
    schemas = {name: model.model_json_schema() for name, model in MODELS.items()}
    # A snapshot node row is a discriminated union, not a single model, so it is exported from a
    # TypeAdapter rather than from MODELS.
    schemas["node"] = TypeAdapter(SnapshotNode).json_schema()
    for name, schema in schemas.items():
        Draft202012Validator.check_schema(schema)
        path = destination / f"{name}.schema.json"
        path.write_text(
            json.dumps(schema, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
