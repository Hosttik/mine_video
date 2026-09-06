import hmac
import json
import time

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .models import JobSpec
from .store import Conflict, Store


def create_app(config):
    if len(config.api_token) < 24:
        raise RuntimeError("Run init or configure a strong MV_API_TOKEN (24+ characters)")
    app = FastAPI(title="Mine Video", version="0.1.0", description="Production queue for real Minecraft footage")
    store = Store(config.database)
    bearer = HTTPBearer(auto_error=False)

    def authorize(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if credentials is None or not hmac.compare_digest(credentials.credentials.encode(), config.api_token.encode()):
            raise HTTPException(401, "Invalid bearer token", headers={"WWW-Authenticate": "Bearer"})

    def get_job(job_id):
        job = store.get(job_id)
        if job is None:
            raise HTTPException(404, "Job not found")
        job["downloads"] = {name: f"/jobs/{job_id}/artifacts/{name}" for name in job["artifacts"]}
        return job

    @app.get("/health")
    def health():
        try:
            heartbeat = json.loads((config.data_dir / "worker.json").read_text())
            worker_ready = time.time() - heartbeat["at"] < 15
        except (OSError, ValueError, KeyError):
            worker_ready = False
        return {"api": "ok", "worker_ready": worker_ready}

    @app.get("/templates", dependencies=[Depends(authorize)])
    def templates():
        return [
            {"id": "mob_arena", "description": "One iron golem versus a seeded husk horde"},
            {"id": "tnt_chain", "description": "Destruction with a TNT chain and a second salvo"},
            {"id": "tower_build", "description": "A tower assembled layer by layer in the game world"},
        ]

    @app.post("/jobs", status_code=202, dependencies=[Depends(authorize)])
    def submit(spec: JobSpec, idempotency_key: str | None = Header(default=None)):
        try:
            return store.submit(spec, idempotency_key)
        except Conflict as error:
            raise HTTPException(409, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.get("/jobs", dependencies=[Depends(authorize)])
    def jobs(limit: int = Query(default=50, ge=1, le=200)):
        return store.list(limit)

    @app.get("/jobs/{job_id}", dependencies=[Depends(authorize)])
    def job(job_id: str):
        return get_job(job_id)

    @app.get("/jobs/{job_id}/events", dependencies=[Depends(authorize)])
    def events(job_id: str):
        get_job(job_id)
        return store.events(job_id)

    @app.post("/jobs/{job_id}/cancel", dependencies=[Depends(authorize)])
    def cancel(job_id: str):
        try:
            return store.cancel(job_id)
        except KeyError as error:
            raise HTTPException(404, "Job not found") from error

    @app.post("/jobs/{job_id}/retry", status_code=202, dependencies=[Depends(authorize)])
    def retry(job_id: str):
        previous = get_job(job_id)
        if previous["state"] not in {"failed", "interrupted", "cancelled"}:
            raise HTTPException(409, "Only failed, interrupted, or cancelled jobs can be retried")
        return store.submit(JobSpec.model_validate(previous["spec"]))

    @app.get("/jobs/{job_id}/artifacts/{name}", dependencies=[Depends(authorize)])
    def artifact(job_id: str, name: str):
        job = get_job(job_id)
        if job["state"] != "succeeded" or name not in job["artifacts"]:
            raise HTTPException(404, "Artifact not available")
        directory = (config.data_dir / "jobs" / job_id).resolve()
        path = (directory / job["artifacts"][name]).resolve()
        if not path.is_relative_to(directory) or not path.is_file():
            raise HTTPException(404, "Artifact not available")
        return FileResponse(path, filename=path.name)

    return app
