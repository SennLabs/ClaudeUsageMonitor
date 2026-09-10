# Known issues — to be done

Compiled 2026-09-10 from a four-way review of the codebase (backend, frontend,
security/deployment, docs-vs-code). Nothing here is fixed yet.

**Verification status** is marked on each item:

- **[verified]** — reproduced in-session against the real code, with a script or
  a live server. Treat as fact.
- **[reported]** — found by review and consistent with the code on reading, but
  not independently reproduced. Confirm before acting.
- **[narrowed]** — reported as severe, investigated, and found to be materially
  smaller than first described. The entry says how.

Severity is relative to this project's actual threat and usage model: a
single-instance internal tool on a private network.

---

## Checklist


**P1 — fix first**

- [ ] [1. Model price overrides and the budget cycle are dead code](#1-model-price-overrides-and-the-budget-cycle-are-dead-code) — *verified*
- [x] [2. A failed API call renders no error at all](#2-a-failed-api-call-renders-no-error-at-all) — *fixed*
- [x] [3. A failed project-tag save is swallowed silently](#3-a-failed-project-tag-save-is-swallowed-silently) — *fixed*
- [ ] [4. CORS `*` plus the token-injecting proxy makes port 9595 an open API](#4-cors--plus-the-token-injecting-proxy-makes-port-9595-an-open-api) — *verified*
- [ ] [5. Ingest blocks its own event loop](#5-ingest-blocks-its-own-event-loop) — *verified*
- [ ] [6. Batches are not atomic, so failures permanently inflate totals](#6-batches-are-not-atomic-so-failures-permanently-inflate-totals) — *verified*
- [ ] [7. Malformed OTLP payloads return 500 instead of 400](#7-malformed-otlp-payloads-return-500-instead-of-400) — *reported*
- [ ] [8. Backups fail silently in several ordinary configurations](#8-backups-fail-silently-in-several-ordinary-configurations) — *verified*
- [ ] [9. Concurrent backups collide on one temp file](#9-concurrent-backups-collide-on-one-temp-file) — *reported*
- [ ] [31. User-ID mapping breaks on every dev container rebuild](#31-user-id-mapping-breaks-on-every-dev-container-rebuild) — *verified*

**P2 — should fix**

- [x] [10. `log.info` is never emitted](#10-loginfo-is-never-emitted) — *fixed*
- [ ] [11. `ACTIVE_WINDOW_MINUTES` is not passed through Compose](#11-active_window_minutes-is-not-passed-through-compose) — *verified*
- [ ] [12. Three different "active session" windows](#12-three-different-active-session-windows) — *verified*
- [ ] [13. Events with no `session.id` inflate the headline only](#13-events-with-no-sessionid-inflate-the-headline-only) — *verified*
- [ ] [14. `computeHourlyRate` is a sawtooth, not a rate](#14-computehourlyrate-is-a-sawtooth-not-a-rate) — *reported*
- [ ] [15. Polling destroys an in-progress inline edit](#15-polling-destroys-an-in-progress-inline-edit) — *reported*
- [ ] [16. Chart rendering edge cases](#16-chart-rendering-edge-cases) — *reported*
- [ ] [17. `/tablet` in light theme has invisible chart axes](#17-tablet-in-light-theme-has-invisible-chart-axes) — *reported*
- [ ] [18. Empty `INGEST_AUTH_TOKEN` silently disables all authentication](#18-empty-ingest_auth_token-silently-disables-all-authentication) — *reported*
- [ ] [19. `/docs`, `/redoc` and `/openapi.json` are unauthenticated](#19-docs-redoc-and-openapijson-are-unauthenticated) — *verified*
- [ ] [20. No body-size cap on `POST /v1/logs`, and no retention policy](#20-no-body-size-cap-on-post-v1logs-and-no-retention-policy) — *reported*
- [ ] [21. Container and deployment hardening](#21-container-and-deployment-hardening) — *reported*
- [ ] [22. Healthchecks pass in the situations that actually break the system](#22-healthchecks-pass-in-the-situations-that-actually-break-the-system) — *reported*
- [x] [23. `test_ingest.py` deletes the development database](#23-test_ingestpy-deletes-the-development-database) — *fixed*

**P3 — worth doing**

- [ ] [24. `/api/sessions` is a hard `LIMIT 100` with no pagination](#24-apisessions-is-a-hard-limit-100-with-no-pagination) — *reported*
- [ ] [25. Data-quality gaps in the OTLP parser](#25-data-quality-gaps-in-the-otlp-parser) — *reported*
- [ ] [26. Robustness details in the purge and shutdown paths](#26-robustness-details-in-the-purge-and-shutdown-paths) — *reported*
- [ ] [27. Settings and formatting papercuts](#27-settings-and-formatting-papercuts) — *reported*
- [ ] [28. Touch and kiosk ergonomics on `/tablet`](#28-touch-and-kiosk-ergonomics-on-tablet) — *reported*
- [ ] [29. TypeScript `strict` is off](#29-typescript-strict-is-off) — *verified*
- [ ] [30. Backup SSH trust and argument quoting](#30-backup-ssh-trust-and-argument-quoting) — *reported*

**D. Documentation corrections**

- [ ] [D1. `README.md` is out of date in four places](#d1-readmemd-is-out-of-date-in-four-places) — *verified*
- [ ] [D2. `security.md` wrongly says `.claude/settings.json` is committed](#d2-securitymd-wrongly-says-claudesettingsjson-is-committed) — *verified*
- [ ] [D3. A real internal IP is published in a committed file](#d3-a-real-internal-ip-is-published-in-a-committed-file) — *verified*
- [ ] [D4. `api-reference.md` factual errors](#d4-api-referencemd-factual-errors) — *verified*
- [ ] [D5. Docs describe the dead features as working](#d5-docs-describe-the-dead-features-as-working) — *verified*
- [ ] [D6. Diagnostics in the docs that do not work](#d6-diagnostics-in-the-docs-that-do-not-work) — *verified*
- [ ] [D7. Smaller doc inaccuracies](#d7-smaller-doc-inaccuracies) — *verified*
- [ ] [D8. Client setup recommends the wrong default for dev containers](#d8-client-setup-recommends-the-wrong-default-for-dev-containers) — *verified*

---

## P1 — fix first

### 1. Model price overrides and the budget cycle are dead code

*Status: **verified**.*

`dashboard/src/settings.ts:89` — `adjustedCost()` has exactly one reference in
the repo: its own definition. `billingCycleDay` is written by the settings form
and read by no calculation anywhere.

Consequences:

- Per-model price overrides change no displayed figure. `SettingsPage.tsx:392`
  tells the user "the Model Breakdown table shows an adjusted cost column
  alongside the reported cost"; `ModelBreakdown.tsx` has five columns and no
  such column.
- The tablet budget bar computes `usedFraction` from `summary().total_cost_usd`,
  which is the **all-time** total. It is not a monthly bar and it never resets.

Fix: either wire both through (`adjustedCost` into `ModelBreakdown` and the
session/summary cost displays; a cycle-start filter for the budget bar), or
remove the settings and the copy that promises them. Do not leave them
half-present. See also the doc corrections in section D.

### 2. A failed API call renders no error at all

*Status: **fixed**.*

**Fixed 2026-09-10.** Added `dashboard/src/resource.ts` with `latest()` / `firstError()`; every resource read in `App.tsx`, `TabletDashboard.tsx` and `UsersPage.tsx` now checks `.error` before reading, and falls back to `.latest` so a failed refetch keeps the last good value on screen instead of blanking it. The banner now covers all five resources (was three) and shows the actual error text. An `ErrorBoundary` in `index.tsx` is a backstop for anything unhandled. Verified with a Solid repro: banner renders, no throw.

Reading an errored `createResource` accessor **throws** in Solid
(`solid-js/dist/solid.js:322`). In `App.tsx` the effect reading `summary()` is
created before the error toast's insert, so the throw aborts the update batch
and the toast is never inserted. Reproduced against the installed Solid:

```
accessor threw on read : HTTP 401
error toast rendered   : false
```

Symptom: a token mismatch or a stopped backend gives a permanently blank
dashboard with no message, plus an unhandled rejection every poll interval.
Affects `/`, `/users` and `/tablet`. There is no `ErrorBoundary` anywhere in the
app.

Fix: wrap each route in an `ErrorBoundary`, and read resources defensively
(`summary.error ? undefined : summary.latest`) or check `.error` before the
accessor. The existing toast condition also only covers 3 of 5 resources — moot
today because it covers 0 in practice, but fix both.

### 3. A failed project-tag save is swallowed silently

*Status: **fixed**.*

**Fixed 2026-09-10.** `commitEdit` in `SessionsTable.tsx` now catches, keeps the row in edit state so the typed value survives, and shows the error above the table — matching the pattern `UsersPage` already used.

`dashboard/src/components/SessionsTable.tsx:152` — `commitEdit` is
`try { … } finally { setEditingId(null) }` with no `catch`. A PATCH that 401s
closes the input, discards the edit, shows nothing, and leaves an unhandled
rejection. The user believes the tag saved.

`UsersPage.tsx:47` handles the same flow correctly (catch → `setError`, keeps
the row in edit state). Copy that pattern.

### 4. CORS `*` plus the token-injecting proxy makes port 9595 an open API

*Status: **verified**.*

`ingest/app/main.py:48` sets `allow_origins=["*"]`;
`dashboard/nginx.conf.template:13` attaches `Authorization` server-side. So a
cross-origin page never needs the token — nginx supplies it — and ingest's
`Access-Control-Allow-Origin: *` lets the page read the response. Confirmed
against a live server:

```
Origin: https://evil.example              -> 200, access-control-allow-origin: *
OPTIONS preflight for DELETE              -> allow-methods: GET, PATCH, POST, PUT, DELETE
```

Writes are in scope too: `DELETE /api/user-projects/{id}` and
`POST /api/backup/trigger` both pass preflight.

Mitigating factors, so this is not scored higher: an HTTPS page cannot fetch
`http://` (mixed content), and Chrome's private-network access restrictions
block some of the remainder. The realistic vector is an HTTP page or an older
browser, not any site on the internet.

Fix: the dashboard is same-origin through nginx and needs no CORS at all. Only
the Vite dev server does, and `vite.config.ts` already proxies for it. Drop
`CORSMiddleware`, or set `allow_origins=["http://localhost:5173"]`. Optionally
add `proxy_hide_header Access-Control-Allow-Origin;` to the nginx `/api/` block.

### 5. Ingest blocks its own event loop

*Status: **verified**.*

`ingest/app/main.py:69` — `ingest_logs` is `async def` but calls
`db.upsert_session` and `db.insert_event` synchronously, and `db._connect`
opens a fresh connection, sets `PRAGMA journal_mode=WAL`, commits and closes for
every statement. That is two connect/commit/close cycles per record, on the
event loop thread.

Measured: one 200-record batch = **1.65 s of total event-loop starvation**.
`/healthz` is unserved throughout, and the Dockerfile healthcheck is
`--timeout=3s --retries=3`, so sustained load can get the container restarted
mid-write.

Fix: `await asyncio.to_thread(...)` around the whole loop, batched into one
connection with a single transaction and `executemany`. `_purge_loop`
(`main.py:25`) already does this correctly.

### 6. Batches are not atomic, so failures permanently inflate totals

*Status: **verified**.*

Each record commits in its own transaction. A malformed record mid-batch leaves
earlier records committed and returns 500:

```
status=500   sessions before=2 after=3
```

OTLP exporters retry 5xx by design and `usage_events` has no idempotency key, so
every transient failure double-counts cost and tokens forever.

Fix: one transaction per batch, plus a natural dedupe key (e.g. a unique index
on `session_id, occurred_at, event_name, model, input_tokens, output_tokens`, or
a hash of the record) with `INSERT OR IGNORE`. Pairs with item 5.

### 7. Malformed OTLP payloads return 500 instead of 400

*Status: **reported**.*

`ingest/app/otlp.py:90` — `.get("resource", {})` defends against missing keys
but not JSON `null`. All of these 500: `"resource": null`,
`"attributes": null`, `"scopeLogs": null`, a top-level JSON list, an
`intValue` that is not numeric (`_decode_value` line 57 calls `int()` before the
guarded cast at line 109), and syntactically invalid JSON.

Combined with item 6 this is the retry-duplication engine. Fix: validate the
envelope, skip and count bad records rather than aborting, and return 400 for a
genuinely malformed body.

### 8. Backups fail silently in several ordinary configurations

*Status: **verified**.*

All in `ingest/app/backup.py`. Grouped because they share one root cause —
`run_backup` never raises and `POST /api/backup/trigger` returns 200 regardless,
so every one of these reports success:

| # | Config | What happens |
| --- | --- | --- |
| 8a | `BACKUP_DESTINATION=/data` (the Dockerfile's own volume) | `shutil.copy2` raises `SameFileError`, swallowed; the good snapshot is deleted by `finally` |
| 8b | `BACKUP_DESTINATION=nas:/volume1/backups` (no `user@`) | `_is_remote` is `"@" in dest and ":" in dest`, so it is treated as **local**: creates a directory literally named `nas:` inside the container layer, destroyed on next rebuild |
| 8c | `BACKUP_KEEP=0` | `files[:-0]` is empty, so **everything** is retained rather than nothing |
| 8d | `BACKUP_INTERVAL_HOURS=0` | `asyncio.sleep(0.0)` spin loop — 4121 full backups in 0.3 s |
| 8e | `BACKUP_INTERVAL_HOURS=daily` | `float()` at module scope raises at import; the whole ingest service fails to start over a backup setting |
| 8f | Any rsync destination | `_prune_local` is only called in the local branch, so `BACKUP_KEEP` is ignored and the NAS fills without bound |
| 8g | Restart cadence < `BACKUP_INTERVAL_HOURS` | `start_scheduler` sets `next_run = now + interval` before the loop, so a nightly-redeployed container never backs up once |

Fix: replace the `_is_remote` heuristic with an explicit `BACKUP_MODE=local|rsync`;
validate `INTERVAL`/`KEEP` at startup with sane floors and a clear error;
refuse a destination equal to `db_path.parent`; in local mode assert the
destination is a real mount point rather than creating it; run once at startup
(or after a short delay) rather than after a full interval; make
`/api/backup/trigger` return 500 when the run it just performed failed.

### 9. Concurrent backups collide on one temp file

*Status: **reported**.*

`backup.py:38` names the snapshot from a **1-second-resolution** timestamp in
`db_path.parent`, and there is no lock — the scheduler and
`POST /api/backup/trigger` both use the default executor and can run in
parallel. Two runs in the same second open two connections onto the same path;
whichever finishes first unlinks the file the other is still using. Observed
result: one backup lost, `attempt to write a readonly database`.

Also `subprocess.run(..., timeout=300)` at `backup.py:65` kills rsync but not the
`ssh` child it spawned; ssh inherits the captured pipes, so the post-kill read
can block well past the timeout against an unresponsive NAS.

Fix: a module-level non-blocking `threading.Lock` returning 409 when busy; a
uuid4 suffix on the snapshot name; `start_new_session=True` and kill the process
group on `TimeoutExpired`.

### 31. User-ID mapping breaks on every dev container rebuild

*Status: **verified**.*

Claude Code documents `user.id` as a *"random anonymous installation ID from
`~/.claude.json`"*. The user has confirmed that in this deployment the dev
container's home directory does not persist — it is recreated with the
container. So `~/.claude.json` is regenerated on every rebuild and the container
reports a **new** `user.id`.

Consequence: the `user_projects` mapping and the whole `/users` page, added
2026-09-10, silently stop working at the first container rebuild. The old
mapping row keeps pointing at an identity that will never report again, the
rebuilt container arrives untagged, and nothing in the UI indicates that
anything has changed — it just looks like a new, unlabelled user appeared while
the old one went quiet. Over time the users table accumulates one dead row per
rebuild.

This is a design fault, not an implementation bug: the project is a property of
the container, but the key chosen for it is an identity the platform
regenerates.

**Fix:** attribute projects with `OTEL_RESOURCE_ATTRIBUTES` instead —
[roadmap R0](roadmap.md#r0-attribute-projects-with-otel_resource_attributes-instead-of-mapping-user-ids).
Keep the `/users` page for containers that cannot be reconfigured and for fixing
historical data, but it must stop being the documented default.

**Interim mitigation** if R0 is not done soon: the stable identifiers available
are `user.account_uuid` / `user.email` (stable across rebuilds, but identical
across every container the same person runs, so useless for per-project
attribution) and `workspace.host_paths`, which reports the host workspace
directory and would at least survive a rebuild. Neither is as good as a resource
attribute. Do not simply re-key the mapping on `user.email` and call it fixed —
it would collapse every project into one row.

---

## P2 — should fix

### 10. `log.info` is never emitted

*Status: **fixed**.*

**Fixed 2026-09-10.** `logging.basicConfig` in `main.py` installs a root handler at WARNING (so httpx and asyncio stay quiet) and the `app` logger is set from `LOG_LEVEL`, default INFO. Verified against a real uvicorn boot: "Backup scheduler started", "Purged N empty inactive session(s)" and "Backup succeeded" all now appear, so the `grep -i purge` diagnostic in [Troubleshooting](troubleshooting.md) works.

There is no logging configuration anywhere in `ingest/`, so under uvicorn's
default config `app.*` inherits an effective level of `WARNING`:

```
effective level for app.main: WARNING
log.info emitted?  False      log.warning emitted? True
```

"Backup succeeded", "Backup scheduler started" and "Purged N empty session(s)"
are all invisible. Only failures surface. Fix with a `logging.basicConfig` at
startup or a uvicorn `--log-config`.

### 11. `ACTIVE_WINDOW_MINUTES` is not passed through Compose

*Status: **verified**.*

`db.py:17` reads it, `docs/configuration.md` documents it, but
`docker-compose.yml` never puts it in the ingest `environment:` block and
`.env.example` does not mention it. Setting it in `.env` currently does nothing.
Fix: add `- ACTIVE_WINDOW_MINUTES=${ACTIVE_WINDOW_MINUTES:-15}` and an
`.env.example` entry.

### 12. Three different "active session" windows

*Status: **verified**.*

| Where | Source | Default |
| --- | --- | --- |
| `/api/summary`, `/api/users`, the purge | `ACTIVE_WINDOW_MINUTES` (server) | 15 min |
| `/tablet` per-session dots | `activeSessionWindowMin` (browser) | 15 min |
| `/` session dots, By-project "Active" column | `SessionsTable.tsx:6`, hardcoded | 15 min |

They agree only at defaults. Change the browser setting to 60 and the same
session shows active on the tablet and inactive on the desktop. Worse, the
server value also decides when an unused session is purged, so a longer browser
window can show a session as active shortly before it is deleted.

Fix: one source of truth. Best done as part of roadmap item R1 (server-side
settings); as a stopgap, have `SessionsTable` read the browser setting and
document that the server value must match.

### 13. Events with no `session.id` inflate the headline only

*Status: **verified**.*

`fetch_summary` sums all of `usage_events`; `fetch_sessions` and `fetch_users`
reach events only through `sessions`. An event with no session id is stored with
`session_id = NULL` and disappears from every per-session and per-user view:

```
headline total_cost_usd : 10.0
sum of per-session cost : 1.0
```

Fix: either attribute them to a synthetic `(unattributed)` session so the views
reconcile, or expose the difference explicitly on the summary. Silently
diverging totals are the worst option.

### 14. `computeHourlyRate` is a sawtooth, not a rate

*Status: **reported**.*

`settings.ts:69` — the hourly branch filters bucket **start times** against
`now - 1h`, so only the current clock-hour bucket ever passes: it reports "spend
since the top of the hour", not spend over the last hour. The cost alert
therefore flaps — banner up at 09:55, gone at 10:00:30 while spend continues.

The daily branch (`newest bucket / 24`, used for 7d/30d/all) divides a *partial*
UTC day by 24, so the tablet's rate tile drops to ~0 at 08:00 local in UTC+8 and
climbs all day.

Fix: interpolate across the trailing window rather than reading one bucket, or
add a dedicated `/api/rate` endpoint that computes it server-side from raw
event timestamps.

### 15. Polling destroys an in-progress inline edit

*Status: **reported**.*

`SessionsTable.tsx:205` — `<For>` keys by reference and every poll yields
freshly-parsed objects, so all rows are disposed and rebuilt every
`refreshIntervalMs`. The `ref` refocuses via `requestAnimationFrame`, but the
caret jumps to the end of the value, and on iOS the focus happens outside a user
gesture so the on-screen keyboard closes and does not reopen — the field becomes
untypeable on the wall tablet.

`UsersPage.tsx:33` already pauses polling while `editingId() !== null`. Apply the
same guard here, and consider `<For>` with a keyed wrapper or `<Index>`.

### 16. Chart rendering edge cases

*Status: **reported**.*

- `UsageChart.tsx:168` — a **single bucket** puts every point at `x = ML`, so the
  line renders as nothing and the area becomes a triangle spanning the full plot
  width. A fresh install with one hour of data shows a large wedge implying a
  full day of ramping spend. Render a dot when `n === 1`.
- `UsageChart.tsx:348` — per-series hover circles render only under
  `isMulti() && showDots()`, and `showDots()` is false above 60 buckets. In
  by-project + all-time, hovering any line falls through to `seriesPaths()[0]`,
  so the tooltip labels every hover with the highest-spending project. Keep
  invisible per-series hit targets independent of the dot cap.
- Nothing resets `hoveredSeries` to `null`, so after touching one line the
  tooltip stays pinned to that series.
- `UsageChart.tsx:62` — `fmtY` uses `toFixed(2)` while `niceMax` can return
  sub-cent maxima, giving five identical `$0.00` axis labels; in tokens mode a
  peak of 3 yields fractional ticks `0.75`, `1.5`, `2.25`. Realistic on a fresh
  install, which is exactly when people study the chart.

### 17. `/tablet` in light theme has invisible chart axes

*Status: **reported**.*

`TabletDashboard.tsx` is hardcoded `bg-slate-950 text-white` and has no
`ThemeToggle`, while the chart card is `bg-white dark:bg-slate-900` and all axis
text and gridlines use `currentColor`. Click "☀️ Light" once on `/`, then open
`/tablet` on the wall device: a white card with white labels, and no way to
switch back from that route. Fix: give the tablet route its own explicit dark
palette independent of the theme class, or add a toggle.

### 18. Empty `INGEST_AUTH_TOKEN` silently disables all authentication

*Status: **reported**.*

`main.py:56` returns early when the token is empty, and
`docker-compose.yml` uses `${INGEST_AUTH_TOKEN:-}`. A missing `.env` — which
`git clone` never provides, since it is correctly gitignored — brings the whole
stack up fully open and reporting healthy, with no signal anywhere.

Fix: `${INGEST_AUTH_TOKEN:?refusing to start without a token}` in Compose, or
keep the open mode behind an explicit `INGEST_ALLOW_ANONYMOUS=true` plus a
startup warning. While in that function, switch to `hmac.compare_digest` — the
current `!=` is not constant-time. That part is not practically exploitable
here, but it is free to fix.

### 19. `/docs`, `/redoc` and `/openapi.json` are unauthenticated

*Status: **verified**.*

All three return 200 without a token on port 9585. `docs/security.md` lists
`/healthz` as the only open route. Impact is recon only and they are not proxied
by nginx, but it is one line to close:
`FastAPI(..., docs_url=None, redoc_url=None, openapi_url=None)`.

### 20. No body-size cap on `POST /v1/logs`, and no retention policy

*Status: **reported**.*

`main.py:70` — `await request.json()` buffers the whole body; uvicorn imposes no
limit and `/v1/logs` is not proxied through nginx, so nginx's
`client_max_body_size` never applies. With no `mem_limit` on the container, a
large POST is a host-level OOM.

Separately, nothing ever deletes old events, and `raw_attributes` is the bulk of
each row. The bind mount grows until the disk fills. Retention is roadmap item
R2; the size cap should be done here.

### 21. Container and deployment hardening

*Status: **reported**.*

- Both containers run as **root**. The ingest container is the one that matters:
  the backup SSH key and the database bind mount are both there. No
  `cap_drop`, `no-new-privileges`, `read_only`, `mem_limit` or `pids_limit`.
- `docker-compose.yml:5,30` publish on `0.0.0.0`, and Docker's rules sit ahead
  of the `INPUT` chain — so `ufw deny 9585` does **not** block it. The hardening
  checklist in `docs/security.md` currently recommends exactly that. Bind
  explicitly (`"192.168.1.50:9585:8000"`) or use the `DOCKER-USER` chain.
- `fastapi>=0.115` / `uvicorn[standard]>=0.30` are unpinned floors and no base
  image is digest-pinned; two builds a month apart differ with no record.
- `nginx:1.27-alpine` is a superseded mainline line.

### 22. Healthchecks pass in the situations that actually break the system

*Status: **reported**.*

- `/healthz` returns a static dict and never touches SQLite: a locked, corrupt
  or full-disk database reports healthy. Add a `SELECT 1`.
- The dashboard healthcheck fetches `/` (static files), which is up whenever
  nginx is up. If ingest is down or the token mismatches, the dashboard is
  "healthy" while completely non-functional. Point it at `/api/summary`.
- `depends_on: - ingest` has no `condition: service_healthy`.
- nginx resolves `ingest` in `proxy_pass` **once at startup** and caches the IP.
  `docker compose up -d ingest` alone gives ingest a new IP and the dashboard
  proxies to a dead address until it is also restarted. Fix with
  `resolver 127.0.0.11 valid=10s;` and a variable in `proxy_pass`, or always
  recreate both.

### 23. `test_ingest.py` deletes the development database

*Status: **fixed**.*

**Fixed 2026-09-10.** The suite sets `DB_PATH` to a `tempfile.mkdtemp()` scratch database *before* importing `app.db` (which resolves it at import time), removes the directory via `atexit`, and `_reset_db` now asserts it is operating inside that directory before unlinking anything. It also clears the `-wal`/`-shm` sidecars, which were previously left behind.

`_reset_db` unlinks `db_module.DB_PATH`, which defaults to `ingest/usage.db` —
the exact file local development writes to — before every test. Both
`getting-started.md` and `development.md` recommend running it with no warning.
Fix: point the tests at a temp `DB_PATH` and reload `db` (they currently reload
only `main`).

---

## P3 — worth doing

### 24. `/api/sessions` is a hard `LIMIT 100` with no pagination

*Status: **reported**.*

`db.py:278`. The grouped-by-project totals in `SessionsTable` and
`TabletDashboard` are computed from that truncated list, while `SummaryCards`
shows all-time totals from the server. The two disagree with no indication once
you pass 100 sessions.

### 25. Data-quality gaps in the OTLP parser

*Status: **reported**.*

- `otlp.py:79` — `timeUnixNano: "0"` (the *string*) passes the falsy guard and
  becomes `1970-01-01`, pinning `first_seen_at` and hiding the event from every
  windowed chart.
- No numeric validation on `cost_usd`: `{"stringValue":"1,25"}` is stored as TEXT
  in a REAL column and `SUM` silently reads it as `1.0`.
- No `PRAGMA foreign_keys`, so `usage_events.session_id → sessions.session_id` is
  declarative only. `docs/data-model.md` presents it as a reference without
  saying it is unenforced.

### 26. Robustness details in the purge and shutdown paths

*Status: **reported**.*

- `db.py:156` builds an unbounded `IN (?,?,…)` list. This build's SQLite limit is
  250 000 so it will not fire today, but if it ever does, `_purge_loop` swallows
  the error and the purge is permanently dead with no symptom. Chunk it or use a
  subquery.
- `_purge_loop` has a blanket `except Exception` and there is no purge
  equivalent of `/api/backup/status`, so a systematically failing purge is
  invisible (see item 10 — the warning is not even logged at a visible level).
- `main.py:40` cancels lifespan tasks without awaiting them; the process can exit
  while a backup thread is mid-copy.
- No explicit `busy_timeout`. The 5 s default held at 12 concurrent writers, but
  a timeout surfaces as a 500 → exporter retry → duplicate rows (item 6).

### 27. Settings and formatting papercuts

*Status: **reported**.*

- `SettingsPage.tsx:131` — `setPrice` early-returns on `isNaN`, so clearing a
  price field leaves the DOM empty while the old value is still saved. The only
  way to remove an override is the ✕ button.
- `TabletDashboard.tsx:205` — `(totalTokens()/1000).toFixed(0)` prints **"0k"**
  as a 5xl headline for anything under 500 tokens, and rounds 1,500 to "2k".
- `settings.ts:60` (`saveSettings`) and `index.html:11` touch `localStorage`
  unguarded. `loadSettings` is correctly wrapped. With site data blocked, Save
  throws in the click handler and the confirmation never appears.
- `TabletDashboard.tsx:299` — a `createMemo` inside an IIFE inside `<Show>`'s
  children. Safe today only because `createMemo` opens its own tracking scope;
  add one bare signal read above it and the project list is rebuilt on every
  poll. Move it to component scope.

### 28. Touch and kiosk ergonomics on `/tablet`

*Status: **reported**.*

The chart is hover-only (`onMouseEnter`, no pointer or touch handlers);
`ToggleBtn` gives roughly 24 px tap targets on a wall display; and neither
dashboard refetches on `visibilitychange`, so a slept tablet is throttled to
about one update per minute while the countdown bar keeps animating as though it
were live.

### 29. TypeScript `strict` is off

*Status: **verified**.*

`dashboard/tsconfig.app.json` never sets `"strict"` or `strictNullChecks` — only
`noUnusedLocals`, `noUnusedParameters`, `erasableSyntaxOnly` and
`noFallthroughCasesInSwitch`. `docs/development.md` claims strict is on. Turning
it on will surface real null-handling gaps around the resource accessors.

### 30. Backup SSH trust and argument quoting

*Status: **reported**.*

- `backup.py:63` — `StrictHostKeyChecking=no` is a defensible LAN trade, but
  `known_hosts` is written to the container's writable layer and destroyed on
  every recreate, so there is no trust-on-first-use pinning at all, just
  permanent blind acceptance. Mount a pre-populated `known_hosts` read-only and
  use `StrictHostKeyChecking=yes`.
- The `-e "ssh -i {SSH_KEY} …"` value is one argv element that rsync re-splits on
  whitespace, so a key path containing a space breaks silently.
- A `BACKUP_DESTINATION` beginning with `-` would be parsed by rsync as a flag.
  Add `--` before the path arguments. Not attacker-reachable — both values come
  from the operator's environment — but cheap to close.

---

## D. Documentation corrections

These are errors in `docs/` and `README.md` introduced when the documentation was
written, not defects in the application. Grouped separately so they can be fixed
in one pass.

### D1. `README.md` is out of date in four places

*Status: **verified**.*

| Line | Claims | Actually |
| --- | --- | --- |
| 53 | local dev runs uvicorn on `--port 9585` | Vite proxies to `127.0.0.1:8000`; following the README gives a dashboard that cannot reach the API |
| 71 | data persists in the `usage-data` **named volume** | it is a bind mount, `./usage-data:/data`; the `volumes:` block is commented out |
| 113 | "port 8000 has to be reachable from every reporting dev container" | 8000 is the container port; the published host port is 9585 |
| 18, 40 | "three views", three read endpoints | four routes, nine `/api/*` routes |

### D2. `security.md` wrongly says `.claude/settings.json` is committed

*Status: **verified**.*

It is not. `.gitignore:23` covers `.claude/`, `git ls-files` shows nothing
tracked under it, and no token appears anywhere in history. The local file does
contain a real endpoint and token, so "rotate it" stands — but the "is
committed" framing would send a reader hunting through git history for a leak
that never happened. Rewrite as a risk pattern, not an incident.

### D3. A real internal IP is published in a committed file

*Status: **verified**.*

`docs/client-setup.md` uses `10.9.254.218:9585` as its example host. Low impact,
but gratuitous. Replace with `10.0.0.5` or similar.

### D4. `api-reference.md` factual errors

*Status: **verified**.*

- CORS methods listed as `GET, POST, PATCH`; the code allows
  `GET, PATCH, POST, PUT, DELETE`. The two omitted are exactly the ones the
  Users page depends on.
- States the active-session window is "a server-side constant, not configurable
  by environment variable". It is `ACTIVE_WINDOW_MINUTES`. This contradicts
  `configuration.md` and `troubleshooting.md` in the same doc set.
- The protected-routes table omits `PUT`/`DELETE /api/user-projects/{user_id}`.
- "A malformed record raises and aborts the rest of that request" — the whole
  body is parsed before any insert, so a parse failure discards the entire batch.

### D5. Docs describe the dead features as working

*Status: **verified**.*

`configuration.md`, `dashboard.md` (×3) and `troubleshooting.md` all describe
model price overrides recomputing displayed cost, and the budget bar resetting on
`billingCycleDay`. Neither happens (item 1). Fix these together with the code
decision — do not correct the docs to describe a feature you are about to wire up.

### D6. Diagnostics in the docs that do not work

*Status: **verified**.*

- `troubleshooting.md` says `docker compose logs ingest | grep -i purge`. Purge
  successes are `log.info` and never emitted (item 10).
- `deployment.md` says backup successes are logged at INFO. Same cause.
- `deployment.md` explains you must run from `ingest/` because "`schema.sql` is
  resolved relative to the package". Wrong reason — `db.py` resolves it from
  `__file__` and is cwd-independent. You need that cwd so `app.main:app`
  imports.
- `getting-started.md` says the smoke test prints "PASS" lines; it prints `OK`.
- Every `curl` example uses `$INGEST_AUTH_TOKEN` without telling the reader how
  to get it into their shell (`.env` is read by Compose, not sourced). As
  written they send `Bearer ` and get a 401. One `set -a; . ./.env; set +a` note
  fixes all of them.
- `development.md`'s "in short" block runs `cd ingest` then `cd dashboard`; the
  second fails from inside `ingest/`.

### D7. Smaller doc inaccuracies

*Status: **verified**.*

- `development.md` claims TypeScript strict is on (item 29) and that tests must
  reload both `db` and `main` modules; they reload only `main`.
- `dashboard.md` calls the button "Back up now"; it is "Run backup now". It also
  says the button returns 400 when backups are disabled — no button renders at
  all in that state.
- `dashboard.md` describes the tablet stat cards wrongly: cards 2 and 3 swap
  with the metric toggle.
- `dashboard.md` says settings "take effect on save"; they are read once per
  component mount, so other tabs keep old values until reload.
- `data-model.md`'s example one-liners use
  `WHERE occurred_at >= datetime('now','-30 days')` — exactly the lexicographic
  mistake the same file warns about 90 lines earlier.
- `architecture.md` and `docs/README.md` both say "three routes".

### D8. Client setup recommends the wrong default for dev containers

*Status: **verified**.*

`docs/client-setup.md` presents "link the container's user ID once" as the
recommended approach for dev containers, and `docs/dashboard.md` describes the
`/users` page the same way. Per [item 31](#31-user-id-mapping-breaks-on-every-dev-container-rebuild)
that approach does not survive a container rebuild in this deployment.

Both pages need to lead with `OTEL_RESOURCE_ATTRIBUTES` and demote user-ID
linking to a fallback. `docs/architecture.md`'s "Projects are a server-side
label" decision note needs the same revision — it currently presents the mapping
as the considered design, without the rebuild caveat.

Do this together with [roadmap R0](roadmap.md#r0-attribute-projects-with-otel_resource_attributes-instead-of-mapping-user-ids)
rather than before it, so the docs describe one approach rather than three.

---

## Investigated and **not** defects

Recorded so they are not re-litigated.

| Claim | Finding |
| --- | --- |
| Purge orphans billable events | **[narrowed]** Could not reproduce. Requires the purge to land in the microsecond gap between `upsert_session` and `insert_event` for one record; a later event simply recreates the session row. The real, milder version: a session that idles past the window before its first real use has its `first_seen_at` reset on resume. Nothing billable is lost — by construction the purge only touches zero-usage sessions. |
| `set_user_project` overwrites hand-set labels | Intentional and documented — re-saving a link is an explicit instruction. Worth reconsidering as a UX choice, not a bug. |
| Non-constant-time token comparison | Real but not practically exploitable at this token entropy over a LAN. Fix opportunistically (item 18). |
| SQL injection | **None.** `_time_bucket_fmt` returns one of two module-level literals chosen by an already-validated int; `_window_clause` formats a hardcoded column name supplied by code; the purge f-string interpolates only generated `?` placeholders. Every user-controlled value is a bound parameter. |
| Path traversal | **None.** `session_id`/`user_id` are only ever bind parameters and never touch the filesystem. `/api/backup/trigger` takes no parameters. nginx uses `root` + `try_files`, no `alias`. |
| Command injection | **None.** `subprocess.run` takes an argument list, no `shell=True`, and the only interpolated values come from the operator's environment at import time. |
| Token leaking into the browser bundle | **Cannot happen.** `api.ts` uses a relative base with no auth header, there is no `VITE_*` reference, the token is a runtime env var on the serve stage, and `proxy_set_header` overrides anything a client sends. |
| Secrets in image layers | **None.** Both `.dockerignore` files exclude `.git` and `*.db`; `.env` and `.claude/` are outside both build contexts; the dashboard's final stage copies only `dist/`. |
| `.gitignore` completeness | Correct. Worth adding `.env.*`, `*.local`, and SSH key patterns (`id_*`, `*.pem`, `*.key`) given the backup feature invites keys near the tree. |
| JOIN fan-out in `fetch_users` | Correct as written — a user with 2 sessions and 4 events reports the right totals. |
| `_cutoff` lexicographic bound | Sound for the stored `isoformat()` shape, including the microsecond-omission case (`'+' < '.'`). |
| Reconciling cost against Anthropic's billed figures | Ruled out for this deployment — the Admin API needs an admin key that will not be obtained. `cost_usd` from the client is the only cost figure available; treat it as an estimate. See [Roadmap → Out of scope](roadmap.md#out-of-scope). |
