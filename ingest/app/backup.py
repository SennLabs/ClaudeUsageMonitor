"""Periodic SQLite backup — copy to a local/mounted path or rsync over SSH to a NAS."""

import asyncio
import logging
import os
import re
import shutil
import sqlite3
import signal
import subprocess
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

# ── Config from environment ────────────────────────────────────────────────
# Backup is active when BACKUP_DESTINATION is non-empty AND the configuration
# validates. An invalid setting disables backups and is reported through
# /api/backup/status rather than stopping ingestion — collecting usage is the
# primary job — but it is never silently treated as working.
DESTINATION = os.environ.get("BACKUP_DESTINATION", "").strip()
SSH_KEY = os.environ.get("BACKUP_SSH_KEY", "").strip()
MODE = os.environ.get("BACKUP_MODE", "").strip().lower()  # "", "local", "rsync"

# user@host:/path — deliberately strict. The old heuristic was `"@" in dest and
# ":" in dest`, which classified `nas:/volume1/backups` (a perfectly ordinary
# SSH destination that omits the user) as LOCAL: it created a directory called
# `nas:` inside the container and reported every backup as succeeding, until a
# rebuild deleted the lot.
_REMOTE_RE = re.compile(r"^[^/@\s]+@[^/@\s:]+:")

CONFIG_ERROR: str | None = None


def _read_positive(name: str, default: str, minimum: float) -> float:
    raw = os.environ.get(name, default).strip() or default
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{name}={raw!r} is not a number")
    if value < minimum:
        raise ValueError(f"{name}={raw!r} is below the minimum of {minimum}")
    return value


def _resolve_mode(dest: str) -> str:
    if MODE in ("local", "rsync"):
        return MODE
    if MODE:
        raise ValueError(f"BACKUP_MODE={MODE!r} must be 'local' or 'rsync'")
    if _REMOTE_RE.match(dest):
        return "rsync"
    if dest.startswith(("/", "./", "../")):
        return "local"
    raise ValueError(
        f"cannot tell whether BACKUP_DESTINATION={dest!r} is a local path or an "
        "rsync target. Use an absolute path for local, user@host:/path for rsync, "
        "or set BACKUP_MODE=local|rsync explicitly."
    )


try:
    INTERVAL_HOURS = _read_positive("BACKUP_INTERVAL_HOURS", "24", 1 / 60)
    KEEP = int(_read_positive("BACKUP_KEEP", "7", 1))
    RESOLVED_MODE = _resolve_mode(DESTINATION) if DESTINATION else "local"
except ValueError as exc:
    INTERVAL_HOURS, KEEP, RESOLVED_MODE = 24.0, 7, "local"
    CONFIG_ERROR = str(exc)

ENABLED = bool(DESTINATION) and CONFIG_ERROR is None

# Only one backup at a time. The scheduler and POST /api/backup/trigger both
# used the default executor, so two runs in the same second opened two
# connections onto the same snapshot path and one deleted the file the other
# was still writing.
_RUN_LOCK = threading.Lock()

# How long after startup the first scheduled backup runs.
FIRST_RUN_DELAY_SECONDS = float(os.environ.get("BACKUP_FIRST_RUN_DELAY_SECONDS", "60"))


# ── Runtime state (reset on restart — that's fine for a small tool) ────────
_last_at: str | None = None
_last_ok: bool | None = None
_last_error: str | None = None
_next_at: str | None = None


def _is_remote(dest: str) -> bool:
    return RESOLVED_MODE == "rsync"


def _snapshot(db_path: Path) -> Path:
    """
    Create a consistent, point-in-time copy using sqlite3.backup().
    This is safe even with WAL mode active — no need to stop writes first.
    """
    # Second resolution alone collided when two runs started in the same second.
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    tmp = db_path.parent / f"usage_backup_{ts}_{uuid.uuid4().hex[:8]}.db"
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
    cmd += ["--", str(src), DESTINATION]  # -- so a destination starting with - is a path
    # start_new_session puts rsync and the ssh child it spawns in their own
    # process group, so a timeout kills both. Killing rsync alone leaves ssh
    # holding the captured pipes, and the read afterwards can block well past
    # the timeout against an unresponsive NAS.
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True
    )
    try:
        _, stderr = proc.communicate(timeout=300)
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        proc.communicate()
        raise RuntimeError("rsync timed out after 300s and was killed")
    if proc.returncode != 0:
        raise RuntimeError(stderr.strip() or f"rsync exited with code {proc.returncode}")


