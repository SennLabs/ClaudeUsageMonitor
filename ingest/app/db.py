import hashlib
import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .otlp import LogEvent
from .otlp_metrics import MetricPoint

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

# Nothing used to delete an event, ever. raw_attributes is the bulk of each
# row, so dropping it from old events reclaims most of the space while keeping
# every number the dashboard shows. 0 disables each sweep.
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "0"))
RAW_ATTRIBUTES_RETENTION_DAYS = int(os.environ.get("RAW_ATTRIBUTES_RETENTION_DAYS", "0"))

# SQLite's default SQLITE_MAX_VARIABLE_NUMBER is 999 on some builds.
_MAX_SQL_VARIABLES = 900


@contextmanager
def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=BUSY_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute(f"PRAGMA busy_timeout = {int(BUSY_TIMEOUT_SECONDS * 1000)}")
    # SQLite ignores REFERENCES unless this is set per connection, so
    # usage_events.session_id -> sessions.session_id was declarative only.
    conn.execute("PRAGMA foreign_keys = ON")
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
            "ALTER TABLE usage_events ADD COLUMN duration_ms INTEGER",
            "ALTER TABLE usage_events ADD COLUMN query_source TEXT",
            "ALTER TABLE usage_events ADD COLUMN effort TEXT",
            "ALTER TABLE usage_events ADD COLUMN speed TEXT",
            "ALTER TABLE usage_events ADD COLUMN agent_name TEXT",
            "ALTER TABLE usage_events ADD COLUMN skill_name TEXT",
            "ALTER TABLE usage_events ADD COLUMN mcp_server_name TEXT",
            "ALTER TABLE usage_events ADD COLUMN prompt_id TEXT",
            "ALTER TABLE usage_events ADD COLUMN app_version TEXT",
            "ALTER TABLE usage_events ADD COLUMN terminal_type TEXT",
            "ALTER TABLE usage_events ADD COLUMN tool_name TEXT",
            "ALTER TABLE usage_events ADD COLUMN status_code INTEGER",
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
        # Recover attributes that were only ever stored in raw_attributes.
        conn.execute(
            "UPDATE usage_events SET duration_ms = json_extract(raw_attributes, '$.\"duration_ms\"') "
            "WHERE duration_ms IS NULL AND json_extract(raw_attributes, '$.\"duration_ms\"') IS NOT NULL"
        )
        conn.execute(
            "UPDATE usage_events SET query_source = json_extract(raw_attributes, '$.\"query_source\"') "
            "WHERE query_source IS NULL AND json_extract(raw_attributes, '$.\"query_source\"') IS NOT NULL"
        )
        conn.execute(
            "UPDATE usage_events SET effort = json_extract(raw_attributes, '$.\"effort\"') "
            "WHERE effort IS NULL AND json_extract(raw_attributes, '$.\"effort\"') IS NOT NULL"
        )
        conn.execute(
            "UPDATE usage_events SET speed = json_extract(raw_attributes, '$.\"speed\"') "
            "WHERE speed IS NULL AND json_extract(raw_attributes, '$.\"speed\"') IS NOT NULL"
        )
        conn.execute(
            "UPDATE usage_events SET agent_name = json_extract(raw_attributes, '$.\"agent.name\"') "
            "WHERE agent_name IS NULL AND json_extract(raw_attributes, '$.\"agent.name\"') IS NOT NULL"
        )
        conn.execute(
            "UPDATE usage_events SET skill_name = json_extract(raw_attributes, '$.\"skill.name\"') "
            "WHERE skill_name IS NULL AND json_extract(raw_attributes, '$.\"skill.name\"') IS NOT NULL"
        )
        conn.execute(
            "UPDATE usage_events SET mcp_server_name = json_extract(raw_attributes, '$.\"mcp_server.name\"') "
            "WHERE mcp_server_name IS NULL AND json_extract(raw_attributes, '$.\"mcp_server.name\"') IS NOT NULL"
        )
        conn.execute(
            "UPDATE usage_events SET prompt_id = json_extract(raw_attributes, '$.\"prompt.id\"') "
            "WHERE prompt_id IS NULL AND json_extract(raw_attributes, '$.\"prompt.id\"') IS NOT NULL"
        )
        conn.execute(
            "UPDATE usage_events SET app_version = json_extract(raw_attributes, '$.\"app.version\"') "
            "WHERE app_version IS NULL AND json_extract(raw_attributes, '$.\"app.version\"') IS NOT NULL"
        )
        conn.execute(
            "UPDATE usage_events SET terminal_type = json_extract(raw_attributes, '$.\"terminal.type\"') "
            "WHERE terminal_type IS NULL AND json_extract(raw_attributes, '$.\"terminal.type\"') IS NOT NULL"
        )
        conn.execute(
            "UPDATE usage_events SET tool_name = json_extract(raw_attributes, '$.\"tool_name\"') "
            "WHERE tool_name IS NULL AND json_extract(raw_attributes, '$.\"tool_name\"') IS NOT NULL"
        )
        conn.execute(
            "UPDATE usage_events SET status_code = json_extract(raw_attributes, '$.\"status_code\"') "
            "WHERE status_code IS NULL AND json_extract(raw_attributes, '$.\"status_code\"') IS NOT NULL"
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
    Create the UNIQUE indexes that make INSERT OR IGNORE deduplicate.

    Kept out of schema.sql and out of the migration transaction on purpose: an
    existing database may already contain duplicates from retried batches, and
    refusing to start over that would be a worse failure than running without
    dedupe. Warn, keep serving, and let the operator clean up deliberately with
    dedupe.py.
    """
    for table, column, index in (
        ("usage_events", "event_hash", "idx_usage_events_hash"),
        ("metric_points", "point_hash", "idx_metric_points_hash"),
    ):
        with _connect() as conn:
            try:
                conn.execute(
                    f"CREATE UNIQUE INDEX IF NOT EXISTS {index} ON {table}({column})"
                )
            except sqlite3.IntegrityError:
                duplicates = conn.execute(
                    f"""
                    SELECT COALESCE(SUM(n - 1), 0) AS extra FROM (
                        SELECT COUNT(*) AS n FROM {table}
                         WHERE {column} IS NOT NULL
                         GROUP BY {column} HAVING n > 1
                    )
                    """
                ).fetchone()["extra"]
                log.warning(
                    "De-duplication on %s is INACTIVE: %d duplicate row(s) already "
                    "exist, so the unique index could not be created. Retried batches "
                    "will keep inflating totals until this is resolved. Review and "
                    "clean up with `python dedupe.py` (it reports before it deletes, "
                    "and takes --apply).",
                    table,
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


def ping() -> None:
    """Prove the database is reachable and readable. Used by /healthz."""
    with _connect() as conn:
        conn.execute("SELECT 1 FROM usage_events LIMIT 1").fetchone()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Writes ─────────────────────────────────────────────────────────────────

def _collect_session(sessions: dict, record) -> None:
    """
    Fold one record into the per-session summary for this batch.

    Collapsing here rather than upserting per record means an out-of-order
    batch cannot move last_seen_at backwards, and first_seen_at ends up at the
    earliest record rather than whichever one happened to be written last.
    """
    if not record.session_id:
        return
    current = sessions.get(record.session_id)
    if current is None:
        sessions[record.session_id] = {
            "first": record.occurred_at,
            "last": record.occurred_at,
            "user_id": record.user_id,
            "organization_id": record.organization_id,
            "project": record.project_name,
        }
        return
    current["first"] = min(current["first"], record.occurred_at)
    current["last"] = max(current["last"], record.occurred_at)
    current["user_id"] = current["user_id"] or record.user_id
    current["organization_id"] = current["organization_id"] or record.organization_id
    current["project"] = current["project"] or record.project_name


def _upsert_sessions(conn: sqlite3.Connection, sessions: dict) -> None:
    """
    Create or refresh the sessions rows for one batch.

    Shared by the logs and metrics paths: a container that exports metrics is
    reporting a live session whether or not it has billed an API call yet, and
    resource attributes (so project attribution) ride on both streams.
    """
    if not sessions:
        return

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

    sessions: dict[str, dict] = {}
    for event in events:
        _collect_session(sessions, event)

    with _connect() as conn:
        _upsert_sessions(conn, sessions)

        before = conn.total_changes
        conn.executemany(
            """
            INSERT OR IGNORE INTO usage_events (
                session_id, occurred_at, event_name, model,
                input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens,
                cost_usd, cost_usd_micros, raw_attributes, event_hash,
                duration_ms, query_source, effort, speed, agent_name, skill_name,
                mcp_server_name, prompt_id, app_version, terminal_type, tool_name, status_code
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    event.duration_ms,
                    event.query_source,
                    event.effort,
                    event.speed,
                    event.agent_name,
                    event.skill_name,
                    event.mcp_server_name,
                    event.prompt_id,
                    event.app_version,
                    event.terminal_type,
                    event.tool_name,
                    event.status_code,
                )
                for event in events
            ],
        )
        inserted = conn.total_changes - before

    return (inserted, len(events) - inserted)


