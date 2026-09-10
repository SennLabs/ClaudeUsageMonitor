# Roadmap — ideas and enhancements

Proposed work, none of it started. Separate from
[Known issues](known-issues.md), which tracks defects in what already exists.

Each item states the problem first. If the problem doesn't bite you, skip the
item — this is a list of options, not a plan of record.

---

## Checklist


**High value**

- [x] [R0. Attribute projects with `OTEL_RESOURCE_ATTRIBUTES` instead of mapping user IDs](#r0-attribute-projects-with-otel_resource_attributes-instead-of-mapping-user-ids) — *done*
- [ ] [R1. Move settings server-side](#r1-move-settings-server-side)
- [ ] [R2. Retention and daily rollups](#r2-retention-and-daily-rollups)
- [ ] [R3. Surface cache efficiency](#r3-surface-cache-efficiency)
- [x] [R4. Event de-duplication](#r4-event-de-duplication) — *done*

**Telemetry coverage — data Claude Code already sends**

- [x] [R17. Store cost as integer micros](#r17-store-cost-as-integer-micros) — *done*
- [ ] [R18. Promote the attributes already being received](#r18-promote-the-attributes-already-being-received)
- [ ] [R19. Surface errors and refusals](#r19-surface-errors-and-refusals)
- [ ] [R20. Surface tool performance](#r20-surface-tool-performance)
- [ ] [R21. Cost attribution by agent, skill and MCP server](#r21-cost-attribution-by-agent-skill-and-mcp-server)
- [ ] [R22. Per-prompt cost](#r22-per-prompt-cost)
- [ ] [R23. Fleet visibility](#r23-fleet-visibility)
- [ ] [R24. Ingest the metrics stream](#r24-ingest-the-metrics-stream)
- [ ] [R25. Derived productivity metrics](#r25-derived-productivity-metrics)
- [ ] [R26. Security and audit view](#r26-security-and-audit-view)
- [ ] [R27. Document the client-side content flags](#r27-document-the-client-side-content-flags)

**Worth considering**

- [ ] [R5. A `/metrics` endpoint for Prometheus](#r5-a-metrics-endpoint-for-prometheus)
- [ ] [R6. Per-client tokens](#r6-per-client-tokens)
- [ ] [R7. Scheduled digest](#r7-scheduled-digest)
- [ ] [R8. CSV / JSON export](#r8-csv--json-export)
- [ ] [R9. Session lifecycle and duration](#r9-session-lifecycle-and-duration)
- [ ] [R10. Display timezone setting](#r10-display-timezone-setting)

**Project hygiene**

- [ ] [R11. Continuous integration](#r11-continuous-integration)
- [ ] [R12. Pin dependencies and base images](#r12-pin-dependencies-and-base-images)
- [ ] [R13. Turn on TypeScript strict mode, and add frontend tests](#r13-turn-on-typescript-strict-mode-and-add-frontend-tests)
- [ ] [R14. Adopt pytest, and stop the tests eating the dev database](#r14-adopt-pytest-and-stop-the-tests-eating-the-dev-database)
- [x] [R15. Ship an example client configuration](#r15-ship-an-example-client-configuration) — *done*
- [ ] [R16. Add a LICENSE](#r16-add-a-license)

---

## High value

### R0. Attribute projects with `OTEL_RESOURCE_ATTRIBUTES` instead of mapping user IDs

*Status: **done**.*

**Done 2026-09-10.** Parsed in `otlp.py`, applied in `db.write_events` with an explicit precedence recorded in `sessions.project_source`. The `/users` page is demoted to a fallback with a banner. Rolling the setting out to each container is the remaining work — see [R15](#r15-ship-an-example-client-configuration).

**Problem.** The `/users` page maps `user.id` to a project. Claude Code documents
`user.id` as a *"random anonymous installation ID from `~/.claude.json`"*, and in
this deployment the dev container's home directory is recreated with the
container — so every rebuild produces a new `user.id` and silently orphans the
mapping. That makes the mapping feature unreliable by construction here; see
[known issue 31](known-issues.md#31-user-id-mapping-breaks-on-every-dev-container-rebuild).

**Shape.** Claude Code supports arbitrary custom resource attributes that ride on
every metric and event. One line per container:

```
OTEL_RESOURCE_ATTRIBUTES=project=radiology-pacs,team.id=platform,cost_center=eng-123
```

Parse `project` (and any other agreed keys) in `otlp.py`, store it on the event,
and prefer it over the session's mapped label. Format is strict: comma-separated
`key=value`, no spaces, US-ASCII, percent-encode anything unusual — a malformed
value silently produces no attribute, so validate on ingest and surface unparsed
containers somewhere.

**Why this is the right layer.** The project is a property of the container, not
of an identity the platform regenerates. It survives rebuilds, needs no database
table, needs no retroactive relabelling, and arrives correct on the very first
event of a brand-new container.

**Keep the `/users` page** as the fallback for containers you cannot reconfigure,
and as the way to fix historical data — but it stops being the documented default.

**Effort.** Small on the server. The real work is rolling the setting out to every
container, which is also [R15](#r15-ship-an-example-client-configuration).

### R1. Move settings server-side

**Problem.** Every preference lives in one browser's `localStorage` under
`claudeMonitorSettings`. The wall tablet and your laptop hold different values,
clearing site data resets everything, and the server cannot act on any of it.
This is also the root of [known issue 12](known-issues.md#12-three-different-active-session-windows)
— three separate "active window" values that only agree at their defaults.

**Shape.** A `settings` table (single row, or key/value), `GET`/`PUT
/api/settings`, and the existing `loadSettings()` becomes a resource like any
other. Keep `localStorage` for genuinely per-device preferences — theme is the
obvious one, since a wall display and a laptop reasonably differ.

**Unlocks.** One active window everywhere. Budget and alert thresholds the
server can evaluate, which is a prerequisite for R7. Price overrides that apply
consistently rather than per-browser. Configure the wall tablet from your desk.

**Effort.** Moderate. The migration path matters: read `localStorage` once on
first load and seed the server from it, so nobody loses their configuration.

### R2. Retention and daily rollups

**Problem.** Nothing ever deletes a `usage_events` row, and `raw_attributes`
(the full JSON attribute map of every record) is the bulk of each row's size.
Every 5-second poll from every open dashboard re-aggregates the whole table, and
the all-time view added recently scans all of it. This gets slower forever.

**Shape.** A `usage_daily` rollup table (`day, project_name, model, tokens,
cost`) written by a nightly job; time-series endpoints read rollups for anything
older than a few days and raw events for the recent window. Plus a retention
sweep that drops or truncates `raw_attributes` on events older than N days —
that alone reclaims most of the space while keeping the numbers.

**Effort.** Moderate. Pairs naturally with the existing `_purge_loop`.

### R3. Surface cache efficiency

**Problem.** `cache_read_tokens` and `cache_creation_tokens` are captured on
every event and almost entirely unused: one summary card shows cache reads, and
`total_cache_creation_tokens` is returned by the API, typed in `api.ts`, and
rendered nowhere.

**Why it matters more than another cost chart.** Cache hit ratio is one of the
few things a Claude Code user can actually *change* to move their bill. A
per-project and per-session cache-efficiency panel turns this from a monitoring
tool into a tool that tells you what to do.

**Shape.** Cache read ÷ (cache read + input) as a ratio, trended over time and
broken down by project. Flag projects whose ratio is falling.

**Effort.** Small — the data is already in the database.

### R4. Event de-duplication

*Status: **done**.*

**Done 2026-09-10.** Implemented with the batch-atomicity fix, as this item anticipated. `event_hash` + a unique index + `INSERT OR IGNORE`; pre-existing duplicates are cleared deliberately with `ingest/dedupe.py`.

**Problem.** No idempotency key on `usage_events`, and OTLP exporters retry on
5xx by design. Every transient failure permanently inflates cost and token
totals — see [known issue 6](known-issues.md#6-batches-are-not-atomic-so-failures-permanently-inflate-totals).

**Shape.** A unique index over a natural key (`session_id`, `occurred_at`,
`event_name`, `model`, token counts) or a hash column, with `INSERT OR IGNORE`.
Should be done alongside the batch-atomicity fix rather than separately.

**Effort.** Small, but needs a plan for existing duplicates already in the data.

---

## Telemetry coverage — data Claude Code already sends

Everything in this section comes from a review of Claude Code's OpenTelemetry
reference against what this tool actually consumes. The tool currently enables
`OTEL_LOGS_EXPORTER` only and promotes nine attributes to columns; the rest of
each record is already stored verbatim in `usage_events.raw_attributes`.

**That matters for sequencing:** items R17–R23 need no client-side change and no
backfill from clients — the data is already in the database and can be recovered
with `json_extract`. Items R24–R27 need a config change or a new endpoint.

### R17. Store cost as integer micros

*Status: **done**.*

**Done 2026-09-10.** `cost_usd_micros` is read from the event when the client sends it and derived from `cost_usd` otherwise, and is backfilled for existing rows on startup. The read queries still aggregate `cost_usd`; switching them over is part of [R18](#r18-promote-the-attributes-already-being-received).

Every `claude_code.api_request` event carries `cost_usd_micros` (cost in
millionths, as an integer) alongside the decimal `cost_usd`. The `cost_usd`
column is `REAL`, so every `SUM` across a growing table accumulates floating
point error — and those sums are the headline figures on every view.

Add a `cost_usd_micros INTEGER` column, aggregate on it, and divide only at
display time. Backfill from `raw_attributes`.

### R18. Promote the attributes already being received

Four attributes on `claude_code.api_request` are stored but unusable:

| Attribute | Unlocks |
| --- | --- |
| `duration_ms` | API latency — there is no p50/p95 anywhere today |
| `query_source` | `main` / `subagent` / `auxiliary` — how much of the bill is subagents, usually the surprise |
| `effort` | `low`…`max` — cost by effort level |
| `speed` | `fast` / `normal` — fast mode is priced well above standard, so this is a real line item |

Add columns, add a guarded `ALTER TABLE`, backfill with `json_extract`. Follows
the procedure in [Data model](data-model.md#otlp-attribute-mapping).

### R19. Surface errors and refusals

`claude_code.api_error` (`status_code`, `attempt`, `error`, `duration_ms`) and
`claude_code.api_refusal` (`category`, `server_fallback_hop`, `attempt`) are
already being ingested and rendered nowhere.

A view over them gives error rate, rate-limit (429) frequency, retry counts, and
refusal categories. Retry counts matter twice over: `attempt > 1` is also the
signal for the duplicate-event problem in
[known issue 6](known-issues.md#6-batches-are-not-atomic-so-failures-permanently-inflate-totals).

### R20. Surface tool performance

`claude_code.tool_result` carries `tool_name`, `success`, `duration_ms`,
`error_type`, `tool_use_id`, and input/result sizes. That answers "which tools
are slow" and "which tools fail, and how" — neither of which is visible today,
despite the data being present.

### R21. Cost attribution by agent, skill and MCP server

`claude_code.api_request` carries `agent.name`, `skill.name`, `plugin.name`,
`marketplace.name`, `mcp_server.name` and `mcp_tool.name`. These are a genuinely
new attribution dimension — cost per subagent, per skill, per MCP server —
orthogonal to the per-project and per-model breakdowns that already exist.

Note the documented redaction: user-defined agents report as `custom` and
third-party skills/plugins as `third-party` unless `OTEL_LOG_TOOL_DETAILS=1` is
set on the client. Plan for those buckets rather than treating them as one agent.

### R22. Per-prompt cost

Every event from a single user prompt shares a `prompt.id`. Grouping on it makes
"what did that one question cost" answerable, which is the question people
actually ask when a bill looks wrong. Cheap to add once R18 lands.

### R23. Fleet visibility

`app.version` and `terminal.type` are on every event (`app.version` requires
`OTEL_METRICS_INCLUDE_VERSION=true` for metrics, but is present on events).
A small table of "which Claude Code versions are running where" catches
containers stuck on an old build — relevant because attribute naming has changed
across versions, which is why `_ATTR_ALIASES` carries aliases at all.

### R24. Ingest the metrics stream

Claude Code emits eight pre-aggregated metrics that this tool receives none of,
because only `OTEL_LOGS_EXPORTER` is enabled:

| Metric | Attributes | Why |
| --- | --- | --- |
| `claude_code.lines_of_code.count` | `type` added/removed, `model` | Output volume |
| `claude_code.commit.count` | standard | Cost per commit |
| `claude_code.pull_request.count` | standard | Cost per PR |
| `claude_code.active_time.total` | `type` user/cli | **Cost per active hour** — a far better number than raw spend |
| `claude_code.code_edit_tool.decision` | `tool_name`, `decision`, `source`, `language` | Edit acceptance rate by language |
| `claude_code.session.count` | `start_type` fresh/resume/continue | Session funnel |
| `claude_code.cost.usage`, `claude_code.token.usage` | as the event attributes | Cross-check against the event stream |

**Trap.** The client currently sets the *generic* `OTEL_EXPORTER_OTLP_ENDPOINT`,
which applies to every signal. Turning on `OTEL_METRICS_EXPORTER=otlp` without
implementing `POST /v1/metrics` means Claude Code posts metrics at this service
and gets a 404 on every export interval. Either implement the endpoint or split
the destinations with `OTEL_EXPORTER_OTLP_LOGS_ENDPOINT` and
`OTEL_EXPORTER_OTLP_METRICS_ENDPOINT`.

Metrics default to a 60 s export interval and `delta` temporality — both differ
from the logs stream, so the ingest path is not a copy of `/v1/logs`.

### R25. Derived productivity metrics

Once R24 lands, the numbers worth putting on the dashboard are ratios, not
totals: cost per commit, cost per PR, cost per active hour, dollars per thousand
lines, and edit acceptance rate by language. Raw spend says how much was spent;
these say whether it bought anything, and they are what someone outside the team
will ask for.

### R26. Security and audit view

Three event types are already arriving and have nothing to do with cost:

- `claude_code.permission_mode_changed` — records transitions between
  `default`, `plan`, `acceptEdits`, `auto` and `bypassPermissions`, with the
  `trigger`. **Alerting when a container enters `bypassPermissions` is probably
  worth more than another cost chart**, given this monitors a fleet centrally.
- `claude_code.auth` — `login`/`logout`, `success`, `error_category`.
- `claude_code.mcp_server_connection` — `status`, `transport_type`,
  `server_scope`, `duration_ms`, `error_code`; MCP server reliability.

### R27. Document the client-side content flags

Claude Code has opt-in flags that put content into the telemetry stream:
`OTEL_LOG_USER_PROMPTS`, `OTEL_LOG_ASSISTANT_RESPONSES`, `OTEL_LOG_TOOL_DETAILS`,
`OTEL_LOG_TOOL_CONTENT`, and `OTEL_LOG_RAW_API_BODIES` (which exports **full API
request and response JSON**).

Because unrecognised attributes are stored verbatim, anything a client enables
lands in `raw_attributes`, in the SQLite file, and in every backup snapshot —
none of which is encrypted at rest. This is a policy decision made on the client
side that this service silently inherits. It belongs in
[Security](security.md) as an explicit statement of what the database may
contain, not as something discovered later.

## Worth considering

### R5. A `/metrics` endpoint for Prometheus

**Problem.** Every feature request for this tool — alerting, longer retention,
richer dashboards, historical comparison — is something Prometheus and Grafana
already do well.

**Shape.** Expose counters and gauges (cost and tokens by project/model/user,
active sessions, ingest rate, backup age and status) in Prometheus text format.
Roughly 30 lines with no dependency.

**Why.** If you already run that stack anywhere, this is likely more value than
building more of this UI, and it makes the dashboard a convenience rather than
the only way to see the data.

**Distinct from [R24](#r24-ingest-the-metrics-stream).** This item exposes *this
tool's* aggregates to Prometheus. R24 is about consuming Claude Code's own
metrics stream. Claude Code can also skip both and export straight to Prometheus
via `OTEL_METRICS_EXPORTER=prometheus` (scraped on `:9464`). Since endpoints are
configurable per signal, the pragmatic split is **logs to this tool** — session
and project attribution, the tablet view, the things Grafana is bad at — and
**metrics to Prometheus**, for alerting and long retention. That is a client
config change, not a rewrite.

### R6. Per-client tokens

**Problem.** One shared secret is simultaneously the write credential for every
dev container and the read credential for all data. You cannot revoke one
container without rotating everywhere, and any container that can report usage
can also read everyone else's and trigger backups.

**Shape.** A `clients` table mapping token → name → project. Ingest identifies
the client from its token, which also makes the user→project mapping step
unnecessary for new containers — the token already says which project it is.
Keep a separate read-only token for the dashboard.

**Effort.** Moderate, and it touches the client setup instructions for every
container. Worth it if this ever serves more than a handful of people.

**Reduced scope since [R0](#r0-attribute-projects-with-otel_resource_attributes-instead-of-mapping-user-ids).**
Resource attributes already solve the *attribution* half of this. What remains is
the security half — revoking one container without rotating everywhere, and
separating read from write credentials. Judge it on that alone.

### R7. Scheduled digest

**Problem.** The dashboard only tells you anything while someone is looking at
it. Budget overruns and cost spikes are discovered by chance.

**Shape.** A weekly or monthly summary — spend by project, change against the
previous period, top models, cache efficiency — delivered to email, Slack, or
just rendered at `/report` for the period. A threshold breach could push
immediately rather than waiting for the digest.

**Depends on** R1 for server-evaluable thresholds.

### R8. CSV / JSON export

**Problem.** The first time someone asks you to justify the spend, you will be
copying numbers out of a web page.

**Shape.** `GET /api/export?from=&to=&format=csv`, respecting the same project
and window filters as the dashboard. Small, self-contained, and useful the day
it lands.

### R9. Session lifecycle and duration

**Problem.** `sessions.end_reason` exists in the schema and is never written.
There is no notion of how long a session ran, only when it was first and last
seen — and the empty-session purge can reset `first_seen_at` on a session that
idled before its first real use.

**Shape.** Capture session start/end events properly, derive duration, and show
cost per hour of actual use. That is a more meaningful number than raw spend for
comparing projects.

**Partly superseded by [R24](#r24-ingest-the-metrics-stream):**
`claude_code.active_time.total` gives actual active time directly, and
`claude_code.session.count` carries `start_type` (fresh/resume/continue). Prefer
those over deriving duration from first/last-seen timestamps, which the
empty-session purge can distort anyway.

### R10. Display timezone setting

**Problem.** Everything is stored and bucketed in UTC. Daily buckets are UTC
days, so in UTC+8 a "day" on the chart runs 08:00 to 08:00 local. The axis
labels are correct but the day boundaries will not match anyone's intuition.

**Shape.** A display-timezone setting (server-side, per R1) applied at bucketing
time rather than in the browser, so the buckets themselves align to local days.
Note this makes the bucket strings timezone-dependent, which the chart's label
logic currently assumes they are not.

---

## Project hygiene

### R11. Continuous integration

There is no CI. A GitHub Actions job running `test_ingest.py` and
`tsc -b && vite build` on every push would have caught several items in
[Known issues](known-issues.md) before they landed.

**Deferred by decision (2026-09-10).** No GitHub Actions for now. Until this
lands, both commands are run by hand before any change set is considered done —
that is the only guard against regressions, so it is not optional.

### R12. Pin dependencies and base images

`fastapi>=0.115` and `uvicorn[standard]>=0.30` are unpinned floors, and no base
image is digest-pinned. Two builds a month apart produce different software with
no record of what changed. Pin exact versions (ideally with hashes) and pin base
images by digest. See [known issue 21](known-issues.md#21-container-and-deployment-hardening).

### R13. Turn on TypeScript strict mode, and add frontend tests

`strict` is off ([known issue 29](known-issues.md#29-typescript-strict-is-off)).
Turning it on will surface real null-handling gaps, particularly around the
resource accessors that cause
[known issue 2](known-issues.md#2-a-failed-api-call-renders-no-error-at-all).
There are no frontend tests at all; Vitest plus a handful of tests over
`UsageChart`'s bucket maths, `computeHourlyRate`, and the label formatters would
cover the parts most likely to break silently.

### R14. Adopt pytest, and stop the tests eating the dev database

`test_ingest.py` is a hand-rolled runner with a `main()`. Moving to pytest costs
little and makes CI reporting standard. Do it together with
[known issue 23](known-issues.md#23-test_ingestpy-deletes-the-development-database)
so the tests stop deleting `ingest/usage.db`.

### R15. Ship an example client configuration

*Status: **done**.*

**Done 2026-09-10.** [`examples/claude-settings.json`](../examples/claude-settings.json) plus a README covering placement, the `OTEL_RESOURCE_ATTRIBUTES` format rules, and how to check it worked.

Onboarding a new dev container means copying a JSON block out of the README and
editing three values. An `examples/claude-settings.json` with placeholders, plus
a one-liner that fills in the host and token, would make it copy-paste. Keep the
warning about never committing a real token to a project-level file.

### R16. Add a LICENSE

There isn't one. Even for an internal tool it settles the question of what
colleagues may do with it.

---

## Out of scope

Recorded so they are not re-proposed.

| Idea | Decision |
| --- | --- |
| Reconcile against Anthropic's authoritative billed cost via the Admin API (`GET /v1/organizations/usage_report/messages`, `GET /v1/organizations/cost_report`) | **Declined.** Requires an Admin API key (`sk-ant-admin…`), which will not be obtained. `cost_usd` from the client stays the only cost figure available, so treat it as an estimate — see the caveat in [Troubleshooting](troubleshooting.md#costs-dont-match-anthropic-billing). |
| Anthropic-hosted telemetry ingestion or an enterprise usage dashboard | **Does not exist.** Claude Code's telemetry documentation describes no hosted collector — running the OTLP backend is the customer's responsibility. Self-hosting, as this tool does, is the supported path. |
