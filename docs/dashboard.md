# Dashboard guide

A SolidJS single-page app with three routes, served by nginx in production and
by Vite in development.

| Route | Component | For |
| --- | --- | --- |
| `/` | [`App.tsx`](../dashboard/src/App.tsx) | Day-to-day desktop use |
| `/tablet` | [`TabletDashboard.tsx`](../dashboard/src/components/TabletDashboard.tsx) | A wall display or tablet you glance at |
| `/users` | [`UsersPage.tsx`](../dashboard/src/components/UsersPage.tsx) | Linking dev-container user IDs to projects |
| `/settings` | [`SettingsPage.tsx`](../dashboard/src/components/SettingsPage.tsx) | Preferences, model prices, backup controls |

Every view polls the API on `refreshIntervalMs` (default 5s) and re-renders in
place. There is no manual refresh button because there is no need for one.

## `/` — main dashboard

### Summary cards

Six all-time figures from `/api/summary`: active sessions, total sessions, input
tokens, output tokens, cache read tokens, total cost.

"Active" here means seen in the last 15 minutes, computed **server-side**. The
`activeSessionWindowMin` setting does not change this card — see
[Configuration](configuration.md#dashboard-preferences-client-side).

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

### Error toast

If any of the resource fetches fail, a red toast appears bottom-right: *"Couldn't
reach the usage API — is the backend running?"* Most often that is a stopped
ingest container or a token mismatch — see
[Troubleshooting](troubleshooting.md).

## `/tablet` — glanceable view

Built for a screen across the room: large type, dark background, minimal
chrome. Same data, different priorities.

- **Stat cards** — big-number active sessions, total cost, token totals.
- **Refresh countdown** — seconds until the next poll, so a stale-looking screen
  is visibly still alive.
- **Budget bar** — only when `monthlyBudget` is set. Shows spend against budget
  for the current cycle, with the bar changing color as the fraction climbs.
  `billingCycleDay` decides when it resets.
- **Active session list** — per-session state with a relative "time ago", using
  the client-side `activeSessionWindowMin` to mark each one active or idle.
- **Project rollup** — cost, active count, and session count per project.
- Same window/metric/grouping toggles as the main view.

Point a kiosk browser at `http://<host>:9595/tablet`. Settings are per-browser,
so configure the budget and thresholds *on that device*.

## `/users` — linking containers to projects

Almost all usage tends to come from dev containers, and each reports a stable
`user.id`. Tagging sessions one at a time is busywork when the container is
always the same project — this page links the ID once instead.

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

A project set by hand on one session still wins day to day: later events from
that user never overwrite a label that is already there. Re-saving the link on
this page does relabel everything, since that is an explicit instruction.

Polling pauses while a field is open, so a refresh cannot yank the input out
from under you mid-edit.

Once containers are linked, the **By project** toggles on the main and tablet
views become genuinely useful — every series is a real project rather than one
large `(untagged)` bucket.

## `/settings` — preferences and backups

All preferences except the backup controls are stored in that browser's
`localStorage` (key `claudeMonitorSettings`) and take effect on save.

**Budget** — monthly budget amount and the day of month it resets (1–28; capped
at 28 so every month has that day).

**Charts** — default time window (including **All time**) and default metric for
a fresh page load.

**Alerts** — hourly spend threshold that triggers the amber banner. Blank
disables it.

**Refresh** — poll interval, and the active-session window used by the tablet
view.

**Model prices** — per-model overrides in dollars per million input and output
tokens. Add a model by its exact reported name (as shown in the Usage by model
table), and the dashboard recomputes that model's cost from raw token counts
instead of using the reported `cost_usd`. Models without an override are
untouched. This affects display only; stored data is never rewritten.

**Backup** — the one server-side section. It reads `/api/backup/status` and
shows destination, method (`copy` or `rsync-ssh`), schedule, next run, last run,
and last result. The **Back up now** button calls `POST /api/backup/trigger` and
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

- **Settings are per browser.** They don't sync between your laptop and the wall
  tablet, and clearing site data resets them.
- **The sessions table is capped at 100 rows** server-side, ordered by recency.
  Older sessions still count toward totals but drop off the table.
- **Cost figures are what the client reported**, unless you have set a price
  override for that model.
- **`/api/summary` totals are all-time** and ignore the chart's time window.
- **Sessions that start but never get used are deleted** once they go quiet —
  see [Empty-session cleanup](data-model.md#empty-session-cleanup). Nothing with
  recorded usage is ever removed.
