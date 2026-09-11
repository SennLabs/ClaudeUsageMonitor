# Data model

Storage is a single SQLite database — `/data/usage.db` in the container,
`ingest/usage.db` when running locally, overridable via `DB_PATH`. The schema
lives in [`ingest/schema.sql`](../ingest/schema.sql) and is applied with
`executescript` on every startup; all statements are `IF NOT EXISTS`, so startup
is idempotent.

WAL journal mode is set on every connection (see `_connect` in
[`db.py`](../ingest/app/db.py)), which lets the backup snapshot read a
consistent copy without blocking writers.

## Tables

### `sessions`

One row per Claude Code session.

| Column | Type | Notes |
| --- | --- | --- |
| `session_id` | TEXT PK | The `session.id` attribute from telemetry |
| `user_id` | TEXT | Set on first sight, never overwritten with a later `NULL` |
| `organization_id` | TEXT | Same treatment |
| `first_seen_at` | TEXT | ISO 8601, from the first event's timestamp |
| `last_seen_at` | TEXT | ISO 8601, refreshed on every event |
| `end_reason` | TEXT | Declared but not currently written by any code path |
| `project_name` | TEXT | The project this session belongs to |
| `project_source` | TEXT | How it was set: `resource`, `user_map` or `manual` — see [Client setup](client-setup.md#precedence) |

Rows are written by `upsert_session`, an `INSERT … ON CONFLICT DO UPDATE` that
refreshes `last_seen_at` and fills in `user_id` / `organization_id` only if they
are still null (`COALESCE(sessions.user_id, excluded.user_id)`). A later event
missing those attributes therefore cannot erase them.

### `user_projects`

The standing `user.id` → project mapping. Dev containers report a stable user
id, so one row here labels every session that container will ever open.

| Column | Type | Notes |
| --- | --- | --- |
| `user_id` | TEXT PK | Matches `sessions.user_id` |
| `project_name` | TEXT NOT NULL | The label to apply |
| `updated_at` | TEXT | ISO 8601, when the mapping was last set |

Written by `set_user_project`, which also relabels that user's existing
sessions in the same transaction. Read in two places: `upsert_session` (so a new
session is born labelled) and `fetch_users`.

### `app_settings`

A single row (`id = 1`) holding the dashboard preferences as a JSON blob in
`data`. Instance-wide rather than per-viewer: these used to live in each
browser's `localStorage`, so the wall tablet and a laptop disagreed and nothing
server-side could act on them.

Defaults live in `db.DEFAULT_SETTINGS` and unknown keys are ignored on write, so
adding a setting needs no migration. `activeSessionWindowMin` is served
alongside them but is **not** stored here — it comes from the environment.

### `usage_events`

One row per received log record. Append-only — nothing updates or deletes here.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | INTEGER PK AUTOINCREMENT | |
| `session_id` | TEXT → `sessions.session_id` | Nullable; a record with no session id still gets a row |
| `occurred_at` | TEXT NOT NULL | ISO 8601 UTC, derived from `timeUnixNano` |
| `event_name` | TEXT | The log record's `body`, e.g. `claude_code.api_request` |
| `model` | TEXT | |
| `input_tokens` | INTEGER | |
| `output_tokens` | INTEGER | |
| `cache_read_tokens` | INTEGER | |
| `cache_creation_tokens` | INTEGER | |
| `cost_usd` | REAL | As reported by the client |
| `cost_usd_micros` | INTEGER | The same figure in exact millionths — aggregate on this, not on the float |
| `raw_attributes` | TEXT | JSON of the complete merged attribute map, key-sorted |
| `event_hash` | TEXT | SHA-256 identity digest, unique — see below |
| `duration_ms` | INTEGER | API request duration |
| `query_source` | TEXT | `main` / `subagent` / `auxiliary` |
| `effort` | TEXT | `low` … `max` |
| `speed` | TEXT | `fast` / `normal` |
| `agent_name` | TEXT | From `agent.name`; user-defined agents report as `custom` |
| `skill_name` | TEXT | From `skill.name`; third-party skills report as `third-party` |
| `mcp_server_name` | TEXT | From `mcp_server.name` |
| `prompt_id` | TEXT | From `prompt.id`; links every event of one user prompt |
| `app_version` | TEXT | Claude Code version |
| `terminal_type` | TEXT | `vscode`, `iTerm.app`, `tmux`, … |
| `tool_name` | TEXT | On `claude_code.tool_result` events |
| `status_code` | INTEGER | On `claude_code.api_error` events |

### `metric_points`

One row per data point received on `POST /v1/metrics`. Kept separate from
`usage_events` rather than folded into it: these are periodic aggregates over a
time window, not records of a single API call, and they arrive on a different
interval (60s against 5s). Append-only.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | INTEGER PK AUTOINCREMENT | |
| `metric_name` | TEXT NOT NULL | e.g. `claude_code.commit.count` |
| `occurred_at` | TEXT NOT NULL | End of the point's window, from `timeUnixNano` |
| `started_at` | TEXT | Start of the point's window, from `startTimeUnixNano` |
| `value` | REAL NOT NULL | `asInt` / `asDouble`, or a histogram's `sum` |
| `temporality` | TEXT | `delta` / `cumulative` / `unspecified` — see below |
| `is_monotonic` | INTEGER | From the sum's `isMonotonic` |
| `session_id` | TEXT | Not a foreign key: a metrics-only session is still upserted into `sessions`, but points are kept even if the session row is not |
| `user_id`, `organization_id`, `project_name` | TEXT | From resource attributes |
| `app_version`, `terminal_type`, `model` | TEXT | |
| `type` | TEXT | `added`/`removed` (lines), `user`/`cli` (active time), `input`/`output`/… (tokens) |
| `tool_name`, `decision`, `source`, `language` | TEXT | On `code_edit_tool.decision` |
| `start_type` | TEXT | `fresh` / `resume` / `continue`, on `session.count` |
| `raw_attributes` | TEXT | JSON of the complete merged attribute map, key-sorted |
| `point_hash` | TEXT | SHA-256 identity digest, unique — same mechanism as `event_hash` |

**Temporality is the thing to get right.** A delta point is an increment and
sums correctly; a cumulative point is a running total, and summing the series
counts the same work once per export interval. Claude Code exports delta, and
every read query filters to `temporality = 'delta'`. Cumulative points are
still stored and `/api/metrics` returns `cumulative_points_ignored` so a
misconfigured client shows up rather than quietly halving every figure. Pin it
on the client with
`OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=delta`.

**Metric names are not a closed set.** Anything that arrives is stored and
appears in `/api/metrics`'s `by_metric` list. Naming has shifted across Claude
Code versions before — which is why `_ATTR_ALIASES` carries aliases at all — so
a rename should degrade to "unrecognised", not vanish. The names currently
recognised by name:

| Metric | Key attributes | Feeds |
| --- | --- | --- |
| `claude_code.lines_of_code.count` | `type` added/removed | `$ / 1k lines` |
| `claude_code.commit.count` | — | `cost / commit` |
| `claude_code.pull_request.count` | — | `cost / PR` |
| `claude_code.active_time.total` | `type` user/cli | `cost / active hour` |
| `claude_code.code_edit_tool.decision` | `decision`, `language`, `tool_name`, `source` | Acceptance rate by language |
| `claude_code.session.count` | `start_type` | Session funnel |
| `claude_code.cost.usage` | — | Cross-check against the event stream |
| `claude_code.token.usage` | `type` | Cross-check against the event stream |

### Event de-duplication

An OpenTelemetry exporter retries a 5xx by resending the identical batch. With
nothing to recognise the resend, a single transient failure permanently
inflated cost and token totals.

The same applies to `metric_points`, where a re-counted delta is a
permanently wrong total; `point_hash` covers the metric name, its window, its
value and its attribute map, and is created and repaired by the same code path.

Every record now carries an `event_hash`: a SHA-256 over its session id,
timestamp, event name and complete key-sorted attribute map. A `UNIQUE` index
on that column plus `INSERT OR IGNORE` makes a replayed batch a no-op. Two
genuinely distinct records would have to be byte-identical *and* share a
nanosecond timestamp to collide.

The unique index is created from `init_db()` rather than `schema.sql`, on
purpose. An existing database may already contain duplicates, and `executescript`
failing over that would stop the service from starting at all. Instead:

- Startup backfills `event_hash` and `cost_usd_micros` for existing rows.
- It then tries to create the index. If duplicates block it, it logs a warning
  naming the count and keeps serving — **without** de-duplication.
- `ingest/dedupe.py` clears them out deliberately. It reports first and deletes
  nothing without `--apply`, keeping the earliest row of each set:

  ```bash
  cd ingest && python dedupe.py            # report only
  cd ingest && python dedupe.py --apply    # delete, after taking a backup
  ```

- The index is created on the next startup.

### Indexes

```sql
CREATE INDEX        idx_usage_events_session ON usage_events(session_id);
CREATE INDEX        idx_usage_events_time    ON usage_events(occurred_at);
CREATE INDEX        idx_sessions_user        ON sessions(user_id);
CREATE UNIQUE INDEX idx_usage_events_hash    ON usage_events(event_hash);  -- from init_db()
CREATE INDEX        idx_usage_events_name    ON usage_events(event_name);
CREATE INDEX        idx_metric_points_name_time ON metric_points(metric_name, occurred_at);
CREATE INDEX        idx_metric_points_session   ON metric_points(session_id);
CREATE UNIQUE INDEX idx_metric_points_hash      ON metric_points(point_hash);  -- from init_db()
```

The time index is what keeps the windowed chart queries cheap as the table
grows; the user index serves the Users page and mapping updates. Every
`/api/metrics` query filters on metric name and window together, which is why
that index is composite.

### Migrations

There is no migration framework. `init_db()` runs the schema script, then
attempts additive `ALTER TABLE` statements inside a `try/except
sqlite3.OperationalError`, so re-running against an already-migrated database is
a no-op:

```python
try:
    conn.execute("ALTER TABLE sessions ADD COLUMN project_name TEXT")
except sqlite3.OperationalError:
    pass  # column already exists
```

Adding a nullable column to an existing deployment follows the same pattern:
add it to `schema.sql` for fresh databases, *and* add a guarded `ALTER TABLE` in
`init_db()` for existing ones. Anything destructive — dropping or retyping a
column — needs a hand-written one-off script and a backup taken first.

## OTLP attribute mapping

[`otlp.py`](../ingest/app/otlp.py) walks
`resourceLogs[] → scopeLogs[] → logRecords[]`, merges resource attributes under
record attributes, decodes each OTLP `AnyValue`, and maps recognized keys to
columns via `_ATTR_ALIASES`:

| Incoming attribute key | Column |
| --- | --- |
| `session.id`, `session_id` | `session_id` |
| `user.id` | `user_id` |
| `organization.id` | `organization_id` |
| `model` | `model` |
| `input_tokens` | `input_tokens` |
| `output_tokens` | `output_tokens` |
| `cache_read_tokens`, `cache_read_input_tokens` | `cache_read_tokens` |
| `cache_creation_tokens`, `cache_creation_input_tokens` | `cache_creation_tokens` |
| `cost_usd` | `cost_usd` |
| `cost_usd_micros` | `cost_usd_micros` |
| `duration_ms` | `duration_ms` |
| `query_source` | `query_source` |
| `effort` | `effort` |
| `speed` | `speed` |
| `agent.name` | `agent_name` |
| `skill.name` | `skill_name` |
| `mcp_server.name` | `mcp_server_name` |
| `prompt.id` | `prompt_id` |
| `app.version` | `app_version` |
| `terminal.type` | `terminal_type` |
| `tool_name` | `tool_name` |
| `status_code` | `status_code` |
| `project`, `project.name`, `project_name` | `project_name` (from `OTEL_RESOURCE_ATTRIBUTES`) |

The duplicate entries are version aliases — exporter attribute naming has
shifted over Claude Code releases, and accepting both spellings means an
upgrade on the sending side doesn't silently produce null token columns.

Anything **not** in this table is still preserved, in `raw_attributes`. To
surface a new attribute as a first-class column later:

1. Add the column to `schema.sql` and a guarded `ALTER TABLE` in `init_db()`.
2. Add the alias to `_ATTR_ALIASES` and the field to the `LogEvent` dataclass.
3. Add it to the `INSERT` in `insert_event`.
4. Backfill historical rows from the JSON already stored:
   ```sql
   UPDATE usage_events
      SET new_col = json_extract(raw_attributes, '$."the.attribute.key"')
    WHERE new_col IS NULL;
   ```

### Value decoding

OTLP JSON wraps every value in a type tag. `_decode_value` handles
`stringValue`, `intValue`, `doubleValue`, `boolValue`, `arrayValue`, and
`kvlistValue` (recursively). `intValue` arrives as a *string* — the OTLP JSON
encoding does that to preserve 64-bit precision — so it is parsed with `int()`.
Fields in `_INT_FIELDS` get a second defensive `int()` cast; a value that can't
be coerced is dropped from that column rather than failing the whole record.

### Timestamps

`_nanos_to_iso` converts `timeUnixNano` to a timezone-aware UTC ISO 8601 string
(`2026-09-10T01:12:04.123456+00:00`). Missing or falsy values fall back to
`datetime.now(timezone.utc)`.

Everything is stored as TEXT, so window comparisons are **lexicographic**. That
makes the exact string format load-bearing: `db._cutoff()` generates the
comparison bound in the same format the timestamps are stored in, rather than
comparing against SQLite's `datetime()` output. `datetime()` uses a space
separator, and `'T' > ' '`, so a bound built that way makes every row from the
same calendar day compare as newer than the cutoff — a 24-hour window silently
behaves like "since the start of yesterday". Generating the bound in the stored
format keeps the comparison correct and still lets it use the index on
`occurred_at`.

`strftime()` is only used for bucketing, where SQLite parses the ISO offset
correctly, so bucket assignment was never affected.

## Aggregation queries

All read endpoints aggregate in SQL. The functions in `db.py`:

| Function | Endpoint | Grouping |
| --- | --- | --- |
| `fetch_summary` | `/api/summary` | Whole table, plus an active-session count over the active window |
| `fetch_users` | `/api/users` | By `user_id`, with the mapped project joined in |
| `fetch_sessions` | `/api/sessions` | `LEFT JOIN` events onto sessions, grouped by session, newest 100 |
| `fetch_usage_by_model` | `/api/usage-by-model` | By `model`, `COALESCE`d to `'unknown'` |
| `fetch_usage_over_time` | `/api/usage-over-time` | By time bucket |
| `fetch_usage_over_time_by_project` | `/api/usage-over-time-by-project` | By time bucket and project |

### Time bucketing

Windows up to 48 hours bucket hourly; longer windows — and all-time, where
`hours` is `None` — bucket daily (`_granularity()`). This keeps a 30-day chart
at ~30 points instead of ~720. The dashboard mirrors the rule when choosing
axis label format, so the two must stay in step.

Buckets are named in the **display time zone** (`displayTimeZone`, an IANA
name, default `UTC`). Timestamps are stored in UTC and stay that way — only the
day boundaries move. That matters wherever the offset is not zero: in UTC+8 a
UTC-day bucket runs 08:00 to 08:00 local, so "yesterday's spend" on the chart
was never anybody's yesterday.

`_bucket_sql()` picks one of two implementations:

| Zone | Expression | Bucket string |
| --- | --- | --- |
| `UTC` (default) | `strftime('%Y-%m-%dT00:00:00Z', occurred_at)` | `2026-09-10T00:00:00Z` |
| anything else | `local_bucket(occurred_at)`, a Python function registered on the connection | `2026-09-10T00:00:00+08:00` |

UTC keeps the pure-SQL path: it is the default, it is exact, and leaving it
alone means configuring nothing changes nothing. Any other zone needs a
per-row conversion rather than a fixed offset added to the SQL, because a fixed
offset is wrong on either side of a DST transition. `conn.create_function(...,
deterministic=True)` lets SQLite reuse results for repeated inputs.

The format string is interpolated into the SQL, but only ever from these
literals — never from user input. `displayTimeZone` is validated against
`zoneinfo` on save and re-checked on read; an unresolvable zone falls back to
UTC rather than raising, because a chart in the wrong zone beats a chart that
500s.

Bucket strings therefore carry a real offset instead of always ending in `Z`.
Consumers must read the label off the string; passing it through a `Date` and
re-formatting applies the *reader's* zone on top and shifts the label off its
own bucket.

Buckets with no events produce no row. Consumers must treat the series as sparse.

## Empty-session cleanup

Claude Code emits telemetry when a session starts, so a dev container that comes
up and is never actually used still creates a `sessions` row and a zero-token
event. Those are useful while the container is live and worthless once it goes
quiet.

`purge_empty_sessions()` deletes sessions that have gone inactive without ever
logging usage, along with their placeholder events. A session qualifies when
both hold:

- `last_seen_at` is older than the active window (`ACTIVE_WINDOW_MINUTES`,
  default 15), and
- the sum of its input, output, cache-read and cache-creation tokens is 0
  **and** its total cost is 0, and
- it has no rows in `metric_points`. Active time, commits and lines of code
  arrive on the metrics stream alone, with no billable API call behind them —
  purging such a session would delete the only record of work the productivity
  figures are computed from.

Any session that recorded real usage is never touched, however old. A session
that is still live is never touched, however empty.

It runs on a 60-second loop started from the FastAPI lifespan
(`_purge_loop` in `main.py`), so cleanup happens whether or not anyone has the
dashboard open. A purge failure is logged and the loop continues.

If a purged container starts being used again, its next event simply recreates
the session row.

## Querying the database directly

```bash
# Docker
docker compose exec ingest python -c "
import sqlite3; c=sqlite3.connect('/data/usage.db')
for r in c.execute('SELECT model, COUNT(*), ROUND(SUM(cost_usd),2) FROM usage_events GROUP BY model'):
    print(r)"

# Local — the host bind mount
sqlite3 ./usage-data/usage.db "SELECT COUNT(*) FROM usage_events;"
```

Useful one-liners:

```sql
-- Spend per day, last 30 days.
-- Note the bound format: timestamps are TEXT and compare lexicographically, so
-- comparing against datetime('now', ...) — which uses a space separator — would
-- wrongly include the whole boundary day. See "Timestamps" above.
SELECT date(occurred_at) AS day, ROUND(SUM(cost_usd), 2) AS usd
FROM usage_events
WHERE occurred_at >= strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now', '-30 days')
GROUP BY day ORDER BY day;

-- Which attributes are arriving that we don't have columns for
SELECT DISTINCT key FROM usage_events, json_each(usage_events.raw_attributes)
ORDER BY key;

-- Sessions that never got tagged with a project
SELECT session_id, last_seen_at FROM sessions
WHERE project_name IS NULL ORDER BY last_seen_at DESC;

-- User ids with no project mapping yet — candidates for the Users page
SELECT DISTINCT s.user_id FROM sessions s
LEFT JOIN user_projects up ON up.user_id = s.user_id
WHERE s.user_id IS NOT NULL AND up.user_id IS NULL;
```

Open the file read-only (`sqlite3 -readonly`) if the service is running and you
only intend to look.

`PRAGMA foreign_keys = ON` is set on every connection, so
`usage_events.session_id → sessions.session_id` is now enforced rather than
declarative. Deleting a session with events still attached fails; delete the
events first, as the purge and retention sweeps do.

## Growth and retention

Roughly one row per Claude Code API request. A handful of busy sessions produces
tens of thousands of rows a week — small, but `raw_attributes` is the bulk of
each row's size.

Two settings, both **off by default**, applied by the same 60-second sweep that
purges empty sessions:

| Variable | What it does | Changes your numbers? |
| --- | --- | --- |
| `RAW_ATTRIBUTES_RETENTION_DAYS` | Nulls `raw_attributes` on older events **and metric points** | **No** — every aggregate is computed from the typed columns |
| `RETENTION_DAYS` | Deletes older events **and metric points**, and sessions left empty by it | **Yes** — all-time totals shrink |

`GET /api/maintenance` reports the two tables separately
(`events_deleted` / `metric_points_deleted`): a 60-second metrics stream
produces far more rows than the event stream, so a combined figure would be
dominated by metric points and read as though the event table had been
emptied.

Reach for the first one. It reclaims most of the space and costs you only the
ability to recover an attribute that was never promoted to a column.

Progress is reported by `GET /api/maintenance`. `VACUUM` is not run
automatically — it needs exclusive access and temporarily doubles the file
size, so run it by hand during a quiet period if you want the space returned to
the filesystem rather than reused.
