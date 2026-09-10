# Architecture

## The shape of the system

```
 ┌──────────────────────┐
 │ dev container A      │
 │  Claude Code session │──┐
 └──────────────────────┘  │
 ┌──────────────────────┐  │  OTLP/HTTP JSON
 │ dev container B      │──┼──POST /v1/logs────►┌───────────────────────┐
 │  Claude Code session │  │  (every 5s)        │ ingest                │
 └──────────────────────┘  │                    │ FastAPI + uvicorn     │
 ┌──────────────────────┐  │                    │ :8000 (host :9585)    │
 │ dev container …      │──┘                    └───────────┬───────────┘
 └──────────────────────┘                                   │
                                                            ▼
                                                   ┌──────────────────┐
                                                   │ usage.db (SQLite)│
                                                   │ sessions         │
                                                   │ usage_events     │
                                                   └────────┬─────────┘
                                                            │ read queries
 ┌──────────────┐    GET /api/*    ┌────────────────────────┴──┐
 │ browser      │◄────────────────►│ dashboard                 │
 │ (SolidJS SPA)│  nginx adds the  │ nginx serving static build│
 └──────────────┘  bearer token    │ :80 (host :9595)          │
                                   └───────────────────────────┘
```

There is no agent, sidecar, or log tailer on the sending side. Claude Code has
OpenTelemetry export built in; pointing it at this service is the entire
integration.

## Components

### `ingest/` — collector and read API

A FastAPI application ([`ingest/app/main.py`](../ingest/app/main.py)) with three
responsibilities:

1. **Accept telemetry.** `POST /v1/logs` receives OTLP/HTTP JSON
   `ExportLogsServiceRequest` payloads, flattens them into rows, and writes them
   to SQLite.
2. **Serve aggregates.** The `/api/*` routes run grouped SQL queries and return
   JSON shaped for the dashboard's charts and tables. Aggregation happens in
   SQLite, not in the browser.
