# Development

## Repository layout

```
.
├── docker-compose.yml        Both services, ports, volumes, backup wiring
├── .env.example              Template for local configuration
├── docs/                     This documentation
├── ingest/                   FastAPI collector + read API
│   ├── Dockerfile
│   ├── requirements.txt      Runtime deps (fastapi, uvicorn)
│   ├── requirements-dev.txt  Adds httpx for the smoke test
│   ├── schema.sql            Tables and indexes
│   ├── test_ingest.py        End-to-end smoke test
│   ├── dedupe.py             One-off cleanup of pre-existing retry duplicates
│   └── app/
│       ├── main.py           Routes, auth, CORS, lifespan
│       ├── otlp.py           OTLP JSON → LogEvent (pure, no I/O)
│       ├── db.py             Connections, schema init, all SQL
│       └── backup.py         Snapshot, ship, prune, schedule, status
└── dashboard/                SolidJS + Vite + Tailwind v4
    ├── Dockerfile            Node build → nginx serve
    ├── nginx.conf.template   /api proxy with server-side token injection
    ├── vite.config.ts        Dev proxy to 127.0.0.1:8000
    ├── index.html            Pre-paint theme script
    └── src/
        ├── index.tsx         Router: / , /tablet , /settings
        ├── api.ts            All fetch calls + response types
        ├── settings.ts       localStorage prefs, rate & cost helpers
        ├── format.ts         Number, cost, and date formatting
        ├── index.css         Tailwind entry, dark-variant remap
        └── components/       SummaryCards, SessionsTable, UsageChart,
                              ModelBreakdown, TabletDashboard, UsersPage,
                              SettingsPage, ThemeToggle
```

## Local setup

