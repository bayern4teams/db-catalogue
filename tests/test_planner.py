import json
from datetime import UTC, datetime

import pytest
from click.testing import CliRunner
from dbig_builder.builder import assemble
from dbig_builder.config import BuilderConfig
from dbig_contracts.artifact import read_artifact, write_artifact
from dbig_contracts.models import Edge, Evidence, Field, Object, Source
from dbig_planner.api import create_app
from dbig_planner.cli import main
from dbig_planner.config import PlannerConfig
from dbig_planner.graph import build_graph
from dbig_planner.observations import DatasetMapping, SQLRelation, read_lines, sql_relations
from dbig_planner.paths import PathRequest, find_paths
from dbig_planner.plans import PlanRequest, _mongo_name, _sql_join, render_plan
from fastapi.testclient import TestClient
from sqlglot import exp, parse_one
from test_builder import source_bundle


def _line(path, record):
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record) + "\n")


def test_observation_directory_enforces_aggregate_byte_and_file_limits(tmp_path):
    directory = tmp_path / "observations"
    directory.mkdir()
    (directory / "a.jsonl").write_bytes(b"{}\n")
    (directory / "b.jsonl").write_bytes(b"{}\n")
    with pytest.raises(ValueError, match="UNSAFE_OR_OVERSIZED_OBSERVATIONS"):
        read_lines(directory, max_bytes=5, max_records=10)
    assert read_lines(directory, max_bytes=6, max_records=10) == [{}, {}]

    (directory / "a.jsonl").write_bytes(b"")
    (directory / "b.jsonl").write_bytes(b"")
    with pytest.raises(ValueError, match="OBSERVATION_LIMIT"):
        read_lines(directory, max_bytes=6, max_records=1)


def test_observation_directory_rejects_linked_root(tmp_path):
    directory = tmp_path / "observations"
    directory.mkdir()
    link = tmp_path / "linked"
    try:
        link.symlink_to(directory, target_is_directory=True)
    except OSError:
        pytest.skip(  # C5: reason=see message; approved=user, 2026-11-08; review=2027-05-08
            "directory symlinks unavailable on this host"
        )
    with pytest.raises(ValueError, match="UNSAFE_OBSERVATION_DIRECTORY"):
        read_lines(link, max_bytes=100, max_records=10)


def sql_graph(tmp_path, monkeypatch):
    source_bundle(tmp_path / "bundle")
    monkeypatch.setenv("BUNDLE_KEY", "synthetic-key-12345678901234567890")
    assemble(
        [tmp_path / "bundle"],
        tmp_path / "snapshot",
        BuilderConfig(signing_keys={"test": "BUNDLE_KEY"}),
    )
    observations = tmp_path / "observations"
    observations.mkdir()
    _line(
        observations / "sql.jsonl",
        {
            "kind": "sql",
            "id": "sql-1",
            "source_id": "site-a",
            "dialect": "postgres",
            "sql": "INSERT INTO orders SELECT * FROM customers",
            "observed_at": datetime.now(UTC).isoformat(),
            "status": "executed",
        },
    )
    _line(
        observations / "sql.jsonl",
        {
            "kind": "sql",
            "id": "sql-join",
            "source_id": "site-a",
            "dialect": "postgres",
            "sql": "SELECT * FROM orders JOIN customers ON orders.customer_id = customers.id",
            "observed_at": datetime.now(UTC).isoformat(),
            "status": "executed",
        },
    )
    _line(
        observations / "ol.jsonl",
        {
            "eventType": "COMPLETE",
            "eventTime": datetime.now(UTC).isoformat(),
            "run": {"runId": "run-1"},
            "job": {"namespace": "jobs", "name": "transform"},
            "inputs": [{"namespace": "site", "name": "orders"}],
            "outputs": [
                {"namespace": "site", "name": "customers"},
                {"namespace": "site", "name": "loose_orders"},
            ],
        },
    )
    config = PlannerConfig(graph_dir=tmp_path / "graph", api_key_env="PLANNER_TOKEN")
    graph = build_graph(
        tmp_path / "snapshot",
        observations,
        config.graph_dir,
        config,
        DatasetMapping(dataset_to_object_id={"site/orders": "order", "site/customers": "customer"}),
    )
    return config, graph


