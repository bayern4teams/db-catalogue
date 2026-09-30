"""Render a verified snapshot, and the bundles it was built from, as one readable HTML page.

The snapshot carries sources, objects, fields, relationships and their evidence. Column
statistics, declared keys and indexes, and the reasons a collector dropped something stay in the
collector bundles, so a complete catalogue needs both. Each bundle is signature-verified with the
assembler's own keys and must be one the snapshot was assembled from: a newer or older collection
of the same source would describe a different database state than the relationships beside it.

The page is a report, not an interface. Every name in it comes from a database and is untrusted,
so all text is escaped; it loads nothing from anywhere, and its content security policy allows
only its own inline style and the one inline script, pinned by hash. Value tokens are never
rendered: they are keyed digests of sampled values, meaningful only inside one assembly.
"""

from __future__ import annotations

import base64
import hashlib
import os
import tempfile
from collections import defaultdict
from collections.abc import Iterable, Mapping
from html import escape
from pathlib import Path
from typing import Any

from dbig_contracts.artifact import read_artifact
from dbig_contracts.errors import OperatorError
from dbig_contracts.models import (
    Constraint,
    Diagnostic,
    Edge,
    Evidence,
    Field,
    Manifest,
    Object,
    Profile,
    Source,
)

from .builder import _read_signed, bundle_run_id
from .config import BuilderConfig

# A plain filter over object and column names. Kept tiny and static so its hash can be pinned in
# the content security policy; it reads only the search box and never evaluates page content.
SCRIPT = """
const box = document.getElementById("filter");
box.addEventListener("input", () => {
  const term = box.value.trim().toLowerCase();
  for (const card of document.querySelectorAll("[data-search]")) {
    card.hidden = term !== "" && !card.dataset.search.includes(term);
  }
});
"""

STYLE = """
:root { --bg:#fbfbfa; --fg:#1d1d1b; --muted:#6b6b66; --line:#e3e2de; --card:#ffffff;
  --accent:#2f5d8a; --warn-bg:#fff4e0; --warn-fg:#7a4a00; --bad-bg:#fdecec; --bad-fg:#8a1c1c;
  --ok-bg:#e9f5ec; --ok-fg:#1f5e33; }
@media (prefers-color-scheme: dark) { :root { --bg:#161615; --fg:#ecebe7; --muted:#a3a29c;
  --line:#34332f; --card:#1f1f1d; --accent:#8fb6de; --warn-bg:#3a2c12; --warn-fg:#f3cf8b;
  --bad-bg:#3b1a1a; --bad-fg:#f2b0b0; --ok-bg:#16301f; --ok-fg:#a6dcb5; } }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--fg);
  font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; }
main { max-width:1200px; margin:0 auto; padding:24px 16px 64px; }
h1 { font-size:22px; margin:0 0 4px; } h2 { font-size:17px; margin:32px 0 8px; }
h3 { font-size:15px; margin:0; } .muted { color:var(--muted); }
code { font:12.5px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }
.stats { display:flex; flex-wrap:wrap; gap:8px; margin:16px 0; }
.stat { background:var(--card); border:1px solid var(--line); border-radius:8px;
  padding:8px 12px; min-width:110px; }
.stat b { display:block; font-size:18px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:8px;
  padding:12px 14px; margin:10px 0; overflow-x:auto; }
table { border-collapse:collapse; width:100%; font-size:13px; }
th, td { text-align:left; padding:4px 8px; border-bottom:1px solid var(--line);
  vertical-align:top; }
th { color:var(--muted); font-weight:600; white-space:nowrap; }
td.num { text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }
.badge { display:inline-block; font-size:11px; padding:0 6px; border-radius:9px;
  border:1px solid var(--line); margin-right:4px; white-space:nowrap; }
.warn { background:var(--warn-bg); color:var(--warn-fg); }
.bad { background:var(--bad-bg); color:var(--bad-fg); }
.ok { background:var(--ok-bg); color:var(--ok-fg); }
.banner { padding:10px 14px; border-radius:8px; margin:12px 0; }
input#filter { width:100%; max-width:420px; padding:8px 10px; border:1px solid var(--line);
  border-radius:6px; background:var(--card); color:var(--fg); font:inherit; }
a { color:var(--accent); } ul.evidence { margin:4px 0 0; padding-left:18px; }
"""


