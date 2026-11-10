"""Step 4: load the 8 stores and the August 2025 sales into the database.

Run from the project folder (after load_catalog.py):   python load_stores_sales.py
"""
import re
import sqlite3
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "data" / "raw"
DB_FILE = ROOT / "data" / "retail.db"
SALES_FILE = "Aug2025_AllStores_ProductSalesReport.csv.csv"
MAPPING_FILE = ROOT / "data" / "store_mapping.csv"   # real outlet -> Store A..H (keep OUT of GitHub)


def outlet_sort_key(name: str) -> str:
    """'Brand - Place Name' -> 'place name', so outlets sort by their place name (a name without ' - ' is used as it is)."""
    name = re.sub(r"^[^-]*-\s*", "", name.strip()) if "-" in name else re.sub(r"^\s*\w+\s+", "", name.strip())
    return re.sub(r"\s+", " ", name).lower()


def main() -> None:
    sales = pd.read_csv(RAW / SALES_FILE, dtype=str)

    # Tidy outlet names (one outlet has a double space) and keep only the columns we need.
    sales["POS Outlet"] = sales["POS Outlet"].str.replace(r"\s+", " ", regex=True).str.strip()
    sales["SKU"] = sales["SKU"].str.strip()
    # Customer-level columns (invoice number etc.) are deliberately dropped: agents need product-level sales only.
    sales["sale_date"] = pd.to_datetime(sales["Sales Date"], format="%d/%m/%y").dt.strftime("%Y-%m-%d")
    sales["quantity"] = pd.to_numeric(sales["Quantity"]).round().astype(int)
    sales["net_amount"] = pd.to_numeric(sales["Net Total Amt (Rs.)"])

    # Store A, B, C ... in alphabetical order of the real outlet name.
    outlets = sorted(sales["POS Outlet"].unique(), key=outlet_sort_key)
    store_code = {o: f"Store {chr(ord('A') + i)}" for i, o in enumerate(outlets)}
    pd.DataFrame({"outlet": list(store_code), "store_code": list(store_code.values())}).to_csv(
        MAPPING_FILE, index=False)

    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        if conn.execute("SELECT COUNT(*) FROM stores").fetchone()[0] > 0:
            raise SystemExit("Stores already loaded. Delete data\\retail.db and rebuild for a fresh start.")

        with conn:  # one transaction: all or nothing
            conn.executemany("INSERT INTO stores (store_code) VALUES (?)", [(c,) for c in store_code.values()])
            store_id = dict(conn.execute("SELECT store_code, store_id FROM stores"))
            product_id = dict(conn.execute("SELECT sku, product_id FROM products"))

            sales["store_id"] = sales["POS Outlet"].map(store_code).map(store_id)
            sales["product_id"] = sales["SKU"].map(product_id)
            missing = int(sales["product_id"].isna().sum())
            if missing:
                raise SystemExit(f"{missing} sales rows have a SKU that is not in products. Stopping.")

            rows = list(zip(sales["sale_date"], sales["store_id"].astype(int), sales["product_id"].astype(int),
                            sales["quantity"], sales["net_amount"]))
            conn.executemany(
                "INSERT INTO sales (sale_date, store_id, product_id, quantity, net_amount) VALUES (?,?,?,?,?)",
                [(d, s, p, int(q), float(a)) for d, s, p, q, a in rows])

            negative = int((sales["quantity"] < 0).sum())
            conn.executemany(
                "INSERT INTO data_quality_log (issue, affected_count, action_taken) VALUES (?,?,?)",
                [("Sales rows with negative quantity (returns)", negative, "Kept as they are"),
                 ("Customer/invoice columns in the sales file", 1, "Not loaded (product-level sales only)")])

        print(f"Loaded {len(store_id)} stores and {len(rows)} sales rows.\n")
        for code, sid in store_id.items():
            n, units = conn.execute("SELECT COUNT(*), SUM(quantity) FROM sales WHERE store_id=?", (sid,)).fetchone()
            print(f"  {code}: {n} rows, {units} units")
        print(f"\nDates: {sales['sale_date'].min()} to {sales['sale_date'].max()}")
        print(f"Negative-quantity rows kept: {negative}")
        print(f"\nOutlet -> store mapping saved to {MAPPING_FILE} (do not commit it).")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