def test_sql_lineage_and_join_paths_are_separate(tmp_path, monkeypatch):
    config, graph = sql_graph(tmp_path, monkeypatch)
    assert len(graph["lineage"]) == 1
    assert "INSERT INTO" not in (config.graph_dir / "graph.json").read_text(encoding="utf-8")
    assert graph["observed_joins"]
    assert any(item["code"] == "AMBIGUOUS_OPENLINEAGE_OUTPUT" for item in graph["diagnostics"])
    lineage = find_paths(
        config.graph_dir, PathRequest(from_id="customer", to_id="order"), "lineage", config
    )
    assert lineage["paths"][0]["status"] == "observed"
    assert (
        find_paths(
            config.graph_dir, PathRequest(from_id="order", to_id="customer"), "lineage", config
        )["paths"]
        == []
    )
    joins = find_paths(
        config.graph_dir, PathRequest(from_id="order", to_id="customer"), "join", config
    )
    assert joins["paths"][0]["status"] == "declared"
    plan = render_plan(config.graph_dir, PlanRequest(path_id=joins["paths"][0]["path_id"]), config)
    assert plan["steps"][0]["kind"] == "sql_read"
    assert "JOIN" in plan["steps"][0]["sql"]
    assert "LIMIT" in plan["steps"][0]["sql"]
    assert plan["unresolved_prerequisites"] == []


def test_planner_api_auth_and_cli(tmp_path, monkeypatch):
    config, _ = sql_graph(tmp_path, monkeypatch)
    monkeypatch.setenv("PLANNER_TOKEN", "synthetic-private-token")
    with TestClient(create_app(config)) as client:
        assert (
            client.post(
                "/v1/join-paths", json={"from_id": "order", "to_id": "customer"}
            ).status_code
            == 401
        )
        response = client.post(
            "/v1/join-paths",
            json={"from_id": "order", "to_id": "customer"},
            headers={"Authorization": "Bearer synthetic-private-token"},
        )
        assert response.status_code == 200
        path_id = response.json()["paths"][0]["path_id"]
        assert (
            client.post(
                "/v1/plans",
                json={"path_id": path_id},
                headers={"Authorization": "Bearer synthetic-private-token"},
            ).status_code
            == 200
        )
    result = CliRunner().invoke(
        main, ["plan", "--graph", str(config.graph_dir), "--path-id", path_id]
    )
    assert result.exit_code == 0
    assert json.loads(result.output)["path_id"] == path_id


def test_planner_api_requires_secret_at_startup_and_uses_configured_body_limit(
    tmp_path, monkeypatch
):
    config = PlannerConfig(
        graph_dir=tmp_path / "graph", api_key_env="PLANNER_TOKEN", max_request_bytes=16
    )
    monkeypatch.delenv("PLANNER_TOKEN", raising=False)
    with pytest.raises(ValueError, match="PLANNER_TOKEN, which is not set"):
        create_app(config)
    monkeypatch.setenv("PLANNER_TOKEN", " ")
    with pytest.raises(ValueError, match="PLANNER_TOKEN for planner API bearer token is empty"):
        create_app(config)
    monkeypatch.setenv("PLANNER_TOKEN", "synthetic-private-token")
    with TestClient(create_app(config)) as client:
        response = client.post(
            "/v1/join-paths",
            json={"from_id": "order", "to_id": "customer"},
            headers={"Authorization": "Bearer synthetic-private-token"},
        )
        assert response.status_code == 413


