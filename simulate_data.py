"""Step 5a: add SIMULATED data the real files don't contain.

  1. Vendors: GSTIN, registered date, India flag.
  2. Warehouse racks: a rack name like 'R03-B-07' for every product that has stock.

All of this is made up (with a fixed random seed, so every run gives the same result).
Run from the project folder (after load_stores_sales.py):   python simulate_data.py
"""
import random
import sqlite3
import string
from pathlib import Path

DB_FILE = Path(__file__).resolve().parent / "data" / "retail.db"
SEED = 42
STATE_CODE = "29"            # Karnataka (the stores are in Bengaluru)
SHELVES = "ABCD"             # A = easiest to reach (waist height), D = hardest
POSITIONS_PER_SHELF = 10
RACK_CAPACITY = len(SHELVES) * POSITIONS_PER_SHELF   # 40 products per rack


def fake_gstin(rng: random.Random) -> str:
    """15 characters in the real GSTIN layout: state(2) + PAN-like(10) + entity(1) + 'Z' + check(1). Not real."""
    pan = ("".join(rng.choices(string.ascii_uppercase, k=5)) + "".join(rng.choices(string.digits, k=4))
           + rng.choice(string.ascii_uppercase))
    return f"{STATE_CODE}{pan}{rng.randint(1, 9)}Z{rng.choice(string.ascii_uppercase + string.digits)}"


def simulate_vendors(conn: sqlite3.Connection, rng: random.Random) -> int:
    vendors = conn.execute("SELECT vendor_id FROM vendors ORDER BY vendor_id").fetchall()
    for (vendor_id,) in vendors:
        year, month, day = rng.randint(2015, 2024), rng.randint(1, 12), rng.randint(1, 28)
        conn.execute(
            "UPDATE vendors SET vendor_gstin = ?, registered_date = ?, is_india_vendor = 1 WHERE vendor_id = ?",
            (fake_gstin(rng), f"{year:04d}-{month:02d}-{day:02d}", vendor_id))
    return len(vendors)


def simulate_racks(conn: sqlite3.Connection) -> tuple[int, int]:
    """Group products by category (each category gets its own racks); inside a category the best sellers
    go first, so they land on the lowest rack number and the easiest shelf."""
    rows = conn.execute("""
        SELECT w.product_id, c.category_name,
               COALESCE((SELECT SUM(s.quantity) FROM sales s WHERE s.product_id = w.product_id), 0) AS sold
        FROM warehouse_stock w
        JOIN products p ON p.product_id = w.product_id
        LEFT JOIN categories c ON c.category_id = p.category_id
        WHERE w.available_qty > 0
        ORDER BY c.category_name, sold DESC, w.product_id""").fetchall()

    by_category: dict[str, list[int]] = {}
    for product_id, category, _sold in rows:
        by_category.setdefault(category or "Uncategorised", []).append(product_id)

    conn.execute("UPDATE warehouse_stock SET rack_name = NULL")
    next_rack, assigned = 1, 0
    for category in sorted(by_category):
        for i, product_id in enumerate(by_category[category]):
            rack = next_rack + i // RACK_CAPACITY
            slot = i % RACK_CAPACITY
            name = f"R{rack:02d}-{SHELVES[slot // POSITIONS_PER_SHELF]}-{slot % POSITIONS_PER_SHELF + 1:02d}"
            conn.execute("UPDATE warehouse_stock SET rack_name = ? WHERE product_id = ?", (name, product_id))
            assigned += 1
        next_rack += -(-len(by_category[category]) // RACK_CAPACITY)   # round up: next category starts a new rack
    return assigned, next_rack - 1


def main() -> None:
    rng = random.Random(SEED)
    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        with conn:  # one transaction
            n_vendors = simulate_vendors(conn, rng)
            n_products, n_racks = simulate_racks(conn)
            conn.execute("DELETE FROM data_quality_log WHERE action_taken LIKE 'SIMULATED%'")  # safe to re-run
            conn.executemany(
                "INSERT INTO data_quality_log (issue, affected_count, action_taken) VALUES (?,?,?)",
                [("Vendor GSTIN, registered date and India flag are not in the source data", n_vendors,
                  "SIMULATED (fixed seed 42); all vendors set to India"),
                 ("Rack names are not in the source data", n_products,
                  "SIMULATED: grouped by category, best sellers on the lowest racks and easiest shelves")])

        print(f"Vendors updated with simulated GSTIN / date / India flag: {n_vendors}")
        print(f"Products given a rack: {n_products}, across {n_racks} racks\n")
        print("Examples (product, rack, units sold in August):")
        for name, rack, sold in conn.execute("""
                SELECT p.product_name, w.rack_name,
                       (SELECT SUM(quantity) FROM sales s WHERE s.product_id = p.product_id)
                FROM warehouse_stock w JOIN products p USING (product_id)
                WHERE w.rack_name IS NOT NULL ORDER BY w.rack_name LIMIT 5"""):
            print(f"  {rack}  {name[:45]:45}  sold {sold}")
        print("\nVendor example:", conn.execute(
            "SELECT vendor_name, vendor_gstin, registered_date FROM vendors LIMIT 1").fetchone())
    finally:
        conn.close()


if __name__ == "__main__":
    main()
