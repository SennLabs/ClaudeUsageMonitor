# Deployment

Two containers, one compose file, one bind-mounted database. There is nothing
clustered here — it is a small internal tool and the deployment story matches.

## Images

### ingest

`python:3.13-slim`, with `rsync` and `openssh-client` added for the optional NAS
backup. Sets `DB_PATH=/data/usage.db`, declares `VOLUME /data`, exposes `8000`,
and runs:

```
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Health check every 30s: an HTTP GET of `/healthz` via `urllib`. That endpoint
runs a `SELECT 1`, so a locked, corrupt or full-disk database returns 503 and
the container is marked unhealthy.

### dashboard

Two stages:

1. `node:22-slim` — `npm ci` then `npm run build` (`tsc -b && vite build`),
   producing `dist/`.
2. `nginx:1.29-alpine` — serves `dist/` and installs
   `nginx.conf.template` into `/etc/nginx/templates/default.conf.template`.

The nginx base image runs `envsubst` over anything in `/etc/nginx/templates/` at
container start, which is how `INGEST_AUTH_TOKEN` gets into the
`proxy_set_header Authorization` line without ever reaching the browser.

Health check every 30s: `wget --spider http://localhost/api/summary` — the
proxy path, not just the static files. Fetching `/` only proved nginx was up:
with ingest down or the token mismatched, every `/api/` call 401s or 502s
while the container still reported healthy.

## nginx configuration

```nginx
location /api/ {
    proxy_pass http://ingest:8000/api/;
    proxy_set_header Host $host;
    proxy_set_header Authorization "Bearer ${INGEST_AUTH_TOKEN}";
}

location / {
    try_files $uri $uri/ /index.html;
}
```

The `try_files` fallback is what makes `/tablet` and `/settings` work on a
direct load or refresh — the SPA router needs `index.html` served for unknown
paths.

Note that only `/api/` is proxied. `POST /v1/logs` is **not** exposed through
the dashboard; clients must reach ingest on port 9585 directly.

## First deployment

```bash
git clone <repo> && cd ClaudeUsageMonitor
cp .env.example .env
python3 -c "import secrets; print(secrets.token_hex(20))"   # paste into INGEST_AUTH_TOKEN
docker compose up --build -d
docker compose ps
```

Confirm:

```bash
curl -s http://localhost:9585/healthz
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:9595/
```

## Upgrading

```bash
git pull
docker compose up --build -d
```

Compose recreates only what changed. The database is a bind mount, so it is
untouched; `init_db()` applies any new schema on startup. Take a backup first
if the release notes mention a schema change.

Roll back by checking out the previous commit and rebuilding. If a release
added a column, rolling back the code leaves the extra column in place —
harmless, since every query names its columns explicitly.

## Applying configuration changes

| Changed | Action |
| --- | --- |
| `INGEST_AUTH_TOKEN` | `docker compose up -d --force-recreate` (both — nginx re-runs envsubst only on create), then update every client's `OTEL_EXPORTER_OTLP_HEADERS` |
| Backup variables | `docker compose up -d --force-recreate ingest` |
| Port mappings | `docker compose up -d`, then update client endpoints |
| Volume mounts | `docker compose up -d --force-recreate ingest` |

`docker compose restart` re-runs the process but keeps the old environment, so
it is not enough for any of these.

## Persistence

```yaml
volumes:
  - ./usage-data:/data
```

The database is `./usage-data/usage.db` on the host, plus `-wal` and `-shm`
sidecar files while running. It survives `docker compose down`, rebuilds, and
image changes.

`docker compose down -v` removes named volumes but not this bind mount — the
data survives that too. Deleting the `usage-data` directory is the only way to
lose it by accident.

## Operations

```bash
docker compose logs -f ingest          # follow ingest logs
docker compose logs --tail=100 dashboard
docker compose ps                      # health status
docker compose exec ingest sh          # shell inside ingest
docker stats                           # resource usage
```

