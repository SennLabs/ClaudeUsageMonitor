# Getting started

Two ways to run this: Docker Compose (what you want for anything long-lived) or
both services directly on your machine (what you want while changing code).

## Prerequisites

| Path | Needs |
| --- | --- |
| Docker | Docker Engine with the Compose plugin |
| Local | Python 3.11+ (the image uses 3.13), Node 20+ (the image uses 22) |

## Option A — Docker Compose

```bash
git clone <this repo>
cd ClaudeUsageMonitor
cp .env.example .env
```

Edit `.env` and set a token — any long random string:

```bash
python3 -c "import secrets; print(secrets.token_hex(20))"
```

```dotenv
INGEST_AUTH_TOKEN=<paste it here>
```

Then bring the stack up:

```bash
docker compose up --build -d
docker compose ps        # both services should report healthy
```

| What | Where |
| --- | --- |
| Dashboard | <http://localhost:9595> |
| Tablet view | <http://localhost:9595/tablet> |
| Settings | <http://localhost:9595/settings> |
| Ingest endpoint (for dev containers) | `http://<this-host>:9585` |
| Health check | <http://localhost:9585/healthz> |

Usage data is written to `./usage-data/usage.db` on the host, so it survives
`docker compose down` and rebuilds.

Verify ingest is reachable and answering:

```bash
curl -s http://localhost:9585/healthz
# {"status":"ok"}

curl -s -H "Authorization: Bearer $INGEST_AUTH_TOKEN" http://localhost:9585/api/summary
```

The dashboard will be empty until at least one Claude Code session reports to
it. That is the next step: [Client setup](client-setup.md).

## Option B — Run locally, no Docker

Two terminals.

**Terminal 1 — ingest**

```bash
cd ingest
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt   # dev adds httpx, needed by the smoke test
.venv/bin/python test_ingest.py                 # optional but fast; should print PASS lines
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Port 8000 matters: the Vite dev server proxies `/api` to `http://127.0.0.1:8000`
(see [`dashboard/vite.config.ts`](../dashboard/vite.config.ts)). If you run
uvicorn on a different port, change the proxy target to match.

Without `DB_PATH` set, the database is created at `ingest/usage.db`. Override it
if you want it elsewhere:

```bash
DB_PATH=/tmp/usage.db .venv/bin/uvicorn app.main:app --port 8000
```

**Terminal 2 — dashboard**

```bash
cd dashboard
npm install
npm run dev      # http://localhost:5173
```

In this mode nginx is not in the picture, so nothing injects the bearer token
into `/api/*` calls. Leave `INGEST_AUTH_TOKEN` unset for local development, or
the dashboard's requests will come back `401`.

## Send it some data

If no dev container is pointed here yet, you can still prove the pipe works by
posting a synthetic OTLP payload:

```bash
curl -s -X POST http://localhost:9585/v1/logs \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $INGEST_AUTH_TOKEN" \
  -d '{
    "resourceLogs": [{
      "resource": {"attributes": [
        {"key": "user.id", "value": {"stringValue": "demo-user"}}
      ]},
      "scopeLogs": [{"logRecords": [{
        "timeUnixNano": "'"$(date +%s)000000000"'",
        "body": {"stringValue": "claude_code.api_request"},
        "attributes": [
          {"key": "session.id",    "value": {"stringValue": "demo-session"}},
          {"key": "model",         "value": {"stringValue": "claude-opus-5"}},
          {"key": "input_tokens",  "value": {"intValue": "1200"}},
          {"key": "output_tokens", "value": {"intValue": "340"}},
          {"key": "cost_usd",      "value": {"doubleValue": 0.0231}}
        ]
      }]}]
    }]
  }'
```

Reload the dashboard — one session, one model, and a few cents of cost should
appear within the refresh interval.

## Next steps

- [Client setup](client-setup.md) — point real dev containers at this instance
- [Configuration](configuration.md) — ports, tokens, backup variables
- [Dashboard guide](dashboard.md) — what each view and control does
- [Backup and restore](backup-and-restore.md) — keep the SQLite file safe
