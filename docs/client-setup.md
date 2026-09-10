# Client setup — reporting usage from a dev container

The sending side needs no software installed. Claude Code ships with
OpenTelemetry export; you enable it and point it at this service.

## Minimum configuration

Add to a Claude Code `settings.json` — or copy
[`examples/claude-settings.json`](../examples/claude-settings.json) and fill in
the three placeholders:

```json
{
  "env": {
    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
    "OTEL_LOGS_EXPORTER": "otlp",
    "OTEL_EXPORTER_OTLP_PROTOCOL": "http/json",
    "OTEL_EXPORTER_OTLP_ENDPOINT": "http://<host-running-ingest>:9585",
    "OTEL_EXPORTER_OTLP_HEADERS": "Authorization=Bearer <INGEST_AUTH_TOKEN value>",
    "OTEL_LOGS_EXPORT_INTERVAL": "5000",
    "OTEL_RESOURCE_ATTRIBUTES": "project=<project name>"
  }
}
```

| Variable | Why it's here |
| --- | --- |
| `CLAUDE_CODE_ENABLE_TELEMETRY` | Master switch. Nothing is exported without it. |
| `OTEL_LOGS_EXPORTER` | `otlp` — usage events are emitted as OTLP *logs*, not metrics or traces. |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `http/json` — the only encoding `/v1/logs` parses. `grpc` or `http/protobuf` will not work here. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Base URL. The exporter appends `/v1/logs` itself — do **not** include it. |
| `OTEL_EXPORTER_OTLP_HEADERS` | Supplies the bearer token. Omit the whole line if the server has auth disabled. |
| `OTEL_LOGS_EXPORT_INTERVAL` | Batch flush interval in ms. 5000 keeps the dashboard feeling live; raise it to reduce request volume. |
| `OTEL_RESOURCE_ATTRIBUTES` | Declares which project this container belongs to. Rides on every event, so usage arrives already labelled. |

## Where to put the file

| Location | Scope | Use when |
| --- | --- | --- |
| `~/.claude/settings.json` | Every project and session for that user, inside that container | The usual choice — the goal is "everything in this container reports" |
| `<repo>/.claude/settings.json` | Only that project, and shared with everyone who clones it | You want a specific repo's usage tracked wherever it's worked on |
| `<repo>/.claude/settings.local.json` | Only that project, only you (gitignored) | You want project-scoped reporting without committing your endpoint or token |

If more than one applies, project settings take precedence over user settings.

**Do not commit a real token to a shared repo.** The project-level file is
checked in and visible to anyone with repo access. Use the user-level or
`.local` file for anything with a credential in it. (This repository's own
`.claude/settings.json` is an example of the pattern, not a template to copy
verbatim — it contains a live endpoint and token.)

## Choosing the endpoint host

`<host-running-ingest>` must be resolvable **from inside the dev container**,
which is often not `localhost`:

| Situation | Use |
| --- | --- |
| Claude Code and ingest on the same machine, no container | `http://localhost:9585` |
| Dev container on the same Docker host as ingest | The host's LAN IP, e.g. `http://10.0.0.5:9585`, or `http://host.docker.internal:9585` |
| Dev container elsewhere on the network | The ingest host's LAN IP or internal DNS name |
| Both in the same compose project | `http://ingest:8000` (service name, internal port) |

Check reachability from inside the container before debugging anything else:

```bash
curl -v http://<host-running-ingest>:9585/healthz
```

## Applying the change

Settings are read once, at session start. After editing `settings.json`, start a
new Claude Code session — or restart the current one — before expecting data.

## Multiple concurrent sessions

No extra configuration. Every Claude Code session generates its own
`session.id`, which is exactly what the dashboard groups by. Ten sessions in one
container appear as ten rows.

## Labelling sessions with a project

Claude Code does not report a project name of its own, so you have to say which
project a container works on. There are three ways, and the first is the one you
want for a dev container.

### 1. Declare it on the container (recommended)

```json
"OTEL_RESOURCE_ATTRIBUTES": "project=radiology-pacs"
```

Claude Code attaches custom resource attributes to every event it exports, so
this arrives with the usage itself. Nothing to map, nothing to maintain, correct
on the container's very first event, and it survives container rebuilds.

You can add more keys — they are all stored with the event and are available for
later reporting:

```json
"OTEL_RESOURCE_ATTRIBUTES": "project=radiology-pacs,team.id=platform,cost_center=eng-123"
```

The format is strict and fails quietly if you get it wrong: comma-separated
`key=value`, **no spaces anywhere**, US-ASCII only, percent-encode anything else
(`My Team` → `My%20Team`).

### 2. Link a user ID (fallback)

For containers you cannot reconfigure, and for fixing historical data. On
`/users`, click the Project cell next to a `user.id` and name it; every session
that ID owns, past and future, is tagged.

**Know the limitation before relying on this.** Claude Code generates `user.id`
per *installation* and stores it in `~/.claude.json`. If the container's home
directory does not persist across rebuilds — the usual case for a dev container
— every rebuild produces a new ID, the mapping is orphaned, and the rebuilt
container arrives untagged with nothing in the UI to indicate why.

### 3. Tag one session by hand

On the `/` view's Sessions table, click a session's Project cell, type a name,
press Enter. That is a `PATCH /api/sessions/{session_id}`.

### Precedence

Recorded per session in `sessions.project_source`, so it is inspectable rather
than implied:

| Source | Wins over | Behaviour |
| --- | --- | --- |
| `manual` | everything | Never overridden by later events |
| `resource` | `user_map` | Re-applied on every event, so fixing a container's config fixes its in-flight sessions too |
| `user_map` | nothing | Only ever fills a gap |

Whichever you use, the **By project** toggle on the chart and the sessions table
groups by that label; anything still untagged collects under `(untagged)`.

## Sessions that start but are never used

A container that comes up and reports telemetry without anyone actually using
Claude Code creates a session with no token usage. Those show while the session
is live, then get cleaned up automatically once it goes quiet — see
[Empty-session cleanup](data-model.md#empty-session-cleanup). Nothing with
recorded usage is ever removed.

## Verifying it works

1. Start a fresh Claude Code session in the configured container.
2. Ask it anything — one prompt is enough to produce an API request event.
3. Within ~5 seconds plus one dashboard poll, a new session row appears.

If it doesn't, work through [Troubleshooting → No data appears](troubleshooting.md#no-data-appears-on-the-dashboard).

## What gets sent

Token counts, cost, model name, session/user/organization ids, and timestamps —
the attributes listed in the [Data model](data-model.md#otlp-attribute-mapping).

Note that this service stores the **complete** attribute map of every log record
it receives in `usage_events.raw_attributes`, not just the columns it
understands. So whatever Claude Code's exporter attaches to an event ends up in
the database. Claude Code does not include prompt or response text by default,
but it has opt-in settings that add prompt content to telemetry — if you enable
anything like that on the sending side, that content lands here too. Treat the
database as containing exactly what the clients chose to send.
