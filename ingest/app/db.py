import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .otlp import LogEvent

# DB_PATH is overridable so a container can point it at a mounted volume
# (e.g. /data/usage.db) instead of the package directory.
DB_PATH = Path(os.environ.get("DB_PATH", str(Path(__file__).resolve().parent.parent / "usage.db")))
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"


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
        try:
            conn.execute("ALTER TABLE sessions ADD COLUMN project_name TEXT")
        except sqlite3.OperationalError:
            pass  # column already exists


def upsert_session(
    session_id: str,
    occurred_at: str,
    user_id: str | None,
    organization_id: str | None,
) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO sessions (session_id, user_id, organization_id, first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                last_seen_at = excluded.last_seen_at,
                user_id = COALESCE(sessions.user_id, excluded.user_id),
                organization_id = COALESCE(sessions.organization_id, excluded.organization_id)
            """,
            (session_id, user_id, organization_id, occurred_at, occurred_at),
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


def fetch_summary(active_within_minutes: int = 15) -> dict:
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
            "SELECT COUNT(*) AS active_sessions FROM sessions WHERE last_seen_at >= datetime('now', ?)",
            (f"-{active_within_minutes} minutes",),
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


def _time_bucket_fmt(hours: int) -> str:
    """Use hourly buckets for short windows, daily for anything over 2 days."""
    return '%Y-%m-%dT%H:00:00Z' if hours <= 48 else '%Y-%m-%dT00:00:00Z'


def fetch_usage_over_time(hours: int = 24) -> list[dict]:
    """Return cost and token totals bucketed by hour or day depending on the window."""
    fmt = _time_bucket_fmt(hours)
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
            WHERE occurred_at >= datetime('now', ?)
            GROUP BY bucket
            ORDER BY bucket
            """,
            (f"-{hours} hours",),
        ).fetchall()
        return [dict(row) for row in rows]


def fetch_usage_over_time_by_project(hours: int = 24) -> list[dict]:
    """Return cost/token totals grouped by project_name, bucketed by hour or day."""
    fmt = _time_bucket_fmt(hours)
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
            WHERE e.occurred_at >= datetime('now', ?)
            GROUP BY bucket, COALESCE(s.project_name, '(untagged)')
            ORDER BY bucket, project_name
            """,
            (f"-{hours} hours",),
        ).fetchall()
        return [dict(row) for row in rows]
