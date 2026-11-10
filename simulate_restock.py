"""Step 5b: create SIMULATED restock requests for testing Agent 1.

Each store asks for 15-25 products it actually sold in August. About 20% of the requests are for products
the warehouse has ZERO stock of, so Agent 1 can later mark them 'out_of_stock'.
All requests start as 'pending'. Fixed random seed, so every run gives the same requests.

Run from the project folder (after simulate_data.py):   python simulate_restock.py
"""
import datetime
import random
import sqlite3
from pathlib import Path

DB_FILE = Path(__file__).resolve().parent / "data" / "retail.db"
SEED = 42
REQUEST_DATE = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()   # requests were raised yesterday
ITEMS_PER_STORE = {"Store A": 30, "Store B": 25, "Store C": 21, "Store D": 14,       # how many items each store asks for
                   "Store E": 34, "Store F": 31, "Store G": 39, "Store H": 40}
STAFF = {"Store A": ["Asha", "Ravi"], "Store B": ["Divya", "Imran"], "Store C": ["Kiran", "Latha"], "Store D": ["Manoj", "Neha"],
         "Store E": ["Omkar", "Priya"], "Store F": ["Qadir", "Rekha"], "Store G": ["Suresh", "Tara"], "Store H": ["Uday", "Vani"]}
NOTES = ["Big order - please hold this quantity", "Needed for the weekend sale", "Customer demand is high", "Display is empty"]
BIG_ORDER_SHARE = 0.08
OUT_OF_STOCK_SHARE = 0.20


def main() -> None:
    rng = random.Random(SEED)
    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        if conn.execute("SELECT COUNT(*) FROM restock_requests").fetchone()[0] > 0:
            raise SystemExit("Restock requests already exist. Delete data\\retail.db and rebuild for a fresh start.")

        stores = conn.execute("SELECT store_id, store_code FROM stores ORDER BY store_id").fetchall()
        with conn:
            for store_id, store_code in stores:
                requested_time = f"{rng.randint(15, 18):02d}:{rng.randint(0, 59):02d}:00"      # one time per store (the whole list is raised together)
                # Products this store sold in August, with units sold and warehouse stock.
                sold = conn.execute("""
                    SELECT s.product_id, SUM(s.quantity) AS units, COALESCE(w.available_qty, 0) AS in_stock
                    FROM sales s
                    JOIN products p ON p.product_id = s.product_id AND p.is_active = 1
                    LEFT JOIN warehouse_stock w ON w.product_id = s.product_id
                    WHERE s.store_id = ?
                    GROUP BY s.product_id HAVING units > 0""", (store_id,)).fetchall()
                have = [r for r in sold if r[2] > 0]
                none = [r for r in sold if r[2] == 0]

                n = ITEMS_PER_STORE[store_code]
                n_none = min(round(n * OUT_OF_STOCK_SHARE), len(none))
                chosen = rng.sample(none, n_none) + rng.sample(have, min(n - n_none, len(have)))

                for product_id, units, _stock in chosen:
                    qty = max(2, round(units * rng.uniform(0.5, 1.5)))   # roughly what it sold, give or take
                    note = rng.choice(NOTES) if rng.random() < BIG_ORDER_SHARE else None
                    conn.execute(
                        "INSERT INTO restock_requests (request_date, requested_at, store_id, product_id, requested_qty, requested_by, request_note) "
                        "VALUES (?,?,?,?,?,?,?)", (REQUEST_DATE, f"{REQUEST_DATE} {requested_time}", store_id, product_id, qty, rng.choice(STAFF[store_code]), note))

            conn.execute(
                "INSERT INTO data_quality_log (issue, affected_count, action_taken) VALUES (?,?,?)",
                ("Restock requests are not in the source data",
                 conn.execute("SELECT COUNT(*) FROM restock_requests").fetchone()[0],
                 "SIMULATED: ~20% for zero-stock products so Agent 1 can mark them out_of_stock"))

        print(f"Restock requests dated {REQUEST_DATE}:\n")
        for code, total, zero in conn.execute("""
                SELECT st.store_code, COUNT(*), SUM(COALESCE(w.available_qty, 0) = 0)
                FROM restock_requests r
                JOIN stores st ON st.store_id = r.store_id
                LEFT JOIN warehouse_stock w ON w.product_id = r.product_id
                GROUP BY st.store_code ORDER BY st.store_code"""):
            print(f"  {code}: {total} items, {zero} with zero warehouse stock")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
