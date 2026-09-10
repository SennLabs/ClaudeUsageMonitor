"""Smoke test: posts a sample OTLP/JSON log payload, checks it lands in
SQLite, and verifies the optional bearer-token auth gate.

Run with: python3 test_ingest.py
"""

import atexit
import importlib
import os
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

# Point the suite at a scratch database BEFORE importing app.db, which resolves
# DB_PATH once at import time. Without this the tests delete ingest/usage.db —
# the same file local development writes to — on every run.
_TMP_DIR = tempfile.mkdtemp(prefix="claude-usage-tests-")
os.environ["DB_PATH"] = str(Path(_TMP_DIR) / "test_usage.db")
atexit.register(shutil.rmtree, _TMP_DIR, True)

import app.db as db_module  # noqa: E402  (must follow the DB_PATH assignment)
import app.main as main_module  # noqa: E402

SAMPLE_PAYLOAD = {
    "resourceLogs": [
        {
            "resource": {
                "attributes": [
                    {"key": "user.id", "value": {"stringValue": "user-123"}},
                    {"key": "organization.id", "value": {"stringValue": "org-456"}},
                ]
            },
            "scopeLogs": [
                {
                    "logRecords": [
                        {
                            "timeUnixNano": str(int(time.time() * 1_000_000_000)),
                            "body": {"stringValue": "claude_code.api_request"},
                            "attributes": [
                                {"key": "session.id", "value": {"stringValue": "session-abc"}},
                                {"key": "model", "value": {"stringValue": "claude-opus-4-8"}},
                                {"key": "input_tokens", "value": {"intValue": "1200"}},
                                {"key": "output_tokens", "value": {"intValue": "340"}},
                                {"key": "cache_read_tokens", "value": {"intValue": "800"}},
                                {"key": "cost_usd", "value": {"doubleValue": 0.0231}},
                            ],
                        }
                    ]
                }
            ],
        }
    ]
}



def make_payload(
    session_id: str,
    *,
    user_id: str = "user-123",
    input_tokens: int | None = 1200,
    output_tokens: int | None = 340,
    cost_usd: float | None = 0.0231,
    age_seconds: float = 0.0,
    event_name: str = "claude_code.api_request",
) -> dict:
    """Build a minimal OTLP/JSON payload for one log record."""
    attrs = [
        {"key": "session.id", "value": {"stringValue": session_id}},
        {"key": "model", "value": {"stringValue": "claude-opus-4-8"}},
    ]
    if input_tokens is not None:
        attrs.append({"key": "input_tokens", "value": {"intValue": str(input_tokens)}})
    if output_tokens is not None:
        attrs.append({"key": "output_tokens", "value": {"intValue": str(output_tokens)}})
    if cost_usd is not None:
        attrs.append({"key": "cost_usd", "value": {"doubleValue": cost_usd}})
    return {
        "resourceLogs": [
            {
                "resource": {"attributes": [{"key": "user.id", "value": {"stringValue": user_id}}]},
                "scopeLogs": [
                    {
                        "logRecords": [
                            {
                                "timeUnixNano": str(int((time.time() - age_seconds) * 1_000_000_000)),
                                "body": {"stringValue": event_name},
                                "attributes": attrs,
                            }
                        ]
                    }
                ],
            }
        ]
    }


def _reset_db() -> None:
    """Drop the scratch database, including the WAL sidecars."""
    assert db_module.DB_PATH.parent == Path(_TMP_DIR), (
        f"refusing to delete {db_module.DB_PATH} — tests must run against the scratch DB"
    )
    for suffix in ("", "-wal", "-shm"):
        path = Path(str(db_module.DB_PATH) + suffix)
        if path.exists():
            path.unlink()


def test_ingest_without_auth() -> None:
    _reset_db()

    # TestClient only runs the app's lifespan (startup/shutdown) when used as
    # a context manager - without `with`, db.init_db() never runs.
    with TestClient(main_module.app) as client:
        health = client.get("/healthz")
        assert health.status_code == 200, health.text

        response = client.post("/v1/logs", json=SAMPLE_PAYLOAD)
        assert response.status_code == 200, response.text

        summary = client.get("/api/summary")
        assert summary.status_code == 200, summary.text

    conn = sqlite3.connect(db_module.DB_PATH)
    session_row = conn.execute("SELECT session_id, user_id, organization_id FROM sessions").fetchone()
    event_row = conn.execute(
        "SELECT session_id, model, input_tokens, output_tokens, cache_read_tokens, cost_usd FROM usage_events"
    ).fetchone()
    conn.close()

    assert session_row == ("session-abc", "user-123", "org-456"), session_row
    assert event_row == ("session-abc", "claude-opus-4-8", 1200, 340, 800, 0.0231), event_row

    print("OK (no auth) - session and usage_event rows inserted correctly:")
    print("  session:", session_row)
    print("  event:  ", event_row)