def _csp() -> str:
    digest = base64.b64encode(hashlib.sha256(SCRIPT.encode()).digest()).decode()
    return (
        "default-src 'none'; style-src 'unsafe-inline'; "
        f"script-src 'sha256-{digest}'; base-uri 'none'; form-action 'none'"
    )


def _e(value: object) -> str:
    return escape("" if value is None else str(value), quote=True)


def _anchor(record_id: str) -> str:
    # Anchors are derived from the stable ID, never from a database name, so an untrusted name
    # cannot collide with or break out of an attribute.
    return "n" + hashlib.sha256(record_id.encode()).hexdigest()[:16]


def _percent(value: float | None) -> str:
    return "" if value is None else f"{value * 100:.1f}%"


def _number(value: float | int | None) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.2f}"
    return f"{int(value):,}"


def _metrics(values: Mapping[str, object]) -> str:
    return ", ".join(f"{_e(key)}={_e(values[key])}" for key in sorted(values))


def _verified_bundles(
    snapshot_runs: set[str], bundles: Iterable[Path], config: BuilderConfig
) -> list[tuple[Manifest, dict[str, list]]]:
    verified = []
    for path in bundles:
        manifest, records = _read_signed(path, config)
        source = Source.model_validate(records["sources.jsonl"][0])
        if bundle_run_id(source.id, manifest) not in snapshot_runs:
            raise OperatorError(
                f"bundle {path} was not assembled into this snapshot; render it with the "
                "snapshot built from it"
            )
        verified.append((manifest, records))
    return verified


