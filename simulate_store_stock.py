"""Step 5c: create SIMULATED store stock (what each store has on its shelves) so Agent 1 can use 'Store qty'.

For every product a store sold in the 30 days of data, the store holds some days of cover:
  store_qty = average daily sales x days of cover.   Days of cover is random: about 15% of items are sold out at the store (0),
  most have 1-8 days of cover, a few are well stocked (up to 20 days). Fixed random seed, so every run gives the same numbers.

Run from the project folder (after simulate_restock.py):   python simulate_store_stock.py
"""
import random
import sqlite3
from pathlib import Path

DB_FILE = Path(__file__).resolve().parent / "data" / "retail.db"
SEED = 42
SNAPSHOT_DATE = "2025-08-28"          # same day as the warehouse stock snapshot
FIRST_DAY, LAST_DAY, DAYS = "2025-08-02", "2025-08-31", 30
SOLD_OUT_SHARE = 0.15


def main() -> None:
    rng = random.Random(SEED)
    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        with conn:
            conn.execute("DELETE FROM store_stock")          # safe to re-run
            rows = conn.execute("""SELECT store_id, product_id, SUM(quantity) FROM sales
                                   WHERE sale_date BETWEEN ? AND ? GROUP BY store_id, product_id HAVING SUM(quantity) > 0
                                   ORDER BY store_id, product_id""", (FIRST_DAY, LAST_DAY)).fetchall()
            made = []
            for store_id, product_id, sold in rows:
                daily = sold / DAYS
                if rng.random() < SOLD_OUT_SHARE:
                    cover = 0
                elif rng.random() < 0.8:
                    cover = rng.uniform(1, 8)
                else:
                    cover = rng.uniform(8, 20)
                made.append((store_id, product_id, SNAPSHOT_DATE, round(daily * cover)))
            conn.executemany("INSERT INTO store_stock VALUES (?,?,?,?)", made)
            conn.execute("DELETE FROM data_quality_log WHERE issue LIKE 'Store stock%'")
            conn.execute("INSERT INTO data_quality_log (issue, affected_count, action_taken) VALUES (?,?,?)",
                         ("Store stock is not in the source data", len(made),
                          "SIMULATED (fixed seed 42): daily sales x random days of cover; ~15% sold out at the store"))
        print(f"Store stock rows (SIMULATED): {len(made):,}\n")
        print("Store     products  units on shelf  sold out (0)")
        for code, n, units, zero in conn.execute("""SELECT st.store_code, COUNT(*), SUM(k.store_qty), SUM(k.store_qty = 0)
                FROM store_stock k JOIN stores st USING (store_id) GROUP BY st.store_code ORDER BY st.store_code"""):
            print(f"{code:8} {n:9,} {units:15,} {zero:13,}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
