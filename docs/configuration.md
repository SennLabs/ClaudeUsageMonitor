# Configuration

Everything is configured through environment variables. There is no config file
to edit; `.env` at the repository root is read by Docker Compose and fed into
the containers.

Start from the template:

```bash
cp .env.example .env
```

## Ingest service

| Variable | Default | Description |
| --- | --- | --- |
| `INGEST_AUTH_TOKEN` | *(empty)* | Bearer token gating `POST /v1/logs` and every `/api/*` route. **Ingest refuses to start if this is empty**, unless `INGEST_ALLOW_ANONYMOUS` is also set. |
| `INGEST_ALLOW_ANONYMOUS` | *(unset)* | Set to `true` to deliberately run with authentication disabled on a trusted network. A prominent warning is logged at startup. |
| `DB_PATH` | `ingest/usage.db` (repo) / `/data/usage.db` (container) | Absolute path to the SQLite file. Set in the Dockerfile so a mounted volume keeps data across container recreation. |
| `ACTIVE_WINDOW_MINUTES` | `15` | How long a session may go quiet before it stops counting as active. Also the grace period before an unused session is purged. |
| `BACKUP_DESTINATION` | *(empty)* | Where snapshots go. Empty disables backups. A local path enables copy mode; a `user@host:/path` string enables rsync-over-SSH mode. |
| `BACKUP_INTERVAL_HOURS` | `24` | Hours between scheduled backups. Decimals allowed (`0.5` = every 30 min). |
| `BACKUP_KEEP` | `7` | How many timestamped snapshots to retain. **Local/copy mode only** — remote pruning is not performed. |
| `BACKUP_SSH_KEY` | *(empty)* | Path *inside the container* to a private key for rsync mode. Mount it in as a read-only volume. |
| `LOG_LEVEL` | `INFO` | Level for this application's loggers. Backup and purge activity is logged at INFO; third-party loggers stay at WARNING regardless. |

Backup variables are covered in depth in [Backup and restore](backup-and-restore.md).

## Dashboard service

| Variable | Default | Description |
| --- | --- | --- |
| `INGEST_AUTH_TOKEN` | *(empty)* | Same value as the ingest service. nginx substitutes it into `proxy_set_header Authorization` at container start, so the browser never receives it. |

Substitution happens via the nginx base image's `envsubst` step over
`/etc/nginx/templates/default.conf.template`. Changing the token therefore
requires recreating the dashboard container, not just restarting nginx:

```bash
docker compose up -d --force-recreate dashboard
```

## Ports

| Service | Container port | Host port | Notes |
| --- | --- | --- | --- |
| ingest | 8000 | 9585 | Must be reachable from every reporting dev container |
| dashboard | 80 | 9595 | Browser-facing |

Deliberately uncommon host ports, to stay out of the way of whatever else is on
the box. To change them, edit the `ports:` entries in `docker-compose.yml` — the
left-hand number is the host side:

```yaml
ports:
  - "18585:8000"   # ingest now on host port 18585
```

Then update `OTEL_EXPORTER_OTLP_ENDPOINT` in every client's settings to match.

Internally, the dashboard's nginx always proxies to `http://ingest:8000` over
the compose network, so the host port mapping is irrelevant to it.

## Volumes

```yaml
volumes:
  - ./usage-data:/data
```

A bind mount: the database lives at `./usage-data/usage.db` in the repository
directory. It survives `docker compose down`, rebuilds, and image upgrades.

A named volume is also viable — there is a commented-out `volumes:` block at the
bottom of `docker-compose.yml` for it. Bind mount is the default because it
makes the file trivially findable for backup and inspection.

Two more mounts are commented out in `docker-compose.yml`, for backup use:

```yaml
# - /home/youruser/.ssh/id_ed25519:/run/secrets/backup_key:ro   # rsync mode
# - /mnt/nas/claude-backups:/backups                            # local NAS mount
```

## Dashboard preferences (client-side)

These are *not* environment variables. They are per-browser settings, stored in
`localStorage` under the key `claudeMonitorSettings`, edited at `/settings`, and
defined in [`dashboard/src/settings.ts`](../dashboard/src/settings.ts).

| Setting | Default | Effect |
| --- | --- | --- |
| `monthlyBudget` | `null` | Enables the budget progress bar on the tablet view |
| `billingCycleDay` | `1` | Day of month (1–28) the budget resets |
| `refreshIntervalMs` | `5000` | How often every view refetches the API |
| `activeSessionWindowMin` | `15` | Minutes before a session counts as inactive **in the tablet view's own calculation** |
| `defaultTimeWindow` | `'24h'` | Chart window on first load (`24h` / `7d` / `30d` / `all`) |
| `defaultMetric` | `'cost'` | Chart metric on first load (`cost` / `tokens`) |
| `costAlertThresholdPerHour` | `null` | Shows a warning banner when the hourly spend rate exceeds this |
| `modelPrices` | `{}` | Per-model `$/Mtok` overrides used to recompute cost instead of trusting the reported figure |

Two consequences worth knowing:

- **They do not sync.** Each browser has its own copy. Configuring the wall
  tablet does not configure your laptop.
- **`activeSessionWindowMin` is not universal.** The `active_sessions` number in
  `/api/summary` and `/api/users` is computed server-side from
  `ACTIVE_WINDOW_MINUTES`. The browser setting only affects the tablet view's own
  per-session active/idle marking. Keep the two in agreement, and note the server
  value is also what decides when an unused session gets
  [purged](data-model.md#empty-session-cleanup) — set the browser window longer
  than the server one and the tablet can show a session as active moments before
  it is deleted.

Theme is stored separately, under the `theme` key, and defaults to dark.

## Model price overrides

By default the dashboard displays the `cost_usd` Claude Code reports for each
event. If your pricing differs — a negotiated rate, or a model the client prices
incorrectly — add an override at `/settings` under **Model prices**, giving
input and output dollars per million tokens. `adjustedCost()` then recomputes
from raw token counts for that model only; models without an override keep the
reported figure.

Overrides are display-time only. The database always keeps what was reported.

## Related

- [Security](security.md) — what the token does and does not protect
- [Deployment](deployment.md) — applying configuration changes safely
