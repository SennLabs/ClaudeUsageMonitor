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
| `BACKUP_DESTINATION` | *(empty)* | Where snapshots go. Empty disables backups. An absolute path means copy mode; `user@host:/path` means rsync. The directory must already exist — it is not created. |
| `BACKUP_MODE` | *(inferred)* | `local` or `rsync`. Required when the destination is ambiguous, e.g. `nas:/vol/backups` with no `user@`. |
| `BACKUP_INTERVAL_HOURS` | `24` | Hours between scheduled backups. Decimals allowed (`0.5` = every 30 min); minimum one minute. A non-numeric or too-small value disables backups and is reported in the status. |
| `BACKUP_KEEP` | `7` | How many timestamped snapshots to retain. Minimum 1. **Copy mode only** — nothing prunes an rsync destination, and the status says so. |
| `BACKUP_FIRST_RUN_DELAY_SECONDS` | `60` | How long after startup the first scheduled backup runs. |
| `BACKUP_SSH_KEY` | *(empty)* | Path *inside the container* to a private key for rsync mode. Mount it in as a read-only volume. No spaces — rsync word-splits the `-e` argument. |
| `BACKUP_SSH_KNOWN_HOSTS` | *(empty)* | Path inside the container to a `known_hosts` file. Set it to verify the destination's host key; without it the connection is accepted blind and the status says so. |
| `LOG_LEVEL` | `INFO` | Level for this application's loggers. Backup and purge activity is logged at INFO; third-party loggers stay at WARNING regardless. |
| `DB_BUSY_TIMEOUT_SECONDS` | `15` | How long a write waits for the SQLite lock before failing. A failure here becomes a 500, which an exporter retries — waiting is better. |
| `MAX_LOG_BODY_BYTES` | `33554432` (32 MB) | Largest accepted `POST /v1/logs` **and `POST /v1/metrics`** body. Neither is proxied through nginx, so nginx's own limit never applies to them. |
| `RAW_ATTRIBUTES_RETENTION_DAYS` | `0` (off) | Null `raw_attributes` on events and metric points older than this. Reclaims most of the disk without changing a single displayed number — reach for this first. |
| `RETENTION_DAYS` | `0` (off) | **Delete** events and metric points older than this, and the sessions left empty by it. This does change historical totals, which is why it is off by default. |

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

## Dashboard preferences

Stored **on the server** in the `app_settings` table, edited at `/settings`, and
served by `GET /api/settings`. They are instance-wide: the wall tablet and your
laptop see the same values, and a save is picked up by every open view without
a reload.

| Setting | Default | Effect |
| --- | --- | --- |
| `monthlyBudget` | `null` | Enables the budget bar on the tablet view |
| `billingCycleDay` | `1` | Day of month (1–28) the budget period resets |
| `refreshIntervalMs` | `5000` | How often every view refetches |
| `defaultTimeWindow` | `'24h'` | Chart window on first load (`24h` / `7d` / `30d` / `all`) |
| `defaultMetric` | `'cost'` | Chart metric on first load (`cost` / `tokens`) |
| `costAlertThresholdPerHour` | `null` | Warning banner when the hourly spend rate exceeds this |
| `displayTimeZone` | `'UTC'` | IANA zone name. Chart days and the budget period start at midnight in this zone; storage stays UTC. Rejected on save if `zoneinfo` does not know it. |

Values are validated on write; an out-of-range one returns `400` rather than
being silently stored. A partial `PUT` merges, so a newer dashboard talking to
an older server degrades instead of failing.

**Read-only, and shown as such:** `activeSessionWindowMin` comes from the
`ACTIVE_WINDOW_MINUTES` environment variable. It is not editable from the
browser because it also decides when a session that never logged usage is
[purged](data-model.md#empty-session-cleanup) — deleting data is operator
configuration, not a display preference.

**Still per-device:** theme, under the `theme` key in `localStorage`. A wall
display and a laptop reasonably differ, and it defaults to dark.

## Related

- [Security](security.md) — what the token does and does not protect
- [Deployment](deployment.md) — applying configuration changes safely
