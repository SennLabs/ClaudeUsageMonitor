import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .otlp import LogEvent

# DB_PATH is overridable so a container can point it at a mounted volume
# (e.g. /data/usage.db) instead of the package directory.
DB_PATH = Path(os.environ.get("DB_PATH", str(Path(__file__).resolve().parent.parent / "usage.db")))
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"

# How long a session may go quiet before it stops counting as active. Also the
# grace period before an empty session is purged (see purge_empty_sessions).
ACTIVE_WINDOW_MINUTES = int(os.environ.get("ACTIVE_WINDOW_MINUTES", "15"))


@contextmanager
def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
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
        # Migrate existing databases that predate schema additions
        for stmt in (
            "ALTER TABLE sessions ADD COLUMN project_name TEXT",
        ):
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError:
                pass  # column already exists


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

def upsert_session(
    session_id: str,
    occurred_at: str,
    user_id: str | None,
    organization_id: str | None,
) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO sessions (
                session_id, user_id, organization_id, first_seen_at, last_seen_at, project_name
            )
            VALUES (?, ?, ?, ?, ?, (SELECT project_name FROM user_projects WHERE user_id = ?))
            ON CONFLICT(session_id) DO UPDATE SET
                last_seen_at = excluded.last_seen_at,
                user_id = COALESCE(sessions.user_id, excluded.user_id),
                organization_id = COALESCE(sessions.organization_id, excluded.organization_id),
                -- Picks up a user->project mapping created after the session started,
                -- without ever overwriting a label set by hand on this session.
                project_name = COALESCE(sessions.project_name, excluded.project_name)
            """,
            (session_id, user_id, organization_id, occurred_at, occurred_at, user_id),
        )


def update_session_project(session_id: str, project_name: str | None) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE sessions SET project_name = ? WHERE session_id = ?",
            (project_name, session_id),
        )


def insert_event(event: LogEvent) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO usage_events (
                session_id, occurred_at, event_name, model,
                input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens,
                cost_usd, raw_attributes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
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
                json.dumps(event.raw_attributes),
            ),
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
        marks = ",".join("?" * len(ids))
        conn.execute(f"DELETE FROM usage_events WHERE session_id IN ({marks})", ids)
        conn.execute(f"DELETE FROM sessions WHERE session_id IN ({marks})", ids)
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
        cur = conn.execute(
            "UPDATE sessions SET project_name = ? WHERE user_id = ?",
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
            conn.execute("UPDATE sessions SET project_name = NULL WHERE user_id = ?", (user_id,))


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