def test_mongo_and_cross_source_plans(tmp_path):
    now = datetime.now(UTC)
    common = {"observed_at": now, "collector_version": "synthetic", "provenance": "fixture"}
    pg = Source(id="pg", source_id="pg", kind="postgresql", name="pg", **common)
    mongo = Source(id="mongo", source_id="mongo", kind="mongodb", name="mongo", **common)
    objects = [
        Object(
            id="orders", source_id="pg", native_id="orders", kind="table", name="orders", **common
        ),
        Object(
            id="users",
            source_id="mongo",
            native_id="users",
            kind="collection",
            name="users",
            **common,
        ),
        Object(
            id="details",
            source_id="mongo",
            native_id="details",
            kind="collection",
            name="details",
            **common,
        ),
    ]
    fields = [
        Field(
            id="orders-user",
            object_id="orders",
            source_id="pg",
            native_id="user_id",
            name="user_id",
            data_type="string",
            **common,
        ),
        Field(
            id="users-id",
            object_id="users",
            source_id="mongo",
            native_id="_id",
            name="_id",
            data_type="string",
            **common,
        ),
        Field(
            id="details-id",
            object_id="details",
            source_id="mongo",
            native_id="user_id",
            name="user_id",
            data_type="string",
            **common,
        ),
    ]
    evidence = [
        Evidence(id="ev1", source_id="pg", kind="fixture", subject_id="orders", **common),
        Evidence(id="ev2", source_id="mongo", kind="fixture", subject_id="users", **common),
    ]
    edges = [
        Edge.model_validate(
            {
                "id": "edge-cross",
                "source_id": "pg",
                "kind": "REFERENCE_CANDIDATE",
                "from_id": "orders",
                "to_id": "users",
                "status": "tentative",
                "evidence_ids": ["ev1"],
                "field_pairs": [["orders-user", "users-id"]],
                **common,
            }
        ),
        Edge.model_validate(
            {
                "id": "edge-mongo",
                "source_id": "mongo",
                "kind": "DECLARES_FK",
                "from_id": "details",
                "to_id": "users",
                "status": "declared",
                "evidence_ids": ["ev2"],
                "field_pairs": [["details-id", "users-id"]],
                **common,
            }
        ),
    ]
    snapshot = tmp_path / "snapshot"
    write_artifact(
        snapshot,
        "catalog-snapshot",
        {
            "nodes.jsonl": [pg, mongo, *objects, *fields],
            "edges.jsonl": edges,
            "evidence.jsonl": evidence,
        },
        producer="fixture",
        source_run_ids=[],
    )
    observations = tmp_path / "observations"
    observations.mkdir()
    config = PlannerConfig(graph_dir=tmp_path / "graph")
    build_graph(snapshot, observations, config.graph_dir, config)
    cross = find_paths(
        config.graph_dir, PathRequest(from_id="orders", to_id="users"), "join", config
    )
    plan = render_plan(config.graph_dir, PlanRequest(path_id=cross["paths"][0]["path_id"]), config)
    assert plan["status"] == "unresolved"
    assert "approved_staging_or_federated_target" in plan["unresolved_prerequisites"]
    assert [step["kind"] for step in plan["steps"]] == [
        "source_read",
        "source_read",
        "logical_join",
    ]
    path = find_paths(
        config.graph_dir, PathRequest(from_id="details", to_id="users"), "join", config
    )
    mongo_plan = render_plan(
        config.graph_dir, PlanRequest(path_id=path["paths"][0]["path_id"]), config
    )
    assert mongo_plan["steps"][0]["pipeline"][0]["$lookup"]["foreignField"] == "_id"

    # Consecutive objects in one source are one read that joins them there. orders (pg) ->
    # users (mongo) -> details (mongo) is one PostgreSQL read, one MongoDB pipeline with a
    # $lookup, and a single logical join where the path crosses sources.
    through = find_paths(
        config.graph_dir, PathRequest(from_id="orders", to_id="details"), "join", config
    )
    merged = render_plan(
        config.graph_dir, PlanRequest(path_id=through["paths"][0]["path_id"]), config
    )
    assert [step["kind"] for step in merged["steps"]] == [
        "source_read", "source_read", "logical_join",
    ]
    assert merged["steps"][1]["object_ids"] == ["users", "details"]
    # The MongoDB read is restricted to the users the PostgreSQL read referenced, bound through
    # the aggregate command's `let`, and takes the key from the first read's named column.
    assert merged["steps"][1]["pipeline"][0] == {
        "$match": {"$expr": {"$in": ["$_id", "$$keys_1_1"]}}
    }
    assert merged["steps"][1]["parameters"] == [
        {
            "name": "keys_1_1",
            "filters_field": "_id",
            "values_from_step": 0,
            "values_from_column": "orders__user_id",
        }
    ]
    assert merged["steps"][1]["parameter_binding"] == "aggregate let"
    assert merged["steps"][0]["parameters"] == []
    assert merged["steps"][1]["pipeline"][1]["$lookup"]["from"] == "details"
    assert merged["steps"][2]["left_object_id"] == "orders"
    assert merged["steps"][2]["right_object_id"] == "users"