def point_hash(point: MetricPoint) -> str:
    """
    Identity of one metric data point.

    A point is identified by its metric, its window and its full attribute
    map. Value is included too: two points that agree on all of those but
    differ in value cannot both be the same measurement, and dropping the
    second would silently lose data.
    """
    return _hash_parts(
        point.metric_name,
        f"{point.started_at or ''}/{point.occurred_at}",
        repr(point.value),
        json.dumps(point.raw_attributes, sort_keys=True),
    )


_METRIC_COLUMNS = (
    "metric_name", "occurred_at", "started_at", "value", "temporality", "is_monotonic",
    "session_id", "user_id", "organization_id", "project_name", "app_version",
    "terminal_type", "model", "type", "tool_name", "decision", "source", "language",
    "start_type", "raw_attributes", "point_hash",
)


def write_metric_points(points: list[MetricPoint]) -> tuple[int, int]:
    """
    Write a whole metrics batch in one connection and one transaction.

    Returns (inserted, duplicates_ignored). Same shape and same reasoning as
    write_events: atomic so a mid-batch failure leaves nothing behind for the
    exporter's retry to duplicate, and INSERT OR IGNORE against a unique hash
    so the retry itself is a no-op.
    """
    if not points:
        return (0, 0)

    sessions: dict[str, dict] = {}
    for point in points:
        _collect_session(sessions, point)

    marks = ", ".join("?" * len(_METRIC_COLUMNS))
    with _connect() as conn:
        _upsert_sessions(conn, sessions)

        before = conn.total_changes
        conn.executemany(
            f"INSERT OR IGNORE INTO metric_points ({', '.join(_METRIC_COLUMNS)}) VALUES ({marks})",
            [
                (
                    p.metric_name,
                    p.occurred_at,
                    p.started_at,
                    p.value,
                    p.temporality,
                    1 if p.is_monotonic else 0,
                    p.session_id,
                    p.user_id,
                    p.organization_id,
                    p.project_name,
                    p.app_version,
                    p.terminal_type,
                    p.model,
                    p.type,
                    p.tool_name,
                    p.decision,
                    p.source,
                    p.language,
                    p.start_type,
                    json.dumps(p.raw_attributes, sort_keys=True),
                    point_hash(p),
                )
                for p in points
            ],
        )
        inserted = conn.total_changes - before

    return (inserted, len(points) - inserted)


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
       -- A session that reported metrics but no billable API call is still a
       -- real session: active time, commits and lines-of-code all arrive on
       -- the metrics stream alone. Purging it would delete the only record of
       -- work that the productivity figures are computed from.
       AND NOT EXISTS (SELECT 1 FROM metric_points m WHERE m.session_id = s.session_id)
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



