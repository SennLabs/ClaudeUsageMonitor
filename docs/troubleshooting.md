# Troubleshooting

Work top-down: is the client sending, is ingest receiving, is the dashboard
reading.

## Quick triage

```bash
# 1. Is ingest alive?
curl -s http://localhost:9585/healthz
# {"status":"ok"}

# 2. Does the token work?
curl -s -o /dev/null -w '%{http_code}\n' \
  -H "Authorization: Bearer $INGEST_AUTH_TOKEN" \
  http://localhost:9585/api/summary
# 200 = good, 401 = token mismatch

# 3. Is there any data at all?
sqlite3 ./usage-data/usage.db "SELECT COUNT(*), MAX(occurred_at) FROM usage_events;"

# 4. Is the dashboard up?
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:9595/

# 5. Anything in the logs?
docker compose logs --tail=50 ingest
```

---

## No data appears on the dashboard

Work through these in order.

**Is the dashboard reaching the API at all?** Open the browser devtools Network
tab and reload. If `/api/summary` is 200 with an empty-looking body, the problem
is upstream (no events). If it's 401 or failing to connect, jump to
[Dashboard shows the error toast](#dashboard-shows-couldnt-reach-the-usage-api).

**Are events in the database?**

```bash
sqlite3 ./usage-data/usage.db "SELECT COUNT(*) FROM usage_events;"
```

Zero means nothing has ever been ingested — a client-side problem.

**Is the client configured and restarted?** Settings are read at session start.
After editing `settings.json` you must start a new Claude Code session; an
already-running one keeps the old configuration. Check the file:

```bash
cat ~/.claude/settings.json
```

`CLAUDE_CODE_ENABLE_TELEMETRY` must be `"1"`, `OTEL_LOGS_EXPORTER` must be
`otlp`, and `OTEL_EXPORTER_OTLP_PROTOCOL` must be `http/json` — `grpc` and
`http/protobuf` payloads are not parsed by `/v1/logs`.

**Can the container reach ingest?**

```bash
curl -v http://<host-running-ingest>:9585/healthz
```

Run this *from inside the dev container*, not the host. `localhost` inside a
container is the container itself — you almost certainly need the host's LAN IP
or `host.docker.internal`. See
[Client setup → Choosing the endpoint host](client-setup.md#choosing-the-endpoint-host).

**Is the endpoint URL right?** `OTEL_EXPORTER_OTLP_ENDPOINT` must be the **base**
URL. The exporter appends `/v1/logs` itself; including it produces requests to
`/v1/logs/v1/logs`, which 404.

**Is the token matching?** Post a payload by hand and read the status:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  http://<host>:9585/v1/logs \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer <token from the client settings>" \
  -d '{"resourceLogs":[]}'
```

`200` means auth is fine. `401` means the client's token doesn't match the
server's — note the header format is `Authorization=Bearer <token>` in
`OTEL_EXPORTER_OTLP_HEADERS` (an `=` between name and value, a space before the
token).

**Give it time.** Up to `OTEL_LOGS_EXPORT_INTERVAL` (5s) for the client to flush,
plus up to `refreshIntervalMs` (5s) for the dashboard to poll.

---

## `401 Unauthorized`

The configured `INGEST_AUTH_TOKEN` and the token in the request don't match.

- **From a dev container:** compare the token in `OTEL_EXPORTER_OTLP_HEADERS`
  against `.env` on the server. Watch for trailing whitespace and quotes.
- **From the dashboard:** nginx injects the token via `envsubst` **at container
  start**. If you changed `.env` and only ran `docker compose restart`, nginx is
  still serving the old value. Fix:
  ```bash
  docker compose up -d --force-recreate dashboard
  ```
- **In local dev:** nothing injects the header. Either unset
  `INGEST_AUTH_TOKEN` for the local uvicorn process, or expect 401s.

Confirm what the server thinks it has:

```bash
docker compose exec ingest sh -c 'echo "[$INGEST_AUTH_TOKEN]"'
```

The brackets make stray whitespace visible.

---

## Dashboard shows "Couldn't reach the usage API"

Raised when any of the resource fetches fail.

1. Is ingest running and healthy? `docker compose ps`
2. Can the dashboard container reach it?
   ```bash
   docker compose exec dashboard wget -qO- http://ingest:8000/healthz
   ```
   Failure here means a compose network problem — `docker compose up -d` to
   rebuild the network.
3. Is it a 401? Check the browser Network tab; if so, see above.
4. `docker compose logs dashboard` for nginx proxy errors.

In local dev, this usually means uvicorn isn't on port 8000, which is what
`vite.config.ts` proxies to. Either start uvicorn on 8000 or change the proxy
target.

---

## Sessions appear but tokens or cost are zero

The event arrived, but its token attributes weren't recognized. Look at what
actually came in:

```bash
sqlite3 ./usage-data/usage.db \
  "SELECT raw_attributes FROM usage_events ORDER BY id DESC LIMIT 1;"
```

If you see attribute keys that aren't in
[the alias table](data-model.md#otlp-attribute-mapping) — a Claude Code version
using different names — the fix is to add the alias. The data isn't lost:
`raw_attributes` has it, and you can backfill with `json_extract`. See
[Development → Adding a telemetry attribute](development.md#adding-a-telemetry-attribute).

---

## Model shows as "unknown"

Events arrived without a `model` attribute. `COALESCE(model, 'unknown')` groups
them. Same diagnosis as above — check `raw_attributes` for a differently-named
key.

---

## The chart is empty but the tables have data

- **Everything is older than the window.** The chart queries a look-back
  (`24h` / `7d` / `30d`); the tables and summary cards are all-time. Switch to
  **All** to drop the look-back entirely.
- **Timestamps are wrong.** If a client's clock is skewed, events land outside
  the window — or in the future:
  ```bash
  sqlite3 ./usage-data/usage.db \
    "SELECT MIN(occurred_at), MAX(occurred_at) FROM usage_events;"
  ```
  A max timestamp in the future means clock skew on a reporting machine.

Gaps in a line are normal — empty buckets produce no rows.

---

## Active session count seems wrong

`/api/summary`'s `active_sessions` is computed server-side from
`ACTIVE_WINDOW_MINUTES` (default 15). The `activeSessionWindowMin` setting on
`/settings` only affects the tablet view's own per-session marking, so the two
disagree unless you set them to the same value. Change the server side with the
environment variable and recreate the ingest container.

---

## Sessions are disappearing from the table

Expected, if they never logged any usage. A session that goes quiet past the
active window without recording a single token or cent is deleted, along with
its placeholder events — see
[Empty-session cleanup](data-model.md#empty-session-cleanup). This is what stops
"container started, Claude never used" rows accumulating.

A session with **any** recorded usage is never purged, regardless of age. If
something with real usage vanished, that is not this — check whether the
database was restored from an older backup.

To confirm what the sweep is doing:

```bash
docker compose logs ingest | grep -i purge
```

To turn the behaviour off, comment out the `_purge_loop` task in the lifespan in
[`main.py`](../ingest/app/main.py). To give sessions longer before they qualify,
raise `ACTIVE_WINDOW_MINUTES`.

---

## Chart x-axis shows times instead of dates

Fixed — but worth knowing what it was, since the shape can recur. The `hours`
prop was being passed to `<UsageChart>` *after* the props spread, so it always
won at 24, and the chart formatted every bucket as a clock time. Daily buckets
are UTC midnight, so in UTC+8 every day rendered as `08:00`.

Label format now follows the bucket granularity, which mirrors the server's
`_time_bucket_fmt`: hourly buckets get a clock time, daily buckets get a date.
If it regresses, check that nothing is overriding `hours` on the component and
that `WINDOW_HOURS` in [`settings.ts`](../dashboard/src/settings.ts) still
matches the server's 48-hour cutover.

---

## Linking a user to a project didn't tag a session

The mapping fills a session's project only when it is **empty**. If that session
already carries a label someone set by hand, later events leave it alone — see
[`PUT /api/user-projects/{user_id}`](api-reference.md#put-apiuser-projectsuser_id).
Re-saving the link on the `/users` page relabels everything that user owns,
including hand-set labels.

If nothing at all was tagged, check the user id actually matches:

```bash
sqlite3 ./usage-data/usage.db \
  "SELECT DISTINCT user_id FROM sessions WHERE user_id IS NOT NULL;"
sqlite3 ./usage-data/usage.db "SELECT * FROM user_projects;"
```

Sessions arriving with no `user.id` attribute at all cannot be mapped this way —
tag those individually from the Sessions table.

---

## Costs don't match Anthropic billing

`cost_usd` is what Claude Code reported per event; this service stores it as
received. Mismatches usually come from client-side pricing assumptions, an
unrecognized model, or double-counted retried batches (there is no
de-duplication).

Set per-model overrides at `/settings` → **Model prices** to recompute display
cost from raw token counts. That changes presentation only; stored values are
never rewritten. This is a monitoring tool, not a billing system — reconcile
against the real invoice.

---

## Backups aren't running

**Check status first:**

```bash
curl -s -H "Authorization: Bearer $INGEST_AUTH_TOKEN" \
  http://localhost:9585/api/backup/status | python3 -m json.tool
```

| Symptom | Cause |
| --- | --- |
| `"enabled": false` | `BACKUP_DESTINATION` is empty. Set it and `docker compose up -d --force-recreate ingest`. |
| `enabled` true, all `last_*` null | The scheduler waits one full interval before its first run — and state resets on restart. Trigger manually to test. |
| `last_backup_ok: false` | Read `last_backup_error`. |

**Common errors:**

- *Permission denied* — the destination directory isn't writable by the
  container's user. Check ownership on the host side of the mount.
- *No such file or directory* — the destination isn't mounted into the
  container. `BACKUP_DESTINATION` is a path **inside** the container.
- *Permission denied (publickey)* — rsync mode. Verify the key is mounted and
  `BACKUP_SSH_KEY` points at it:
  ```bash
  docker compose exec ingest ls -l /run/secrets/backup_key
  docker compose exec ingest ssh -i /run/secrets/backup_key \
    -o StrictHostKeyChecking=no -o BatchMode=yes user@nas 'echo ok'
  ```
  `BatchMode=yes` means a passphrase-protected key always fails — use one
  without a passphrase.
- *Container restarts more often than the interval* — no scheduled backup will
  ever fire. Shorten `BACKUP_INTERVAL_HOURS` or fix the restarts.

**Remote backups piling up:** expected. `BACKUP_KEEP` prunes in copy mode only;
nothing prunes the remote in rsync mode.

---

## Database is locked

Rare — WAL mode plus this write volume shouldn't produce it. If you see it:

- Something else has the file open for writing. Don't run a second ingest
  process against the same `DB_PATH` — that includes multiple uvicorn workers.
- Open external inspections read-only: `sqlite3 -readonly ./usage-data/usage.db`.

---

## Container won't start

```bash
docker compose logs ingest
docker compose logs dashboard
```

| Cause | Fix |
| --- | --- |
| Port already in use | Change the host port in `docker-compose.yml` |
| `/data` not writable | Check permissions on `./usage-data` |
| Corrupt database | `sqlite3 ./usage-data/usage.db "PRAGMA integrity_check;"` → [restore](backup-and-restore.md#restoring) |
| Dashboard build failure | A TypeScript error fails `tsc -b`; the error is in the build log |

---

## Data disappeared after a rebuild

Check that the volume mount is intact in `docker-compose.yml`:

```yaml
volumes:
  - ./usage-data:/data
```

Without it, the database lives in the container's writable layer and dies with
the container. If `./usage-data/usage.db` still exists on the host, the mount is
the problem, not the data. If the directory is gone, restore from a
[backup](backup-and-restore.md#restoring).

---

## Still stuck

Collect this before asking for help:

```bash
docker compose ps
docker compose logs --tail=100 ingest
curl -s http://localhost:9585/healthz
curl -s -H "Authorization: Bearer $INGEST_AUTH_TOKEN" http://localhost:9585/api/summary
sqlite3 ./usage-data/usage.db "SELECT COUNT(*), MIN(occurred_at), MAX(occurred_at) FROM usage_events;"
```

Redact the token before sharing any of it.