3. **Keep itself tidy.** Two asyncio tasks run from the lifespan: a 60-second
   sweep that removes sessions which went quiet without ever logging usage (see
   [Empty-session cleanup](data-model.md#empty-session-cleanup)), and an optional
   backup scheduler (see [Backup and restore](backup-and-restore.md)).

Module breakdown:

| Module | Role |
| --- | --- |
| [`app/main.py`](../ingest/app/main.py) | Routes, auth dependency, CORS, lifespan (DB init + backup scheduler) |
| [`app/otlp.py`](../ingest/app/otlp.py) | Pure parser: OTLP JSON → `LogEvent` dataclasses. No I/O, no DB. |
| [`app/db.py`](../ingest/app/db.py) | Connection handling, schema init/migration, all SQL |
| [`app/backup.py`](../ingest/app/backup.py) | Snapshot, copy or rsync, retention, scheduler, status |
| [`schema.sql`](../ingest/schema.sql) | Table and index definitions, applied idempotently on startup |

The OTLP parser is deliberately isolated from the database layer: it turns a
payload into dataclasses and nothing else, which is what makes
[`test_ingest.py`](../ingest/test_ingest.py) able to exercise the full path
against a throwaway SQLite file.

### `dashboard/` — SolidJS SPA behind nginx

A Vite-built SolidJS app served as static files by nginx. Three routes, wired in
[`src/index.tsx`](../dashboard/src/index.tsx):

| Route | Component | Purpose |
| --- | --- | --- |
| `/` | `App.tsx` | Full desktop dashboard |
| `/tablet` | `components/TabletDashboard.tsx` | Large-type, glanceable wall/tablet view |
| `/users` | `components/UsersPage.tsx` | Link dev-container user IDs to projects |
| `/settings` | `components/SettingsPage.tsx` | Client-side preferences plus backup controls |

Data access goes through one thin module, [`src/api.ts`](../dashboard/src/api.ts),
which owns both the `fetch` calls and the TypeScript types describing every
response. If you change a response shape on the server, that file is the one
place the frontend needs to follow.

## Key design decisions

**SQLite, not Postgres.** The write volume is a handful of events per session
per minute, and the whole point is a single small container you can put on a NAS
or a spare box. WAL mode is enabled on every connection so the periodic backup
snapshot doesn't have to block writers.

**The dashboard never holds the token.** nginx proxies `/api/*` to `ingest` and
injects `Authorization: Bearer …` server-side from a templated config
([`nginx.conf.template`](../dashboard/nginx.conf.template)). The browser bundle
contains no credential, so anyone who can reach the dashboard port can read the
data, and that is the intended trust boundary. See [Security](security.md).

**Aggregation in SQL.** `/api/summary`, `/api/usage-by-model`,
`/api/usage-over-time` and `/api/usage-over-time-by-project` all return
pre-aggregated rows. The browser polls every 5 seconds by default; shipping raw
events to the client and grouping there would not survive a few weeks of data.

**Adaptive time buckets.** Windows of 48 hours or less bucket hourly; anything
longer — including the all-time view, which passes `hours=0` and drops the
look-back entirely — buckets daily (`_time_bucket_fmt` in
[`db.py`](../ingest/app/db.py)). One chart component renders both, mirroring the
same 48-hour cutover to decide whether an axis label is a time or a date.

**Timestamps compare as strings, so their format is load-bearing.** Everything
is TEXT, and window bounds are generated in the exact stored format by
`db._cutoff()` rather than compared against SQLite's `datetime()` output — see
[Data model → Timestamps](data-model.md#timestamps) for why that distinction
matters.

**Unknown attributes are never dropped.** The parser normalizes the attribute
keys it recognizes into typed columns and stores the complete merged attribute
map in `usage_events.raw_attributes` as JSON. When Claude Code adds or renames a
telemetry attribute, the data is already captured — only the parser's alias map
and a query need updating, with no backfill lost.

**Projects are a server-side label, not a client one.** Claude Code does not
report a project name, so the label lives on the `sessions` row. It gets there
two ways: `PATCH /api/sessions/{id}` for one session, or a standing
`user.id` → project mapping in `user_projects` that applies to every session a
dev container opens, past and future. The per-session label wins — `upsert_session`
only ever fills a *null* `project_name`, so a hand-set label survives later
events. Everything grouped "by project" joins through that one column, and
untagged sessions collapse into a `(untagged)` bucket rather than disappearing.

**Empty sessions are deleted, not hidden.** Starting Claude Code in a container
emits telemetry even if nobody uses it. Filtering those out at read time would
leave them accumulating in the database forever, so they are removed once they
go inactive — the row and its zero-token events both. Anything with recorded
usage is never touched.

## Request lifecycle: one usage event

1. A Claude Code session makes an API request and records a log record with
   attributes `session.id`, `model`, `input_tokens`, `output_tokens`,
   `cost_usd`, and others.
2. Up to 5 seconds later (`OTEL_LOGS_EXPORT_INTERVAL`) the exporter POSTs a
   batch to `http://<host>:9585/v1/logs`.
3. `require_auth` compares the `Authorization` header against `INGEST_AUTH_TOKEN`
   — or waves the request through if no token is configured.
4. `extract_log_events` walks `resourceLogs → scopeLogs → logRecords`, merges
   resource-level attributes under record-level ones, decodes each OTLP
   `AnyValue`, and yields one `LogEvent` per record.
5. For each event with a session id, `upsert_session` inserts or refreshes
   `last_seen_at`; then `insert_event` appends the row to `usage_events`.
6. The endpoint returns `{}` with HTTP 200 — the OTLP success response.
7. Within one poll interval the dashboard's next `/api/*` refetch reflects it.

## Related

- [Data model](data-model.md) — the tables and the attribute mapping in detail
- [API reference](api-reference.md) — exact request/response shapes
- [Development](development.md) — how to extend any of the above
