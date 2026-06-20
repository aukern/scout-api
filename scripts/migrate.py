#!/usr/bin/env python3
"""Simple SQL migration runner for scout-api.

Tracks applied migrations in a ``schema_migrations`` table.
Runs all ``migrations/NNN_*.sql`` files in numerical order, skipping
any that have already been applied. Idempotent — safe to run on
every container start.

Usage:
    python scripts/migrate.py                # apply pending migrations
    python scripts/migrate.py --status       # show applied/pending
    python scripts/migrate.py --rollback 1   # roll back N migrations

Requires DATABASE_URL environment variable.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from pathlib import Path


DATABASE_URL = os.environ.get("DATABASE_URL", "")
MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"


def _migration_files() -> list[tuple[int, str, Path]]:
    """Return sorted list of (seq_num, name, path) for NNN_*.sql migration files."""
    pattern = re.compile(r"^(\d+)_(.+)\.sql$")
    results = []
    for f in MIGRATIONS_DIR.glob("*.sql"):
        m = pattern.match(f.name)
        if m:
            results.append((int(m.group(1)), m.group(2), f))
    return sorted(results)


async def _run(status: bool = False, rollback: int = 0) -> None:
    import asyncpg  # noqa: PLC0415

    if not DATABASE_URL:
        print("ERROR: DATABASE_URL environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        # Bootstrap migrations tracking table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                seq_num     INTEGER PRIMARY KEY,
                name        TEXT    NOT NULL,
                applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)

        applied = {
            row["seq_num"]
            for row in await conn.fetch(
                "SELECT seq_num FROM schema_migrations ORDER BY seq_num"
            )
        }

        all_files = _migration_files()

        if status:
            print(f"{'SEQ':>5}  {'STATUS':<10}  NAME")
            print("-" * 40)
            for seq, name, _ in all_files:
                state = "applied" if seq in applied else "pending"
                print(f"{seq:>5}  {state:<10}  {name}")
            return

        if rollback > 0:
            # Rollback is not supported with simple (non-up/down) migrations
            print(
                "Rollback not supported for plain SQL migrations. "
                "Run DDL manually.",
                file=sys.stderr,
            )
            sys.exit(1)

        # Apply pending migrations
        pending = [
            (seq, name, path)
            for seq, name, path in all_files
            if seq not in applied
        ]
        if not pending:
            print("No pending migrations.")
            return

        for seq, name, path in pending:
            sql = path.read_text()
            print(f"Applying {seq:04d}_{name}...")
            await conn.execute(sql)
            await conn.execute(
                "INSERT INTO schema_migrations (seq_num, name) VALUES ($1, $2)",
                seq,
                name,
            )
            print(f"  applied {seq:04d}_{name}")

        print(f"Done. Applied {len(pending)} migration(s).")

    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run SQL migrations")
    parser.add_argument("--status", action="store_true", help="Show migration status")
    parser.add_argument(
        "--rollback",
        type=int,
        default=0,
        metavar="N",
        help="Roll back N migrations (not supported for plain SQL migrations)",
    )
    args = parser.parse_args()
    asyncio.run(_run(status=args.status, rollback=args.rollback))


if __name__ == "__main__":
    main()
