import hashlib
import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .otlp import LogEvent

log = logging.getLogger(__name__)

# DB_PATH is overridable so a container can point it at a mounted volume
# (e.g. /data/usage.db) instead of the package directory.
DB_PATH = Path(os.environ.get("DB_PATH", str(Path(__file__).resolve().parent.parent / "usage.db")))
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"

# How long a session may go quiet before it stops counting as active. Also the
# grace period before an empty session is purged (see purge_empty_sessions).
ACTIVE_WINDOW_MINUTES = int(os.environ.get("ACTIVE_WINDOW_MINUTES", "15"))

# Under WAL all writers serialize. A timeout surfaces as a 500, which an OTLP
# exporter retries — so wait rather than fail.
BUSY_TIMEOUT_SECONDS = float(os.environ.get("DB_BUSY_TIMEOUT_SECONDS", "15"))

# SQLite's default SQLITE_MAX_VARIABLE_NUMBER is 999 on some builds.
_MAX_SQL_VARIABLES = 900


@contextmanager
def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=BUSY_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute(f"PRAGMA busy_timeout = {int(BUSY_TIMEOUT_SECONDS * 1000)}")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(SCHEMA_PATH.read_text())
        # Migrate existing databases that predate schema additions. Additive
        # and nullable only; re-running is a no-op.
        for stmt in (
            "ALTER TABLE sessions ADD COLUMN project_name TEXT",
            "ALTER TABLE usage_events ADD COLUMN cost_usd_micros INTEGER",
            "ALTER TABLE usage_events ADD COLUMN event_hash TEXT",
            "ALTER TABLE sessions ADD COLUMN project_source TEXT",
        ):
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError:
                pass  # column already exists

        # Derive the exact integer cost for rows that predate the column.
        conn.execute(
            """
            UPDATE usage_events
               SET cost_usd_micros = CAST(ROUND(cost_usd * 1000000) AS INTEGER)
             WHERE cost_usd_micros IS NULL AND cost_usd IS NOT NULL
            """
        )
        conn.execute(
            """
            UPDATE sessions
               SET project_source = CASE
                     WHEN user_id IN (SELECT user_id FROM user_projects) THEN 'user_map'
                     ELSE 'manual'
                   END
             WHERE project_name IS NOT NULL AND project_source IS NULL
            """
        )
        _backfill_event_hashes(conn)

    _ensure_dedupe_index()


def _backfill_event_hashes(conn: sqlite3.Connection) -> None:
    """Fill event_hash for rows written before the column existed."""
    rows = conn.execute(
        """
        SELECT id, session_id, occurred_at, event_name, raw_attributes
          FROM usage_events
         WHERE event_hash IS NULL
        """
    ).fetchall()
    if not rows:
        return
    conn.executemany(
        "UPDATE usage_events SET event_hash = ? WHERE id = ?",
        [
            (
                _hash_parts(r["session_id"], r["occurred_at"], r["event_name"], r["raw_attributes"]),
                r["id"],
            )
            for r in rows
        ],
    )
    log.info("Backfilled event_hash for %d existing event(s)", len(rows))


def _ensure_dedupe_index() -> None:
    """
    Create the UNIQUE index that makes INSERT OR IGNORE deduplicate.

    Kept out of schema.sql and out of the migration transaction on purpose: an
    existing database may already contain duplicates from retried batches, and
    refusing to start over that would be a worse failure than running without
    dedupe. Warn, keep serving, and let the operator clean up deliberately with
    dedupe.py.
    """
    with _connect() as conn:
        try:
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_usage_events_hash "
                "ON usage_events(event_hash)"
            )
        except sqlite3.IntegrityError:
            duplicates = conn.execute(
                """
                SELECT COALESCE(SUM(n - 1), 0) AS extra FROM (
                    SELECT COUNT(*) AS n FROM usage_events
                     WHERE event_hash IS NOT NULL
                     GROUP BY event_hash HAVING n > 1
                )
                """
            ).fetchone()["extra"]
            log.warning(
                "Event de-duplication is INACTIVE: %d duplicate event row(s) already "
                "exist, so the unique index could not be created. Retried batches will "
                "keep inflating totals until this is resolved. Review and clean up with "
                "`python dedupe.py` (it reports before it deletes, and takes --apply).",
                duplicates,
            )


