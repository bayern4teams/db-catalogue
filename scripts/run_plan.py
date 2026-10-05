"""Run a `dbig-planner plan` against your databases, read-only, to check it returns what you expect.

A testing aid, not part of the planner: the planner never executes anything, and this script is
how a person (or their local agent) checks a plan on real data. It reads the source configuration
files `dbig collect` uses, so connections, `tls_ca_file` and secrets come from the same place:
the variables named by `connection_env` must be set (for example by loading `.env`).

For each read step, in order, it binds the step's `parameters` from the earlier step and column
they name, runs the read, and then performs each `logical_join` in memory, as a staging target
would. PostgreSQL runs inside `SET TRANSACTION READ ONLY` and is rolled back; ClickHouse and
MongoDB rely on the read-only accounts from docs/LOCAL_RUN.md step 2. It prints row counts and
column names; row values only with `--show N`, and only to your terminal.

    dbig-planner plan --graph "$out/graph" --path-id <id> > plan.json
    python scripts/run_plan.py --plan plan.json --config artifacts/config/postgresql.toml \\
        --config artifacts/config/mongodb.toml [--show 5]
"""

from __future__ import annotations

import argparse
import json
import ssl
import sys
from pathlib import Path
from typing import Any

from dbig_collectors.config import SourceConfig, load_config
from dbig_contracts.security import secret_from_env


def _connection(config: SourceConfig) -> dict:
    value = json.loads(secret_from_env(config.connection_env, purpose="source connection"))
    if not isinstance(value, dict):
        raise SystemExit(f"{config.connection_env} must hold a JSON object")
    return value


def _postgres(step: dict, config: SourceConfig, values: dict) -> list[dict]:
    from pg8000 import dbapi

    settings = _connection(config)
    connection = dbapi.connect(
        **{key: settings[key] for key in ("host", "port", "database", "user", "password")
           if key in settings},
        ssl_context=ssl.create_default_context(
            cafile=str(config.tls_ca_file) if config.tls_ca_file else None
        ) if config.tls else None,
        timeout=config.read_timeout_seconds,
    )
    # pg8000 takes its placeholder style from a module setting; plans use %(name)s.
    style, dbapi.paramstyle = dbapi.paramstyle, "pyformat"
    try:
        cursor = connection.cursor()
        cursor.execute("SET TRANSACTION READ ONLY")
        if values:
            cursor.execute(step["sql"], values)
        else:
            cursor.execute(step["sql"])
        names = [column[0] for column in cursor.description]
        return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
    finally:
        dbapi.paramstyle = style
        connection.rollback()
        connection.close()


def _clickhouse(step: dict, config: SourceConfig, values: dict) -> list[dict]:
    import clickhouse_connect

    settings = _connection(config)
    options: dict[str, Any] = {"secure": config.tls}
    if config.tls:
        options["verify"] = True
        if config.tls_ca_file:
            options["ca_cert"] = str(config.tls_ca_file)
    client = clickhouse_connect.get_client(
        **{key: settings[key] for key in ("host", "port", "username", "password")
           if key in settings},
        **options,
    )
    try:
        result = client.query(step["sql"], parameters=values or None)
        return [dict(zip(result.column_names, row, strict=True)) for row in result.result_set]
    finally:
        client.close()


def _mongodb(step: dict, config: SourceConfig, values: dict) -> list[dict]:
    from pymongo import MongoClient

    settings = _connection(config)
    options: dict[str, Any] = {"tls": config.tls}
    if config.tls and config.tls_ca_file:
        options["tlsCAFile"] = str(config.tls_ca_file)
    client = MongoClient(
        settings["uri"], serverSelectionTimeoutMS=config.read_timeout_seconds * 1000, **options
    )
    try:
        databases = config.namespaces
        if len(databases) != 1:
            raise SystemExit(
                f"source {config.source_id} lists several namespaces; the plan does not say "
                "which database holds the collection, so run it against a config with one"
            )
        cursor = client[databases[0]][step["collection"]].aggregate(
            step["pipeline"], let=values or None
        )
        return list(cursor)
    finally:
        client.close()


def _value(row: dict, column: str) -> Any:
    """A column of a SQL row, or a dotted path in a MongoDB document."""
    current: Any = row
    for part in column.split("."):
        current = current.get(part) if isinstance(current, dict) else None
    return current


def run(plan: dict, configs: dict[str, SourceConfig], show: int) -> dict:
    runners = {"postgresql": _postgres, "clickhouse": _clickhouse, "mongodb": _mongodb}
    results: list[list[dict]] = []
    report: dict = {"status": plan["status"], "warnings": plan["warnings"], "steps": []}
    for step in plan["steps"]:
        if step["kind"] == "logical_join":
            continue
        config = configs.get(step["source_id"])
        if config is None:
            raise SystemExit(f"no --config given for source {step['source_id']}")
        values = {
            parameter["name"]: sorted(
                {_value(row, parameter["values_from_column"])
                 for row in results[parameter["values_from_step"]]} - {None},
                key=repr,
            )
            for parameter in step.get("parameters", [])
        }
        rows = runners[config.kind](step, config, values)
        results.append(rows)
        report["steps"].append({
            "source_id": step["source_id"],
            "rows": len(rows),
            "bound_keys": {name: len(keys) for name, keys in values.items()},
            "columns": sorted(rows[0]) if rows else [],
            **({"sample": [{key: repr(value) for key, value in row.items()}
                           for row in rows[:show]]} if show else {}),
        })
    reads = [step for step in plan["steps"] if step["kind"] != "logical_join"]
    for step in plan["steps"]:
        if step["kind"] != "logical_join":
            continue
        # The later read was filtered by the earlier read's keys, so joining them on the column
        # its parameter names reproduces the cross-source join a staging target would do.
        later = next(index for index, read in enumerate(reads)
                     if read.get("parameters")
                     and step["right_object_id"] in read["object_ids"])
        parameter = reads[later]["parameters"][0]
        left_rows = results[parameter["values_from_step"]]
        right_rows = results[later]
        right_column = next(
            (name for name in (right_rows[0] if right_rows else {})
             if name == parameter["filters_field"]
             or name.endswith(f"__{parameter['filters_field']}")),
            parameter["filters_field"],
        )
        index: dict[Any, int] = {}
        for row in right_rows:
            key = _value(row, right_column)
            index[key] = index.get(key, 0) + 1
        matched = sum(index.get(_value(row, parameter["values_from_column"]), 0)
                      for row in left_rows)
        report.setdefault("joins", []).append({
            "left_rows": len(left_rows), "right_rows": len(right_rows),
            "joined_rows": matched,
            "left_rows_without_a_match": sum(
                1 for row in left_rows
                if _value(row, parameter["values_from_column"]) not in index
            ),
        })
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--plan", type=Path, required=True,
                        help="the JSON printed by dbig-planner plan")
    parser.add_argument("--config", type=Path, action="append", required=True,
                        help="a source configuration file; repeat for each source in the plan")
    parser.add_argument("--show", type=int, default=0, help="print this many rows per step")
    arguments = parser.parse_args()
    plan = json.loads(arguments.plan.read_text(encoding="utf-8"))
    configs = {config.source_id: config
               for config in (load_config(path) for path in arguments.config)}
    try:
        report = run(plan, configs, arguments.show)
    except Exception as error:  # report the failure in one line, as the commands do
        raise SystemExit(f"run_plan: {type(error).__name__}: {error}") from error
    json.dump(report, sys.stdout, indent=2, default=str)
    print()


if __name__ == "__main__":
    main()
