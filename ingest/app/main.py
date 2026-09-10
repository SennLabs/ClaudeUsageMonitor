import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import backup, db
from .otlp import extract_log_events

# uvicorn configures handlers for its own loggers but leaves the root logger
# alone, so without this every log.info() in this package is dropped — the
# backup scheduler, backup successes and the empty-session purge all report
# nothing, and only failures are ever visible.
logging.basicConfig(
    level=logging.WARNING,  # keep third-party loggers (httpx, asyncio) quiet
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
)
logging.getLogger("app").setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())

log = logging.getLogger(__name__)

AUTH_TOKEN = os.environ.get("INGEST_AUTH_TOKEN")

# How often to sweep out sessions that went inactive without logging any usage.
PURGE_INTERVAL_SECONDS = 60


async def _purge_loop() -> None:
    while True:
        try:
            removed = await asyncio.to_thread(db.purge_empty_sessions)
            if removed:
                log.info("Purged %d empty inactive session(s)", removed)
        except Exception as exc:  # never let cleanup kill the loop
            log.warning("Empty-session purge failed: %s", exc)
        await asyncio.sleep(PURGE_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    tasks = [asyncio.create_task(_purge_loop())]
    if backup.ENABLED:
        tasks.append(asyncio.create_task(backup.start_scheduler(db.DB_PATH)))
    yield
    for task in tasks:
        task.cancel()


app = FastAPI(title="Claude Usage Monitor - Ingest", lifespan=lifespan)

# Dashboard is a separate origin (Vite dev server / static host) hitting the
# /api/* routes — this is an internal tool, so allow any origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "PATCH", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)


def require_auth(request: Request) -> None:
    if not AUTH_TOKEN:
        return
    if request.headers.get("authorization") != f"Bearer {AUTH_TOKEN}":
        raise HTTPException(status_code=401, detail="Unauthorized")


def _window(hours: int) -> int | None:
    """0 or below means all time; anything else is capped at 30 days."""
    return None if hours <= 0 else min(hours, 720)


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
    return db.fetch_usage_over_time(hours=_window(hours))


@app.get("/api/usage-over-time-by-project", dependencies=[Depends(require_auth)])
async def get_usage_over_time_by_project(hours: int = 24):
    return db.fetch_usage_over_time_by_project(hours=_window(hours))


class SessionUpdate(BaseModel):
    project_name: str | None = None


@app.patch("/api/sessions/{session_id}", dependencies=[Depends(require_auth)])
async def patch_session(session_id: str, body: SessionUpdate):
    db.update_session_project(session_id, body.project_name or None)
    return {"ok": True}


# ── Users and user -> project mappings ─────────────────────────────────────

@app.get("/api/users", dependencies=[Depends(require_auth)])
async def get_users():
    return db.fetch_users()


@app.get("/api/projects", dependencies=[Depends(require_auth)])
async def get_projects():
    return db.fetch_project_names()


class UserProjectUpdate(BaseModel):
    project_name: str | None = None


@app.put("/api/user-projects/{user_id}", dependencies=[Depends(require_auth)])
async def put_user_project(user_id: str, body: UserProjectUpdate):
    """
    Link a user id to a project. Applies to every session that user already
    owns as well as every future one. An empty name removes the mapping and
    clears the label from that user's sessions.
    """
    name = (body.project_name or "").strip()
    if not name:
        db.delete_user_project(user_id, clear_sessions=True)
        return {"ok": True, "project_name": None, "sessions_updated": 0}
    updated = db.set_user_project(user_id, name)
    return {"ok": True, "project_name": name, "sessions_updated": updated}


@app.delete("/api/user-projects/{user_id}", dependencies=[Depends(require_auth)])
async def remove_user_project(user_id: str, clear_sessions: bool = False):
    db.delete_user_project(user_id, clear_sessions=clear_sessions)
    return {"ok": True}


# ── Backup endpoints ───────────────────────────────────────────────────────

@app.get("/api/backup/status", dependencies=[Depends(require_auth)])
async def get_backup_status():
    return backup.status()


@app.post("/api/backup/trigger", dependencies=[Depends(require_auth)])
async def trigger_backup():
    if not backup.DESTINATION:
        raise HTTPException(status_code=400, detail="BACKUP_DESTINATION is not configured")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: backup.run_backup(db.DB_PATH))
    return backup.status()
