"""Authenticated requester REST surface over the same application functions as CLI."""

from __future__ import annotations

from typing import Literal

from dbig_contracts.scan import ScanRequest
from dbig_contracts.security import service_keys
from dbig_contracts.service import ServiceGuard
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .answer import answer_question
from .config import RequesterConfig
from .scan import CollectorUnavailable, dispatch_scan, scan_status
from .search import SearchRequest, index_snapshot, search

# The scope a caller key needs, by path prefix (report Part 3, layer L5).
ROUTE_SCOPES = {
    "/v1/catalogue/search": "search",
    "/v1/catalogue/questions": "ask",
    "/v1/catalogue/reindex": "reindex",
    "/v1/scan-requests": "scan",
}


class QuestionBody(BaseModel):
    question: str = Field(min_length=1, max_length=1024)


class QuestionResponse(BaseModel):
    """A grounded answer. Only `relationships` states how tables link (B3, owner D8)."""

    model_config = ConfigDict(extra="allow")

    answer: str = Field(
        description="The model's own text about the retrieved metadata. Not a catalogue fact: "
        "a link it describes is established only by an entry in relationships."
    )
    citations: list[str] = Field(description="The retrieved IDs the answer cites.")
    relationships: list[dict] = Field(
        description="The only catalogue facts about links: one entry per cited stored edge, "
        "written by code from that edge (names, columns, status, metadata-only evidence)."
    )


class ScanBody(BaseModel):
    request: ScanRequest
    adapter: Literal["bundle", "http"]


def create_app(config: RequesterConfig) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None)
    app.add_middleware(
        ServiceGuard,
        keys=service_keys(config.api_key_env, purpose="requester API bearer token"),
        route_scopes=ROUTE_SCOPES,
        max_request_bytes=config.max_request_bytes,
        max_requests_per_minute=config.max_requests_per_minute,
    )

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
    ):
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
    def search_post_route(body: SearchRequest):
        # Same search as the GET route with the query in the body, so it is not in the URL and
        # therefore not in a server access log (user decision 2026-10-14, DEC-17).
        try:
            return search(config.state_dir, body)
        except ValueError as error:
            raise HTTPException(status_code=409, detail={"code": str(error)}) from error

    @app.post("/v1/catalogue/questions", response_model=QuestionResponse)
    def question_route(body: QuestionBody):
        if config.answer is None:
            raise HTTPException(status_code=503, detail={"code": "MODEL_NOT_CONFIGURED"})
        result = answer_question(config.state_dir, body.question, config.answer)
        if "error" in result:
            raise HTTPException(status_code=503, detail=result["error"])
        return result

    @app.post("/v1/scan-requests")
    def scan_route(body: ScanBody):
        if config.scan is None:
            raise HTTPException(status_code=503, detail={"code": "SCAN_NOT_CONFIGURED"})
        try:
            return dispatch_scan(body.request, config.scan, config.state_dir, body.adapter)
        except CollectorUnavailable as error:
            raise HTTPException(status_code=502, detail={"code": str(error)}) from error
        except ValueError as error:
            raise HTTPException(status_code=403, detail={"code": str(error)}) from error

    @app.get("/v1/scan-requests/{request_id}")
    def status_route(request_id: str):
        receipt = scan_status(config.state_dir, request_id)
        if receipt is None:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND"})
        return receipt

    @app.post("/v1/catalogue/reindex")
    def reindex_route():
        # Step 4 of the service flow (report Part 3.5): after the builder writes a snapshot, the
        # caller asks for a reindex; until then search answers STALE_INDEX.
        try:
            digest = index_snapshot(config.snapshot_dir, config.state_dir, config.exclude_objects)
        except ValueError as error:
            raise HTTPException(status_code=409, detail={"code": str(error)}) from error
        return {"snapshot_digest": digest}

    return app
