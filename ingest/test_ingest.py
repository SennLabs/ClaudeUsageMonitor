"""
End-to-end tests for the ingest service.

Every test drives the real FastAPI app through TestClient against a scratch
SQLite database, rather than calling db.py directly — the interesting failures
have historically been in the seams (the OTLP envelope, transaction boundaries,
the auth dependency, the lifespan), not in a single function.

Run with:

    .venv/bin/python -m pytest            # the whole suite
    .venv/bin/python -m pytest -k metrics # one area
    .venv/bin/python -m pytest -x -vv     # stop at the first failure, verbose

DB_PATH is redirected to a scratch directory in conftest.py, which pytest loads
before it imports this module. See the comment there.
"""

import csv
import importlib
import io
import os
import shutil
import sqlite3
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

import app.db as db_module
import app.main as main_module

from conftest import SCRATCH_DIR

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
    project: str | None = None,
    extra_attrs: list | None = None,
    app_version: str | None = None,
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
    attrs.extend(extra_attrs or [])
    resource_attrs = [{"key": "user.id", "value": {"stringValue": user_id}}]
    if project is not None:
        # What OTEL_RESOURCE_ATTRIBUTES=project=<name> puts on the wire
        resource_attrs.append({"key": "project", "value": {"stringValue": project}})
    if app_version is not None:
        resource_attrs.append({"key": "app.version", "value": {"stringValue": app_version}})
    return {
        "resourceLogs": [
            {
                "resource": {"attributes": resource_attrs},
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


def sessions_of(client) -> list:
    """/api/sessions returns an envelope so callers can see when it is truncated."""
    body = client.get("/api/sessions").json()
    assert set(body) == {"total", "limit", "offset", "sessions"}, body
    return body["sessions"]


def _reset_db() -> None:
    """Drop the scratch database, including the WAL sidecars."""
    assert db_module.DB_PATH.parent == SCRATCH_DIR, (
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

        sessions = sessions_of(client)
        assert sessions[0]["project_name"] == "billing-api", sessions

        # ...and to sessions that turn up later
        client.post("/v1/logs", json=make_payload("session-2", user_id="devcontainer-a"))
        by_id = {s["session_id"]: s for s in sessions_of(client)}
        assert by_id["session-2"]["project_name"] == "billing-api", by_id

        # A hand-set label on one session survives further events from that user
        client.patch("/api/sessions/session-2", json={"project_name": "spike"})
        client.post("/v1/logs", json=make_payload("session-2", user_id="devcontainer-a"))
        by_id = {s["session_id"]: s for s in sessions_of(client)}
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
        assert all(s["project_name"] is None for s in sessions_of(client))



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

        remaining = {s["session_id"] for s in sessions_of(client)}
        assert remaining == {"empty-live", "used-old"}, remaining

        conn = sqlite3.connect(db_module.DB_PATH)
        orphans = conn.execute(
            "SELECT COUNT(*) FROM usage_events WHERE session_id = 'empty-old'"
        ).fetchone()[0]
        conn.close()
        assert orphans == 0, f"{orphans} orphaned events left behind"



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




def test_no_cors_headers() -> None:
    """
    The API must not advertise itself to other origins. nginx injects the bearer
    token server-side, so a permissive Access-Control-Allow-Origin would let any
    page on the network read the dashboard's data without holding the token.
    """
    _reset_db()
    with TestClient(main_module.app) as client:
        resp = client.get("/api/summary", headers={"Origin": "https://evil.example"})
        assert resp.status_code == 200, resp.text
        assert "access-control-allow-origin" not in {k.lower() for k in resp.headers}, dict(resp.headers)

        preflight = client.options(
            "/api/sessions",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "DELETE",
            },
        )
        assert preflight.status_code in (405, 404), preflight.status_code



def test_docs_endpoints_disabled() -> None:
    """Schema endpoints are unauthenticated by default; they must not be served."""
    _reset_db()
    with TestClient(main_module.app) as client:
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert client.get(path).status_code == 404, path


def test_refuses_to_start_without_a_token() -> None:
    """
    An unset token must fail loudly rather than silently disabling auth.

    This reloads app.main, so it must leave a working module behind for
    whatever runs next — hence the reload in the `finally`. It used to be
    pinned last in the hand-rolled runner; pytest gives no such ordering, so
    the restoration has to be unconditional rather than relied on by
    convention.
    """
    saved = os.environ.pop("INGEST_ALLOW_ANONYMOUS", None)
    try:
        importlib.reload(main_module)
    except RuntimeError as exc:
        assert "INGEST_AUTH_TOKEN" in str(exc), exc
    else:
        raise AssertionError("expected a RuntimeError with no token and no opt-in")
    finally:
        if saved is not None:
            os.environ["INGEST_ALLOW_ANONYMOUS"] = saved
        importlib.reload(main_module)




def test_duplicate_batches_are_ignored() -> None:
    """An exporter retrying a 5xx resends the identical batch; it must not double-count."""
    _reset_db()
    with TestClient(main_module.app) as client:
        payload = make_payload("retry-1")
        assert client.post("/v1/logs", json=payload).status_code == 200
        first = client.get("/api/summary").json()
        # Same batch again, byte for byte
        assert client.post("/v1/logs", json=payload).status_code == 200
        second = client.get("/api/summary").json()

        assert first == second, f"totals changed on replay: {first} -> {second}"

        conn = sqlite3.connect(db_module.DB_PATH)
        rows = conn.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0]
        conn.close()
        assert rows == 1, f"expected 1 stored event, got {rows}"

        # A genuinely different event still lands
        assert client.post("/v1/logs", json=make_payload("retry-2")).status_code == 200
        assert client.get("/api/summary").json()["total_sessions"] == 2



def test_malformed_records_do_not_lose_the_batch() -> None:
    """One unparseable record is dropped; the rest of the batch still commits."""
    _reset_db()
    with TestClient(main_module.app) as client:
        payload = make_payload("good-1")
        records = payload["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
        # A value shape the parser cannot turn into a column
        records.append({
            "timeUnixNano": str(int(time.time() * 1_000_000_000)),
            "body": {"stringValue": "claude_code.api_request"},
            "attributes": [
                {"key": "session.id", "value": {"stringValue": "bad-1"}},
                {"key": "model", "value": {"kvlistValue": {"values": []}}},
            ],
        })
        records.append(make_payload("good-2")["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0])

        resp = client.post("/v1/logs", json=payload)
        assert resp.status_code == 200, resp.text

        ids = {s["session_id"] for s in sessions_of(client)}
        assert {"good-1", "good-2"} <= ids, ids



def test_malformed_envelope_is_a_400() -> None:
    """A structurally invalid body is a client error, not a server error."""
    _reset_db()
    with TestClient(main_module.app) as client:
        for bad in ([], "nonsense", {"resourceLogs": "not-a-list"}, {}):
            resp = client.post("/v1/logs", json=bad)
            assert resp.status_code == 400, f"{bad!r} -> {resp.status_code}"

        # null-shaped members must not 500 either
        for bad in (
            {"resourceLogs": [{"resource": None, "scopeLogs": None}]},
            {"resourceLogs": [{"resource": {"attributes": None}, "scopeLogs": []}]},
        ):
            resp = client.post("/v1/logs", json=bad)
            assert resp.status_code == 200, f"{bad!r} -> {resp.status_code} {resp.text}"



def test_cost_micros_recorded() -> None:
    """Integer millionths are stored so SUM() does not accumulate float error."""
    _reset_db()
    with TestClient(main_module.app) as client:
        client.post("/v1/logs", json=make_payload("micros-1", cost_usd=0.0231))
        conn = sqlite3.connect(db_module.DB_PATH)
        micros = conn.execute("SELECT cost_usd_micros FROM usage_events").fetchone()[0]
        conn.close()
        assert micros == 23100, micros


def test_oversized_body_rejected() -> None:
    """A declared body over the cap is refused before it is buffered."""
    _reset_db()
    with TestClient(main_module.app) as client:
        resp = client.post(
            "/v1/logs",
            content=b"{}",
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(main_module.MAX_BODY_BYTES + 1),
            },
        )
        assert resp.status_code == 413, resp.status_code



def test_project_from_resource_attribute() -> None:
    """A container that declares its own project needs no mapping at all."""
    _reset_db()
    with TestClient(main_module.app) as client:
        client.post("/v1/logs", json=make_payload("rp-1", user_id="ctr-a", project="radiology-pacs"))
        row = sessions_of(client)[0]
        assert row["project_name"] == "radiology-pacs", row
        assert row["project_source"] == "resource", row

        # The identity can churn — a rebuilt container reports a new user.id —
        # and attribution still lands, which is the whole point.
        client.post("/v1/logs", json=make_payload("rp-2", user_id="ctr-a-rebuilt", project="radiology-pacs"))
        by_id = {s["session_id"]: s for s in sessions_of(client)}
        assert by_id["rp-2"]["project_name"] == "radiology-pacs", by_id

        # Re-pointing the container relabels its in-flight session
        client.post("/v1/logs", json=make_payload("rp-1", user_id="ctr-a", project="billing"))
        by_id = {s["session_id"]: s for s in sessions_of(client)}
        assert by_id["rp-1"]["project_name"] == "billing", by_id



def test_project_precedence() -> None:
    """manual > resource > user_map, and the source is recorded."""
    _reset_db()
    with TestClient(main_module.app) as client:
        # user_map fills a gap when nothing else says otherwise
        client.post("/v1/logs", json=make_payload("pp-1", user_id="ctr-b"))
        client.put("/api/user-projects/ctr-b", json={"project_name": "from-mapping"})
        row = {s["session_id"]: s for s in sessions_of(client)}["pp-1"]
        assert (row["project_name"], row["project_source"]) == ("from-mapping", "user_map"), row

        # resource beats user_map
        client.post("/v1/logs", json=make_payload("pp-1", user_id="ctr-b", project="from-container"))
        row = {s["session_id"]: s for s in sessions_of(client)}["pp-1"]
        assert (row["project_name"], row["project_source"]) == ("from-container", "resource"), row

        # manual beats resource, and survives further events
        client.patch("/api/sessions/pp-1", json={"project_name": "by-hand"})
        client.post("/v1/logs", json=make_payload("pp-1", user_id="ctr-b", project="from-container"))
        row = {s["session_id"]: s for s in sessions_of(client)}["pp-1"]
        assert (row["project_name"], row["project_source"]) == ("by-hand", "manual"), row

        # a user mapping must not clobber a container-declared project
        client.post("/v1/logs", json=make_payload("pp-2", user_id="ctr-b", project="from-container"))
        client.put("/api/user-projects/ctr-b", json={"project_name": "remapped"})
        row = {s["session_id"]: s for s in sessions_of(client)}["pp-2"]
        assert row["project_name"] == "from-container", row




def test_settings_round_trip() -> None:
    """Settings live on the server so every viewer sees the same values."""
    _reset_db()
    with TestClient(main_module.app) as client:
        defaults = client.get("/api/settings").json()
        assert defaults["refreshIntervalMs"] == 5000, defaults
        assert defaults["monthlyBudget"] is None, defaults
        # Read-only, supplied by the operator's environment
        assert defaults["activeSessionWindowMin"] == db_module.ACTIVE_WINDOW_MINUTES

        updated = client.put("/api/settings", json={"monthlyBudget": 250, "defaultTimeWindow": "7d"}).json()
        assert updated["monthlyBudget"] == 250 and updated["defaultTimeWindow"] == "7d", updated
        # A partial update must not reset the rest
        assert updated["refreshIntervalMs"] == 5000, updated
        assert client.get("/api/settings").json()["monthlyBudget"] == 250

        # Read-only keys are ignored, not honoured
        client.put("/api/settings", json={"activeSessionWindowMin": 999})
        assert client.get("/api/settings").json()["activeSessionWindowMin"] == db_module.ACTIVE_WINDOW_MINUTES

        for bad in ({"billingCycleDay": 31}, {"refreshIntervalMs": 10},
                    {"defaultMetric": "bananas"}, {"monthlyBudget": -5}):
            assert client.put("/api/settings", json=bad).status_code == 400, bad



def test_budget_cycle() -> None:
    """The budget bar measures the current billing period, not all time."""
    import datetime as _dt
    _reset_db()

    # cycle_start walks back to last month when today is before the cycle day
    jan15 = _dt.datetime(2026, 1, 15, 12, 0, tzinfo=_dt.timezone.utc)
    assert db_module.cycle_start(1, jan15).isoformat().startswith("2026-01-01"), "same month"
    assert db_module.cycle_start(20, jan15).isoformat().startswith("2025-12-20"), "previous month"
    mar3 = _dt.datetime(2026, 3, 3, 0, 30, tzinfo=_dt.timezone.utc)
    assert db_module.cycle_start(28, mar3).isoformat().startswith("2026-02-28"), "across February"

    with TestClient(main_module.app) as client:
        client.post("/v1/logs", json=make_payload("b-old", age_seconds=75 * 24 * 3600, cost_usd=99.0))
        client.post("/v1/logs", json=make_payload("b-new", cost_usd=1.5))

        all_time = client.get("/api/summary").json()["total_cost_usd"]
        cycle = client.get("/api/budget?cycle_day=1").json()

        assert all_time > 100, all_time
        assert cycle["cost_usd"] == 1.5, cycle
        assert cycle["cycle_start"] <= _dt.datetime.now(_dt.timezone.utc).isoformat()




def test_promoted_attributes_are_queryable() -> None:
    """Attributes Claude Code always sent are now columns, not just JSON."""
    _reset_db()
    with TestClient(main_module.app) as client:
        client.post("/v1/logs", json=make_payload("ins-1", app_version="2.1.263", extra_attrs=[
            {"key": "duration_ms", "value": {"intValue": "1200"}},
            {"key": "query_source", "value": {"stringValue": "subagent"}},
            {"key": "effort", "value": {"stringValue": "xhigh"}},
            {"key": "speed", "value": {"stringValue": "fast"}},
            {"key": "agent.name", "value": {"stringValue": "Explore"}},
            {"key": "skill.name", "value": {"stringValue": "code-review"}},
            {"key": "mcp_server.name", "value": {"stringValue": "custom"}},
            {"key": "prompt.id", "value": {"stringValue": "p-123"}},
            {"key": "terminal.type", "value": {"stringValue": "vscode"}},
        ]))
        client.post("/v1/logs", json=make_payload("ins-2", cost_usd=5.0, extra_attrs=[
            {"key": "duration_ms", "value": {"intValue": "400"}},
            {"key": "query_source", "value": {"stringValue": "main"}},
        ]))

        attr = client.get("/api/attribution?hours=0").json()
        sources = {r["name"]: r for r in attr["by_query_source"]}
        assert set(sources) == {"main", "subagent"}, sources
        assert sources["main"]["cost_usd"] == 5.0, sources
        assert {r["name"] for r in attr["by_agent"]} == {"Explore", "(none)"}, attr["by_agent"]
        assert {r["name"] for r in attr["by_effort"]} == {"xhigh", "(none)"}, attr["by_effort"]

        lat = client.get("/api/latency?hours=0").json()
        assert lat["requests"] == 2 and lat["max_ms"] == 1200, lat
        assert lat["p50_ms"] in (400, 1200) and lat["p95_ms"] == 1200, lat

        fleet = client.get("/api/fleet").json()
        assert any(f["app_version"] == "2.1.263" for f in fleet), fleet



def test_errors_and_tools_views() -> None:
    """api_error, api_refusal and tool_result events finally surface."""
    _reset_db()
    with TestClient(main_module.app) as client:
        client.post("/v1/logs", json=make_payload("e-1"))
        client.post("/v1/logs", json=make_payload(
            "e-1", event_name="claude_code.api_error", cost_usd=None,
            extra_attrs=[{"key": "status_code", "value": {"intValue": "429"}},
                         {"key": "attempt", "value": {"intValue": "3"}}]))
        client.post("/v1/logs", json=make_payload(
            "e-1", event_name="claude_code.api_refusal", cost_usd=None,
            extra_attrs=[{"key": "category", "value": {"stringValue": "cyber"}}]))
        client.post("/v1/logs", json=make_payload(
            "e-1", event_name="claude_code.tool_result", cost_usd=None,
            extra_attrs=[{"key": "tool_name", "value": {"stringValue": "Bash"}},
                         {"key": "success", "value": {"stringValue": "false"}},
                         {"key": "duration_ms", "value": {"intValue": "900"}},
                         {"key": "error_type", "value": {"stringValue": "ShellError"}}]))

        errs = client.get("/api/errors?hours=0").json()
        assert errs["errors"] == 1 and errs["refusals"] == 1, errs
        assert errs["retried"] == 1, errs
        assert errs["by_status_code"][0]["status_code"] == 429, errs
        assert errs["by_refusal_category"][0]["category"] == "cyber", errs

        tools = client.get("/api/tools?hours=0").json()
        assert tools[0]["tool_name"] == "Bash", tools
        assert tools[0]["failures"] == 1 and tools[0]["max_ms"] == 900, tools



def test_unattributed_events_are_reported() -> None:
    """Events with no session.id no longer silently diverge from the views."""
    _reset_db()
    with TestClient(main_module.app) as client:
        client.post("/v1/logs", json=make_payload("u-1", cost_usd=1.0))
        client.post("/v1/logs", json=make_payload(None, cost_usd=9.0))

        summary = client.get("/api/summary").json()
        per_session = sum(s["cost_usd"] for s in sessions_of(client))
        assert summary["total_cost_usd"] == 10.0, summary
        assert per_session == 1.0, per_session
        assert summary["unattributed_events"] == 1, summary
        assert summary["unattributed_cost_usd"] == 9.0, summary




def test_backup_configuration_guards() -> None:
    """Every backup misconfiguration that used to report success."""
    import app.backup as backup_module

    saved = {k: os.environ.get(k) for k in
             ("BACKUP_DESTINATION", "BACKUP_MODE", "BACKUP_INTERVAL_HOURS", "BACKUP_KEEP")}

    def reload_with(**env):
        for key in saved:
            os.environ.pop(key, None)
        os.environ.update({k: str(v) for k, v in env.items()})
        importlib.reload(backup_module)
        return backup_module

    root = Path(tempfile.mkdtemp(prefix="claude-usage-backup-"))
    db_dir = root / "data"; db_dir.mkdir()
    dest = root / "backups"; dest.mkdir()
    db_file = db_dir / "usage.db"
    sqlite3.connect(db_file).execute("CREATE TABLE t (x)")

    try:
        # Rejected outright — backups disabled, reason reported, service unaffected
        for env, fragment in (
            ({"BACKUP_DESTINATION": "nas:/volume1/backups"}, "cannot tell whether"),
            ({"BACKUP_DESTINATION": dest, "BACKUP_INTERVAL_HOURS": 0}, "below the minimum"),
            ({"BACKUP_DESTINATION": dest, "BACKUP_INTERVAL_HOURS": "daily"}, "is not a number"),
            ({"BACKUP_DESTINATION": dest, "BACKUP_KEEP": 0}, "below the minimum"),
        ):
            b = reload_with(**env)
            assert b.ENABLED is False, env
            assert fragment in (b.status()["config_error"] or ""), b.status()

        # Accepted, but the run must fail loudly rather than report success
        for env, fragment in (
            ({"BACKUP_DESTINATION": db_dir}, "own directory"),
            ({"BACKUP_DESTINATION": root / "missing"}, "does not exist"),
        ):
            b = reload_with(**env)
            b.run_backup(db_file)
            assert b.status()["last_backup_ok"] is False, env
            assert fragment in b.status()["last_backup_error"], b.status()

        # A working local destination, with retention actually applied
        b = reload_with(BACKUP_DESTINATION=dest, BACKUP_KEEP=2)
        for _ in range(3):
            b.run_backup(db_file)
        assert b.status()["last_backup_ok"] is True, b.status()
        assert len(list(dest.glob("usage_backup_*.db"))) == 2, list(dest.iterdir())

        # Concurrency: the second caller is told, not silently collided with
        b._RUN_LOCK.acquire()
        try:
            b.run_backup(db_file)
        except b.BackupBusy:
            pass
        else:
            raise AssertionError("expected BackupBusy while a run holds the lock")
        finally:
            b._RUN_LOCK.release()

        # rsync mode is explicit about retention not applying
        b = reload_with(BACKUP_DESTINATION="user@nas:/volume1/b/", BACKUP_MODE="rsync")
        st = b.status()
        assert st["enabled"] and st["method"] == "rsync-ssh" and st["keep"] is None, st
        assert st["warnings"], st
    finally:
        for key, value in saved.items():
            os.environ.pop(key, None)
            if value is not None:
                os.environ[key] = value
        importlib.reload(backup_module)
        shutil.rmtree(root, ignore_errors=True)




def test_retention_and_maintenance() -> None:
    """Retention trims history; the sweep's state is observable."""
    import app.db as _db
    _reset_db()
    saved = (_db.RETENTION_DAYS, _db.RAW_ATTRIBUTES_RETENTION_DAYS)
    try:
        with TestClient(main_module.app) as client:
            client.post("/v1/logs", json=make_payload("keep-1", cost_usd=1.0))
            client.post("/v1/logs", json=make_payload("old-1", age_seconds=200 * 86400, cost_usd=2.0))
            client.post("/v1/logs", json=make_payload("mid-1", age_seconds=40 * 86400, cost_usd=3.0))

            # Clearing raw_attributes must not move a single number
            _db.RAW_ATTRIBUTES_RETENTION_DAYS, _db.RETENTION_DAYS = 30, 0
            before = client.get("/api/summary").json()["total_cost_usd"]
            result = _db.apply_retention()
            assert result["attributes_cleared"] == 2, result
            assert client.get("/api/summary").json()["total_cost_usd"] == before

            # Deleting events does change history, which is why it is opt-in
            _db.RETENTION_DAYS = 90
            result = _db.apply_retention()
            assert result["events_deleted"] == 1, result
            assert client.get("/api/summary").json()["total_cost_usd"] == 4.0
            assert "old-1" not in {s["session_id"] for s in sessions_of(client)}

            state = client.get("/api/maintenance").json()
            assert state["retention_days"] == 90, state
            assert set(state) >= {"last_run_at", "last_ok", "last_error", "sessions_purged"}, state
    finally:
        _db.RETENTION_DAYS, _db.RAW_ATTRIBUTES_RETENTION_DAYS = saved



def test_sessions_pagination() -> None:
    """The list is capped, and callers can tell that it is."""
    _reset_db()
    with TestClient(main_module.app) as client:
        for i in range(7):
            client.post("/v1/logs", json=make_payload(f"pg-{i}", age_seconds=i * 60))

        body = client.get("/api/sessions?limit=3").json()
        assert body["total"] == 7 and len(body["sessions"]) == 3, body

        page2 = client.get("/api/sessions?limit=3&offset=3").json()
        assert page2["offset"] == 3 and len(page2["sessions"]) == 3, page2
        first = {s["session_id"] for s in body["sessions"]}
        assert first.isdisjoint({s["session_id"] for s in page2["sessions"]}), "pages overlap"



def test_healthz_checks_the_database() -> None:
    """A static 200 reported healthy while the database was unusable."""
    _reset_db()
    with TestClient(main_module.app) as client:
        assert client.get("/healthz").status_code == 200

        original = db_module.DB_PATH
        db_module.DB_PATH = SCRATCH_DIR / "no-such-dir" / "missing.db"
        try:
            assert client.get("/healthz").status_code == 503
        finally:
            db_module.DB_PATH = original
        assert client.get("/healthz").status_code == 200




def test_cache_efficiency_and_prompts() -> None:
    """Cache hit ratio and per-prompt cost, from data already being stored."""
    _reset_db()
    with TestClient(main_module.app) as client:
        client.post("/v1/logs", json=make_payload(
            "ce-1", project="pacs", input_tokens=1000, cost_usd=1.0, extra_attrs=[
                {"key": "cache_read_tokens", "value": {"intValue": "3000"}},
                {"key": "cache_creation_tokens", "value": {"intValue": "500"}},
                {"key": "prompt.id", "value": {"stringValue": "p-1"}}]))
        client.post("/v1/logs", json=make_payload(
            "ce-1", project="pacs", input_tokens=1000, cost_usd=2.0, extra_attrs=[
                {"key": "prompt.id", "value": {"stringValue": "p-1"}}]))

        cache = client.get("/api/cache-efficiency?hours=0").json()
        # 3000 served from cache out of 5000 input tokens overall
        assert cache["overall"]["cache_read_tokens"] == 3000, cache
        assert cache["overall"]["hit_ratio"] == 0.6, cache
        assert cache["by_project"][0]["name"] == "pacs", cache

        prompts = client.get("/api/prompts?hours=0").json()
        assert len(prompts) == 1, prompts
        assert prompts[0]["prompt_id"] == "p-1", prompts
        assert prompts[0]["requests"] == 2 and prompts[0]["cost_usd"] == 3.0, prompts



def test_audit_view() -> None:
    """Permission-mode, auth and MCP events surface for the first time."""
    _reset_db()
    with TestClient(main_module.app) as client:
        client.post("/v1/logs", json=make_payload(
            "a-1", event_name="claude_code.permission_mode_changed", cost_usd=None,
            extra_attrs=[{"key": "from_mode", "value": {"stringValue": "default"}},
                         {"key": "to_mode", "value": {"stringValue": "bypassPermissions"}},
                         {"key": "trigger", "value": {"stringValue": "shift_tab"}}]))
        client.post("/v1/logs", json=make_payload(
            "a-1", event_name="claude_code.auth", cost_usd=None,
            extra_attrs=[{"key": "action", "value": {"stringValue": "login"}},
                         {"key": "success", "value": {"stringValue": "false"}},
                         {"key": "error_category", "value": {"stringValue": "network"}}]))
        client.post("/v1/logs", json=make_payload(
            "a-1", event_name="claude_code.mcp_server_connection", cost_usd=None,
            extra_attrs=[{"key": "status", "value": {"stringValue": "failed"}},
                         {"key": "transport_type", "value": {"stringValue": "stdio"}},
                         {"key": "mcp_server.name", "value": {"stringValue": "custom"}}]))

        audit = client.get("/api/audit?hours=0").json()
        assert audit["bypass_count"] == 1, audit
        assert audit["permission_changes"][0]["to_mode"] == "bypassPermissions", audit
        assert audit["auth_failures"][0]["error_category"] == "network", audit
        assert audit["mcp_connections"][0]["status"] == "failed", audit



def test_csv_export() -> None:
    """Export is the answer to 'justify this spend'."""
    _reset_db()
    with TestClient(main_module.app) as client:
        client.post("/v1/logs", json=make_payload("x-1", project="pacs", cost_usd=1.5))
        client.post("/v1/logs", json=make_payload("x-2", age_seconds=40 * 86400, cost_usd=9.0))

        resp = client.get("/api/export.csv")
        assert resp.status_code == 200, resp.text
        assert "text/csv" in resp.headers["content-type"], resp.headers
        assert "attachment" in resp.headers["content-disposition"], resp.headers

        rows = list(csv.DictReader(io.StringIO(resp.text)))
        assert len(rows) == 2, rows
        assert rows[0]["session_id"] == "x-2", rows  # ordered by time
        assert rows[1]["project_name"] == "pacs", rows

        # Bounded export
        cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        recent = list(csv.DictReader(io.StringIO(client.get(f"/api/export.csv?since={cutoff}").text)))
        assert len(recent) == 1 and recent[0]["session_id"] == "x-1", recent




def make_metrics_payload(
    metric: str,
    value: float,
    *,
    session_id: str = "session-abc",
    attrs: dict | None = None,
    age_seconds: float = 0.0,
    temporality: int = 1,
    project: str | None = None,
) -> dict:
    """Build a minimal OTLP/JSON ExportMetricsServiceRequest for one sum point."""
    end = time.time() - age_seconds
    resource_attrs = [{"key": "user.id", "value": {"stringValue": "user-123"}}]
    if project:
        resource_attrs.append({"key": "project", "value": {"stringValue": project}})
    point_attrs = [{"key": "session.id", "value": {"stringValue": session_id}}]
    for key, val in (attrs or {}).items():
        point_attrs.append({"key": key, "value": {"stringValue": val}})
    return {
        "resourceMetrics": [
            {
                "resource": {"attributes": resource_attrs},
                "scopeMetrics": [
                    {
                        "metrics": [
                            {
                                "name": metric,
                                "sum": {
                                    "aggregationTemporality": temporality,
                                    "isMonotonic": True,
                                    "dataPoints": [
                                        {
                                            "startTimeUnixNano": str(int((end - 60) * 1e9)),
                                            "timeUnixNano": str(int(end * 1e9)),
                                            "asInt": str(int(value)),
                                            "attributes": point_attrs,
                                        }
                                    ],
                                },
                            }
                        ]
                    }
                ],
            }
        ]
    }


def test_metrics_ingest_and_productivity_view() -> None:
    """R24/R25: the metrics stream lands, and the ratios come out of it."""
    _reset_db()
    with TestClient(main_module.app) as client:
        # $3.00 of spend on the logs stream, to divide by.
        assert client.post("/v1/logs", json=make_payload("m-1", cost_usd=3.0)).status_code == 200

        posts = [
            make_metrics_payload("claude_code.commit.count", 2, session_id="m-1"),
            make_metrics_payload("claude_code.pull_request.count", 1, session_id="m-1"),
            make_metrics_payload("claude_code.active_time.total", 5400, session_id="m-1",
                                 attrs={"type": "user"}),
            make_metrics_payload("claude_code.lines_of_code.count", 400, session_id="m-1",
                                 attrs={"type": "added"}),
            make_metrics_payload("claude_code.lines_of_code.count", 100, session_id="m-1",
                                 attrs={"type": "removed"}),
            make_metrics_payload("claude_code.session.count", 1, session_id="m-1",
                                 attrs={"start_type": "fresh"}),
            make_metrics_payload("claude_code.code_edit_tool.decision", 8, session_id="m-1",
                                 attrs={"decision": "accept", "language": "python"}),
            make_metrics_payload("claude_code.code_edit_tool.decision", 2, session_id="m-1",
                                 attrs={"decision": "reject", "language": "python"}),
        ]
        for payload in posts:
            resp = client.post("/v1/metrics", json=payload)
            assert resp.status_code == 200, resp.text

        data = client.get("/api/metrics?hours=24").json()
        assert data["reporting"] is True, data
        totals = data["totals"]
        assert totals["commits"] == 2 and totals["pull_requests"] == 1, totals
        assert totals["lines_added"] == 400 and totals["lines_removed"] == 100, totals
        assert totals["active_seconds"] == 5400, totals

        derived = data["derived"]
        assert abs(derived["cost_per_commit"] - 1.5) < 1e-9, derived
        assert abs(derived["cost_per_active_hour"] - 2.0) < 1e-9, derived   # 1.5h active
        assert abs(derived["usd_per_1k_lines"] - 6.0) < 1e-9, derived       # 500 lines
        assert derived["cost_per_pull_request"] == 3.0, derived
        # Nothing to divide by must read as "unknown", not as $0.00 — the two
        # are opposite statements and $0.00 per commit looks like a win. The
        # whole money()/'—' rendering chain depends on this being null.
        assert derived["lines_per_active_hour"] is not None, derived

        python = next(d for d in data["edit_decisions"] if d["language"] == "python")
        assert abs(python["acceptance_rate"] - 0.8) < 1e-9, python

        assert [s["name"] for s in data["sessions_by_start_type"]] == ["fresh"], data



def test_ratios_are_null_not_zero_without_a_denominator() -> None:
    """
    "No commits recorded" and "$0.00 per commit" are opposite statements.

    The second one looks like a win, so every ratio must come back null. The
    money()/'—' rendering chain on the dashboard depends on this.
    """
    _reset_db()
    with TestClient(main_module.app) as client:
        # Spend, but no metrics at all behind it.
        client.post("/v1/logs", json=make_payload("n-1", cost_usd=5.0))

        derived = client.get("/api/metrics?hours=24").json()["derived"]
        assert all(v is None for v in derived.values()), derived


def test_ratios_are_scoped_to_metrics_reporting_sessions() -> None:
    """
    A partial metrics rollout must not inflate every ratio.

    The counters (commits, lines, active time) exist only for clients with
    OTEL_METRICS_EXPORTER set, which is optional. Dividing fleet-wide cost by
    a partial fleet's commits is wrong by the inverse of the rollout — with a
    fifth of spend reporting metrics, cost-per-commit reads 5x high with
    nothing on screen to say so.
    """
    _reset_db()
    with TestClient(main_module.app) as client:
        # One session reports both streams: $2.00 and 2 commits.
        client.post("/v1/logs", json=make_payload("cov-metrics", cost_usd=2.0))
        assert client.post(
            "/v1/metrics",
            json=make_metrics_payload("claude_code.commit.count", 2, session_id="cov-metrics"),
        ).status_code == 200

        # Three more sessions spend $6.00 between them and report no metrics.
        for i, amount in enumerate((1.0, 2.0, 3.0)):
            client.post("/v1/logs", json=make_payload(f"cov-logs-{i}", cost_usd=amount))

        data = client.get("/api/metrics?hours=24").json()

        assert data["cost_usd_fleet"] == 8.0, data       # everything
        assert data["cost_usd"] == 2.0, data             # the metrics-reporting subset
        assert data["metrics_cost_coverage"] == 0.25, data
        # $2.00 over 2 commits. Against fleet cost this would have read $4.00.
        assert data["derived"]["cost_per_commit"] == 1.0, data["derived"]

        # And with a full rollout, coverage is 1 and the two agree.
        assert client.post(
            "/v1/metrics",
            json=make_metrics_payload("claude_code.commit.count", 1, session_id="cov-logs-0"),
        ).status_code == 200
        data = client.get("/api/metrics?hours=24").json()
        assert data["cost_usd"] == 3.0, data
        assert data["metrics_cost_coverage"] == 0.375, data


def test_gauges_are_reported_apart_from_cumulative_points() -> None:
    """
    A gauge carries no aggregationTemporality, so the delta advice cannot help.

    Counting it as "cumulative" produced a UI message telling the operator to
    set a client variable that would change nothing.
    """
    _reset_db()
    with TestClient(main_module.app) as client:
        gauge = {
            "resourceMetrics": [{
                "resource": {"attributes": []},
                "scopeMetrics": [{"metrics": [{
                    "name": "claude_code.some_gauge",
                    "gauge": {"dataPoints": [{
                        "timeUnixNano": str(int(time.time() * 1e9)),
                        "asInt": "17",
                        "attributes": [
                            {"key": "session.id", "value": {"stringValue": "g-1"}},
                        ],
                    }]},
                }]}],
            }]
        }
        assert client.post("/v1/metrics", json=gauge).status_code == 200

        data = client.get("/api/metrics?hours=24").json()
        assert data["reporting"] is True, data
        assert data["cumulative_points_ignored"] == 0, data
        assert data["unsummable_points"] == 1, data
        # Visible by name, contributing to no total.
        row = next(m for m in data["by_metric"] if m["metric_name"] == "claude_code.some_gauge")
        assert row["points"] == 1 and row["total"] == 0, row


def test_unsupported_metric_kind_is_counted_as_dropped() -> None:
    """An OTLP data kind this parser does not read must not vanish silently."""
    from app.otlp_metrics import extract_metric_points

    payload = {"resourceMetrics": [{"scopeMetrics": [{"metrics": [
        # `summary` is a valid OTLP kind that this parser does not handle.
        {"name": "legacy.summary", "summary": {"dataPoints": [{}, {}, {}]}},
        # A nameless metric carrying several points is several losses, not one.
        {"sum": {"aggregationTemporality": 1, "dataPoints": [{}, {}]}},
    ]}]}]}

    points, skipped = extract_metric_points(payload)
    assert points == [], points
    assert skipped == 5, skipped


def test_metrics_duplicates_and_cumulative() -> None:
    """A retried export must not double a delta, and cumulative must not be summed."""
    _reset_db()
    with TestClient(main_module.app) as client:
        payload = make_metrics_payload("claude_code.commit.count", 5, session_id="m-2")
        assert client.post("/v1/metrics", json=payload).status_code == 200
        assert client.post("/v1/metrics", json=payload).status_code == 200  # the retry

        data = client.get("/api/metrics?hours=24").json()
        assert data["totals"]["commits"] == 5, data  # not 10
        assert data["cumulative_points_ignored"] == 0, data

        # A cumulative point is a running total: summing the series would count
        # the same work once per export interval.
        cumulative = make_metrics_payload(
            "claude_code.commit.count", 99, session_id="m-3", temporality=2
        )
        assert client.post("/v1/metrics", json=cumulative).status_code == 200
        data = client.get("/api/metrics?hours=24").json()
        assert data["totals"]["commits"] == 5, data
        assert data["cumulative_points_ignored"] == 1, data


def test_cumulative_only_client_still_reports() -> None:
    """
    A client stuck on cumulative temporality must not read as "not reporting".

    It contributes to no total, so every figure is zero — but the fix is the
    temporality preference, not enabling the exporter. Reporting on delta
    points alone sent the UI to the wrong message.
    """
    _reset_db()
    with TestClient(main_module.app) as client:
        assert client.post(
            "/v1/metrics",
            json=make_metrics_payload(
                "claude_code.commit.count", 12, session_id="c-1", temporality=2
            ),
        ).status_code == 200

        data = client.get("/api/metrics?hours=24").json()
        assert data["reporting"] is True, data
        assert data["totals"]["commits"] == 0, data
        assert data["cumulative_points_ignored"] == 1, data



def test_metrics_payload_robustness() -> None:
    """Same contract as the logs path: bad envelope 400s, bad points are dropped."""
    _reset_db()
    with TestClient(main_module.app) as client:
        assert client.post("/v1/metrics", json={"nope": []}).status_code == 400
        assert client.post("/v1/metrics", json=[]).status_code == 400
        assert client.post("/v1/metrics", content=b"{").status_code == 400

        # One usable point, one valued-but-unrecognised metric, and two
        # genuinely unusable ones (no name; a value-less point).
        payload = make_metrics_payload("claude_code.commit.count", 3, session_id="m-4")
        metrics = payload["resourceMetrics"][0]["scopeMetrics"][0]["metrics"]
        metrics.append({"sum": {"aggregationTemporality": 1, "dataPoints": [
            {"timeUnixNano": str(int(time.time() * 1e9)), "asInt": "7"},
        ]}})
        metrics.append({
            "name": "claude_code.nameless_point",
            "sum": {"aggregationTemporality": 1, "dataPoints": [{"timeUnixNano": "0"}]},
        })
        metrics.append({
            "name": "claude_code.mystery",
            "sum": {
                "aggregationTemporality": 1,
                "dataPoints": [
                    {"timeUnixNano": str(int(time.time() * 1e9)), "asInt": "42"},
                ],
            },
        })
        assert client.post("/v1/metrics", json=payload).status_code == 200

        data = client.get("/api/metrics?hours=24").json()
        assert data["totals"]["commits"] == 3, data
        # An unrecognised metric must stay visible — that is what storing
        # metrics by name rather than against a fixed list buys, and metric
        # naming has shifted across Claude Code versions before.
        mystery = next(
            (m for m in data["by_metric"] if m["metric_name"] == "claude_code.mystery"), None
        )
        assert mystery is not None, data["by_metric"]
        assert mystery["points"] == 1 and mystery["total"] == 42, mystery



def test_metrics_only_session_is_not_purged() -> None:
    """Active time and commits arrive with no billable API call behind them."""
    _reset_db()
    with TestClient(main_module.app) as client:
        assert client.post(
            "/v1/metrics",
            json=make_metrics_payload(
                "claude_code.active_time.total", 120, session_id="m-5",
                age_seconds=3600, project="pacs",
            ),
        ).status_code == 200

        removed = db_module.purge_empty_sessions(minutes=1)
        assert removed == 0, "a session whose only record is metrics was purged"

        # Resource attributes ride on the metrics stream too, so attribution works.
        sessions = client.get("/api/sessions").json()["sessions"]
        row = next(s for s in sessions if s["session_id"] == "m-5")
        assert row["project_name"] == "pacs", row




def test_display_timezone_buckets_local_days() -> None:
    """R10: a UTC day boundary is nobody's day boundary in UTC+8."""
    _reset_db()
    with TestClient(main_module.app) as client:
        # 2h ago and 10h ago. In UTC+8 those can straddle a different midnight
        # than they do in UTC, which is the whole point — so pick timestamps
        # that pin it down rather than relying on when the suite happens to run.
        now = datetime.now(timezone.utc)
        # 22:00 UTC = 06:00 next day in Perth.
        late = now.replace(hour=22, minute=0, second=0, microsecond=0)
        if late > now:
            late -= timedelta(days=1)
        age = (now - late).total_seconds()
        assert client.post(
            "/v1/logs", json=make_payload("tz-1", age_seconds=age, cost_usd=1.0)
        ).status_code == 200

        # Default (UTC): the bucket is the UTC day, named with a Z.
        utc_rows = client.get("/api/usage-over-time?hours=0").json()
        assert len(utc_rows) == 1, utc_rows
        utc_bucket = utc_rows[0]["bucket"]
        assert utc_bucket.endswith("Z"), utc_bucket
        assert utc_bucket[:10] == late.strftime("%Y-%m-%d"), utc_bucket

        # Switch to Perth: same event, the next local day, and the bucket now
        # carries a real offset so the label is unambiguous.
        assert client.put(
            "/api/settings", json={"displayTimeZone": "Australia/Perth"}
        ).status_code == 200
        perth_rows = client.get("/api/usage-over-time?hours=0").json()
        assert len(perth_rows) == 1, perth_rows
        perth_bucket = perth_rows[0]["bucket"]
        assert perth_bucket.endswith("+08:00"), perth_bucket
        expected = (late + timedelta(hours=8)).strftime("%Y-%m-%d")
        assert perth_bucket[:10] == expected, (perth_bucket, expected)
        assert perth_bucket[:10] != utc_bucket[:10], "22:00 UTC should land on the next Perth day"

        # The per-project chart has to agree with the headline one.
        by_project = client.get("/api/usage-over-time-by-project?hours=0").json()
        assert by_project[0]["bucket"] == perth_bucket, by_project

        # Totals must not move — only the labelling does.
        assert abs(sum(r["cost_usd"] for r in perth_rows)
                   - sum(r["cost_usd"] for r in utc_rows)) < 1e-9

        # A zone that does not exist is rejected, not silently stored.
        bad = client.put("/api/settings", json={"displayTimeZone": "Mars/Olympus"})
        assert bad.status_code == 400, bad.text
        assert client.get("/api/settings").json()["displayTimeZone"] == "Australia/Perth"



def test_budget_cycle_uses_the_display_timezone() -> None:
    """The budget period starts at local midnight, not eight hours late."""
    _reset_db()
    with TestClient(main_module.app) as client:
        client.post("/v1/logs", json=make_payload("tz-2", cost_usd=1.0))

        # `now` is pinned. Reading the wall clock here failed for eight hours
        # at every month boundary: between 16:00 UTC on the last day and
        # midnight UTC, Perth is already in the next month, so the two calls
        # return starts a month apart rather than eight hours.
        mid_month = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
        utc_start = db_module.cycle_start(1, now=mid_month, tz="UTC")
        perth_start = db_module.cycle_start(1, now=mid_month, tz="Australia/Perth")
        # Local midnight on the 1st in UTC+8 is 16:00 on the last day of the
        # previous month in UTC — earlier, so the first eight hours of the
        # month stop being counted against the wrong period.
        assert perth_start < utc_start, (perth_start, utc_start)
        assert (utc_start - perth_start) == timedelta(hours=8), (utc_start, perth_start)

        # And the same during the window that used to break the assertions.
        edge = datetime(2026, 8, 31, 18, 0, tzinfo=timezone.utc)  # 02:00, 1 Sep in Perth
        assert db_module.cycle_start(1, now=edge, tz="Australia/Perth") == datetime(
            2026, 8, 31, 16, 0, tzinfo=timezone.utc
        )

        assert client.put(
            "/api/settings", json={"displayTimeZone": "Australia/Perth"}
        ).status_code == 200
        budget = client.get("/api/budget").json()
        assert budget["cycle_start"] == db_module.cycle_start(
            1, tz="Australia/Perth"
        ).isoformat(), budget

        # A non-string zone is a 400, not an AttributeError escaping as a 500.
        for bad in (5, ["UTC"], {"tz": "UTC"}):
            resp = client.put("/api/settings", json={"displayTimeZone": bad})
            assert resp.status_code == 400, (bad, resp.status_code, resp.text)

