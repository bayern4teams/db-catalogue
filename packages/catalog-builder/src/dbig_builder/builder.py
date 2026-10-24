"""Evidence-first catalog construction from sealed source bundles."""

from __future__ import annotations

import hashlib
import math
import re
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from statistics import NormalDist
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
    Profile,
    Record,
    Source,
    ValueTokens,
    utc_now,
)
from rapidfuzz.fuzz import ratio

from .config import MIN_TOKEN_SUPPORT as MIN_TOKEN_SUPPORT
from .config import BuilderConfig
from .reviewer import MODEL_FAILURE_CODES, review_candidates


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


def bundle_run_id(source_id: str, manifest: Manifest) -> str:
    """The snapshot run ID a bundle contributes, derived from its exact manifest bytes.

    Public because the catalogue renderer uses it to prove a bundle is one this snapshot was
    assembled from, rather than a newer or older collection of the same source.
    """
    return stable_id(source_id, "run", hashlib.sha256(canonical(manifest)).hexdigest())


def _base(record: Record) -> dict[str, Any]:
    return {
        "source_id": record.source_id,
        "observed_at": record.observed_at,
        "collector_version": "dbig-builder/0.1.0",
        "provenance": record.id,
    }


def _evidence(
    source: Record,
    kind: str,
    target_id: str,
    suffix: str,
    metrics: dict[str, Any] | None = None,
    explanation: str = "",
) -> Evidence:
    return Evidence(
        id=stable_id(source.source_id, "evidence", f"{source.id}:{suffix}"),
        kind=kind,
        subject_id=source.id,
        object_id=target_id,
        metrics=metrics or {},
        explanation=explanation,
        **_base(source),
    )


# Suffixes that conventionally mark a field as pointing somewhere else. Only `_id` was recognised
# before, so a column called `owner_ref` or `account_code` could not become a candidate at all,
# however perfectly its values contained. Order matters: the longest match wins, so `_uuid` is
# tried before `_id` would strip only two characters off it.
REFERENCE_SUFFIXES = ("_uuid", "_code", "_num", "_ref", "_key", "_id", "_fk", "_no")
# A field measured this unique is treated as a possible parent key even with no declared
# constraint, which is how a table that never declared a primary key can still be referenced.
MEASURED_UNIQUE = 0.99
# Native ID suffixes the ClickHouse collector gives a one-column key it reports as a key claim.
WEAK_KEY_SUFFIXES = (":sorting_key", ":primary_key")


def _measure(profiles: list[Profile] | None, name: str) -> float | None:
    """First reported value for one measure across a field's profiles.

    A field can have a planner statistic and a sample, measuring different things. Reading per
    measure rather than per profile means a statistic that knows uniqueness is not shadowed by a
    sample that does not.
    """
    for profile in profiles or []:
        value = profile.model_dump().get(name)
        if value is not None:
            return float(value)
    return None


# Names too generic to identify a key by themselves: every table has an `id`, so a child called
# `id` is not a reference to every table's primary key.
GENERIC_KEY_NAMES = frozenset({"id", "_id", "uuid", "key", "code", "num", "ref", "fk", "no"})


def _snake(name: str) -> str:
    """`AccountID` -> `account_id`, `customerId` -> `customer_id`, `HTTPServerID` ->
    `http_server_id`; snake_case is unchanged.

    camelCase and PascalCase keys are common in schemas from SQL Server and in MongoDB, and the
    suffix rule needs the underscore, so without this they were never compared at all.
    """
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", name)
    return spaced.casefold()


