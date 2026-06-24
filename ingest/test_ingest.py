"""Smoke test: posts a sample OTLP/JSON log payload, checks it lands in
SQLite, and verifies the optional bearer-token auth gate.

Run with: python3 test_ingest.py
"""

import importlib
import os
import sqlite3
import time

from fastapi.testclient import TestClient

import app.db as db_module
import app.main as main_module

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


def _reset_db() -> None:
    if db_module.DB_PATH.exists():
        db_module.DB_PATH.unlink()


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


def main() -> None:
    test_ingest_without_auth()
    test_auth_gating()


if __name__ == "__main__":
    main()
