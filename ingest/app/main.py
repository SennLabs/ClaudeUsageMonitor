import asyncio
import csv
import hmac
import io
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from . import backup, db
from .otlp import extract_log_events
from .otlp_metrics import extract_metric_points

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


# Observable state for the maintenance loop. A blanket `except` that only logs
# means a systematically failing sweep is invisible; this is reported by
# /api/maintenance so it can be noticed.
_maintenance: dict = {
    "last_run_at": None,
    "last_ok": None,
    "last_error": None,
    "sessions_purged": 0,
    # Events and metric points are counted apart. A 60-second metrics stream
    # produces far more rows than the event stream, so one combined figure
    # would report deleting many times more "events" than have ever existed.
    "attributes_cleared": 0,
    "events_deleted": 0,
    "metric_attributes_cleared": 0,
    "metric_points_deleted": 0,
}


async def _maintenance_loop() -> None:
    while True:
        try:
            removed = await asyncio.to_thread(db.purge_empty_sessions)
            if removed:
                log.info("Purged %d empty inactive session(s)", removed)
            retention = await asyncio.to_thread(db.apply_retention)
            if any(retention.values()):
                log.info(
                    "Retention: cleared attributes on %d event(s) and %d metric point(s); "
                    "deleted %d event(s) and %d metric point(s)",
                    retention["attributes_cleared"],
                    retention["metric_attributes_cleared"],
                    retention["events_deleted"],
                    retention["metric_points_deleted"],
                )
            _maintenance.update(
                last_ok=True,
                last_error=None,
                sessions_purged=_maintenance["sessions_purged"] + removed,
                **{
                    key: _maintenance[key] + retention[key]
                    for key in (
                        "attributes_cleared",
                        "events_deleted",
                        "metric_attributes_cleared",
                        "metric_points_deleted",
                    )
                },
            )
        except Exception as exc:  # never let cleanup kill the loop
            _maintenance.update(last_ok=False, last_error=str(exc))
            log.warning("Maintenance sweep failed: %s", exc)
        finally:
            _maintenance["last_run_at"] = datetime.now(timezone.utc).isoformat()
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
    tasks = [asyncio.create_task(_maintenance_loop())]
    if backup.ENABLED:
        tasks.append(asyncio.create_task(backup.start_scheduler(db.DB_PATH)))
    elif backup.CONFIG_ERROR:
        log.error("Backups are DISABLED: %s", backup.CONFIG_ERROR)
    yield
    for task in tasks:
        task.cancel()
    # Await the cancellations, so the process cannot exit while a backup
    # thread is mid-copy.
    await asyncio.gather(*tasks, return_exceptions=True)


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