def _reference_stem(name: str, known_objects: frozenset[str] = frozenset()) -> tuple[str, bool]:
    """Return the part of a field name that should match a parent, and whether a suffix marked it.

    With a suffix the name announces itself as a reference, so a fuzzy match against the parent
    object's name is justified. Without one it announces nothing, and fuzzy matching every field
    against every unique key would trade precision away for recall; the caller requires an exact
    match in that case instead.
    """
    folded = _snake(name)
    for suffix in REFERENCE_SUFFIXES:
        if folded.endswith(suffix) and len(folded) > len(suffix):
            return folded[: -len(suffix)], True
    # No separator and no case boundary (`companyid`): read it as a reference only when what is
    # left of the suffix is a known object, so `xid`, `valid` and `paid` propose nothing
    # (user decision 2026-10-12). `known_objects` holds object names as the parent comparison
    # normalises them.
    lowered = name.casefold()
    for suffix in REFERENCE_SUFFIXES:
        bare = suffix.removeprefix("_")
        stem = lowered[: -len(bare)]
        if (
            lowered.endswith(bare)
            and len(lowered) > len(bare)
            and (stem in known_objects or stem.rstrip("s") in known_objects)
        ):
            return stem, True
    return lowered, False


def _is_reference_style(name: str) -> bool:
    """Whether a name ends in a reference suffix after a stem, with no object lookup.

    ponytail: a lowercase name with no separator (`valid`) passes when its stem has three or more
    characters; a catalogue-aware test would need the parent's own table, so the ceiling is a
    few ordinary words ending in `id`. Upgrade path: require `known_objects` for them.
    """
    folded = _snake(name)
    if any(folded.endswith(suffix) and len(folded) > len(suffix) for suffix in REFERENCE_SUFFIXES):
        return True
    lowered = name.casefold()
    return any(
        lowered.endswith(suffix.removeprefix("_")) and len(lowered) - len(suffix) + 1 >= 3
        for suffix in REFERENCE_SUFFIXES
    )


def _resembles(child: Field, parent: Field, config: BuilderConfig) -> bool:
    """Whether a parent column's name is the child's name (up to case and separators) or generic."""
    name = parent.name.casefold()
    if name in GENERIC_KEY_NAMES:
        return True
    left = _snake(child.name).replace("_", "")
    right = _snake(parent.name).replace("_", "")
    return left == right or round(ratio(left, right)) >= config.name_similarity_threshold


def _composite_candidates(
    composite_keys: list[tuple[str, list[Field]]],
    objects: dict[str, Object],
    by_object_name: dict[str, dict[str, Field]],
    declared_pairs: set[tuple[str, str]],
    field_profiles: dict[str, list[Profile]],
    evidence: dict[str, Evidence],
    edges: dict[str, Edge],
    config: BuilderConfig,
) -> None:
    """Propose candidates against declared multi-column keys.

    Declared foreign keys already carry multi-column `field_pairs`, but candidate generation was
    single-field only, so a two-column natural key could never be proposed at all.

    Precision-first, in three ways. Every component must match, because a partial tuple is not a
    reference to a tuple key: matching two of three columns says nothing about the third. At least
    one component must be named for the parent, as `<singular parent>_<component>`, because two
    unrelated tables that merely both have `id` and `name` columns would otherwise look like a
    reference. And the components keep the source's declared order, since a tuple compared out of
    order is a different tuple.

    No value evidence is attached. §8 requires ordered tuple comparison for composite keys, and
    per-field tokens cannot provide it: knowing each column's values overlap separately says
    nothing about whether the *combinations* do. Claiming containment from per-field tokens here
    would be the kind of invented measurement the rest of this design refuses.

    Identifiers include which key matched, so a parent with two composite keys that one child
    matches both of yields two candidates rather than one silently overwriting the other.
    """
    for parent_object_id, members in composite_keys:
        key_ref = ":".join(member.id for member in members)
        parent_object = objects.get(parent_object_id)
        if parent_object is None:
            continue
        singular = parent_object.name.casefold().rstrip("s")
        for child_object_id, child_fields in by_object_name.items():
            if child_object_id == parent_object_id:
                continue
            if (child_object_id, parent_object_id) in declared_pairs:
                continue
            child_object = objects.get(child_object_id)
            if child_object is None or not config.pair_allowed(
                child_object.source_id, parent_object.source_id
            ):
                continue
            folded = {name.casefold(): field for name, field in child_fields.items()}
            matched: list[Field] = []
            pairs: list[list[str]] = []
            named_for_parent = 0
            for member in members:
                component = member.name.casefold()
                prefixed = folded.get(f"{singular}_{component}")
                match = prefixed or folded.get(component)
                # One child field cannot stand for two components, and a field with no values
                # cannot reference anything, the same rule the single-field path applies.
                if (
                    match is None
                    or match in matched
                    or _type_family(match.data_type) != _type_family(member.data_type)
                    or _measure(field_profiles.get(match.id), "null_fraction") == 1.0
                ):
                    pairs = []
                    break
                if prefixed is not None:
                    named_for_parent += 1
                matched.append(match)
                pairs.append([match.id, member.id])
            if not pairs or named_for_parent == 0:
                continue
            anchor = matched[0]
            components = _evidence(
                anchor,
                "composite_name_match",
                members[0].id,
                f"composite:{key_ref}",
                {
                    "component_count": len(members),
                    "components_named_for_parent": named_for_parent,
                    "tuple_containment_measured": False,
                },
            )
            key = _evidence(
                members[0],
                "catalog_key",
                anchor.id,
                f"composite-unique:{child_object_id}:{key_ref}",
                {"component_count": len(members)},
            )
            for item in (components, key):
                evidence[item.id] = item
            edge_id = stable_id(
                anchor.source_id, "edge", f"composite:{child_object_id}:{key_ref}"
            )
            edges[edge_id] = Edge.model_validate(
                {
                    "id": edge_id,
                    "kind": "REFERENCE_CANDIDATE",
                    "from_id": child_object_id,
                    "to_id": parent_object_id,
                    "status": "tentative",
                    "evidence_ids": [components.id, key.id],
                    "field_pairs": pairs,
                    "explanation": (
                        "every component of a declared composite key matched by name, with no "
                        "tuple value comparison available"
                    ),
                    "confidence": None,
                    **_base(anchor),
                }
            )