def apply_retention() -> dict:
    """
    Enforce the retention settings. Returns what it did.

    Two independent knobs, because they trade off differently:
      RAW_ATTRIBUTES_RETENTION_DAYS nulls raw_attributes on older events. Every
        aggregate stays correct — only the raw JSON, which is the bulk of each
        row, goes. This is the one to reach for first.
      RETENTION_DAYS deletes the events outright, which does change historical
        totals. Off by default for that reason.
    """
    # Counted per table rather than summed. A 60-second metrics stream produces
    # far more rows than the event stream, so one combined "events_deleted"
    # would report deleting several times more events than have ever existed.
    result = {
        "attributes_cleared": 0,
        "events_deleted": 0,
        "metric_attributes_cleared": 0,
        "metric_points_deleted": 0,
    }

    with _connect() as conn:
        if RAW_ATTRIBUTES_RETENTION_DAYS > 0:
            attr_cutoff = _cutoff(RAW_ATTRIBUTES_RETENTION_DAYS * 24 * 60)
            cur = conn.execute(
                "UPDATE usage_events SET raw_attributes = NULL "
                " WHERE raw_attributes IS NOT NULL AND occurred_at < ?",
                (attr_cutoff,),
            )
            result["attributes_cleared"] = cur.rowcount
            cur = conn.execute(
                "UPDATE metric_points SET raw_attributes = NULL "
                " WHERE raw_attributes IS NOT NULL AND occurred_at < ?",
                (attr_cutoff,),
            )
            result["metric_attributes_cleared"] = cur.rowcount

        if RETENTION_DAYS > 0:
            cutoff = _cutoff(RETENTION_DAYS * 24 * 60)
            cur = conn.execute("DELETE FROM usage_events WHERE occurred_at < ?", (cutoff,))
            result["events_deleted"] = cur.rowcount
            cur = conn.execute("DELETE FROM metric_points WHERE occurred_at < ?", (cutoff,))
            result["metric_points_deleted"] = cur.rowcount
            # Sessions whose events have all aged out are now empty shells.
            conn.execute(
                """
                DELETE FROM sessions
                 WHERE last_seen_at < ?
                   AND session_id NOT IN (SELECT DISTINCT session_id FROM usage_events
                                           WHERE session_id IS NOT NULL)
                """,
                (cutoff,),
            )
    return result


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
    "displayTimeZone": "UTC",         # IANA name; buckets days to local midnight
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
    if key == "displayTimeZone":
        if value is None:
            return "UTC"
        # Checked before .strip(): an int or a list reached AttributeError here,
        # which put_settings does not catch, so a bad type was a 500 rather
        # than the 400 every other malformed setting produces.
        if not isinstance(value, str):
            raise ValueError("displayTimeZone must be a string IANA time zone name")
        value = value.strip() or "UTC"
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(
                f"displayTimeZone {value!r} is not a known IANA time zone "
                "(for example 'UTC' or 'Australia/Perth')"
            )
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


def cycle_start(cycle_day: int, now: datetime | None = None, tz: str = "UTC") -> datetime:
    """
    Start of the billing period containing `now`, as an instant.

    The period begins at *local* midnight on the cycle day, in the display
    zone, and is returned converted to UTC for comparison against the stored
    timestamps. Computing it in UTC meant that in UTC+8 the month's budget
    started at 08:00 on the 1st local time, so eight hours of the first day
    were counted against the previous period.

    If today is before the cycle day, the period started last month.
    """
    try:
        zone = ZoneInfo(tz)
    except Exception:
        zone = timezone.utc
    now = (now or datetime.now(timezone.utc)).astimezone(zone)
    day = max(1, min(28, cycle_day))
    start = now.replace(day=day, hour=0, minute=0, second=0, microsecond=0)
    if start > now:
        month, year = (start.month - 1, start.year) if start.month > 1 else (12, start.year - 1)
        start = start.replace(year=year, month=month)
    return start.astimezone(timezone.utc)


def fetch_budget_usage(cycle_day: int) -> dict:
    """Spend since the start of the current billing period."""
    start = cycle_start(cycle_day, tz=display_time_zone())
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
        # Events that arrived with no session.id reach no per-session or
        # per-user view, so the headline and those views used to disagree with
        # no indication. Report the gap rather than hiding it.
        unattributed = conn.execute(
            """
            SELECT COUNT(*)                   AS unattributed_events,
                   COALESCE(SUM(cost_usd), 0) AS unattributed_cost_usd
              FROM usage_events WHERE session_id IS NULL
            """
        ).fetchone()
        return {**dict(totals), **dict(active), **dict(unattributed)}


