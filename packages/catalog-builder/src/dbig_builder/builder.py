"""Evidence-first catalog construction from sealed source bundles."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path
from typing import Any

from dbig_contracts.artifact import ArtifactLimits, canonical, read_artifact, write_artifact
from dbig_contracts.ids import stable_id
from dbig_contracts.models import (
    Constraint,
    Edge,
    Evidence,
    Field,
    Manifest,
    Object,
    Record,
    Source,
    ValueTokens,
    utc_now,
)
from rapidfuzz.fuzz import ratio

from .config import BuilderConfig
from .reviewer import review_candidates


def _read_signed(path: Path, config: BuilderConfig) -> tuple[Manifest, dict[str, list[Record]]]:
    manifest_path = path / "manifest.json"
    if manifest_path.stat().st_size > ArtifactLimits().max_manifest_bytes:
        raise ValueError("oversized manifest")
    preliminary = Manifest.model_validate_json(manifest_path.read_bytes())
    if preliminary.signature is None:
        raise ValueError("unsigned source bundle")
    manifest, records = read_artifact(
        path, signing_key=config.key(preliminary.signature.key_id), require_signature=True
    )
    if manifest.content_type != "collector-bundle":
        raise ValueError("expected collector bundle")
    return manifest, records


def _base(record: Record) -> dict[str, Any]:
    return {
        "source_id": record.source_id,
        "observed_at": record.observed_at,
        "collector_version": "dbig-builder/0.1.0",
        "provenance": record.id,
    }


def _evidence(
    source: Record, kind: str, target_id: str, suffix: str, metrics: dict[str, Any] | None = None
) -> Evidence:
    return Evidence(
        id=stable_id(source.source_id, "evidence", f"{source.id}:{suffix}"),
        kind=kind,
        subject_id=source.id,
        object_id=target_id,
        metrics=metrics or {},
        **_base(source),
    )


MIN_TOKEN_SUPPORT = 4


def _containment(
    child: Field,
    child_tokens: ValueTokens | None,
    parent_tokens: ValueTokens | None,
) -> Evidence | None:
    """Measure how much of a child's sampled values appear among a candidate parent key's.

    Containment, not similarity: a reference means the child's values are drawn from the parent's
    key, so the question is one-directional and the denominator is the child's distinct count.
    Overlap is reported too, because a high containment on a tiny child sample against a huge
    parent is weak, and the two numbers together show that.

    Abstains, returning nothing, in four cases rather than reporting a number that means nothing:
    either side has no tokens, the two were keyed under different key IDs and so are not
    comparable at all, the two were normalised differently, or either side's distinct count is
    below the minimum support. §15's rule applies: no `confidence` is produced here, only a
    measurement with its denominators, and the strength tier stays whatever the deterministic
    rules already chose.
    """
    if child_tokens is None or parent_tokens is None:
        return None
    if child_tokens.key_id != parent_tokens.key_id:
        # Tokens keyed under different run keys are unrelated byte strings. Comparing them would
        # always report zero overlap, which reads as a counterexample rather than as ignorance.
        return None
    if child_tokens.normalization != parent_tokens.normalization:
        return None
    if min(child_tokens.distinct_count, parent_tokens.distinct_count) < MIN_TOKEN_SUPPORT:
        return None
    child_set, parent_set = set(child_tokens.tokens), set(parent_tokens.tokens)
    if not child_set:
        return None
    shared = len(child_set & parent_set)
    union = len(child_set | parent_set)
    return _evidence(
        child,
        "value_containment",
        parent_tokens.field_id,
        f"containment:{parent_tokens.field_id}",
        {
            "contained_fraction": round(shared / len(child_set), 6),
            "shared_tokens": shared,
            "child_distinct_sampled": len(child_set),
            "parent_distinct_sampled": len(parent_set),
            "jaccard": round(shared / union, 6) if union else None,
            "child_sample_count": child_tokens.sample_count,
            "parent_sample_count": parent_tokens.sample_count,
            "child_sample_method": child_tokens.sample_method,
            "parent_sample_method": parent_tokens.sample_method,
            # A capped token set makes containment a lower bound, not a measurement.
            "lower_bound_only": child_tokens.truncated or parent_tokens.truncated,
            "key_id": child_tokens.key_id,
            "minimum_support": MIN_TOKEN_SUPPORT,
        },
    )


def _type_family(value: str) -> str:
    normalized = value.casefold().split("(", 1)[0]
    if normalized in {"integer", "bigint", "smallint", "int", "int64", "uint64", "int32"}:
        return "integer"
    if normalized in {"uuid", "uuid4"}:
        return "uuid"
    return normalized


def assemble(
    bundle_paths: list[Path],
    output: Path,
    config: BuilderConfig,
    *,
    partial_source_receipts: dict[str, str] | None = None,
) -> Manifest:
    if not bundle_paths:
        raise ValueError("at least one source bundle is required")
    sources: dict[str, Source] = {}
    nodes: dict[str, Record] = {}
    constraints: list[Constraint] = []
    token_sets: dict[str, ValueTokens] = {}
    runs: list[Record] = []
    partial_source_receipts = partial_source_receipts or {}
    partial = bool(partial_source_receipts)
    for path in bundle_paths:
        manifest, records = _read_signed(path, config)
        bundle_sources = records["sources.jsonl"]
        if len(bundle_sources) != 1:
            raise ValueError("bundle must describe exactly one source")
        source = Source.model_validate(bundle_sources[0])
        if source.id in sources:
            raise ValueError("ambiguous source identity")
        sources[source.id] = source
        partial |= manifest.completeness == "partial"
        for table in ("sources.jsonl", "objects.jsonl", "fields.jsonl"):
            for record in records[table]:
                if record.id in nodes:
                    raise ValueError("duplicate node ID across bundles")
                nodes[record.id] = record
        constraints.extend(Constraint.model_validate(item) for item in records["constraints.jsonl"])
        for item in records.get("tokens.jsonl", []):
            # Held in memory for this assembly only. Nothing derived from a token reaches the
            # snapshot except the aggregate metrics on a containment evidence record.
            entry = ValueTokens.model_validate(item.model_dump())
            token_sets[entry.field_id] = entry
        digest = hashlib.sha256(canonical(manifest)).hexdigest()
        runs.append(
            Record.model_validate(
                {
                    "record_type": "run",
                    "id": stable_id(source.id, "run", digest),
                    "source_id": source.id,
                    "observed_at": manifest.as_of,
                    "collector_version": manifest.producer,
                    "provenance": digest,
                    "completeness": manifest.completeness,
                }
            )
        )
    objects = {
        key: Object.model_validate(value)
        for key, value in nodes.items()
        if value.record_type == "object"
    }
    fields = {
        key: Field.model_validate(value)
        for key, value in nodes.items()
        if value.record_type == "field"
    }
    by_object: dict[str, dict[int, Field]] = defaultdict(dict)
    by_object_name: dict[str, dict[str, Field]] = defaultdict(dict)
    for field in fields.values():
        if field.ordinal is not None:
            by_object[field.object_id][field.ordinal] = field
        by_object_name[field.object_id][field.name] = field
    evidence: dict[str, Evidence] = {}
    edges: dict[str, Edge] = {}
    unique_fields: list[Field] = []
    declared_pairs: set[tuple[str, str]] = set()
    for constraint in constraints:
        kind = constraint.kind
        owner = constraint.object_id
        ordinals = constraint.field_ordinals
        if kind in {"primary", "unique"} and len(ordinals) == 1:
            unique_field = by_object.get(owner, {}).get(ordinals[0])
            if unique_field:
                unique_fields.append(unique_field)
        elif kind in {"primary", "unique"} and len(constraint.field_names) == 1:
            unique_field = by_object_name.get(owner, {}).get(constraint.field_names[0])
            if unique_field:
                unique_fields.append(unique_field)
        if kind != "foreign":
            continue
        target = constraint.target_object_id
        target_ordinals = constraint.target_field_ordinals
        if (
            target is None
            or owner not in objects
            or target not in objects
            or len(ordinals) != len(target_ordinals)
        ):
            partial = True
            continue
        pairs = []
        for left, right in zip(ordinals, target_ordinals, strict=True):
            child = by_object.get(owner, {}).get(left)
            parent = by_object.get(target, {}).get(right)
            if child is None or parent is None:
                partial = True
                break
            pairs.append([child.id, parent.id])
        else:
            item = _evidence(constraint, "catalog_key", target, "fk")
            evidence[item.id] = item
            edge_id = stable_id(constraint.source_id, "edge", constraint.id)
            edges[edge_id] = Edge.model_validate(
                {
                    "id": edge_id,
                    "kind": "DECLARES_FK",
                    "from_id": owner,
                    "to_id": target,
                    "status": "declared",
                    "evidence_ids": [item.id],
                    "field_pairs": pairs,
                    "explanation": "source-declared foreign key",
                    "confidence": None,
                    **_base(constraint),
                }
            )
            declared_pairs.add((owner, target))
    for child in fields.values():
        if not child.name.casefold().endswith("_id"):
            continue
        child_object = objects.get(child.object_id)
        if child_object is None:
            continue
        stem = child.name.casefold()[:-3]
        candidates = []
        for parent in unique_fields:
            parent_object = objects.get(parent.object_id)
            if (
                parent_object is None
                or parent.object_id == child.object_id
                or (child.object_id, parent.object_id) in declared_pairs
                or _type_family(child.data_type) != _type_family(parent.data_type)
            ):
                continue
            name_score = round(ratio(stem, parent_object.name.casefold().rstrip("s")))
            if name_score >= config.name_similarity_threshold:
                candidates.append((name_score, parent))
        for score, parent in sorted(candidates, key=lambda item: (-item[0], item[1].id))[
            : config.top_k_per_field
        ]:
            lexical = _evidence(
                child, "name_similarity", parent.id, f"name:{parent.id}", {"ratio_percent": score}
            )
            typed = _evidence(child, "compatible_type", parent.id, f"type:{parent.id}")
            key = _evidence(parent, "catalog_key", child.id, f"unique:{child.id}")
            for item in (lexical, typed, key):
                evidence[item.id] = item
            cited = [lexical.id, typed.id, key.id]
            explanation = "name, compatible type, and declared unique key"
            containment = _containment(
                child, token_sets.get(child.id), token_sets.get(parent.id)
            )
            if containment is not None:
                evidence[containment.id] = containment
                cited.append(containment.id)
                explanation = f"{explanation}; {containment.explanation}"
            edge_id = stable_id(child.source_id, "edge", f"{child.id}:{parent.id}")
            edges[edge_id] = Edge.model_validate(
                {
                    "id": edge_id,
                    "kind": "REFERENCE_CANDIDATE",
                    "from_id": child.object_id,
                    "to_id": parent.object_id,
                    "status": "tentative",
                    "evidence_ids": cited,
                    "field_pairs": [[child.id, parent.id]],
                    "explanation": explanation,
                    "confidence": None,
                    **_base(child),
                }
            )
    review_status = "REVIEW_DISABLED"
    if config.review is not None:
        suggestions, review_status = review_candidates(
            [edge for edge in edges.values() if edge.kind == "REFERENCE_CANDIDATE"],
            nodes,
            evidence,
            config.review,
        )
        for suggestion in suggestions:
            evidence[suggestion.id] = suggestion
        partial |= review_status == "MODEL_UNAVAILABLE_OR_INVALID"
    runs.append(
        Record.model_validate(
            {
                "record_type": "run",
                "id": stable_id(
                    "builder",
                    "run",
                    "|".join(
                        sorted(record.id for record in runs)
                        + sorted(partial_source_receipts.values())
                    ),
                ),
                "source_id": "builder",
                "observed_at": utc_now(),
                "collector_version": "dbig-builder/0.1.0",
                "provenance": "assembler",
                "review_status": review_status,
                "partial_source_receipts": partial_source_receipts,
            }
        )
    )
    runs.sort(key=lambda record: record.id)
    return write_artifact(
        output,
        "catalog-snapshot",
        {
            "nodes.jsonl": [nodes[key] for key in sorted(nodes)],
            "edges.jsonl": [edges[key] for key in sorted(edges)],
            "evidence.jsonl": [evidence[key] for key in sorted(evidence)],
            "runs.jsonl": runs,
        },
        producer="dbig-builder/0.1.0",
        source_run_ids=[record.id for record in runs],
        completeness="partial" if partial else "complete",
    )