@app.post("/v1/metrics", dependencies=[Depends(require_auth)])
async def ingest_metrics(request: Request):
    """
    Claude Code's pre-aggregated metrics stream (OTEL_METRICS_EXPORTER=otlp).

    This exists partly so it cannot 404. Clients set the *generic*
    OTEL_EXPORTER_OTLP_ENDPOINT, which applies to every signal — so the moment
    anyone enabled metrics, Claude Code posted here on every export interval
    and got a 404 back, silently, forever.
    """
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Payload too large")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Body is not valid JSON")

    try:
        points, skipped = extract_metric_points(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Malformed OTLP payload: {exc}")

    if skipped:
        log.warning("Dropped %d unusable metric point(s) from a batch", skipped)

    inserted, duplicates = await asyncio.to_thread(db.write_metric_points, points)
    if duplicates:
        log.info("Ignored %d duplicate metric point(s) — likely an exporter retry", duplicates)

    # OTLP/HTTP success response is an empty ExportMetricsServiceResponse body.
    return JSONResponse(content={}, status_code=200)


@app.get("/healthz")
async def healthz():
    """
    Unauthenticated, for container and external probes.

    It touches the database deliberately: a static 200 reported healthy while
    the file was locked, corrupt or on a full disk, which are exactly the
    situations a health check exists to catch.
    """
    try:
        await asyncio.to_thread(db.ping)
    except Exception as exc:
        log.error("Health check failed: %s", exc)
        raise HTTPException(status_code=503, detail=f"database unavailable: {exc}")
    return {"status": "ok"}


@app.get("/api/summary", dependencies=[Depends(require_auth)])
async def get_summary():
    return db.fetch_summary()


@app.get("/api/sessions", dependencies=[Depends(require_auth)])
async def get_sessions(limit: int = 100, offset: int = 0):
    """
    Sessions, newest first.

    `total` lets a caller tell that the list is truncated. Without it the
    per-project totals computed from this list silently disagreed with the
    all-time summary once there were more than 100 sessions.
    """
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    rows = db.fetch_sessions(limit=limit, offset=offset)
    return {"total": db.count_sessions(), "limit": limit, "offset": offset, "sessions": rows}


@app.get("/api/usage-by-model", dependencies=[Depends(require_auth)])
async def get_usage_by_model():
    return db.fetch_usage_by_model()


# These two are the only reads moved off the event loop, because they are the
# only ones that can run a *Python* callback per row: with a non-UTC
# displayTimeZone, bucketing calls into `local_bucket` for every matching
# event. On an all-time chart over a large table that is long enough to stall
# every other request, /healthz included — the same failure the write path was
# moved off the loop to avoid. Every other read is pure SQL and stays inline.

@app.get("/api/usage-over-time", dependencies=[Depends(require_auth)])
async def get_usage_over_time(hours: int = 24):
    return await asyncio.to_thread(db.fetch_usage_over_time, _window(hours))


@app.get("/api/usage-over-time-by-project", dependencies=[Depends(require_auth)])
async def get_usage_over_time_by_project(hours: int = 24):
    return await asyncio.to_thread(db.fetch_usage_over_time_by_project, _window(hours))


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


@app.get("/api/cache-efficiency", dependencies=[Depends(require_auth)])
async def get_cache_efficiency(hours: int = 24):
    return db.fetch_cache_efficiency(hours=_window(hours))


@app.get("/api/prompts", dependencies=[Depends(require_auth)])
async def get_prompts(hours: int = 24, limit: int = 25):
    return db.fetch_prompts(hours=_window(hours), limit=max(1, min(limit, 200)))


@app.get("/api/audit", dependencies=[Depends(require_auth)])
async def get_audit(hours: int = 168):
    return db.fetch_audit(hours=_window(hours))


@app.get("/api/metrics", dependencies=[Depends(require_auth)])
async def get_metrics(hours: int = 168):
    """
    Claude Code's own metrics, and the productivity ratios derived from them.

    Defaults to a week rather than a day: commits and pull requests are sparse
    enough that a 24h window is usually all zeroes even on an active fleet.
    """
    return db.fetch_metrics(hours=_window(hours))


@app.get("/api/export.csv", dependencies=[Depends(require_auth)])
async def export_csv(since: str | None = None, until: str | None = None):
    """
    Every event in the range as CSV, streamed.

    Bounds are ISO 8601 and compared as text against the stored timestamps —
    a bare date like 2026-09-01 works because the format sorts lexicographically.
    """
    columns = [
        "occurred_at", "event_name", "session_id", "user_id", "project_name",
        "model", "query_source", "effort", "speed",
        "input_tokens", "output_tokens", "cache_read_tokens",
        "cache_creation_tokens", "cost_usd", "duration_ms",
    ]

    def rows():
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        yield buffer.getvalue()
        for row in db.iter_export_rows(since=since, until=until):
            buffer.seek(0); buffer.truncate(0)
            writer.writerow(row)
            yield buffer.getvalue()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return StreamingResponse(
        rows(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="claude-usage-{stamp}.csv"'},
    )


@app.get("/api/maintenance", dependencies=[Depends(require_auth)])
async def get_maintenance():
    """State of the background sweep, so a persistent failure is visible."""
    return {
        **_maintenance,
        "interval_seconds": PURGE_INTERVAL_SECONDS,
        "active_window_minutes": db.ACTIVE_WINDOW_MINUTES,
        "retention_days": db.RETENTION_DAYS or None,
        "raw_attributes_retention_days": db.RAW_ATTRIBUTES_RETENTION_DAYS or None,
    }


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
