from __future__ import annotations

from fastapi import FastAPI, HTTPException

from . import __version__
from .doctor import run_doctor
from .service import MusicMaterialService


def build_app(service: MusicMaterialService | None = None) -> FastAPI:
    service = service or MusicMaterialService()
    app = FastAPI(title="Music Material Agent", version=__version__)

    @app.get("/health")
    def health() -> dict:
        return run_doctor(service.paths)

    @app.get("/jobs")
    def list_jobs(limit: int = 50) -> list[dict]:
        return service.list_jobs(limit)

    @app.get("/jobs/{job_id}")
    def job_status(job_id: str) -> dict:
        try:
            return service.job_status(job_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/jobs/{job_id}/rows")
    def get_rows(job_id: str, offset: int = 0, limit: int = 100) -> dict:
        try:
            return service.get_rows(job_id, offset=offset, limit=limit)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    return app


app = build_app()
