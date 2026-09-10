# Dashboard guide

A SolidJS single-page app with four routes, served by nginx in production and
by Vite in development.

| Route | Component | For |
| --- | --- | --- |
| `/` | [`App.tsx`](../dashboard/src/App.tsx) | Day-to-day desktop use |
| `/tablet` | [`TabletDashboard.tsx`](../dashboard/src/components/TabletDashboard.tsx) | A wall display or tablet you glance at |
| `/insights` | [`InsightsPage.tsx`](../dashboard/src/components/InsightsPage.tsx) | Latency, errors, cost attribution, tools, fleet |
| `/users` | [`UsersPage.tsx`](../dashboard/src/components/UsersPage.tsx) | Linking dev-container user IDs to projects |
| `/settings` | [`SettingsPage.tsx`](../dashboard/src/components/SettingsPage.tsx) | Preferences, model prices, backup controls |

Every view polls the API on `refreshIntervalMs` (default 5s) and re-renders in
place. There is no manual refresh button because there is no need for one.

## `/` — main dashboard

### Summary cards

Six all-time figures from `/api/summary`: active sessions, total sessions, input
tokens, output tokens, cache read tokens, total cost.

Below the cards, a line appears if any events arrived with no `session.id` —
they are in these totals but in no per-session view, so the discrepancy is
stated rather than left to be discovered.