def count_sessions() -> int:
    with _connect() as conn:
        return conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"]


def fetch_sessions(limit: int = 100, offset: int = 0) -> list[dict]:
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
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
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



# ── Insight queries ────────────────────────────────────────────────────────
# Everything below reads attributes Claude Code has always sent. They were
# stored in raw_attributes from the start; promoting them to columns is what
# made these aggregations possible.

def fetch_attribution(hours: int | None = 24) -> dict:
    """Cost split by the dimensions other than model and project."""
    where, params = _window_clause(hours)
    clause = where.format(col="occurred_at")
    joiner = "AND" if clause else "WHERE"

    def by(conn: sqlite3.Connection, column: str) -> list[dict]:
        rows = conn.execute(
            f"""
            SELECT COALESCE({column}, '(none)')        AS name,
                   COUNT(*)                            AS requests,
                   COALESCE(SUM(cost_usd), 0)          AS cost_usd,
                   COALESCE(SUM(COALESCE(input_tokens,0)
                              + COALESCE(output_tokens,0)), 0) AS total_tokens
              FROM usage_events
              {clause}
              {joiner} event_name = 'claude_code.api_request'
             GROUP BY {column}
             ORDER BY cost_usd DESC
             LIMIT 20
            """,
            params,
        ).fetchall()
        return [dict(r) for r in rows]

    # Column names here are code literals, never request input.
    dimensions = {
        "by_query_source": "query_source",
        "by_agent": "agent_name",
        "by_skill": "skill_name",
        "by_mcp_server": "mcp_server_name",
        "by_effort": "effort",
        "by_speed": "speed",
    }
    with _connect() as conn:
        return {key: by(conn, column) for key, column in dimensions.items()}


def fetch_spend_rate(minutes: int = 60) -> dict:
    """
    Spend over a true trailing window, from raw event timestamps.

    The client used to derive this from chart buckets, which made it a sawtooth:
    the hourly branch only ever caught the current clock-hour bucket, so the
    cost alert dropped to near zero on the hour and climbed back mid-hour, and
    the daily branch divided a partial UTC day by 24.
    """
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(cost_usd), 0)  AS cost_usd,
                   COUNT(*)                    AS events
              FROM usage_events
             WHERE occurred_at >= ?
            """,
            (_cutoff(minutes),),
        ).fetchone()
    scale = 60 / minutes
    return {
        "window_minutes": minutes,
        "cost_usd": row["cost_usd"],
        "cost_usd_per_hour": row["cost_usd"] * scale,
        "events": row["events"],
    }


def fetch_latency(hours: int | None = 24) -> dict:
    """API request latency. SQLite has no percentile function, so p50/p95 are
    read positionally out of the ordered set."""
    where, params = _window_clause(hours)
    clause = where.format(col="occurred_at")
    joiner = "AND" if clause else "WHERE"
    base = f"FROM usage_events {clause} {joiner} event_name = 'claude_code.api_request' AND duration_ms IS NOT NULL"

    with _connect() as conn:
        agg = conn.execute(
            f"SELECT COUNT(*) AS n, AVG(duration_ms) AS avg_ms, MAX(duration_ms) AS max_ms {base}",
            params,
        ).fetchone()
        n = agg["n"] or 0
        if n == 0:
            return {"requests": 0, "avg_ms": None, "p50_ms": None, "p95_ms": None, "max_ms": None}

        def pct(fraction: float) -> int:
            offset = min(n - 1, int(n * fraction))
            row = conn.execute(
                f"SELECT duration_ms {base} ORDER BY duration_ms LIMIT 1 OFFSET ?",
                (*params, offset),
            ).fetchone()
            return row["duration_ms"]

        return {
            "requests": n,
            "avg_ms": round(agg["avg_ms"]),
            "p50_ms": pct(0.50),
            "p95_ms": pct(0.95),
            "max_ms": agg["max_ms"],
        }


def fetch_errors(hours: int | None = 24) -> dict:
    """Failed and refused API requests, and how often they were retried."""
    where, params = _window_clause(hours)
    clause = where.format(col="occurred_at")
    joiner = "AND" if clause else "WHERE"

    with _connect() as conn:
        counts = conn.execute(
            f"""
            SELECT
                SUM(event_name = 'claude_code.api_request') AS requests,
                SUM(event_name = 'claude_code.api_error')   AS errors,
                SUM(event_name = 'claude_code.api_refusal') AS refusals,
                SUM(CAST(COALESCE(json_extract(raw_attributes, '$.attempt'), 1) AS INTEGER) > 1) AS retried
              FROM usage_events {clause}
            """,
            params,
        ).fetchone()

        by_status = [
            dict(r)
            for r in conn.execute(
                f"""
                SELECT COALESCE(status_code, 0) AS status_code, COUNT(*) AS n
                  FROM usage_events {clause} {joiner} event_name = 'claude_code.api_error'
                 GROUP BY status_code ORDER BY n DESC LIMIT 10
                """,
                params,
            ).fetchall()
        ]
        by_category = [
            dict(r)
            for r in conn.execute(
                f"""
                SELECT COALESCE(json_extract(raw_attributes, '$.category'), '(undisclosed)') AS category,
                       COUNT(*) AS n
                  FROM usage_events {clause} {joiner} event_name = 'claude_code.api_refusal'
                 GROUP BY category ORDER BY n DESC LIMIT 10
                """,
                params,
            ).fetchall()
        ]

    total = (counts["requests"] or 0) + (counts["errors"] or 0)
    return {
        "requests": counts["requests"] or 0,
        "errors": counts["errors"] or 0,
        "refusals": counts["refusals"] or 0,
        "retried": counts["retried"] or 0,
        "error_rate": round((counts["errors"] or 0) / total, 4) if total else 0.0,
        "by_status_code": by_status,
        "by_refusal_category": by_category,
    }


def fetch_tool_stats(hours: int | None = 24) -> list[dict]:
    """Per-tool call counts, failure rate and duration."""
    where, params = _window_clause(hours)
    clause = where.format(col="occurred_at")
    joiner = "AND" if clause else "WHERE"
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT
                COALESCE(tool_name, '(unknown)')  AS tool_name,
                COUNT(*)                          AS calls,
                SUM(json_extract(raw_attributes, '$.success') = 'false') AS failures,
                ROUND(AVG(duration_ms))           AS avg_ms,
                MAX(duration_ms)                  AS max_ms
              FROM usage_events
              {clause}
              {joiner} event_name = 'claude_code.tool_result'
             GROUP BY tool_name
             ORDER BY calls DESC
             LIMIT 30
            """,
            params,
        ).fetchall()
        return [dict(r) for r in rows]


