#!/usr/bin/env python3
"""
Remove duplicate rows left behind by retried OTLP exports.

An OpenTelemetry exporter retries a 5xx by resending the identical batch. Until
the unique index on the identity hash exists, those resends were stored again,
inflating cost and token totals permanently. The index cannot be created while
duplicates are present, so this script clears them out first.

It covers both ingest tables:

    usage_events   duplicated by event_hash  — inflates cost and tokens
    metric_points  duplicated by point_hash  — inflates commits, lines,
                                               active time and every ratio
                                               derived from them

It reports before it deletes. Nothing is removed without --apply.

    python dedupe.py                # report only
    python dedupe.py --apply        # delete, keeping the earliest row of each set

Take a backup first — see docs/backup-and-restore.md.
"""

import argparse
import sqlite3
import sys

from app import db

# (table, hash column, a cost-ish column to quantify the damage, its label)
TABLES = (
    ("usage_events", "event_hash", "cost_usd", "cost double-counted", "$"),
    ("metric_points", "point_hash", "value", "metric value double-counted", ""),
)


def _report_table(conn: sqlite3.Connection, table: str, column: str,
                  weight: str, label: str, unit: str) -> list[sqlite3.Row] | None:
    """Print what is duplicated in one table. None means 'cannot proceed yet'."""
    columns = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        # A database that predates the hash column. init_db() adds it and
        # backfills, which is the same instruction as an unbackfilled row.
        print(f"\n{table}: no {column} column yet. Start the service once to add and")
        print("backfill it, then re-run this script.")
        return None

    missing = conn.execute(
        f"SELECT COUNT(*) AS n FROM {table} WHERE {column} IS NULL"
    ).fetchone()["n"]
    if missing:
        print(f"\n{table}: {missing} row(s) have no {column} yet. Start the service")
        print("once to backfill them, then re-run this script.")
        return None

    groups = conn.execute(
        f"""
        SELECT {column} AS hash, COUNT(*) AS n, MIN(id) AS keep_id,
               COALESCE(SUM({weight}), 0) AS group_weight
          FROM {table}
         GROUP BY {column}
        HAVING n > 1
         ORDER BY n DESC
        """
    ).fetchall()

    if not groups:
        print(f"\n{table}: no duplicates.")
        return []

    extra = sum(g["n"] - 1 for g in groups)
    wasted = sum(g["group_weight"] * (g["n"] - 1) / g["n"] for g in groups)
    print(f"\n{table}: {len(groups)} duplicated record(s), {extra} redundant row(s).")
    print(f"  Approximate {label}: {unit}{wasted:.4f}")
    print("  Worst offenders:")
    for g in groups[:10]:
        print(f"    {g['hash'][:16]}…  x{g['n']}  keeping id={g['keep_id']}")
    return groups


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--apply", action="store_true",
                        help="actually delete (default: report only)")
    args = parser.parse_args()

    print(f"Database: {db.DB_PATH}")
    if not db.DB_PATH.exists():
        print("No database at that path. Set DB_PATH if it lives elsewhere.")
        return 1

    conn = sqlite3.connect(db.DB_PATH)
    conn.row_factory = sqlite3.Row

    blocked = False
    dirty: list[tuple[str, str]] = []
    for table, column, weight, label, unit in TABLES:
        # A database written before metric_points existed will not have it.
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
        if not exists:
            continue
        groups = _report_table(conn, table, column, weight, label, unit)
        if groups is None:
            blocked = True
        elif groups:
            dirty.append((table, column))

    if blocked:
        return 1
    if not dirty:
        print("\nNothing to clean. The unique indexes will be created on next startup.")
        return 0

    if not args.apply:
        print("\nReport only. Re-run with --apply to delete (back up first).")
        return 0

    with conn:
        for table, column in dirty:
            cur = conn.execute(
                f"DELETE FROM {table} "
                f"WHERE id NOT IN (SELECT MIN(id) FROM {table} GROUP BY {column})"
            )
            print(f"\nRemoved {cur.rowcount} row(s) from {table}.")
    print("\nRestart ingest to create the unique indexes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