def test_sql_parser_rejects_unsafe_and_unmapped(tmp_path, monkeypatch):
    config, _ = sql_graph(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="UNKNOWN_OBJECT"):
        find_paths(config.graph_dir, PathRequest(from_id="unknown", to_id="order"), "join", config)
    observations = tmp_path / "other"
    observations.mkdir()
    _line(
        observations / "bad.jsonl",
        {
            "kind": "sql",
            "id": "bad",
            "source_id": "site-a",
            "dialect": "postgres",
            "sql": "DELETE FROM orders",
            "observed_at": datetime.now(UTC).isoformat(),
            "status": "executed",
        },
    )
    other = tmp_path / "other-graph"
    graph = build_graph(tmp_path / "snapshot", observations, other, PlannerConfig(graph_dir=other))
    assert not graph["lineage"]
    assert graph["diagnostics"][0]["code"] == "UNRESOLVED_SQL"
    _line(
        observations / "bad.jsonl",
        {
            "kind": "sql",
            "id": "cte",
            "source_id": "site-a",
            "dialect": "postgres",
            "sql": "INSERT INTO orders WITH x AS (SELECT * FROM customers) SELECT * FROM x",
            "observed_at": datetime.now(UTC).isoformat(),
            "status": "executed",
        },
    )
    newer = tmp_path / "newer-graph"
    graph = build_graph(tmp_path / "snapshot", observations, newer, PlannerConfig(graph_dir=newer))
    assert {(edge["from_id"], edge["to_id"]) for edge in graph["lineage"]} == {
        ("customer", "order")
    }
    assert len(graph["diagnostics"]) == 1


def test_sql_cte_nested_read_qualified_target_and_aliases():
    target, reads, joins = sql_relations(
        "WITH x AS (SELECT * FROM public.customers) "
        "INSERT INTO public.orders SELECT * FROM x",
        "postgres",
    )
    assert target == SQLRelation("orders", "public")
    assert reads == [SQLRelation("customers", "public")]
    assert joins == []
    target, reads, joins = sql_relations(
        "SELECT * FROM (SELECT * FROM public.orders) x "
        "JOIN public.customers c ON x.customer_id = c.id",
        "postgres",
    )
    assert target is None
    assert set(reads) == {
        SQLRelation("orders", "public"), SQLRelation("customers", "public")
    }
    assert joins == []
    with pytest.raises(ValueError, match="UNSUPPORTED_NESTED_WRITE"):
        sql_relations(
            "WITH x AS (DELETE FROM orders RETURNING customer_id) "
            "INSERT INTO orders SELECT * FROM x",
            "postgres",
        )


@pytest.mark.parametrize(
    ("statement", "target_name"),
    [
        ("CREATE TABLE public.copy AS SELECT * FROM public.customers", "copy"),
        ("CREATE MATERIALIZED VIEW public.mv AS SELECT * FROM public.customers", "mv"),
    ],
)
def test_sql_defined_views_and_ctas(statement, target_name):
    target, reads, joins = sql_relations(statement, "postgres")
    assert target == SQLRelation(target_name, "public")
    assert reads == [SQLRelation("customers", "public")]
    assert joins == []


def test_sql_alias_join_keeps_base_table_names():
    target, reads, joins = sql_relations(
        "SELECT * FROM public.orders o JOIN public.customers c "
        "ON o.customer_id = c.id",
        "postgres",
    )
    assert target is None
    assert set(reads) == {
        SQLRelation("orders", "public"), SQLRelation("customers", "public")
    }
    assert joins == [
        (SQLRelation("orders", "public"), SQLRelation("customers", "public"))
    ]


def test_clickhouse_materialized_view_target_abstains_without_false_read():
    with pytest.raises(ValueError, match="UNRESOLVED_CLICKHOUSE_MV_TARGET"):
        sql_relations(
            "CREATE MATERIALIZED VIEW mv TO target AS SELECT * FROM raw_events",
            "clickhouse",
        )
    target, reads, joins = sql_relations(
        "CREATE MATERIALIZED VIEW mv AS SELECT * FROM raw_events", "clickhouse"
    )
    assert target == SQLRelation("mv", None)
    assert reads == [SQLRelation("raw_events", None)]
    assert joins == []


