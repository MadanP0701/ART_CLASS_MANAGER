r"""
One-time migration tool: copy the existing local SQLite business database
into the new persistent PostgreSQL database.

SAFE BY DEFAULT:
- The PostgreSQL database must be empty.
- Existing PostgreSQL rows cause the migration to stop.
- The SQLite source is read-only.
- IDs are preserved so existing relationships remain intact.

Usage:
  python migrate_sqlite_to_postgres.py --sqlite "C:\path\to\art_class.db"
  python migrate_sqlite_to_postgres.py --sqlite "..." --database-url "postgresql://..."

For Streamlit Cloud, do NOT put your DATABASE_URL in this file or commit it.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

from sqlalchemy import create_engine, text


TABLES = [
    "societies",
    "students",
    "attendance",
    "fee_charges",
    "payments",
    "payment_allocations",
]


def parse_args():
    p = argparse.ArgumentParser(description="Migrate Art Class Manager SQLite data to PostgreSQL.")
    p.add_argument("--sqlite", required=True, help="Path to the existing art_class.db")
    p.add_argument("--database-url", default="", help="Target PostgreSQL URL. Prefer DATABASE_URL environment variable.")
    p.add_argument("--yes", action="store_true", help="Confirm migration without interactive prompt.")
    return p.parse_args()


def sqlite_rows(path: Path, table: str):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(f"SELECT * FROM {table} ORDER BY id")]
    finally:
        con.close()


def main():
    args = parse_args()
    source = Path(args.sqlite).expanduser().resolve()
    target_url = (args.database_url or os.getenv("DATABASE_URL", "")).strip()

    if not source.exists():
        raise SystemExit(f"Source SQLite database not found: {source}")
    if not target_url:
        raise SystemExit("Set DATABASE_URL or pass --database-url.")
    if not target_url.lower().startswith(("postgresql://", "postgres://")):
        raise SystemExit("The target must be a PostgreSQL DATABASE_URL.")

    # Avoid importing the app's database module until the target URL is known.
    os.environ["DATABASE_URL"] = target_url
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import database as db

    db.init_db()

    counts = {table: len(sqlite_rows(source, table)) for table in TABLES}
    print("Source SQLite rows:")
    for table, count in counts.items():
        print(f"  {table}: {count}")

    with db.engine.begin() as conn:
        target_counts = {
            table: int(conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one())
            for table in TABLES
        }

    occupied = {k: v for k, v in target_counts.items() if v}
    if occupied:
        raise SystemExit(
            "Migration stopped: target PostgreSQL database is not empty: "
            + ", ".join(f"{k}={v}" for k, v in occupied.items())
            + ". Use a brand-new/empty database for this one-time migration."
        )

    total = sum(counts.values())
    if total == 0:
        raise SystemExit("The SQLite database contains no business records.")

    print(f"\nAbout to copy {total} rows into PostgreSQL.")
    print("The SQLite file will NOT be modified.")
    if not args.yes:
        answer = input('Type MIGRATE to continue: ').strip()
        if answer != "MIGRATE":
            raise SystemExit("Migration cancelled.")

    with db.engine.begin() as conn:
        for table in TABLES:
            rows = sqlite_rows(source, table)
            if not rows:
                continue

            columns = list(rows[0].keys())
            placeholders = ", ".join(f":{c}" for c in columns)
            col_sql = ", ".join(columns)
            stmt = text(f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders})")

            prepared = []
            for row in rows:
                row = dict(row)
                if "active" in row and row["active"] is not None:
                    row["active"] = bool(row["active"])
                if "present" in row and row["present"] is not None:
                    row["present"] = bool(row["present"])
                prepared.append(row)

            conn.execute(stmt, prepared)
            print(f"  migrated {len(rows):>6} rows -> {table}")

        # PostgreSQL identity sequences must continue after the preserved IDs.
        for table in TABLES:
            conn.execute(
                text(
                    f"""SELECT setval(
                        pg_get_serial_sequence('{table}', 'id'),
                        COALESCE((SELECT MAX(id) FROM {table}), 0) + 1,
                        false
                    )"""
                )
            )

    # Final verification.
    with db.engine.connect() as conn:
        final_counts = {
            table: int(conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one())
            for table in TABLES
        }

    if final_counts != counts:
        raise SystemExit(
            f"Verification failed. Expected {counts}, got {final_counts}."
        )

    print("\nSUCCESS: all rows were migrated and verified.")
    print("Next step: put this PostgreSQL DATABASE_URL into Streamlit Secrets.")
    print("Do not upload the SQLite file or the DATABASE_URL to GitHub.")


if __name__ == "__main__":
    main()
