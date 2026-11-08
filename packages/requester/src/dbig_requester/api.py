"""Authenticated requester REST surface over the same application functions as CLI."""

from __future__ import annotations

import hmac
import time
from collections import deque
from threading import Lock
from typing import Literal

from dbig_contracts.scan import ScanRequest
from dbig_contracts.security import secret_from_env
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .answer import answer_question
from .config import RequesterConfig
from .scan import dispatch_scan, scan_status
from .search import SearchRequest, search


class QuestionBody(BaseModel):
    question: str = Field(min_length=1, max_length=1024)


class ScanBody(BaseModel):
    request: ScanRequest
    adapter: Literal["bundle", "http"]


def create_app(config: RequesterConfig) -> FastAPI:
    token = secret_from_env(config.api_key_env, purpose="requester API bearer token")
    expected = "Bearer " + token
    app = FastAPI()
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

    @app.get("/v1/catalogue/search")
    def search_route(
        query: str = "",
        source_id: str | None = None,
        kind: str | None = None,
        data_type: str | None = None,
        tag: str | None = None,
        capability: str | None = None,
        evidence_status: str | None = None,
        max_age_seconds: int | None = None,
        limit: int = 20,
        offset: int = 0,
        match: Literal["all", "any"] = "all",
        authorization: str | None = Header(default=None),
    ):
        authorize(authorization)
        try:
            return search(
                config.state_dir,
                SearchRequest(
                    query=query,
                    source_id=source_id,
                    kind=kind,
                    data_type=data_type,
                    tag=tag,
                    capability=capability,
                    evidence_status=evidence_status,
                    max_age_seconds=max_age_seconds,
                    limit=limit,
                    offset=offset,
                    match=match,
                ),
            )
        except ValueError as error:
            raise HTTPException(status_code=409, detail={"code": str(error)}) from error

    @app.post("/v1/catalogue/search")
    def search_post_route(body: SearchRequest, authorization: str | None = Header(default=None)):
        # Same search as the GET route with the query in the body, so it is not in the URL and
        # therefore not in a server access log (user decision 2026-10-14, DEC-17).
        authorize(authorization)
        try:
            return search(config.state_dir, body)
        except ValueError as error:
            raise HTTPException(status_code=409, detail={"code": str(error)}) from error

    @app.post("/v1/catalogue/questions")
    def question_route(body: QuestionBody, authorization: str | None = Header(default=None)):
        authorize(authorization)
        if config.answer is None:
            raise HTTPException(status_code=503, detail={"code": "MODEL_NOT_CONFIGURED"})
        result = answer_question(config.state_dir, body.question, config.answer)
        if "error" in result:
            raise HTTPException(status_code=503, detail=result["error"])
        return result

    @app.post("/v1/scan-requests")
    def scan_route(body: ScanBody, authorization: str | None = Header(default=None)):
        authorize(authorization)
        if config.scan is None:
            raise HTTPException(status_code=503, detail={"code": "SCAN_NOT_CONFIGURED"})
        try:
            return dispatch_scan(body.request, config.scan, config.state_dir, body.adapter)
        except ValueError as error:
            raise HTTPException(status_code=403, detail={"code": str(error)}) from error

    @app.get("/v1/scan-requests/{request_id}")
    def status_route(request_id: str, authorization: str | None = Header(default=None)):
        authorize(authorization)
        receipt = scan_status(config.state_dir, request_id)
        if receipt is None:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND"})
        return receipt

    return app
