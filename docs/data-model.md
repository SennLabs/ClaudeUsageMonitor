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
| `project_name` | TEXT | Manual label, set via `PATCH /api/sessions/{id}` |

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
| `raw_attributes` | TEXT | JSON of the complete merged attribute map |

### Indexes

```sql
CREATE INDEX idx_usage_events_session ON usage_events(session_id);
CREATE INDEX idx_usage_events_time    ON usage_events(occurred_at);
CREATE INDEX idx_sessions_user        ON sessions(user_id);
```

The time index is what keeps the windowed chart queries cheap as the table
grows; the user index serves the Users page and mapping updates.

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

```python
def _time_bucket_fmt(hours: int | None) -> str:
    return '%Y-%m-%dT%H:00:00Z' if hours is not None and hours <= 48 else '%Y-%m-%dT00:00:00Z'
```

Windows up to 48 hours bucket hourly; longer windows — and all-time, where
`hours` is `None` — bucket daily. This keeps a 30-day chart at ~30 points
instead of ~720. The dashboard mirrors this rule when choosing axis label
format, so the two must stay in step. The format string is interpolated
into the SQL, but only ever from this function's two literals — never from user
input.

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
  **and** its total cost is 0.

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

## Growth and retention

Roughly one row per Claude Code API request. A handful of busy sessions produces
tens of thousands of rows a week — small, but `raw_attributes` is the bulk of
each row's size. Nothing prunes automatically. If the file gets unwieldy:

```sql
DELETE FROM usage_events
 WHERE occurred_at < strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now', '-180 days');
VACUUM;
```

Take a backup first, and note that this rewrites all-time totals on the summary
cards.
