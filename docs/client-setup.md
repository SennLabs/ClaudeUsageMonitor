# Client setup — reporting usage from a dev container

The sending side needs no software installed. Claude Code ships with
OpenTelemetry export; you enable it and point it at this service.

## Minimum configuration

Add to a Claude Code `settings.json`:

```json
{
  "env": {
    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
    "OTEL_LOGS_EXPORTER": "otlp",
    "OTEL_EXPORTER_OTLP_PROTOCOL": "http/json",
    "OTEL_EXPORTER_OTLP_ENDPOINT": "http://<host-running-ingest>:9585",
    "OTEL_EXPORTER_OTLP_HEADERS": "Authorization=Bearer <INGEST_AUTH_TOKEN value>",
    "OTEL_LOGS_EXPORT_INTERVAL": "5000"
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
| Dev container on the same Docker host as ingest | The host's LAN IP, e.g. `http://10.9.254.218:9585`, or `http://host.docker.internal:9585` |
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

Claude Code does not report a project name, so sessions arrive untagged. There
are two ways to fix that, and for dev containers the first is the one you want.

**Link the container's user ID once (recommended).** Every session from a given
container reports the same `user.id`. Open `/users`, click the Project cell next
to that ID, and give it a name. Every session that container has already opened
is relabelled, and every future one arrives already tagged — no per-session
work, ever. Details in the [Dashboard guide](dashboard.md#users--linking-containers-to-projects).

**Tag one session by hand.** On the `/` view's Sessions table, click a session's
Project cell, type a name, press Enter. That is a
`PATCH /api/sessions/{session_id}` writing to `sessions.project_name`, and it
wins over the user link for that one session.

Either way, the **By project** toggle on the chart and the sessions table then
groups everything under that label; anything still untagged collects under
`(untagged)`.

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
