"""The builder service: an assembly job over bundles already on the shared volume (report Part
3.2). The caller runs each pipeline step (lead ruling D3 (a)): it starts an assembly here, then
asks the requester to reindex and the planner to rebuild its graph.

Each finished assembly is a new snapshot directory under `snapshots_root`, and `current` links
to the newest, so a reader configured with `<snapshots_root>/current` sees it (and reports
STALE_INDEX until it reindexes).
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import threading
from pathlib import Path

from dbig_contracts import jobs
from dbig_contracts.artifact import read_artifact
from dbig_contracts.errors import OperatorError
from dbig_contracts.security import service_keys
from dbig_contracts.service import ServiceGuard
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .builder import assemble
from .config import BuilderConfig, ServiceConfig

CURRENT = "current"
# A bundle is named by its directory under `bundles_root`; nothing that could climb out of it.
BUNDLE_NAME = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"


class AssemblyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bundles: list[str] = Field(min_length=1, max_length=64)


def _service(config: BuilderConfig) -> ServiceConfig:
    if config.service is None:
        raise OperatorError("dbig-builder serve needs a [builder.service] section in the config")
    return config.service


def create_app(config: BuilderConfig) -> FastAPI:
    service = _service(config)
    app = FastAPI(title="DBIG catalog builder")
    app.add_middleware(
        ServiceGuard,
        keys=service_keys(service.api_key_env, purpose="builder API bearer token"),
        route_scopes={"/v1/builder/": "assemble"},
        max_request_bytes=service.max_request_bytes,
        max_requests_per_minute=service.max_requests_per_minute,
    )

    @app.post("/v1/builder/assemblies", status_code=202)
    def start(body: AssemblyBody) -> dict:
        root = service.bundles_root.resolve()
        for name in body.bundles:
            if not _named(name) or not (root / name / "manifest.json").is_file():
                raise HTTPException(status_code=404, detail={"code": "BUNDLE_NOT_FOUND"})
        return {"id": jobs.submit(service.jobs_db, {"bundles": body.bundles}), "status": "queued"}

    @app.get("/v1/builder/assemblies/{job_id}")
    def status(job_id: str) -> dict:
        found = jobs.status(service.jobs_db, job_id)
        if found is None:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND"})
        return found

    return app


def _named(name: str) -> bool:
    return re.fullmatch(BUNDLE_NAME, name) is not None


def _point_current(snapshots_root: Path, name: str) -> None:
    """Swap `current` to `name` in one rename, so a reader never sees a missing link."""
    link = snapshots_root / CURRENT
    staged = snapshots_root / f".{CURRENT}.{name}"
    staged.unlink(missing_ok=True)
    staged.symlink_to(name, target_is_directory=True)
    os.replace(staged, link)


def run_one(config: BuilderConfig) -> bool:
    """Assemble the oldest queued job; False when the queue is empty."""
    service = _service(config)
    claimed = jobs.claim(service.jobs_db)
    if claimed is None:
        return False
    job_id, payload = claimed
    root = service.bundles_root.resolve()
    destination = service.snapshots_root / job_id
    try:
        # A killed write leaves only its hidden temporary folder; one worker per queue file, so no
        # other writer owns this job's leftovers.
        for leftover in service.snapshots_root.glob(f".{job_id}-*"):
            shutil.rmtree(leftover)
        if destination.exists():
            # A requeued job whose worker crashed after publishing: `write_artifact` publishes by
            # one rename after the manifest, so the folder is complete; reading it checks that.
            manifest, _ = read_artifact(destination)
        else:
            manifest = assemble([root / name for name in payload["bundles"]], destination, config)
        _point_current(service.snapshots_root, job_id)
    except OperatorError as error:
        jobs.finish(service.jobs_db, job_id, result=None, error=str(error)[:1024])
    except Exception as error:
        # The class only: a message can carry a path or a value from a bundle.
        jobs.finish(service.jobs_db, job_id, result=None, error=type(error).__name__)
    else:
        jobs.finish(
            service.jobs_db,
            job_id,
            result={"snapshot": job_id, "completeness": manifest.completeness},
            error=None,
        )
    return True


def run_worker(config: BuilderConfig, stop: threading.Event) -> None:
    service = _service(config)
    jobs.requeue_running(service.jobs_db, service.max_crash_requeues)
    while not stop.is_set():
        try:
            busy = run_one(config)
        except Exception:
            logging.getLogger(__name__).error("assembly worker step failed")
            busy = False
        if not busy:
            stop.wait(service.poll_seconds)