def _overlap(
    child: Field,
    parent_id: str,
    child_tokens: ValueTokens | None,
    parent_tokens: ValueTokens | None,
    min_support: int,
    level: float,
) -> Evidence:
    """Say what was learned about a candidate's values: a measurement, or why there is none.

    Containment, not similarity: a reference means the child's values are drawn from the parent's
    key, so the question is one-directional and the denominator is the child's distinct count.
    Overlap is reported too, because a high containment on a tiny child sample against a huge
    parent is weak, and the two numbers together show that.

    Where no number is honest (either side has no tokens, the two were keyed under different key
    IDs, normalised differently, or a side's distinct count is below `min_support`) the evidence
    is `value_overlap_not_measured` with the reason as a code and counts only. Ignorance is never
    reported as zero overlap: a zero measured here is the only thing the caller may drop on.
    No `confidence` is produced (§15): a measurement with its denominators, and the strength tier
    stays whatever the deterministic rules already chose.
    """

    def unmeasured(reason: str, why: str, metrics: dict[str, int] | None = None) -> Evidence:
        return _evidence(
            child,
            "value_overlap_not_measured",
            parent_id,
            f"containment:{parent_id}",
            {"reason": reason, **(metrics or {})},
            f"value overlap not measured: {why}",
        )

    if child_tokens is None or parent_tokens is None:
        return unmeasured(
            "no_value_tokens", "no value tokens for one side (sampling is off or unconfigured)"
        )
    if child_tokens.key_id != parent_tokens.key_id:
        # Tokens keyed under different run keys are unrelated byte strings. Comparing them would
        # always report zero overlap, which reads as a counterexample rather than as ignorance.
        return unmeasured("key_ids_differ", "the two sides were tokenised under different key IDs")
    if child_tokens.normalization != parent_tokens.normalization:
        return unmeasured("normalisation_differs", "the two sides were normalised differently")
    child_set, parent_set = set(child_tokens.tokens), set(parent_tokens.tokens)
    smallest = min(child_tokens.distinct_count, parent_tokens.distinct_count)
    # A capped side that kept no token bounds the range to nothing, which reads as too small.
    capped = [
        max(side.tokens, default="") for side in (child_tokens, parent_tokens) if side.truncated
    ]
    if capped:
        # Both sides keep the smallest hashes under one run key, so they are coordinated bottom-k
        # sketches: below the smaller of the capped sides' largest kept hash, each side holds
        # every hash it saw. Compared only there, a missing value is really missing from what was
        # read, and the child's support is what that range holds: containment divides by the
        # child (F5, owner decision 2026-10-19, DEC-19).
        # ponytail: a parent never read completely (a large ClickHouse table) still gives no
        # trusted zero; a full-column sketch read is the upgrade path (LEAD_REVIEW F5 option C).
        bound = min(capped)
        child_set = {token for token in child_set if token <= bound}
        parent_set = {token for token in parent_set if token <= bound}
        smallest = min(len(child_set), parent_tokens.distinct_count)
    if smallest < min_support or not child_set:
        return unmeasured(
            "sample_too_small",
            f"sample too small (minimum {min_support} distinct values per side)",
            {
                "child_distinct_sampled": child_tokens.distinct_count,
                "parent_distinct_sampled": parent_tokens.distinct_count,
                "minimum_support": min_support,
            },
        )
    shared = len(child_set & parent_set)
    union = len(child_set | parent_set)
    sampled_zero = not shared and not parent_tokens.complete
    # F14: only a parent read in part makes the fraction a floor. A capped side is compared inside
    # the common hash range, where it holds every value it saw, so that stays a measurement.
    floor = not parent_tokens.complete
    interval: dict[str, str | float | None] = {}
    if shared:
        estimate = shared / len(child_set)
        if floor:
            interval = {"basis": "lower_bound", "lower": estimate, "upper": None}
        else:
            interval = {"basis": "measured", **_wilson(shared, len(child_set), level)}
        interval["estimate"] = estimate
        interval = {
            key: round(value, 6) if isinstance(value, float) else value
            for key, value in interval.items()
        }
    return _evidence(
        child,
        "value_containment",
        parent_tokens.field_id,
        f"containment:{parent_tokens.field_id}",
        {
            # A zero against a parent that was only partly read says nothing: the child's values
            # may lie in the rows that were not read. Kept, and said, under its own code.
            **({"reason": "parent_sampled"} if sampled_zero else {}),
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
            "parent_truncated": parent_tokens.truncated,
            "parent_complete": parent_tokens.complete,
            "compared_in_common_hash_range": bool(capped),
            "key_id": child_tokens.key_id,
            "minimum_support": min_support,
            **interval,
        },
        "parent sampled, a zero is not a measurement" if sampled_zero else "",
    )