"Active" here means seen within `ACTIVE_WINDOW_MINUTES`, computed
**server-side**. The
`activeSessionWindowMin` setting does not change this card — see
[Configuration](configuration.md#dashboard-preferences).

### Usage chart

A hand-rolled SVG chart ([`UsageChart.tsx`](../dashboard/src/components/UsageChart.tsx))
— no charting library — with three toggle groups above it:

| Toggle | Options | Effect |
| --- | --- | --- |
| Time window | `24h` / `7d` / `30d` / `All` | Sets the `hours` query param (24 / 168 / 720 / 0). 24h buckets hourly; everything longer, all-time included, buckets daily. |
| Metric | Cost / Tokens | Switches the Y axis between USD and token count |
| Grouping | All sessions / By project | Switches between `/api/usage-over-time` (one series) and `/api/usage-over-time-by-project` (one series per project) |

In project mode each project gets its own colored line, with `(untagged)`
covering sessions nobody has labelled. Hovering shows a tooltip with the bucket
timestamp and value.

X-axis labels follow the bucket granularity: a clock time (`14:00`, in local
time) for hourly buckets, a date (`10 Sep`, in UTC, since daily buckets *are*
UTC days) for daily ones. The all-time view adds a two-digit year when the data
spans more than one, and stops drawing per-point dots past 60 buckets so a long
history stays legible.

Since empty buckets produce no rows, a quiet period is a gap in the series
rather than a run of zeroes.

**All time** has no look-back at all — it returns every daily bucket in the
database. On a long-running instance that is the slowest of the four windows,
since it scans the whole `usage_events` table on each poll.

### Sessions table

From `/api/sessions` — the 100 most recently active sessions.

In **By session** mode the columns are session id, project, user, models, input,
output, cost, and last seen. The **Project** cell is editable: click it, type,
press Enter (Escape cancels). That fires
`PATCH /api/sessions/{id}` and updates optimistically, so the label appears
immediately and persists whether or not the next poll has landed.

In **By project** mode rows collapse into per-project totals: session count,
how many are currently active, the union of models used, tokens, cost, and the
most recent activity across the group.

### Usage by model

From `/api/usage-by-model`: request count, input and output tokens, and cost per
model, ordered by cost descending. Events that arrived without a model attribute
appear as `unknown`.

### Spend alert banner

If `costAlertThresholdPerHour` is set and the computed hourly rate exceeds it, an
amber banner appears under the header. The rate comes from `computeHourlyRate`
in [`settings.ts`](../dashboard/src/settings.ts): for hourly buckets it sums the
last 60 minutes of points; for daily buckets it takes the newest bucket and
divides by 24 — a rough figure by construction, since a daily bucket that is
two hours old is still being filled.

### Error banner

If any of the five resource fetches fail, a red banner appears bottom-right
reading *"Couldn't reach the usage API"* followed by the actual error text.
Most often that is a stopped ingest container or a token mismatch — see
[Troubleshooting](troubleshooting.md).

The last successfully fetched values stay on screen through a failed refetch
rather than blanking, and an `ErrorBoundary` around each route catches anything
unhandled instead of leaving a white page.

## `/tablet` — glanceable view

Built for a screen across the room: large type, dark background, minimal
chrome. Same data, different priorities.

- **Stat cards** — big-number active sessions, total cost, token totals.
- **Refresh countdown** — seconds until the next poll, so a stale-looking screen
  is visibly still alive.
- **Budget bar** — only when `monthlyBudget` is set. Shows spend for the
  current billing period against the budget, from `GET /api/budget`, with the
  bar changing colour as the fraction climbs. `billingCycleDay` decides when
  the period starts; before 2026-09-10 the bar actually showed all-time spend
  and never reset.
- **Active session list** — per-session state with a relative "time ago", using
  the client-side `activeSessionWindowMin` to mark each one active or idle.
- **Project rollup** — cost, active count, and session count per project.
- Same window/metric/grouping toggles as the main view.

Point a kiosk browser at `http://<host>:9595/tablet`. Settings are per-browser,
so configure the budget and thresholds *on that device*.

## `/insights` — everything the cost view doesn't show

Panels over telemetry Claude Code has always sent, which this service stored in
`raw_attributes` from the start but could not query until those attributes
became columns. Same time-window control as the main chart.

| Panel | Answers |
| --- | --- |
| **API latency** | p50 / p95 / max request duration |
| **Errors and refusals** | Error rate, HTTP status breakdown, refusal categories, and how many requests were retried |
| **Cost by query source** | How much of the bill is `subagent` and `auxiliary` rather than `main` — usually the surprise |
| **Cost by effort and speed** | Spend per effort level, and how much went through fast mode |
| **Cost by agent** | Per-agent spend |
| **Cost by skill and MCP server** | Per-skill and per-MCP-server spend |
| **Tool performance** | Calls, failures and duration per tool |
| **Cache efficiency** | Hit ratio overall and per project — cached input is billed far below uncached, so this is the panel that suggests an action |
| **Most expensive prompts** | What a single user question cost, grouped by `prompt.id` |
| **Audit** | Permission-mode changes (with a banner for `bypassPermissions`), failed logins, MCP connectivity |
| **Fleet** | Which Claude Code versions and terminals are reporting, and when each was last seen |

There is also an **Export CSV** button in the header, which downloads every
event joined to its project — the answer to being asked to justify the spend.

`(none)` in a row means the attribute was absent on those events. Some values
are redacted by Claude Code itself unless the client sets
`OTEL_LOG_TOOL_DETAILS=1`: user-defined agents report as `custom`, third-party
skills and plugins as `third-party`.

## `/users` — linking containers to projects

**A fallback, not the primary route.** The recommended way to attribute a
container's usage is to declare it on the container with
`OTEL_RESOURCE_ATTRIBUTES` — see
[Client setup](client-setup.md#1-declare-it-on-the-container-recommended). This
page exists for containers you cannot reconfigure, and for fixing historical
data.

The reason it is not the default: Claude Code generates `user.id` per
*installation*, in `~/.claude.json`. When a dev container's home directory does
not persist across rebuilds, every rebuild produces a new ID and orphans the
mapping, with nothing in the UI to indicate it happened. The page shows a banner
saying so.

The table lists every user ID that has reported, plus any that have a mapping
but no sessions yet: ID (with organization ID beside it), the linked project,
session count, how many are currently active, input and output tokens, cost, and
how long ago it was last seen.

Click the **Project** cell to link it. The field autocompletes from
`/api/projects`, so existing project names are one keystroke away. Enter saves,
Escape cancels. What happens on save:

- Every session that user **already** owns is relabelled — the confirmation
  banner reports how many.
- Every **future** session from that user is created already labelled, with no
  further action.
- Clearing the field removes the link and untags that user's sessions.

Precedence is recorded per session in `project_source` and shown in the
Sessions table tooltip: a hand-set label always wins, a container-declared one
beats a user mapping, and a mapping only ever fills a gap. Re-saving a link here
relabels that user's sessions **except** those whose container declared its own
project — a resource attribute would win back on the next event anyway.

Polling pauses while a field is open, so a refresh cannot yank the input out
from under you mid-edit.

Once containers are linked, the **By project** toggles on the main and tablet
views become genuinely useful — every series is a real project rather than one
large `(untagged)` bucket.

## `/settings` — preferences and backups

Preferences are stored **on the server** and apply to every viewer. Saving
pushes the new values into the shared settings resource, so an open tablet
picks them up without being touched.

The **Active session window** is shown but not editable — it comes from the
`ACTIVE_WINDOW_MINUTES` environment variable, because it also governs when an
unused session is deleted.

**Budget** — monthly budget amount and the day of month it resets (1–28; capped
at 28 so every month has that day).

**Charts** — default time window (including **All time**) and default metric for
a fresh page load.

**Alerts** — hourly spend threshold that triggers the amber banner. Blank
disables it.

**Refresh** — poll interval, and the active-session window used by the tablet
view.

Model price overrides were **removed** on 2026-09-10. They had never been
wired to anything — the setting saved and no displayed figure changed — and
with no way to reconcile against Anthropic's billed figures, a hand-entered
price would have made the numbers less trustworthy rather than more. `cost_usd`
as reported by Claude Code is the only cost figure shown.

**Backup** — the one server-side section. It reads `/api/backup/status` and
shows destination, method (`copy` or `rsync-ssh`), schedule, next run, last run,
and last result. The **Run backup now** button calls `POST /api/backup/trigger` and
reports success or the error string. With `BACKUP_DESTINATION` unset the section
shows as disabled and the button returns a 400. See
[Backup and restore](backup-and-restore.md).

## Theme

Dark by default. The toggle in the header flips a `dark` class on `<html>` and
saves the choice under the `theme` localStorage key. An inline script in
[`index.html`](../dashboard/index.html) applies it before first paint, so there
is no flash of the wrong theme on load. Tailwind v4's `dark:` variant is
remapped to that class in [`index.css`](../dashboard/src/index.css) rather than
following the OS media query — an always-on wall display shouldn't switch itself
at sunset.

## Notes and limits

- **Settings are server-side** as of 2026-09-10, so every viewer shares them.
  Only the theme is per-device.
- **The sessions table is capped at 100 rows** server-side, ordered by recency.
  Older sessions still count toward totals but drop off the table.
- **Cost figures are what the client reported**, unless you have set a price
  override for that model.
- **`/api/summary` totals are all-time** and ignore the chart's time window.
- **Sessions that start but never get used are deleted** once they go quiet —
  see [Empty-session cleanup](data-model.md#empty-session-cleanup). Nothing with
  recorded usage is ever removed.
