"""Step 10: the WAREHOUSE STAFF, played by a script (a "simulated human").

Two jobs, in the order a real warehouse does them:
    python simulate_warehouse.py labels    print the barcode labels that Agent 1 requested
    python simulate_warehouse.py pack      pack the items, rack by rack, and record what really happened
    python simulate_warehouse.py           do both, in that order

RULE (a guardrail): nothing is packed until its barcode labels have been printed.
Later, the Barcode Request and Warehouse Packaging screens (Step 15) replace this script.
Real staff are not perfect, so a few items come out short or damaged. Fixed seed = same result every run.
"""
import random
import sqlite3
import sys
from pathlib import Path

DB_FILE = Path(__file__).resolve().parent / "data" / "retail.db"
SEED = 42


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def print_labels() -> None:
    conn = connect()
    with conn:
        waiting = conn.execute("""
            SELECT b.barcode_request_id, s.store_code, b.total_labels
            FROM barcode_requests b JOIN stores s USING (store_id)
            WHERE b.status = 'requested' ORDER BY b.barcode_request_id""").fetchall()
        for r in waiting:
            conn.execute("UPDATE barcode_requests SET status='printed', printed_at=datetime('now', 'localtime') "
                         "WHERE barcode_request_id = ?", (r["barcode_request_id"],))
            print(f"  Printed {r['total_labels']:>4} labels for {r['store_code']}")
    conn.close()
    print(f"Barcode requests printed: {len(waiting)}" if waiting else "No barcode requests are waiting.")


def pack_items() -> None:
    rng = random.Random(SEED)
    conn = connect()
    with conn:
        items = conn.execute("""
            SELECT i.pack_id, i.qty_to_pack, s.store_code
            FROM packing_items i
            JOIN barcode_requests b USING (barcode_request_id)
            JOIN stores s ON s.store_id = i.store_id
            WHERE i.pack_status = 'to_pack' AND b.status = 'printed'
            ORDER BY i.store_id, i.rack_name, i.pack_id""").fetchall()
        blocked = conn.execute("""
            SELECT COUNT(*) FROM packing_items i JOIN barcode_requests b USING (barcode_request_id)
            WHERE i.pack_status = 'to_pack' AND b.status = 'requested'""").fetchone()[0]

        for it in items:
            qty, roll = it["qty_to_pack"], rng.random()
            packed, damaged, note = qty, 0, None
            if roll < 0.02:                                    # 2%: item not at its rack
                packed, note = 0, "Item not found at rack"
            elif roll < 0.06:                                  # 4%: some units damaged
                damaged = max(1, round(qty * 0.2))
                damaged = min(damaged, qty)
                packed, note = qty - damaged, f"{damaged} unit(s) damaged"
            elif roll < 0.12:                                  # 6%: fewer units on the shelf than expected
                packed = int(qty * rng.uniform(0.5, 0.9))
                note = "Fewer units on shelf than expected"
            status = "packed" if packed == qty else "short"
            conn.execute("""UPDATE packing_items SET packed_qty = ?, damaged_qty = ?, pack_status = ?,
                            staff_note = ?, packed_at = datetime('now', 'localtime') WHERE pack_id = ?""",
                         (packed, damaged, status, note, it["pack_id"]))

        report = conn.execute("""
            SELECT s.store_code, COUNT(*) AS lines, SUM(i.pack_status='packed') AS full,
                   SUM(i.pack_status='short') AS short, SUM(i.packed_qty) AS units, SUM(i.damaged_qty) AS damaged
            FROM packing_items i JOIN stores s USING (store_id)
            WHERE i.pack_status != 'to_pack' GROUP BY s.store_code ORDER BY s.store_code""").fetchall()
    conn.close()

    if blocked:
        print(f"  {blocked} items NOT packed: their barcode labels have not been printed yet. Run 'labels' first.")
    if not items:
        print("No items were waiting to be packed.")
    for r in report:
        print(f"  {r['store_code']}: {r['lines']} lines, {r['full']} fully packed, {r['short']} short, "
              f"{r['units']} units packed, {r['damaged']} damaged")


if __name__ == "__main__":
    job = sys.argv[1] if len(sys.argv) > 1 else "both"
    if job in ("labels", "both"):
        print("Barcode labels:")
        print_labels()
    if job in ("pack", "both"):
        print("\nPacking:")
        pack_items()
