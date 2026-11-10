"""Engine type names grouped into value domains, shared by the builder and the requester."""

from __future__ import annotations

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


def type_family(value: str) -> str:
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
