import asyncio
import hmac
import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
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

AUTH_TOKEN = os.environ.get("INGEST_AUTH_TOKEN", "").strip()

# Running without a token leaves every route, including POST /v1/logs, open to
# anything that can reach the port. That is a legitimate choice on a trusted
# network, but it must be a choice — an unset variable (a missing .env, a typo
# in the name) used to produce a wide-open service that looked perfectly
# healthy, with no signal anywhere.
ALLOW_ANONYMOUS = os.environ.get("INGEST_ALLOW_ANONYMOUS", "").strip().lower() in {
    "1", "true", "yes",
}

if not AUTH_TOKEN and not ALLOW_ANONYMOUS:
    raise RuntimeError(
        "INGEST_AUTH_TOKEN is not set. Set it to a random secret (for example "
        "`python3 -c \"import secrets; print(secrets.token_hex(20))\"`), or set "
        "INGEST_ALLOW_ANONYMOUS=true to deliberately run with authentication "
        "disabled on a trusted network."
    )

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
    if not AUTH_TOKEN:
        log.warning(
            "AUTHENTICATION DISABLED — INGEST_ALLOW_ANONYMOUS is set and no token "
            "is configured. Every route, including POST /v1/logs, is open to "
            "anything that can reach this port."
        )
    db.init_db()
    tasks = [asyncio.create_task(_purge_loop())]
    if backup.ENABLED:
        tasks.append(asyncio.create_task(backup.start_scheduler(db.DB_PATH)))
    yield
    for task in tasks:
        task.cancel()


app = FastAPI(
    title="Claude Usage Monitor - Ingest",
    lifespan=lifespan,
    # The interactive docs are unauthenticated by default and this port has to
    # be reachable from every reporting dev container. Nothing needs them in
    # production; re-enable locally if you want to browse the schema.
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# No CORS middleware, deliberately — do not add one back without reading this.
#
# Both consumers are same-origin: nginx proxies /api/* to this service in
# production, and the Vite dev server proxies /api to it in development. A
# browser never talks to this port cross-origin.
#
# It previously ran allow_origins=["*"], which combined badly with nginx
# injecting the bearer token server-side: any page in any browser on the
# network could call the dashboard's /api/* and read the response, because the
# credential came from nginx rather than the caller, and the wildcard let the
# script read the body.


def require_auth(request: Request) -> None:
    if not AUTH_TOKEN:
        return  # anonymous mode, explicitly opted into at startup
    supplied = request.headers.get("authorization", "")
    if not hmac.compare_digest(supplied, f"Bearer {AUTH_TOKEN}"):
        raise HTTPException(status_code=401, detail="Unauthorized")


def _window(hours: int) -> int | None:
    """0 or below means all time; anything else is capped at 30 days."""
    return None if hours <= 0 else min(hours, 720)


# Guard against an unbounded body: request.json() buffers the whole thing, and
# this route is not proxied through nginx, so nginx's client_max_body_size
# never applies to it.
MAX_BODY_BYTES = int(os.environ.get("MAX_LOG_BODY_BYTES", str(32 * 1024 * 1024)))


@app.post("/v1/logs", dependencies=[Depends(require_auth)])
async def ingest_logs(request: Request):
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Payload too large")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Body is not valid JSON")

    try:
        events, skipped = extract_log_events(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Malformed OTLP payload: {exc}")

    if skipped:
        log.warning("Dropped %d unparseable record(s) from a batch", skipped)

    # Blocking SQLite, moved off the event loop: a large batch used to stall
    # every other request, including /healthz, for the duration of the write.
    inserted, duplicates = await asyncio.to_thread(db.write_events, events)
    if duplicates:
        log.info("Ignored %d duplicate event(s) — likely an exporter retry", duplicates)

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


# ── Insights ───────────────────────────────────────────────────────────────

@app.get("/api/attribution", dependencies=[Depends(require_auth)])
async def get_attribution(hours: int = 24):
    return db.fetch_attribution(hours=_window(hours))


@app.get("/api/rate", dependencies=[Depends(require_auth)])
async def get_rate(minutes: int = 60):
    """Spend over a true trailing window, for the cost alert."""
    return db.fetch_spend_rate(minutes=max(1, min(minutes, 1440)))


@app.get("/api/latency", dependencies=[Depends(require_auth)])
async def get_latency(hours: int = 24):
    return db.fetch_latency(hours=_window(hours))


@app.get("/api/errors", dependencies=[Depends(require_auth)])
async def get_errors(hours: int = 24):
    return db.fetch_errors(hours=_window(hours))


@app.get("/api/tools", dependencies=[Depends(require_auth)])
async def get_tools(hours: int = 24):
    return db.fetch_tool_stats(hours=_window(hours))


@app.get("/api/fleet", dependencies=[Depends(require_auth)])
async def get_fleet():
    return db.fetch_fleet()


# ── Dashboard settings ─────────────────────────────────────────────────────

@app.get("/api/settings", dependencies=[Depends(require_auth)])
async def get_settings():
    return db.fetch_settings()


@app.put("/api/settings", dependencies=[Depends(require_auth)])
async def put_settings(patch: dict):
    """
    Merge a partial settings update. Unknown and read-only keys are ignored
    rather than rejected, so a newer dashboard talking to an older server
    degrades instead of failing.
    """
    try:
        return db.update_settings(patch)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/budget", dependencies=[Depends(require_auth)])
async def get_budget(cycle_day: int | None = None):
    """Spend since the start of the current billing period."""
    if cycle_day is None:
        cycle_day = int(db.fetch_settings()["billingCycleDay"])
    return db.fetch_budget_usage(cycle_day)


# ── Backup endpoints ───────────────────────────────────────────────────────

@app.get("/api/backup/status", dependencies=[Depends(require_auth)])
async def get_backup_status():
    return backup.status()


@app.post("/api/backup/trigger", dependencies=[Depends(require_auth)])
async def trigger_backup():
    if not backup.DESTINATION:
        raise HTTPException(status_code=400, detail="BACKUP_DESTINATION is not configured")
    if backup.CONFIG_ERROR:
        raise HTTPException(status_code=400, detail=backup.CONFIG_ERROR)
    try:
        await asyncio.to_thread(backup.run_backup, db.DB_PATH)
    except backup.BackupBusy:
        raise HTTPException(status_code=409, detail="A backup is already running")

    result = backup.status()
    if result["last_backup_ok"] is False:
        # The run happened and failed. Returning 200 here meant the UI showed a
        # success toast while the backup had not been written.
        raise HTTPException(status_code=500, detail=result["last_backup_error"])
    return result