def fetch_cache_efficiency(hours: int | None = 24) -> dict:
    """
    Cache hit ratio, overall and per project.

    Of the levers available to a Claude Code user, this is one of the few that
    actually moves the bill: a cached input token is billed at a fraction of an
    uncached one. The numbers were being collected from the start and shown
    almost nowhere.
    """
    where, params = _window_clause(hours)
    clause = where.format(col="e.occurred_at")
    joiner = "AND" if clause else "WHERE"
    cols = """
        COALESCE(SUM(e.input_tokens), 0)           AS uncached_input_tokens,
        COALESCE(SUM(e.cache_read_tokens), 0)      AS cache_read_tokens,
        COALESCE(SUM(e.cache_creation_tokens), 0)  AS cache_creation_tokens,
        COALESCE(SUM(e.cost_usd), 0)               AS cost_usd
    """

    def ratio(row: dict) -> dict:
        served = row["cache_read_tokens"]
        total = served + row["uncached_input_tokens"]
        return {**row, "hit_ratio": round(served / total, 4) if total else None}

    with _connect() as conn:
        overall = ratio(dict(conn.execute(
            f"SELECT {cols} FROM usage_events e {clause} {joiner} e.event_name = "
            "'claude_code.api_request'", params).fetchone()))
        by_project = [
            ratio(dict(r))
            for r in conn.execute(
                f"""
                SELECT COALESCE(s.project_name, '(untagged)') AS name, {cols}
                  FROM usage_events e
                  LEFT JOIN sessions s ON s.session_id = e.session_id
                  {clause}
                  {joiner} e.event_name = 'claude_code.api_request'
                 GROUP BY name
                 ORDER BY cost_usd DESC
                 LIMIT 20
                """,
                params,
            ).fetchall()
        ]
    return {"overall": overall, "by_project": by_project}


def fetch_prompts(hours: int | None = 24, limit: int = 25) -> list[dict]:
    """Cost per user prompt. Every event of one prompt shares a prompt.id."""
    where, params = _window_clause(hours)
    clause = where.format(col="e.occurred_at")
    joiner = "AND" if clause else "WHERE"
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT
                e.prompt_id,
                COALESCE(s.project_name, '(untagged)')     AS project_name,
                e.session_id,
                COUNT(*)                                   AS requests,
                COALESCE(SUM(e.cost_usd), 0)               AS cost_usd,
                COALESCE(SUM(COALESCE(e.input_tokens,0)
                           + COALESCE(e.output_tokens,0)), 0) AS total_tokens,
                COALESCE(SUM(e.duration_ms), 0)            AS duration_ms,
                MIN(e.occurred_at)                         AS started_at,
                GROUP_CONCAT(DISTINCT e.model)             AS models
              FROM usage_events e
              LEFT JOIN sessions s ON s.session_id = e.session_id
              {clause}
              {joiner} e.prompt_id IS NOT NULL
             GROUP BY e.prompt_id
             ORDER BY cost_usd DESC
             LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def fetch_audit(hours: int | None = 24) -> dict:
    """
    Events that have nothing to do with cost but everything to do with running
    a fleet: permission-mode changes, logins, and MCP server connectivity.
    """
    where, params = _window_clause(hours)
    clause = where.format(col="occurred_at")
    joiner = "AND" if clause else "WHERE"

    def rows(sql: str) -> list[dict]:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]

    with _connect() as conn:
        permission_changes = rows(f"""
            SELECT occurred_at, session_id,
                   json_extract(raw_attributes, '$.from_mode') AS from_mode,
                   json_extract(raw_attributes, '$.to_mode')   AS to_mode,
                   json_extract(raw_attributes, '$.trigger')   AS trigger
              FROM usage_events {clause}
              {joiner} event_name = 'claude_code.permission_mode_changed'
             ORDER BY occurred_at DESC LIMIT 50
        """)
        auth_failures = rows(f"""
            SELECT occurred_at, session_id,
                   json_extract(raw_attributes, '$.action')         AS action,
                   json_extract(raw_attributes, '$.success')        AS success,
                   json_extract(raw_attributes, '$.error_category') AS error_category
              FROM usage_events {clause}
              {joiner} event_name = 'claude_code.auth'
               AND COALESCE(json_extract(raw_attributes, '$.success'), 'true') = 'false'
             ORDER BY occurred_at DESC LIMIT 50
        """)
        mcp = rows(f"""
            SELECT COALESCE(mcp_server_name, json_extract(raw_attributes, '$.server_name'),
                            '(unnamed)')                              AS server,
                   json_extract(raw_attributes, '$.status')           AS status,
                   json_extract(raw_attributes, '$.transport_type')   AS transport,
                   COUNT(*)                                           AS n,
                   MAX(occurred_at)                                   AS last_at
              FROM usage_events {clause}
              {joiner} event_name = 'claude_code.mcp_server_connection'
             GROUP BY server, status, transport
             ORDER BY n DESC LIMIT 30
        """)

    # bypassPermissions is the one worth an alert rather than a row in a table.
    bypasses = [c for c in permission_changes if c["to_mode"] == "bypassPermissions"]
    return {
        "permission_changes": permission_changes,
        "bypass_count": len(bypasses),
        "auth_failures": auth_failures,
        "mcp_connections": mcp,
    }