class BackupBusy(RuntimeError):
    """A backup is already running."""


def _copy_local(snapshot: Path, db_path: Path) -> None:
    dest_dir = Path(DESTINATION)

    # The snapshot is staged in the database directory, so a destination equal
    # to it makes shutil.copy2 raise SameFileError — which used to be swallowed
    # and reported as success while the good snapshot was deleted.
    if dest_dir.resolve() == db_path.parent.resolve():
        raise RuntimeError(
            f"BACKUP_DESTINATION ({DESTINATION}) is the database's own directory. "
            "A backup there would be deleted with the database it is meant to protect."
        )

    # Deliberately not mkdir -p. A typo'd or unmounted path used to be created
    # inside the container's writable layer, where every backup was reported as
    # succeeding right up until a rebuild deleted them all.
    if not dest_dir.is_dir():
        raise RuntimeError(
            f"BACKUP_DESTINATION ({DESTINATION}) does not exist or is not a directory. "
            "Create it, or check the volume is mounted — it is not created automatically."
        )

    shutil.copy2(snapshot, dest_dir / snapshot.name)
    _prune_local(dest_dir)


def run_backup(db_path: Path) -> None:
    """
    Execute one backup cycle synchronously. Returns normally whether it worked
    or not; the outcome is in status(). Raises BackupBusy if one is in flight.

    Intended to be called via asyncio.to_thread so it does not block the loop.
    """
    global _last_at, _last_ok, _last_error

    if not _RUN_LOCK.acquire(blocking=False):
        raise BackupBusy("a backup is already running")

    snapshot: Path | None = None
    try:
        if CONFIG_ERROR:
            raise RuntimeError(f"backup configuration is invalid: {CONFIG_ERROR}")
        if not DESTINATION:
            raise RuntimeError("BACKUP_DESTINATION is not configured")

        snapshot = _snapshot(db_path)

        if _is_remote(DESTINATION):
            _rsync(snapshot)
        else:
            _copy_local(snapshot, db_path)

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
        _RUN_LOCK.release()


async def start_scheduler(db_path: Path) -> None:
    """
    Long-running asyncio task.  Waits one interval then runs backups on a schedule.
    Start this with asyncio.create_task() inside the FastAPI lifespan.
    """
    global _next_at

    interval_secs = INTERVAL_HOURS * 3600

    # Run shortly after startup rather than after a full interval. With the
    # 24h default, a container redeployed nightly never backed up at all —
    # while /api/backup/status cheerfully reported enabled: true.
    next_run = datetime.now(timezone.utc) + timedelta(seconds=FIRST_RUN_DELAY_SECONDS)
    _next_at = next_run.isoformat()
    log.info(
        "Backup scheduler started (%s → %s, every %sh) — first run at %s",
        RESOLVED_MODE, DESTINATION, INTERVAL_HOURS, _next_at,
    )

    while True:
        sleep_for = max(0.0, (next_run - datetime.now(timezone.utc)).total_seconds())
        await asyncio.sleep(sleep_for)

        try:
            await asyncio.to_thread(run_backup, db_path)
        except BackupBusy:
            log.info("Scheduled backup skipped — one was already running")

        next_run = datetime.now(timezone.utc) + timedelta(seconds=interval_secs)
        _next_at = next_run.isoformat()


def status() -> dict:
    warnings = []
    if ENABLED and RESOLVED_MODE == "rsync":
        warnings.append(
            "BACKUP_KEEP does not apply to rsync destinations — nothing prunes the "
            "remote, so snapshots accumulate there until you clean them up."
        )
    return {
        "enabled": ENABLED,
        "destination": DESTINATION or None,
        "method": "rsync-ssh" if _is_remote(DESTINATION) else "copy",
        "interval_hours": INTERVAL_HOURS,
        "keep": KEEP if RESOLVED_MODE == "local" else None,
        "last_backup_at": _last_at,
        "last_backup_ok": _last_ok,
        "last_backup_error": _last_error,
        "next_backup_at": _next_at,
        "config_error": CONFIG_ERROR,
        "warnings": warnings,
    }
