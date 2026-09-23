"""One-source discovery with bounded metadata reads and sealed publication."""

from __future__ import annotations

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
            "SELECT c.oid, n.nspname, c.relname, c.relkind "  # noqa: S608
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
        for oid, schema, name, relkind in rows:
            tables["objects.jsonl"].append(
                Object(
                    id=stable_id(config.source_id, "object", str(oid)),
                    native_id=str(oid),
                    kind=kinds[relkind],
                    name=name,
                    namespace=schema,
                    **common,
                )
            )
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
            for oid, owner_oid, target_oid, kind, keys, target_keys in constraint_rows:
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
            "SELECT name, toString(uuid), engine FROM system.tables "
            "WHERE database = {db:String} ORDER BY name LIMIT {lim:UInt64}",
            parameters={"db": database, "lim": config.max_objects + 1},
            settings={"max_execution_time": config.read_timeout_seconds},
        ).result_set
        if len(rows) > config.max_objects:
            raise RuntimeError("BUDGET_EXHAUSTED")
        identities: dict[str, str] = {}
        for name, uuid, engine in rows:
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
            tables["objects.jsonl"].append(
                Object(
                    id=stable_id(config.source_id, "object", uuid),
                    native_id=uuid,
                    kind="table",
                    name=name,
                    namespace=database,
                    attributes={"engine": engine},
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
    document: dict[str, Any], prefix: str, depth: int, limit: int, found: dict[str, set[str]]
) -> None:
    if depth > limit:
        return
    for key, value in document.items():
        path = f"{prefix}.{key}" if prefix else key
        found[path].add(type(value).__name__)
        if isinstance(value, dict):
            _mongo_paths(value, path, depth + 1, limit, found)
        elif isinstance(value, list) and value and isinstance(value[0], dict):
            _mongo_paths(value[0], path + "[]", depth + 1, limit, found)


def discover_mongodb(client: Any, config: SourceConfig, budget: Budget) -> dict[str, list[Record]]:
    common = _common(config)
    tables: dict[str, list[Record]] = defaultdict(list)
    total = 0
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
            tables["objects.jsonl"].append(
                Object(
                    id=object_id,
                    native_id=native,
                    kind="collection",
                    name=name,
                    namespace=database,
                    **common,
                )
            )
            budget.charge()
            for index in client[database][name].list_indexes():
                tables["constraints.jsonl"].append(
                    Record.model_validate(
                        {
                            "record_type": "constraint",
                            "id": stable_id(config.source_id, "index", f"{native}:{index['name']}"),
                            "native_id": f"{native}:{index['name']}",
                            "kind": "unique" if index.get("unique") else "index",
                            "object_id": object_id,
                            "field_names": list(index["key"]),
                            **common,
                        }
                    )
                )
            paths: dict[str, set[str]] = defaultdict(set)
            if config.sample_rows:
                budget.charge()
                cursor = client[database][name].find({}).limit(config.sample_rows)
                cursor.max_time_ms(config.read_timeout_seconds * 1000)
                for document in cursor:
                    _mongo_paths(document, "", 1, config.max_path_depth, paths)
            for path, types in sorted(paths.items()):
                field_native = f"{native}:{path}"
                tables["fields.jsonl"].append(
                    Field(
                        id=stable_id(config.source_id, "field", field_native),
                        object_id=object_id,
                        native_id=field_native,
                        name=path,
                        data_type="|".join(sorted(types)),
                        **common,
                    )
                )
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
