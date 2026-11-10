"""The contracts service: the IR schemas, and a structure check for one record (report Part 3.2;
lead ruling D5 (a)). For programs that do not run this Python. It holds no signing key, so it
cannot verify a signature, and it has no volume and no outbound path.

    dbig-contracts serve --host 172.30.0.14 --port 8443 --tls-cert c.crt --tls-key c.key

Needs the `api` extra and a caller key file in `DBIG_CALLER_KEYS_FILE` (scope `validate`).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Annotated, Any

from fastapi import Body, FastAPI, HTTPException
from pydantic import ValidationError

from .errors import describe_validation
from .schemas import ADAPTERS, json_schemas
from .security import KEYS_FILE_ENV, SecretReferenceError, load_caller_keys, serve_refusal
from .service import ServiceGuard

MAX_REQUEST_BYTES = 1_048_576
MAX_REQUESTS_PER_MINUTE = 120


def create_app(keys_file: Path) -> FastAPI:
    schemas = json_schemas()
    app = FastAPI(title="DBIG contracts", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(
        ServiceGuard,
        keys=load_caller_keys(keys_file),
        route_scopes={"/v1/contracts/": "validate"},
        max_request_bytes=MAX_REQUEST_BYTES,
        max_requests_per_minute=MAX_REQUESTS_PER_MINUTE,
    )

    @app.get("/v1/contracts/schemas")
    def schema_names() -> dict:
        return {"schemas": sorted(schemas)}

    @app.get("/v1/contracts/schemas/{name}")
    def schema(name: str) -> dict:
        if name not in schemas:
            raise HTTPException(status_code=404, detail={"code": "UNKNOWN_SCHEMA"})
        return schemas[name]

    @app.post("/v1/contracts/validate/{record_type}")
    def validate(record_type: str, record: Annotated[Any, Body()]) -> dict:
        if record_type not in ADAPTERS:
            raise HTTPException(status_code=404, detail={"code": "UNKNOWN_SCHEMA"})
        try:
            ADAPTERS[record_type].validate_python(record)
        except ValidationError as error:
            # Locations and rules only: the record's own values are never echoed back.
            raise HTTPException(
                status_code=422,
                detail={"code": "INVALID_RECORD", "problems": describe_validation(error)},
            ) from None
        return {"valid": True}

    return app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="dbig-contracts")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="serve the schemas and the record check")
    serve.add_argument("--host", required=True)
    serve.add_argument("--port", type=int, required=True)
    serve.add_argument("--tls-cert", type=Path)
    serve.add_argument("--tls-key", type=Path)
    arguments = parser.parse_args(argv)
    if refusal := serve_refusal(arguments.host, arguments.tls_cert, arguments.tls_key):
        parser.error(refusal)
    keys_file = os.environ.get(KEYS_FILE_ENV)
    if not keys_file:
        parser.error(f"set {KEYS_FILE_ENV} to the caller key file")
    try:
        app = create_app(Path(keys_file))
    except SecretReferenceError as error:
        print(f"dbig-contracts: {error}", file=sys.stderr)
        raise SystemExit(2) from None
    import uvicorn

    uvicorn.run(
        app,
        host=arguments.host,
        port=arguments.port,
        ssl_certfile=arguments.tls_cert,
        ssl_keyfile=arguments.tls_key,
    )