def iter_export_rows(since: str | None = None, until: str | None = None):
    """Stream events for CSV export, joined to their session's project."""
    conditions, params = [], []
    if since:
        conditions.append("e.occurred_at >= ?"); params.append(since)
    if until:
        conditions.append("e.occurred_at < ?"); params.append(until)
    clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    with _connect() as conn:
        cursor = conn.execute(
            f"""
            SELECT e.occurred_at, e.event_name, e.session_id,
                   s.user_id, COALESCE(s.project_name, '') AS project_name,
                   e.model, e.query_source, e.effort, e.speed,
                   e.input_tokens, e.output_tokens, e.cache_read_tokens,
                   e.cache_creation_tokens, e.cost_usd, e.duration_ms
              FROM usage_events e
              LEFT JOIN sessions s ON s.session_id = e.session_id
              {clause}
             ORDER BY e.occurred_at
            """,
            params,
        )
        # Yielded inside the connection's scope so the cursor stays valid.
        for row in cursor:
            yield dict(row)


def fetch_fleet() -> list[dict]:
    """Which Claude Code versions and terminals are reporting."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT COALESCE(app_version, '(unreported)')   AS app_version,
                   COALESCE(terminal_type, '(unreported)') AS terminal_type,
                   COUNT(DISTINCT session_id)              AS sessions,
                   MAX(occurred_at)                        AS last_seen_at
              FROM usage_events
             GROUP BY app_version, terminal_type
             ORDER BY last_seen_at DESC
             LIMIT 50
            """
        ).fetchall()
        return [dict(r) for r in rows]


# Claude Code's metric names. Anything not listed still lands in metric_points
# and shows up in the by_metric catch-all — naming has shifted across versions
# before, and a renamed metric should degrade to "unrecognised", not vanish.
METRIC_LINES = "claude_code.lines_of_code.count"
METRIC_COMMITS = "claude_code.commit.count"
METRIC_PRS = "claude_code.pull_request.count"
METRIC_ACTIVE_TIME = "claude_code.active_time.total"
METRIC_EDIT_DECISION = "claude_code.code_edit_tool.decision"
METRIC_SESSIONS = "claude_code.session.count"
METRIC_COST = "claude_code.cost.usage"
METRIC_TOKENS = "claude_code.token.usage"


def _safe_ratio(numerator: float, denominator: float) -> float | None:
    """None rather than 0 when there is nothing to divide by — a cost per commit
    of $0.00 and "no commits recorded" are different statements."""
    if not denominator:
        return None
    return numerator / denominator