def test_sql_qualified_resolution_survives_duplicate_names(tmp_path, monkeypatch):
    sql_graph(tmp_path, monkeypatch)
    _, tables = read_artifact(tmp_path / "snapshot")
    original = next(item for item in tables["nodes.jsonl"] if item.id == "order")
    archive = original.model_copy(update={"id": "archive-order", "namespace": "archive"})
    write_artifact(
        tmp_path / "ambiguous-snapshot",
        "catalog-snapshot",
        {
            "nodes.jsonl": [*tables["nodes.jsonl"], archive],
            "edges.jsonl": tables["edges.jsonl"],
            "evidence.jsonl": tables["evidence.jsonl"],
            "runs.jsonl": tables["runs.jsonl"],
        },
        producer="synthetic",
        source_run_ids=[item.id for item in tables["runs.jsonl"]],
    )
    observations = tmp_path / "qualified-observations"
    observations.mkdir()
    _line(
        observations / "sql.jsonl",
        {
            "kind": "sql", "id": "qualified", "source_id": "site-a",
            "dialect": "postgres",
            "sql": "INSERT INTO public.orders SELECT * FROM public.customers",
            "observed_at": datetime.now(UTC).isoformat(), "status": "executed",
        },
    )
    target = tmp_path / "qualified-graph"
    graph = build_graph(
        tmp_path / "ambiguous-snapshot", observations, target, PlannerConfig(graph_dir=target)
    )
    assert {(edge["from_id"], edge["to_id"]) for edge in graph["lineage"]} == {
        ("customer", "order")
    }
    assert not graph["diagnostics"]


def test_pipeline_and_openlineage_input_mapping(tmp_path, monkeypatch):
    config, _ = sql_graph(tmp_path, monkeypatch)
    observations = tmp_path / "mapped-observations"
    observations.mkdir()
    _line(
        observations / "events.jsonl",
        {
            "kind": "pipeline",
            "id": "pipeline-1",
            "input_object_ids": ["customer"],
            "output_object_id": "order",
            "observed_at": datetime.now(UTC).isoformat(),
            "status": "defined",
        },
    )
    _line(
        observations / "events.jsonl",
        {
            "eventType": "COMPLETE",
            "eventTime": datetime.now(UTC).isoformat(),
            "run": {"runId": "run-2"},
            "job": {"namespace": "jobs", "name": "transform"},
            "inputs": [
                {"namespace": "site", "name": "customers"},
                {"namespace": "site", "name": "loose_orders"},
            ],
            "outputs": [{"namespace": "site", "name": "orders"}],
        },
    )
    mapped = DatasetMapping(
        dataset_to_object_id={
            "site/customers": "customer",
            "site/loose_orders": "loose",
            "site/orders": "order",
        }
    )
    target = tmp_path / "mapped-graph"
    graph = build_graph(
        tmp_path / "snapshot", observations, target, PlannerConfig(graph_dir=target), mapped
    )
    assert len(graph["lineage"]) == 3
    assert any(
        "multi_input_column_mapping_unresolved" in edge["limitations"] for edge in graph["lineage"]
    )
    assert not graph["diagnostics"]


def test_openlineage_job_facet_maps_multi_output_without_cartesian_edges(tmp_path, monkeypatch):
    sql_graph(tmp_path, monkeypatch)
    observations = tmp_path / "facet-observations"
    observations.mkdir()
    event = {
        "eventType": "COMPLETE",
        "eventTime": datetime.now(UTC).isoformat(),
        "run": {"runId": "run-facet"},
        "job": {
            "namespace": "jobs",
            "name": "two-outputs",
            "facets": {
                "lineage": {
                    "entries": [
                        {
                            "namespace": "site", "name": "customers", "type": "DATASET",
                            "inputs": [{"namespace": "site", "name": "orders", "type": "DATASET"}],
                        },
                        {
                            "namespace": "site", "name": "orders", "type": "DATASET",
                            "inputs": [
                                {"namespace": "site", "name": "loose_orders", "type": "DATASET"}
                            ],
                        },
                    ]
                }
            },
        },
        "inputs": [
            {"namespace": "site", "name": "orders"},
            {"namespace": "site", "name": "loose_orders"},
        ],
        "outputs": [
            {"namespace": "site", "name": "customers"},
            {"namespace": "site", "name": "orders"},
        ],
    }
    _line(observations / "event.jsonl", event)
    target = tmp_path / "facet-graph"
    mapping = DatasetMapping(
        dataset_to_object_id={
            "site/customers": "customer", "site/orders": "order", "site/loose_orders": "loose"
        }
    )
    graph = build_graph(
        tmp_path / "snapshot", observations, target, PlannerConfig(graph_dir=target), mapping
    )
    assert {(edge["from_id"], edge["to_id"]) for edge in graph["lineage"]} == {
        ("order", "customer"), ("loose", "order")
    }
    assert not graph["diagnostics"]

    event["job"]["facets"]["lineage"]["entries"] = "invalid"
    _line(observations / "event.jsonl", event)
    broken = tmp_path / "broken-facet-graph"
    graph = build_graph(
        tmp_path / "snapshot", observations, broken, PlannerConfig(graph_dir=broken), mapping
    )
    assert len(graph["lineage"]) == 2
    assert any(item["code"] == "INVALID_OPENLINEAGE_LINEAGE_FACET" for item in graph["diagnostics"])


