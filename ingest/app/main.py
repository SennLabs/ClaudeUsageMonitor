import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import db
from .otlp import extract_log_events

AUTH_TOKEN = os.environ.get("INGEST_AUTH_TOKEN")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="Claude Usage Monitor - Ingest", lifespan=lifespan)

# Dashboard is a separate origin (Vite dev server / static host) hitting the
# read-only /api/* routes - this is an internal tool, so allow any origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "PATCH"],
    allow_headers=["*"],
)


def require_auth(request: Request) -> None:
    if not AUTH_TOKEN:
        return
    if request.headers.get("authorization") != f"Bearer {AUTH_TOKEN}":
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.post("/v1/logs", dependencies=[Depends(require_auth)])
async def ingest_logs(request: Request):
    body = await request.json()

    for event in extract_log_events(body):
        if event.session_id:
            db.upsert_session(
                event.session_id,
                event.occurred_at,
                user_id=event.user_id,
                organization_id=event.organization_id,
            )
        db.insert_event(event)

    # OTLP/HTTP success response is an empty ExportLogsServiceResponse body.
    return JSONResponse(content={}, status_code=200)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/api/summary", dependencies=[Depends(require_auth)])
async def get_summary():
    return db.fetch_summary()


@app.get("/api/sessions", dependencies=[Depends(require_auth)])
async def get_sessions():
    return db.fetch_sessions()


@app.get("/api/usage-by-model", dependencies=[Depends(require_auth)])
async def get_usage_by_model():
    return db.fetch_usage_by_model()


@app.get("/api/usage-over-time", dependencies=[Depends(require_auth)])
async def get_usage_over_time(hours: int = 24):
    return db.fetch_usage_over_time(hours=min(hours, 720))  # cap at 30 days


@app.get("/api/usage-over-time-by-project", dependencies=[Depends(require_auth)])
async def get_usage_over_time_by_project(hours: int = 24):
    return db.fetch_usage_over_time_by_project(hours=min(hours, 720))


class SessionUpdate(BaseModel):
    project_name: str | None = None


@app.patch("/api/sessions/{session_id}", dependencies=[Depends(require_auth)])
async def patch_session(session_id: str, body: SessionUpdate):
    db.update_session_project(session_id, body.project_name or None)
    return {"ok": True}
