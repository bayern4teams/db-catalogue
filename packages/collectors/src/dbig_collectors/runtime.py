"""One-source discovery with bounded metadata reads and sealed publication."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import socket
import ssl
import unicodedata
from collections import Counter, defaultdict
from decimal import Decimal
from importlib.metadata import version
from itertools import groupby
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl
from uuid import uuid4

from dbig_contracts.artifact import write_artifact
from dbig_contracts.errors import OperatorError
from dbig_contracts.ids import stable_id
from dbig_contracts.models import (
    DESCRIPTION_MAX_LENGTH,
    Field,
    Object,
    Profile,
    Record,
    Source,
    ValueRanges,
    ValueTokens,
    utc_now,
)
from dbig_contracts.security import (
    is_loopback_literal,
    key_from_env,
    private_address,
    private_host_refusal,
    secret_from_env,
)

from .config import SourceConfig

# pg8000's own default when the connection reference names no port.
PG_DEFAULT_PORT = 5432


class BudgetExhausted(RuntimeError):
    """A configured query, object or field limit was reached before the scan finished.

    A `RuntimeError` with the message `BUDGET_EXHAUSTED`, as before, so existing handling keeps
    working; its own type lets `collect` recognise it without matching message text.
    """

    def __init__(self) -> None:
        super().__init__("BUDGET_EXHAUSTED")


class Budget:
    def __init__(self, max_queries: int) -> None:
        self.max_queries = max_queries
        self.used = 0

    def charge(self) -> None:
        if self.used >= self.max_queries:
            raise BudgetExhausted
        self.used += 1


def _description(value: Any, config: SourceConfig) -> str | None:
    """A source comment made safe to carry: no control characters, bounded, empty is absent.

    Newlines and tabs are kept because comments are often written as paragraphs. Anything longer
    than the contract allows is cut and ends with an ellipsis, so a reader can tell.
    """
    if not config.include_comments or value is None:
        return None
    text = "".join(
        character
        for character in str(value)
        if character in "\n\t" or (character.isprintable() and character != "\x7f")
    ).strip()
    if not text:
        return None
    if len(text) > DESCRIPTION_MAX_LENGTH:
        text = text[: DESCRIPTION_MAX_LENGTH - 1].rstrip() + "\u2026"
    return text


def _common(config: SourceConfig) -> dict[str, Any]:
    return {
        "source_id": config.source_id,
        "observed_at": utc_now(),
        "collector_version": version("dbig-collectors"),
        "provenance": "source-catalog",
    }


IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def _quoted(name: str) -> str:
    """Quote a catalog identifier, refusing anything that is not one.

    The names come from the server's own catalog rather than from a caller, so this is a
    belt-and-braces check: a name that cannot be an identifier means something is wrong with the
    read, and reading on would build SQL from an unexpected string.
    """
    if not IDENTIFIER.match(name):
        raise ValueError(f"refusing to build SQL for unexpected identifier: {name!r}")
    return f'"{name}"'


def _token_text(value: Any) -> str | None:
    """Normalise one sampled value to the text that gets keyed, or nothing if it cannot be.

    `exact_text` normalisation deliberately does not fold case or trim: §8 requires case, locale
    and leading zeros to be preserved, because "01" and "1" are different keys in some systems
    and the same in others, and guessing which would invent matches. It does mean an integer 123
    and the string "123" produce the same token, which is the cross-source case that matters most:
    a MongoDB string identifier pointing at a relational integer key.

    Booleans, bytes and structured values return nothing. They are counted as unusable rather
    than coerced, because there is no normalisation for them that two different engines would
    agree on.
    """
    if value is None or isinstance(value, bool | bytes | bytearray | dict | list):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return value
    if isinstance(value, float | Decimal):
        # Canonical decimal text, so 1.0 and 1 agree and float repr noise does not leak in.
        as_decimal = Decimal(str(value)).normalize()
        return format(as_decimal, "f")
    return None


def _value_tokens(
    values: list[Any],
    *,
    key: bytes,
    limit: int,
) -> tuple[list[str], int, int, bool]:
    """Key the distinct usable values, capped, returning the honest denominators.

    `observed` and `distinct` describe the sample, not the cap, because they are the denominators
    a containment metric divides by. `truncated` says the token set is a subset, which makes any
    containment computed from it a lower bound rather than a measurement.
    """
    digests: set[str] = set()
    observed = 0
    for value in values:
        text = _token_text(value)
        if text is None:
            continue
        observed += 1
        digests.add(_digest(text, key))
    return _capped_tokens(digests, observed, limit)


def _digest(text: str, key: bytes) -> str:
    return hmac.new(key, text.encode("utf-8"), hashlib.sha256).hexdigest()


def _capped_tokens(
    digests: set[str], observed: int, limit: int
) -> tuple[list[str], int, int, bool]:
    """Apply the token cap, keeping the denominators describing the sample rather than the cap.

    Shared by both connectors so `distinct_count` means the same thing on either side of a
    containment comparison. Sorted, so the file's bytes and therefore its digest do not depend on
    the order values happened to be read in.
    """
    distinct = len(digests)
    kept = sorted(digests)[:limit]
    return kept, observed, distinct, distinct > len(kept)


def _tokenize_postgresql(
    cursor: Any,
    config: SourceConfig,
    budget: Budget,
    tables: dict[str, list[Record]],
    object_rows: list[Any],
    field_rows: list[Any],
    common: dict[str, Any],
) -> None:
    """Sample bounded rows per table and publish keyed tokens for their values.

    Off unless sampling is configured *and* a token key reference is given, because a token can
    only come from a value that was read and reading values is not the default. One bounded read
    per table, with the discovered columns named explicitly rather than `SELECT *`, so the read
    is predictable and the byte cost is bounded by the row limit.

    The sample is the first rows the server returns, which is biased, and `sample_method` says so.
    §20 rules out the alternatives that would look fairer: `ORDER BY random()` and unbounded
    scans are exactly the reads that hurt a production source, and `TABLESAMPLE` plus `LIMIT`
    guarantees neither a cheap nor an unbiased fixed-size sample.

    Raw values never leave this function. Only keyed digests and counts are published.
    """
    if config.sample_rows <= 0 or not config.value_token_key_env:
        return
    key = key_from_env(config.value_token_key_env, purpose="cross-source value tokenisation")
    key_id = config.value_token_key_id or ""
    identity = {oid: (schema, name) for oid, schema, name, kind, *_ in object_rows if kind in "rmp"}
    columns: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for oid, attnum, name, *_ in field_rows:
        if oid in identity:
            columns[oid].append((attnum, name))
    for oid, entries in sorted(columns.items()):
        schema, table = identity[oid]
        names = [name for _, name in sorted(entries)]
        budget.charge()
        selected = ",".join(_quoted(name) for name in names)
        cursor.execute(
            # Identifiers are quoted from the server's own catalog and validated by `_quoted`;
            # the row limit is bound. No caller input reaches this statement.
            f"SELECT {selected} FROM {_quoted(schema)}.{_quoted(table)} "  # noqa: S608 # C5: reason=only placeholder tokens or quoted identifiers are interpolated, values are bound; approved=user, 2026-11-08; review=2027-05-08
            "LIMIT %s",
            (config.sample_rows,),
        )
        rows = cursor.fetchall()
        for position, (attnum, _) in enumerate(sorted(entries)):
            field_id = stable_id(config.source_id, "field", f"{oid}:{attnum}")
            digests, observed, distinct, truncated = _value_tokens(
                [row[position] for row in rows], key=key, limit=config.max_value_tokens
            )
            if not digests:
                continue
            tables["tokens.jsonl"].append(
                ValueTokens(
                    id=stable_id(config.source_id, "tokens", field_id),
                    field_id=field_id,
                    object_id=stable_id(config.source_id, "object", str(oid)),
                    key_id=key_id,
                    sample_count=len(rows),
                    sample_method=f"first_rows_limit:{config.sample_rows}",
                    observed_count=observed,
                    distinct_count=distinct,
                    truncated=truncated,
                    # A `LIMIT` read that returned fewer rows than it allowed read the whole table.
                    complete=len(rows) < config.sample_rows,
                    tokens=digests,
                    **common,
                )
            )


def _profile_postgresql(
    cursor: Any,
    config: SourceConfig,
    budget: Budget,
    tables: dict[str, list[Record]],
    object_rows: list[Any],
    field_rows: list[Any],
    common: dict[str, Any],
) -> None:
    """Profile fields from planner statistics, reading no user data.

    `pg_stats` exposes what the planner already computed, so this stays inside the metadata-only
    default: no row is read and no value is sampled. `most_common_vals` and histogram bounds are
    deliberately not selected, because those columns contain real values.

    The catalogue keys a field by `oid:attnum` while `pg_stats` keys by schema, table and column
    name, so the two are joined here on the names already discovered.
    """
    identity = {oid: (schema, name) for oid, schema, name, *_ in object_rows}
    by_name: dict[tuple[str, str, str], tuple[str, str]] = {}
    for oid, attnum, name, *_ in field_rows:
        if oid not in identity:
            continue
        schema, table = identity[oid]
        native = f"{oid}:{attnum}"
        by_name[(schema, table, name)] = (
            stable_id(config.source_id, "field", native),
            stable_id(config.source_id, "object", str(oid)),
        )
    if not by_name:
        return
    placeholders = ",".join(["%s"] * len(config.namespaces))
    budget.charge()
    # Only placeholder tokens are interpolated; schema names remain bound values.
    cursor.execute(
        "SELECT s.schemaname, s.tablename, s.attname, s.null_frac, s.n_distinct, "  # noqa: S608 # C5: reason=only placeholder tokens or quoted identifiers are interpolated, values are bound; approved=user, 2026-11-08; review=2027-05-08
        "s.avg_width, a.last_analyze, a.last_autoanalyze, c.reltuples "
        "FROM pg_catalog.pg_stats s "
        "JOIN pg_catalog.pg_class c ON c.relname = s.tablename "
        "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
        "AND n.nspname = s.schemaname "
        "LEFT JOIN pg_catalog.pg_stat_all_tables a ON a.relid = c.oid "
        f"WHERE s.schemaname IN ({placeholders}) ORDER BY s.schemaname, s.tablename, s.attname "
        "LIMIT %s",
        (*config.namespaces, config.max_fields + 1),
    )
    stat_rows = cursor.fetchall()
    if len(stat_rows) > config.max_fields:
        raise BudgetExhausted
    for schema, table, column, null_frac, n_distinct, width, analyzed, auto, reltuples in stat_rows:
        target = by_name.get((schema, table, column))
        if target is None:
            continue
        field_id, object_id = target
        distinct, ratio_derived, uniqueness = _distinct_from_pg(n_distinct, reltuples)
        tables["profiles.jsonl"].append(
            Profile(
                id=stable_id(config.source_id, "profile", f"{field_id}:source_statistic"),
                field_id=field_id,
                object_id=object_id,
                method="source_statistic",
                stats_updated_at=_latest(analyzed, auto),
                null_fraction=min(max(float(null_frac), 0.0), 1.0)
                if null_frac is not None
                else None,
                distinct_estimate=distinct,
                distinct_is_ratio_derived=ratio_derived,
                uniqueness=uniqueness,
                average_width_bytes=int(width) if width is not None and width >= 0 else None,
                **common,
            )
        )


def _distinct_from_pg(
    n_distinct: float | None, reltuples: float | None
) -> tuple[float | None, bool, float | None]:
    """Interpret `pg_stats.n_distinct`, whose sign changes its meaning.

    A positive value is an absolute estimated count. A negative value is the negative of the
    distinct-to-row ratio, which PostgreSQL uses when distinctness scales with table size. Zero
    means unknown. Reading a negative value as a count would report a nonsense field, so the two
    cases are separated here and the ratio-derived case is flagged, since converting it to an
    absolute count multiplies in the row-count estimate's own error.
    """
    if n_distinct is None or n_distinct == 0:
        return None, False, None
    if n_distinct > 0:
        rows = float(reltuples) if reltuples is not None and reltuples > 0 else None
        uniqueness = min(n_distinct / rows, 1.0) if rows else None
        return float(n_distinct), False, uniqueness
    ratio = min(-float(n_distinct), 1.0)
    rows = float(reltuples) if reltuples is not None and reltuples > 0 else None
    return (ratio * rows if rows else None), rows is not None, ratio


def _latest(*values: Any) -> Any:
    present = [value for value in values if value is not None]
    return max(present) if present else None


# --- value-free format profiles (P7-5 option C, F11-b) --------------------------------------------

_INTEGER_TYPES = frozenset(
    {"integer", "bigint", "smallint", "int", "int2", "int4", "int8", "serial", "bigserial"}
    | {f"{sign}int{bits}" for sign in ("", "u") for bits in (8, 16, 32, 64, 128, 256)}
)
_TEXT_TYPES = frozenset(
    {"text", "character varying", "varchar", "character", "char", "bpchar", "citext",
     "string", "fixedstring"}
)
_UUID_TYPES = frozenset({"uuid"})
_DIGITS = re.compile(r"[0-9]+")
_HEX = re.compile(r"[0-9a-fA-F]+")
_UUID = re.compile(r"[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")


def _format_family(data_type: str) -> str | None:
    """`integer`, `text` or `uuid` for the types a candidate can link, else nothing.

    Mirrors the builder's type families; a wrapper such as ClickHouse `Nullable(...)` is looked
    through, and a parameter such as `varchar(32)` or an array suffix is not part of the name.
    """
    name = data_type.casefold().strip()
    if name.endswith("]"):
        return None  # a PostgreSQL array: its text form is a list, not a value of the element type
    while True:
        for wrapper in ("nullable(", "lowcardinality("):
            if name.startswith(wrapper) and name.endswith(")"):
                name = name[len(wrapper) : -1].strip()
                break
        else:
            break
    name = name.split("(", 1)[0].strip()
    if name in _INTEGER_TYPES:
        return "integer"
    if name in _TEXT_TYPES:
        return "text"
    return "uuid" if name in _UUID_TYPES else None


# The only non-alphanumeric characters a shape keeps. Anything else (space, a control character,
# a brace, a quote, a symbol, an emoji, a mark, a letter-like or digit-like sign that is neither)
# becomes `_SHAPE_OTHER`, so no character of a value outside this list can survive into a profile.
# The SQL statements use the same two constants.
_SHAPE_PUNCT = "!#$&()+,-./:<=>@[]^_`|~"
_SHAPE_OTHER = "*"


def _shape_class(char: str) -> str:
    """`9` for a decimal digit, `a` for a letter, the character itself if whitelisted, else `*`."""
    if "0" <= char <= "9":
        return "9"
    category = unicodedata.category(char)
    if category == "Nd":
        return "9"
    if category[0] == "L":
        return "a"
    return char if char in _SHAPE_PUNCT else _SHAPE_OTHER


def _shape_token(text: str, limit: int) -> str:
    """The shape of a value: digit runs `9{n}`, letter runs `a{n}`, whitelisted punctuation as is.

    Every other character is one `*`. Made from the first `limit` characters, so a very long value
    cannot make a very long token. The SQL profile statements make the same token; the two must
    agree because a shape is only compared with a shape from another engine.
    """
    out: list[str] = []
    for key, group in groupby(text[:limit], key=_shape_class):
        size = len(list(group))
        # Only a digit or letter run is keyed "9" or "a"; no other class is either.
        out.append(f"{key}{{{size}}}" if key in ("9", "a") else key * size)
    return "".join(out)


class _Shape:
    """Running format statistics for one MongoDB path, from sampled scalar values.

    Only counts, lengths and shape tokens are kept; no value is stored. Numbers are text for the
    shape (an integer's decimal digits), as the relational side reads them.
    """

    def __init__(self) -> None:
        self.count = 0
        self.length_min = self.length_max = self.length_sum = 0
        self.digits = self.hex = self.uuid = self.alpha = 0
        self.formats: Counter[str] = Counter()
        self.integer_range: tuple[int, int] | None = None
        self.only_integers = True

    def add(self, value: Any, limit: int) -> None:
        if value is None:
            return
        is_integer = isinstance(value, int) and not isinstance(value, bool)
        if not is_integer and not isinstance(value, str):
            self.only_integers = False
            return
        text = str(value)
        size = len(text)
        self.length_min = size if not self.count else min(self.length_min, size)
        self.length_max = max(self.length_max, size)
        self.length_sum += size
        self.count += 1
        self.digits += bool(_DIGITS.fullmatch(text))
        self.hex += bool(_HEX.fullmatch(text))
        self.uuid += bool(_UUID.fullmatch(text))
        self.alpha += bool(text.isalpha())
        self.formats[_shape_token(text, limit)] += 1
        if is_integer:
            low, high = self.integer_range or (value, value)
            self.integer_range = (min(low, value), max(high, value))
        else:
            self.only_integers = False


class _Shapes:
    """The `_Shape` of every MongoDB path met in one collection's sample."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.by_path: dict[str, _Shape] = {}

    def add(self, path: str, value: Any) -> None:
        self.by_path.setdefault(path, _Shape()).add(value, self.limit)


def _finite(value: Any) -> float | None:
    """A plain finite float, or nothing: ClickHouse averages over no rows are nan, and PostgreSQL
    returns Decimal, which would be written as a JSON string."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _share(value: Any) -> float | None:
    number = _finite(value)
    return None if number is None else min(max(number, 0.0), 1.0)


def _exact_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal) and value == value.to_integral_value():
        return int(value)
    return None


def _format_fields(
    count: int | None,
    length_min: Any,
    length_max: Any,
    length_avg: Any,
    shares: tuple[Any, Any, Any, Any],
    formats: dict[str, Any],
    *,
    shapes_seen: int | None = None,
    complete: bool | None = None,
) -> dict[str, Any]:
    """The profile attributes, or nothing when no value was seen (an empty or all-null column)."""
    low, high = _exact_int(length_min), _exact_int(length_max)
    if (count is not None and count <= 0) or low is None or high is None or low < 0:
        return {}
    kept = {
        token: share for token, value in formats.items() if (share := _share(value)) is not None
    }
    digits, hexadecimal, uuid, alpha = (_share(item) for item in shares)
    return {
        "length_min": low,
        "length_max": high,
        "length_avg": _finite(length_avg),
        "digits_share": digits,
        "hex_share": hexadecimal,
        "uuid_share": uuid,
        "alpha_share": alpha,
        "formats": kept or None,
        # Only a count larger than the list proves it was cut; no count is "unknown".
        "formats_truncated": (shapes_seen > len(kept)) if shapes_seen is not None else None,
        "values_complete": complete,
    }


def _format_records(
    config: SourceConfig,
    common: dict[str, Any],
    field_id: str,
    object_id: str,
    names: list[str],
    rows: list[Any],
) -> list[Record]:
    """Turn the rows of one profile statement into a profile and, for an integer, a range row.

    Answered by output name, so the order of the statement's columns does not matter. The first
    row carries the column-wide numbers; every row carries one format and its share.
    """
    if not rows:
        return []
    records = [dict(zip(names, row, strict=False)) for row in rows]
    head = records[0]
    count = _exact_int(head.get("value_count"))
    fields = _format_fields(
        count,
        head.get("length_min"),
        head.get("length_max"),
        head.get("length_avg"),
        (head.get("digits_share"), head.get("hex_share"), head.get("uuid_share"),
         head.get("alpha_share")),
        {
            item["format_token"]: item.get("format_share")
            for item in records
            if item.get("format_token") is not None
        },
        shapes_seen=_exact_int(head.get("shape_count")),
        complete=True,  # a SQL aggregate reads the whole column
    )
    if not fields:
        return []
    out: list[Record] = [
        Profile(
            id=stable_id(config.source_id, "profile", f"{field_id}:format"),
            field_id=field_id,
            object_id=object_id,
            method="sample",
            sample_count=count,
            **fields,
            **common,
        )
    ]
    low, high = _exact_int(head.get("value_min")), _exact_int(head.get("value_max"))
    if low is not None and high is not None and low <= high:
        out.append(
            ValueRanges(
                id=stable_id(config.source_id, "ranges", field_id),
                field_id=field_id,
                object_id=object_id,
                minimum=low,
                maximum=high,
                **common,
            )
        )
    return out


# One statement per column, one scan of it: the groups are the distinct shapes, and the column-wide
# numbers are windows over them. `@NAME@` marks where a validated identifier or a configured
# number goes; no value and no caller input reaches the text. No `%`, `{name:Type}` pair or `?`
# appears, so neither driver reads anything as a parameter.
_FORMAT_PG = """
WITH v AS (SELECT @COL@::text AS dbig_t@XSEL@ FROM @REL@ WHERE @COL@ IS NOT NULL),
w AS (SELECT dbig_t@XCARRY@, coalesce((SELECT string_agg(CASE
  WHEN m.p[1] ~ '^[0-9]' THEN '9{' || length(m.p[1])::text || '}'
  WHEN m.p[1] ~ '^[[:alpha:]]' THEN 'a{' || length(m.p[1])::text || '}'
  WHEN strpos('@PUNCT@', m.p[1]) > 0 THEN m.p[1]
  ELSE '@OTHER@' END, '' ORDER BY m.n)
  FROM regexp_matches(left(v.dbig_t, @TOKEN@), '[0-9]+|[[:alpha:]]+|.', 'g')
  WITH ORDINALITY AS m(p, n)), '') AS tok FROM v),
grp AS (SELECT tok, count(*) AS c, sum(length(dbig_t)) AS len_sum, min(length(dbig_t)) AS len_lo,
  max(length(dbig_t)) AS len_hi, sum((dbig_t ~ '^[0-9]+$')::int) AS dg,
  sum((dbig_t ~ '^[0-9a-fA-F]+$')::int) AS hx,
  sum((dbig_t ~* '^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$')::int) AS uu,
  sum((dbig_t ~ '^[[:alpha:]]+$')::int) AS al@XGROUP@ FROM w GROUP BY tok)
SELECT (sum(c) OVER ()) AS value_count, min(len_lo) OVER () AS length_min,
  max(len_hi) OVER () AS length_max,
  (sum(len_sum) OVER ())::float8 / (sum(c) OVER ()) AS length_avg,
  (sum(dg) OVER ())::float8 / (sum(c) OVER ()) AS digits_share,
  (sum(hx) OVER ())::float8 / (sum(c) OVER ()) AS hex_share,
  (sum(uu) OVER ())::float8 / (sum(c) OVER ()) AS uuid_share,
  (sum(al) OVER ())::float8 / (sum(c) OVER ()) AS alpha_share,
  count(*) OVER () AS shape_count,
  tok AS format_token, c::float8 / (sum(c) OVER ()) AS format_share@XOUT@
FROM grp ORDER BY c DESC, tok LIMIT @FORMATS@
"""
_FORMAT_CH = r"""
WITH v AS (SELECT @TEXT@ AS dbig_t@XSEL@ FROM @REL@ WHERE @COL@ IS NOT NULL),
w AS (SELECT dbig_t@XCARRY@, arrayStringConcat(arrayMap(piece -> multiIf(
  match(piece, '^\\p{Nd}'), concat('9{', toString(lengthUTF8(piece)), '}'),
  match(piece, '^\\pL'), concat('a{', toString(lengthUTF8(piece)), '}'),
  position('@PUNCT@', piece) > 0, piece, '@OTHER@'),
  extractAll(leftUTF8(dbig_t, @TOKEN@), '\\p{Nd}+|\\pL+|[\\s\\S]'))) AS tok FROM v),
grp AS (SELECT tok, count() AS c, sum(lengthUTF8(dbig_t)) AS len_sum,
  min(lengthUTF8(dbig_t)) AS len_lo,
  max(lengthUTF8(dbig_t)) AS len_hi, sum(match(dbig_t, '^[0-9]+$')) AS dg,
  sum(match(dbig_t, '^[0-9a-fA-F]+$')) AS hx,
  sum(match(dbig_t, '^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$')) AS uu,
  sum(match(dbig_t, '^\\pL+$')) AS al@XGROUP@ FROM w GROUP BY tok)
SELECT toUInt64(sum(c) OVER ()) AS value_count, min(len_lo) OVER () AS length_min,
  max(len_hi) OVER () AS length_max, sum(len_sum) OVER () / sum(c) OVER () AS length_avg,
  sum(dg) OVER () / sum(c) OVER () AS digits_share, sum(hx) OVER () / sum(c) OVER () AS hex_share,
  sum(uu) OVER () / sum(c) OVER () AS uuid_share, sum(al) OVER () / sum(c) OVER () AS alpha_share,
  count() OVER () AS shape_count,
  tok AS format_token, c / sum(c) OVER () AS format_share@XOUT@
FROM grp ORDER BY c DESC, tok LIMIT @FORMATS@
"""


def _format_statement(
    template: str,
    config: SourceConfig,
    relation: str,
    column: str,
    text: str,
    integer: bool,
) -> str:
    """Fill a profile template. `relation` and `column` are already quoted (`_quoted`).

    The working names (`dbig_t`, `dbig_x`) are unlikely column names: ClickHouse resolves an alias
    across a whole query, so a column called like the alias would otherwise be read as the alias.
    """
    parts = {
        "@REL@": relation,
        "@COL@": column,
        "@TEXT@": text,
        "@TOKEN@": str(config.format_token_max_length),
        "@FORMATS@": str(config.format_profile_max_formats),
        "@PUNCT@": _SHAPE_PUNCT,
        "@OTHER@": _SHAPE_OTHER,
        "@XSEL@": f", {column} AS dbig_x" if integer else "",
        "@XCARRY@": ", dbig_x" if integer else "",
        "@XGROUP@": ", min(dbig_x) AS x_lo, max(dbig_x) AS x_hi" if integer else "",
        "@XOUT@": (
            ", min(x_lo) OVER () AS value_min, max(x_hi) OVER () AS value_max" if integer else ""
        ),
    }
    for marker, value in parts.items():
        template = template.replace(marker, value)
    return " ".join(template.split())


def _diagnostic(
    config: SourceConfig, common: dict[str, Any], key: str, code: str, message: str
) -> Record:
    return Record.model_validate(
        {
            "record_type": "diagnostic",
            "id": stable_id(config.source_id, "diagnostic", key),
            "code": code,
            "message": message,
            **common,
        }
    )


def _format_unavailable(config: SourceConfig, common: dict[str, Any], key: str) -> Record:
    return _diagnostic(
        config,
        common,
        f"{key}:format",
        "STATS_UNAVAILABLE",
        "format profile unavailable",
    )


def _format_skipped(
    config: SourceConfig, common: dict[str, Any], key: str, count: int, budget: Budget
) -> Record:
    """One diagnostic for the profiles left unread because the query budget ran out: a count."""
    return _diagnostic(
        config,
        common,
        f"{key}:format-skipped",
        "FORMAT_PROFILES_SKIPPED",
        f"{count} format profiles not read: query budget reached; "
        f"set max_queries to at least {budget.used + count} for this source to read them",
    )


def _profile_formats_postgresql(
    connection: Any,
    cursor: Any,
    config: SourceConfig,
    budget: Budget,
    tables: dict[str, list[Record]],
    object_rows: list[Any],
    field_rows: list[Any],
    common: dict[str, Any],
) -> None:
    """One whole-column aggregate per text, integer or uuid column; see `_FORMAT_PG`.

    Off unless `format_profiles`. Needs no sample and no token key. Each statement is charged to
    the budget and runs inside the scan's read-only transaction under its `statement_timeout`.
    The pass stops, with one count in a diagnostic, when the budget has no query left: an optional
    read never uses the last queries up and so never costs the catalogue. A name the collector
    will not quote, or a statement the server refuses or times out on, costs that column its
    profile and leaves a code-only diagnostic; the failed transaction is rolled back and re-armed
    so the scan goes on.
    """
    if not config.format_profiles:
        return
    identity = {oid: (schema, name) for oid, schema, name, kind, *_ in object_rows if kind in "rmp"}
    skipped = 0
    for oid, attnum, name, data_type, *_ in field_rows:
        family = _format_family(str(data_type))
        if family is None or oid not in identity:
            continue
        if budget.used >= budget.max_queries:
            skipped += 1
            continue
        schema, table = identity[oid]
        try:
            statement = _format_statement(
                _FORMAT_PG, config, f"{_quoted(schema)}.{_quoted(table)}", _quoted(name), "",
                family == "integer",
            )
        except ValueError:  # a name that is no plain identifier: skipped, never an abort
            tables["diagnostics.jsonl"].append(
                _format_unavailable(config, common, f"{oid}:{attnum}")
            )
            continue
        budget.charge()
        try:
            cursor.execute(statement)
            names = [
                item[0].decode() if isinstance(item[0], bytes) else str(item[0])
                for item in cursor.description
            ]
            rows = cursor.fetchall()
        except Exception:
            connection.rollback()
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute(
                "SELECT set_config('statement_timeout', %s, true)",
                (str(config.read_timeout_seconds * 1000),),
            )
            tables["diagnostics.jsonl"].append(
                _format_unavailable(config, common, f"{oid}:{attnum}")
            )
            continue
        for record in _format_records(
            config,
            common,
            stable_id(config.source_id, "field", f"{oid}:{attnum}"),
            stable_id(config.source_id, "object", str(oid)),
            names,
            rows,
        ):
            tables["ranges.jsonl" if isinstance(record, ValueRanges) else "profiles.jsonl"].append(
                record
            )
    if skipped:
        tables["diagnostics.jsonl"].append(
            _format_skipped(config, common, "postgresql", skipped, budget)
        )


def _profile_formats_clickhouse(
    client: Any,
    config: SourceConfig,
    budget: Budget,
    database: str,
    identities: dict[str, str],
    views: set[str],
    columns: dict[str, list[tuple[str, str, str]]],
    tables: dict[str, list[Record]],
    common: dict[str, Any],
) -> int:
    """The ClickHouse counterpart of `_profile_formats_postgresql`, under `max_execution_time`.

    A view is not read, since reading one runs its query. A column of an empty table has no
    groups, so no row comes back and no profile is made: an empty column is never a [0, 0] range.
    Returns how many profiles were skipped for want of budget; the caller reports the total.
    """
    skipped = 0
    if not config.format_profiles:
        return skipped
    try:
        quoted_database = _quoted(database)
    except ValueError:  # a database that is no plain identifier: skipped, never an abort
        tables["diagnostics.jsonl"].append(_format_unavailable(config, common, database))
        return skipped
    for table, entries in sorted(columns.items()):
        if table in views:
            continue
        try:
            relation = f"{quoted_database}.{_quoted(table)}"
        except ValueError:
            tables["diagnostics.jsonl"].append(
                _format_unavailable(config, common, f"{database}:{table}")
            )
            continue
        for field_id, name, data_type in entries:
            family = _format_family(data_type)
            if family is None:
                continue
            if budget.used >= budget.max_queries:
                skipped += 1
                continue
            try:
                quoted = _quoted(name)
            except ValueError:
                tables["diagnostics.jsonl"].append(
                    _format_unavailable(config, common, f"{database}:{table}:{field_id}")
                )
                continue
            statement = _format_statement(
                _FORMAT_CH,
                config,
                relation,
                quoted,
                f"toStringCutToZero({quoted})"
                if "fixedstring" in data_type.casefold()
                else f"toString({quoted})",
                family == "integer",
            )
            budget.charge()
            try:
                result = client.query(
                    statement, settings={"max_execution_time": config.read_timeout_seconds}
                )
                rows, names = list(result.result_set), list(result.column_names)
            except Exception:
                tables["diagnostics.jsonl"].append(
                    _format_unavailable(config, common, f"{database}:{table}:{name}")
                )
                continue
            for record in _format_records(
                config, common, field_id, stable_id(config.source_id, "object", identities[table]),
                names, rows,
            ):
                tables[
                    "ranges.jsonl" if isinstance(record, ValueRanges) else "profiles.jsonl"
                ].append(record)
    return skipped


def discover_postgresql(
    connection: Any, config: SourceConfig, budget: Budget
) -> dict[str, list[Record]]:
    """Read pg_catalog using one read-only transaction and no user table reads."""
    common = _common(config)
    tables: dict[str, list[Record]] = defaultdict(list)
    cursor = connection.cursor()
    try:
        cursor.execute("SET TRANSACTION READ ONLY")
        cursor.execute(
            "SELECT set_config('statement_timeout', %s, true)",
            (str(config.read_timeout_seconds * 1000),),
        )
        placeholders = ",".join(["%s"] * len(config.namespaces))
        budget.charge()
        # Only placeholder tokens are interpolated; schema names remain bound values.
        cursor.execute(
            "SELECT c.oid, n.nspname, c.relname, c.relkind, c.reltuples, "  # noqa: S608 # C5: reason=only placeholder tokens or quoted identifiers are interpolated, values are bound; approved=user, 2026-11-08; review=2027-05-08
            "c.relispartition, i.inhparent, pg_catalog.obj_description(c.oid, 'pg_class') "
            "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n "
            "ON n.oid = c.relnamespace "
            "LEFT JOIN pg_catalog.pg_inherits i ON i.inhrelid = c.oid "
            "AND c.relispartition AND i.inhseqno = 1 AND NOT i.inhdetachpending "
            f"WHERE n.nspname IN ({placeholders}) AND c.relkind IN ('r','p','v','m','f') "
            "ORDER BY c.oid LIMIT %s",
            (*config.namespaces, config.max_objects + 1),
        )
        rows = cursor.fetchall()
        if len(rows) > config.max_objects:
            raise BudgetExhausted
        kinds = {
            "r": "table",
            "p": "partitioned_table",
            "v": "view",
            "m": "materialized_view",
            "f": "foreign_table",
        }
        visible_oids = {oid for oid, *_ in rows}
        for oid, schema, name, relkind, reltuples, is_partition, parent_oid, comment in rows:
            attributes: dict[str, str | int | float | bool | None] = {}
            if relkind in {"r", "m"} and reltuples is not None and reltuples >= 0:
                attributes["row_count_estimate"] = float(reltuples)
                attributes["row_count_method"] = "pg_class.reltuples"
            if is_partition:
                attributes["is_partition"] = True
            tables["objects.jsonl"].append(
                Object(
                    id=stable_id(config.source_id, "object", str(oid)),
                    native_id=str(oid),
                    kind=kinds[relkind],
                    name=name,
                    namespace=schema,
                    parent_id=stable_id(config.source_id, "object", str(parent_oid))
                    if is_partition and parent_oid in visible_oids
                    else None,
                    attributes=attributes,
                    description=_description(comment, config),
                    **common,
                )
            )
        view_oids = [oid for oid, _, _, relkind, *_ in rows if relkind in {"v", "m"}]
        if view_oids:
            budget.charge()
            cursor.execute(
                "SELECT c.oid, pg_catalog.pg_get_viewdef(c.oid, true) "
                "FROM pg_catalog.pg_class c WHERE c.oid = ANY(%s) ORDER BY c.oid",
                (view_oids,),
            )
            definitions = {oid: definition for oid, definition in cursor.fetchall()}
            for item in tables["objects.jsonl"]:
                if (
                    isinstance(item, Object)
                    and int(item.native_id) in definitions
                    and definitions[int(item.native_id)]
                ):
                    item.attributes["view_definition_sha256"] = hashlib.sha256(
                        definitions[int(item.native_id)].encode("utf-8")
                    ).hexdigest()
                    item.attributes["view_definition_available"] = True
        field_rows: list[Any] = []
        if rows:
            budget.charge()
            cursor.execute(
                "SELECT a.attrelid, a.attnum, a.attname, "
                "pg_catalog.format_type(a.atttypid,a.atttypmod), a.attnotnull, "
                "pg_catalog.col_description(a.attrelid, a.attnum) "
                "FROM pg_catalog.pg_attribute a WHERE a.attrelid = ANY(%s) "
                "AND a.attnum > 0 AND NOT a.attisdropped "
                "ORDER BY a.attrelid,a.attnum LIMIT %s",
                ([row[0] for row in rows], config.max_fields + 1),
            )
            field_rows = cursor.fetchall()
            if len(field_rows) > config.max_fields:
                raise BudgetExhausted
            for oid, attnum, name, data_type, not_null, comment in field_rows:
                native = f"{oid}:{attnum}"
                tables["fields.jsonl"].append(
                    Field(
                        id=stable_id(config.source_id, "field", native),
                        object_id=stable_id(config.source_id, "object", str(oid)),
                        native_id=native,
                        name=name,
                        data_type=data_type,
                        nullable=not not_null,
                        ordinal=attnum,
                        description=_description(comment, config),
                        **common,
                    )
                )
            _profile_postgresql(cursor, config, budget, tables, rows, field_rows, common)
            _tokenize_postgresql(cursor, config, budget, tables, rows, field_rows, common)
            budget.charge()
            cursor.execute(
                "SELECT oid, conrelid, confrelid, contype, conkey, confkey "
                "FROM pg_catalog.pg_constraint WHERE conrelid = ANY(%s) "
                "AND contype IN ('p','u','f') ORDER BY oid LIMIT %s",
                ([row[0] for row in rows], config.max_fields + 1),
            )
            constraint_rows = cursor.fetchall()
            if len(constraint_rows) > config.max_fields:
                raise BudgetExhausted
            discovered_oids = {row[0] for row in rows}
            for oid, owner_oid, target_oid, kind, keys, target_keys in constraint_rows:
                if kind == "f" and target_oid not in discovered_oids:
                    tables["diagnostics.jsonl"].append(
                        _diagnostic(
                            config,
                            common,
                            str(oid),
                            "OUT_OF_SCOPE_REFERENCE",
                            "foreign key target outside approved scan scope",
                        )
                    )
                    continue
                tables["constraints.jsonl"].append(
                    Record.model_validate(
                        {
                            "record_type": "constraint",
                            "id": stable_id(config.source_id, "constraint", str(oid)),
                            "native_id": str(oid),
                            "kind": {"p": "primary", "u": "unique", "f": "foreign"}[kind],
                            "object_id": stable_id(config.source_id, "object", str(owner_oid)),
                            "target_object_id": (
                                stable_id(config.source_id, "object", str(target_oid))
                                if kind == "f"
                                else None
                            ),
                            "field_ordinals": keys or [],
                            "target_field_ordinals": target_keys or [],
                            **common,
                        }
                    )
                )
        listed = {schema for _, schema, *_ in rows}
        empty = [item for item in config.namespaces if item not in listed]
        if empty:
            # Only when a schema listed nothing: an empty schema is legitimate, a missing
            # one is a typo. pg_namespace lists every schema whatever the account's privileges.
            budget.charge()
            cursor.execute(
                "SELECT nspname FROM pg_catalog.pg_namespace "  # noqa: S608 # C5: reason=only placeholder tokens or quoted identifiers are interpolated, values are bound; approved=user, 2026-11-08; review=2027-05-08
                f"WHERE nspname IN ({','.join(['%s'] * len(empty))})",
                tuple(empty),
            )
            existing = {row[0] for row in cursor.fetchall()}
            _refuse_missing_namespaces(
                config, [item for item in empty if item not in existing], "schema"
            )
        # Last, and stopped at the budget limit instead of raising, so the optional column reads
        # cost profiles and never the catalogue.
        _profile_formats_postgresql(
            connection, cursor, config, budget, tables, rows, field_rows, common
        )
        connection.rollback()
    finally:
        cursor.close()
    return tables


def discover_clickhouse(
    client: Any, config: SourceConfig, budget: Budget
) -> dict[str, list[Record]]:
    common = _common(config)
    tables: dict[str, list[Record]] = defaultdict(list)
    listed: set[str] = set()
    format_work: list[
        tuple[str, dict[str, str], set[str], dict[str, list[tuple[str, str, str]]]]
    ] = []
    for database in config.namespaces:
        budget.charge()
        rows = client.query(
            "SELECT name, toString(uuid), engine, partition_key, sorting_key, "
            "primary_key, dependencies_database, dependencies_table, total_rows, comment "
            "FROM system.tables "
            "WHERE database = {db:String} ORDER BY name LIMIT {lim:UInt64}",
            parameters={"db": database, "lim": config.max_objects + 1},
            settings={"max_execution_time": config.read_timeout_seconds},
        ).result_set
        if len(rows) > config.max_objects:
            raise BudgetExhausted
        if rows:
            listed.add(database)
        identities: dict[str, str] = {}
        views: set[str] = set()
        row_counts: dict[str, int] = {}
        key_expressions: dict[str, dict[str, str]] = {}
        for (
            name,
            uuid,
            engine,
            partition_key,
            sorting_key,
            primary_key,
            dependency_dbs,
            dependency_tables,
            total_rows,
            comment,
        ) in rows:
            if not uuid or uuid == "00000000-0000-0000-0000-000000000000":
                tables["diagnostics.jsonl"].append(
                    _diagnostic(
                        config,
                        common,
                        f"{database}:{name}",
                        "IDENTITY_UNAVAILABLE",
                        "table UUID unavailable",
                    )
                )
                continue
            identities[name] = uuid
            if engine in {"View", "LiveView"}:
                views.add(name)
            if total_rows is not None:
                row_counts[name] = int(total_rows)
            attributes: dict[str, str | int | float | bool | None] = {
                "engine": engine,
                "is_view": engine in {"View", "MaterializedView", "LiveView"},
                "dependency_count": len(dependency_tables),
            }
            if total_rows is not None:
                attributes["row_count_estimate"] = int(total_rows)
                attributes["row_count_method"] = "system.tables.total_rows"
            key_expressions[name] = {"primary_key": primary_key, "sorting_key": sorting_key}
            for key, expression in (
                ("partition", partition_key),
                ("sort", sorting_key),
                ("primary", primary_key),
            ):
                if expression:
                    attributes[f"{key}_key_sha256"] = hashlib.sha256(
                        expression.encode("utf-8")
                    ).hexdigest()
            if dependency_tables:
                attributes["dependencies"] = ",".join(
                    sorted(
                        f"{db}.{table}"
                        for db, table in zip(dependency_dbs, dependency_tables, strict=True)
                    )
                )
            tables["objects.jsonl"].append(
                Object(
                    id=stable_id(config.source_id, "object", uuid),
                    native_id=uuid,
                    kind="materialized_view"
                    if engine == "MaterializedView"
                    else "view"
                    if engine == "View"
                    else "table",
                    name=name,
                    namespace=database,
                    attributes=attributes,
                    description=_description(comment, config),
                    **common,
                )
            )
        budget.charge()
        # `data_uncompressed_bytes` rides along on the query that is already made, so profiling
        # ClickHouse costs no additional read. ClickHouse keeps no per-column null or distinct
        # statistics by default, so average width is honestly all this yields.
        columns = client.query(
            "SELECT table,name,type,position,data_uncompressed_bytes,comment FROM system.columns "
            "WHERE database = {db:String} ORDER BY table,position LIMIT {lim:UInt64}",
            parameters={"db": database, "lim": config.max_fields + 1},
            settings={"max_execution_time": config.read_timeout_seconds},
        ).result_set
        if len(columns) > config.max_fields:
            raise BudgetExhausted
        column_names: dict[str, set[str]] = defaultdict(set)
        probe_columns: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
        for table, name, data_type, position, uncompressed, comment in columns:
            if table not in identities:
                continue
            native = f"{identities[table]}:{position}"
            field_id = stable_id(config.source_id, "field", native)
            object_id = stable_id(config.source_id, "object", identities[table])
            tables["fields.jsonl"].append(
                Field(
                    id=field_id,
                    object_id=object_id,
                    native_id=native,
                    name=name,
                    data_type=data_type,
                    ordinal=position,
                    description=_description(comment, config),
                    **common,
                )
            )
            column_names[table].add(name)
            probe_columns[table].append((field_id, name, str(data_type)))
            width = _average_width(uncompressed, row_counts.get(table))
            if width is not None:
                tables["profiles.jsonl"].append(
                    Profile(
                        id=stable_id(
                            config.source_id, "profile", f"{field_id}:source_statistic"
                        ),
                        field_id=field_id,
                        object_id=object_id,
                        method="source_statistic",
                        average_width_bytes=width,
                        **common,
                    )
                )
        _tokenize_clickhouse(
            client, config, budget, database, identities, views, probe_columns, tables, common
        )
        format_work.append((database, identities, views, probe_columns))
        if config.cardinality_probe:
            for table, probed in probe_columns.items():
                _probe_clickhouse(
                    client,
                    config,
                    budget,
                    common,
                    database,
                    table,
                    probed,
                    stable_id(config.source_id, "object", identities[table]),
                    tables["profiles.jsonl"],
                    tables["diagnostics.jsonl"],
                )
        # A sorting or primary key that is one plain column is a weak parent claim (DEC-2): schema
        # metadata, not values, and not a uniqueness guarantee. `index` is the existing kind that
        # carries column names; the native ID suffix says it is a key claim, not a skip index.
        # ponytail: expressions and multi-column keys are skipped; upgrade is parsing them.
        for table, expressions in key_expressions.items():
            for claim, expression in expressions.items():
                column = (expression or "").strip()
                # ClickHouse 26.8 reports `ORDER BY (companyID)` as "(companyID)" (live run
                # 2026-10-17); one outer pair is not part of the name. "(a, b)" still fails below.
                if column.startswith("(") and column.endswith(")"):
                    column = column[1:-1].strip()
                column = column.strip("`")
                if not _PLAIN_COLUMN.fullmatch(column) or column not in column_names[table]:
                    continue
                if claim == "sorting_key" and expressions["primary_key"] == expression:
                    continue
                native = f"{identities[table]}:{claim}"
                tables["constraints.jsonl"].append(
                    Record.model_validate(
                        {
                            "record_type": "constraint",
                            "id": stable_id(config.source_id, "constraint", native),
                            "native_id": native,
                            "kind": "index",
                            "object_id": stable_id(config.source_id, "object", identities[table]),
                            "field_names": [column],
                            **common,
                        }
                    )
                )
    empty = [item for item in config.namespaces if item not in listed]
    if empty:
        # Only when a database listed no tables, so a normal scan pays nothing for this.
        # system.databases shows only databases this account may see, so "missing" here also
        # covers a database the account has no grant on.
        budget.charge()
        existing = {
            row[0]
            for row in client.query(
                "SELECT name FROM system.databases WHERE has({names:Array(String)}, name)",
                parameters={"names": empty},
                settings={"max_execution_time": config.read_timeout_seconds},
            ).result_set
        }
        _refuse_missing_namespaces(
            config, [item for item in empty if item not in existing], "database"
        )
    # Last, and stopped at the budget limit instead of raising, so the optional column reads cost
    # profiles and never the catalogue.
    skipped = 0
    for database, identities, views, probe_columns in format_work:
        skipped += _profile_formats_clickhouse(
            client, config, budget, database, identities, views, probe_columns, tables, common
        )
    if skipped:
        tables["diagnostics.jsonl"].append(
            _format_skipped(config, common, "clickhouse", skipped, budget)
        )
    return tables


_PLAIN_COLUMN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NESTED_TYPE = re.compile(r"^(Nullable\(|LowCardinality\()*(Array|Map|Tuple|Nested|Object|JSON)")


def _tokenize_clickhouse(
    client: Any,
    config: SourceConfig,
    budget: Budget,
    database: str,
    identities: dict[str, str],
    views: set[str],
    columns: dict[str, list[tuple[str, str, str]]],
    tables: dict[str, list[Record]],
    common: dict[str, Any],
) -> None:
    """Keyed tokens for ClickHouse, as `_tokenize_postgresql` makes them (F7).

    Off unless sampling and a token key are both configured. One bounded read per table, of the
    first `sample_rows` rows, naming the plain, non-nested columns from the server's own catalog;
    a view is not read, since reading one runs its query, but a materialized view holds stored
    rows and is read, as on PostgreSQL. A read the server refuses (an EPHEMERAL column, a missing
    backing file) costs that table its tokens and leaves a code-only diagnostic, as the probe
    does, rather than ending the scan. `columns` holds (field_id, name, type).
    ponytail: a table larger than `sample_rows` is never read whole, so a zero against it never
    drops (P6-2); a full-column sketch read is the upgrade path (LEAD_REVIEW F5 option C).
    """
    if config.sample_rows <= 0 or not config.value_token_key_env:
        return
    key = key_from_env(config.value_token_key_env, purpose="cross-source value tokenisation")
    for table, entries in sorted(columns.items()):
        chosen = [
            item
            for item in entries
            if _PLAIN_COLUMN.fullmatch(item[1]) and not _NESTED_TYPE.match(item[2])
        ]
        if table in views or not chosen or not _PLAIN_COLUMN.fullmatch(table):
            continue
        budget.charge()
        selected = ",".join(_quoted(name) for _, name, _ in chosen)
        try:
            rows = client.query(
                # Identifiers come from the server's catalog and pass `_quoted`; the row limit is
                # a bound parameter. No caller input reaches this statement.
                f"SELECT {selected} FROM {_quoted(database)}.{_quoted(table)} "  # noqa: S608 # C5: reason=identifiers pass _quoted and limits are bound parameters; approved=user, 2026-11-08; review=2027-05-08
                "LIMIT {lim:UInt64}",
                parameters={"lim": config.sample_rows},
                settings={"max_execution_time": config.read_timeout_seconds},
            ).result_set
        except Exception:
            tables["diagnostics.jsonl"].append(
                _diagnostic(
                    config,
                    common,
                    f"{database}:{table}:sample",
                    "STATS_UNAVAILABLE",
                    "value sample unavailable",
                )
            )
            continue
        for position, (field_id, _, _) in enumerate(chosen):
            digests, observed, distinct, truncated = _value_tokens(
                [row[position] for row in rows], key=key, limit=config.max_value_tokens
            )
            if not digests:
                continue
            tables["tokens.jsonl"].append(
                ValueTokens(
                    id=stable_id(config.source_id, "tokens", field_id),
                    field_id=field_id,
                    object_id=stable_id(config.source_id, "object", identities[table]),
                    key_id=config.value_token_key_id or "",
                    sample_count=len(rows),
                    sample_method=f"first_rows_limit:{config.sample_rows}",
                    observed_count=observed,
                    distinct_count=distinct,
                    truncated=truncated,
                    complete=len(rows) < config.sample_rows,
                    tokens=digests,
                    **common,
                )
            )


def _probe_profile(
    config: SourceConfig,
    common: dict[str, Any],
    field_id: str,
    object_id: str,
    rows: int,
    distinct: int,
) -> Profile | None:
    """A distinct count over the rows the probe read, as a uniqueness measure; counts only.

    ponytail: the rows are the first `cardinality_probe_max_rows`, so on a larger table this is
    uniqueness within that prefix, a lower bound on duplicates, not a table-wide measurement
    (`sample_count` says how many rows). Upgrade path: a table-wide `uniqExact` behind its own
    budget.
    """
    if rows <= 0 or distinct < 0:
        return None
    return Profile(
        id=stable_id(config.source_id, "profile", f"{field_id}:cardinality_probe"),
        field_id=field_id,
        object_id=object_id,
        method="sample",
        sample_count=rows,
        distinct_estimate=float(distinct),
        uniqueness=min(distinct / rows, 1.0),
        **common,
    )


def _probe_clickhouse(
    client: Any,
    config: SourceConfig,
    budget: Budget,
    common: dict[str, Any],
    database: str,
    table: str,
    columns: list[tuple[str, str, str]],
    object_id: str,
    out: list[Record],
    diagnostics: list[Record],
) -> None:
    """One query: `count()` and `uniq()` of up to N plain columns over a bounded row prefix.

    `columns` holds (field_id, name, type). Identifiers come from the server's own catalog and
    pass `_quoted`; the row bound is a bound parameter. Counted through a `LIMIT` subquery on one
    thread, the count covers exactly the cap and the server reads about one block more (F9,
    measured on ClickHouse 26.8: about 2x, against 7.8x for `max_rows_to_read` with `break`).
    """
    chosen = [
        item
        for item in columns
        if _PLAIN_COLUMN.fullmatch(item[1]) and not _NESTED_TYPE.match(item[2])
    ][: config.cardinality_probe_max_columns]
    if not chosen or budget.used >= budget.max_queries:
        return
    budget.charge()
    select = ", ".join(["count()", *(f"uniq({_quoted(name)})" for _, name, _ in chosen)])
    inner = ",".join(_quoted(name) for _, name, _ in chosen)
    try:
        result = client.query(
            f"SELECT {select} FROM (SELECT {inner} "  # noqa: S608 # C5: reason=identifiers pass _quoted and limits are bound parameters; approved=user, 2026-11-08; review=2027-05-08
            f"FROM {_quoted(database)}.{_quoted(table)} LIMIT {{lim:UInt64}})",
            parameters={"lim": config.cardinality_probe_max_rows},
            settings={"max_execution_time": config.read_timeout_seconds, "max_threads": 1},
        ).result_set
        row = result[0]
        rows = int(row[0])
    except Exception:
        diagnostics.append(
            _diagnostic(
                config,
                common,
                f"{database}:{table}:probe",
                "STATS_UNAVAILABLE",
                "cardinality probe unavailable",
            )
        )
        return
    for (field_id, _, _), distinct in zip(chosen, row[1:], strict=True):
        profile = _probe_profile(config, common, field_id, object_id, rows, int(distinct))
        if profile is not None:
            out.append(profile)


def _average_width(uncompressed: Any, rows: int | None) -> int | None:
    """Average uncompressed bytes per row for one ClickHouse column.

    Returns nothing rather than zero when the table is empty or the size is unavailable, because
    a width of zero is a measurement and "no rows to measure" is not. ClickHouse keeps no
    per-column null or distinct statistics by default, so this is the only field-level number
    available without reading data.

    Zero bytes over a nonzero row count is also nothing. ClickHouse reports 0 per column for a
    table stored in Compact parts, which keep every column in one file; observed on 25.8.33.6 on
    2026-09-30 with a 1,000-row table whose only part was Compact. Rows cannot occupy no bytes,
    so a zero here means "not tracked", and publishing it would be a false measurement.
    """
    if uncompressed is None or not rows or rows <= 0 or int(uncompressed) <= 0:
        return None
    width = int(uncompressed) // rows
    return width if width >= 0 else None


_MONGO_PATH = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*")


def _probe_mongo(
    collection: Any,
    config: SourceConfig,
    budget: Budget,
    common: dict[str, Any],
    object_id: str,
    native: str,
    paths: dict[str, set[str]],
    out: list[Record],
    diagnostics: list[Record],
) -> None:
    """One aggregation: a bounded document prefix, then a distinct count per scalar path.

    Only counts leave the server (`$facet` of `$group` then `$count`); no value is returned. Paths
    that are arrays or sub-documents, or whose names are not plain, are skipped.
    """
    chosen = [
        path
        for path, types in sorted(paths.items())
        if _MONGO_PATH.fullmatch(path) and not types & {"list", "dict", "array", "object"}
    ][: config.cardinality_probe_max_columns]
    if not chosen or budget.used >= budget.max_queries:
        return
    budget.charge()
    facets: dict[str, Any] = {
        f"p{index}": [{"$group": {"_id": f"${path}"}}, {"$count": "n"}]
        for index, path in enumerate(chosen)
    }
    pipeline = [
        {"$limit": config.cardinality_probe_max_rows},
        {"$facet": {"rows": [{"$count": "n"}], **facets}},
    ]
    try:
        result = list(
            collection.aggregate(pipeline, maxTimeMS=config.read_timeout_seconds * 1000)
        )[0]
        rows = int(result["rows"][0]["n"]) if result["rows"] else 0
        counts = [
            int(result[f"p{index}"][0]["n"]) if result[f"p{index}"] else 0
            for index in range(len(chosen))
        ]
    except Exception:
        diagnostics.append(
            _diagnostic(
                config,
                common,
                f"{native}:probe",
                "STATS_UNAVAILABLE",
                "cardinality probe unavailable",
            )
        )
        return
    for path, distinct in zip(chosen, counts, strict=True):
        field_id = stable_id(config.source_id, "field", f"{native}:{path}")
        profile = _probe_profile(config, common, field_id, object_id, rows, distinct)
        if profile is not None:
            out.append(profile)


def _mongo_paths(
    document: dict[str, Any],
    prefix: str,
    depth: int,
    limit: int,
    array_limit: int,
    found: dict[str, set[str]],
    present: set[str],
    tokens: dict[str, set[str]] | None = None,
    observed: dict[str, int] | None = None,
    token_key: bytes | None = None,
    cut: set[str] | None = None,
    shapes: _Shapes | None = None,
) -> None:
    """Walk a sampled document for paths and types, and optionally key its scalar values.

    `cut` collects the array paths where `array_limit` left elements unread, so their value
    tokens are not marked complete.

    When tokenising, each scalar is keyed where it is met and only the digest is kept, so no
    buffer of raw source values ever exists, not even for the duration of one document.
    """
    if depth > limit:
        return
    for key, value in document.items():
        path = f"{prefix}.{key}" if prefix else key
        present.add(path)
        found[path].add(type(value).__name__)
        if isinstance(value, dict):
            _mongo_paths(
                value, path, depth + 1, limit, array_limit, found, present,
                tokens, observed, token_key, cut, shapes,
            )
        elif isinstance(value, list):
            if cut is not None and len(value) > array_limit:
                cut.add(path + "[]")
            for element in value[:array_limit]:
                found[path + "[]"].add(type(element).__name__)
                present.add(path + "[]")
                if isinstance(element, dict):
                    _mongo_paths(
                        element, path + "[]", depth + 1, limit, array_limit, found, present,
                        tokens, observed, token_key, cut, shapes,
                    )
                else:
                    if shapes is not None:
                        shapes.add(path + "[]", element)
                    if token_key is not None and tokens is not None and observed is not None:
                        _record_token(path + "[]", element, tokens, observed, token_key)
        else:
            if shapes is not None:
                shapes.add(path, value)
            if token_key is not None and tokens is not None and observed is not None:
                _record_token(path, value, tokens, observed, token_key)


def _record_token(
    path: str,
    value: Any,
    tokens: dict[str, set[str]],
    observed: dict[str, int],
    token_key: bytes,
) -> None:
    text = _token_text(value)
    if text is None:
        return
    observed[path] = observed.get(path, 0) + 1
    tokens.setdefault(path, set()).add(_digest(text, token_key))


def _mongo_schema_paths(
    schema: dict[str, Any],
    prefix: str,
    depth: int,
    limit: int,
    found: dict[str, set[str]],
    origins: dict[str, set[str]],
    required: set[str],
) -> None:
    if depth > limit:
        return
    required_here = set(schema.get("required", []))
    for key, definition in schema.get("properties", {}).items():
        if not isinstance(definition, dict):
            continue
        path = f"{prefix}.{key}" if prefix else key
        types = definition.get("bsonType", definition.get("type", "unknown"))
        found[path].update(types if isinstance(types, list) else [str(types)])
        origins[path].add("validator")
        if key in required_here:
            required.add(path)
        _mongo_schema_paths(definition, path, depth + 1, limit, found, origins, required)
        items = definition.get("items")
        if isinstance(items, dict):
            child = {"properties": items.get("properties", {})}
            _mongo_schema_paths(child, path + "[]", depth + 1, limit, found, origins, required)


def discover_mongodb(client: Any, config: SourceConfig, budget: Budget) -> dict[str, list[Record]]:
    common = _common(config)
    tables: dict[str, list[Record]] = defaultdict(list)
    total = 0
    sampled_bytes_total = 0
    listed: dict[str, int] = defaultdict(int)
    for database in config.namespaces:
        budget.charge()
        for info in client[database].list_collections():
            listed[database] += 1
            if str(info.get("name", "")).startswith("system."):
                # MongoDB reserves the `system.` prefix for its own collections: view
                # definitions, the profiler, time-series buckets. They hold no application data,
                # and sampling `system.views` would publish the shape of every view's pipeline.
                continue
            total += 1
            if total > config.max_objects:
                raise BudgetExhausted
            native = str(info.get("info", {}).get("uuid", ""))
            if not native:
                tables["diagnostics.jsonl"].append(
                    # A view is a saved query, not stored data, and has no UUID to give it a
                    # stable identity. Its own code keeps it out of the catalogue without
                    # marking the scan partial (user decision 2026-10-05).
                    _diagnostic(
                        config,
                        common,
                        f"{database}:{info['name']}",
                        "VIEW_NOT_CATALOGUED"
                        if info.get("type") == "view"
                        else "IDENTITY_UNAVAILABLE",
                        (
                            "MongoDB views have no collection UUID, so this view has no "
                            "stable identity and is left out; the collection it reads is "
                            "catalogued"
                            if info.get("type") == "view"
                            else "collection UUID unavailable"
                        ),
                    )
                )
                continue
            name = info["name"]
            object_id = stable_id(config.source_id, "object", native)
            options = info.get("options", {})
            item = Object(
                id=object_id,
                native_id=native,
                kind="collection",
                name=name,
                namespace=database,
                attributes={
                    "collection_type": info.get("type", "collection"),
                    "has_validator": bool(options.get("validator")),
                },
                **common,
            )
            tables["objects.jsonl"].append(item)
            paths: dict[str, set[str]] = defaultdict(set)
            origins: dict[str, set[str]] = defaultdict(set)
            required: set[str] = set()
            validator = options.get("validator", {})
            if isinstance(validator, dict) and isinstance(validator.get("$jsonSchema"), dict):
                _mongo_schema_paths(
                    validator["$jsonSchema"], "", 1, config.max_path_depth, paths, origins, required
                )
            budget.charge()
            for index in client[database][name].list_indexes():
                field_names = list(index["key"])
                tables["constraints.jsonl"].append(
                    Record.model_validate(
                        {
                            "record_type": "constraint",
                            "id": stable_id(config.source_id, "index", f"{native}:{index['name']}"),
                            "native_id": f"{native}:{index['name']}",
                            "kind": "unique" if index.get("unique") else "index",
                            "object_id": object_id,
                            "field_names": field_names,
                            **common,
                        }
                    )
                )
                for field_name in field_names:
                    if field_name != "$**" and not field_name.endswith(".$**"):
                        paths[field_name].add("unknown")
                        origins[field_name].add("index")
            sample_count = 0
            # Set when `max_sample_bytes` stopped the read, which leaves the sample incomplete
            # however few documents it holds.
            byte_cut = False
            array_cut: set[str] = set()
            present_counts: dict[str, int] = defaultdict(int)
            # Tokenising needs a key as well as a sample, and is off unless both are configured.
            token_key = (
                key_from_env(config.value_token_key_env, purpose="cross-source value tokenisation")
                if config.value_token_key_env
                else None
            )
            path_tokens: dict[str, set[str]] = {}
            path_observed: dict[str, int] = {}
            # Format profiles come from the same sample, so they cost no query (P7-5 option C).
            shapes = _Shapes(config.format_token_max_length) if config.format_profiles else None
            if config.sample_rows and sampled_bytes_total >= config.max_sample_bytes:
                # Said rather than skipped: without a sample a collection with no validator has
                # no fields at all, which would otherwise look like an empty collection.
                tables["diagnostics.jsonl"].append(
                    _diagnostic(
                        config,
                        common,
                        f"{native}:sample-bytes",
                        "SAMPLE_BYTE_LIMIT",
                        "not sampled: earlier collections used the source's max_sample_bytes",
                    )
                )
            elif config.sample_rows:
                # `sample_rows` is per collection, as it is per table for PostgreSQL. It was a
                # total for the whole source, so after one large collection every later one was
                # silently left unsampled. `max_sample_bytes` still bounds the whole source.
                budget.charge()
                cursor = client[database][name].find({}).limit(config.sample_rows)
                cursor.max_time_ms(config.read_timeout_seconds * 1000)
                try:
                    for document in cursor:
                        document_bytes = len(str(document).encode("utf-8"))
                        if sampled_bytes_total + document_bytes > config.max_sample_bytes:
                            tables["diagnostics.jsonl"].append(
                                _diagnostic(
                                    config,
                                    common,
                                    f"{native}:sample-bytes",
                                    "SAMPLE_BYTE_LIMIT",
                                    "type-only sample stopped at byte budget",
                                )
                            )
                            byte_cut = True
                            break
                        sampled_bytes_total += document_bytes
                        sample_count += 1
                        present: set[str] = set()
                        _mongo_paths(
                            document,
                            "",
                            1,
                            config.max_path_depth,
                            config.max_array_elements,
                            paths,
                            present,
                            path_tokens,
                            path_observed,
                            token_key,
                            array_cut,
                            shapes,
                        )
                        for path in present:
                            present_counts[path] += 1
                            origins[path].add("sample")
                finally:
                    if hasattr(cursor, "close"):
                        cursor.close()
            for path, types in sorted(paths.items()):
                if len(tables["fields.jsonl"]) >= config.max_fields:
                    raise BudgetExhausted
                field_native = f"{native}:{path}"
                tables["fields.jsonl"].append(
                    Field(
                        id=stable_id(config.source_id, "field", field_native),
                        object_id=object_id,
                        native_id=field_native,
                        name=path,
                        data_type="|".join(sorted(types - {"unknown"})) or "unknown",
                        nullable=(present_counts[path] < sample_count or "NoneType" in types)
                        if sample_count
                        else (False if path in required else None),
                        attributes={"field_origin": ",".join(sorted(origins[path]))},
                        **common,
                    )
                )
                if sample_count:
                    # MongoDB exposes no per-field statistics, so a profile here can only come
                    # from the sample that was already taken and paid for. Absence is the only
                    # measure a type-only sample yields: no value was read, so nothing about
                    # distinctness is known and none is claimed.
                    field_id = stable_id(config.source_id, "field", field_native)
                    shape = shapes.by_path.get(path) if shapes is not None else None
                    tables["profiles.jsonl"].append(
                        Profile(
                            id=stable_id(config.source_id, "profile", f"{field_id}:sample"),
                            field_id=field_id,
                            object_id=object_id,
                            method="sample",
                            sample_count=sample_count,
                            null_fraction=max(
                                0.0, min(1.0, 1.0 - present_counts[path] / sample_count)
                            ),
                            **(
                                _format_fields(
                                    shape.count,
                                    shape.length_min,
                                    shape.length_max,
                                    shape.length_sum / shape.count if shape.count else None,
                                    (
                                        shape.digits / shape.count if shape.count else None,
                                        shape.hex / shape.count if shape.count else None,
                                        shape.uuid / shape.count if shape.count else None,
                                        shape.alpha / shape.count if shape.count else None,
                                    ),
                                    {
                                        token: size / shape.count
                                        for token, size in shape.formats.most_common(
                                            config.format_profile_max_formats
                                        )
                                    },
                                    shapes_seen=len(shape.formats),
                                    # The numbers describe the sample: whole only if the read
                                    # reached the end of the collection (as the tokens' flag).
                                    complete=not byte_cut
                                    and sample_count < config.sample_rows
                                    and not any(
                                        path == item or path.startswith(item + ".")
                                        for item in array_cut
                                    ),
                                )
                                if shape is not None
                                else {}
                            ),
                            **common,
                        )
                    )
                    if (
                        shape is not None
                        and shape.only_integers
                        and shape.integer_range is not None
                        and "[]" not in path
                    ):
                        # A range from the documents read: whole only when the read reached the
                        # end of the collection, otherwise it may mark an overlap but never drop.
                        tables["ranges.jsonl"].append(
                            ValueRanges(
                                id=stable_id(config.source_id, "ranges", field_id),
                                field_id=field_id,
                                object_id=object_id,
                                minimum=shape.integer_range[0],
                                maximum=shape.integer_range[1],
                                complete=not byte_cut and sample_count < config.sample_rows,
                                **common,
                            )
                        )
                    if token_key is not None and path in path_tokens:
                        # The case the whole design exists for: a MongoDB identifier compared
                        # against a relational key. Same keying and same normalisation as the
                        # relational side, so the two token sets are comparable.
                        #
                        # The `token_key is not None` test is deliberate belt and braces, not a
                        # load-bearing branch: `_mongo_paths` is given no key when tokenising is
                        # off, so `path_tokens` is empty anyway. Mutation testing confirmed that
                        # removing this test alone changes no behaviour. It stays because it also
                        # keeps an empty `key_id` from ever reaching `ValueTokens`, which requires
                        # a non-empty one.
                        digests, observed, distinct, truncated = _capped_tokens(
                            path_tokens[path],
                            path_observed.get(path, 0),
                            config.max_value_tokens,
                        )
                        if digests:
                            tables["tokens.jsonl"].append(
                                ValueTokens(
                                    id=stable_id(config.source_id, "tokens", field_id),
                                    field_id=field_id,
                                    object_id=object_id,
                                    key_id=config.value_token_key_id or "",
                                    sample_count=sample_count,
                                    sample_method=f"first_documents_limit:{config.sample_rows}",
                                    observed_count=observed,
                                    distinct_count=distinct,
                                    truncated=truncated,
                                    complete=not byte_cut
                                    and sample_count < config.sample_rows
                                    and not any(
                                        path == item or path.startswith(item + ".")
                                        for item in array_cut
                                    ),
                                    tokens=digests,
                                    **common,
                                )
                            )
            if config.cardinality_probe and info.get("type", "collection") == "collection":
                _probe_mongo(
                    client[database][name],
                    config,
                    budget,
                    common,
                    object_id,
                    native,
                    paths,
                    tables["profiles.jsonl"],
                    tables["diagnostics.jsonl"],
                )
            if info.get("type", "collection") == "collection" and budget.used < budget.max_queries:
                budget.charge()
                try:
                    estimate = client[database][name].estimated_document_count(
                        maxTimeMS=config.read_timeout_seconds * 1000
                    )
                except Exception:
                    tables["diagnostics.jsonl"].append(
                        _diagnostic(
                            config,
                            common,
                            f"{native}:row-count",
                            "STATS_UNAVAILABLE",
                            "collection count estimate unavailable",
                        )
                    )
                else:
                    item.attributes["row_count_estimate"] = int(estimate)
                    item.attributes["row_count_method"] = "estimated_document_count"
    # MongoDB has no empty database: one with no collections does not exist, so a namespace that
    # listed nothing is a typo or a database this account cannot read. No extra query needed.
    _refuse_missing_namespaces(
        config, [item for item in config.namespaces if not listed[item]], "database"
    )
    return tables


# Diagnostics that explain something deliberately left out rather than something the scan could
# not read; they are published and shown, but do not make a bundle partial.
INFORMATIONAL_DIAGNOSTICS = frozenset({"VIEW_NOT_CATALOGUED"})


def _require_loopback(hosts: list[str]) -> None:
    """Refuse plaintext unless every host is a loopback IP literal.

    An IP literal, not a name: `localhost` is whatever the resolver says it is, and the point of
    the rule is that nothing crosses a network unencrypted. The same rule already governs plain
    HTTP to a private model endpoint.
    """
    for host in hosts or [""]:
        if not is_loopback_literal(host):
            raise OperatorError(
                f"tls = false is allowed only for loopback IP literals such as 127.0.0.1 or ::1, "
                f"not {host or 'an unspecified host'!r}; enable TLS on the server instead"
            )


def _require_private(hosts: list[str]) -> None:
    """Refuse a database host that is, or resolves to, any address that is not private."""
    for host in hosts or [""]:
        if refusal := private_host_refusal(host):
            raise OperatorError(refusal)


def _certificate_failure(error: BaseException, config: SourceConfig) -> OperatorError | None:
    """Recognise a certificate that did not verify anywhere in an exception chain.

    The most likely first-run failure against servers with a private CA, and each driver reports
    it differently: pg8000 raises the `ssl` error itself, ClickHouse Connect wraps it, and
    PyMongo only carries its text. The message names the fix and repeats only the verifier's own
    reason, never connection details, which may include credentials.
    """
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ssl.SSLCertVerificationError) or (
            "CERTIFICATE_VERIFY_FAILED" in str(current)
        ):
            reason = getattr(current, "verify_message", None) or "certificate verify failed"
            hint = (
                "tls_ca_file is set, so check that it is the CA that issued this server's "
                "certificate"
                if config.tls_ca_file is not None
                else "if the server's certificate comes from a private CA, set tls_ca_file to "
                "that CA's PEM file"
            )
            return OperatorError(
                f"{config.kind} source {config.source_id!r}: the server's TLS certificate did "
                f"not verify ({reason}); {hint}, and that the host in the connection matches "
                "the certificate"
            )
        current = current.__cause__ or current.__context__
    return None


# URI options that would switch certificate or hostname verification off. A MongoDB URI is free
# text and PyMongo merges its options with the ones passed here, so without this check a URI
# could disable verification while the bundle still recorded `tls-verified`.
MONGO_INSECURE_OPTIONS = frozenset(
    {"tlsinsecure", "tlsallowinvalidcertificates", "tlsallowinvalidhostnames"}
)


def _refuse_mongo_tls_overrides(uri: str, config: SourceConfig) -> None:
    query = uri.split("?", 1)[1] if "?" in uri else ""
    options = {
        key.lower(): value.strip().lower()
        for key, value in parse_qsl(query.replace(";", "&"), keep_blank_values=True)
    }
    for key in sorted(MONGO_INSECURE_OPTIONS & set(options)):
        if options[key] != "false":
            raise OperatorError(
                f"the MongoDB URI sets {key}, which turns TLS verification off; remove it and "
                "use tls_ca_file for a private CA"
            )
    for key in ("tls", "ssl"):
        if key in options and options[key] != ("true" if config.tls else "false"):
            raise OperatorError(
                f"the MongoDB URI sets {key}={options[key]}, which contradicts tls = "
                f"{'true' if config.tls else 'false'} in the source configuration; remove it"
            )


# PostgreSQL SQLSTATE codes a login can fail with: invalid_password, and
# invalid_authorization_specification (unknown role, or no pg_hba.conf entry allows it).
POSTGRES_LOGIN_CODES = frozenset({"28P01", "28000"})
# ClickHouse AUTHENTICATION_FAILED, as ClickHouse Connect reports it in the message text.
CLICKHOUSE_LOGIN_FAILED = re.compile(r"\bcode: 516\b", re.IGNORECASE)
# MongoDB AuthenticationFailed.
MONGODB_LOGIN_FAILED = 18


def _connection_failure(error: BaseException, config: SourceConfig) -> OperatorError | None:
    """Recognise a refused login, a missing database or an unreachable server.

    Each driver reports these differently (observed on PostgreSQL 16, ClickHouse 25.8 and
    MongoDB 7): pg8000 carries the SQLSTATE in a dict or wraps the socket error, ClickHouse
    Connect wraps urllib3 and only carries the server code in its text, and PyMongo raises its
    own types with no socket error in the chain. Like the certificate message, these never
    repeat driver or server text, which can include host names and user names.
    """
    where = f"{config.kind} source {config.source_id!r}"
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        code = (
            current.args[0].get("C")
            if current.args and isinstance(current.args[0], dict)
            else None
        )
        name = type(current).__name__
        if (
            code in POSTGRES_LOGIN_CODES
            or (name == "DatabaseError" and CLICKHOUSE_LOGIN_FAILED.search(str(current)))
            or (
                name == "OperationFailure"
                and getattr(current, "code", None) == MONGODB_LOGIN_FAILED
            )
        ):
            return OperatorError(
                f"{where}: the server refused the login; check the user and password in "
                f"{config.connection_env} and that the account exists on that server"
            )
        if code == "3D000":
            return OperatorError(
                f"{where}: the database named in {config.connection_env} does not exist on the "
                "server"
            )
        reason = None
        if isinstance(current, socket.gaierror):
            reason = "the host name was not found"
        elif isinstance(current, ConnectionRefusedError):
            reason = "the connection was refused"
        elif isinstance(current, TimeoutError):
            reason = "the server did not answer in time"
        elif name == "ServerSelectionTimeoutError":
            reason = "no server answered in time"
        if reason is not None:
            return OperatorError(
                f"{where}: could not reach the server ({reason}); check the host and port in "
                f"{config.connection_env}, that this machine can reach them through any "
                "firewall, and read_timeout_seconds for a slow network"
            )
        current = current.__cause__ or current.__context__
    return None


def _refuse_missing_namespaces(
    config: SourceConfig, missing: list[str], what: str
) -> None:
    """Stop instead of publishing a complete-looking bundle for a namespace that is not there.

    A misspelt namespace otherwise reads nothing, writes an empty bundle and marks it complete.
    """
    if missing:
        raise OperatorError(
            f"{config.kind} source {config.source_id!r}: {what} "
            f"{', '.join(repr(item) for item in missing)} listed in namespaces does not exist or "
            "is not visible to this account; check the spelling and the account's grants. No "
            "bundle was written"
        )


def collect(config: SourceConfig, destination: Path) -> None:
    """Create a signed bundle; never accept connection values on the CLI."""
    try:
        _collect(config, destination)
    except OperatorError:
        raise
    except BudgetExhausted as error:
        # Still fails closed with no bundle, as before; only the report changes, from a
        # traceback to the limits an operator can raise.
        hint = (
            ": MongoDB charges one query per database, one per collection and one more per "
            "sampled collection"
            if config.kind == "mongodb"
            else ""
        )
        raise OperatorError(
            f"{config.kind} source {config.source_id!r}: the scan reached a configured limit "
            f"before finishing (max_queries={config.max_queries}, "
            f"max_objects={config.max_objects}, max_fields={config.max_fields}); no bundle was "
            f"written. Raise the limit this source needs{hint}"
        ) from error
    except Exception as error:
        converted = _certificate_failure(error, config) or _connection_failure(error, config)
        if converted is not None:
            raise converted from error
        raise


def _collect(config: SourceConfig, destination: Path) -> None:
    connection = json.loads(
        secret_from_env(config.connection_env, purpose="source connection settings")
    )
    if not isinstance(connection, dict):
        raise ValueError("connection reference must contain a JSON object")
    signing_key = key_from_env(config.signing_key_env, purpose="collector bundle signing")
    budget = Budget(config.max_queries)
    ca_file = str(config.tls_ca_file) if config.tls_ca_file is not None else None
    if config.kind == "postgresql":
        from pg8000 import dbapi

        allowed = {
            key: connection[key]
            for key in ("host", "port", "database", "user", "password")
            if key in connection
        }
        if not config.tls:
            _require_loopback([str(connection.get("host", ""))])
        host = str(connection.get("host", ""))
        address = private_address(host)
        pinned = None
        if address != host:
            # A name: connect to the address that was checked, so a second lookup cannot send
            # the driver elsewhere; pg8000 still verifies the certificate against the name in
            # `host` (review finding 5). An IP literal is never looked up again.
            pinned = socket.create_connection(
                (address, int(connection.get("port", PG_DEFAULT_PORT))),
                timeout=config.read_timeout_seconds,
            )
        try:
            client = dbapi.connect(
                **allowed,
                **({"sock": pinned} if pinned is not None else {}),
                ssl_context=ssl.create_default_context(cafile=ca_file) if config.tls else None,
                timeout=config.read_timeout_seconds,
            )
        except BaseException:
            if pinned is not None:
                pinned.close()
            raise
        try:
            data = discover_postgresql(client, config, budget)
        finally:
            client.close()
    elif config.kind == "clickhouse":
        import clickhouse_connect

        allowed = {
            key: connection[key]
            for key in ("host", "port", "username", "password")
            if key in connection
        }
        if not config.tls:
            _require_loopback([str(connection.get("host", ""))])
        _require_private([str(connection.get("host", ""))])
        tls_options: dict[str, Any] = {"secure": config.tls}
        if config.tls:
            tls_options["verify"] = True
            if ca_file is not None:
                tls_options["ca_cert"] = ca_file
        client = clickhouse_connect.get_client(**allowed, **tls_options)
        try:
            data = discover_clickhouse(client, config, budget)
        finally:
            client.close()
    else:
        from pymongo import MongoClient, uri_parser

        if set(connection) != {"uri"}:
            raise ValueError("MongoDB connection needs only uri")
        _refuse_mongo_tls_overrides(connection["uri"], config)
        mongo_options: dict[str, Any] = {"tls": config.tls}
        # Before parsing: parsing an SRV URI performs DNS lookups, which would hang or fail
        # with a driver error before this refusal could be reported.
        if not config.tls and connection["uri"].startswith("mongodb+srv://"):
            raise OperatorError("tls = false cannot be used with a mongodb+srv:// URI")
        parsed = uri_parser.parse_uri(connection["uri"], connect_timeout=None)
        members = [host for host, _ in parsed["nodelist"]]
        if config.tls:
            if ca_file is not None:
                mongo_options["tlsCAFile"] = ca_file
        else:
            _require_loopback(members)
        # shortcut: PyMongo looks the names up again and finds replica-set members itself, and it
        # cannot connect by address while still verifying the certificate against the name, so
        # the check cannot be pinned (review finding 5); IP literals in the URI close the gap.
        _require_private(members)
        client = MongoClient(
            connection["uri"],
            serverSelectionTimeoutMS=config.read_timeout_seconds * 1000,
            **mongo_options,
        )
        try:
            data = discover_mongodb(client, config, budget)
        finally:
            client.close()
    common = _common(config)
    data["sources.jsonl"] = [
        Source(
            id=config.source_id,
            kind=config.kind,
            name=config.source_id,
            # How the metadata travelled, so a plaintext loopback collection is visible in every
            # artifact downstream rather than only in the config that produced it.
            transport="tls-verified" if config.tls else "plaintext-loopback",
            **common,
        )
    ]
    write_artifact(
        destination,
        "collector-bundle",
        data,
        producer=version("dbig-collectors"),
        source_run_ids=[str(uuid4())],
        completeness=(
            "partial"
            if any(
                getattr(item, "code", None) not in INFORMATIONAL_DIAGNOSTICS
                for item in data["diagnostics.jsonl"]
            )
            else "complete"
        ),
        signing_key=signing_key,
        key_id=config.signing_key_id,
    )
