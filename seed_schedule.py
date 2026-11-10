"""Fills the weekly delivery schedule (which stores the one vehicle visits on which weekday).

Monday = packing only, Friday = urgent restocks only, so neither has fixed stores.
To change the schedule later, edit SCHEDULE below and run:  python seed_schedule.py
"""
import sqlite3
from pathlib import Path

DB_FILE = Path(__file__).resolve().parent / "data" / "retail.db"

SCHEDULE = {            # weekday number: 1=Mon ... 5=Fri
    2: ["Store C", "Store D", "Store E", "Store G"],   # Tuesday - 3 very near stores + one far store (G)
    3: ["Store A", "Store B", "Store F"],    # Wednesday - top stores
    4: ["Store H"],                          # Thursday  - far store with traffic
}

conn = sqlite3.connect(DB_FILE)
conn.execute("PRAGMA foreign_keys = ON")
with conn:
    conn.execute("DELETE FROM trip_schedule")
    for day, codes in SCHEDULE.items():
        for code in codes:
            row = conn.execute("SELECT store_id FROM stores WHERE store_code = ?", (code,)).fetchone()
            if row is None:
                raise SystemExit(f"Unknown store in SCHEDULE: {code}")
            conn.execute("INSERT INTO trip_schedule (weekday, store_id) VALUES (?, ?)", (day, row[0]))
print("Delivery schedule saved:")
for day, codes in SCHEDULE.items():
    print(f"  {['Mon','Tue','Wed','Thu','Fri'][day-1]}: {', '.join(codes)}")
print("  Mon: packing only | Fri: urgent restocks (any store with packed, unassigned items)")
