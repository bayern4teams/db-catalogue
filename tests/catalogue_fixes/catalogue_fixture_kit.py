"""Shared fixtures for the catalogue-fixes acceptance checks (plan section 9, Phase 1 and 2).

Everything here is synthetic. Names are invented and generic (`vendor`, `shipment`, `gadget`),
never taken from a real database, so a fix that passes here works on any catalogue and cannot
have been fitted to one (plan section 2.4).

The module holds three things:

* `build_snapshot`: signed collector bundles for one or more invented sources, assembled into a
  snapshot, with planted ground truth (declared keys, measured uniqueness, value tokens, profiles).
* `ModelStub`: a recording HTTP stand-in for a llama.cpp style model server. It answers 400 to a
  schema that carries `maxLength`, `minLength`, `maxItems` or `minItems`, can cut an answer off
  with `finish_reason: length`, can delay, and `closed_port_url` gives an unreachable port.
* small readers (`candidates`, `read_nodes`) that return plain tuples, so a test says what it
  expects without depending on how the builder names its evidence.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import socket
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from dbig_builder.builder import assemble
from dbig_builder.config import BuilderConfig, ReviewConfig
from dbig_contracts.artifact import read_artifact, write_artifact
from dbig_contracts.ids import stable_id
from dbig_contracts.models import Field, Object, Profile, Source, ValueTokens

BUNDLE_KEY = "synthetic-bundle-signing-material-1234567890"
TOKEN_MATERIAL = "synthetic-value-tokenising-material-over-32-bytes"  # noqa: S105
MODEL_KEY = "loopback-model-key-for-tests"
BOUND_KEYWORDS = ("maxLength", "minLength", "maxItems", "minItems")


# --- a synthetic catalogue ----------------------------------------------------------------------


def _common(source_id: str) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "observed_at": datetime.now(UTC),
        "collector_version": "test",
        "provenance": "synthetic",
    }


def token_set(
    field_id: str, object_id: str, values: list[Any], *, source_id: str, key_id: str = "run-1"
) -> ValueTokens:
    """A keyed token set exactly as the collector builds one (HMAC-SHA256 of the text form)."""
    digests = sorted(
        {
            hmac.new(TOKEN_MATERIAL.encode(), str(value).encode(), hashlib.sha256).hexdigest()
            for value in values
        }
    )
    return ValueTokens(
        id=stable_id(source_id, "tokens", field_id),
        field_id=field_id,
        object_id=object_id,
        key_id=key_id,
        sample_count=len(values),
        sample_method="first_rows_limit:10",
        observed_count=len(values),
        distinct_count=len(digests),
        truncated=False,
        # Every value of the column is given, so the read is whole (P6-1).
        complete=True,
        tokens=digests,
        **_common(source_id),
    )


def write_bundle(path: Path, source_id: str, kind: str, tables: dict[str, dict]) -> None:
    """One signed collector bundle.

    Each table spec: `fields` [(name, type)], optional `key` [names] (declared primary key),
    `unique` {field: measured uniqueness} (a planner statistic), `values` {field: [values]}
    (planted ground truth, tokenised), `token_key` (key id, default run-1), `profiles` {field:
    {Profile kwargs}}, `descriptions` {field: text}, `kind` (default table), `foreign`
    [(child field, parent table, parent field)] (a declared foreign key within this source),
    `weak_key` field (a ClickHouse-style one-column sorting key, reported as an `index` constraint).
    """
    common = _common(source_id)
    objects, fields, constraints, profiles, tokens = [], [], [], [], []
    for number, (table, spec) in enumerate(tables.items(), start=1):
        item = Object(
            id=stable_id(source_id, "object", str(number)),
            native_id=str(number),
            kind=spec.get("kind", "table"),
            name=table,
            **common,
        )
        objects.append(item)
        for ordinal, (name, data_type) in enumerate(spec["fields"], start=1):
            field = Field(
                id=stable_id(source_id, "field", f"{number}:{ordinal}"),
                object_id=item.id,
                native_id=f"{number}:{ordinal}",
                name=name,
                data_type=data_type,
                ordinal=ordinal,
                description=spec.get("descriptions", {}).get(name),
                **common,
            )
            fields.append(field)
            if name in spec.get("unique", {}):
                profiles.append(
                    Profile(
                        id=stable_id(source_id, "profile", f"{field.id}:stat"),
                        field_id=field.id,
                        object_id=item.id,
                        method="source_statistic",
                        uniqueness=spec["unique"][name],
                        **common,
                    )
                )
            if name in spec.get("profiles", {}):
                profiles.append(
                    Profile(
                        id=stable_id(source_id, "profile", f"{field.id}:given"),
                        field_id=field.id,
                        object_id=item.id,
                        **spec["profiles"][name],
                        **common,
                    )
                )
            if name in spec.get("values", {}):
                tokens.append(
                    token_set(
                        field.id,
                        item.id,
                        spec["values"][name],
                        source_id=source_id,
                        key_id=spec.get("token_key", "run-1"),
                    )
                )
        for child_name, parent_table, parent_field in spec.get("foreign", []):
            parent_number = list(tables).index(parent_table) + 1
            parent_ordinal = [n for n, _ in tables[parent_table]["fields"]].index(parent_field) + 1
            child_ordinal = [n for n, _ in spec["fields"]].index(child_name) + 1
            constraints.append(
                {
                    "record_type": "constraint",
                    "id": stable_id(source_id, "constraint", f"{table}.{child_name}:fk"),
                    "native_id": f"{table}.{child_name}:fk",
                    "kind": "foreign",
                    "object_id": item.id,
                    "target_object_id": stable_id(source_id, "object", str(parent_number)),
                    "field_ordinals": [child_ordinal],
                    "target_field_ordinals": [parent_ordinal],
                    **common,
                }
            )
        if spec.get("key"):
            constraints.append(
                {
                    "record_type": "constraint",
                    "id": stable_id(source_id, "constraint", table),
                    "native_id": table,
                    "kind": "primary",
                    "object_id": item.id,
                    "field_names": spec["key"],
                    **common,
                }
            )
        if spec.get("weak_key"):
            constraints.append(
                {
                    "record_type": "constraint",
                    "id": stable_id(source_id, "constraint", f"{table}:sorting_key"),
                    "native_id": f"{table}:sorting_key",
                    "kind": "index",
                    "object_id": item.id,
                    "field_names": [spec["weak_key"]],
                    **common,
                }
            )
    tables_out: dict[str, list] = {
        "sources.jsonl": [Source(id=source_id, kind=kind, name=source_id, **common)],
        "objects.jsonl": objects,
        "fields.jsonl": fields,
        "constraints.jsonl": constraints,
        "profiles.jsonl": profiles,
    }
    if tokens:
        tables_out["tokens.jsonl"] = tokens
    write_artifact(
        path,
        "collector-bundle",
        tables_out,
        producer="test",
        source_run_ids=[f"run-{source_id}"],
        signing_key=BUNDLE_KEY.encode(),
        key_id="test",
    )


def build_snapshot(
    tmp_path: Path,
    monkeypatch: Any,
    sources: dict[str, dict],
    *,
    review: ReviewConfig | None = None,
    name: str = "snapshot",
    builder_extra: dict[str, Any] | None = None,
) -> Path:
    """Assemble `sources` ({source_id: {"kind": ..., "tables": {...}}}) into a snapshot."""
    monkeypatch.setenv("BUNDLE_KEY", BUNDLE_KEY)
    bundles = []
    for source_id, spec in sources.items():
        bundle = tmp_path / f"bundle-{source_id}-{name}"
        write_bundle(bundle, source_id, spec.get("kind", "postgresql"), spec["tables"])
        bundles.append(bundle)
    snapshot = tmp_path / name
    assemble(
        bundles,
        snapshot,
        BuilderConfig(signing_keys={"test": "BUNDLE_KEY"}, review=review, **(builder_extra or {})),
    )
    return snapshot


def bundle_dirs(tmp_path: Path, name: str = "snapshot") -> list[Path]:
    return sorted(tmp_path.glob(f"bundle-*-{name}"))


def candidates(snapshot: Path) -> list[tuple[str, str, str, str]]:
    """REFERENCE_CANDIDATE edges as sorted (child table, field, parent table, field)."""
    _, data = read_artifact(snapshot)
    names = {item.id: item.name for item in data["nodes.jsonl"]}
    owners = {
        item.id: names[item.object_id]
        for item in data["nodes.jsonl"]
        if item.record_type == "field"
    }
    found = []
    for edge in data["edges.jsonl"]:
        if edge.kind != "REFERENCE_CANDIDATE":
            continue
        for child, parent in edge.model_extra["field_pairs"]:
            found.append((owners[child], names[child], owners[parent], names[parent]))
    return sorted(found)


def candidate_text(snapshot: Path) -> str:
    """Everything the snapshot says about its candidate edges, as one lower-cased string.

    The edge rows plus every evidence row an edge cites. A test that needs "the candidate says
    values were not compared" searches this rather than a field name the implementation may
    choose differently.
    """
    _, data = read_artifact(snapshot)
    evidence = {item.id: item.model_dump(mode="json") for item in data["evidence.jsonl"]}
    parts = []
    for edge in data["edges.jsonl"]:
        if edge.kind != "REFERENCE_CANDIDATE":
            continue
        dumped = edge.model_dump(mode="json")
        parts.append(json.dumps(dumped, sort_keys=True))
        parts.extend(json.dumps(evidence[key], sort_keys=True) for key in dumped["evidence_ids"]
                     if key in evidence)
    return "\n".join(parts).lower()


def edge_kinds(snapshot: Path) -> list[str]:
    _, data = read_artifact(snapshot)
    return sorted(edge.kind for edge in data["edges.jsonl"])


def containment_metrics(snapshot: Path) -> list[dict]:
    _, data = read_artifact(snapshot)
    return [
        item.model_dump()["metrics"]
        for item in data["evidence.jsonl"]
        if item.model_dump()["kind"] == "value_containment"
    ]


def builder_run(snapshot: Path) -> dict:
    _, data = read_artifact(snapshot)
    return next(item.model_dump() for item in data["runs.jsonl"] if item.source_id == "builder")


# --- the model stand-in -------------------------------------------------------------------------


def schema_bounds(node: Any, path: str = "$") -> list[str]:
    """Every `maxLength`, `minLength`, `maxItems` or `minItems` key in a schema, recursively."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in BOUND_KEYWORDS:
                found.append(f"{path}.{key}")
            found.extend(schema_bounds(value, f"{path}.{key}"))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(schema_bounds(value, f"{path}[{index}]"))
    return found


