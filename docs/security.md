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
| `POST /api/backup/trigger` | Yes |
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

- **An empty token disables authentication silently.** There is no warning at
  startup. A typo'd variable name or a missing `.env` produces a fully open
  service that looks perfectly healthy.
- **The comparison is not constant-time.** A plain `!=` on strings is
  theoretically timing-attackable. Over a network, against a high-entropy random
  token, this is not a practical concern — but if you are hardening, use
  `hmac.compare_digest`.

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

**`allow_origins=["*"]`.** With a token set this is fine — a browser on a
malicious page has no way to obtain the header. With the token unset, any web
page a user on your network visits can read the API and post fabricated events.

**No TLS.** Both services speak plain HTTP. On the wire, the bearer token is
visible to anyone who can observe the traffic. Fine on a trusted LAN, not fine
across anything else — terminate TLS in front (see
[Deployment](deployment.md#running-behind-a-reverse-proxy)).

**No rate limiting or payload size cap.** Anyone with the token, or anyone at
all if it is unset, can flood `POST /v1/logs` and grow the database without
bound.

**No event de-duplication or authenticity check.** Events are trusted as sent.
A client can report any session id, model, or cost it likes. This is a usage
*monitor*, not an audit log — don't bill anyone off it without a second source.

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

> This repository's own `.claude/settings.json` is committed and contains a
> live-looking endpoint and token. Treat it as an illustration of the
> configuration shape, not a file to copy — and if that token is real, rotate
> it.

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
- [ ] Tighten CORS to your dashboard's real origin if you don't need the Vite dev server
- [ ] Use `hmac.compare_digest` for the token comparison
- [ ] Log a loud warning at startup when no token is configured
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