def _wilson(shared: int, total: int, level: float) -> dict[str, float]:
    """Wilson score interval on shared/total at a two-sided confidence level (stdlib only)."""
    z = NormalDist().inv_cdf(0.5 + level / 2)
    p = shared / total
    d = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / d
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / d
    return {"lower": max(0.0, centre - half), "upper": min(1.0, centre + half)}


def _percent(fraction: float, how: Callable[[float], float]) -> int:
    # Rounded to 6 places first so float noise (0.29 * 100 = 28.999...) cannot move a floor.
    return int(how(round(fraction * 100, 6)))


def _overlap_sentence(metrics: dict[str, Any], level: float) -> str:
    """The candidate's overlap words, from the metrics so the two cannot disagree (F14).

    Never overclaims: the estimate rounds to nearest, a floor ("at least", CI lower) rounds down
    and a CI upper rounds up; a nonzero share is never shown as 0% and a partial one never as 100%.
    """
    count = metrics["child_distinct_sampled"]
    estimate = metrics["estimate"]
    if metrics["basis"] == "lower_bound":
        floor = _percent(estimate, math.floor)
        # A share under 1% is said as the exact count instead of "at least 0%".
        figure = f"{floor}% of" if floor else f"{metrics['shared_tokens']} of"
        return (
            f"value overlap at least {figure} {count} sampled child values "
            "(parent only partly read; the true overlap may be much higher)"
        )
    nearest = _percent(estimate, lambda v: math.floor(v + 0.5))
    if estimate > 0 and nearest == 0:
        shown = "<1%"
    elif estimate < 1 and nearest == 100:
        shown = ">99%"
    else:
        shown = f"{nearest}%"
    low, high = _percent(metrics["lower"], math.floor), _percent(metrics["upper"], math.ceil)
    return (
        f"value overlap ≈{shown} ({level * 100:.10g}% CI {low}-{high}%) "
        f"of {count} sampled child values"
    )


