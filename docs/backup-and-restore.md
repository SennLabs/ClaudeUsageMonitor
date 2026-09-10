# Backup and restore

The ingest service can snapshot its SQLite database on a schedule and ship it to
a local path or a NAS over SSH. It is **off by default** and stays off until
`BACKUP_DESTINATION` is set. Implementation:
[`ingest/app/backup.py`](../ingest/app/backup.py).

## How a backup runs

1. `_snapshot()` uses Python's `sqlite3.backup()` API to write a consistent
   point-in-time copy to a temp file named `usage_backup_<UTC timestamp>.db`.
   Because this goes through SQLite's own backup API and the database is in WAL
   mode, no writes need to be paused — a plain `cp` of a live WAL database can
   capture a torn state, which is exactly what this avoids.
2. The snapshot is delivered:
   - **copy mode** — `shutil.copy2` into the destination directory (created if
     missing), then old snapshots are pruned to `BACKUP_KEEP`.
   - **rsync mode** — `rsync -az --no-implied-dirs` to the remote, with a
     5-minute timeout.
3. The temp snapshot is deleted regardless of outcome.
4. Success or failure is recorded in memory and surfaced at
   `/api/backup/status`.

A failure is logged and recorded, never raised — a broken NAS mount does not
take down ingestion.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `BACKUP_DESTINATION` | *(empty)* | Empty = disabled. A path = copy mode. `user@host:/path` = rsync mode. |
| `BACKUP_INTERVAL_HOURS` | `24` | Hours between runs; decimals allowed |
| `BACKUP_KEEP` | `7` | Snapshots to retain — **copy mode only** |
| `BACKUP_SSH_KEY` | *(empty)* | Path inside the container to the private key for rsync mode |

Mode is `BACKUP_MODE` when you set it. Otherwise it is inferred: `user@host:/path`
means rsync, an absolute path means copy, **and anything else is rejected** —
backups are disabled and the reason appears in `/api/backup/status`.

That last part matters. The old rule was "contains `@` and `:`", which
classified `nas:/volume1/backups` — an ordinary SSH destination that omits the
user — as *local*. It created a directory called `nas:` inside the container,
reported every backup as succeeding, and lost the lot on the next rebuild.

