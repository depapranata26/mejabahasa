#!/usr/bin/env python3
"""Seed MejaBahasa's SQLite database from data/schema.sql + demo/menu.json.

Usage:
  python3 backend/seed.py [db-path]

Default db path: data/mejabahasa.db (repo root). Existing tables are kept;
menu rows are inserted or replaced by id, so re-running is safe. The .db
file is git-ignored and must never be committed.
"""
import json
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def create_db(db_path, schema_path=None, menu_path=None):
    schema_path = Path(schema_path or REPO_ROOT / "data" / "schema.sql")
    menu_path = Path(menu_path or REPO_ROOT / "demo" / "menu.json")
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    schema = schema_path.read_text(encoding="utf-8")
    menu = json.loads(menu_path.read_text(encoding="utf-8"))
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(schema)
        conn.executemany(
            "INSERT OR REPLACE INTO menu_items"
            " (id, name, category, price_idr, stock, description)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    it["id"],
                    it["name"],
                    it["category"],
                    it["price_idr"],
                    it["stock"],
                    it.get("description", ""),
                )
                for it in menu["items"]
            ],
        )
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM menu_items").fetchone()[0]
    finally:
        conn.close()
    return count


def main(argv):
    db_path = argv[1] if len(argv) > 1 else REPO_ROOT / "data" / "mejabahasa.db"
    count = create_db(db_path)
    print(f"seeded {db_path}: {count} menu items")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
