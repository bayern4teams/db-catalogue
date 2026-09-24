"""Private planner REST API; no source connection or execution routes."""

from __future__ import annotations

import hmac
import os
import time
from collections import deque
from threading import Lock

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from .config import PlannerConfig
from .paths import PathRequest, find_paths
from .plans import PlanRequest, render_plan


def create_app(config: PlannerConfig) -> FastAPI:
    token = os.getenv(config.api_key_env or "")
    if token is None or not token.strip():
        raise ValueError("planner API key environment reference required")
    expected = "Bearer " + token
    app = FastAPI(title="DBIG lineage planner", docs_url=None, redoc_url=None)
    timestamps: deque[float] = deque()
    lock = Lock()

    @app.middleware("http")
    async def request_limits(request: Request, call_next):
        supplied = request.headers.get("authorization")
        if supplied is None or not hmac.compare_digest(supplied, expected):
            return JSONResponse({"error": {"code": "UNAUTHORIZED"}}, status_code=401)
        if request.method == "POST":
            length = request.headers.get("content-length")
            if length is None or not length.isdecimal() or int(length) > config.max_request_bytes:
                return JSONResponse({"error": {"code": "PAYLOAD_TOO_LARGE"}}, status_code=413)
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > config.max_request_bytes:
                    return JSONResponse({"error": {"code": "PAYLOAD_TOO_LARGE"}}, status_code=413)
            request._body = bytes(body)
        with lock:
            now = time.monotonic()
            while timestamps and timestamps[0] < now - 60:
                timestamps.popleft()
            if len(timestamps) >= config.max_requests_per_minute:
                return JSONResponse({"error": {"code": "RATE_LIMITED"}}, status_code=429)
            timestamps.append(now)
        return await call_next(request)

    def authorize(value: str | None) -> None:
        if value is None or not hmac.compare_digest(value, expected):
            raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED"})

    @app.post("/v1/lineage/paths")
    def lineage_paths(body: PathRequest, authorization: str | None = Header(default=None)):
        authorize(authorization)
        try:
            return find_paths(config.graph_dir, body, "lineage", config)
        except ValueError as error:
            raise HTTPException(status_code=409, detail={"code": str(error)}) from error

    @app.post("/v1/join-paths")
    def join_paths(body: PathRequest, authorization: str | None = Header(default=None)):
        authorize(authorization)
        try:
            return find_paths(config.graph_dir, body, "join", config)
        except ValueError as error:
            raise HTTPException(status_code=409, detail={"code": str(error)}) from error

    @app.post("/v1/plans")
    def plans(body: PlanRequest, authorization: str | None = Header(default=None)):
        authorize(authorization)
        try:
            return render_plan(config.graph_dir, body, config)
        except ValueError as error:
            raise HTTPException(status_code=409, detail={"code": str(error)}) from error

    return app