See [Getting started → Option B](getting-started.md#option-b--run-locally-no-docker)
for the full sequence. In short:

```bash
# terminal 1
cd ingest && python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
INGEST_ALLOW_ANONYMOUS=1 .venv/bin/uvicorn app.main:app --port 8000 --reload

# terminal 2
cd dashboard && npm install && npm run dev
```

Leave `INGEST_AUTH_TOKEN` unset locally. Nothing injects the header in dev, so
setting it just produces 401s.

## Tests

One smoke test, [`ingest/test_ingest.py`](../ingest/test_ingest.py), run
directly rather than through pytest:

```bash
cd ingest
.venv/bin/python test_ingest.py
```

It covers, in order: OTLP ingestion into SQLite, the bearer-token gate,
user→project mapping (retroactive, forward-applying and clearable), the
empty-session purge, and the time windows (that a 24h window excludes 30h-old
data, and that `hours=0` returns all time). Each test starts from a fresh
database via `_reset_db`, and `make_payload` builds a one-record OTLP payload
with a controllable age and token count.

The thing to remember: `DB_PATH`, `AUTH_TOKEN` and `ALLOW_ANONYMOUS` are all
read at **import time**. `DB_PATH` is therefore set before `app.db` is imported
at the top of the file, and a test that changes `INGEST_AUTH_TOKEN` mid-run must
`importlib.reload(main_module)` afterwards — as `test_auth_gating` and
`test_refuses_to_start_without_a_token` both do.

There are no frontend tests. `npm run build` runs `tsc -b` first, so a type
error fails the build — treat that as the frontend's check.

## Adding an API endpoint

1. **Write the query** in [`db.py`](../ingest/app/db.py) as a `fetch_*`
   function using `_connect()` and returning plain dicts. Aggregate in SQL, not
   in Python.
2. **Add the route** in [`main.py`](../ingest/app/main.py) with
   `dependencies=[Depends(require_auth)]` — every route except `/healthz` has
   it. Cap any user-supplied window as the existing ones do
   (`min(hours, 720)`).
3. **Add the type and fetcher** to [`api.ts`](../dashboard/src/api.ts) — an
   `export interface` for the response and a one-line `getJSON` wrapper.
4. **Consume it** in a component via `createResource`, and add its `refetch` to
   the interval in `App.tsx` and/or `TabletDashboard.tsx` if it should live-update.
5. **Document it** in [API reference](api-reference.md).

Never interpolate user input into SQL. `hours` reaches the query as a bound
parameter; the only interpolated value anywhere is the bucket format string,
which comes from `_time_bucket_fmt`'s two literals.

## Adding a telemetry attribute

Covered step by step in
[Data model → OTLP attribute mapping](data-model.md#otlp-attribute-mapping).
The short version: add the alias to `_ATTR_ALIASES`, the field to `LogEvent`,
the column to `schema.sql` **and** a guarded `ALTER TABLE` in `init_db()`, the
column to `insert_event`'s `INSERT`, then backfill from `raw_attributes` with
`json_extract`.

Because unrecognized attributes are already stored in `raw_attributes`,
historical data is not lost while an attribute is unmapped.

## Schema changes

There is no migration tool. The pattern is:

```python
# in init_db(), after executescript
try:
    conn.execute("ALTER TABLE sessions ADD COLUMN new_column TEXT")
except sqlite3.OperationalError:
    pass  # column already exists
```

Additive, nullable columns only. Anything destructive needs a one-off script
and a [backup](backup-and-restore.md) taken first.

## Frontend conventions

- **SolidJS, not React.** `createSignal` / `createMemo` / `createResource`, and
  props are accessed as `props.x` inside JSX so reactivity survives — never
  destructure props.
- **Tailwind v4** via `@tailwindcss/vite`. Dark mode is a `dark` class on
  `<html>`, remapped from the default media query in `index.css`. Every color
  needs both a light and a `dark:` variant.
- **`api.ts` owns all network access.** Components import fetchers from it and
  never call `fetch` directly.
- **Charts are hand-rolled SVG.** [`UsageChart.tsx`](../dashboard/src/components/UsageChart.tsx)
  uses a fixed 800×220 viewBox with explicit margin constants and scales into
  it. No charting dependency — keep it that way unless there is a strong reason.
- **Formatting goes through [`format.ts`](../dashboard/src/format.ts)** so
  numbers and costs render consistently.
- **Sparse series.** Time-series endpoints omit empty buckets. Anything
  consuming them must handle gaps rather than assuming contiguity.
- **Bucket granularity lives in two places.** The server picks it in
  `_time_bucket_fmt`; the chart mirrors it in `granularityFor` to choose the
  axis label format. Change one and you must change the other, or daily buckets
  start rendering as clock times again.
- **Watch prop order with spreads.** `<UsageChart {...chartProps()} hours={24}
  />` silently overrides the spread `hours` — that was exactly the axis-label
  bug. Put explicit props before the spread, or don't duplicate them.

## Adding a settings field

1. Add it to `AppSettings` and `DEFAULT_SETTINGS` in
   [`settings.ts`](../dashboard/src/settings.ts). `loadSettings` spreads
   defaults under the stored object, so existing browsers pick up the new field
   without a reset.
2. Add a `<Field>` to the relevant `<Section>` in `SettingsPage.tsx`.
3. Read it where it applies.

Remember these are per-browser and never reach the server.

## Adding a time window

`WINDOW_HOURS` and `WINDOW_OPTIONS` in
[`settings.ts`](../dashboard/src/settings.ts) are the single source for the
window toggles on both the desktop and tablet views — add an entry there and it
appears in both. `0` is the API's all-time sentinel; the server maps anything
`<= 0` to "no look-back" in `_window()`. Add the option to `SettingsPage.tsx`'s
default-window `OptionGroup` too, so it can be chosen as the startup default.

## Building for production

```bash
docker compose build              # both images
docker compose build ingest       # one

cd dashboard && npm run build     # tsc -b && vite build → dist/
```

## Debugging

```bash
docker compose logs -f ingest
docker compose exec ingest sh

# What's actually arriving
curl -s -H "Authorization: Bearer $INGEST_AUTH_TOKEN" \
  http://localhost:9585/api/sessions | python3 -m json.tool

# Inspect stored attributes for the newest event
sqlite3 ./usage-data/usage.db \
  "SELECT raw_attributes FROM usage_events ORDER BY id DESC LIMIT 1;"
```

For parser work, `extract_log_events` is a pure function — feed it a payload
dict in a REPL and inspect the `LogEvent` list without touching the database.

## Code style

Python: standard library plus FastAPI, type hints throughout, module-level
constants read from the environment at import time. Frontend: TypeScript
checked with `tsc -b`, though `strict` is **not** enabled — see
[known issue 29](known-issues.md#29-typescript-strict-is-off). No lint config in
the repo; match what's around you.

Comments in this codebase explain *why*, not *what* (see the notes on WAL mode,
attribute aliases, and the pre-paint theme script). Follow that.
