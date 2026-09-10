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

# Most tests exercise the unauthenticated path, which the app now refuses to
# start in unless the operator opts in explicitly.
os.environ["INGEST_ALLOW_ANONYMOUS"] = "1"

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

        remaining = {s["session_id"] for s in sessions_of(client)}
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

    print("OK (cors) - no Access-Control-Allow-Origin, preflight not honoured")


def test_docs_endpoints_disabled() -> None:
    """Schema endpoints are unauthenticated by default; they must not be served."""
    _reset_db()
    with TestClient(main_module.app) as client:
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert client.get(path).status_code == 404, path
    print("OK (docs) - /docs, /redoc and /openapi.json return 404")


def test_refuses_to_start_without_a_token() -> None:
    """An unset token must fail loudly rather than silently disabling auth."""
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

    print("OK (startup guard) - refuses to start with no token and no explicit opt-in")



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

    print("OK (dedupe) - replayed batch stored once, distinct events still stored")


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

    print("OK (partial batch) - good records committed, bad one dropped, no 500")


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

    print("OK (envelope) - malformed bodies return 400, null members tolerated")


def test_cost_micros_recorded() -> None:
    """Integer millionths are stored so SUM() does not accumulate float error."""
    _reset_db()
    with TestClient(main_module.app) as client:
        client.post("/v1/logs", json=make_payload("micros-1", cost_usd=0.0231))
        conn = sqlite3.connect(db_module.DB_PATH)
        micros = conn.execute("SELECT cost_usd_micros FROM usage_events").fetchone()[0]
        conn.close()
        assert micros == 23100, micros
    print("OK (cost micros) - cost_usd_micros derived exactly from the reported figure")


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
    print("OK (body cap) - oversized Content-Length rejected with 413")



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

    print("OK (resource project) - container-declared project applied and kept current")


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

    print("OK (precedence) - manual > resource > user_map, source recorded on each")



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

    print("OK (settings) - server-side, partial updates merge, invalid values rejected")


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

    print("OK (budget) - cycle spend excludes prior periods; all-time total unchanged")



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

    print("OK (insights) - promoted attributes drive attribution, latency and fleet views")


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

    print("OK (errors/tools) - error rate, retries, refusal categories and tool failures")


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

    print("OK (unattributed) - the gap between headline and per-session totals is reported")



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

    print("OK (backup config) - bad configs disabled or failed loudly, retention applied")



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

    print("OK (retention) - attributes cleared without changing totals, deletion opt-in")


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

    print("OK (pagination) - total reported, offset returns a distinct page")


def test_healthz_checks_the_database() -> None:
    """A static 200 reported healthy while the database was unusable."""
    _reset_db()
    with TestClient(main_module.app) as client:
        assert client.get("/healthz").status_code == 200

        original = db_module.DB_PATH
        db_module.DB_PATH = Path(_TMP_DIR) / "no-such-dir" / "missing.db"
        try:
            assert client.get("/healthz").status_code == 503
        finally:
            db_module.DB_PATH = original
        assert client.get("/healthz").status_code == 200

    print("OK (healthz) - reports 503 when the database is unreachable")


def main() -> None:
    print(f"Scratch database: {db_module.DB_PATH}\n")
    test_ingest_without_auth()
    test_auth_gating()
    test_user_project_mapping()
    test_purge_empty_sessions()
    test_time_windows()
    test_duplicate_batches_are_ignored()
    test_malformed_records_do_not_lose_the_batch()
    test_malformed_envelope_is_a_400()
    test_cost_micros_recorded()
    test_oversized_body_rejected()
    test_project_from_resource_attribute()
    test_project_precedence()
    test_settings_round_trip()
    test_budget_cycle()
    test_promoted_attributes_are_queryable()
    test_errors_and_tools_views()
    test_unattributed_events_are_reported()
    test_retention_and_maintenance()
    test_sessions_pagination()
    test_healthz_checks_the_database()
    test_backup_configuration_guards()
    test_no_cors_headers()
    test_docs_endpoints_disabled()
    test_refuses_to_start_without_a_token()  # reloads main_module; keep last


if __name__ == "__main__":
    main()
