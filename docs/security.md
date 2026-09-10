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

**`StrictHostKeyChecking=no` in rsync backups.** Unattended backup accepts the
NAS's host key without verification, which is a LAN-appropriate trade. Across an
untrusted network, pre-populate a `known_hosts` file and drop the flag.

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
- [ ] Firewall 9585 to the subnets your dev containers actually live on
- [ ] Firewall 9595 to your office/VPN range
- [ ] Terminate TLS in front of both if traffic leaves a trusted LAN
- [ ] Put SSO or basic auth in front of the dashboard if read access needs control
- [x] ~~Tighten CORS~~ — removed entirely, 2026-09-10
- [x] ~~Use `hmac.compare_digest`~~ — done, 2026-09-10
- [x] ~~Warn at startup when no token is configured~~ — now refuses to start, 2026-09-10
- [ ] Use a dedicated, restricted SSH key for backups — not your personal one
- [ ] Verify backup destination permissions: snapshots are full copies of the data

## What is stored

Session, user, and organization identifiers, model names, token counts, costs,
timestamps, and the complete attribute map of every log record in
`raw_attributes`.

Claude Code does not send prompt or response content by default. It does have
opt-in settings that add prompt content to telemetry, and this service stores
whatever attributes arrive — so if a client enables that, the content lands in
this database and in every backup snapshot. Decide deliberately on the client
side, and treat the database and its backups as containing exactly what the
clients chose to send.

## Reporting a problem

This is an internal tool with no published disclosure process. Raise issues
through whatever channel your team uses for internal infrastructure.
