# Security

This is an internal tool with a deliberately simple security model. Knowing
exactly where its edges are matters more than the model being sophisticated.

## The model in one paragraph

One shared bearer token, `INGEST_AUTH_TOKEN`, gates both writes
(`POST /v1/logs`) and reads (`/api/*`). Every reporting dev container holds it.
The dashboard's nginx holds it and attaches it server-side, so browsers never
see it. `GET /healthz` is unauthenticated. If the token is unset, everything is
open.

## What the token protects

| Route | Protected |
| --- | --- |
| `POST /v1/logs` | Yes |
| `GET /api/*` (all reads) | Yes |
| `PATCH /api/sessions/{id}` | Yes |
| `PUT`/`DELETE /api/user-projects/{user_id}` | Yes |
| `POST /api/backup/trigger` | Yes |
| `GET /docs`, `/redoc`, `/openapi.json` | Disabled — return 404 |
| `GET /healthz` | **No** — intentionally, for health probes |

Enforcement is one dependency in [`main.py`](../ingest/app/main.py):

```python
def require_auth(request: Request) -> None:
    if not AUTH_TOKEN:
        return
    if request.headers.get("authorization") != f"Bearer {AUTH_TOKEN}":
        raise HTTPException(status_code=401, detail="Unauthorized")
```

Two properties follow directly from those four lines:

- **An empty token no longer passes silently** (fixed 2026-09-10). The service
  refuses to start unless `INGEST_ALLOW_ANONYMOUS=true` is set as well, and logs
  a prominent warning at startup when running in that mode.
- **The comparison is constant-time**, via `hmac.compare_digest`. This was never
  a practical risk at this token entropy over a LAN, but it costs nothing.

## Known limits — read these before exposing anything

**One token for everyone.** Read access and write access are the same
credential, held by every reporting client. Any container that can report usage
can also read all usage from every other container, and can trigger backups.
There is no per-client identity or revocation short of rotating the token
everywhere.

**Anyone who reaches the dashboard port can read everything.** nginx supplies
the credential, so the dashboard has no login. Access control for reads is
entirely "can you route to port 9595". Put an authenticating reverse proxy in
front if that is not acceptable.

**The ingest port is the exposed surface.** Port 9585 must be reachable from
every reporting dev container, which makes it the most widely-reachable part of
the stack. It should be reachable *only* from those networks.

**CORS has been removed** (2026-09-10). It previously ran `allow_origins=["*"]`,
which was not fine: nginx injects the bearer token server-side, so a page on any
origin could call the dashboard's `/api/*` without holding the token, and the
wildcard let the script read the response. Both consumers are same-origin
through a proxy, so no CORS header is needed at all.

