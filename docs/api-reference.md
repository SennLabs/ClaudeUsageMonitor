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
- The whole body is parsed before any insert, so a record that fails to *parse*
  discards the entire batch. Records are then written one transaction each, so a
  failure partway through leaves earlier ones committed — see
  [known issue 6](known-issues.md#6-batches-are-not-atomic-so-failures-permanently-inflate-totals).
- There is no de-duplication. If a client retries a batch, its events are
  counted twice.

---

## `GET /healthz`

Unauthenticated. Used by the container `HEALTHCHECK`.

```json
{"status": "ok"}
```

It confirms the process is serving, not that the database is writable.

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
  "active_sessions": 2
}
```

- Totals cover the entire `usage_events` table — there is no window parameter.
- `total_sessions` counts distinct session ids **that have events**, so it can
  be lower than the row count of the `sessions` table.
- `active_sessions` counts sessions whose `last_seen_at` is within the last
  `ACTIVE_WINDOW_MINUTES` (default 15) — see [Configuration](configuration.md).

---

## `GET /api/sessions`

The 100 most recently active sessions, each with its rolled-up usage.

```json
[
  {
    "session_id": "session-abc",
    "user_id": "user-123",
    "organization_id": "org-456",
    "project_name": "billing-api",
    "first_seen_at": "2026-09-10T01:12:04+00:00",
    "last_seen_at": "2026-09-10T02:48:31+00:00",
    "event_count": 87,
    "input_tokens": 210433,
    "output_tokens": 9821,
    "cost_usd": 3.42,
    "models": "claude-opus-5,claude-haiku-4-5-20251001"
  }
]
```

- Ordered by `last_seen_at` descending, hard-limited to 100.
- `models` is a comma-joined `GROUP_CONCAT(DISTINCT …)` — a string, not an array.
- `project_name` is `null` until someone tags the session.
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

- `method` is `"rsync-ssh"` when the destination looks remote (contains both
  `@` and `:`), otherwise `"copy"`.
- `last_*` fields are in-memory only and reset to `null` on restart. They
  describe this process's history, not the destination's contents.
- With backups disabled, `enabled` is `false` and `destination` is `null`.

---

## `POST /api/backup/trigger`

Runs one backup immediately, synchronously (offloaded to a thread so the event
loop keeps serving), and returns the same body as `/api/backup/status`.

- `400` `{"detail": "BACKUP_DESTINATION is not configured"}` when backups are off.
- A backup that *runs* but fails still returns `200`; the failure shows in
  `last_backup_ok: false` and `last_backup_error`.
- Triggering manually does not shift the scheduled `next_backup_at`.

---

## Error responses

| Status | When |
| --- | --- |
| `400` | Manual backup trigger with no destination configured |
| `401` | Missing or wrong bearer token, when a token is configured |
| `422` | Request body fails validation (e.g. a non-string `project_name`) |
| `500` | Unhandled server error — check `docker compose logs ingest` |

## Client type definitions

The dashboard's TypeScript interfaces in
[`dashboard/src/api.ts`](../dashboard/src/api.ts) mirror every response shape
above and are the practical source of truth for the frontend. Keep them in step
when changing a response.
