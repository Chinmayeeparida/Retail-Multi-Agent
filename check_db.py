"""Quick health check of data/retail.db. Run:  python check_db.py"""
import sqlite3
from pathlib import Path

db = Path(__file__).resolve().parent / "data" / "retail.db"
conn = sqlite3.connect(db)
conn.execute("PRAGMA foreign_keys = ON")

print("Rows per table:")
for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall():
    print(f"  {t:18}", conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])

print("\nCosmetics prefix       :", conn.execute("SELECT sku_prefix FROM categories WHERE category_name='Cosmetics'").fetchone()[0])
print("Products without vendor:", conn.execute("SELECT COUNT(*) FROM products WHERE vendor_id IS NULL").fetchone()[0])
print("Stock units in database:", conn.execute("SELECT SUM(available_qty) FROM warehouse_stock").fetchone()[0])
print("Foreign-key problems   :", conn.execute("PRAGMA foreign_key_check").fetchall())
print("Integrity check        :", conn.execute("PRAGMA integrity_check").fetchone()[0])
conn.close()