def fetch_metrics(hours: int | None = 24) -> dict:
    """
    Claude Code's own pre-aggregated metrics, plus the ratios derived from them.

    Only DELTA points are summed. A cumulative point is a running total, so
    adding the series counts the same work once per export interval; the count
    of skipped points is returned so a misconfigured client is visible rather
    than quietly halving every figure.
    """
    where, params = _window_clause(hours)
    delta = "temporality = 'delta'"
    scope = (where.format(col="occurred_at") + f" AND {delta}") if where else f"WHERE {delta}"

    with _connect() as conn:
        def total(metric: str, extra: str = "", extra_params: tuple = ()) -> float:
            row = conn.execute(
                f"SELECT COALESCE(SUM(value), 0) AS v FROM metric_points "
                f"{scope} AND metric_name = ? {extra}",
                (*params, metric, *extra_params),
            ).fetchone()
            return float(row["v"])

        lines_added = total(METRIC_LINES, "AND type = ?", ("added",))
        lines_removed = total(METRIC_LINES, "AND type = ?", ("removed",))
        commits = total(METRIC_COMMITS)
        pull_requests = total(METRIC_PRS)
        active_seconds = total(METRIC_ACTIVE_TIME)
        sessions_started = total(METRIC_SESSIONS)
        cost_from_metrics = total(METRIC_COST)

        # Deliberately NOT delta-filtered: this is the catch-all that keeps an
        # unrecognised — or non-summable — metric visible, which is the whole
        # point of storing metrics by name rather than against a fixed list.
        # `total` is still the delta-only sum, because that is the only part
        # that can be added up; a gauge therefore shows points > 0, total 0.
        by_metric = [
            dict(r)
            for r in conn.execute(
                f"""
                SELECT metric_name,
                       COUNT(*)                                            AS points,
                       COALESCE(SUM(CASE WHEN temporality = 'delta'
                                         THEN value END), 0)               AS total
                  FROM metric_points {where.format(col='occurred_at')}
                 GROUP BY metric_name
                 ORDER BY metric_name
                """,
                params,
            ).fetchall()
        ]

        def grouped(metric: str, column: str) -> list[dict]:
            return [
                dict(r)
                for r in conn.execute(
                    f"""
                    SELECT COALESCE({column}, '(unreported)') AS name,
                           COALESCE(SUM(value), 0)            AS total
                      FROM metric_points {scope} AND metric_name = ?
                     GROUP BY COALESCE({column}, '(unreported)')
                     ORDER BY total DESC
                    """,
                    (*params, metric),
                ).fetchall()
            ]

        # Acceptance rate per language: a low one means Claude Code is being
        # asked for edits it keeps getting wrong in that language, which is a
        # prompt or context problem rather than a cost problem.
        decisions = [
            dict(r)
            for r in conn.execute(
                f"""
                SELECT COALESCE(language, '(unreported)')                       AS language,
                       COALESCE(SUM(CASE WHEN decision = 'accept' THEN value END), 0) AS accepted,
                       COALESCE(SUM(CASE WHEN decision = 'reject' THEN value END), 0) AS rejected
                  FROM metric_points {scope} AND metric_name = ?
                 GROUP BY COALESCE(language, '(unreported)')
                 ORDER BY accepted + rejected DESC
                 LIMIT 25
                """,
                (*params, METRIC_EDIT_DECISION),
            ).fetchall()
        ]
        for row in decisions:
            row["acceptance_rate"] = _safe_ratio(row["accepted"], row["accepted"] + row["rejected"])

        # Deliberately NOT filtered to delta. `reporting` answers "is anything
        # arriving at all", and a client stuck on cumulative temporality is
        # arriving — it just contributes to no total. Counting only delta points
        # here made that case render as "no metrics received yet", which points
        # at the wrong fix entirely.
        counts = conn.execute(
            f"""
            SELECT COUNT(*)                                              AS total_points,
                   COALESCE(SUM(temporality = 'cumulative'), 0)          AS cumulative,
                   COALESCE(SUM(temporality NOT IN ('delta','cumulative')), 0)
                                                                         AS unsummable
              FROM metric_points {where.format(col='occurred_at')}
            """,
            params,
        ).fetchone()
        total_points = counts["total_points"]
        # Split apart on purpose. Only a *cumulative* point is fixed by the
        # temporality preference, and that is the advice the UI gives — a gauge
        # carries no aggregationTemporality at all, so counting it as
        # "cumulative" produced a message that could never change anything.
        cumulative = counts["cumulative"]
        unsummable = counts["unsummable"]

        event_where = where.format(col="occurred_at")
        fleet_cost = conn.execute(
            f"SELECT COALESCE(SUM(cost_usd_micros), 0) AS micros FROM usage_events {event_where}",
            params,
        ).fetchone()["micros"] / 1_000_000

        # The ratios below divide cost by counters that exist ONLY for clients
        # with OTEL_METRICS_EXPORTER set — which is optional, and documented as
        # such. Dividing fleet-wide cost by a partial fleet's commits is not a
        # slightly-off number, it is wrong by the inverse of the rollout: with
        # 2 of 10 developers exporting metrics, "cost per commit" reads 5x high
        # with nothing on screen to say so.
        #
        # So the numerator is restricted to the same sessions the denominators
        # came from, and the coverage fraction is returned so a partial rollout
        # is visible rather than silently distorting every figure.
        scoped_cost = conn.execute(
            f"""
            SELECT COALESCE(SUM(cost_usd_micros), 0) AS micros
              FROM usage_events e
             {event_where + ' AND' if event_where else 'WHERE'} e.session_id IN (
                   SELECT DISTINCT session_id FROM metric_points
                    WHERE session_id IS NOT NULL
             )
            """,
            params,
        ).fetchone()["micros"] / 1_000_000
        cost_from_events = scoped_cost

        # Materialised inside the connection's scope: `grouped` closes over the
        # cursor, and evaluating it lazily in the return dict ran it after the
        # context manager had closed the database.
        sessions_by_start_type = grouped(METRIC_SESSIONS, "start_type")
        active_time_by_type = grouped(METRIC_ACTIVE_TIME, "type")
        tokens_by_type = grouped(METRIC_TOKENS, "type")

    active_hours = active_seconds / 3600
    total_lines = lines_added + lines_removed

    return {
        "window_hours": hours,
        # False means nothing at all has been received on /v1/metrics in this
        # window — almost always OTEL_METRICS_EXPORTER not being set on the
        # clients, which the UI says explicitly rather than showing a page of
        # zeroes. True with every total at zero is a different diagnosis, and
        # cumulative_points_ignored is usually the reason.
        "reporting": total_points > 0,
        "cumulative_points_ignored": cumulative,
        # Points that are neither delta nor cumulative — a gauge, which carries
        # no aggregationTemporality. Excluded from totals because a gauge is
        # not summable, and reported apart from `cumulative` because no client
        # setting will change it.
        "unsummable_points": unsummable,
        "totals": {
            "lines_added": lines_added,
            "lines_removed": lines_removed,
            "commits": commits,
            "pull_requests": pull_requests,
            "active_seconds": active_seconds,
            "sessions_started": sessions_started,
        },
        # Restricted to sessions that also report metrics, so it matches the
        # denominators of everything under `derived`.
        "cost_usd": cost_from_events,
        # Every session in the window, metrics-reporting or not. This is the
        # figure /api/summary agrees with.
        "cost_usd_fleet": fleet_cost,
        # What fraction of spend comes from metrics-reporting clients. Below 1,
        # the ratios describe that subset and not the whole fleet — which is a
        # legitimate reading, but only if it is said out loud.
        "metrics_cost_coverage": _safe_ratio(cost_from_events, fleet_cost),
        # The metrics stream carries its own cost counter. Compared against the
        # *scoped* event cost above, both now cover the same population, so a
        # gap really is a delivery problem (a dropped export, a duplicate)
        # rather than an artefact of a partial rollout.
        "cost_usd_from_metrics": cost_from_metrics,
        "derived": {
            "cost_per_commit": _safe_ratio(cost_from_events, commits),
            "cost_per_pull_request": _safe_ratio(cost_from_events, pull_requests),
            "cost_per_active_hour": _safe_ratio(cost_from_events, active_hours),
            "usd_per_1k_lines": _safe_ratio(cost_from_events * 1000, total_lines),
            "lines_per_active_hour": _safe_ratio(total_lines, active_hours),
        },
        "sessions_by_start_type": sessions_by_start_type,
        "active_time_by_type": active_time_by_type,
        "tokens_by_type": tokens_by_type,
        "edit_decisions": decisions,
        "by_metric": by_metric,
    }


