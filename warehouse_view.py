"""Warehouse screen (read only): where is every store's order, and what exactly is in a trip?

    python warehouse_view.py                      stage of every store + list of trips
    python warehouse_view.py TRIP-20250902-01     the full picture of one trip, store by store

Stages:  Packing in process -> Packing over (ready to dispatch) -> In transit -> Clear
         (Clear = the store has checked everything; 'Checked - action needed' if something was less/extra/damaged)
"""
import sqlite3
import sys
from pathlib import Path

from alerts import print_open_alerts

DB_FILE = Path(__file__).resolve().parent / "data" / "retail.db"


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def trip_store_stage(conn, trip_id: int, store_id: int, status: str) -> str:
    if status == "in_transit":
        return "In transit"
    if status == "reached":
        return "Items reached the store"
    if status == "checking":
        return "Checking in progress"
    bad = conn.execute("""SELECT 1 FROM store_receipts r JOIN receipt_items i USING (receipt_id)
                          WHERE r.trip_id = ? AND r.store_id = ?
                          AND (i.received_qty != i.expected_qty OR i.damaged_qty > 0) LIMIT 1""", (trip_id, store_id)).fetchone()
    return "Checked - action needed" if bad else "Clear"


def line_remark(requested: int, approved: int, status: str) -> str:
    if status == "out_of_stock":
        return "Out of stock"
    if status == "not_needed":
        return "Not needed"
    return "Less qty available" if approved < requested else "OK"


def overview() -> None:
    conn = connect()
    print_open_alerts()
    print("NOT YET ON A TRIP")
    rows = conn.execute("""SELECT s.store_code, SUM(i.pack_status = 'to_pack') AS open_lines, COUNT(*) AS n
                           FROM packing_items i JOIN stores s USING (store_id) WHERE i.trip_id IS NULL
                           GROUP BY s.store_code ORDER BY s.store_code""").fetchall()
    for r in rows:
        stage = "Packing in process" if r["open_lines"] else "Packing over (ready to dispatch)"
        print(f"  {r['store_code']:9} {stage:34} {r['n']} lines")
    if not rows:
        print("  (nothing waiting)")
    print("\nTRIPS")
    for t in conn.execute("SELECT trip_id, trip_code, trip_date FROM trips ORDER BY trip_date, trip_id"):
        print(f"  {t['trip_code']}  ({t['trip_date']})")
        for s in conn.execute("""SELECT ts.store_id, st.store_code, ts.status, ts.total_units, ts.boxes_count FROM trip_stores ts
                                 JOIN stores st USING (store_id) WHERE ts.trip_id = ? ORDER BY st.store_code""", (t["trip_id"],)):
            print(f"      {s['store_code']:9} {trip_store_stage(conn, t['trip_id'], s['store_id'], s['status']):26} "
                  f"{s['total_units']} units, {s['boxes_count']} boxes")
    if not conn.execute("SELECT 1 FROM trips").fetchone():
        print("  (no trips yet)")
    conn.close()


def trip_detail(code: str) -> None:
    conn = connect()
    trip = conn.execute("SELECT * FROM trips WHERE trip_code = ?", (code,)).fetchone()
    if trip is None:
        raise SystemExit(f"No trip called {code}. Run  python warehouse_view.py  to see the trip IDs.")
    print(f"{trip['trip_code']}  |  {trip['trip_date']}")
    for s in conn.execute("""SELECT ts.store_id, st.store_code, ts.status, ts.boxes_count FROM trip_stores ts
                             JOIN stores st USING (store_id) WHERE ts.trip_id = ? ORDER BY st.store_code""", (trip["trip_id"],)):
        stage = trip_store_stage(conn, trip["trip_id"], s["store_id"], s["status"])
        al = conn.execute("SELECT level, message FROM warehouse_alerts WHERE trip_id = ? AND store_id = ?",
                          (trip["trip_id"], s["store_id"])).fetchone()
        print(f"\n--- {s['store_code']}  |  {stage}{' [' + al['level'] + ']' if al else ''}  |  {s['boxes_count']} boxes " + "-" * 20)
        checked = {r["pack_id"]: r for r in conn.execute("""SELECT i.packing_item_id AS pack_id, i.received_qty, i.damaged_qty, i.remark
                                                         FROM receipt_items i JOIN store_receipts r USING (receipt_id)
                                                         WHERE r.trip_id = ? AND r.store_id = ?""", (trip["trip_id"], s["store_id"]))}
        done = bool(checked)
        head = f"{'SKU':16} {'Product name':34} {'Packed':>6}"
        head += f" {'Counted':>8} {'Damaged':>8} {'Actual recd':>11}  Remark" if done else "  Remark"
        print(head)
        rows = conn.execute("""SELECT i.pack_id, p.sku, p.product_name, i.packed_qty, r.requested_qty, r.approved_qty, r.status, r.request_date
                               FROM packing_items i JOIN restock_requests r USING (request_id) JOIN products p ON p.product_id = i.product_id
                               WHERE i.trip_id = ? AND i.store_id = ? AND i.packed_qty > 0 ORDER BY p.sku""",
                            (trip["trip_id"], s["store_id"])).fetchall()
        dates = {r["request_date"] for r in rows}
        for r in rows:
            base = f"{r['sku'][:16]:16} {r['product_name'][:34]:34} {r['packed_qty']:>6}"
            if done:
                c = checked[r["pack_id"]]
                print(f"{base} {c['received_qty']:>8} {c['damaged_qty']:>8} {c['received_qty'] - c['damaged_qty']:>11}  {c['remark']}")
            else:
                print(f"{base}  {line_remark(r['requested_qty'], r['approved_qty'], r['status'])}")
        for r in conn.execute(f"""SELECT p.sku, p.product_name, r.requested_qty FROM restock_requests r JOIN products p USING (product_id)
                                  WHERE r.store_id = ? AND r.status = 'out_of_stock'
                                  AND r.request_date IN ({','.join('?' * len(dates)) or "''"}) ORDER BY p.sku""",
                              (s["store_id"], *dates)):
            print(f"{r['sku'][:16]:16} {r['product_name'][:34]:34} {0:>6}  Out of stock (store asked {r['requested_qty']})")
        total = sum(r["packed_qty"] for r in rows)
        if done:
            counted = sum(c["received_qty"] for c in checked.values())
            dmg = sum(c["damaged_qty"] for c in checked.values())
            print(f"{'':16} {'TOTAL':34} {total:>6} {counted:>8} {dmg:>8} {counted - dmg:>11}")
            if al and al["level"] == "SERIOUS":
                print("!" * 70 + "\n!!! " + al["message"] + "\n" + "!" * 70)
            elif al:
                print(al["message"])
            if counted != total or dmg:
                print(f"       (The {dmg} damaged unit(s) go back to the warehouse, so the actual received qty is {counted - dmg}.)")
        else:
            print(f"{'':16} {'TOTAL packed':34} {total:>6}")
    conn.close()


if __name__ == "__main__":
    trip_detail(sys.argv[1].upper()) if len(sys.argv) > 1 else overview()