def test_auth_gating() -> None:
    """INGEST_AUTH_TOKEN must gate both /v1/logs and /api/* once set."""
    _reset_db()
    os.environ["INGEST_AUTH_TOKEN"] = "test-token"
    try:
        importlib.reload(main_module)
        with TestClient(main_module.app) as client:
            no_header = client.post("/v1/logs", json=SAMPLE_PAYLOAD)
            assert no_header.status_code == 401, no_header.text

            wrong_header = client.get("/api/summary", headers={"Authorization": "Bearer wrong"})
            assert wrong_header.status_code == 401, wrong_header.text

            right_ingest = client.post(
                "/v1/logs", json=SAMPLE_PAYLOAD, headers={"Authorization": "Bearer test-token"}
            )
            assert right_ingest.status_code == 200, right_ingest.text

            right_read = client.get("/api/summary", headers={"Authorization": "Bearer test-token"})
            assert right_read.status_code == 200, right_read.text
    finally:
        del os.environ["INGEST_AUTH_TOKEN"]
        importlib.reload(main_module)

    print("OK (auth enabled) - missing/wrong token rejected on both routes, correct token accepted")



def test_user_project_mapping() -> None:
    """Linking a user id to a project labels its existing and future sessions."""
    _reset_db()
    with TestClient(main_module.app) as client:
        client.post("/v1/logs", json=make_payload("session-1", user_id="devcontainer-a"))

        # Mapping applies retroactively to sessions that already exist
        resp = client.put(
            "/api/user-projects/devcontainer-a", json={"project_name": "billing-api"}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["sessions_updated"] == 1, resp.json()

        sessions = client.get("/api/sessions").json()
        assert sessions[0]["project_name"] == "billing-api", sessions

        # ...and to sessions that turn up later
        client.post("/v1/logs", json=make_payload("session-2", user_id="devcontainer-a"))
        by_id = {s["session_id"]: s for s in client.get("/api/sessions").json()}
        assert by_id["session-2"]["project_name"] == "billing-api", by_id

        # A hand-set label on one session survives further events from that user
        client.patch("/api/sessions/session-2", json={"project_name": "spike"})
        client.post("/v1/logs", json=make_payload("session-2", user_id="devcontainer-a"))
        by_id = {s["session_id"]: s for s in client.get("/api/sessions").json()}
        assert by_id["session-2"]["project_name"] == "spike", by_id

        users = client.get("/api/users").json()
        assert len(users) == 1, users
        assert users[0]["user_id"] == "devcontainer-a", users
        assert users[0]["project_name"] == "billing-api", users
        assert users[0]["session_count"] == 2, users

        assert "billing-api" in client.get("/api/projects").json()

        # Clearing the mapping unlabels that user's sessions
        cleared = client.put("/api/user-projects/devcontainer-a", json={"project_name": ""})
        assert cleared.status_code == 200, cleared.text
        assert all(s["project_name"] is None for s in client.get("/api/sessions").json())

    print("OK (user mapping) - retroactive, forward-applying, and clearable")


def test_purge_empty_sessions() -> None:
    """Inactive sessions that never logged usage are removed; used ones are kept."""
    _reset_db()
    old = (db_module.ACTIVE_WINDOW_MINUTES + 5) * 60

    with TestClient(main_module.app) as client:
        # Started but never used, and now quiet
        client.post(
            "/v1/logs",
            json=make_payload(
                "empty-old",
                input_tokens=0,
                output_tokens=0,
                cost_usd=0.0,
                age_seconds=old,
                event_name="claude_code.session_start",
            ),
        )
        # Started but never used, still live
        client.post(
            "/v1/logs",
            json=make_payload(
                "empty-live",
                input_tokens=0,
                output_tokens=0,
                cost_usd=0.0,
                event_name="claude_code.session_start",
            ),
        )
        # Actually used, and now quiet
        client.post("/v1/logs", json=make_payload("used-old", age_seconds=old))

        removed = db_module.purge_empty_sessions()
        assert removed == 1, f"expected 1 purged, got {removed}"

        remaining = {s["session_id"] for s in client.get("/api/sessions").json()}
        assert remaining == {"empty-live", "used-old"}, remaining

        conn = sqlite3.connect(db_module.DB_PATH)
        orphans = conn.execute(
            "SELECT COUNT(*) FROM usage_events WHERE session_id = 'empty-old'"
        ).fetchone()[0]
        conn.close()
        assert orphans == 0, f"{orphans} orphaned events left behind"

    print("OK (purge) - empty inactive session and its events removed, others kept")


def test_time_windows() -> None:
    """hours bounds the window correctly; hours=0 means all time."""
    _reset_db()
    with TestClient(main_module.app) as client:
        client.post("/v1/logs", json=make_payload("recent"))
        client.post("/v1/logs", json=make_payload("stale", age_seconds=60 * 24 * 3600))
        # 30 hours back: inside the same calendar day boundary that a naive
        # string comparison against datetime('now') would wrongly include.
        client.post("/v1/logs", json=make_payload("yesterday", age_seconds=30 * 3600))

        day = client.get("/api/usage-over-time?hours=24").json()
        assert len(day) == 1, day

        month = client.get("/api/usage-over-time?hours=720").json()
        assert 2 <= len(month) <= 3, month

        everything = client.get("/api/usage-over-time?hours=0").json()
        assert len(everything) == 3, everything
        assert all(b["bucket"].endswith("T00:00:00Z") for b in everything), everything

        by_project = client.get("/api/usage-over-time-by-project?hours=0").json()
        assert len(by_project) == 3, by_project

    print("OK (windows) - 24h excludes 30h-old data, hours=0 returns all time")


def main() -> None:
    print(f"Scratch database: {db_module.DB_PATH}\n")
    test_ingest_without_auth()
    test_auth_gating()
    test_user_project_mapping()
    test_purge_empty_sessions()
    test_time_windows()


if __name__ == "__main__":
    main()
