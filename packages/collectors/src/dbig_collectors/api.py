"""Private collector HTTP adapter; all requests pass through local scan policy."""

import hmac
from pathlib import Path

from dbig_contracts.scan import ScanReceipt, SignedScanRequest
from dbig_contracts.security import secret_from_env
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from .config import SourceConfig
from .scan import accept_scan, scan_status


def create_app(config: SourceConfig, queue_path: Path) -> FastAPI:
    token = secret_from_env(config.http_token_env, purpose="collector HTTP bearer token")
    expected = "Bearer " + token
    app = FastAPI()

    @app.middleware("http")
    async def request_size(request: Request, call_next):
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
        return await call_next(request)

    def authorize(value: str | None) -> None:
        if value is None or not hmac.compare_digest(value, expected):
            raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED"})

    @app.post("/v1/collector/scan-requests", response_model=ScanReceipt)
    def post_scan(envelope: SignedScanRequest, authorization: str | None = Header(default=None)):
        authorize(authorization)
        try:
            return accept_scan(envelope, config, queue_path)
        except ValueError as error:
            raise HTTPException(status_code=403, detail={"code": str(error)}) from error

    @app.get("/v1/collector/scan-requests/{request_id}", response_model=ScanReceipt)
    def get_scan(request_id: str, authorization: str | None = Header(default=None)):
        authorize(authorization)
        receipt = scan_status(queue_path, request_id)
        if receipt is None:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND"})
        return receipt

    return app