def _granularity(hours: int | None) -> str:
    """Hourly buckets for short windows, daily for anything longer (and for all-time)."""
    return "hour" if hours is not None and hours <= 48 else "day"


def display_time_zone(conn: sqlite3.Connection | None = None) -> str:
    """
    The configured IANA zone, or 'UTC' if it is unset or no longer resolvable.

    Never raises: a zone that validated when it was saved can stop resolving if
    the tzdata package is dropped from an image, and a chart that renders in
    UTC is a far better failure than one that 500s.
    """
    def read(c: sqlite3.Connection) -> str:
        row = c.execute("SELECT data FROM app_settings WHERE id = 1").fetchone()
        if not row:
            return "UTC"
        try:
            return json.loads(row["data"]).get("displayTimeZone") or "UTC"
        except (TypeError, ValueError):
            return "UTC"

    try:
        name = read(conn) if conn is not None else _read_with_new_conn(read)
        ZoneInfo(name)
        return name
    except Exception:
        return "UTC"


def _read_with_new_conn(fn):
    with _connect() as conn:
        return fn(conn)


def _bucket_sql(conn: sqlite3.Connection, column: str, hours: int | None) -> str:
    """
    A SQL expression naming the bucket one row falls in, in the display zone.

    Everything is stored in UTC, and it stays that way — only the *labelling*
    moves. In UTC+8 a UTC-day bucket runs 08:00 to 08:00 local, so "yesterday's
    spend" on the chart was never anybody's yesterday.

    UTC keeps the pure-SQL path: it is the default, it is exact, and leaving it
    untouched means configuring nothing changes nothing. Any other zone needs a
    per-row conversion, because a fixed offset is wrong across a DST boundary —
    so a Python function is registered on the connection and SQLite calls it
    per row. Bucket strings then carry a real offset (`...+08:00`) instead of
    `Z`, which is what makes them unambiguous to the chart.
    """
    granularity = _granularity(hours)
    zone = display_time_zone(conn)
    if zone == "UTC":
        fmt = "%Y-%m-%dT%H:00:00Z" if granularity == "hour" else "%Y-%m-%dT00:00:00Z"
        return f"strftime('{fmt}', {column})"

    tz = ZoneInfo(zone)

    def bucket(ts: str | None) -> str | None:
        if not ts:
            return None
        try:
            local = datetime.fromisoformat(ts).astimezone(tz)
        except (TypeError, ValueError):
            return None
        if granularity == "hour":
            local = local.replace(minute=0, second=0, microsecond=0)
        else:
            local = local.replace(hour=0, minute=0, second=0, microsecond=0)
        return local.isoformat()

    # deterministic=True lets SQLite reuse the result for repeated inputs, and
    # is true here: the same timestamp always lands in the same bucket.
    conn.create_function("local_bucket", 1, bucket, deterministic=True)
    return f"local_bucket({column})"


def _window_clause(hours: int | None) -> tuple[str, tuple]:
    """WHERE fragment for the look-back window. hours=None means all time."""
    if hours is None:
        return "", ()
    return "WHERE {col} >= ?", (_cutoff(hours * 60),)


def fetch_usage_over_time(hours: int | None = 24) -> list[dict]:
    """Cost and token totals bucketed by hour or day. hours=None returns all time."""
    where, params = _window_clause(hours)
    with _connect() as conn:
        bucket = _bucket_sql(conn, "occurred_at", hours)
        rows = conn.execute(
            f"""
            SELECT
                {bucket}                                                 AS bucket,
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
    where, params = _window_clause(hours)
    with _connect() as conn:
        bucket = _bucket_sql(conn, "e.occurred_at", hours)
        rows = conn.execute(
            f"""
            SELECT
                {bucket}                                                 AS bucket,
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
