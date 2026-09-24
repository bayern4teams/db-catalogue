"""One-source discovery with bounded metadata reads and sealed publication."""

from __future__ import annotations

import hashlib
import json
import os
import ssl
from collections import defaultdict
from importlib.metadata import version
from pathlib import Path
from typing import Any
from uuid import uuid4

from dbig_contracts.artifact import write_artifact
from dbig_contracts.ids import stable_id
from dbig_contracts.models import Field, Object, Record, Source, utc_now

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
            "SELECT c.oid, n.nspname, c.relname, c.relkind, c.reltuples "  # noqa: S608
            "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n "
            "ON n.oid = c.relnamespace "
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
        for oid, schema, name, relkind, reltuples in rows:
            attributes: dict[str, str | int | float | bool | None] = {}
            if relkind in {"r", "m"} and reltuples is not None and reltuples >= 0:
                attributes["row_count_estimate"] = float(reltuples)
                attributes["row_count_method"] = "pg_class.reltuples"
            tables["objects.jsonl"].append(
                Object(
                    id=stable_id(config.source_id, "object", str(oid)),
                    native_id=str(oid),
                    kind=kinds[relkind],
                    name=name,
                    namespace=schema,
                    attributes=attributes,
                    **common,
                )
            )
        view_oids = [oid for oid, _, _, relkind, _ in rows if relkind in {"v", "m"}]
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
        columns = client.query(
            "SELECT table,name,type,position FROM system.columns "
            "WHERE database = {db:String} ORDER BY table,position LIMIT {lim:UInt64}",
            parameters={"db": database, "lim": config.max_fields + 1},
            settings={"max_execution_time": config.read_timeout_seconds},
        ).result_set
        if len(columns) > config.max_fields:
            raise RuntimeError("BUDGET_EXHAUSTED")
        for table, name, data_type, position in columns:
            if table not in identities:
                continue
            native = f"{identities[table]}:{position}"
            tables["fields.jsonl"].append(
                Field(
                    id=stable_id(config.source_id, "field", native),
                    object_id=stable_id(config.source_id, "object", identities[table]),
                    native_id=native,
                    name=name,
                    data_type=data_type,
                    ordinal=position,
                    **common,
                )
            )
    return tables


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
    connection = json.loads(os.environ[config.connection_env])
    if not isinstance(connection, dict):
        raise ValueError("connection reference must contain a JSON object")
    signing_key = os.environ[config.signing_key_env].encode("utf-8")
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