def _hash_parts(session_id, occurred_at, event_name, raw_attributes) -> str:
    """
    Identity of one telemetry record.

    Includes the full attribute map, so any difference at all between two
    records makes them distinct. A retried export resends byte-identical
    records, which is exactly what this catches.
    """
    payload = "\x1f".join(
        "" if part is None else str(part)
        for part in (session_id, occurred_at, event_name, raw_attributes)
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def event_hash(event: LogEvent) -> str:
    return _hash_parts(
        event.session_id,
        event.occurred_at,
        event.event_name,
        json.dumps(event.raw_attributes, sort_keys=True),
    )


def _cutoff(minutes: int) -> str:
    """
    UTC cutoff timestamp in the exact format occurred_at/last_seen_at are stored
    in (Python's datetime.isoformat with a +00:00 offset).

    Timestamps are TEXT, so comparisons are lexicographic. Comparing them against
    SQLite's datetime() output would be wrong — that uses a space separator, and
    'T' > ' ', so every same-day row would compare as newer than the cutoff.
    Generating the bound in the stored format keeps the comparison correct *and*
    lets it use the index on occurred_at.
    """
    ts = datetime.now(timezone.utc).timestamp() - minutes * 60
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Writes ─────────────────────────────────────────────────────────────────

def write_events(events: list[LogEvent]) -> tuple[int, int]:
    """
    Write a whole batch in ONE connection and ONE transaction.

    Returns (inserted, duplicates_ignored).

    Previously each record opened its own connection, set WAL, committed and
    closed — two connect/commit/close cycles per record, on the event loop
    thread. A 200-record batch cost ~1.65s of event-loop starvation, during
    which /healthz went unanswered and the container healthcheck could restart
    the process mid-write.

    One transaction also makes the batch atomic: a failure partway through no
    longer leaves earlier records committed for the exporter's retry to
    duplicate.
    """
    if not events:
        return (0, 0)

    # Collapse per-session updates so an out-of-order batch cannot move
    # last_seen_at backwards, and first_seen_at reflects the earliest record.
    sessions: dict[str, dict] = {}
    for event in events:
        if not event.session_id:
            continue
        current = sessions.get(event.session_id)
        if current is None:
            sessions[event.session_id] = {
                "first": event.occurred_at,
                "last": event.occurred_at,
                "user_id": event.user_id,
                "organization_id": event.organization_id,
                "project": event.project_name,
            }
        else:
            current["first"] = min(current["first"], event.occurred_at)
            current["last"] = max(current["last"], event.occurred_at)
            current["user_id"] = current["user_id"] or event.user_id
            current["organization_id"] = current["organization_id"] or event.organization_id
            current["project"] = current["project"] or event.project_name

    with _connect() as conn:
        # Resolve any user->project mappings for this batch in one query, so the
        # per-row SQL stays readable.
        user_ids = {s["user_id"] for s in sessions.values() if s["user_id"]}
        mapped: dict[str, str] = {}
        if user_ids:
            marks = ",".join("?" * len(user_ids))
            mapped = {
                row["user_id"]: row["project_name"]
                for row in conn.execute(
                    f"SELECT user_id, project_name FROM user_projects WHERE user_id IN ({marks})",
                    list(user_ids),
                )
            }

        rows = []
        for sid, s in sessions.items():
            if s["project"]:
                project, source = s["project"], "resource"
            elif s["user_id"] and mapped.get(s["user_id"]):
                project, source = mapped[s["user_id"]], "user_map"
            else:
                project, source = None, None
            rows.append(
                (sid, s["user_id"], s["organization_id"], s["first"], s["last"], project, source)
            )

        conn.executemany(
            """
            INSERT INTO sessions (
                session_id, user_id, organization_id, first_seen_at, last_seen_at,
                project_name, project_source
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                last_seen_at = MAX(sessions.last_seen_at, excluded.last_seen_at),
                first_seen_at = MIN(sessions.first_seen_at, excluded.first_seen_at),
                user_id = COALESCE(sessions.user_id, excluded.user_id),
                organization_id = COALESCE(sessions.organization_id, excluded.organization_id),

                -- Project precedence, highest first:
                --   'manual'   someone tagged this specific session; never overridden
                --   'resource' the container declared it; authoritative and re-applied
                --              on every event, so fixing a container's config fixes
                --              its in-flight sessions too
                --   'user_map' a user.id mapping; only ever fills a gap
                project_name = CASE
                    WHEN sessions.project_source = 'manual' THEN sessions.project_name
                    WHEN excluded.project_source = 'resource' THEN excluded.project_name
                    ELSE COALESCE(sessions.project_name, excluded.project_name)
                END,
                project_source = CASE
                    WHEN sessions.project_source = 'manual' THEN 'manual'
                    WHEN excluded.project_source = 'resource' THEN 'resource'
                    WHEN sessions.project_name IS NOT NULL THEN sessions.project_source
                    ELSE excluded.project_source
                END
            """,
            rows,
        )

        before = conn.total_changes
        conn.executemany(
            """
            INSERT OR IGNORE INTO usage_events (
                session_id, occurred_at, event_name, model,
                input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens,
                cost_usd, cost_usd_micros, raw_attributes, event_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    event.session_id,
                    event.occurred_at,
                    event.event_name,
                    event.model,
                    event.input_tokens,
                    event.output_tokens,
                    event.cache_read_tokens,
                    event.cache_creation_tokens,
                    event.cost_usd,
                    event.cost_usd_micros,
                    json.dumps(event.raw_attributes, sort_keys=True),
                    event_hash(event),
                )
                for event in events
            ],
        )
        inserted = conn.total_changes - before

    return (inserted, len(events) - inserted)


def update_session_project(session_id: str, project_name: str | None) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE sessions SET project_name = ?, project_source = ? WHERE session_id = ?",
            (project_name, "manual" if project_name else None, session_id),
        )


# ── Empty-session cleanup ──────────────────────────────────────────────────

# A session that only ever emitted zero-token, zero-cost events — Claude Code
# was started in a container but never actually used. Worth showing while it is
# live (so you can see the container came up), worthless once it goes quiet.
_EMPTY_SESSION_IDS = """
    SELECT s.session_id
    FROM sessions s
    LEFT JOIN usage_events e ON e.session_id = s.session_id
    WHERE s.last_seen_at < ?
    GROUP BY s.session_id
    HAVING COALESCE(SUM(COALESCE(e.input_tokens, 0)
                      + COALESCE(e.output_tokens, 0)
                      + COALESCE(e.cache_read_tokens, 0)
                      + COALESCE(e.cache_creation_tokens, 0)), 0) = 0
       AND COALESCE(SUM(COALESCE(e.cost_usd, 0)), 0) = 0
"""


def purge_empty_sessions(minutes: int | None = None) -> int:
    """
    Delete sessions that went inactive without ever logging usage, along with
    whatever placeholder events they produced. Returns the number removed.
    """
    cutoff = _cutoff(ACTIVE_WINDOW_MINUTES if minutes is None else minutes)
    with _connect() as conn:
        ids = [row["session_id"] for row in conn.execute(_EMPTY_SESSION_IDS, (cutoff,)).fetchall()]
        if not ids:
            return 0
        # Chunked: a single IN (...) list can exceed SQLite's variable limit
        # after a long outage leaves a backlog of short-lived containers.
        for start in range(0, len(ids), _MAX_SQL_VARIABLES):
            chunk = ids[start : start + _MAX_SQL_VARIABLES]
            marks = ",".join("?" * len(chunk))
            conn.execute(f"DELETE FROM usage_events WHERE session_id IN ({marks})", chunk)
            conn.execute(f"DELETE FROM sessions WHERE session_id IN ({marks})", chunk)
        return len(ids)


# ── user.id -> project mapping ─────────────────────────────────────────────

def set_user_project(user_id: str, project_name: str) -> int:
    """
    Map a user id to a project and apply it to every session that user owns,
    existing ones included. Returns the number of sessions relabelled.
    """
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO user_projects (user_id, project_name, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                project_name = excluded.project_name,
                updated_at = excluded.updated_at
            """,
            (user_id, project_name, _now_iso()),
        )
        # Does not touch sessions whose container declared its own project —
        # the resource attribute is authoritative and would win back on the
        # next event anyway.
        cur = conn.execute(
            """
            UPDATE sessions SET project_name = ?, project_source = 'user_map'
             WHERE user_id = ? AND COALESCE(project_source, '') <> 'resource'
            """,
            (project_name, user_id),
        )
        return cur.rowcount


def delete_user_project(user_id: str, clear_sessions: bool = False) -> None:
    """
    Remove a mapping. Existing session labels are left alone unless
    clear_sessions is set — future sessions from this user simply arrive
    untagged again.
    """
    with _connect() as conn:
        conn.execute("DELETE FROM user_projects WHERE user_id = ?", (user_id,))
        if clear_sessions:
            conn.execute(
                """
                UPDATE sessions SET project_name = NULL, project_source = NULL
                 WHERE user_id = ? AND COALESCE(project_source, '') <> 'resource'
                """,
                (user_id,),
            )


def fetch_users() -> list[dict]:
    """
    One row per user id — the stable identity a dev container reports — with its
    mapped project and rolled-up usage. Includes mapped users that have not
    reported a session yet.
    """
    cutoff = _cutoff(ACTIVE_WINDOW_MINUTES)
    with _connect() as conn:
        rows = conn.execute(
            """
            WITH ids AS (
                SELECT user_id FROM sessions WHERE user_id IS NOT NULL
                UNION
                SELECT user_id FROM user_projects
            )
            SELECT
                ids.user_id                                    AS user_id,
                up.project_name                                AS project_name,
                MAX(s.organization_id)                         AS organization_id,
                COUNT(DISTINCT s.session_id)                   AS session_count,
                COUNT(DISTINCT CASE WHEN s.last_seen_at >= ?
                                    THEN s.session_id END)     AS active_sessions,
                MIN(s.first_seen_at)                           AS first_seen_at,
                MAX(s.last_seen_at)                            AS last_seen_at,
                COALESCE(SUM(e.input_tokens), 0)               AS input_tokens,
                COALESCE(SUM(e.output_tokens), 0)              AS output_tokens,
                COALESCE(SUM(e.cost_usd), 0)                   AS cost_usd
            FROM ids
            LEFT JOIN sessions s      ON s.user_id = ids.user_id
            LEFT JOIN usage_events e  ON e.session_id = s.session_id
            LEFT JOIN user_projects up ON up.user_id = ids.user_id
            GROUP BY ids.user_id
            ORDER BY last_seen_at DESC
            """,
            (cutoff,),
        ).fetchall()
        return [dict(row) for row in rows]


def fetch_project_names() -> list[str]:
    """Every project label in use, from either sessions or user mappings."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT project_name FROM (
                SELECT project_name FROM sessions      WHERE project_name IS NOT NULL AND project_name <> ''
                UNION
                SELECT project_name FROM user_projects WHERE project_name IS NOT NULL AND project_name <> ''
            )
            ORDER BY project_name
            """
        ).fetchall()
        return [row["project_name"] for row in rows]