def render_catalog(
    snapshot: Path, bundles: list[Path], output: Path, config: BuilderConfig | None
) -> dict[str, int]:
    """Write the catalogue page and return the counts it reports."""
    manifest, records = read_artifact(snapshot)
    if manifest.content_type != "catalog-snapshot":
        raise OperatorError("expected a catalog snapshot")
    if bundles and config is None:
        raise OperatorError("--config is required to verify bundle signatures")
    snapshot_runs = {record.id for record in records["runs.jsonl"]}
    verified = _verified_bundles(snapshot_runs, bundles, config) if config else []

    sources = {}
    objects: dict[str, Object] = {}
    fields: dict[str, Field] = {}
    for node in records["nodes.jsonl"]:
        if node.record_type == "source":
            sources[node.id] = Source.model_validate(node.model_dump())
        elif node.record_type == "object":
            objects[node.id] = Object.model_validate(node.model_dump())
        else:
            fields[node.id] = Field.model_validate(node.model_dump())
    edges = [Edge.model_validate(item.model_dump()) for item in records["edges.jsonl"]]
    evidence = {
        item.id: Evidence.model_validate(item.model_dump()) for item in records["evidence.jsonl"]
    }

    profiles: dict[str, list[Profile]] = defaultdict(list)
    constraints: dict[str, list[Constraint]] = defaultdict(list)
    diagnostics: dict[str, list[Diagnostic]] = defaultdict(list)
    bundled_sources: set[str] = set()
    for _, bundle in verified:
        bundled_sources.add(bundle["sources.jsonl"][0].id)
        for item in bundle["profiles.jsonl"]:
            profile = Profile.model_validate(item.model_dump())
            profiles[profile.field_id].append(profile)
        for item in bundle["constraints.jsonl"]:
            constraint = Constraint.model_validate(item.model_dump())
            constraints[constraint.object_id].append(constraint)
        for item in bundle["diagnostics.jsonl"]:
            diagnostic = Diagnostic.model_validate(item.model_dump())
            diagnostics[diagnostic.source_id].append(diagnostic)

    fields_by_object: dict[str, list[Field]] = defaultdict(list)
    for field in fields.values():
        fields_by_object[field.object_id].append(field)
    for members in fields_by_object.values():
        members.sort(key=lambda item: (item.ordinal is None, item.ordinal or 0, item.name))
    objects_by_source: dict[str, list[Object]] = defaultdict(list)
    for item in objects.values():
        objects_by_source[item.source_id].append(item)
    for listed in objects_by_source.values():
        listed.sort(key=lambda item: (item.namespace or "", item.name, item.id))
    edges_by_object: dict[str, list[Edge]] = defaultdict(list)
    relationship_edges = [
        edge for edge in edges if edge.kind in {"DECLARES_FK", "REFERENCE_CANDIDATE"}
    ]
    for edge in relationship_edges:
        edges_by_object[edge.from_id].append(edge)
        if edge.to_id != edge.from_id:
            edges_by_object[edge.to_id].append(edge)

    builder_run = next(
        (item.model_dump() for item in records["runs.jsonl"] if item.source_id == "builder"), {}
    )
    counts = {
        "sources": len(sources),
        "objects": len(objects),
        "fields": len(fields),
        "declared_fks": sum(1 for edge in edges if edge.kind == "DECLARES_FK"),
        "candidates": sum(1 for edge in edges if edge.kind == "REFERENCE_CANDIDATE"),
        "diagnostics": sum(len(items) for items in diagnostics.values()),
    }

    def label(record_id: str) -> str:
        if record_id in fields:
            field = fields[record_id]
            owner = objects.get(field.object_id)
            return f"{label(owner.id) if owner else '?'}.{field.name}"
        if record_id in objects:
            item = objects[record_id]
            return f"{item.namespace}.{item.name}" if item.namespace else item.name
        return record_id

    def link(record_id: str) -> str:
        target = fields[record_id].object_id if record_id in fields else record_id
        return f'<a href="#{_anchor(target)}">{_e(label(record_id))}</a>'

    def edge_row(edge: Edge) -> str:
        pairs = edge.model_dump().get("field_pairs") or []
        joined = (
            "<br>".join(f"{link(left)} &rarr; {link(right)}" for left, right in pairs)
            or f"{link(edge.from_id)} &rarr; {link(edge.to_id)}"
        )
        status_class = {"declared": "ok", "strong": "ok", "rejected": "bad"}.get(
            edge.status, "warn"
        )
        cited = "".join(
            f"<li><code>{_e(evidence[item].kind)}</code> {_metrics(evidence[item].metrics)}</li>"
            for item in edge.evidence_ids
            if item in evidence
        )
        confidence = (
            "not calibrated" if edge.confidence is None else f"{edge.confidence:.2f}"
        )
        return (
            f"<tr><td>{joined}</td><td><span class='badge'>{_e(edge.kind)}</span>"
            f"<span class='badge {status_class}'>{_e(edge.status)}</span></td>"
            f"<td>{_e(edge.explanation)}<ul class='evidence'>{cited}</ul></td>"
            f"<td class='muted'>{_e(confidence)}</td></tr>"
        )

    def key_badges(item: Object, field: Field) -> str:
        badges = []
        for constraint in constraints.get(item.id, []):
            ordinals = constraint.field_ordinals
            names = constraint.field_names
            member = (field.ordinal is not None and field.ordinal in ordinals) or (
                field.name in names
            )
            if not member:
                continue
            width = max(len(ordinals), len(names))
            text = {"primary": "PK", "unique": "UNIQUE", "foreign": "FK", "index": "INDEX"}[
                constraint.kind
            ]
            if width > 1:
                text += f" ({width} cols)"
            badges.append(f"<span class='badge'>{_e(text)}</span>")
        return "".join(badges)

    def profile_cells(field: Field) -> str:
        measured = profiles.get(field.id, [])

        def first(name: str) -> Any:
            return next(
                (getattr(p, name) for p in measured if getattr(p, name) is not None), None
            )

        null_fraction = first("null_fraction")
        distinct = first("distinct_estimate")
        uniqueness = first("uniqueness")
        width = first("average_width_bytes")
        methods = sorted({p.method for p in measured})
        updated = first("stats_updated_at")
        provenance = ", ".join(methods)
        if updated is not None:
            provenance += f" (analyzed {updated:%Y-%m-%d %H:%M}Z)"
        return (
            f"<td class='num'>{_percent(null_fraction)}</td>"
            f"<td class='num'>{_number(distinct)}</td>"
            f"<td class='num'>{_percent(uniqueness)}</td>"
            f"<td class='num'>{_number(width)}</td>"
            f"<td class='muted'>{_e(provenance)}</td>"
        )

    def object_card(item: Object) -> str:
        members = fields_by_object.get(item.id, [])
        search = " ".join([label(item.id), *(field.name for field in members)]).casefold()
        described = dict(item.attributes)
        rows_estimate = described.pop("row_count_estimate", None)
        rows_method = described.pop("row_count_method", None)
        size = ""
        if isinstance(rows_estimate, int | float) and not isinstance(rows_estimate, bool):
            method = f" (estimate from {_e(rows_method)})" if rows_method else " (estimate)"
            size = f"<b>~{_number(rows_estimate)}</b> rows<span class='muted'>{method}</span><br>"
        attributes = size + _metrics(described)
        rows = "".join(
            f"<tr><td class='num muted'>{_e(field.ordinal)}</td>"
            f"<td><code>{_e(field.name)}</code> {key_badges(item, field)}</td>"
            f"<td><code>{_e(field.data_type)}</code></td>"
            f"<td>{'' if field.nullable is None else ('yes' if field.nullable else 'no')}</td>"
            f"{profile_cells(field)}"
            f"<td class='muted'>{_metrics(field.attributes)}</td></tr>"
            for field in members
        )
        indexes = [
            constraint
            for constraint in constraints.get(item.id, [])
            if constraint.kind in {"primary", "unique", "index"}
        ]
        by_ordinal = {field.ordinal: field.name for field in members if field.ordinal is not None}

        def covered(constraint: Constraint) -> str:
            # Column names, never the constraint's native ID: that is an internal identifier (a
            # PostgreSQL OID, or a MongoDB collection UUID) and meant nothing to a reader.
            names = constraint.field_names or [
                by_ordinal.get(ordinal, f"column {ordinal}")
                for ordinal in constraint.field_ordinals
            ]
            return ", ".join(names)

        index_list = "".join(
            f"<li><span class='badge'>{_e(constraint.kind)}</span> "
            f"<code>{_e(covered(constraint))}</code></li>"
            for constraint in indexes
        )
        related = edges_by_object.get(item.id, [])
        relationship_table = (
            "<h4>Relationships</h4><table><tr><th>Fields</th><th>Kind</th>"
            "<th>Why</th><th>Confidence</th></tr>"
            + "".join(edge_row(edge) for edge in related)
            + "</table>"
            if related
            else ""
        )
        return (
            f"<section class='card' id='{_anchor(item.id)}' data-search='{_e(search)}'>"
            f"<h3>{_e(label(item.id))} <span class='badge'>{_e(item.kind)}</span></h3>"
            f"<div class='muted'>{attributes}</div>"
            f"<table><tr><th>#</th><th>Column</th><th>Type</th><th>Nullable</th>"
            f"<th>Null %</th><th>Distinct (est.)</th><th>Uniqueness</th>"
            f"<th>Avg bytes</th><th>Statistics from</th><th>Attributes</th></tr>{rows}</table>"
            + (f"<h4>Keys and indexes</h4><ul>{index_list}</ul>" if index_list else "")
            + relationship_table
            + "</section>"
        )

    parts = []
    completeness_class = "ok" if manifest.completeness == "complete" else "bad"
    review_status = builder_run.get("review_status", "")
    parts.append(
        f"<h1>Database catalogue</h1><div class='muted'>Snapshot as of "
        f"{manifest.as_of:%Y-%m-%d %H:%M:%S}Z &middot; produced by {_e(manifest.producer)}"
        f" &middot; reviewer: {_e(review_status)}</div>"
        f"<div class='banner {completeness_class}'>Completeness: "
        f"<b>{_e(manifest.completeness)}</b>"
        + (
            ""
            if manifest.completeness == "complete"
            else " &mdash; at least one source was partial or a step could not finish; see "
            "diagnostics below."
        )
        + "</div>"
    )
    parts.append(
        "<div class='stats'>"
        + "".join(
            f"<div class='stat'><b>{_number(value)}</b>{_e(name.replace('_', ' '))}</div>"
            for name, value in counts.items()
        )
        + "</div>"
    )
    missing = sorted(set(sources) - bundled_sources)
    if missing:
        parts.append(
            "<div class='banner warn'>No bundle was supplied for "
            + ", ".join(f"<code>{_e(item)}</code>" for item in missing)
            + ", so column statistics, keys, indexes and diagnostics are not shown for "
            "those sources.</div>"
        )
    if any(diagnostics.values()):
        rows = "".join(
            f"<tr><td><code>{_e(source_id)}</code></td><td><code>{_e(item.code)}</code></td>"
            f"<td>{_e(item.message)}</td></tr>"
            for source_id in sorted(diagnostics)
            for item in sorted(diagnostics[source_id], key=lambda d: (d.code, d.id))
        )
        parts.append(
            "<h2>Diagnostics: what could not be catalogued</h2><div class='card'><table>"
            f"<tr><th>Source</th><th>Code</th><th>Detail</th></tr>{rows}</table></div>"
        )
    if relationship_edges:
        ordered = sorted(relationship_edges, key=lambda e: (e.kind, label(e.from_id), e.id))
        parts.append(
            "<h2>Relationships</h2><p class='muted'>Declared foreign keys come from the source "
            "catalog. Candidates are inferred and stay tentative until reviewed; a confidence "
            "is shown only once it has been calibrated against labelled data.</p>"
            "<div class='card'><table><tr><th>Fields</th><th>Kind</th><th>Why</th>"
            "<th>Confidence</th></tr>" + "".join(edge_row(edge) for edge in ordered)
            + "</table></div>"
        )
    parts.append(
        "<h2>Objects</h2><input id='filter' type='search' "
        "placeholder='Filter tables and columns' aria-label='Filter tables and columns'>"
    )
    for source_id in sorted(sources):
        source = sources[source_id]
        transport = source.transport or "transport not recorded"
        transport_class = {"tls-verified": "ok", "plaintext-loopback": "warn"}.get(
            source.transport or "", ""
        )
        parts.append(
            f"<h2>{_e(source.name)} <span class='badge'>{_e(source.kind)}</span>"
            f"<span class='badge {transport_class}'>{_e(transport)}</span></h2>"
        )
        parts.extend(object_card(item) for item in objects_by_source.get(source_id, []))

    page = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<meta http-equiv='Content-Security-Policy' content=\"{_csp()}\">"
        "<meta name='referrer' content='no-referrer'>"
        f"<title>Database catalogue</title><style>{STYLE}</style></head><body><main>"
        + "".join(parts)
        + f"</main><script>{SCRIPT}</script></body></html>\n"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    # Atomic, so a reader never sees a half-written page and a failure leaves any previous one.
    handle, temporary = tempfile.mkstemp(dir=output.parent, prefix=".catalog-", suffix=".html")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(page)
        os.replace(temporary, output)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return counts
