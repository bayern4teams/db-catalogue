"""Regenerate `docs/API.md` from the OpenAPI documents of the five running services
(owner decision 6, 2026-11-21: "API.md lists every route and field from the OpenAPI").

The document is generated, never hand-edited: the services are the source of truth.

    python scripts/export_api_docs.py --out docs/API.md \
        --key-file deploy/secrets/<a key with a caller key for every service>

Reads `http://127.0.0.1:<port>/openapi.json` for each service (ports from deploy/.env, the
loopback rehearsal values as defaults) with `Authorization: Bearer <key>`, because every route
including /openapi.json needs a caller key (decision 16). Set --base to a host other than
127.0.0.1 when reading a deployment. Placeholder rule (owner decision 1): the file names the
services by port setting, never by an address.
"""

from __future__ import annotations

import argparse
import http.client
import json
from pathlib import Path
from typing import Any

# Service, port setting, default port (deploy/.env names, same order as deploy/compose.yaml).
SERVICES = [
    ("collectors", "COLLECTOR_PORT", 8441),
    ("catalog-builder", "BUILDER_PORT", 8442),
    ("requester", "REQUESTER_PORT", 8443),
    ("planner", "PLANNER_PORT", 8444),
    ("contracts", "CONTRACTS_PORT", 8445),
]


def fetch(base: str, port: int, key: str) -> dict[str, Any]:
    # http.client, not urlopen: the scheme is http by fixed design (a deployment address is
    # never taken from user input here), which is also why no audit-url exception is needed.
    connection = http.client.HTTPConnection(base, port, timeout=30)
    try:
        connection.request("GET", "/openapi.json", headers={"Authorization": f"Bearer {key}"})
        response = connection.getresponse()
        if response.status != 200:
            raise SystemExit(f"{base}:{port} answered {response.status}; a caller key is needed")
        return json.loads(response.read())
    finally:
        connection.close()


def ref_name(schema: dict[str, Any]) -> str | None:
    ref = schema.get("$ref")
    return ref.rsplit("/", 1)[-1] if ref else None


def type_text(schema: dict[str, Any]) -> str:
    if ref := ref_name(schema):
        return ref
    if "anyOf" in schema:
        parts = " or ".join(type_text(part) for part in schema["anyOf"])
        return f"{parts}" if len(schema["anyOf"]) > 1 else parts
    if schema.get("type") == "array":
        return f"list[{type_text(schema.get('items', {}))}]"
    if schema.get("enum"):
        return " / ".join(f"`{v}`" for v in schema["enum"])
    kind = schema.get("type", "object")
    if fmt := schema.get("format"):
        kind = f"{kind}({fmt})"
    return kind


def body_of(operation: dict[str, Any]) -> dict[str, Any] | None:
    content = operation.get("requestBody", {}).get("content", {})
    schema = content.get("application/json", {}).get("schema")
    return schema


def response_of(operation: dict[str, Any]) -> dict[str, Any] | None:
    for code, response in sorted(operation.get("responses", {}).items()):
        if code.startswith("2"):
            schema = response.get("content", {}).get("application/json", {}).get("schema")
            return schema
    return None


def fields(doc: dict[str, Any], name: str) -> list[tuple[str, str, str, str]]:
    """One row per property of a component schema: name, type, required, description."""
    schema = doc["components"]["schemas"].get(name, {})
    props = schema.get("properties", {})
    required = set(schema.get("required", []))
    rows = []
    for prop, spec in props.items():
        description = (spec.get("description") or "—").replace("\n", " ")
        rows.append((prop, type_text(spec), "yes" if prop in required else "no", description))
    return rows


def render(documents: list[tuple[str, str, int, dict[str, Any]]]) -> str:
    lines = [
        "# API",
        "",
        "Every route and field of the five services, generated from their live OpenAPI",
        "documents by `python scripts/export_api_docs.py` — do not hand-edit. All routes,",
        "including `/openapi.json` and `/docs`, need a caller key in the `Authorization:",
        "Bearer <key>` header (owner decision 16, 2026-11-21); scopes per route are listed",
        "under each service. Replace `<app-machine address>` with the host address; the",
        "ports are the `deploy/.env` settings named in the headings.",
        "",
    ]
    for service, port_env, port_default, doc in documents:
        lines += [f"## {service} (`{port_env}`, default {port_default})", ""]
        lines += ["Health: `GET /healthz` (no key).", ""]
        for path, item in sorted(doc.get("paths", {}).items()):
            for method, op in sorted(item.items()):
                if method.upper() not in {"GET", "POST", "PUT", "DELETE", "PATCH"}:
                    continue
                summary = op.get("summary", "")
                lines += [f"### `{method.upper()} {path}`", "", summary, ""]
                params = op.get("parameters", [])
                if params:
                    lines += ["| Path/query parameter | Type | Required |", "| --- | --- | --- |"]
                    for p in params:
                        lines.append(f"| `{p['name']}` | {type_text(p.get('schema', {}))} | "
                                     f"{'yes' if p.get('required') else 'no'} |")
                    lines.append("")
                if (schema := body_of(op)) and (name := ref_name(schema) or ""):
                    lines += ["Request body (`" + name + "`):", "",
                              "| Field | Type | Required | Description |",
                              "| --- | --- | --- | --- |"]
                    lines += [f"| `{f}` | {t} | {r} | {d} |" for f, t, r, d in fields(doc, name)]
                    lines.append("")
                if (schema := response_of(op)) and (name := ref_name(schema)):
                    lines += ["Response body (`" + name + "`):", "",
                              "| Field | Type | Required | Description |",
                              "| --- | --- | --- | --- |"]
                    lines += [f"| `{f}` | {t} | {r} | {d} |" for f, t, r, d in fields(doc, name)]
                    lines.append("")
                codes = sorted(op.get("responses", {}))
                lines += [f"Status codes: {', '.join(codes)}.", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="127.0.0.1")
    parser.add_argument("--key-file", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    arguments = parser.parse_args()
    key = arguments.key_file.read_text().strip()
    documents = []
    for service, port_env, port_default in SERVICES:
        document = fetch(arguments.base, port_default, key)
        documents.append((service, port_env, port_default, document))
    arguments.out.write_text(render(documents))
    print(f"wrote {arguments.out} from {len(documents)} services")


if __name__ == "__main__":
    main()
