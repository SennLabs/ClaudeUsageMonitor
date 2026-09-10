# API reference

Base URL: `http://<host>:9585` (or `http://ingest:8000` from inside the compose
network). All routes are defined in [`ingest/app/main.py`](../ingest/app/main.py).

## Authentication

Every route except `GET /healthz` is wrapped in the `require_auth` dependency:

- If `INGEST_AUTH_TOKEN` is **unset or empty**, the service refuses to start
  unless `INGEST_ALLOW_ANONYMOUS=true` is also set — in which case all requests
  are allowed and a warning is logged at startup.
- If it is set, the request must carry `Authorization: Bearer <token>` exactly.
  Anything else returns `401 {"detail": "Unauthorized"}`.

There is no per-client token, no scopes, and no rotation mechanism — one shared
secret. See [Security](security.md).

FastAPI's interactive docs (`/docs`, `/redoc`, `/openapi.json`) are disabled —
they are unauthenticated by default and this port is the most widely reachable
part of the stack.

The `curl` examples throughout these docs assume `$INGEST_AUTH_TOKEN` is set in
your shell. `.env` is read by Compose, not sourced by it, so load it first:

```bash
set -a; . ./.env; set +a
curl -H "Authorization: Bearer $INGEST_AUTH_TOKEN" http://localhost:9585/api/summary
```

## CORS

**There is none, deliberately.** Both consumers are same-origin: nginx proxies
`/api/*` to this service in production, and the Vite dev server proxies `/api`
to it in development. No browser talks to this port cross-origin, so no
`Access-Control-Allow-Origin` header is sent and preflights are not honoured.

Do not add a CORS middleware back without reading the comment in
[`main.py`](../ingest/app/main.py). It previously ran `allow_origins=["*"]`,
which combined badly with nginx injecting the bearer token server-side — see
[Security](security.md).

---

## `POST /v1/logs`

Accepts an OTLP/HTTP JSON `ExportLogsServiceRequest`. This is the endpoint
Claude Code's exporter posts to; the path is fixed by the OTLP spec (the client
appends `/v1/logs` to `OTEL_EXPORTER_OTLP_ENDPOINT`).

**Request** — standard OTLP JSON encoding:

```json
{
  "resourceLogs": [{
    "resource": {"attributes": [
      {"key": "user.id", "value": {"stringValue": "user-123"}},
      {"key": "organization.id", "value": {"stringValue": "org-456"}}
    ]},
    "scopeLogs": [{
      "logRecords": [{
        "timeUnixNano": "1757462400000000000",
        "body": {"stringValue": "claude_code.api_request"},
        "attributes": [
          {"key": "session.id",        "value": {"stringValue": "session-abc"}},
          {"key": "model",             "value": {"stringValue": "claude-opus-5"}},
          {"key": "input_tokens",      "value": {"intValue": "1200"}},
          {"key": "output_tokens",     "value": {"intValue": "340"}},
          {"key": "cache_read_tokens", "value": {"intValue": "800"}},
          {"key": "cost_usd",          "value": {"doubleValue": 0.0231}}
        ]
      }]
    }]
  }]
}
```

**Response** — `200` with `{}`, the OTLP empty-success body.

Notes:

- Resource-level attributes are merged into every record beneath them; record
  attributes win on conflict.
- A record with no recognizable `session.id` still gets an event row; only the
  `sessions` upsert is skipped.
- Records with a missing or zero `timeUnixNano` are timestamped with the
  server's current UTC time.
- **The whole batch is one transaction.** It either lands or it doesn't; a
  failure partway through no longer leaves earlier records committed for the
  retry to duplicate.
- **A single unparseable record is dropped, not fatal.** It is logged and
  counted; every other record in the batch still lands.
- The write runs off the event loop, so a large batch no longer blocks
  `/healthz` and the other routes.
