# Development

## Repository layout

```
.
├── docker-compose.yml        Both services, ports, volumes, backup wiring
├── .env.example              Template for local configuration
├── docs/                     This documentation
├── ingest/                   FastAPI collector + read API
│   ├── Dockerfile
│   ├── requirements.txt      Runtime deps (fastapi, uvicorn, tzdata)
│   ├── requirements-dev.txt  Adds httpx and pytest for the test suite
│   ├── pytest.ini            pythonpath, testpaths, default flags
│   ├── conftest.py           Redirects DB_PATH to a scratch dir before import
│   ├── schema.sql            Tables and indexes
│   ├── test_ingest.py        End-to-end test suite
│   ├── dedupe.py             One-off cleanup of pre-existing retry duplicates
│   └── app/
│       ├── main.py           Routes, auth, CORS, lifespan
│       ├── otlp.py           OTLP logs JSON → LogEvent (pure, no I/O)
│       ├── otlp_metrics.py   OTLP metrics JSON → MetricPoint (pure, no I/O)
│       ├── db.py             Connections, schema init, all SQL
│       └── backup.py         Snapshot, ship, prune, schedule, status
└── dashboard/                SolidJS + Vite + Tailwind v4
    ├── Dockerfile            Node build → nginx serve
    ├── nginx.conf.template   /api proxy with server-side token injection
    ├── vite.config.ts        Dev proxy to 127.0.0.1:8000
    ├── index.html            Pre-paint theme script
    └── src/
        ├── index.tsx         Router: / , /tablet , /insights , /users , /settings
        ├── api.ts            All fetch calls + response types
        ├── settings.ts       Window constants and options
        ├── settingsStore.ts  The one shared server-settings resource
        ├── resource.ts       latest() / firstError() — safe resource reads
        ├── chart.ts          Pure chart maths and label formatting
        ├── chart.test.ts     Vitest: bucket labels, niceMax, axis formatting
        ├── format.ts         Number, cost, and date formatting
        ├── format.test.ts    Vitest: token/cost abbreviation thresholds
        ├── index.css         Tailwind entry, dark-variant remap
        └── components/       SummaryCards, SessionsTable, UsageChart,
                              ModelBreakdown, TabletDashboard, UsersPage,
                              InsightsPage, SettingsPage, ThemeToggle
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

Both suites are run by hand before any change set is considered done. There is
no CI ([R11](roadmap.md#r11-continuous-integration) is deferred by decision),
so this is the only guard against regressions — it is not optional.

### Backend — pytest

```bash
cd ingest
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest              # the whole suite, ~2s
.venv/bin/python -m pytest -k metrics   # one area
.venv/bin/python -m pytest -x -vv       # stop at the first failure, verbose
```

[`ingest/test_ingest.py`](../ingest/test_ingest.py) drives the real FastAPI app
through `TestClient` rather than calling `db.py` directly — the interesting
failures have historically been in the seams (the OTLP envelope, transaction
boundaries, the auth dependency, the lifespan), not in a single function.

Each test starts from a fresh database via `_reset_db()`. `make_payload` builds
a one-record OTLP *logs* payload with a controllable age, token count and
project; `make_metrics_payload` does the same for a one-point *metrics* payload.

Two things to remember:

- **`DB_PATH`, `AUTH_TOKEN` and `ALLOW_ANONYMOUS` are read at import time.**
  `DB_PATH` is therefore redirected in [`conftest.py`](../ingest/conftest.py),
  which pytest loads before it imports any test module — without that, the
  suite deleted `ingest/usage.db`, the file local development writes to.
  `_reset_db()` asserts it is pointed at the scratch directory before
  unlinking anything.
- **A test that reloads `app.main` must restore it.** `test_auth_gating` and
  `test_refuses_to_start_without_a_token` both `importlib.reload(main_module)`
  in a `finally`, so they work in any order. pytest guarantees no ordering;
  do not rely on one.

### Frontend — Vitest

```bash
cd dashboard
npm test              # once
npm run test:watch    # on change
```

[`chart.test.ts`](../dashboard/src/chart.test.ts) and
[`format.test.ts`](../dashboard/src/format.test.ts) cover the pure logic most
likely to break silently: bucket label parsing (which must not depend on the
browser's time zone), `niceMax`'s axis rounding, and the token/cost
abbreviation thresholds.

They run in the `node` environment via
[`vitest.config.ts`](../dashboard/vitest.config.ts) — no DOM, no Solid plugin,
no jsdom dependency — because everything under test is a pure function.
`chart.ts` exists precisely so that logic can be imported without mounting a
component. A future *component* test would need `environment: 'jsdom'`, the
Solid plugin and `@solidjs/testing-library`; add them then.

`npm run build` runs `tsc -b` first, so a type error fails the build. Run both.

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
which comes from `_bucket_sql()`'s two literals.

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
  `_granularity()`; the chart mirrors it in `granularityFor` (in
  [`chart.ts`](../dashboard/src/chart.ts), covered by `chart.test.ts`) to choose
  the axis label format. Change one and you must change the other, or daily
  buckets start rendering as clock times again.
- **Never build a `Date` from a bucket string.** The server already names each
  bucket in the configured display zone; re-formatting through a `Date` applies
  the *browser's* zone a second time and shifts the label off its own bucket.
  Read the characters — that is what `parts()` in `chart.ts` is for.
- **Watch prop order with spreads.** `<UsageChart {...chartProps()} hours={24}
  />` silently overrides the spread `hours` — that was exactly the axis-label
  bug. Put explicit props before the spread, or don't duplicate them.

## Adding a settings field

Settings live **on the server** (a single JSON row in `app_settings`), so a new
field touches both sides:

1. Add it to `DEFAULT_SETTINGS` in [`db.py`](../ingest/app/db.py). Unknown keys
   are ignored on write and defaults are merged under the stored object, so
   there is **no migration** — an older stored row picks up the new default.
2. Add a branch to `_coerce_setting()` validating it. Raise `ValueError` for a
   bad value; the route turns that into a `400`. A setting with no branch is
   rejected as unknown, so this step is not optional.
3. Add it to `AppSettings` in [`api.ts`](../dashboard/src/api.ts) and to
   `FALLBACK_SETTINGS` in
   [`settingsStore.ts`](../dashboard/src/settingsStore.ts) — the fallback is
   what every view reads until the first fetch resolves.
4. Add a signal, a seed line in the `createEffect`, and a line in the `patch`
   object in `SettingsPage.tsx`, then a `<Field>` in the relevant `<Section>`.
5. Read it anywhere via `settings()`, which never throws and never returns
   undefined.
6. Document it in [Configuration](configuration.md#dashboard-preferences) and
   in the `GET /api/settings` example in [API reference](api-reference.md).

Only genuinely per-device preferences stay in `localStorage` — currently just
the theme.

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
strict, checked with `tsc -b`. No lint config in the repo; match what's around
you.

Comments in this codebase explain *why*, not *what* (see the notes on WAL mode,
attribute aliases, and the pre-paint theme script). Follow that.