# ── Dashboard settings ─────────────────────────────────────────────────────

# The server owns these now. They used to live in each browser's localStorage,
# so the wall tablet and a laptop held different values and nothing server-side
# could act on them. Theme stays per-device — a wall display and a laptop
# reasonably differ there.
DEFAULT_SETTINGS: dict = {
    "monthlyBudget": None,            # USD, null disables the budget bar
    "billingCycleDay": 1,             # 1-28, day of month the budget resets
    "refreshIntervalMs": 5000,        # how often views refetch
    "defaultTimeWindow": "24h",       # 24h | 7d | 30d | all
    "defaultMetric": "cost",          # cost | tokens
    "costAlertThresholdPerHour": None,  # USD/hr, null disables the banner
}

_TIME_WINDOWS = {"24h", "7d", "30d", "all"}
_METRICS = {"cost", "tokens"}


def _coerce_setting(key: str, value):
    """Validate one setting, returning the value to store. Raises ValueError."""
    if key in ("monthlyBudget", "costAlertThresholdPerHour"):
        if value is None:
            return None
        value = float(value)
        if value < 0:
            raise ValueError(f"{key} must not be negative")
        return value
    if key == "billingCycleDay":
        value = int(value)
        # Capped at 28 so the day exists in every month.
        if not 1 <= value <= 28:
            raise ValueError("billingCycleDay must be between 1 and 28")
        return value
    if key == "refreshIntervalMs":
        value = int(value)
        if value < 1000:
            raise ValueError("refreshIntervalMs must be at least 1000")
        return value
    if key == "defaultTimeWindow":
        if value not in _TIME_WINDOWS:
            raise ValueError(f"defaultTimeWindow must be one of {sorted(_TIME_WINDOWS)}")
        return value
    if key == "defaultMetric":
        if value not in _METRICS:
            raise ValueError(f"defaultMetric must be one of {sorted(_METRICS)}")
        return value
    raise ValueError(f"unknown setting {key!r}")