def test_openlineage_job_facet_maps_exact_field_lineage(tmp_path, monkeypatch):
    sql_graph(tmp_path, monkeypatch)
    observations = tmp_path / "field-observations"
    observations.mkdir()
    _line(
        observations / "event.jsonl",
        {
            "eventType": "COMPLETE",
            "eventTime": datetime.now(UTC).isoformat(),
            "run": {"runId": "run-fields"},
            "job": {
                "namespace": "jobs", "name": "field-transform",
                "facets": {
                    "lineage": {
                        "entries": [
                            {
                                "namespace": "site", "name": "orders", "type": "DATASET",
                                "fields": {
                                    "customer_id": {
                                        "inputs": [
                                            {
                                                "namespace": "site", "name": "loose_orders",
                                                "type": "DATASET", "field": "customer_id",
                                                "transformations": [
                                                    {
                                                        "type": "DIRECT",
                                                        "description": "CANARY_PRIVATE_SQL",
                                                    }
                                                ],
                                            }
                                        ]
                                    }
                                },
                            }
                        ]
                    }
                },
            },
            "inputs": [{"namespace": "site", "name": "customers"}],
            "outputs": [{"namespace": "site", "name": "orders"}],
        },
    )
    target = tmp_path / "field-graph"
    mapping = DatasetMapping(
        dataset_to_object_id={
            "site/customers": "customer", "site/orders": "order", "site/loose_orders": "loose"
        }
    )
    graph = build_graph(
        tmp_path / "snapshot", observations, target, PlannerConfig(graph_dir=target), mapping
    )
    assert {(edge["from_id"], edge["to_id"]) for edge in graph["lineage"]} == {
        ("loose", "order")
    }
    assert {(edge["from_id"], edge["to_id"]) for edge in graph["field_lineage"]} == {
        ("loose-fk", "order-fk")
    }
    assert not graph["diagnostics"]
    field_paths = find_paths(
        target, PathRequest(from_id="loose-fk", to_id="order-fk"),
        "lineage", PlannerConfig(graph_dir=target),
    )
    assert field_paths["paths"][0]["field_ids"] == ["loose-fk", "order-fk"]
    assert field_paths["paths"][0]["status"] == "observed"
    assert "CANARY_PRIVATE_SQL" not in (target / "graph.json").read_text(encoding="utf-8")
    # A lineage path has no plan to render, which the message now says outright. The old
    # STALE_OR_NON_JOIN_PATH collapsed two different causes, a stale path and a category
    # error, into one code that told the operator neither which applied nor what to do.
    with pytest.raises(ValueError, match="NON_JOIN_PATH: .* is a lineage path"):
        render_plan(target, PlanRequest(path_id=field_paths["paths"][0]["path_id"]),
                    PlannerConfig(graph_dir=target))
    monkeypatch.setenv("PLANNER_TOKEN", "synthetic-private-token")
    api_config = PlannerConfig(graph_dir=target, api_key_env="PLANNER_TOKEN")
    with TestClient(create_app(api_config)) as client:
        response = client.post(
            "/v1/lineage/paths",
            json={"from_id": "loose-fk", "to_id": "order-fk"},
            headers={"Authorization": "Bearer synthetic-private-token"},
        )
        assert response.status_code == 200
        assert response.json()["paths"][0]["entity_kind"] == "field"


