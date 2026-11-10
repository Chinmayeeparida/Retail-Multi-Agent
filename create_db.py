"""Step 2: create the empty retail database from schema.sql.

Run from the project folder:   python create_db.py
"""
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCHEMA_FILE = ROOT / "schema.sql"
DB_FILE = ROOT / "data" / "retail.db"


def create_database(db_path: Path = DB_FILE, schema_path: Path = SCHEMA_FILE) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Safety: never overwrite an existing database by accident.
    if db_path.exists():
        raise SystemExit(f"{db_path} already exists. Delete it first for a fresh start.")

    conn = sqlite3.connect(db_path)
    try:
        # SQLite only enforces foreign keys when you switch this on, per connection.
        conn.execute("PRAGMA foreign_keys = ON")

        # Run every CREATE TABLE statement in schema.sql.
        conn.executescript(schema_path.read_text(encoding="utf-8"))
        conn.commit()

        # Show what was built.
        tables = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        print(f"Created {db_path} with {len(tables)} tables:\n")
        for (name,) in tables:
            columns = [row[1] for row in conn.execute(f"PRAGMA table_info({name})")]
            print(f"  {name}: {', '.join(columns)}")
    finally:
        conn.close()


if __name__ == "__main__":
    create_database()
