# Example client configuration

[`claude-settings.json`](claude-settings.json) is the Claude Code configuration
a dev container needs to report usage here. Copy its `env` block into the
container's own settings file and replace the three placeholders.

## The line that matters most

```json
"OTEL_RESOURCE_ATTRIBUTES": "project=<PROJECT_NAME>"
```

Claude Code attaches custom resource attributes to every event it exports, so
this makes the container declare which project it belongs to. Usage arrives
already labelled — nothing to map, nothing to maintain, and it is correct on
the very first event of a brand-new container.

The alternative is linking the container's `user.id` on the dashboard's
`/users` page, which still works but is a fallback. Claude Code generates
`user.id` per *installation*, storing it in `~/.claude.json`; if the container's
home directory does not persist across rebuilds — the usual case — every rebuild
produces a new identity and orphans the mapping.

Format rules for `OTEL_RESOURCE_ATTRIBUTES`, which are strict and fail quietly:

- comma-separated `key=value`, **no spaces anywhere**
- US-ASCII only; percent-encode anything else (`My Team` → `My%20Team`)
- more keys are fine and are stored with the event:
  `project=billing,team.id=platform,cost_center=eng-123`

## Where to put it

| File | Scope | Use when |
| --- | --- | --- |
| `~/.claude/settings.json` | every project in this container | the usual choice |
| `<repo>/.claude/settings.local.json` | this project, gitignored | project-scoped without committing a token |
| `<repo>/.claude/settings.json` | this project, **committed** | shared config only — never a real token |

## Checking it worked

```bash
# from inside the container
curl -v http://<INGEST_HOST>:9585/healthz
```

Then start a fresh Claude Code session, ask it anything, and the session should
appear on the dashboard within about ten seconds — already tagged with its
project. Full walkthrough in [docs/client-setup.md](../docs/client-setup.md).