def test_openlineage_column_facet_maps_fields_without_guessing_other_outputs(
    tmp_path, monkeypatch
):
    sql_graph(tmp_path, monkeypatch)
    observations = tmp_path / "column-observations"
    observations.mkdir()
    _line(
        observations / "event.jsonl",
        {
            "eventType": "COMPLETE",
            "eventTime": datetime.now(UTC).isoformat(),
            "run": {"runId": "column-run"},
            "job": {"namespace": "jobs", "name": "column-transform"},
            "inputs": [
                {"namespace": "site", "name": "loose_orders"},
                {"namespace": "site", "name": "customers"},
            ],
            "outputs": [
                {
                    "namespace": "site", "name": "orders",
                    "facets": {
                        "columnLineage": {
                            "fields": {
                                "customer_id": {
                                    "inputFields": [
                                        {
                                            "namespace": "site", "name": "loose_orders",
                                            "field": "customer_id",
                                        }
                                    ]
                                }
                            },
                            "dataset": [],
                        }
                    },
                },
                {"namespace": "site", "name": "customers"},
            ],
        },
    )
    target = tmp_path / "column-graph"
    mapping = DatasetMapping(
        dataset_to_object_id={
            "site/customers": "customer", "site/orders": "order", "site/loose_orders": "loose"
        }
    )
    graph = build_graph(
        tmp_path / "snapshot", observations, target, PlannerConfig(graph_dir=target), mapping
    )
    assert {(edge["from_id"], edge["to_id"]) for edge in graph["lineage"]} == {
        ("loose", "order")
    }
    assert {(edge["from_id"], edge["to_id"]) for edge in graph["field_lineage"]} == {
        ("loose-fk", "order-fk")
    }
    assert any(
        item["code"] == "OPENLINEAGE_OUTPUT_WITHOUT_EXPLICIT_LINEAGE"
        for item in graph["diagnostics"]
    )


def test_openlineage_job_event_paths_are_defined_not_observed(tmp_path, monkeypatch):
    sql_graph(tmp_path, monkeypatch)
    observations = tmp_path / "job-event-observations"
    observations.mkdir()
    _line(
        observations / "job.jsonl",
        {
            "kind": "openlineage_job",
            "eventTime": datetime.now(UTC).isoformat(),
            "job": {
                "namespace": "jobs", "name": "defined-transform",
                "facets": {
                    "lineage": {
                        "entries": [
                            {
                                "namespace": "site", "name": "orders", "type": "DATASET",
                                "inputs": [
                                    {"namespace": "site", "name": "customers", "type": "DATASET"}
                                ],
                            }
                        ]
                    }
                },
            },
        },
    )
    target = tmp_path / "job-event-graph"
    config = PlannerConfig(graph_dir=target)
    mapping = DatasetMapping(
        dataset_to_object_id={"site/customers": "customer", "site/orders": "order"}
    )
    build_graph(tmp_path / "snapshot", observations, target, config, mapping)
    paths = find_paths(target, PathRequest(from_id="customer", to_id="order"), "lineage", config)
    assert paths["paths"][0]["status"] == "defined"


def test_openlineage_dataset_facet_takes_precedence_over_column_facet(tmp_path, monkeypatch):
    sql_graph(tmp_path, monkeypatch)
    observations = tmp_path / "dataset-facet-observations"
    observations.mkdir()
    _line(
        observations / "event.jsonl",
        {
            "eventType": "COMPLETE",
            "eventTime": datetime.now(UTC).isoformat(),
            "run": {"runId": "dataset-facet-run"},
            "job": {"namespace": "jobs", "name": "dataset-facet"},
            "inputs": [
                {"namespace": "site", "name": "customers"},
                {"namespace": "site", "name": "loose_orders"},
            ],
            "outputs": [
                {
                    "namespace": "site", "name": "orders",
                    "facets": {
                        "lineage": {
                            "inputs": [
                                {"namespace": "site", "name": "customers", "type": "DATASET"}
                            ]
                        },
                        "columnLineage": {
                            "fields": {
                                "customer_id": {
                                    "inputFields": [
                                        {
                                            "namespace": "site", "name": "loose_orders",
                                            "field": "customer_id",
                                        }
                                    ]
                                }
                            }
                        },
                    },
                }
            ],
        },
    )
    target = tmp_path / "dataset-facet-graph"
    mapping = DatasetMapping(
        dataset_to_object_id={
            "site/customers": "customer", "site/orders": "order", "site/loose_orders": "loose"
        }
    )
    graph = build_graph(
        tmp_path / "snapshot", observations, target, PlannerConfig(graph_dir=target), mapping
    )
    assert {(edge["from_id"], edge["to_id"]) for edge in graph["lineage"]} == {
        ("customer", "order")
    }
    assert not graph["field_lineage"]


