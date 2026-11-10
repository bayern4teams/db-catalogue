"""The versioned JSON Schemas of the boundary models, by name.

One map for `scripts/export_schemas.py`, which writes `schemas/ir-v1/`, and for the contracts
service, which serves them: `schemas/ir-v1/` is not in the wheel, so both generate from here.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, TypeAdapter

from .models import (
    Constraint,
    Diagnostic,
    Edge,
    Evidence,
    Field,
    Manifest,
    Object,
    Profile,
    SnapshotNode,
    Source,
    ValueRanges,
    ValueTokens,
)
from .scan import ScanReceipt, ScanRequest, SignedScanRequest

MODELS: dict[str, type[BaseModel]] = {
    "source": Source,
    "object": Object,
    "field": Field,
    "constraint": Constraint,
    "diagnostic": Diagnostic,
    "profile": Profile,
    "value_tokens": ValueTokens,
    "value_ranges": ValueRanges,
    "evidence": Evidence,
    "edge": Edge,
    "manifest": Manifest,
    "scan-request": ScanRequest,
    "signed-scan-request": SignedScanRequest,
    "scan-receipt": ScanReceipt,
}
# A snapshot node row is a discriminated union, not a single model.
ADAPTERS: dict[str, TypeAdapter[Any]] = {
    **{name: TypeAdapter(model) for name, model in MODELS.items()},
    "node": TypeAdapter(SnapshotNode),
}


def json_schemas() -> dict[str, dict[str, Any]]:
    return {name: adapter.json_schema() for name, adapter in ADAPTERS.items()}
