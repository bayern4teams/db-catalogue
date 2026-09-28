"""One-source discovery with bounded metadata reads and sealed publication."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import ssl
from collections import defaultdict
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path
from typing import Any
from uuid import uuid4

from dbig_contracts.artifact import write_artifact
from dbig_contracts.ids import stable_id
from dbig_contracts.models import (
    Field,
    Object,
    Profile,
    Record,
    Source,
    ValueTokens,
    utc_now,
)
from dbig_contracts.security import secret_from_env

from .config import SourceConfig


class Budget:
    def __init__(self, max_queries: int) -> None:
        self.max_queries = max_queries
        self.used = 0

    def charge(self) -> None:
        if self.used >= self.max_queries:
            raise RuntimeError("BUDGET_EXHAUSTED")
        self.used += 1


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
    digests: dict[str, None] = {}
    observed = 0
    for value in values:
        text = _token_text(value)
        if text is None:
            continue
        observed += 1
        digests[hmac.new(key, text.encode("utf-8"), hashlib.sha256).hexdigest()] = None
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
        raise RuntimeError("BUDGET_EXHAUSTED")
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
            "c.relispartition, i.inhparent "
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
            raise RuntimeError("BUDGET_EXHAUSTED")
        kinds = {
            "r": "table",
            "p": "partitioned_table",
            "v": "view",
            "m": "materialized_view",
            "f": "foreign_table",
        }
        visible_oids = {oid for oid, *_ in rows}
        for oid, schema, name, relkind, reltuples, is_partition, parent_oid in rows:
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
                "pg_catalog.format_type(a.atttypid,a.atttypmod), a.attnotnull "
                "FROM pg_catalog.pg_attribute a WHERE a.attrelid = ANY(%s) "
                "AND a.attnum > 0 AND NOT a.attisdropped "
                "ORDER BY a.attrelid,a.attnum LIMIT %s",
                ([row[0] for row in rows], config.max_fields + 1),
            )
            field_rows = cursor.fetchall()
            if len(field_rows) > config.max_fields:
                raise RuntimeError("BUDGET_EXHAUSTED")
            for oid, attnum, name, data_type, not_null in field_rows:
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
                raise RuntimeError("BUDGET_EXHAUSTED")
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
        connection.rollback()
    finally:
        cursor.close()
    return tables


def discover_clickhouse(
    client: Any, config: SourceConfig, budget: Budget
) -> dict[str, list[Record]]:
    common = _common(config)
    tables: dict[str, list[Record]] = defaultdict(list)
    for database in config.namespaces:
        budget.charge()
        rows = client.query(
            "SELECT name, toString(uuid), engine, partition_key, sorting_key, "
            "primary_key, dependencies_database, dependencies_table, total_rows "
            "FROM system.tables "
            "WHERE database = {db:String} ORDER BY name LIMIT {lim:UInt64}",
            parameters={"db": database, "lim": config.max_objects + 1},
            settings={"max_execution_time": config.read_timeout_seconds},
        ).result_set
        if len(rows) > config.max_objects:
            raise RuntimeError("BUDGET_EXHAUSTED")
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
                    **common,
                )
            )
        budget.charge()
        # `data_uncompressed_bytes` rides along on the query that is already made, so profiling
        # ClickHouse costs no additional read. ClickHouse keeps no per-column null or distinct
        # statistics by default, so average width is honestly all this yields.
        columns = client.query(
            "SELECT table,name,type,position,data_uncompressed_bytes FROM system.columns "
            "WHERE database = {db:String} ORDER BY table,position LIMIT {lim:UInt64}",
            parameters={"db": database, "lim": config.max_fields + 1},
            settings={"max_execution_time": config.read_timeout_seconds},
        ).result_set
        if len(columns) > config.max_fields:
            raise RuntimeError("BUDGET_EXHAUSTED")
        for table, name, data_type, position, uncompressed in columns:
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
    return tables


def _average_width(uncompressed: Any, rows: int | None) -> int | None:
    """Average uncompressed bytes per row for one ClickHouse column.

    Returns nothing rather than zero when the table is empty or the size is unavailable, because
    a width of zero is a measurement and "no rows to measure" is not. ClickHouse keeps no
    per-column null or distinct statistics by default, so this is the only field-level number
    available without reading data.
    """
    if uncompressed is None or not rows or rows <= 0:
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
) -> None:
    if depth > limit:
        return
    for key, value in document.items():
        path = f"{prefix}.{key}" if prefix else key
        present.add(path)
        found[path].add(type(value).__name__)
        if isinstance(value, dict):
            _mongo_paths(value, path, depth + 1, limit, array_limit, found, present)
        elif isinstance(value, list):
            for element in value[:array_limit]:
                found[path + "[]"].add(type(element).__name__)
                present.add(path + "[]")
                if isinstance(element, dict):
                    _mongo_paths(
                        element, path + "[]", depth + 1, limit, array_limit, found, present
                    )


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
    sampled_rows_total = 0
    sampled_bytes_total = 0
    for database in config.namespaces:
        budget.charge()
        for info in client[database].list_collections():
            total += 1
            if total > config.max_objects:
                raise RuntimeError("BUDGET_EXHAUSTED")
            native = str(info.get("info", {}).get("uuid", ""))
            if not native:
                tables["diagnostics.jsonl"].append(
                    Record.model_validate(
                        {
                            "record_type": "diagnostic",
                            "id": stable_id(
                                config.source_id, "diagnostic", f"{database}:{info['name']}"
                            ),
                            "code": "IDENTITY_UNAVAILABLE",
                            "message": "collection UUID unavailable",
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
            if (
                config.sample_rows
                and sampled_rows_total < config.sample_rows
                and sampled_bytes_total < config.max_sample_bytes
            ):
                budget.charge()
                cursor = (
                    client[database][name].find({}).limit(config.sample_rows - sampled_rows_total)
                )
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
                        sampled_rows_total += 1
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
                        )
                        for path in present:
                            present_counts[path] += 1
                            origins[path].add("sample")
                finally:
                    if hasattr(cursor, "close"):
                        cursor.close()
            for path, types in sorted(paths.items()):
                if len(tables["fields.jsonl"]) >= config.max_fields:
                    raise RuntimeError("BUDGET_EXHAUSTED")
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
            if info.get("type", "collection") == "collection" and budget.used < budget.max_queries:
                budget.charge()
                try:
                    estimate = client[database][name].estimated_document_count(
                        maxTimeMS=config.read_timeout_seconds * 1000
                    )
                except Exception:  # noqa: BLE001 - optional stats must not fail the metadata scan
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
    return tables


def collect(config: SourceConfig, destination: Path) -> None:
    """Create a signed bundle; never accept connection values on the CLI."""
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
    if config.kind == "postgresql":
        from pg8000 import dbapi

        allowed = {
            key: connection[key]
            for key in ("host", "port", "database", "user", "password")
            if key in connection
        }
        client = dbapi.connect(
            **allowed, ssl_context=ssl.create_default_context(), timeout=config.read_timeout_seconds
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
        client = clickhouse_connect.get_client(**allowed, secure=True)
        try:
            data = discover_clickhouse(client, config, budget)
        finally:
            client.close()
    else:
        from pymongo import MongoClient

        if set(connection) != {"uri"}:
            raise ValueError("MongoDB connection needs only uri")
        client = MongoClient(
            connection["uri"], tls=True, serverSelectionTimeoutMS=config.read_timeout_seconds * 1000
        )
        try:
            data = discover_mongodb(client, config, budget)
        finally:
            client.close()
    common = _common(config)
    data["sources.jsonl"] = [
        Source(id=config.source_id, kind=config.kind, name=config.source_id, **common)
    ]
    write_artifact(
        destination,
        "collector-bundle",
        data,
        producer=version("dbig-collectors"),
        source_run_ids=[str(uuid4())],
        completeness="partial" if data["diagnostics.jsonl"] else "complete",
        signing_key=signing_key,
        key_id=config.signing_key_id,
    )
