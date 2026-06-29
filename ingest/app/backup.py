"""Periodic SQLite backup — copy to a local/mounted path or rsync over SSH to a NAS."""

import asyncio
import logging
import os
import shutil
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

# ── Config from environment ────────────────────────────────────────────────
# Backup is active when BACKUP_DESTINATION is non-empty.
DESTINATION = os.environ.get("BACKUP_DESTINATION", "").strip()
INTERVAL_HOURS = float(os.environ.get("BACKUP_INTERVAL_HOURS", "24"))
KEEP = int(os.environ.get("BACKUP_KEEP", "7"))
SSH_KEY = os.environ.get("BACKUP_SSH_KEY", "").strip()
ENABLED = bool(DESTINATION)

# ── Runtime state (reset on restart — that's fine for a small tool) ────────
_last_at: str | None = None
_last_ok: bool | None = None
_last_error: str | None = None
_next_at: str | None = None


def _is_remote(dest: str) -> bool:
    return "@" in dest and ":" in dest


def _snapshot(db_path: Path) -> Path:
    """
    Create a consistent, point-in-time copy using sqlite3.backup().
    This is safe even with WAL mode active — no need to stop writes first.
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    tmp = db_path.parent / f"usage_backup_{ts}.db"
    src = sqlite3.connect(str(db_path))
    dst = sqlite3.connect(str(tmp))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return tmp


def _prune_local(dest_dir: Path) -> None:
    """Keep only the newest KEEP backup files."""
    files = sorted(dest_dir.glob("usage_backup_*.db"))
    for old in files[:-KEEP]:
        try:
            old.unlink()
        except OSError as e:
            log.warning("Could not remove old backup %s: %s", old, e)


def _rsync(src: Path) -> None:
    cmd = ["rsync", "-az", "--no-implied-dirs"]
    if SSH_KEY:
        cmd += ["-e", f"ssh -i {SSH_KEY} -o StrictHostKeyChecking=no -o BatchMode=yes"]
    cmd += [str(src), DESTINATION]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"rsync exited with code {result.returncode}")


def run_backup(db_path: Path) -> None:
    """
    Execute one backup cycle synchronously.
    Intended to be called from asyncio via run_in_executor so it doesn't block the event loop.
    """
    global _last_at, _last_ok, _last_error

    snapshot: Path | None = None
    try:
        if not DESTINATION:
            raise RuntimeError("BACKUP_DESTINATION is not configured")

        snapshot = _snapshot(db_path)

        if _is_remote(DESTINATION):
            _rsync(snapshot)
        else:
            dest_dir = Path(DESTINATION)
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(snapshot, dest_dir / snapshot.name)
            _prune_local(dest_dir)

        _last_ok = True
        _last_error = None
        log.info("Backup succeeded → %s", DESTINATION)

    except Exception as exc:
        _last_ok = False
        _last_error = str(exc)
        log.error("Backup failed: %s", exc)

    finally:
        _last_at = datetime.now(timezone.utc).isoformat()
        if snapshot and snapshot.exists():
            try:
                snapshot.unlink()
            except OSError:
                pass


async def start_scheduler(db_path: Path) -> None:
    """
    Long-running asyncio task.  Waits one interval then runs backups on a schedule.
    Start this with asyncio.create_task() inside the FastAPI lifespan.
    """
    global _next_at

    interval_secs = INTERVAL_HOURS * 3600
    next_run = datetime.now(timezone.utc) + timedelta(seconds=interval_secs)
    _next_at = next_run.isoformat()
    log.info("Backup scheduler started — first run at %s", _next_at)

    while True:
        sleep_for = max(0.0, (next_run - datetime.now(timezone.utc)).total_seconds())
        await asyncio.sleep(sleep_for)

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, lambda: run_backup(db_path))

        next_run = datetime.now(timezone.utc) + timedelta(seconds=interval_secs)
        _next_at = next_run.isoformat()


def status() -> dict:
    return {
        "enabled": ENABLED,
        "destination": DESTINATION or None,
        "method": "rsync-ssh" if _is_remote(DESTINATION) else "copy",
        "interval_hours": INTERVAL_HOURS,
        "keep": KEEP,
        "last_backup_at": _last_at,
        "last_backup_ok": _last_ok,
        "last_backup_error": _last_error,
        "next_backup_at": _next_at,
    }