Backup successes and failures are logged by ingest at INFO/ERROR.

## Running behind a reverse proxy

To put the dashboard on a hostname with TLS, terminate in front of port 9595 and
forward everything, including `/api/`:

```nginx
server {
    listen 443 ssl;
    server_name claude-usage.internal;

    location / {
        proxy_pass http://127.0.0.1:9595;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

The ingest port still needs to be reachable by dev containers. You can front
that with TLS too — proxy to 9585 and pass the `Authorization` header through
untouched — which is worth doing if clients report from outside the LAN. Do not
strip or rewrite that header.

## Container hardening

Applied by default in `docker-compose.yml`, because none of it can break a
bind mount:

```yaml
security_opt: ["no-new-privileges:true"]
cap_drop: [ALL]          # ingest only
mem_limit: 512m          # 128m on the dashboard
pids_limit: 200
```

The dashboard also waits for ingest to be *healthy* rather than merely started
(`depends_on: condition: service_healthy`).

**Running as non-root is not the default**, deliberately. The ingest container
is the one that matters — the backup SSH key and the database bind mount are
both there — but switching needs the host-side ownership to match first, and
the order matters:

```bash
docker compose down
sudo chown -R 1000:1000 ./usage-data     # or whatever UID you pick
# then add to the ingest service in docker-compose.yml:
#   user: "1000:1000"
docker compose up -d
```

Setting `user:` without the `chown` leaves ingest unable to write its own
database.

**Published ports bind every interface.** Docker's rules sit ahead of the
`INPUT` chain, so `ufw deny 9585` does **not** block them. Bind an explicit
address instead:

```yaml
ports:
  - "192.168.1.50:9585:8000"
```

or add rules to the `DOCKER-USER` chain, which *is* consulted.

**Dependencies are pinned.** `requirements.txt` names exact versions rather
than floors, so two builds a month apart produce the same software. Bump them
deliberately and re-run the test suite. Base images are still tag-pinned rather
than digest-pinned.

## Resource expectations

Both containers are small. ingest is idle apart from short bursts on each batch;
the dashboard is nginx serving static files. Memory sits in the tens of
megabytes each. The database grows roughly one row per Claude Code API request —
see [Data model → Growth and retention](data-model.md#growth-and-retention).

## Deploying without Docker

Nothing requires containers.

**ingest** — under systemd, gunicorn with uvicorn workers, or plain uvicorn:

```bash
pip install -r ingest/requirements.txt
DB_PATH=/var/lib/claude-usage/usage.db \
INGEST_AUTH_TOKEN=... \
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Run it from the `ingest/` directory so that `app.main:app` imports. (`schema.sql`
itself is resolved from `__file__` and is cwd-independent.) Note the
`pip install -r ingest/requirements.txt` above assumes the repository root, so
the two commands are run from different directories.

Keep it single-process. The backup scheduler runs in the app's event loop, so
multiple workers would mean multiple schedulers racing on the same file.

**dashboard** — `npm run build` and serve `dist/` from any static host. You are
then responsible for the `/api/` proxy and for attaching the bearer token
server-side; without that, either the API is unauthenticated or the browser has
to hold the token.

## Health checks and monitoring

Both containers report health to Docker, visible in `docker compose ps`. For
external monitoring:

| Check | Meaning |
| --- | --- |
| `GET /healthz` returns 200 | ingest is serving **and** can read the database |
| `GET /api/maintenance` → `last_ok` | the background purge/retention sweep is working |
| `GET /api/summary` returns 200 with auth | ingest can read the database |
| `GET /api/backup/status` → `last_backup_ok` | last backup attempt in this process |
| `GET /` on 9595 returns 200 | dashboard is serving |

`/healthz` is deliberately unauthenticated so an external monitor needs no
credential; it does not touch the database, so pair it with an authenticated
`/api/summary` check if you want to know storage is healthy too.