**No TLS.** Both services speak plain HTTP. On the wire, the bearer token is
visible to anyone who can observe the traffic. Fine on a trusted LAN, not fine
across anything else — terminate TLS in front (see
[Deployment](deployment.md#running-behind-a-reverse-proxy)).

**No rate limiting or payload size cap.** Anyone with the token, or anyone at
all if it is unset, can flood `POST /v1/logs` and grow the database without
bound.

**No authenticity check.** Events are trusted as sent. A client can report any
session id, model, or cost it likes. This is a usage *monitor*, not an audit
log — don't bill anyone off it without a second source. (Retried batches *are*
de-duplicated as of 2026-09-10, but that guards against accidental
double-counting, not against a client that lies.)

**Backup host key verification is opt-in.** Set `BACKUP_SSH_KNOWN_HOSTS` to a
mounted `known_hosts` file and the connection is verified. Leave it unset and
`StrictHostKeyChecking=no` applies — worse than trust-on-first-use, because
`known_hosts` would be written to the container's writable layer and destroyed
on every recreate, so there is no pinning at all. `/api/backup/status` carries
a warning while it is unset. What a spoofer gets is the whole usage database;
not the private key, which never leaves the client.

## Handling the token

**Generate a real one:**

```bash
python3 -c "import secrets; print(secrets.token_hex(20))"
```

**Keep it out of git.** `.env` is gitignored; `.env.example` ships with the value
blank. The risky file is a project-level `.claude/settings.json` on the *client*
side — it is checked in and carries the token in
`OTEL_EXPORTER_OTLP_HEADERS`. Prefer `~/.claude/settings.json` (user-level) or
`.claude/settings.local.json` (gitignored) for anything with a credential.

> This repository's `.claude/settings.json` is **not** committed — `.gitignore`
> covers `.claude/`, and no token appears anywhere in history. It does exist
> locally with a real endpoint and token in it, which is worth knowing: the file
> is plaintext on disk and reaches anything that ingests the working tree (a
> workspace backup, a screenshare, a support bundle). Keep client credentials in
> `~/.claude/settings.json` or `.claude/settings.local.json`, and rotate a token
> you have shared.

**Rotating:**

1. Generate a new token, update `.env`.
2. `docker compose up -d --force-recreate` — both services, so nginx re-runs
   `envsubst`.
3. Update `OTEL_EXPORTER_OTLP_HEADERS` in every client's settings.
4. Restart each Claude Code session — settings are read at session start.

There is no overlap window: between steps 2 and 3, clients still using the old
token get `401` and their events are dropped, not queued.

## Hardening checklist

For a trusted private network, the defaults plus a real token are reasonable.
Beyond that:

- [ ] Set `INGEST_AUTH_TOKEN` to a high-entropy random value — always
- [ ] Verify it took effect: an unauthenticated `/api/summary` must return 401
- [ ] Bind the published ports to one interface — `"192.168.1.50:9585:8000"`.
      A `ufw deny` will **not** work: Docker's rules sit ahead of the INPUT chain.
- [ ] Or add rules to the `DOCKER-USER` chain, which is consulted
- [ ] Terminate TLS in front of both if traffic leaves a trusted LAN
- [ ] Put SSO or basic auth in front of the dashboard if read access needs control
- [x] ~~Tighten CORS~~ — removed entirely, 2026-09-10
- [x] ~~Use `hmac.compare_digest`~~ — done, 2026-09-10
- [x] ~~Warn at startup when no token is configured~~ — now refuses to start, 2026-09-10
- [ ] Use a dedicated, restricted SSH key for backups — not your personal one
- [x] ~~Verify the backup host key~~ — set `BACKUP_SSH_KNOWN_HOSTS`; the status
      endpoint warns while it is unset
- [x] ~~Drop capabilities and set resource limits~~ — done 2026-09-10
- [ ] Run ingest as a non-root user (needs a `chown` first — see
      [Deployment](deployment.md#container-hardening))
- [ ] Verify backup destination permissions: snapshots are full copies of the data

## What is stored

Session, user, and organization identifiers, model names, token counts, costs,
timestamps, and the complete attribute map of every record — in
`usage_events.raw_attributes` for the log stream, and
`metric_points.raw_attributes` for the metrics stream.

### The client decides what content arrives here

Claude Code sends **no prompt or response content by default**. It has five
opt-in flags that change that, and because this service stores every attribute
it receives verbatim, whatever a client turns on lands in `raw_attributes`, in
`usage.db`, and in every backup snapshot — **none of which is encrypted at
rest**. This is a policy decision made on the client side that this service
silently inherits, so it is stated here rather than discovered later.

| Client flag | What it puts into the telemetry stream |
| --- | --- |
| `OTEL_LOG_USER_PROMPTS` | The text of what the user typed |
| `OTEL_LOG_ASSISTANT_RESPONSES` | The text of what Claude replied |
| `OTEL_LOG_TOOL_DETAILS` | Real agent, skill, plugin and MCP names, instead of the redacted `custom` / `third-party` buckets |
| `OTEL_LOG_TOOL_CONTENT` | Tool inputs and results — file contents, command output, diffs |
| `OTEL_LOG_RAW_API_BODIES` | **Full API request and response JSON.** The broadest of the five by a wide margin |

None of these are set by [`examples/claude-settings.json`](../examples/claude-settings.json),
and nothing in this repository turns them on. Every dashboard view works
without them; the only one that changes anything visible here is
`OTEL_LOG_TOOL_DETAILS`, which replaces the redacted buckets on the
attribution panel with real names.

Before enabling any of them on a container, decide that this database is an
acceptable place for that content to sit. If one has already been enabled and
should not have been, the data is already stored:
`RAW_ATTRIBUTES_RETENTION_DAYS` (see [Configuration](configuration.md)) nulls
`raw_attributes` on older rows while leaving every aggregate intact, and is the
fastest way to clear it — but it only reaches rows past the cutoff, and it does
not reach backups that have already been taken.

### Checking what a client is actually sending

```bash
# Any attribute beyond the documented set, for the last 20 events.
sqlite3 usage-data/usage.db \
  "SELECT occurred_at, raw_attributes FROM usage_events
    ORDER BY occurred_at DESC LIMIT 20;"
```

Long values are the signal: prompt text, tool output and raw API bodies are
orders of magnitude larger than the identifiers and counters that normally
arrive.

## Reporting a problem

This is an internal tool with no published disclosure process. Raise issues
through whatever channel your team uses for internal infrastructure.