Two more guards in copy mode: the destination directory must already exist (a
typo'd path is not created for you), and it may not be the database's own
directory (a backup there dies with the thing it protects).

## Option A — local or mounted path

For a NAS already mounted on the Docker host, or just another disk.

`docker-compose.yml`:

```yaml
services:
  ingest:
    volumes:
      - ./usage-data:/data
      - /mnt/nas/claude-backups:/backups
```

`.env`:

```dotenv
BACKUP_DESTINATION=/backups
BACKUP_INTERVAL_HOURS=24
BACKUP_KEEP=7
```

`/backups` is the path **inside the container**; `/mnt/nas/claude-backups` is
where it actually lands on the host. Retention keeps the newest
`BACKUP_KEEP` files matching `usage_backup_*.db` and deletes the rest —
filenames sort chronologically because the timestamp is `YYYYmmdd_HHMMSS`.

```bash
docker compose up -d --force-recreate ingest
```

## Option B — rsync over SSH to a NAS

Mount a private key read-only and point the destination at the remote:

```yaml
services:
  ingest:
    volumes:
      - ./usage-data:/data
      - /home/youruser/.ssh/id_ed25519:/run/secrets/backup_key:ro
```

```dotenv
BACKUP_DESTINATION=user@192.168.1.50:/volume1/claude-backups/
BACKUP_SSH_KEY=/run/secrets/backup_key
BACKUP_INTERVAL_HOURS=24
```

The ingest image already includes `rsync` and `openssh-client`.

The SSH invocation is:

```
ssh -i $BACKUP_SSH_KEY -o StrictHostKeyChecking=no -o BatchMode=yes
```

`BatchMode=yes` means it never prompts — a passphrase-protected key will simply
fail. `StrictHostKeyChecking=no` accepts the host key without verification,
which is a deliberate trade for unattended operation on a trusted LAN and is
*not* appropriate across an untrusted network.

Two things to know about rsync mode:

- **`BACKUP_KEEP` is ignored.** Nothing prunes the remote. Each run adds a new
  timestamped file and they accumulate until you clean up — a cron job on the
  NAS, or a snapshot/retention policy there. `/api/backup/status` returns
  `keep: null` and a warning saying so, rather than reporting a retention
  policy that is not being applied.
- **A trailing slash on the destination matters** to rsync. Use one, as in the
  example, so files land *in* the directory.

Test the credentials before relying on the schedule:

```bash
docker compose exec ingest ssh -i /run/secrets/backup_key \
  -o StrictHostKeyChecking=no -o BatchMode=yes user@192.168.1.50 'echo ok'
```

## Scheduling behaviour

`start_scheduler` is launched from the FastAPI lifespan when backups are
enabled. The first run happens `BACKUP_FIRST_RUN_DELAY_SECONDS` after startup
(default 60), then on the interval.

Before 2026-09-10 it waited a **full interval** first, so a container
redeployed nightly with the 24-hour default never backed up at all — while the
status endpoint reported `enabled: true` with a `next_backup_at` a day out.

The next run is computed after the previous one completes, so a slow backup
pushes the following one out rather than overlapping.

## Manual backup

From the dashboard: `/settings` → Backup → **Run backup now**. The button is
only rendered when a destination is configured.

From the CLI:

```bash
curl -X POST -H "Authorization: Bearer $INGEST_AUTH_TOKEN" \
  http://localhost:9585/api/backup/trigger
```

Runs one cycle immediately and returns the resulting status. It does not shift
the scheduled next run.

- `409` if a backup is already in flight. Only one runs at a time.
- `400` if no destination is configured, or the configuration is invalid.
- **`500` if the run happened and failed.** It previously returned `200`
  regardless, so the dashboard reported success for a backup that had not been
  written.

## Checking status

```bash
curl -s -H "Authorization: Bearer $INGEST_AUTH_TOKEN" \
  http://localhost:9585/api/backup/status | python3 -m json.tool
```

The `last_backup_*` fields are process-local: they are `null` after a restart
even if the destination is full of good snapshots. To verify what actually
exists, look at the destination.

## Restoring

1. **Stop ingest** so nothing is writing:
   ```bash
   docker compose stop ingest
   ```
2. **Pick a snapshot** — newest is usually right:
   ```bash
   ls -lt /mnt/nas/claude-backups/usage_backup_*.db | head
   ```
3. **Set aside the current database** rather than deleting it:
   ```bash
   mv ./usage-data/usage.db ./usage-data/usage.db.pre-restore
   rm -f ./usage-data/usage.db-wal ./usage-data/usage.db-shm
   ```
   The `-wal` and `-shm` files belong to the old database; leaving them beside a
   restored file is asking for corruption.
4. **Put the snapshot in place:**
   ```bash
   cp /mnt/nas/claude-backups/usage_backup_20260910_020011.db ./usage-data/usage.db
   ```
   Match ownership to what the container runs as if you hit permission errors.
5. **Verify before starting:**
   ```bash
   sqlite3 ./usage-data/usage.db "PRAGMA integrity_check; SELECT COUNT(*) FROM usage_events;"
   ```
6. **Start:**
   ```bash
   docker compose start ingest
   curl -s http://localhost:9585/healthz
   ```

`init_db()` runs on startup and applies any missing schema pieces, so restoring
an older snapshot into a newer version of the service is fine — the guarded
`ALTER TABLE` migrations bring it forward.

Once the dashboard looks right, delete `usage.db.pre-restore`.

## What backups do not cover

Only the database is snapshotted. Not backed up:

- `.env` and your token
- `docker-compose.yml` and any local edits
- Dashboard preferences — those live in each browser's `localStorage`

Keep those in version control or your usual secret store.

## Verifying a backup is real

A backup you have never restored is a hypothesis. Periodically:

```bash
cp /mnt/nas/claude-backups/usage_backup_<ts>.db /tmp/check.db
sqlite3 /tmp/check.db "PRAGMA integrity_check;"
sqlite3 /tmp/check.db "SELECT COUNT(*) FROM usage_events; SELECT MAX(occurred_at) FROM usage_events;"
```

`ok`, a plausible row count, and a recent max timestamp together mean the
snapshot is usable.
