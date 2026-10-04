"""Measure the full pipeline at 10 sources, 200 objects and 1,000 fields on real servers.

Seeds synthetic schemas into the loopback containers `ci.yml` starts (4 PostgreSQL schemas,
3 ClickHouse databases, 3 MongoDB databases; 20 objects of 5 fields each per source, with a
declared foreign-key chain in PostgreSQL and `<table>_id` references from ClickHouse and MongoDB
to PostgreSQL), then runs the shipped commands and reports, per step, wall time and peak memory
of the command, plus bundle and page sizes, queries used against each source's limit, and the
number of relationships proposed. Nothing here is a pass/fail gate: it measures.

    DBIG_TEST_CA_FILE=/path/to/ci/ca.crt python scripts/scale_benchmark.py --out /tmp/scale

Run it from the activated project environment (`dbig` and friends on PATH). It drops and
recreates the `scale_*` schemas and databases it uses, and touches nothing else.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

SOURCES_PG, SOURCES_CH, SOURCES_MONGO = 4, 3, 3
OBJECTS_PER_SOURCE = 20
ENTITIES = [
    "customer", "order", "invoice", "payment", "refund", "product", "supplier", "shipment",
    "warehouse", "employee", "department", "contract", "ticket", "campaign", "lead", "account",
    "region", "store", "coupon", "review",
]
SIGNING_KEY = "synthetic-scale-signing-key-well-over-32-bytes"
SCRIPTS = Path(sys.executable).parent


def _seed() -> None:
    import clickhouse_connect
    from pg8000 import dbapi
    from pymongo import MongoClient

    connection = dbapi.connect(host="127.0.0.1", port=5432, user="postgres", database="postgres")
    try:
        cursor = connection.cursor()
        for source in range(SOURCES_PG):
            schema = f"scale_pg{source}"
            cursor.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            cursor.execute(f"CREATE SCHEMA {schema}")
            for index, entity in enumerate(ENTITIES):
                parent = (
                    f", {ENTITIES[index - 1]}_id integer REFERENCES "
                    f"{schema}.{ENTITIES[index - 1]}s(id)"
                    if index
                    else ", parent_code text"
                )
                cursor.execute(
                    f"CREATE TABLE {schema}.{entity}s (id integer PRIMARY KEY, "
                    f"name text, amount numeric(12,2), created_at timestamptz{parent})"
                )
                cursor.execute(
                    f"INSERT INTO {schema}.{entity}s (id, name, amount, created_at) "  # noqa: S608
                    "SELECT g, 'n' || g, g, now() FROM generate_series(1, 200) g"
                )
        cursor.execute("ANALYZE")
        connection.commit()
    finally:
        connection.close()
    client = clickhouse_connect.get_client(host="127.0.0.1", port=8123)
    try:
        for source in range(SOURCES_CH):
            database = f"scale_ch{source}"
            client.command(f"DROP DATABASE IF EXISTS {database}")
            client.command(f"CREATE DATABASE {database}")
            for entity in ENTITIES:
                client.command(
                    f"CREATE TABLE {database}.{entity}_events (id UInt64, {entity}_id Int32, "
                    "kind LowCardinality(String), value Float64, at DateTime) "
                    "ENGINE = MergeTree ORDER BY id"
                )
                client.command(
                    f"INSERT INTO {database}.{entity}_events SELECT number, "  # noqa: S608
                    "toInt32(1 + number % 200), 'k', number, now() FROM numbers(1000)"
                )
    finally:
        client.close()
    mongo = MongoClient("mongodb://127.0.0.1:27017", serverSelectionTimeoutMS=5000)
    try:
        for source in range(SOURCES_MONGO):
            database = mongo[f"scale_mongo{source}"]
            mongo.drop_database(database.name)
            for entity in ENTITIES:
                database[f"{entity}_notes"].insert_many(
                    [
                        {f"{entity}_id": index % 200 + 1, "text": "t", "meta": {"source": "s"}}
                        for index in range(150)
                    ]
                )
    finally:
        mongo.close()


def _measured(env: dict[str, str], *argv: str) -> dict:
    """Run one command and report its wall time and its own peak resident memory."""
    before = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    start = time.perf_counter()
    result = subprocess.run(  # noqa: S603 - fixed argv built here, no shell
        [str(SCRIPTS / argv[0]), *argv[1:]], capture_output=True, text=True, env=env,
        timeout=600,
    )
    seconds = time.perf_counter() - start
    peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    if result.returncode != 0:
        raise SystemExit(f"{' '.join(argv[:2])} failed:\n{result.stderr}")
    return {
        "seconds": round(seconds, 2),
        # ru_maxrss of children is a high-water mark: exact for the first command that sets it,
        # an upper bound for any later, smaller one. Linux reports kilobytes.
        "peak_rss_mib_at_most": round(max(before, peak) / 1024, 1),
        "stdout": result.stdout,
    }


def _size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--searches", type=int, default=50)
    arguments = parser.parse_args()
    ca = os.environ.get("DBIG_TEST_CA_FILE")
    if not ca:
        raise SystemExit("set DBIG_TEST_CA_FILE to the CA of the loopback test servers")
    out: Path = arguments.out
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    seed_start = time.perf_counter()
    _seed()
    report: dict = {"seed_seconds": round(time.perf_counter() - seed_start, 1), "collect": {}}
    env = {key: value for key, value in os.environ.items() if key != "SSL_CERT_FILE"}
    env["DBIG_SIGNING_KEY"] = SIGNING_KEY
    connections = {
        "postgresql": {"host": "127.0.0.1", "port": 5432, "database": "postgres",
                       "user": "postgres"},
        "clickhouse": {"host": "127.0.0.1", "port": 8443},
        "mongodb": {"uri": "mongodb://127.0.0.1:27017"},
    }
    sources = (
        [("postgresql", f"scale_pg{index}") for index in range(SOURCES_PG)]
        + [("clickhouse", f"scale_ch{index}") for index in range(SOURCES_CH)]
        + [("mongodb", f"scale_mongo{index}") for index in range(SOURCES_MONGO)]
    )
    bundles: list[str] = []
    for kind, namespace in sources:
        config = out / f"{namespace}.toml"
        # The example configuration's settings, so the run shows whether they are enough.
        extra = "sample_rows = 100\nmax_queries = 500\n" if kind == "mongodb" else ""
        config.write_text(
            f'[source]\nsource_id = "{namespace}"\nkind = "{kind}"\n'
            'connection_env = "DBIG_CONNECTION"\nsigning_key_env = "DBIG_SIGNING_KEY"\n'
            f'signing_key_id = "scale"\nnamespaces = ["{namespace}"]\n'
            f'tls_ca_file = "{Path(ca).as_posix()}"\n{extra}',
            encoding="utf-8",
        )
        bundle = out / f"bundle-{namespace}"
        measured = _measured(
            {**env, "DBIG_CONNECTION": json.dumps(connections[kind])},
            "dbig", "collect", "--config", str(config), "--out", str(bundle),
        )
        manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
        counts = {item["path"].split(".")[0]: item["count"] for item in manifest["files"]}
        measured.pop("stdout")
        report["collect"][namespace] = {
            **measured, "bytes": _size(bundle), "objects": counts.get("objects", 0),
            "fields": counts.get("fields", 0),
        }
        bundles += ["--bundle", str(bundle)]
    (out / "builder.toml").write_text(
        '[builder]\nsigning_keys = { scale = "DBIG_SIGNING_KEY" }\n', encoding="utf-8"
    )
    assembled = _measured(env, "dbig-builder", "assemble", "--config",
                          str(out / "builder.toml"), *bundles, "--out", str(out / "snapshot"))
    rendered = _measured(env, "dbig-builder", "render", "--config", str(out / "builder.toml"),
                         "--snapshot", str(out / "snapshot"), *bundles,
                         "--out", str(out / "catalog.html"))
    counts = json.loads(rendered.pop("stdout"))
    assembled.pop("stdout")
    report["assemble"] = {**assembled, "snapshot_bytes": _size(out / "snapshot")}
    report["render"] = {**rendered, "page_bytes": (out / "catalog.html").stat().st_size}
    report["catalogue"] = {key: counts[key] for key in
                           ("sources", "objects", "fields", "declared_fks", "candidates")}

    (out / "requester.toml").write_text(
        f'[requester]\nsnapshot_dir = "{(out / "snapshot").as_posix()}"\n'
        f'state_dir = "{(out / "requester").as_posix()}"\napi_key_env = "REQUESTER_TOKEN"\n',
        encoding="utf-8",
    )
    indexed = _measured(env, "dbig-requester", "index", "--config", str(out / "requester.toml"))
    indexed.pop("stdout")
    report["index"] = indexed
    latencies = []
    words = ENTITIES * (arguments.searches // len(ENTITIES) + 1)
    for word in words[: arguments.searches]:
        searched = _measured(env, "dbig-requester", "search", "--config",
                             str(out / "requester.toml"), "--query", word)
        latencies.append(searched["seconds"])
    report["search_command_seconds"] = {
        "count": len(latencies), "median": statistics.median(latencies),
        "p95": sorted(latencies)[int(0.95 * (len(latencies) - 1))],
    }
    (out / "observations").mkdir()
    built = _measured(env, "dbig-planner", "build", "--snapshot", str(out / "snapshot"),
                      "--observations", str(out / "observations"), "--out", str(out / "graph"))
    built.pop("stdout")
    report["planner_build"] = built
    from dbig_contracts.artifact import read_artifact

    _, tables = read_artifact(out / "snapshot")
    ids = {(item.source_id, item.name): item.id
           for item in tables["nodes.jsonl"] if item.record_type == "object"}
    # payment_notes (MongoDB) -> payments -> invoices -> orders -> customers (PostgreSQL) is
    # 4 hops, the default limit; review_notes -> customers is 20 hops away, so that query finds
    # nothing and shows the cost of exhausting the search.
    for label, start in (("reachable_4_hops", "payment_notes"), ("beyond_limit", "review_notes")):
        timing = _measured(env, "dbig-planner", "join-paths", "--graph", str(out / "graph"),
                           "--from", ids[("scale_mongo0", start)],
                           "--to", ids[("scale_pg0", "customers")])
        paths = json.loads(timing.pop("stdout"))["paths"]
        report[f"join_paths_{label}"] = {
            **timing, "paths": len(paths),
            "hops": sorted({len(item["object_ids"]) - 1 for item in paths}),
        }
        if paths and "plan" not in report:
            planned = _measured(env, "dbig-planner", "plan", "--graph", str(out / "graph"),
                                "--path-id", paths[0]["path_id"])
            plan = json.loads(planned.pop("stdout"))
            report["plan"] = {**planned, "steps": [step["kind"] for step in plan["steps"]]}
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