# Spellings of one value domain across the three engines: PostgreSQL `format_type` names,
# ClickHouse type names, and the Python type names MongoDB sampling records. Before these were
# grouped, ClickHouse `String`, PostgreSQL `text` and MongoDB `str` were three different
# families, so no text key could ever be proposed across engines.
INTEGER_TYPES = frozenset(
    {"integer", "bigint", "smallint", "int", "int2", "int4", "int8", "serial", "bigserial"}
    | {f"{sign}int{bits}" for sign in ("", "u") for bits in (8, 16, 32, 64, 128, 256)}
)
TEXT_TYPES = frozenset(
    {"text", "character varying", "varchar", "character", "char", "bpchar", "citext",
     "string", "fixedstring", "str"}
)
UUID_TYPES = frozenset({"uuid", "uuid4"})
# ClickHouse wrappers that change storage or nullability, not the values a key can hold.
TRANSPARENT_WRAPPERS = ("nullable(", "lowcardinality(")


def _type_family(value: str) -> str:
    normalized = value.casefold().strip()
    unwrapped = True
    while unwrapped:
        unwrapped = False
        for wrapper in TRANSPARENT_WRAPPERS:
            if normalized.startswith(wrapper) and normalized.endswith(")"):
                normalized = normalized[len(wrapper) : -1].strip()
                unwrapped = True
    normalized = normalized.split("(", 1)[0].strip()
    if normalized in INTEGER_TYPES:
        return "integer"
    if normalized in TEXT_TYPES:
        return "text"
    if normalized in UUID_TYPES:
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
    field_profiles: dict[str, list[Profile]] = {}
    runs: list[Record] = []
    partial_source_receipts = partial_source_receipts or {}
    partial = bool(partial_source_receipts)
    # Why the snapshot is partial, recorded on the builder run so a reader is told the cause
    # rather than only the fact. Several causes produce no collector diagnostic at all.
    partial_reasons: set[str] = {
        f"PREVIOUS_COMPLETE_BUNDLE_USED {source}" for source in partial_source_receipts
    }
    for path in bundle_paths:
        manifest, records = _read_signed(path, config)
        bundle_sources = records["sources.jsonl"]
        if len(bundle_sources) != 1:
            raise ValueError("bundle must describe exactly one source")
        source = Source.model_validate(bundle_sources[0])
        if source.id in sources:
            raise ValueError("ambiguous source identity")
        sources[source.id] = source
        if manifest.completeness == "partial":
            partial = True
            partial_reasons.add(f"SOURCE_BUNDLE_PARTIAL {source.id}")
        for table in ("sources.jsonl", "objects.jsonl", "fields.jsonl"):
            for record in records[table]:
                if record.id in nodes:
                    raise ValueError("duplicate node ID across bundles")
                nodes[record.id] = record
        constraints.extend(Constraint.model_validate(item) for item in records["constraints.jsonl"])
        for item in records.get("profiles.jsonl", []):
            measured = Profile.model_validate(item.model_dump())
            # A field can carry more than one profile, a planner statistic and a sample, which
            # measure different things. They are kept together and read per measure, so a
            # statistic that knows uniqueness is not shadowed by a sample that does not.
            field_profiles.setdefault(measured.field_id, []).append(measured)
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
                    "id": bundle_run_id(source.id, manifest),
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
    # Declared multi-column keys, in declared order. A tuple's components are only meaningful
    # together and in sequence, so the order the source declared is preserved throughout.
    composite_keys: list[tuple[str, list[Field]]] = []
    declared_pairs: set[tuple[str, str]] = set()
    # ClickHouse keeps no unique constraint; a one-column sorting or primary key is only a weak
    # claim of a parent key (user decision 2026-10-13, DEC-2), labelled as such in the evidence.
    weak_key_ids: set[str] = set()
    for constraint in constraints:
        kind = constraint.kind
        owner = constraint.object_id
        ordinals = constraint.field_ordinals
        if (
            kind == "index"
            and constraint.native_id.endswith(WEAK_KEY_SUFFIXES)
            and len(constraint.field_names) == 1
        ):
            weak_field = by_object_name.get(owner, {}).get(constraint.field_names[0])
            if weak_field:
                weak_key_ids.add(weak_field.id)
        if kind in {"primary", "unique"} and len(ordinals) == 1:
            unique_field = by_object.get(owner, {}).get(ordinals[0])
            if unique_field:
                unique_fields.append(unique_field)
        elif kind in {"primary", "unique"} and len(constraint.field_names) == 1:
            unique_field = by_object_name.get(owner, {}).get(constraint.field_names[0])
            if unique_field:
                unique_fields.append(unique_field)
        elif kind in {"primary", "unique"} and len(ordinals) > 1:
            members = [by_object.get(owner, {}).get(ordinal) for ordinal in ordinals]
            if all(members):
                composite_keys.append((owner, [member for member in members if member]))
        elif kind in {"primary", "unique"} and len(constraint.field_names) > 1:
            named = [by_object_name.get(owner, {}).get(name) for name in constraint.field_names]
            if all(named):
                composite_keys.append((owner, [member for member in named if member]))
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
            partial_reasons.add("FOREIGN_KEY_TARGET_OUTSIDE_SNAPSHOT")
            continue
        pairs = []
        for left, right in zip(ordinals, target_ordinals, strict=True):
            child = by_object.get(owner, {}).get(left)
            parent = by_object.get(target, {}).get(right)
            if child is None or parent is None:
                partial = True
                partial_reasons.add("FOREIGN_KEY_TARGET_OUTSIDE_SNAPSHOT")
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
    # A field measured unique can be a parent key even with nothing declared, which is how a
    # table that never declared a primary key can still be referenced. Recorded separately so the
    # evidence says which it was: a catalog assertion and a measurement are not the same claim.
    declared_unique = {parent.id for parent in unique_fields}
    # A table that declares a key says which column identifies its rows. Measured uniqueness is
    # for tables that declare none: on a small lookup table every column measures unique, so
    # `regions.name` was proposed beside the declared `regions.code` (user decision 2026-10-05).
    keyed_objects = {parent.object_id for parent in unique_fields} | {
        owner for owner, _ in composite_keys
    }
    for candidate_parent in fields.values():
        if candidate_parent.id in declared_unique or candidate_parent.object_id in keyed_objects:
            continue
        if candidate_parent.id in weak_key_ids or (
            _measure(field_profiles.get(candidate_parent.id), "uniqueness") or 0.0
        ) >= MEASURED_UNIQUE:
            unique_fields.append(candidate_parent)
    dropped_zero_overlap = 0
    dropped_ordinary_name = 0
    known_objects = frozenset(
        form
        for item in objects.values()
        for name in (item.name.casefold(),)
        # The parent comparison drops a trailing `s`; `y` and `es` plurals are added so that
        # `companyid` finds `companies` and `boxid` finds `boxes`.
        for form in (
            name.rstrip("s"),
            name.removesuffix("ies") + "y" if name.endswith("ies") else name,
            name.removesuffix("es") if name.endswith("es") else name,
        )
    )
    for child in fields.values():
        child_object = objects.get(child.object_id)
        if child_object is None:
            continue
        # A field with no values cannot reference anything, so comparing it would produce name
        # evidence for a relationship that has nothing behind it.
        if _measure(field_profiles.get(child.id), "null_fraction") == 1.0:
            continue
        stem, suffixed = _reference_stem(child.name, known_objects)
        candidates = []
        for parent in unique_fields:
            parent_object = objects.get(parent.object_id)
            if (
                parent_object is None
                or parent.id == child.id
                or parent.object_id == child.object_id
                or (child.object_id, parent.object_id) in declared_pairs
                or not config.pair_allowed(child.source_id, parent.source_id)
                or _type_family(child.data_type) != _type_family(parent.data_type)
            ):
                continue
            if parent.id not in declared_unique and not _resembles(child, parent, config):
                # A column that only measures unique, or is a weak claim, is accepted when its
                # name is the child's or a generic key name, so the right table's wrong column
                # (`location.companyid` for a `locationid`) is never proposed (user decision
                # 2026-10-13, DEC-1). A declared key is exempt: the source said what it is.
                continue
            target = parent_object.name.casefold().rstrip("s")
            if (
                child.name.casefold() == parent.name.casefold()
                and child.name.casefold() not in GENERIC_KEY_NAMES
            ):
                # The child carries the parent's key name exactly, `AccountID` beside a primary
                # key `AccountID`: as strong a name signal as there is, whatever the parent table
                # is called (user decision 2026-10-05). A measured-unique parent column counts too
                # (user decision 2026-10-13, DEC-12) when it is named like a reference.
                if parent.id not in declared_unique and not _is_reference_style(parent.name):
                    # A column that only measures unique, or is only a weak key claim, and is not
                    # named like a reference (`email`, `description`, `freeMails`) links only when
                    # declared: data copied between tables shares values, so even measured overlap
                    # is not enough (owner decision 2026-10-19, DEC-20, replacing DEC-16's
                    # overlap allowance). Counted, never recorded by value.
                    dropped_ordinary_name += 1
                    continue
                candidates.append((100, parent, "key_name"))
                continue
            if suffixed:
                # The name announces itself as a reference, so a fuzzy match is justified.
                name_score = round(ratio(stem, target))
                if name_score < config.name_similarity_threshold:
                    continue
            else:
                # It announces nothing. Fuzzy-matching every field against every unique key would
                # trade precision away, so only an exact match after singularisation counts, which
                # is what catches a column called `customer` pointing at `customers`.
                if stem != target:
                    continue
                name_score = 100
            candidates.append((name_score, parent, "suffix" if suffixed else "exact_name"))
        # Several columns of one parent can qualify: the one named like the child wins over a
        # generic or merely-unique one (DEC-1). A declared key stays, whatever its name.
        objects_with_named_key = {
            parent.object_id
            for _, parent, _ in candidates
            if parent.name.casefold() not in GENERIC_KEY_NAMES
        }
        candidates = [
            item
            for item in candidates
            if item[1].object_id not in objects_with_named_key
            or item[1].name.casefold() not in GENERIC_KEY_NAMES
            or item[1].id in declared_unique
        ]
        kept = 0
        # The cut to `top_k_per_field` comes after the overlap check, so a dropped best name
        # match does not use up the slot of the parent that is really referenced.
        # Ties are broken by evidence, not by hash order (F10, LEAD_REVIEW 2026-10-19): same
        # source first, then a declared key, a weak key claim, measured uniqueness; then the id.
        child_source = child.source_id

        def rank(
            item: tuple[int, Field, str], source: str = child_source
        ) -> tuple[int, bool, int, str]:
            score, parent, _ = item
            claim = 0 if parent.id in declared_unique else 1 if parent.id in weak_key_ids else 2
            return (-score, parent.source_id != source, claim, parent.id)

        for score, parent, rule in sorted(candidates, key=rank):
            if kept >= config.top_k_per_field:
                break
            overlap = _overlap(
                child,
                parent.id,
                token_sets.get(child.id),
                token_sets.get(parent.id),
                config.min_token_support,
                config.overlap_confidence_level,
            )
            if (
                overlap.kind == "value_containment"
                and overlap.metrics["shared_tokens"] == 0
                # Only a parent read whole makes a zero a measurement; a capped set was already
                # compared inside the range where it is whole (DEC-19). A sampled child is still
                # a sample of that child: none of its values is in the complete parent.
                and overlap.metrics["parent_complete"]
            ):
                # Measured, with enough values on both sides, and not one value in common: the
                # columns are unrelated (user decision 2026-10-12). Counted, never recorded by
                # value. A non-zero overlap, however small, is shown and never drops.
                dropped_zero_overlap += 1
                continue
            kept += 1
            lexical = _evidence(
                child,
                "name_similarity",
                parent.id,
                f"name:{parent.id}",
                # Which blocking rule admitted the pair, so a reader can tell a fuzzy suffix match
                # from an exact bare-name match without re-deriving it.
                {"ratio_percent": score, "blocking_rule": rule},
            )
            typed = _evidence(child, "compatible_type", parent.id, f"type:{parent.id}")
            if parent.id in declared_unique:
                key = _evidence(parent, "catalog_key", child.id, f"unique:{child.id}")
                key_claim = "declared unique key"
            elif parent.id in weak_key_ids:
                key = _evidence(
                    parent,
                    "weak_key_claim",
                    child.id,
                    f"weak-key:{child.id}",
                    explanation="the source's sorting or primary key; not a uniqueness guarantee",
                )
                key_claim = "weak key claim (source sorting or primary key, uniqueness unproven)"
            else:
                # A measurement, not a catalog assertion. Kept as a separate evidence kind so the
                # two are never read as the same strength of claim.
                key = _evidence(
                    parent,
                    "measured_uniqueness",
                    child.id,
                    f"measured-unique:{child.id}",
                    {
                        "uniqueness": _measure(field_profiles.get(parent.id), "uniqueness"),
                        "threshold": MEASURED_UNIQUE,
                    },
                )
                key_claim = "uniqueness measured from source statistics, not declared"
            for item in (lexical, typed, key):
                evidence[item.id] = item
            cited = [lexical.id, typed.id, key.id]
            explanation = f"name, compatible type, and {key_claim}"
            evidence[overlap.id] = overlap
            cited.append(overlap.id)
            if overlap.kind == "value_overlap_not_measured":
                explanation = f"{explanation}; values not compared ({overlap.explanation})"
            elif "reason" in overlap.metrics:
                # A zero against a parent that was only partly read (P6-2): not a measurement.
                explanation = f"{explanation}; {overlap.explanation}"
            else:
                explanation = (
                    f"{explanation}; "
                    f"{_overlap_sentence(overlap.metrics, config.overlap_confidence_level)}"
                )
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
    _composite_candidates(
        composite_keys,
        objects,
        by_object_name,
        declared_pairs,
        field_profiles,
        evidence,
        edges,
        config,
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
        if review_status in MODEL_FAILURE_CODES:
            partial = True
            partial_reasons.update({"MODEL_REVIEW_FAILED", review_status})
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
                # Only when something was dropped, so other snapshots' bytes are unchanged.
                **(
                    {"dropped_zero_overlap_candidates": dropped_zero_overlap}
                    if dropped_zero_overlap
                    else {}
                ),
                # Renamed from `dropped_unmeasured_key_name_candidates` (P6-4, DEC-20): an
                # ordinary name on an undeclared parent is now dropped whether measured or not.
                **(
                    {"dropped_ordinary_name_candidates": dropped_ordinary_name}
                    if dropped_ordinary_name
                    else {}
                ),
                # Only when partial, so a complete snapshot's bytes are unchanged.
                **({"partial_reasons": sorted(partial_reasons)} if partial_reasons else {}),
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
        partial_reasons=sorted(partial_reasons),
    )
