"""
One-time migration: SQLite -> JSON -> PostgreSQL.

Usage:
  1. Export: python migrate_to_postgres.py export
  2. Set DATABASE_URL in .env to your PostgreSQL connection string
  3. Import: python migrate_to_postgres.py import
"""
import json
import os
import sys
import sqlite3
import asyncio
from datetime import datetime
from pathlib import Path

DB_DIR = Path(__file__).resolve().parent.parent
SQLITE_PATH = DB_DIR / "bot_local.db"
JSON_PATH = DB_DIR / "scripts" / "migration_export.json"

TABLES = [
    "users",
    "credit_templates",
    "draft_menu_buttons",
    "broadcasts",
    "broadcast_recipients",
    "production_menu_buttons",
]


def export_sqlite():
    """Export all data from SQLite to a JSON file."""
    if not SQLITE_PATH.exists():
        print(f"ERROR: SQLite database not found at {SQLITE_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(str(SQLITE_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    data = {}
    for table in TABLES:
        cur.execute(f"SELECT * FROM {table}")
        rows = [dict(row) for row in cur.fetchall()]
        for row in rows:
            for k, v in row.items():
                if isinstance(v, (datetime,)):
                    row[k] = v.isoformat()
        data[table] = rows
        print(f"  {table}: {len(rows)} rows")

    conn.close()

    JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\nOK: Exported to {JSON_PATH}")


async def import_postgres():
    """Import data from JSON into PostgreSQL via SQLAlchemy."""
    if not JSON_PATH.exists():
        print(f"ERROR: No export file found at {JSON_PATH}. Run 'export' first.")
        sys.exit(1)

    with open(JSON_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Load the async engine from the app's db module
    sys.path.insert(0, str(DB_DIR))
    from db import engine, AsyncSessionLocal, Base, User, CreditTemplate, DraftMenuButton, Broadcast, BroadcastRecipient, ProductionMenuButton

    MODEL_MAP = {
        "users": User,
        "credit_templates": CreditTemplate,
        "draft_menu_buttons": DraftMenuButton,
        "broadcasts": Broadcast,
        "broadcast_recipients": BroadcastRecipient,
        "production_menu_buttons": ProductionMenuButton,
    }

    COLUMN_MAP = {
        "users": ["user_id", "username", "first_name", "is_admin", "joined_at"],
        "credit_templates": ["id", "name", "text"],
        "draft_menu_buttons": ["id", "parent_id", "title", "button_type", "order_index", "button_style", "source_chat_id", "source_message_id", "credit_text", "extra_sources"],
        "broadcasts": ["id", "text", "parse_mode", "sent_at", "sent_count", "fail_count"],
        "broadcast_recipients": ["id", "broadcast_id", "user_id", "chat_id", "message_id"],
        "production_menu_buttons": ["id", "parent_id", "title", "button_type", "order_index", "button_style", "source_chat_id", "source_message_id", "credit_text", "extra_sources"],
    }

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("OK: Tables created in PostgreSQL")

    async with AsyncSessionLocal() as session:
        for table_name in TABLES:
            model = MODEL_MAP[table_name]
            cols = COLUMN_MAP[table_name]
            rows = data.get(table_name, [])

            for row in rows:
                kwargs = {col: row.get(col) for col in cols if col in row}
                # Parse datetime strings
                for col in ("joined_at", "sent_at"):
                    if col in kwargs and isinstance(kwargs[col], str):
                        kwargs[col] = datetime.fromisoformat(kwargs[col])
                session.add(model(**kwargs))

            print(f"  {table_name}: {len(rows)} rows")

        await session.commit()

    # Fix autoincrement sequences (PostgreSQL sequences don't auto-update on manual inserts)
    print("\nFixing sequences...")
    from sqlalchemy import text
    seq_tables = ["draft_menu_buttons", "credit_templates", "broadcasts", "broadcast_recipients"]
    async with AsyncSessionLocal() as sess:
        for table in seq_tables:
            max_id = await sess.scalar(text(f"SELECT MAX(id) FROM {table}"))
            if max_id is not None:
                await sess.execute(text(f"SELECT setval('{table}_id_seq', {max_id})"))
                print(f"  {table}: sequence set to {max_id}")
            else:
                print(f"  {table}: empty, skipped")
        await sess.commit()

    print(f"\nOK: Imported {sum(len(data.get(t, [])) for t in TABLES)} total rows into PostgreSQL")

    await engine.dispose()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1]

    if command == "export":
        print("--- Exporting from SQLite ---")
        export_sqlite()
    elif command == "import":
        print("--- Importing into PostgreSQL ---")
        asyncio.run(import_postgres())
    else:
        print(f"Unknown command: {command}")
        print(__doc__)
        sys.exit(1)