def fetch_settings() -> dict:
    """Stored settings merged over the defaults, plus server-owned read-only values."""
    with _connect() as conn:
        row = conn.execute("SELECT data FROM app_settings WHERE id = 1").fetchone()
    stored = {}
    if row:
        try:
            stored = json.loads(row["data"])
        except (TypeError, ValueError):
            log.warning("Stored settings are not valid JSON; falling back to defaults")
    merged = {**DEFAULT_SETTINGS, **{k: v for k, v in stored.items() if k in DEFAULT_SETTINGS}}
    # Read-only: set by the operator, and it also governs when unused sessions
    # are deleted, so it is not something a browser should be able to change.
    merged["activeSessionWindowMin"] = ACTIVE_WINDOW_MINUTES
    return merged


def update_settings(patch: dict) -> dict:
    """Merge a partial update into the stored settings. Raises ValueError."""
    clean = {}
    for key, value in patch.items():
        if key not in DEFAULT_SETTINGS:
            continue  # ignore read-only and unknown keys rather than erroring
        clean[key] = _coerce_setting(key, value)

    with _connect() as conn:
        row = conn.execute("SELECT data FROM app_settings WHERE id = 1").fetchone()
        current = {}
        if row:
            try:
                current = json.loads(row["data"])
            except (TypeError, ValueError):
                current = {}
        current.update(clean)
        conn.execute(
            "INSERT INTO app_settings (id, data) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET data = excluded.data",
            (json.dumps(current),),
        )
    return fetch_settings()