def closed_port_url() -> str:
    """A loopback URL nothing listens on: bind an ephemeral port, then release it."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"http://127.0.0.1:{port}/v1"


class _Server(ThreadingHTTPServer):
    daemon_threads = True


class ModelStub:
    """A loopback model server that records every request and can misbehave on purpose.

    `reply(payload)` receives the parsed user message (the question and items for `ask`, the
    candidates for the review) and returns the assistant content. `reject_bounds` answers 400 to a
    schema carrying a length or item bound, the way llama.cpp's grammar converter does. `delay`
    holds the reply back. `finish_reason="length"` imitates a reasoning model that ran out of
    `max_tokens`. `status` forces an HTTP status for every request.
    """

    def __init__(
        self,
        reply: Any = None,
        *,
        reject_bounds: bool = False,
        finish_reason: str = "stop",
        delay: float = 0.0,
        status: int | None = None,
        content: str | None = None,
    ) -> None:
        self.requests: list[dict] = []
        self.rejected = 0
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_: object) -> None:
                pass

            def do_POST(self) -> None:  # noqa: N802 - http.server naming
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                stub.requests.append({"path": self.path, "body": body})
                if delay:
                    time.sleep(delay)
                schema = (
                    body.get("response_format", {}).get("json_schema", {}).get("schema", {})
                )
                if status is not None:
                    self._send(status, {"error": {"message": "forced status"}})
                    return
                if reject_bounds and schema_bounds(schema):
                    stub.rejected += 1
                    self._send(
                        400,
                        {"error": {"message": "Failed to initialize samplers: failed to parse "
                                              "grammar", "type": "invalid_request_error"}},
                    )
                    return
                if content is not None:
                    text = content
                else:
                    text = reply(json.loads(body["messages"][-1]["content"]))
                self._send(
                    200,
                    {
                        "id": "cmpl-1", "object": "chat.completion", "created": 0,
                        "model": body["model"],
                        "choices": [{"index": 0, "finish_reason": finish_reason,
                                     "message": {"role": "assistant", "content": text}}],
                    },
                )

            def _send(self, code: int, payload: dict) -> None:
                raw = json.dumps(payload).encode()
                try:
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                except OSError:  # the client gave up (a timeout test), which is the point
                    pass

        self.server = _Server(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def schemas(self) -> list[dict]:
        return [
            item["body"].get("response_format", {}).get("json_schema", {}).get("schema", {})
            for item in self.requests
        ]

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def __enter__(self) -> ModelStub:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def cite_first(payload: dict) -> str:
    """A model that answers well: cites the first item it was given."""
    first = payload["items"][0]
    return json.dumps({"answer": f"{first['name']} is catalogued.", "citations": [first["id"]],
        "outcome": "answered"})


def support_everything(payload: list[dict]) -> str:
    """A reviewer that supports every candidate and cites that candidate's first evidence.

    The review sends the list of candidates as the user message, as `tests/test_reviewer_http.py`
    shows."""
    return json.dumps({"decisions": [
        {"candidate_id": item["candidate_id"], "decision": "supports",
         "cited_evidence_ids": [item["evidence"][0]["id"]], "reason": "names and types agree"}
        for item in payload
    ]})
