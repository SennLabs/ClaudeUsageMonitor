# Claude Usage Monitor

Tracks Claude Code token usage and cost across multiple concurrent dev-container
sessions, centrally, via a small ingestion API and a web dashboard.

## How it works

Claude Code has built-in OpenTelemetry export. Each dev container that opts in
sends usage events directly to the `ingest` service here (no agent or log-tailing
required on the sending side). `ingest` stores them in SQLite; `dashboard` polls
`ingest`'s read API and renders them.

```
Claude Code session(s)  --OTLP/HTTP-->  ingest (FastAPI + SQLite)  <--/api/*--  dashboard (SolidJS, via nginx)
   (dev container A)                          |
   (dev container B)                          ▼
   (dev container ...)                  usage.db (sessions, usage_events)
```

## Components

- **`ingest/`** — FastAPI service.
  - `POST /v1/logs` — accepts OTLP/JSON log exports (what Claude Code sends).
  - `GET /api/summary`, `/api/sessions`, `/api/usage-by-model` — read endpoints for the dashboard.
  - `GET /healthz` — unauthenticated health check.
  - Data lives in SQLite (`usage.db` locally, `/data/usage.db` in the container).
- **`dashboard/`** — SolidJS + Vite + Tailwind frontend. Dark mode by default (toggle persists to `localStorage`). Polls the read API every 5s.

## Running locally (no Docker)

```bash
# ingest
cd ingest
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt   # includes httpx, needed for test_ingest.py
.venv/bin/python test_ingest.py                 # optional: run the smoke test
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 9585

# dashboard (separate terminal)
cd dashboard
npm install
npm run dev   # http://localhost:5173, proxies /api to 127.0.0.1:8000
```

## Running with Docker

```bash
cp .env.example .env   # set INGEST_AUTH_TOKEN to enable auth (recommended - see below)
docker compose up --build
```

- Dashboard: `http://localhost:9595`
- Ingest (for dev containers to report to): `http://<this-host>:9585`

Usage data persists in the `usage-data` named volume across container recreation.

## Configuring a dev container to report usage (the sending side)

Any machine running Claude Code can report to this monitor by enabling its
built-in OpenTelemetry export and pointing it here. Add this to `.claude/settings.json`:

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

Where to put it:

- **`~/.claude/settings.json`** (user-level, inside the dev container) — applies to every project/session in that container. This is the usual choice, since the goal is "every session in this container reports."
- **`.claude/settings.json`** (project-level, checked into a repo) — applies only to that project, but is shareable with everyone who clones it.

Notes:

- Replace `<host-running-ingest>` with wherever `ingest` is reachable from that
  container (an internal hostname/IP, or `localhost` if both run on the same
  machine).
- Drop the `OTEL_EXPORTER_OTLP_HEADERS` line entirely if `INGEST_AUTH_TOKEN` is
  unset on the server (auth disabled).
- Settings are read at session start, so open a new Claude Code session (or
  restart the current one) after editing `settings.json`.
- Concurrent sessions need no extra config — each gets its own `session.id`
  automatically, which is what the dashboard groups by.

## Security notes

- `INGEST_AUTH_TOKEN` (set via `.env`/`docker-compose.yml`) gates both
  `POST /v1/logs` and all `/api/*` reads. Leave it blank only on a trusted
  private network — port 8000 has to be reachable from every reporting dev
  container, so it's the most exposed part of this stack.
- The dashboard's nginx injects the token server-side when proxying `/api/*`
  to `ingest`; the token never ships in the browser bundle.