def cycle_start(cycle_day: int, now: datetime | None = None) -> datetime:
    """
    Start of the billing period containing `now`, in UTC.

    If today is before the cycle day, the period started last month.
    """
    now = now or datetime.now(timezone.utc)
    day = max(1, min(28, cycle_day))
    start = now.replace(day=day, hour=0, minute=0, second=0, microsecond=0)
    if start > now:
        month, year = (start.month - 1, start.year) if start.month > 1 else (12, start.year - 1)
        start = start.replace(year=year, month=month)
    return start


def fetch_budget_usage(cycle_day: int) -> dict:
    """Spend since the start of the current billing period."""
    start = cycle_start(cycle_day)
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(cost_usd), 0)                AS cost_usd,
                   COALESCE(SUM(COALESCE(input_tokens, 0)
                              + COALESCE(output_tokens, 0)), 0) AS total_tokens
              FROM usage_events
             WHERE occurred_at >= ?
            """,
            (start.isoformat(),),
        ).fetchone()
    return {"cycle_start": start.isoformat(), **dict(row)}


# ── Reads ──────────────────────────────────────────────────────────────────

def fetch_summary(active_within_minutes: int | None = None) -> dict:
    minutes = ACTIVE_WINDOW_MINUTES if active_within_minutes is None else active_within_minutes
    with _connect() as conn:
        totals = conn.execute(
            """
            SELECT
                COUNT(DISTINCT session_id)          AS total_sessions,
                COALESCE(SUM(input_tokens), 0)       AS total_input_tokens,
                COALESCE(SUM(output_tokens), 0)      AS total_output_tokens,
                COALESCE(SUM(cache_read_tokens), 0)  AS total_cache_read_tokens,
                COALESCE(SUM(cache_creation_tokens), 0) AS total_cache_creation_tokens,
                COALESCE(SUM(cost_usd), 0)           AS total_cost_usd
            FROM usage_events
            """
        ).fetchone()
        active = conn.execute(
            "SELECT COUNT(*) AS active_sessions FROM sessions WHERE last_seen_at >= ?",
            (_cutoff(minutes),),
        ).fetchone()
        return {**dict(totals), **dict(active)}


def fetch_sessions(limit: int = 100) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT
                s.session_id,
                s.user_id,
                s.organization_id,
                s.project_name,
                s.project_source,
                s.first_seen_at,
                s.last_seen_at,
                COUNT(e.id)                     AS event_count,
                COALESCE(SUM(e.input_tokens), 0)  AS input_tokens,
                COALESCE(SUM(e.output_tokens), 0) AS output_tokens,
                COALESCE(SUM(e.cost_usd), 0)       AS cost_usd,
                GROUP_CONCAT(DISTINCT e.model)     AS models
            FROM sessions s
            LEFT JOIN usage_events e ON e.session_id = s.session_id
            GROUP BY s.session_id
            ORDER BY s.last_seen_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]


def fetch_usage_by_model() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT
                COALESCE(model, 'unknown')      AS model,
                COUNT(*)                        AS requests,
                COALESCE(SUM(input_tokens), 0)   AS input_tokens,
                COALESCE(SUM(output_tokens), 0)  AS output_tokens,
                COALESCE(SUM(cost_usd), 0)       AS cost_usd
            FROM usage_events
            GROUP BY model
            ORDER BY cost_usd DESC
            """
        ).fetchall()
        return [dict(row) for row in rows]