- **Retried batches are de-duplicated.** Each record is stored with an
  `event_hash` over its identity and full attribute map, behind a unique index,
  so an exporter resending an identical batch after a 5xx adds nothing. This
  needs the index to exist — see
  [Data model](data-model.md#event-de-duplication) if the startup log says
  de-duplication is inactive.

---

## `GET /healthz`

Unauthenticated. Used by the container `HEALTHCHECK`.

```json
{"status": "ok"}
```

It **does** touch the database — a `SELECT 1` — and returns `503` when that
fails. A static 200 previously reported healthy while the file was locked,
corrupt, or on a full disk, which are exactly the conditions a health check
exists to catch.

---

## `GET /api/summary`

All-time totals plus a live active count.

```json
{
  "total_sessions": 12,
  "total_input_tokens": 918233,
  "total_output_tokens": 41022,
  "total_cache_read_tokens": 2288401,
  "total_cache_creation_tokens": 118002,
  "total_cost_usd": 14.83,
  "active_sessions": 2,
  "unattributed_events": 0,
  "unattributed_cost_usd": 0.0
}
```

- Totals cover the entire `usage_events` table — there is no window parameter.
- `total_sessions` counts distinct session ids **that have events**, so it can
  be lower than the row count of the `sessions` table.
- `active_sessions` counts sessions whose `last_seen_at` is within the last
  `ACTIVE_WINDOW_MINUTES` (default 15) — see [Configuration](configuration.md).
- `unattributed_*` covers events that arrived with no `session.id`. They are in
  the totals here but reach no per-session or per-user view, so the two would
  otherwise disagree with no explanation.

---

## `GET /api/sessions`

Sessions, newest first, with rolled-up usage.

| Query param | Type | Default | Notes |
| --- | --- | --- | --- |
| `limit` | int | `100` | Clamped to 1–500 |
| `offset` | int | `0` | For paging |

> **Response shape changed on 2026-09-10.** This returns an envelope, not a
> bare array. `total` is what lets a caller tell the list is truncated — the
> per-project totals computed from it used to disagree silently with the
> all-time summary past 100 sessions.

```json
{
  "total": 137,
  "limit": 100,
  "offset": 0,
  "sessions": [{
    "session_id": "session-abc",
    "user_id": "user-123",
    "organization_id": "org-456",
    "project_name": "billing-api",
    "project_source": "resource",
    "first_seen_at": "2026-09-10T01:12:04+00:00",
    "last_seen_at": "2026-09-10T02:48:31+00:00",
    "event_count": 87,
    "input_tokens": 210433,
    "output_tokens": 9821,
    "cost_usd": 3.42,
    "models": "claude-opus-5,claude-haiku-4-5-20251001"
  }]
}
```

- Ordered by `last_seen_at` descending.
- `models` is a comma-joined `GROUP_CONCAT(DISTINCT …)` — a string, not an array.
- `project_name` is `null` until something tags the session; `project_source`
  says what did (`resource`, `user_map` or `manual`) — see
  [Client setup](client-setup.md#precedence).
- A session with no events yet still appears, with zeroed totals.

---

## `GET /api/usage-by-model`

```json
[
  {"model": "claude-opus-5", "requests": 412, "input_tokens": 880122, "output_tokens": 38900, "cost_usd": 13.10},
  {"model": "unknown",       "requests": 3,   "input_tokens": 0,      "output_tokens": 0,     "cost_usd": 0.0}
]
```

Grouped by model over all time, ordered by cost descending. Events with no model
attribute collapse into the literal string `"unknown"`.

---

## `GET /api/usage-over-time`

| Query param | Type | Default | Notes |
| --- | --- | --- | --- |
| `hours` | int | `24` | Look-back window. `0` (or any value ≤ 0) means **all time**; anything else is capped server-side at `720` (30 days). |

```json
[
  {"bucket": "2026-09-10T01:00:00Z", "cost_usd": 0.84, "input_tokens": 51200, "output_tokens": 2100, "total_tokens": 53300},
  {"bucket": "2026-09-10T02:00:00Z", "cost_usd": 1.19, "input_tokens": 70110, "output_tokens": 3402, "total_tokens": 73512}
]
```

- Bucket size is **hourly** when `0 < hours <= 48`, and **daily** for longer
  windows and for all-time.
- Empty buckets are absent — the series is sparse, not zero-filled. Charts must
  handle gaps.
- `total_tokens` is `input + output`; cache tokens are not included.

---

## `GET /api/usage-over-time-by-project`

Same window and bucketing rules as above (`hours=0` included), split by
project label.

```json
[
  {"bucket": "2026-09-10T01:00:00Z", "project_name": "billing-api", "cost_usd": 0.61, "total_tokens": 40120},
  {"bucket": "2026-09-10T01:00:00Z", "project_name": "(untagged)",  "cost_usd": 0.23, "total_tokens": 13180}
]
```

Sessions with no `project_name` are grouped under the literal `"(untagged)"`.
Rows are ordered by bucket, then project name.

---

## `PATCH /api/sessions/{session_id}`

Sets or clears a session's project label.

**Request**

```json
{"project_name": "billing-api"}
```

`null`, an omitted field, or an empty string all clear the label.

**Response**

```json
{"ok": true}
```

Returns `200` even when `session_id` matches nothing — the `UPDATE` simply
affects zero rows.

```bash
curl -X PATCH http://localhost:9585/api/sessions/session-abc \
  -H "Authorization: Bearer $INGEST_AUTH_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"project_name": "billing-api"}'
```

---

## `GET /api/users`

One row per `user.id` — the stable identity a dev container reports — with its
standing project mapping and rolled-up usage. Users that have a mapping but have
not reported a session yet are included, with nulls for the timestamps.

```json
[
  {
    "user_id": "devcontainer-alpha",
    "project_name": "radiology-pacs",
    "organization_id": "org-456",
    "session_count": 41,
    "active_sessions": 2,
    "first_seen_at": "2026-08-01T22:31:04+00:00",
    "last_seen_at": "2026-09-10T03:12:55+00:00",
    "input_tokens": 918233,
    "output_tokens": 41022,
    "cost_usd": 2.00
  }
]
```

- `project_name` is the **mapping**, not a per-session label. A session can carry
  a different label if someone set one by hand.
- `active_sessions` counts that user's sessions seen within the active window.
- Ordered by `last_seen_at` descending; never-seen mapped users sort last.

---

## `GET /api/projects`

Every project label currently in use, from sessions or mappings, sorted. Used to
populate the autocomplete on the Users page.

```json
["billing", "radiology-pacs"]
```

---

## `PUT /api/user-projects/{user_id}`

Link a user id to a project.

**Request**

```json
{"project_name": "radiology-pacs"}
```

**Response**

```json
{"ok": true, "project_name": "radiology-pacs", "sessions_updated": 41}
```

What it does:

- Stores the mapping in `user_projects`.
- **Relabels every existing session** owned by that user — `sessions_updated`
  reports how many.
- Every future session from that user is created already labelled.
- A label set by hand on an individual session is never overwritten by later
  events (`upsert_session` only fills a *null* `project_name`) — though a fresh
  `PUT` here does relabel it, since that is an explicit instruction.

An empty or whitespace-only name removes the mapping **and** clears the label
from that user's sessions:

```json
{"project_name": ""}
```

```bash
curl -X PUT http://localhost:9585/api/user-projects/devcontainer-alpha \
  -H "Authorization: Bearer $INGEST_AUTH_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"project_name": "radiology-pacs"}'
```

---

## `DELETE /api/user-projects/{user_id}`

Removes the mapping. Existing session labels are left in place unless you pass
`?clear_sessions=true`. Future sessions from that user arrive untagged.

```json
{"ok": true}
```

---

## Insight endpoints

All read attributes Claude Code has always sent and this service has always
stored — promoting them out of `raw_attributes` is what made them queryable.
Each takes the same `hours` parameter as the time-series endpoints (`0` = all
time).

### `GET /api/attribution`

Cost split by the dimensions other than model and project. Each key holds up to
20 rows of `{name, requests, cost_usd, total_tokens}`, ordered by cost.

```json
{
  "by_query_source": [{"name": "subagent", "requests": 412, "cost_usd": 9.40, "total_tokens": 880122}],
  "by_agent": [...], "by_skill": [...], "by_mcp_server": [...],
  "by_effort": [...], "by_speed": [...]
}
```

`(none)` means the attribute was absent on those events. Note Claude Code's own
redaction: user-defined agents report as `custom` and third-party skills and
plugins as `third-party` unless the client sets `OTEL_LOG_TOOL_DETAILS=1`.

### `GET /api/rate`

Spend over a true trailing window, for the cost alert.

| Query param | Type | Default | Notes |
| --- | --- | --- | --- |
| `minutes` | int | `60` | Clamped to 1–1440 |

```json
{"window_minutes": 60, "cost_usd": 1.42, "cost_usd_per_hour": 1.42, "events": 37}
```

Computed from raw event timestamps rather than chart buckets. The client-side
version was a sawtooth — it only ever caught the current clock-hour bucket, so
the alert dropped to near zero on the hour and climbed back mid-hour.

### `GET /api/latency`

```json
{"requests": 412, "avg_ms": 3100, "p50_ms": 2400, "p95_ms": 9800, "max_ms": 41000}
```

SQLite has no percentile function, so p50 and p95 are read positionally out of
the ordered set. All fields are `null` when no request in the window carried a
`duration_ms`.

### `GET /api/errors`

```json
{
  "requests": 412, "errors": 7, "refusals": 1, "retried": 4, "error_rate": 0.0167,
  "by_status_code": [{"status_code": 429, "n": 5}],
  "by_refusal_category": [{"category": "cyber", "n": 1}]
}
```

`retried` counts events whose `attempt` attribute is above 1 — also the signal
for whether retried batches are being de-duplicated.

### `GET /api/tools`

Up to 30 rows from `claude_code.tool_result`, ordered by call count:

```json
[{"tool_name": "Bash", "calls": 240, "failures": 11, "avg_ms": 820, "max_ms": 30000}]
```

### `GET /api/maintenance`

State of the background sweep, so a persistently failing one is visible rather
than only logged.

```json
{
  "last_run_at": "2026-09-10T07:41:02+00:00",
  "last_ok": true, "last_error": null,
  "sessions_purged": 3, "attributes_cleared": 0, "events_deleted": 0,
  "interval_seconds": 60, "active_window_minutes": 15,
  "retention_days": null, "raw_attributes_retention_days": null
}
```

Counters are cumulative for this process and reset on restart.

### `GET /api/fleet`

Which Claude Code versions and terminals are reporting. All time, no `hours`
parameter.

```json
[{"app_version": "2.1.263", "terminal_type": "vscode", "sessions": 14, "last_seen_at": "..."}]
```

---

## `GET /api/settings`

Dashboard preferences, stored server-side so every viewer shares them.

```json
{
  "monthlyBudget": 300.0,
  "billingCycleDay": 15,
  "refreshIntervalMs": 5000,
  "defaultTimeWindow": "24h",
  "defaultMetric": "tokens",
  "costAlertThresholdPerHour": null,
  "activeSessionWindowMin": 15
}
```

`activeSessionWindowMin` is read-only — it comes from `ACTIVE_WINDOW_MINUTES`
and is included so the UI has one source of truth for it. See
[Configuration](configuration.md#dashboard-preferences).

---

## `PUT /api/settings`

Merges a partial update and returns the full settings as stored.

```bash
curl -X PUT http://localhost:9585/api/settings \
  -H "Authorization: Bearer $INGEST_AUTH_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"monthlyBudget": 300, "billingCycleDay": 15}'
```

- Unknown and read-only keys are **ignored, not rejected**, so a newer
  dashboard talking to an older server degrades rather than failing.
- Invalid values return `400` with a message: `billingCycleDay` outside 1–28,
  `refreshIntervalMs` below 1000, a negative budget, an unrecognised window or
  metric.

---

## `GET /api/budget`

Spend since the start of the current billing period.

| Query param | Type | Default | Notes |
| --- | --- | --- | --- |
| `cycle_day` | int | the stored `billingCycleDay` | Day of month the period starts, 1–28 |

```json
{"cycle_start": "2026-08-15T00:00:00+00:00", "cost_usd": 42.18, "total_tokens": 1904322}
```

The period start is computed in UTC and walks back a month when today is before
the cycle day.

---

## `GET /api/backup/status`

```json
{
  "enabled": true,
  "destination": "/backups",
  "method": "copy",
  "interval_hours": 24.0,
  "keep": 7,
  "last_backup_at": "2026-09-10T02:00:11+00:00",
  "last_backup_ok": true,
  "last_backup_error": null,
  "next_backup_at": "2026-09-11T02:00:11+00:00"
}
```

- `method` is `"rsync-ssh"` or `"copy"`, from `BACKUP_MODE` or inferred from
  the destination shape.
- `keep` is `null` in rsync mode, where retention is not applied.
- `config_error` is non-null when a setting is invalid; backups are disabled
  but ingestion is unaffected.
- `warnings` lists conditions worth knowing that are not errors.
- `last_*` fields are in-memory only and reset to `null` on restart. They
  describe this process's history, not the destination's contents.
- With backups disabled, `enabled` is `false` and `destination` is `null`.

---

## `POST /api/backup/trigger`

Runs one backup immediately, synchronously (offloaded to a thread so the event
loop keeps serving), and returns the same body as `/api/backup/status`.

- `400` when no destination is configured, or the configuration is invalid.
- `409` when a backup is already running.
- `500` when the run happened and failed — the body's `detail` is the error.
- Triggering manually does not shift the scheduled `next_backup_at`.

---

## Error responses

| Status | When |
| --- | --- |
| `400` | Body is not valid JSON, or is not a usable OTLP envelope; manual backup trigger with no destination configured |
| `413` | `POST /v1/logs` body larger than `MAX_LOG_BODY_BYTES` (default 32 MB) |
| `401` | Missing or wrong bearer token, when a token is configured |
| `422` | Request body fails validation (e.g. a non-string `project_name`) |
| `500` | Unhandled server error — check `docker compose logs ingest` |

## Client type definitions

The dashboard's TypeScript interfaces in
[`dashboard/src/api.ts`](../dashboard/src/api.ts) mirror every response shape
above and are the practical source of truth for the frontend. Keep them in step
when changing a response.
