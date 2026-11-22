"""Private planner REST API; no source connection or execution routes."""

from __future__ import annotations

from dbig_contracts.security import service_keys
from dbig_contracts.service import ServiceGuard
from fastapi import FastAPI, HTTPException

from .config import PlannerConfig
from .graph import build_graph
from .paths import PathRequest, find_paths
from .plans import PlanRequest, render_plan

# The scope a caller key needs, by path prefix (report Part 3, layer L5).
ROUTE_SCOPES = {
    "/v1/lineage/paths": "plan",
    "/v1/join-paths": "plan",
    "/v1/plans": "plan",
    "/v1/planner/graph": "graph",
}


def create_app(config: PlannerConfig) -> FastAPI:
    app = FastAPI(title="DBIG lineage planner", docs_url=None, redoc_url=None)
    app.add_middleware(
        ServiceGuard,
        keys=service_keys(config.api_key_env, purpose="planner API bearer token"),
        route_scopes=ROUTE_SCOPES,
        max_request_bytes=config.max_request_bytes,
        max_requests_per_minute=config.max_requests_per_minute,
    )

    @app.post("/v1/lineage/paths", summary="Find lineage paths")
    def lineage_paths(body: PathRequest):
        try:
            return find_paths(config.graph_dir, body, "lineage", config)
        except ValueError as error:
            raise HTTPException(status_code=409, detail={"code": str(error)}) from error

    @app.post("/v1/join-paths")
    def join_paths(body: PathRequest):
        try:
            return find_paths(config.graph_dir, body, "join", config)
        except ValueError as error:
            raise HTTPException(status_code=409, detail={"code": str(error)}) from error

    @app.post("/v1/plans")
    def plans(body: PlanRequest):
        try:
            return render_plan(config.graph_dir, body, config)
        except ValueError as error:
            raise HTTPException(status_code=409, detail={"code": str(error)}) from error

    @app.post("/v1/planner/graph")
    def graph():
        # Step 4 of the service flow (report Part 3.5): rebuild the graph from the snapshot.
        if config.snapshot_dir is None or config.observations_path is None:
            raise HTTPException(status_code=503, detail={"code": "GRAPH_SOURCES_NOT_CONFIGURED"})
        try:
            built = build_graph(
                config.snapshot_dir, config.observations_path, config.graph_dir, config
            )
        except ValueError as error:
            raise HTTPException(status_code=409, detail={"code": str(error)}) from error
        return {"snapshot_digest": built["snapshot_digest"]}

    return app