def _time_bucket_fmt(hours: int | None) -> str:
    """Hourly buckets for short windows, daily for anything longer (and for all-time)."""
    return '%Y-%m-%dT%H:00:00Z' if hours is not None and hours <= 48 else '%Y-%m-%dT00:00:00Z'


def _window_clause(hours: int | None) -> tuple[str, tuple]:
    """WHERE fragment for the look-back window. hours=None means all time."""
    if hours is None:
        return "", ()
    return "WHERE {col} >= ?", (_cutoff(hours * 60),)


def fetch_usage_over_time(hours: int | None = 24) -> list[dict]:
    """Cost and token totals bucketed by hour or day. hours=None returns all time."""
    fmt = _time_bucket_fmt(hours)
    where, params = _window_clause(hours)
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT
                strftime('{fmt}', occurred_at)                            AS bucket,
                COALESCE(SUM(cost_usd), 0)                               AS cost_usd,
                COALESCE(SUM(input_tokens), 0)                           AS input_tokens,
                COALESCE(SUM(output_tokens), 0)                          AS output_tokens,
                COALESCE(SUM(COALESCE(input_tokens,0)
                            + COALESCE(output_tokens,0)), 0)              AS total_tokens
            FROM usage_events
            {where.format(col='occurred_at')}
            GROUP BY bucket
            ORDER BY bucket
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]


def fetch_usage_over_time_by_project(hours: int | None = 24) -> list[dict]:
    """Cost/token totals grouped by project_name. hours=None returns all time."""
    fmt = _time_bucket_fmt(hours)
    where, params = _window_clause(hours)
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT
                strftime('{fmt}', e.occurred_at)                         AS bucket,
                COALESCE(s.project_name, '(untagged)')                   AS project_name,
                COALESCE(SUM(e.cost_usd), 0)                             AS cost_usd,
                COALESCE(SUM(COALESCE(e.input_tokens,0)
                            + COALESCE(e.output_tokens,0)), 0)            AS total_tokens
            FROM usage_events e
            LEFT JOIN sessions s ON e.session_id = s.session_id
            {where.format(col='e.occurred_at')}
            GROUP BY bucket, COALESCE(s.project_name, '(untagged)')
            ORDER BY bucket, project_name
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]
