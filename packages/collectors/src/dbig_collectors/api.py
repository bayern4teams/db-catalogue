"""Private collector HTTP adapter; all requests pass through local scan policy."""

from pathlib import Path

from dbig_contracts.scan import ScanReceipt, SignedScanRequest
from dbig_contracts.security import service_keys
from dbig_contracts.service import ServiceGuard
from fastapi import FastAPI, HTTPException

from .config import SourceConfig
from .scan import accept_scan, scan_status


def create_app(config: SourceConfig, queue_path: Path) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None)
    app.add_middleware(
        ServiceGuard,
        keys=service_keys(config.http_token_env, purpose="collector HTTP bearer token"),
        route_scopes={"/v1/collector/": "scan"},
        max_request_bytes=config.max_request_bytes,
    )

    @app.post("/v1/collector/scan-requests", response_model=ScanReceipt)
    def post_scan(envelope: SignedScanRequest):
        try:
            return accept_scan(envelope, config, queue_path)
        except ValueError as error:
            raise HTTPException(status_code=403, detail={"code": str(error)}) from error

    @app.get("/v1/collector/scan-requests/{request_id}", response_model=ScanReceipt)
    def get_scan(request_id: str):
        receipt = scan_status(queue_path, request_id)
        if receipt is None:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND"})
        return receipt

    return app
