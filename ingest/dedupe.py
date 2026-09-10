#!/usr/bin/env python3
"""
Remove duplicate usage_events left behind by retried OTLP exports.

An OpenTelemetry exporter retries a 5xx by resending the identical batch. Until
the unique index on event_hash exists, those resends were stored again, inflating
cost and token totals permanently. The index cannot be created while duplicates
are present, so this script clears them out first.

It reports before it deletes. Nothing is removed without --apply.

    python dedupe.py                # report only
    python dedupe.py --apply        # delete, keeping the earliest row of each set

Take a backup first — see docs/backup-and-restore.md.
"""

import argparse
import sqlite3
import sys

from app import db


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="actually delete (default: report only)")
    args = parser.parse_args()

    print(f"Database: {db.DB_PATH}")
    if not db.DB_PATH.exists():
        print("No database at that path. Set DB_PATH if it lives elsewhere.")
        return 1

    conn = sqlite3.connect(db.DB_PATH)
    conn.row_factory = sqlite3.Row

    missing = conn.execute(
        "SELECT COUNT(*) AS n FROM usage_events WHERE event_hash IS NULL"
    ).fetchone()["n"]
    if missing:
        print(f"\n{missing} row(s) have no event_hash yet. Start the service once to")
        print("backfill them, then re-run this script.")
        return 1

    groups = conn.execute(
        """
        SELECT event_hash, COUNT(*) AS n, MIN(id) AS keep_id,
               COALESCE(SUM(cost_usd), 0) AS group_cost
          FROM usage_events
         GROUP BY event_hash
        HAVING n > 1
         ORDER BY n DESC
        """
    ).fetchall()

    if not groups:
        print("\nNo duplicates. The unique index will be created on next startup.")
        return 0

    extra = sum(g["n"] - 1 for g in groups)
    wasted = sum(g["group_cost"] * (g["n"] - 1) / g["n"] for g in groups)
    print(f"\n{len(groups)} duplicated event(s), {extra} redundant row(s) to remove.")
    print(f"Approximate cost double-counted: ${wasted:.4f}")
    print("\nWorst offenders:")
    for g in groups[:10]:
        print(f"  {g['event_hash'][:16]}…  x{g['n']}  keeping id={g['keep_id']}")

    if not args.apply:
        print("\nReport only. Re-run with --apply to delete (back up first).")
        return 0

    with conn:
        cur = conn.execute(
            """
            DELETE FROM usage_events
             WHERE id NOT IN (SELECT MIN(id) FROM usage_events GROUP BY event_hash)
            """
        )
        removed = cur.rowcount
    print(f"\nRemoved {removed} row(s). Restart ingest to create the unique index.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
