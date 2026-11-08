"""One-source discovery with bounded metadata reads and sealed publication."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import socket
import ssl
from collections import defaultdict
from decimal import Decimal
from importlib.metadata import version
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
    ValueTokens,
    utc_now,
)
from dbig_contracts.security import is_loopback_literal, secret_from_env

from .config import SourceConfig


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
    key = secret_from_env(
        config.value_token_key_env, purpose="cross-source value tokenisation"
    ).encode("utf-8")
    if len(key) < 32:
        raise ValueError("value token key must contain at least 32 bytes")
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
            f"SELECT {selected} FROM {_quoted(schema)}.{_quoted(table)} "  # noqa: S608
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
        "SELECT s.schemaname, s.tablename, s.attname, s.null_frac, s.n_distinct, "  # noqa: S608
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
            "SELECT c.oid, n.nspname, c.relname, c.relkind, c.reltuples, "  # noqa: S608
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
                        Record.model_validate(
                            {
                                "record_type": "diagnostic",
                                "id": stable_id(config.source_id, "diagnostic", str(oid)),
                                "code": "OUT_OF_SCOPE_REFERENCE",
                                "message": "foreign key target outside approved scan scope",
                                **common,
                            }
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
                "SELECT nspname FROM pg_catalog.pg_namespace "  # noqa: S608
                f"WHERE nspname IN ({','.join(['%s'] * len(empty))})",
                tuple(empty),
            )
            existing = {row[0] for row in cursor.fetchall()}
            _refuse_missing_namespaces(
                config, [item for item in empty if item not in existing], "schema"
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
        row_counts: dict[str, int] = {}
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
                    Record.model_validate(
                        {
                            "record_type": "diagnostic",
                            "id": stable_id(config.source_id, "diagnostic", f"{database}:{name}"),
                            "code": "IDENTITY_UNAVAILABLE",
                            "message": "table UUID unavailable",
                            **common,
                        }
                    )
                )
                continue
            identities[name] = uuid
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
    return tables


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
) -> None:
    """Walk a sampled document for paths and types, and optionally key its scalar values.

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
                tokens, observed, token_key,
            )
        elif isinstance(value, list):
            for element in value[:array_limit]:
                found[path + "[]"].add(type(element).__name__)
                present.add(path + "[]")
                if isinstance(element, dict):
                    _mongo_paths(
                        element, path + "[]", depth + 1, limit, array_limit, found, present,
                        tokens, observed, token_key,
                    )
                elif token_key is not None and tokens is not None and observed is not None:
                    _record_token(path + "[]", element, tokens, observed, token_key)
        elif token_key is not None and tokens is not None and observed is not None:
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
                    Record.model_validate(
                        {
                            "record_type": "diagnostic",
                            "id": stable_id(
                                config.source_id, "diagnostic", f"{database}:{info['name']}"
                            ),
                            # A view is a saved query, not stored data, and has no UUID to give it a
                            # stable identity. Its own code keeps it out of the catalogue without
                            # marking the scan partial (user decision 2026-10-05).
                            "code": (
                                "VIEW_NOT_CATALOGUED"
                                if info.get("type") == "view"
                                else "IDENTITY_UNAVAILABLE"
                            ),
                            "message": (
                                "MongoDB views have no collection UUID, so this view has no "
                                "stable identity and is left out; the collection it reads is "
                                "catalogued"
                                if info.get("type") == "view"
                                else "collection UUID unavailable"
                            ),
                            **common,
                        }
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
            present_counts: dict[str, int] = defaultdict(int)
            # Tokenising needs a key as well as a sample, and is off unless both are configured.
            token_key = (
                secret_from_env(
                    config.value_token_key_env, purpose="cross-source value tokenisation"
                ).encode("utf-8")
                if config.value_token_key_env
                else None
            )
            if token_key is not None and len(token_key) < 32:
                raise ValueError("value token key must contain at least 32 bytes")
            path_tokens: dict[str, set[str]] = {}
            path_observed: dict[str, int] = {}
            if config.sample_rows and sampled_bytes_total >= config.max_sample_bytes:
                # Said rather than skipped: without a sample a collection with no validator has
                # no fields at all, which would otherwise look like an empty collection.
                tables["diagnostics.jsonl"].append(
                    Record.model_validate(
                        {
                            "record_type": "diagnostic",
                            "id": stable_id(
                                config.source_id, "diagnostic", f"{native}:sample-bytes"
                            ),
                            "code": "SAMPLE_BYTE_LIMIT",
                            "message": "not sampled: earlier collections used the source's "
                            "max_sample_bytes",
                            **common,
                        }
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
                                Record.model_validate(
                                    {
                                        "record_type": "diagnostic",
                                        "id": stable_id(
                                            config.source_id, "diagnostic", f"{native}:sample-bytes"
                                        ),
                                        "code": "SAMPLE_BYTE_LIMIT",
                                        "message": "type-only sample stopped at byte budget",
                                        **common,
                                    }
                                )
                            )
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
                                    tokens=digests,
                                    **common,
                                )
                            )
            if info.get("type", "collection") == "collection" and budget.used < budget.max_queries:
                budget.charge()
                try:
                    estimate = client[database][name].estimated_document_count(
                        maxTimeMS=config.read_timeout_seconds * 1000
                    )
                except Exception:
                    tables["diagnostics.jsonl"].append(
                        Record.model_validate(
                            {
                                "record_type": "diagnostic",
                                "id": stable_id(
                                    config.source_id, "diagnostic", f"{native}:row-count"
                                ),
                                "code": "STATS_UNAVAILABLE",
                                "message": "collection count estimate unavailable",
                                **common,
                            }
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
    signing_key = secret_from_env(
        config.signing_key_env, purpose="collector bundle signing"
    ).encode("utf-8")
    if len(signing_key) < 32:
        raise ValueError("signing key must contain at least 32 bytes")
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
        client = dbapi.connect(
            **allowed,
            ssl_context=ssl.create_default_context(cafile=ca_file) if config.tls else None,
            timeout=config.read_timeout_seconds,
        )
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
        if config.tls:
            if ca_file is not None:
                mongo_options["tlsCAFile"] = ca_file
        else:
            # Before parsing: parsing an SRV URI performs DNS lookups, which would hang or fail
            # with a driver error before this refusal could be reported.
            if connection["uri"].startswith("mongodb+srv://"):
                raise OperatorError("tls = false cannot be used with a mongodb+srv:// URI")
            parsed = uri_parser.parse_uri(connection["uri"], connect_timeout=None)
            _require_loopback([host for host, _ in parsed["nodelist"]])
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
