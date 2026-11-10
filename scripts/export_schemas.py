"""Export the versioned JSON Schema documents from boundary models."""

from __future__ import annotations

import json
from pathlib import Path

from dbig_contracts.schemas import json_schemas
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    destination = ROOT / "schemas" / "ir-v1"
    destination.mkdir(parents=True, exist_ok=True)
    for name, schema in json_schemas().items():
        Draft202012Validator.check_schema(schema)
        path = destination / f"{name}.schema.json"
        path.write_text(
            json.dumps(schema, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