def test_catalog_identifiers_are_quoted_and_mongo_operators_refused():
    malicious = 'users"; DROP TABLE secret; --'
    column = 'id"; DELETE FROM secret; --'
    sql, names = _sql_join(
        ["o"],
        [],
        {"o": {"name": malicious, "namespace": "public"}},
        {"f": {"object_id": "o", "name": column, "ordinal": 1}},
        "postgres",
        10,
        [],
    )
    assert '"users""; DROP TABLE secret; --"' in sql
    assert '"id""; DELETE FROM secret; --"' in sql
    parsed = parse_one(sql, read="postgres")
    assert isinstance(parsed, exp.Select)
    assert [table.name for table in parsed.find_all(exp.Table)] == [malicious]
    assert names == {(0, column): f"{malicious}__{column}"}
    with pytest.raises(ValueError, match="UNSAFE_MONGO_IDENTIFIER"):
        _mongo_name("$where")


def test_same_source_runs_join_in_one_query_and_never_across_a_gap():
    """Runs are consecutive only: a path that leaves a source and returns is two reads."""
    from dbig_planner.plans import _runs

    assert _runs(["a", "b", "c", "d"], ["pg", "mongo", "mongo", "mongo"]) == [(0, 1), (1, 4)]
    assert _runs(["a", "b", "c"], ["pg", "mongo", "pg"]) == [(0, 1), (1, 2), (2, 3)]
    assert _runs(["a", "b"], ["pg", "pg"]) == [(0, 2)]
    assert _runs(["a"], ["pg"]) == [(0, 1)]


def _dec4_graph(tmp_path, *, cross_value_evidence=False):
    """ch.exp -> ch.company directly (same source) and via pg.mid (two cross-source candidates)."""
    now = datetime.now(UTC)
    common = {"observed_at": now, "collector_version": "synthetic", "provenance": "fixture"}
    sources = [
        Source(id=name, source_id=name, kind=kind, name=name, **common)
        for name, kind in (("ch", "clickhouse"), ("pg", "postgresql"))
    ]
    placement = {"exp": "ch", "company": "ch", "mid": "pg"}
    objects = [
        Object(id=name, source_id=source, native_id=name, kind="table", name=name, **common)
        for name, source in placement.items()
    ]
    evidence = [Evidence(id="ev", source_id="ch", kind="fixture", subject_id="exp", **common)]
    if cross_value_evidence:
        evidence.append(
            Evidence(id="ev-values", source_id="ch", kind="value_containment", subject_id="exp",
                     **common)
        )

    def edge(name, left, right, cited):
        return Edge.model_validate({
            "id": name, "source_id": placement[left], "kind": "REFERENCE_CANDIDATE",
            "from_id": left, "to_id": right, "status": "tentative", "evidence_ids": cited,
            "field_pairs": [], **common,
        })

    cross = ["ev", "ev-values"] if cross_value_evidence else ["ev"]
    edges = [
        edge("direct", "exp", "company", ["ev"]),
        edge("hop1", "exp", "mid", cross),
        edge("hop2", "mid", "company", cross),
    ]
    snapshot = tmp_path / "snapshot"
    write_artifact(
        snapshot, "catalog-snapshot",
        {"nodes.jsonl": [*sources, *objects], "edges.jsonl": edges, "evidence.jsonl": evidence},
        producer="fixture", source_run_ids=[],
    )
    (tmp_path / "observations").mkdir()
    config = PlannerConfig(graph_dir=tmp_path / "graph")
    build_graph(snapshot, tmp_path / "observations", config.graph_dir, config)
    return config


def test_dec4_a_same_source_path_beats_the_roundabout_cross_source_one(tmp_path):
    config = _dec4_graph(tmp_path)
    found = find_paths(
        config.graph_dir, PathRequest(from_id="exp", to_id="company", max_paths=5), "join", config
    )
    assert [item["object_ids"] for item in found["paths"]] == [["exp", "company"]]
    assert "warnings" not in found["paths"][0]


def test_dec4_a_path_of_only_unverified_cross_source_candidates_warns(tmp_path):
    config = _dec4_graph(tmp_path)
    found = find_paths(
        config.graph_dir, PathRequest(from_id="exp", to_id="mid"), "join", config
    )
    assert found["paths"][0]["warnings"] == ["CROSS_SOURCE_CANDIDATES_UNVERIFIED"]


def test_dec4_value_evidence_silences_the_warning(tmp_path):
    config = _dec4_graph(tmp_path, cross_value_evidence=True)
    found = find_paths(
        config.graph_dir, PathRequest(from_id="exp", to_id="mid"), "join", config
    )
    assert "warnings" not in found["paths"][0]
