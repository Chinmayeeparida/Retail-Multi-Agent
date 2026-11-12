"""The logic behind the screens and the two apps (no screen code here, so it can be tested on its own).

Used by:  app.py (360 Degree View),  store_app.py (Store Replenishment App),  packing_app.py (Warehouse Packaging App)

About dates: the sales data is from August 2025. For a current-looking dashboard the SALES dates are shifted when they are
SHOWN (the database is never changed): the last 30 days of data (2 to 31 Aug 2025) are shown as the 30 days ending yesterday.
"""
import datetime
import sqlite3
from pathlib import Path

import pandas as pd

import guardrails
import store_receiving as receiving
import warehouse_view

DB_FILE = Path(__file__).resolve().parent / "data" / "retail.db"
SALES_FIRST, SALES_LAST = "2025-08-02", "2025-08-31"        # the 30 days of sales that are used
TIER_LABEL = {"top": "Top", "average": "Avg", "low": "Low"}


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def df(sql: str, params: tuple = ()) -> pd.DataFrame:
    conn = connect()
    try:
        return pd.read_sql_query(sql, conn, params=params)
    finally:
        conn.close()


# ---------------- dates ----------------
def today() -> datetime.date:
    return datetime.date.today()


def nice(day) -> str:
    """09 Nov 2026"""
    if isinstance(day, str):
        day = datetime.date.fromisoformat(day)
    return day.strftime("%d %b %Y")


def nice_time(stamp) -> str:
    """10 Nov 2026, 11:42 AM"""
    if stamp is None or (isinstance(stamp, float) and pd.isna(stamp)) or stamp == "":
        return "-"
    return datetime.datetime.fromisoformat(stamp).strftime("%d %b %Y, %I:%M %p")


def short_time(stamp) -> str:
    """09 Nov, 06:54 PM"""
    if stamp is None or (isinstance(stamp, float) and pd.isna(stamp)) or stamp == "":
        return "-"
    return datetime.datetime.fromisoformat(stamp).strftime("%d %b, %I:%M %p")


def nice_day(day) -> str:
    """Monday, 09 Nov 2026"""
    if isinstance(day, str):
        day = datetime.date.fromisoformat(day)
    return day.strftime("%A, %d %b %Y")


def sales_shift() -> datetime.timedelta:
    """Moves 31 Aug 2025 to yesterday."""
    return (today() - datetime.timedelta(days=1)) - datetime.date.fromisoformat(SALES_LAST)


def shown_sales_date(original: str) -> datetime.date:
    return datetime.date.fromisoformat(original) + sales_shift()


def default_date() -> str:
    return today().isoformat()


# ---------------- alerts ----------------
def open_alerts() -> list[dict]:
    return df("""SELECT level, message FROM warehouse_alerts WHERE acknowledged = 0
                 ORDER BY CASE level WHEN 'SERIOUS' THEN 0 WHEN 'WARNING' THEN 1 ELSE 2 END, alert_id""").to_dict("records")


def acknowledge_alerts() -> None:
    conn = connect()
    with conn:
        conn.execute("UPDATE warehouse_alerts SET acknowledged = 1 WHERE acknowledged = 0")
    conn.close()


# ---------------- trips (shared) ----------------
STAGE_WORDS = {"in_transit": "items in transit", "reached": "items reached the store",
               "checking": "received, checking in progress", "received": "checked"}


def trip_codes() -> list[str]:
    return list(df("SELECT trip_code FROM trips ORDER BY trip_date DESC, trip_id DESC")["trip_code"])


def latest_trip(day: str | None = None) -> str | None:
    if day:
        r = df("SELECT trip_code FROM trips WHERE trip_date = ? ORDER BY trip_id DESC LIMIT 1", (day,))
        if len(r):
            return r["trip_code"].iloc[0]
    codes = trip_codes()
    return codes[0] if codes else None


def trips_on(day: str) -> pd.DataFrame:
    return df("""SELECT t.trip_code AS "Trip", s.store_code AS "Store" FROM trip_stores ts JOIN trips t USING (trip_id)
                 JOIN stores s USING (store_id) WHERE t.trip_date = ? ORDER BY s.store_code""", (day,))


def trip_stores_table(trip: str) -> pd.DataFrame:
    """Store Receiving Tracker: one row per store of the trip, with units packed and units received (counted - damaged)."""
    conn = connect()
    rows = []
    for s in conn.execute("""SELECT ts.store_id, st.store_code, ts.status, ts.total_units, ts.boxes_count, t.trip_id
                             FROM trip_stores ts JOIN stores st USING (store_id) JOIN trips t USING (trip_id)
                             WHERE t.trip_code = ? ORDER BY st.store_code""", (trip,)):
        stage = warehouse_view.trip_store_stage(conn, s["trip_id"], s["store_id"], s["status"])
        al = conn.execute("SELECT level FROM warehouse_alerts WHERE trip_id = ? AND store_id = ?", (s["trip_id"], s["store_id"])).fetchone()
        rec = conn.execute("""SELECT SUM(i.received_qty - i.damaged_qty) FROM receipt_items i JOIN store_receipts r USING (receipt_id)
                              WHERE r.trip_id = ? AND r.store_id = ?""", (s["trip_id"], s["store_id"])).fetchone()[0]
        rows.append({"Store": s["store_code"], "Stage": stage + (f" [{al[0]}]" if al else ""), "Boxes": s["boxes_count"],
                     "Units packed": s["total_units"], "Units received": rec if rec is not None else None, "status": s["status"]})
    conn.close()
    t = pd.DataFrame(rows)
    if len(t):
        t["Units received"] = t["Units received"].astype("Int64")
    return t


def trip_store_items(trip: str, store: str) -> pd.DataFrame:
    """Read-only items of one store: Packed qty, Received qty, Match, Damaged, Status."""
    t = df("""SELECT p.sku AS "SKU", p.product_name AS "Item", i.packed_qty AS "Packed qty", ri.received_qty AS "Received qty",
                     ri.damaged_qty AS "Damaged", ri.remark AS "Status"
              FROM packing_items i JOIN products p USING (product_id) JOIN trips t ON t.trip_id = i.trip_id
              JOIN stores s ON s.store_id = i.store_id LEFT JOIN receipt_items ri ON ri.packing_item_id = i.pack_id
              WHERE t.trip_code = ? AND s.store_code = ? AND i.packed_qty > 0 ORDER BY p.sku""", (trip, store))
    t.insert(4, "Match", t["Received qty"] - t["Packed qty"])
    for c in ("Packed qty", "Received qty", "Match", "Damaged"):
        t[c] = t[c].astype("Int64")
    t["Status"] = t["Status"].fillna("Not checked yet")
    return t


def store_boxes(trip: str, store: str) -> tuple[pd.DataFrame, dict]:
    """Today's Replenishment panel: Box No, Item, Packed qty, Received qty and the totals."""
    t = df("""SELECT p.product_name AS "Item",
                     (SELECT GROUP_CONCAT(SUBSTR(b.box_code, LENGTH(b.box_code) - 2), ', ') FROM box_items bi JOIN boxes b USING (box_id)
                      WHERE bi.packing_item_id = i.pack_id) AS "Box",
                     i.packed_qty AS "Packed", ri.received_qty AS "Received", ri.damaged_qty AS "Damaged"
              FROM packing_items i JOIN products p USING (product_id) JOIN trips t ON t.trip_id = i.trip_id
              JOIN stores s ON s.store_id = i.store_id LEFT JOIN receipt_items ri ON ri.packing_item_id = i.pack_id
              WHERE t.trip_code = ? AND s.store_code = ? AND i.packed_qty > 0 ORDER BY "Box", p.sku""", (trip, store))
    boxes = int(df("""SELECT COUNT(*) AS n FROM boxes b JOIN trips t USING (trip_id) JOIN stores s USING (store_id)
                      WHERE t.trip_code = ? AND s.store_code = ?""", (trip, store))["n"].iloc[0])
    packed = int(t["Packed"].sum())
    checked = len(t) > 0 and bool(t["Received"].notna().all())
    actual = int(t["Received"].sum() - t["Damaged"].sum()) if checked else None
    totals = {"packed": packed, "boxes": boxes, "actual": actual,
              "success_pct": round(100 * actual / packed, 1) if checked and packed else None}
    shown = t.drop(columns=["Damaged"])
    shown["Received"] = shown["Received"].map(lambda v: "-" if pd.isna(v) else str(int(v)))
    return shown, totals


# ---------------- 360 view: activities ----------------
def _trip_summary(trip_date: str) -> list[dict]:
    out = []
    for t in df("SELECT trip_id, trip_code FROM trips WHERE trip_date = ? ORDER BY trip_id", (trip_date,)).itertuples():
        rows = df("""SELECT s.store_code, ts.total_units, ts.boxes_count, ts.status, ts.damaged_units,
                            (SELECT COUNT(*) FROM packing_items i WHERE i.trip_id = ts.trip_id AND i.store_id = ts.store_id
                             AND i.packed_qty > 0) AS items,
                            COALESCE((SELECT SUM(ri.damaged_qty) FROM receipt_items ri JOIN store_receipts r USING (receipt_id)
                                      WHERE r.trip_id = ts.trip_id AND r.store_id = ts.store_id), 0) AS store_damaged
                     FROM trip_stores ts JOIN stores s USING (store_id) WHERE ts.trip_id = ? ORDER BY s.store_code""", (t.trip_id,))
        out.append({"trip": t.trip_code, "stores": list(rows["store_code"]), "items": int(rows["items"].sum()),
                    "units": int(rows["total_units"].sum()), "boxes": int(rows["boxes_count"].sum()),
                    "packing_damaged": int(rows["damaged_units"].sum()), "store_damaged": int(rows["store_damaged"].sum()),
                    "status": dict(zip(rows["store_code"], rows["status"]))})
    return out


WEEKDAYS = {1: "Monday", 2: "Tuesday", 3: "Wednesday", 4: "Thursday", 5: "Friday", 6: "Saturday", 7: "Sunday"}


def trip_day_of(store: str) -> str:
    """The weekday this store is on the delivery schedule (e.g. 'Wednesday'), or '-'."""
    r = df("""SELECT t.weekday FROM trip_schedule t JOIN stores s USING (store_id) WHERE s.store_code = ?""", (store,))
    return WEEKDAYS.get(int(r["weekday"].iloc[0]), "-") if len(r) else "-"


def _names(codes: list[str]) -> str:
    return ", ".join(codes)


def activities(day: str) -> dict:
    """The left panel, in this order: Today's activities, Pending work, Yesterday's activities."""
    d = datetime.date.fromisoformat(day)
    yesterday = (d - datetime.timedelta(days=1)).isoformat()
    today_lines, pending, yest_lines, highlights = [], [], [], []
    new_requests, ready_lines = [], []
    for r in df("""SELECT s.store_code, COUNT(*) AS items, SUM(r.requested_qty) AS units, r.requested_at AS at, MAX(r.requested_by) AS who
                   FROM restock_requests r JOIN stores s USING (store_id) WHERE r.status = 'pending'
                   GROUP BY s.store_code, r.requested_at ORDER BY r.requested_at DESC, s.store_code""").itertuples():
        highlights.append(f"NEW REQUEST: {r.store_code} raised {r.items} items ({int(r.units)} units), {nice_time(r.at)}"
                          + (f", by {r.who}" if r.who else "") + ". Waiting for Agent 1.")
        new_requests.append(f"{r.store_code}: {r.items} items ({int(r.units)} units), {short_time(r.at)}")

    in_process = df("""SELECT s.store_code FROM packing_items i JOIN stores s USING (store_id) WHERE i.pack_status = 'to_pack'
                       GROUP BY s.store_code ORDER BY s.store_code""")["store_code"].tolist()
    if in_process:
        today_lines.append(f"Packing is in process for {_names(in_process)}.")
    packed_today = df("""SELECT COUNT(*) AS n, COUNT(DISTINCT store_id) AS s FROM packing_items
                         WHERE pack_status != 'to_pack' AND date(packed_at) = ?""", (day,)).iloc[0]
    if packed_today["n"]:
        today_lines.append(f"{int(packed_today['n'])} items packed for {int(packed_today['s'])} stores so far today.")
    for t in _trip_summary(day):
        today_lines.append(f"{t['items']} items ({t['units']} units, {t['boxes']} boxes) will be delivered to "
                           f"{_names(t['stores'])} on {t['trip']}.")
        today_lines.append(f"Damaged while packing: {t['packing_damaged']} unit(s). "
                           f"Damaged found at the stores: {t['store_damaged']} unit(s).")

    for r in df("""SELECT s.store_code, SUM(b.total_labels) AS labels FROM barcode_requests b JOIN stores s USING (store_id)
                   WHERE b.status = 'requested' GROUP BY s.store_code ORDER BY s.store_code""").itertuples():
        pending.append(f"{r.store_code}: packing list ready, {int(r.labels)} barcode labels waiting to be printed.")
    for r in df("""SELECT s.store_code, COUNT(*) AS items, SUM(i.packed_qty) AS units, MAX(i.packed_at) AS at
                   FROM packing_items i JOIN stores s USING (store_id) WHERE i.trip_id IS NULL
                   GROUP BY s.store_code HAVING SUM(i.pack_status = 'to_pack') = 0 ORDER BY s.store_code""").itertuples():
        highlights.append(f"PACKED: {r.store_code} has {r.items} items ({int(r.units)} units) packed, {nice_time(r.at)}. "
                          f"Ready to dispatch, waiting for the trip ID ({trip_day_of(r.store_code)}).")
        ready_lines.append(f"{r.store_code}: {r.items} items ({int(r.units)} units), packed {short_time(r.at)}. "
                           f"Waiting for trip ID ({trip_day_of(r.store_code)})")
    for trip, grp in df("""SELECT t.trip_code, s.store_code, ts.status FROM trip_stores ts JOIN trips t USING (trip_id)
                           JOIN stores s USING (store_id) WHERE ts.status != 'received'
                           ORDER BY t.trip_date, s.store_code""").groupby("trip_code", sort=False):
        for r in grp.itertuples():
            pending.append(f"{r.store_code}: {STAGE_WORDS[r.status]} ({trip}).")
    n_alerts = len(open_alerts())
    if n_alerts:
        pending.append(f"{n_alerts} warehouse alert(s) not yet read.")

    packed_y = df("""SELECT COUNT(*) AS n, COUNT(DISTINCT store_id) AS s FROM packing_items
                     WHERE pack_status != 'to_pack' AND date(packed_at) = ?""", (yesterday,)).iloc[0]
    if packed_y["n"]:
        yest_lines.append(f"Updates: {int(packed_y['n'])} items packed for {int(packed_y['s'])} stores.")
    for t in _trip_summary(yesterday):
        done = sum(1 for s in t["status"].values() if s == "received")
        yest_lines.append(f"Items dispatched to {_names(t['stores'])}: {t['items']} items, {t['units']} units in {t['boxes']} boxes "
                          f"({t['trip']}). Store checks done: {done} of {len(t['status'])}.")
    return {"today_title": nice_day(d), "yesterday_title": nice_day(yesterday),
            "today": today_lines, "pending": pending, "yesterday": yest_lines, "highlights": highlights,
            "new_requests": new_requests, "ready": ready_lines}


# ---------------- 360 view: Top Stores, best sellers ----------------
def top_stores() -> pd.DataFrame:
    """Based on the 30 days of data, best store first. Remark (Top / Avg / Low) uses the same ranking Agent 1 uses."""
    from allocation import store_tier
    t = df("""SELECT s.store_code AS "Store", SUM(sa.quantity) AS "Units sold", ROUND(SUM(sa.net_amount)) AS "Sales (Rs)"
              FROM sales sa JOIN stores s USING (store_id) WHERE sa.sale_date BETWEEN ? AND ?
              GROUP BY s.store_code ORDER BY SUM(sa.quantity) DESC""", (SALES_FIRST, SALES_LAST))
    ranking = df("""SELECT st.store_code FROM stores st LEFT JOIN sales s ON s.store_id = st.store_id WHERE st.is_active = 1
                    GROUP BY st.store_code ORDER BY COALESCE(SUM(s.quantity), 0) DESC, st.store_code""")["store_code"].tolist()
    tier = {code: store_tier(i + 1, len(ranking))[0] for i, code in enumerate(ranking)}
    t["Remark"] = t["Store"].map(lambda c: TIER_LABEL[tier[c]])
    t["Sales (Rs)"] = t["Sales (Rs)"].astype(int)
    return t.reset_index(drop=True)


def best_sellers(n: int) -> tuple[datetime.date, pd.DataFrame]:
    """The n best selling items of the latest 7 days of data, with the warehouse quantity available."""
    last = datetime.date.fromisoformat(SALES_LAST)
    t = df("""SELECT p.product_name AS "Item", SUM(sa.quantity) AS "Sold",
                     COALESCE((SELECT SUM(w.available_qty) FROM warehouse_stock w WHERE w.product_id = p.product_id), 0) AS "Available"
              FROM sales sa JOIN products p USING (product_id) WHERE sa.sale_date > date(?, '-7 day') AND sa.sale_date <= ?
              GROUP BY p.product_id ORDER BY SUM(sa.quantity) DESC LIMIT ?""", (SALES_LAST, SALES_LAST, n))
    return last + sales_shift(), t


# ---------------- Product Details, Inventory View, Sales Details ----------------
def categories() -> list[str]:
    return ["All"] + list(df("SELECT category_name FROM categories ORDER BY category_name")["category_name"])


def product_details(search: str, category: str, active_only: bool) -> pd.DataFrame:
    t = df("""SELECT p.sku AS "SKU", p.product_name AS "Product", c.category_name AS "Category", v.vendor_name AS "Vendor",
                     p.colour AS "Colour", pr.cost_price AS "Cost price", pr.selling_price AS "Selling price",
                     p.created_date AS created,
                     EXISTS (SELECT 1 FROM packing_items i WHERE i.product_id = p.product_id AND i.trip_id IS NOT NULL) AS sent,
                     CASE p.is_active WHEN 1 THEN 'Active' ELSE 'Inactive' END AS "Status"
              FROM products p LEFT JOIN categories c USING (category_id) LEFT JOIN vendors v USING (vendor_id)
              LEFT JOIN product_prices pr ON pr.product_id = p.product_id AND pr.effective_from =
                   (SELECT MAX(effective_from) FROM product_prices x WHERE x.product_id = p.product_id)
              WHERE (p.product_name LIKE ? OR p.sku LIKE ?) AND (? = 'All' OR c.category_name = ?) AND (? = 0 OR p.is_active = 1)
              ORDER BY p.sku""", (f"%{search}%", f"%{search}%", category, category, 1 if active_only else 0))
    t["Colour"] = t["Colour"].fillna("-")
    t["Selling price"] = t["Selling price"].map(lambda v: "-" if pd.isna(v) else f"{v:,.0f}")
    t["Cost price"] = t["Cost price"].map(lambda v: "-" if pd.isna(v) else f"{v:,.0f}")
    # Listed on: the registration date; "New" for a product added by Agent 4 (its created_date carries a time) that has not gone to a store yet
    t.insert(t.columns.get_loc("Selling price") + 1, "Listed on",
             [("New" if len(str(c)) > 10 and not sent else nice(str(c)[:10])) for c, sent in zip(t["created"], t["sent"])])
    return t.drop(columns=["created", "sent"])


def inventory(search: str, category: str, low_only: bool) -> pd.DataFrame:
    """Highest quantity first. Value = available qty x cost price."""
    return df("""SELECT p.sku AS "SKU", p.product_name AS "Product", c.category_name AS "Category", w.rack_name AS "Rack",
                        SUM(w.available_qty) AS "Available", SUM(w.damaged_qty) AS "Damaged (vendor)",
                        pr.cost_price AS "Cost price", ROUND(SUM(w.available_qty) * COALESCE(pr.cost_price, 0)) AS "Value"
                 FROM warehouse_stock w JOIN products p USING (product_id) LEFT JOIN categories c USING (category_id)
                 LEFT JOIN product_prices pr ON pr.product_id = p.product_id AND pr.effective_from =
                      (SELECT MAX(effective_from) FROM product_prices x WHERE x.product_id = p.product_id)
                 WHERE (p.product_name LIKE ? OR p.sku LIKE ?) AND (? = 'All' OR c.category_name = ?)
                 GROUP BY p.product_id HAVING (? = 0 OR SUM(w.available_qty) < 10)
                 ORDER BY SUM(w.available_qty) DESC, p.sku""",
              (f"%{search}%", f"%{search}%", category, category, 1 if low_only else 0))


def sales_by_category() -> pd.DataFrame:
    return df("""SELECT c.category_name AS "Category", SUM(sa.quantity) AS "Units sold", ROUND(SUM(sa.net_amount)) AS "Sales (Rs)"
                 FROM sales sa JOIN products p USING (product_id) LEFT JOIN categories c USING (category_id)
                 WHERE sa.sale_date BETWEEN ? AND ? GROUP BY c.category_name ORDER BY SUM(sa.quantity) DESC""", (SALES_FIRST, SALES_LAST))


PREVIOUS_MONTH_FILE = Path(__file__).resolve().parent / "data" / "previous_month_sales.csv"


def month_comparison() -> dict | None:
    """This month so far against the previous month (daily totals per store come from the July bill report, treated as last month).
    Returns None when there is no data for it."""
    import calendar
    if not PREVIOUS_MONTH_FILE.exists():
        return None
    first = today().replace(day=1)
    yesterday = today() - datetime.timedelta(days=1)
    if yesterday < first:                                   # the month has only just started
        return None
    days = (yesterday - first).days + 1
    days_in_month = calendar.monthrange(today().year, today().month)[1]
    shift = sales_shift()
    lo, hi = (first - shift).isoformat(), (yesterday - shift).isoformat()
    cur = df("""SELECT s.store_code AS store, SUM(sa.net_amount) AS cur FROM sales sa JOIN stores s USING (store_id)
                WHERE sa.sale_date BETWEEN ? AND ? AND sa.sale_date BETWEEN ? AND ? GROUP BY s.store_code""",
             (lo, hi, SALES_FIRST, SALES_LAST)).set_index("store")["cur"]
    prev = pd.read_csv(PREVIOUS_MONTH_FILE)
    rows, messages = [], []
    all_stores = list(df("SELECT store_code FROM stores WHERE is_active = 1 ORDER BY store_code")["store_code"])
    for store in all_stores + ["All stores"]:
        if store == "All stores":
            have = [r for r in rows if r["prev"] is not None]
            c = sum(r["cur"] for r in have)
            p_total, p_same = sum(r["prev"] for r in have), sum(r["prev_same"] for r in have)
        else:
            c = float(cur.get(store, 0))
            p = prev[prev["store_code"] == store]
            p_total = float(p["net_sales"].sum()) if len(p) else None
            p_same = float(p[p["day"] <= days]["net_sales"].sum()) if len(p) else None
        row = {"store": store, "cur": c, "prev": p_total, "prev_same": p_same}
        if p_total:
            projected = c / days * days_in_month
            row.update(pct_of_prev=100 * c / p_total, vs_same=100 * (c / p_same - 1) if p_same else None,
                       projected=projected, proj_vs_prev=100 * (projected / p_total - 1))
            gap_per_day = max(p_total - c, 0) / max(days_in_month - days, 1)
            verb = "are" if store == "All stores" else "is"
            if c >= p_total:
                row["status"] = "Achieved"
                msg = f"{'have' if verb == 'are' else 'has'} already achieved last month's total of Rs {p_total:,.0f} ({row['pct_of_prev']:.0f}%)."
            elif row["proj_vs_prev"] >= 0:
                row["status"] = "Ahead"
                msg = (f"{verb} ahead: Rs {c:,.0f} so far, {row['vs_same']:+.0f}% against the same {days} days last month; "
                       f"on track to finish {row['proj_vs_prev']:.0f}% above last month.")
            elif row["proj_vs_prev"] >= -5:
                row["status"] = "About to achieve"
                msg = (f"{verb} about to achieve last month ({row['pct_of_prev']:.0f}% done): "
                       f"needs about Rs {gap_per_day:,.0f} a day to get there.")
            else:
                row["status"] = "Behind"
                msg = (f"{verb} behind: {row['pct_of_prev']:.0f}% of last month done, {row['vs_same']:+.0f}% against the same days last month; "
                       f"needs about Rs {gap_per_day:,.0f} a day to catch up.")
            messages.append((row["status"], f"{store} {msg}"))
        else:
            row.update(pct_of_prev=None, vs_same=None, projected=None, proj_vs_prev=None, status="No last month data")
            messages.append(("None", f"{store}: no last month data to compare with."))
        rows.append(row)
    table = pd.DataFrame({
        "Store": [r["store"] for r in rows],
        "Last month (Rs)": [None if r["prev"] is None else round(r["prev"]) for r in rows],
        "This month so far (Rs)": [round(r["cur"]) for r in rows],
        "% of last month": [None if r["pct_of_prev"] is None else round(r["pct_of_prev"]) for r in rows],
        f"vs same {days} days last month (%)": [None if r["vs_same"] is None else round(r["vs_same"], 1) for r in rows],
        "Projected month end (Rs)": [None if r["projected"] is None else round(r["projected"]) for r in rows],
        "Status": [r["status"] for r in rows]})
    for c in ("Last month (Rs)", "% of last month", "Projected month end (Rs)"):
        table[c] = table[c].astype("Int64")
    return {"table": table, "messages": messages, "days": days, "month": first.strftime("%B %Y"),
            "prev_month": (first - datetime.timedelta(days=1)).strftime("%B %Y"), "total": rows[-1]}


def sales_by_day() -> pd.DataFrame:
    t = df("""SELECT sale_date AS d, SUM(quantity) AS "Units sold", ROUND(SUM(net_amount)) AS "Sales (Rs)"
              FROM sales WHERE sale_date BETWEEN ? AND ? GROUP BY sale_date ORDER BY sale_date""", (SALES_FIRST, SALES_LAST))
    t["Date"] = t["d"].map(lambda d: pd.Timestamp(shown_sales_date(d)))
    t = t.drop(columns=["d"]).tail(7)                                  # only the last 7 days
    t["Day"] = t["Date"].dt.strftime("%a %d %b")
    return t.reset_index(drop=True)


# ---------------- Barcode Request Tracker ----------------
def barcode_requests() -> pd.DataFrame:
    t = df("""SELECT b.barcode_request_id AS id, s.store_code AS "Store", b.total_labels AS "Labels", b.status AS "Status",
                     b.requested_at AS "Requested", b.printed_at AS "Printed"
              FROM barcode_requests b JOIN stores s USING (store_id) ORDER BY (b.status = 'printed'), s.store_code, b.barcode_request_id""")
    t["Request"] = [f"{r.Store}" if (t["Store"] == r.Store).sum() == 1 else f"{r.Store} (#{r.id})" for r in t.itertuples()]
    return t


def barcode_lines(request_id: int) -> pd.DataFrame:
    return df("""SELECT p.sku AS "SKU (barcode)", p.product_name AS "Item", i.qty_to_pack AS "Labels to print"
                 FROM packing_items i JOIN products p USING (product_id) WHERE i.barcode_request_id = ? ORDER BY p.sku""", (request_id,))


def mark_printed(request_id: int) -> None:
    conn = connect()
    with conn:
        conn.execute("UPDATE barcode_requests SET status = 'printed', printed_at = datetime('now', 'localtime') "
                     "WHERE barcode_request_id = ? AND status = 'requested'", (request_id,))
    conn.close()


# ---------------- Agent 1: Packing List tab ----------------
def agent1_overview() -> pd.DataFrame:
    return df("""SELECT s.store_code AS "Store",
                        SUM(r.status = 'pending') AS "New requests (waiting)",
                        SUM(r.status = 'approved') AS "Items approved", SUM(r.status = 'out_of_stock') AS "Out of stock", SUM(r.status = 'not_needed') AS "Not needed",
                        COALESCE((SELECT SUM(i.qty_to_pack) FROM packing_items i WHERE i.store_id = s.store_id), 0) AS "Units to pack",
                        CASE WHEN SUM(r.status = 'pending') > 0 THEN 'Agent 1 not run yet'
                             WHEN EXISTS (SELECT 1 FROM packing_items i WHERE i.store_id = s.store_id AND i.pack_status = 'to_pack')
                                  THEN 'Prepared and sent to the Packing App'
                             WHEN EXISTS (SELECT 1 FROM packing_items i WHERE i.store_id = s.store_id) THEN 'Packed'
                             ELSE 'Nothing to pack' END AS "Packing list"
                 FROM restock_requests r JOIN stores s USING (store_id) GROUP BY s.store_code ORDER BY s.store_code""")


def agent1_items(store: str) -> pd.DataFrame:
    t = df("""SELECT p.product_name AS "Item",
                     COALESCE((SELECT SUM(x.quantity) FROM sales x WHERE x.store_id = r.store_id AND x.product_id = r.product_id), 0) AS "Sold",
                     r.requested_qty AS "Asked", COALESCE((SELECT SUM(w.available_qty) FROM warehouse_stock w
                                                          WHERE w.product_id = r.product_id), 0) AS "WH qty now",
                     r.approved_qty AS "Agent 1 qty", r.status AS "Decision", r.decision_reason AS "Why"
              FROM restock_requests r JOIN products p USING (product_id) JOIN stores s ON s.store_id = r.store_id
              WHERE s.store_code = ? ORDER BY r.status, p.product_name""", (store,))
    t["Decision"] = t["Decision"].map({"approved": "Approved", "out_of_stock": "Out of stock", "not_needed": "Not needed", "pending": "Waiting"})
    t["Agent 1 qty"] = t["Agent 1 qty"].astype("Int64")
    t["Why"] = t["Why"].fillna("-")
    return t


def a1_propose() -> dict:
    """Agent 1 makes its proposal for every store that has pending requests. Nothing is saved until Done."""
    import agent1_packing
    return agent1_packing.propose()


def a1_table(state: dict, store: str) -> pd.DataFrame:
    """The Agent 1 screen: one row per requested item of this store. Staff may edit 'Packaging qty' and 'Remark'."""
    rows = [{"request_id": d["request_id"], "SKU": d["sku"], "Product Name": d["product_name"],
             "Selling price": "-" if d["price"] is None else f"{d['price']:,.0f}", "Sales data (30 days)": d["sold"],
             "Inventory qty": d["in_stock"], "Store qty": d["store_qty"], "Request qty": d["requested_qty"],
             "Packaging qty": d["approved_qty"], "Remark": d["reason"]}
            for d in state["decisions"] if d["store_code"] == store]
    rows.sort(key=lambda r: (r["Packaging qty"] == 0, r["SKU"]))          # items to pack first, out of stock last
    return pd.DataFrame(rows)


def a1_done(state: dict, store: str, original: pd.DataFrame, edited: pd.DataFrame) -> tuple[bool, str]:
    """Done pressed: check the edits, then Agent 1 saves the packing list and requests the barcodes."""
    import agent1_packing
    changes = {}
    for (_, was), (_, now) in zip(original.iterrows(), edited.iterrows()):
        qty = now["Packaging qty"]
        if pd.isna(qty) or qty < 0 or int(qty) != qty:
            return False, f"{now['SKU']}: Packaging qty must be a whole number, 0 or more."
        qty = int(qty)
        reason = str(now["Remark"] or "").strip()
        if qty != was["Packaging qty"] and (not reason or reason == was["Remark"]):
            return False, f"{now['SKU']}: you changed the Packaging qty, please write the reason in Remark."
        changes[int(was["request_id"])] = (qty, reason if qty != was["Packaging qty"] else "")
    return agent1_packing.finish_store(state, store, changes)


# ---------------- Store Stock Requests (store staff raise them; the details tab shows them) ----------------
STATUS_SQL = """
    SELECT r.request_id, r.request_date, r.requested_at, s.store_code, p.product_id, p.sku, p.product_name, r.requested_qty, r.approved_qty,
           r.requested_by, r.request_note, r.status AS rs, i.pack_status, date(i.packed_at) AS packed_on, t.trip_date,
           ts.status AS trip_status, date(sr.received_at) AS received_on,
           (SELECT pr.selling_price FROM product_prices pr WHERE pr.product_id = p.product_id
            ORDER BY pr.effective_from DESC LIMIT 1) AS price
    FROM restock_requests r JOIN stores s USING (store_id) JOIN products p USING (product_id)
    LEFT JOIN packing_items i ON i.request_id = r.request_id
    LEFT JOIN trips t ON t.trip_id = i.trip_id
    LEFT JOIN trip_stores ts ON ts.trip_id = i.trip_id AND ts.store_id = r.store_id
    LEFT JOIN store_receipts sr ON sr.trip_id = i.trip_id AND sr.store_id = r.store_id
"""


def request_stage(r) -> str:
    """Request raised -> Packaging in process -> Ready to dispatch -> Dispatched -> Received (each with its date)."""
    def has(key) -> bool:          # an empty database value arrives as NaN, which Python treats as 'true', so test it properly
        return key in r and pd.notna(r[key]) and r[key] != ""

    if r["rs"] == "out_of_stock":
        return "Out of stock"
    if r["rs"] == "not_needed":
        return "Not needed (store has enough)"
    if r["rs"] == "pending":
        return f"Request raised ({nice(r['request_date'])})"
    if has("trip_status") and r["trip_status"] == "received" and has("received_on"):
        return f"Received ({nice(r['received_on'])})"
    if has("trip_status") and has("trip_date"):
        return f"Dispatched ({nice(r['trip_date'])})"
    if has("pack_status") and r["pack_status"] in ("packed", "short"):
        return f"Ready to dispatch ({nice(r['packed_on'])})" if has("packed_on") else "Ready to dispatch"
    return f"Packaging in process ({nice(r['request_date'])})"


def all_requests() -> pd.DataFrame:
    t = df(STATUS_SQL + " ORDER BY r.request_date DESC, s.store_code, p.sku")
    t["Status"] = [request_stage(r) for _, r in t.iterrows()]
    t["Selling price"] = t["price"].map(lambda v: "-" if pd.isna(v) else f"{v:,.0f}")
    return t


def requests_by_store() -> pd.DataFrame:
    t = all_requests()
    return (t.groupby("store_code").agg(Items=("sku", "count"), Units=("requested_qty", "sum")).reset_index()
            .rename(columns={"store_code": "Store", "Units": "Units requested"}))


def requests_store_items(store: str) -> pd.DataFrame:
    t = all_requests()
    t = t[t["store_code"] == store]
    return pd.DataFrame({"SKU": t["sku"], "Product Name": t["product_name"], "Selling Price": t["Selling price"],
                         "Requested QTY": t["requested_qty"], "Requested By": t["requested_by"].fillna("-"),
                         "Requested on": t["requested_at"].map(nice_time),
                         "Status": t["Status"], "Note": t["request_note"].fillna("")})


def requests_by_product() -> pd.DataFrame:
    t = all_requests()
    g = t.groupby(["sku", "product_name", "Selling price"], sort=False).agg(
        total=("requested_qty", "sum"), stores=("store_code", lambda s: ", ".join(sorted(s)))).reset_index()
    g = g.sort_values("total", ascending=False)
    return g.rename(columns={"sku": "SKU", "product_name": "Product Name", "Selling price": "Selling Price",
                             "total": "Total Requested QTY", "stores": "From (Store Names)"})


def request_catalog(search: str) -> tuple[pd.DataFrame, bool]:
    """Items the warehouse can give (warehouse qty above 0). Second value: True when the search matched only items that are out of stock."""
    q = f"%{search.strip()}%"
    rows = df("""SELECT p.product_id, p.sku AS "SKU", p.product_name AS "Product Name",
                        (SELECT pr.selling_price FROM product_prices pr WHERE pr.product_id = p.product_id
                         ORDER BY pr.effective_from DESC LIMIT 1) AS price,
                        COALESCE(SUM(w.available_qty), 0) AS inv
                 FROM products p LEFT JOIN warehouse_stock w ON w.product_id = p.product_id
                 WHERE p.is_active = 1 AND (p.product_name LIKE ? OR p.sku LIKE ?) GROUP BY p.product_id
                 ORDER BY inv DESC, p.product_name LIMIT 400""", (q, q))
    have = rows[rows["inv"] > 0].head(60)
    only_os = bool(search.strip()) and have.empty and not rows.empty
    t = pd.DataFrame({"product_id": have["product_id"], "SKU": have["SKU"], "Product Name": have["Product Name"],
                      "Selling price": have["price"].map(lambda v: "-" if pd.isna(v) else f"{v:,.0f}"),
                      "Inventory qty": have["inv"], "Request qty": pd.array([None] * len(have), dtype="Int64"),
                      "Note": ""})
    return t.reset_index(drop=True), only_os


def already_requested_today(store: str, product_id: int) -> bool:
    return len(df('''SELECT 1 FROM restock_requests r JOIN stores s USING (store_id)
                     WHERE s.store_code = ? AND r.product_id = ? AND r.request_date = ?''',
                  (store, product_id, today().isoformat()))) > 0


def raise_requests(store: str, name: str, table: pd.DataFrame) -> tuple[bool, str]:
    """Saves the requests the staff filled in. Request qty cannot be more than the warehouse qty."""
    if not name.strip():
        return False, "Please type your name first."
    if (table["Request qty"].fillna(0) < 0).any():
        return False, "A Request qty cannot be negative."
    rows = table[table["Request qty"].fillna(0) > 0]
    if rows.empty:
        return False, "Type a Request qty for at least one item."
    bad_text = guardrails.check_request_text(name, "".join(str(n or "") for n in rows["Note"]))
    if bad_text:
        return False, bad_text
    for _, r in rows.iterrows():
        if int(r["Request qty"]) != r["Request qty"]:
            return False, f"{r['SKU']}: the Request qty must be a whole number."
        if r["Request qty"] > r["Inventory qty"]:
            return False, f"{r['SKU']}: you can request at most {int(r['Inventory qty'])} (the warehouse quantity)."
    conn = connect()
    stamp = datetime.datetime.now().isoformat(sep=" ", timespec="seconds")      # one time for the whole list
    try:
        sid = conn.execute("SELECT store_id FROM stores WHERE store_code = ?", (store,)).fetchone()[0]
        with conn:
            for _, r in rows.iterrows():
                conn.execute("""INSERT INTO restock_requests (request_date, requested_at, store_id, product_id, requested_qty, requested_by, request_note)
                                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                             (today().isoformat(), stamp, sid, int(r["product_id"]), int(r["Request qty"]), name.strip(), (r["Note"] or "").strip() or None))
    except sqlite3.IntegrityError:
        return False, "One of these items was already requested today for this store. Remove it from your list."
    finally:
        conn.close()
    return True, f"{len(rows)} item(s) requested for {store}. Agent 1 will prepare the packing list."


def store_request_status(store: str) -> pd.DataFrame:
    t = all_requests()
    t = t[t["store_code"] == store]
    return pd.DataFrame({"SKU": t["sku"], "Product Name": t["product_name"], "Request qty": t["requested_qty"],
                         "Requested on": t["requested_at"].map(nice_time),
                         "Status": t["Status"], "Note": t["request_note"].fillna("")})


# ---------------- Warehouse Packaging: tracker + app ----------------
def packing_status(to_pack: int, packed, damaged: int = 0) -> str:
    """Only damage is tracked while packing (shortages are checked at the store)."""
    if packed is None or pd.isna(packed):
        return "Pending"
    damaged = int(damaged or 0)
    return f"{damaged} damaged" if damaged else "No damage"


def packing_progress() -> pd.DataFrame:
    t = df("""SELECT s.store_code AS "Store", COUNT(*) AS "Lines", SUM(i.packed_qty IS NOT NULL) AS "Lines packed",
                        SUM(i.qty_to_pack) AS "Units to pack",
                        COALESCE(SUM(i.packed_qty), 0) AS "Units packed", COALESCE(SUM(i.damaged_qty), 0) AS "Damaged",
                        CASE WHEN SUM(i.pack_status = 'to_pack') > 0 THEN 'Packing in process'
                             ELSE 'Packed and ready to dispatch' END AS "Stage",
                        MAX(i.packed_at) AS "Packed at"
                 FROM packing_items i JOIN stores s USING (store_id) WHERE i.trip_id IS NULL
                 GROUP BY s.store_code ORDER BY s.store_code""")
    t["Packed at"] = t["Packed at"].map(lambda v: nice_time(v) if v and pd.notna(v) else "-")
    t["Trip day"] = t["Store"].map(trip_day_of)
    return t


def packing_store_items(store: str) -> pd.DataFrame:
    t = df("""SELECT i.rack_name AS "Rack", p.product_name AS "Item", i.qty_to_pack AS "Qty to pack", i.packed_qty AS "Packed qty",
                     i.damaged_qty AS "Damaged", i.pack_status AS s
              FROM packing_items i JOIN products p USING (product_id) JOIN stores st ON st.store_id = i.store_id
              WHERE st.store_code = ? AND i.trip_id IS NULL ORDER BY i.rack_name, p.sku""", (store,))
    t["Status"] = [packing_status(r["Qty to pack"], None if r["s"] == "to_pack" else r["Packed qty"], r["Damaged"]) for _, r in t.iterrows()]
    t["Packed qty"] = t["Packed qty"].astype("Int64")
    return t.drop(columns=["s"])


def lists_ready_to_pack() -> pd.DataFrame:
    """Stores whose packing list was made by Agent 1 and approved by a person (it only exists in the database then).
    Labels = 'printed' only when EVERY barcode request of the store is printed."""
    return df("""SELECT s.store_code AS "Store", COUNT(*) AS "Items", SUM(i.qty_to_pack) AS "Units to pack",
                        CASE WHEN SUM(b.status != 'printed') > 0 THEN 'requested' ELSE 'printed' END AS "Labels",
                        COUNT(DISTINCT CASE WHEN b.status != 'printed' THEN b.barcode_request_id END) AS "Unprinted"
                 FROM packing_items i JOIN stores s USING (store_id) JOIN barcode_requests b USING (barcode_request_id)
                 WHERE i.pack_status = 'to_pack' GROUP BY s.store_code ORDER BY s.store_code""")


def packing_done_lists() -> pd.DataFrame:
    """Stores that are packed and still waiting for a trip (their list stays visible, so staff can check it and fix mistakes)."""
    t = df("""SELECT s.store_code AS "Store", COUNT(*) AS "Items", SUM(i.packed_qty) AS "Units packed",
                     MAX(i.packed_at) AS "Packed at", MAX(i.staff_note) AS note
              FROM packing_items i JOIN stores s USING (store_id) WHERE i.trip_id IS NULL
              GROUP BY s.store_code HAVING SUM(i.pack_status = 'to_pack') = 0 ORDER BY s.store_code""")
    t["Packed by"] = t["note"].map(lambda n: (str(n).split(";")[0].replace("Packed by", "").strip() or "-") if n else "-")
    return t.drop(columns=["note"])


def packing_lines(store: str, done: bool = False) -> pd.DataFrame:
    """done=False: the lines still to pack. done=True: the lines already packed (shown again for checking / correcting)."""
    t = df(f"""SELECT i.pack_id, i.rack_name AS "Rack", p.sku AS "SKU", p.product_name AS "Product", i.qty_to_pack AS "To pack",
                     i.packed_qty AS "Packed", i.damaged_qty AS "Damaged"
              FROM packing_items i JOIN products p USING (product_id) JOIN stores s ON s.store_id = i.store_id
              WHERE s.store_code = ? AND i.trip_id IS NULL AND i.pack_status {"!=" if done else "="} 'to_pack'
              ORDER BY i.rack_name, p.sku""", (store,))
    t["Packed"] = t["Packed"].astype("Int64")
    t["Status"] = "Pending"
    return t


def packing_with_status(table: pd.DataFrame) -> pd.DataFrame:
    t = table.copy()
    t["Status"] = [packing_status(r["To pack"], r["Packed"], 0 if pd.isna(r["Damaged"]) else r["Damaged"]) for _, r in t.iterrows()]
    return t


def save_packing_draft(table: pd.DataFrame) -> None:
    """Keeps the numbers the staff have typed so far (before Done), so the 360 view can show progress such as 12/30.
    Only lines still 'to_pack' are touched; nothing is final until Done."""
    conn = connect()
    try:
        with conn:
            for _, r in table.iterrows():
                packed = None if pd.isna(r["Packed"]) else int(r["Packed"])
                damaged = 0 if pd.isna(r["Damaged"]) else int(r["Damaged"])
                if (packed is not None and packed < 0) or damaged < 0 or (packed is not None and packed + damaged > int(r["To pack"])):
                    continue                                   # a wrong number is not kept (Done refuses it with a message)
                conn.execute("""UPDATE packing_items SET packed_qty = ?, damaged_qty = ?
                                WHERE pack_id = ? AND pack_status = 'to_pack' AND trip_id IS NULL""", (packed, damaged, int(r["pack_id"])))
    finally:
        conn.close()


def save_packing(staff: str, table: pd.DataFrame, update: bool = False) -> tuple[bool, str]:
    """Packed = good units in the box; Damaged = units damaged WHILE PACKING (our mistake). Packed + Damaged <= To pack."""
    if not staff.strip():
        return False, "Please type your name first."
    if table["Packed"].isna().any():
        return False, f"{int(table['Packed'].isna().sum())} line(s) still have no Packed number."
    for _, r in table.iterrows():
        damaged = 0 if pd.isna(r["Damaged"]) else r["Damaged"]
        if r["Packed"] < 0 or damaged < 0:
            return False, f"{r['SKU']}: numbers cannot be negative."
        if r["Packed"] + damaged > r["To pack"]:
            return False, f"{r['SKU']}: packed + damaged ({int(r['Packed'] + damaged)}) is more than to pack ({int(r['To pack'])})."
    conn = connect()
    try:
        with conn:
            for _, r in table.iterrows():
                packed, damaged = int(r["Packed"]), int(0 if pd.isna(r["Damaged"]) else r["Damaged"])
                status = "packed" if packed == int(r["To pack"]) else "short"
                if update:      # a correction after Done (only until the trip exists): keep who packed it and when, add who corrected
                    old = conn.execute("SELECT packed_qty, damaged_qty, staff_note FROM packing_items WHERE pack_id = ?",
                                       (int(r["pack_id"]),)).fetchone()
                    if old and (old["packed_qty"], old["damaged_qty"]) == (packed, damaged):
                        continue
                    first = str(old["staff_note"] or "").split(";")[0] or f"Packed by {staff.strip()}"
                    stamp = datetime.datetime.now().strftime("%d %b %Y, %I:%M %p")
                    note = f"{first}; corrected by {staff.strip()} on {stamp} (was {old['packed_qty']}, damaged {old['damaged_qty']})"
                    conn.execute("""UPDATE packing_items SET packed_qty = ?, damaged_qty = ?, pack_status = ?, staff_note = ?
                                    WHERE pack_id = ? AND trip_id IS NULL AND pack_status != 'to_pack'""",
                                 (packed, damaged, status, note, int(r["pack_id"])))
                else:
                    note = f"Packed by {staff.strip()}" + (f"; {damaged} unit(s) damaged" if damaged else "")
                    conn.execute("""UPDATE packing_items SET packed_qty = ?, damaged_qty = ?, pack_status = ?, staff_note = ?,
                                    packed_at = datetime('now', 'localtime') WHERE pack_id = ? AND pack_status = 'to_pack'""",
                                 (packed, damaged, status, note, int(r["pack_id"])))
    finally:
        conn.close()
    if update:
        return True, "Corrections saved. The list stays open for changes until the trip is created."
    dmg = int(table["Damaged"].fillna(0).sum())
    return True, (f"Saved {len(table)} items ({int(table['Packed'].sum())} units packed, {dmg} unit(s) damaged while packing). "
                  f"Packed and ready to dispatch: it now waits for the trip ID.")


# ---------------- Store Replenishment App ----------------
def in_transit() -> pd.DataFrame:
    return df("""SELECT t.trip_code AS "Trip", s.store_code AS "Store", ts.boxes_count AS "Boxes", ts.total_units AS "Units packed",
                        ts.status AS "status", ts.reached_at, ts.checking_at
                 FROM trip_stores ts JOIN trips t USING (trip_id) JOIN stores s USING (store_id)
                 WHERE ts.status != 'received' ORDER BY t.trip_date, s.store_code""")


def finished_checks() -> pd.DataFrame:
    """Store checks that are done (they stay visible in the Store Replenishment App, view-only)."""
    return df("""SELECT t.trip_code AS "Trip", s.store_code AS "Store", ts.boxes_count AS "Boxes", ts.total_units AS "Units packed",
                        ts.status AS "status", ts.reached_at, ts.checking_at, r.checker_name, r.boxes_received, r.received_at
                 FROM trip_stores ts JOIN trips t USING (trip_id) JOIN stores s USING (store_id)
                 JOIN store_receipts r ON r.trip_id = ts.trip_id AND r.store_id = ts.store_id
                 WHERE ts.status = 'received' ORDER BY t.trip_date DESC, r.received_at DESC LIMIT 30""")


def receipt_view(trip: str, store: str) -> pd.DataFrame:
    t = df("""SELECT p.sku AS "SKU", p.product_name AS "Product", ri.expected_qty AS "Packed", ri.received_qty AS "Received",
                     ri.damaged_qty AS "Damaged", ri.remark AS "Remark"
              FROM receipt_items ri JOIN store_receipts r USING (receipt_id) JOIN packing_items i ON i.pack_id = ri.packing_item_id
              JOIN products p ON p.product_id = i.product_id JOIN trips tr ON tr.trip_id = r.trip_id JOIN stores s ON s.store_id = r.store_id
              WHERE tr.trip_code = ? AND s.store_code = ? ORDER BY p.sku""", (trip, store))
    t.insert(4, "Match", t["Received"] - t["Packed"])
    return t


def set_store_status(trip: str, store: str, status: str) -> None:
    """The store staff press 'Items reached' (reached) or 'Start checking' (checking). Never goes backwards; the time is saved."""
    order = {"in_transit": 0, "reached": 1, "checking": 2, "received": 3}
    now = datetime.datetime.now().isoformat(timespec="seconds")
    conn = connect()
    with conn:
        row = conn.execute("""SELECT ts.status, ts.trip_id, ts.store_id FROM trip_stores ts JOIN trips t USING (trip_id)
                              JOIN stores s USING (store_id) WHERE t.trip_code = ? AND s.store_code = ?""", (trip, store)).fetchone()
        if row and order[status] > order[row["status"]]:
            column = "reached_at" if status == "reached" else "checking_at"
            conn.execute(f"UPDATE trip_stores SET status = ?, {column} = ? WHERE trip_id = ? AND store_id = ?",
                         (status, now, row["trip_id"], row["store_id"]))
    conn.close()


def trip_alerts(trip: str) -> list[dict]:
    """The warehouse alerts of one trip (a strong banner is shown for each)."""
    return df("""SELECT s.store_code AS store, a.level, a.message FROM warehouse_alerts a JOIN trips t USING (trip_id)
                 JOIN stores s USING (store_id) WHERE t.trip_code = ?
                 ORDER BY CASE a.level WHEN 'SERIOUS' THEN 0 WHEN 'WARNING' THEN 1 ELSE 2 END, s.store_code""", (trip,)).to_dict("records")


def receiving_lines(trip: str, store: str) -> tuple[pd.DataFrame, int]:
    state = receiving.load_expected({"trip_code": trip, "store_code": store})
    lines = pd.DataFrame(state["lines"])
    table = pd.DataFrame({"pack_id": lines["pack_id"], "SKU": lines["sku"], "Product": lines["product_name"],
                          "Packed": lines["expected"], "Received": pd.array([None] * len(lines), dtype="Int64"), "Damaged": 0})
    return with_match(table), state["boxes_expected"]


def with_match(table: pd.DataFrame) -> pd.DataFrame:
    """Adds Match (received - packed) and Remark. The staff edit only Received and Damaged; the rest is calculated."""
    t = table.copy()
    t["Damaged"] = t["Damaged"].fillna(0).astype(int)
    t["Match"] = [pd.NA if pd.isna(r["Received"]) else int(r["Received"] - r["Packed"]) for _, r in t.iterrows()]
    t["Match"] = t["Match"].astype("Int64")
    t["Remark"] = ["to check" if pd.isna(r["Received"]) else receiving.remark_for(int(r["Packed"]), int(r["Received"]), int(r["Damaged"]))
                   for _, r in t.iterrows()]
    return t


def submit_receipt(trip: str, store: str, checker: str, boxes: int, table: pd.DataFrame) -> dict:
    if not checker.strip():
        raise ValueError("Please type your name first.")
    if table["Received"].isna().any():
        raise ValueError("Some lines are not checked yet.")
    for _, r in table.iterrows():
        if r["Received"] < 0 or r["Damaged"] < 0 or r["Damaged"] > r["Received"]:
            raise ValueError(f"{r['SKU']}: damaged cannot be more than received, and numbers cannot be negative.")
    items = [{"pack_id": int(r["pack_id"]), "expected": int(r["Packed"]), "received": int(r["Received"]),
              "damaged": int(r["Damaged"])} for _, r in table.iterrows()]
    return receiving.run(trip, store, False, lambda q: {"checker": checker.strip(), "boxes_received": int(boxes), "items": items})


# ---------------- Agent 4: the new-product screen ----------------
def a4_options() -> dict:
    import agent4_new_product
    return agent4_new_product.options()


def a4_suggest(name: str):
    import agent4_new_product
    return agent4_new_product.suggest_category(name)


def a4_propose(form: dict) -> dict:
    import agent4_new_product
    return agent4_new_product.propose(form)


def a4_save(proposal: dict, who: str) -> dict:
    import agent4_new_product
    return agent4_new_product.save(proposal, who)


# ---------------- helpers for the whole chain ----------------
def change_signature(drafts: bool = True) -> str:
    """A short fingerprint of the work in progress: when it changes, the open pages reload themselves."""
    conn = connect()
    try:
        parts = [conn.execute(q).fetchone()[0] for q in (
            "SELECT COUNT(*) || '/' || COALESCE(MAX(request_id), 0) || '/' || SUM(status = 'pending') FROM restock_requests",
            "SELECT COUNT(*) || '/' || SUM(status = 'printed') FROM barcode_requests",
            "SELECT COUNT(*) || '/' || SUM(pack_status = 'to_pack') || '/' || SUM(trip_id IS NOT NULL)"
            + (" || '/' || SUM(packed_qty IS NOT NULL)" if drafts else "") + " FROM packing_items",
            "SELECT COUNT(*) || '/' || COALESCE(GROUP_CONCAT(status), '') FROM trip_stores",
            "SELECT COUNT(*) || '/' || SUM(acknowledged) FROM warehouse_alerts")]
    finally:
        conn.close()
    return "|".join(str(p) for p in parts)


def generate_trip() -> None:
    """The button on the Warehouse Packaging Tracker: Agent 2 plans the trip of the chosen date for the stores that are packed."""
    import agent2_trip
    import streamlit as st
    day = st.session_state.get("trip_day", today())
    out = agent2_trip.run_agent(True, day.isoformat(), lambda q: {})["result"]
    if out.get("trip_code"):
        stores = ", ".join(code for code, _b, _u in out["stores"])
        st_msg = f"Trip {out['trip_code']} created for {stores}. It is now in the Store Replenishment App."
    else:
        ready = packing_progress()
        ready = ready[ready["Stage"] != "Packing in process"]["Store"].tolist()
        days = "; ".join(f"{c}: {trip_day_of(c)}" for c in ready)
        st_msg = ("No trip created. " + (out.get("note") or
                  (f"Still being packed: {', '.join(out['waiting'])}." if out.get("waiting") else
                   "A trip leaves only on the store's scheduled weekday" + (f" ({days})." if days else "."))))
    st.session_state["trip_msg"] = st_msg


# ---------------- the rolling news strip and the weekly delivery plan ----------------
def ticker_items(day: str) -> list[tuple[str, str]]:
    """(tag, text) lines for the rolling strip: the newest things first."""
    act = activities(day)
    items = [("NEW", t) for t in act["new_requests"]]
    items += [("PACKED", t) for t in act["ready"]]
    for line in act["pending"]:
        items.append(("PENDING", line))
    for line in act["today"]:
        if "Damaged while packing" not in line:
            items.append(("TODAY", line))
    for al in open_alerts():
        items.append((al["level"], al["message"]))
    return items


def schedule_plan(day: str) -> list[dict]:
    """The week at a glance: which stores the vehicle visits on which day (read from the delivery schedule)."""
    d = datetime.date.fromisoformat(day)
    rows = df("""SELECT t.weekday, s.store_code FROM trip_schedule t JOIN stores s USING (store_id) ORDER BY t.weekday, s.store_code""")
    stores = {w: ", ".join(g["store_code"].str.replace("Store ", "")) for w, g in rows.groupby("weekday")}
    plan = []
    for w in range(1, 8):
        text = (stores.get(w) or ("Packing day, no trip" if w == 1 else "Urgent restocks only" if w == 5 else "Peak sales, no trip"))
        plan.append({"day": WEEKDAYS[w][:3], "full": WEEKDAYS[w], "text": ("Stores " + text) if w in stores else text,
                     "today": d.isoweekday() == w, "tomorrow": (d + datetime.timedelta(days=1)).isoweekday() == w})
    return plan


# ---------------- front page: status tiles and the store pipeline board ----------------
def status_tiles(day: str) -> dict:
    """The numbers for the tiles on the 360 home page: workflow tiles (what is waiting where) and sales tiles."""
    one = lambda q, a=(): df(q, a).iloc[0, 0]
    req_items = int(one("SELECT COUNT(*) FROM restock_requests WHERE status = 'pending'"))
    req_stores = int(one("SELECT COUNT(DISTINCT store_id) FROM restock_requests WHERE status = 'pending'"))
    labels = int(one("SELECT COUNT(DISTINCT store_id) FROM barcode_requests WHERE status = 'requested'"))
    packing = int(one("SELECT COUNT(DISTINCT store_id) FROM packing_items i WHERE i.pack_status = 'to_pack'"))
    ready = len(packing_done_lists())
    way = int(one("SELECT COUNT(*) FROM trip_stores WHERE status != 'received'"))
    alerts = open_alerts()
    serious = any(a["level"] == "SERIOUS" for a in alerts)
    top = top_stores()
    month = month_comparison()
    return {"workflow": [
        {"label": "New requests", "value": req_items, "sub": f"items from {req_stores} store(s)", "tone": "warn" if req_items else "idle"},
        {"label": "Labels to print", "value": labels, "sub": "store(s) waiting", "tone": "warn" if labels else "idle"},
        {"label": "Packing in process", "value": packing, "sub": packing_sub(), "tone": "info" if packing else "idle"},
        {"label": "Ready to dispatch", "value": ready, "sub": "store(s) waiting for trip ID", "tone": "info" if ready else "idle"},
        {"label": "On a trip", "value": way, "sub": "store(s) on the way or checking", "tone": "info" if way else "idle"},
        {"label": "Open alerts", "value": len(alerts), "sub": "serious: act now" if serious else "not yet read",
         "tone": "bad" if serious else "warn" if alerts else "ok"}],
        "sales": [
        {"label": "Sales, last 30 days", "value": f"Rs {int(top['Sales (Rs)'].sum()):,}", "sub": f"{int(top['Units sold'].sum()):,} units sold", "tone": "info"},
        {"label": "This month vs last month", "value": ("-" if not month or month["total"]["pct_of_prev"] is None else f"{month['total']['pct_of_prev']:.0f}%"),
         "sub": "of last month's sales done so far", "tone": "ok"},
        {"label": "Best store", "value": top.iloc[0]["Store"], "sub": f"Rs {int(top.iloc[0]['Sales (Rs)']):,} in 30 days", "tone": "ok"},
        {"label": "Next trip day", "value": next_trip_day(day), "sub": "stores due that day are listed below", "tone": "info"}]}


def packing_sub() -> str:
    """'A 12/30, B 0/20': lines the staff have typed a packed number for, out of all lines of each store."""
    prog = packing_progress()
    prog = prog[prog["Stage"] == "Packing in process"]
    if prog.empty:
        return "store(s)"
    parts = [f"{r.Store.replace('Store ', '')} {int(r._3)}/{int(r.Lines)}" for r in prog.itertuples()]
    return ", ".join(parts[:4]) + (f" +{len(parts) - 4} more" if len(parts) > 4 else "")


def next_trip_day(day: str) -> str:
    d = datetime.date.fromisoformat(day)
    scheduled = set(df("SELECT DISTINCT weekday FROM trip_schedule")["weekday"])
    for k in range(0, 8):
        x = d + datetime.timedelta(days=k)
        if x.isoweekday() in scheduled:
            return "Today" if k == 0 else WEEKDAYS[x.isoweekday()][:3]
    return "-"


def pipeline_board() -> list[dict]:
    """One row per store: Request > Agent 1 > Labels > Packing > Trip > Receiving, each cell (text, state).
    state: done (green), now (blue), wait (amber), todo (grey)."""
    conn = connect()
    rows = []
    for st_ in conn.execute("SELECT store_id, store_code FROM stores WHERE is_active = 1 ORDER BY store_code").fetchall():
        sid, code = st_["store_id"], st_["store_code"]
        q = lambda sql, *a: conn.execute(sql, (sid, *a)).fetchone()
        pend = q("SELECT COUNT(*) FROM restock_requests WHERE store_id = ? AND status = 'pending'")[0]
        open_items = q("""SELECT COUNT(*), SUM(i.pack_status = 'to_pack'), COUNT(i.trip_id), MAX(t.trip_code), MAX(ts.status)
                          FROM packing_items i LEFT JOIN trips t ON t.trip_id = i.trip_id
                          LEFT JOIN trip_stores ts ON ts.trip_id = i.trip_id AND ts.store_id = i.store_id
                          WHERE i.store_id = ? AND (i.trip_id IS NULL OR ts.status != 'received')""")
        n_items, n_open, n_on_trip, trip_code, trip_status = open_items
        entered, in_list = q("SELECT COALESCE(SUM(packed_qty IS NOT NULL), 0), COUNT(*) FROM packing_items WHERE store_id = ? AND trip_id IS NULL")
        lab = q("SELECT COUNT(*), SUM(status = 'requested') FROM barcode_requests WHERE store_id = ? AND status IN ('requested','printed') "
                "AND barcode_request_id IN (SELECT barcode_request_id FROM packing_items WHERE store_id = ? AND (trip_id IS NULL OR trip_id IN "
                "(SELECT trip_id FROM trip_stores WHERE store_id = ? AND status != 'received')))", sid, sid) if n_items else None
        cells = [("-", "todo")] * 6
        if n_items:
            unprinted = bool(lab and lab[1])
            cells = [(f"{n_items} items", "done"), (f"{n_items} ok", "done"),
                     ("to print", "wait") if unprinted else ("printed", "done"),
                     ("after labels", "todo") if unprinted else
                     ((f"{entered}/{in_list}", "now") if n_open else ("packed", "done")),
                     (trip_code[5:] if n_on_trip else ("no trip yet" if not n_open else "-"), "done" if n_on_trip else ("wait" if not n_open else "todo")),
                     ({"in_transit": "in transit", "reached": "reached store", "checking": "checking"}.get(trip_status, "-"), "now" if trip_status else "todo")]
        elif pend:
            cells = [(f"{pend} waiting", "wait")] + [("-", "todo")] * 5
        else:
            last = q("""SELECT MAX(r.received_at) FROM store_receipts r WHERE r.store_id = ?""")[0]
            cells = [("-", "todo")] * 5 + [(("done " + short_time(last).split(",")[0]) if last else "idle", "done" if last else "todo")]
        rows.append({"store": code, "cells": cells})
    conn.close()
    return rows


def action_items(day: str) -> list[dict]:
    """The Action centre: what needs doing now, who does it, and which screen to open. Most urgent first."""
    items = []
    alerts = open_alerts()
    if alerts:
        worst = "bad" if any(a["level"] == "SERIOUS" for a in alerts) else "warn"
        items.append({"tone": worst, "who": "Team lead", "page": "Store Receiving Tracker",
                      "text": f"{len(alerts)} warehouse alert(s) from store checks: find where the fault is and report it."})
    new = df("""SELECT s.store_code, COUNT(*) AS n FROM restock_requests r JOIN stores s USING (store_id)
                WHERE r.status = 'pending' GROUP BY s.store_code ORDER BY s.store_code""")
    if len(new):
        items.append({"tone": "warn", "who": "Warehouse manager", "page": "Agent 1 Packing List",
                      "text": f"Review the packing list for {len(new)} store(s) with new requests ({int(new['n'].sum())} items): "
                              f"{', '.join(c.replace('Store ', '') for c in new['store_code'])}."})
    lab = df("""SELECT s.store_code FROM barcode_requests b JOIN stores s USING (store_id) WHERE b.status = 'requested'
                GROUP BY s.store_code ORDER BY s.store_code""")["store_code"].tolist()
    if lab:
        items.append({"tone": "warn", "who": "Label printing", "page": "Barcode Request Tracker",
                      "text": f"Print the barcode labels for {', '.join(c.replace('Store ', '') for c in lab)}, then press Labels printed."})
    prog = packing_progress()
    for r in prog[prog["Stage"] == "Packing in process"].itertuples():
        left = int(r.Lines - r._3)
        items.append({"tone": "info", "who": "Warehouse staff", "page": "Warehouse Packaging Tracker",
                      "text": f"{r.Store}: {left} line(s) left to pack (open the Warehouse Packaging App)."})
    wd = datetime.date.fromisoformat(day).isoweekday()
    for r in prog[prog["Stage"] != "Packing in process"].itertuples():
        due = WEEKDAYS[wd] == r._9 or wd == 5
        items.append({"tone": "warn" if due else "info", "who": "Warehouse manager", "page": "Warehouse Packaging Tracker",
                      "text": f"{r.Store} is packed and ready: " + ("generate the trip ID now." if due else f"waiting for its trip day ({r._9}).")})
    for r in in_transit().itertuples():
        items.append({"tone": "info", "who": "Store staff", "page": "Store Receiving Tracker",
                      "text": f"{r.Store} ({r.Trip}): " + {"in_transit": "items on the way, confirm when they reach the store.",
                                                            "reached": "items reached, start checking.",
                                                            "checking": "checking in progress, press Done when finished."}[r.status]})
    order = {"bad": 0, "warn": 1, "info": 2}
    return sorted(items, key=lambda i: order[i["tone"]])


def db_problem() -> str | None:
    """None when the database matches the app files; otherwise a plain sentence saying what to do."""
    if not DB_FILE.exists():
        return "The database file (data/retail.db) was not found. Run:  python build_database.py"
    conn = connect()
    try:
        need = {"restock_requests": "requested_at", "trip_stores": "reached_at", "store_stock": "store_qty"}
        for table, column in need.items():
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
            if column not in cols:
                return (f"Your database is older than the app files (table '{table}' has no '{column}'). "
                        "Stop the apps, then run:  python build_database.py   and type  yes  when it asks.")
    finally:
        conn.close()
    return None


# ---------------- history views (last 12 months) ----------------
HISTORY_DAYS = 365


def history_start() -> str:
    return (today() - datetime.timedelta(days=HISTORY_DAYS)).isoformat()


def packing_history(search: str = "", store: str = "All") -> tuple[pd.DataFrame, dict]:
    """Warehouse staff: which product was packed for which store, how many times (trips) and how many units, in the last year."""
    like = f"%{search.strip()}%"
    t = df("""SELECT p.sku AS "SKU", p.product_name AS "Product", s.store_code AS "Store", COUNT(DISTINCT i.trip_id) AS "Times packed",
                     SUM(i.packed_qty) AS "Total packed", SUM(i.damaged_qty) AS "Damaged while packing", MAX(tr.trip_date) AS last_trip
              FROM packing_items i JOIN trips tr ON tr.trip_id = i.trip_id JOIN products p ON p.product_id = i.product_id
              JOIN stores s ON s.store_id = i.store_id
              WHERE tr.trip_date >= ? AND i.packed_qty > 0 AND (p.product_name LIKE ? OR p.sku LIKE ?) AND (? = 'All' OR s.store_code = ?)
              GROUP BY p.product_id, s.store_id ORDER BY p.product_name, s.store_code""", (history_start(), like, like, store, store))
    t.insert(len(t.columns), "Last packed", t["last_trip"].map(nice))
    t = t.drop(columns=["last_trip"])
    trips = df("""SELECT COUNT(DISTINCT i.trip_id) AS n FROM packing_items i JOIN trips tr ON tr.trip_id = i.trip_id
                  JOIN products p ON p.product_id = i.product_id JOIN stores s ON s.store_id = i.store_id
                  WHERE tr.trip_date >= ? AND i.packed_qty > 0 AND (p.product_name LIKE ? OR p.sku LIKE ?) AND (? = 'All' OR s.store_code = ?)""",
               (history_start(), like, like, store, store))["n"].iloc[0]
    totals = {"products": int(t["SKU"].nunique()), "trips": int(trips), "packed": int(t["Total packed"].sum()) if len(t) else 0,
              "damaged": int(t["Damaged while packing"].sum()) if len(t) else 0}
    return t, totals


_STORE_LINES = """
    FROM packing_items i JOIN trips tr ON tr.trip_id = i.trip_id JOIN stores s ON s.store_id = i.store_id
    JOIN products p ON p.product_id = i.product_id JOIN restock_requests rq ON rq.request_id = i.request_id
    LEFT JOIN receipt_items ri ON ri.packing_item_id = i.pack_id LEFT JOIN store_receipts sr ON sr.receipt_id = ri.receipt_id
    WHERE s.store_code = ? AND tr.trip_date >= ? AND i.packed_qty > 0 """


def _store_line_table(rows: pd.DataFrame) -> pd.DataFrame:
    rows["Received date"] = rows["received_at"].map(lambda v: "Not received yet" if pd.isna(v) else nice(str(v)[:10]))
    for c in ("Requested qty", "Packed qty", "Received qty", "Damaged qty"):
        rows[c] = rows[c].astype("Int64")
    return rows.drop(columns=["received_at"])


def store_trip_list(store: str) -> pd.DataFrame:
    """Trips of this store in the last 12 months, newest first."""
    return df("SELECT DISTINCT tr.trip_code AS code, tr.trip_date AS day " + _STORE_LINES + " ORDER BY tr.trip_date DESC, tr.trip_code DESC",
              (store, history_start()))


def store_trip_view(store: str, trip_code: str) -> tuple[pd.DataFrame, dict]:
    t = df("""SELECT p.product_name AS "Product", rq.requested_qty AS "Requested qty", i.packed_qty AS "Packed qty",
                     ri.received_qty AS "Received qty", ri.damaged_qty AS "Damaged qty", sr.received_at AS received_at """
           + _STORE_LINES + " AND tr.trip_code = ? ORDER BY p.product_name", (store, history_start(), trip_code))
    t = _store_line_table(t)
    t.insert(1, "Trip ID", trip_code)
    return t, _totals(t, trips=1)


def store_product_list(store: str, search: str = "") -> pd.DataFrame:
    like = f"%{search.strip()}%"
    return df("SELECT DISTINCT p.product_id AS pid, p.sku AS sku, p.product_name AS name " + _STORE_LINES
              + " AND (p.product_name LIKE ? OR p.sku LIKE ?) ORDER BY p.product_name LIMIT 300", (store, history_start(), like, like))


def store_product_view(store: str, product_id: int) -> tuple[pd.DataFrame, dict]:
    t = df("""SELECT p.product_name AS "Product", tr.trip_code AS "Trip ID", rq.requested_qty AS "Requested qty", i.packed_qty AS "Packed qty",
                     ri.received_qty AS "Received qty", ri.damaged_qty AS "Damaged qty", sr.received_at AS received_at, tr.trip_date AS day """
           + _STORE_LINES + " AND p.product_id = ? ORDER BY tr.trip_date DESC, tr.trip_code DESC", (store, history_start(), product_id))
    t = _store_line_table(t.drop(columns=["day"]))
    return t, _totals(t, trips=int(t["Trip ID"].nunique()))


def _totals(t: pd.DataFrame, trips: int) -> dict:
    s = lambda c: int(t[c].fillna(0).sum()) if len(t) else 0
    return {"trips": trips, "requested": s("Requested qty"), "packed": s("Packed qty"), "received": s("Received qty"), "damaged": s("Damaged qty")}


# ---------------- Store Stock Request App: bulk upload by CSV ----------------
REQUEST_CSV_TEMPLATE = "SKU,Request qty,Note\n"
MAX_CSV_ROWS = 500


def request_csv_check(store: str, raw: pd.DataFrame) -> tuple[list[dict], pd.DataFrame]:
    """Checks an uploaded CSV row by row (same rules as the one-by-one form). Returns (good rows, a table of every row with its result).
    Nothing is saved here; a row that breaks a rule is skipped with the reason, the good rows still go through."""
    cols = {str(c).strip().lower(): c for c in raw.columns}
    sku_col = cols.get("sku")
    qty_col = next((cols[k] for k in ("request qty", "request_qty", "qty", "quantity") if k in cols), None)
    note_col = cols.get("note")
    if sku_col is None or qty_col is None:
        raise ValueError("The file needs the columns SKU and Request qty (and optionally Note). Download the template to start.")
    if len(raw) > MAX_CSV_ROWS:
        raise ValueError(f"The file has {len(raw)} rows; the limit is {MAX_CSV_ROWS} per upload.")
    skus = [str(v).strip() for v in raw[sku_col] if pd.notna(v) and str(v).strip()]
    info = {}
    if skus:
        marks = ",".join("?" * len(skus))
        for r in df(f"""SELECT p.product_id, p.sku, p.product_name, p.is_active,
                               (SELECT pr.selling_price FROM product_prices pr WHERE pr.product_id = p.product_id
                                ORDER BY pr.effective_from DESC LIMIT 1) AS price,
                               COALESCE((SELECT SUM(w.available_qty) FROM warehouse_stock w WHERE w.product_id = p.product_id), 0) AS inv
                        FROM products p WHERE p.sku IN ({marks})""", tuple(skus)).itertuples():
            info[r.sku] = r
    good, report, seen = [], [], set()
    for n, (_, row) in enumerate(raw.iterrows(), start=2):          # row 1 of the file is the header
        sku = "" if pd.isna(row[sku_col]) else str(row[sku_col]).strip()
        raw_qty = row[qty_col]
        note = "" if note_col is None or pd.isna(row[note_col]) else str(row[note_col]).strip()
        reason = None
        qty = None
        if not sku:
            reason = "SKU is empty"
        else:
            try:
                qty_f = float(raw_qty)
                qty = int(qty_f)
                if qty != qty_f:
                    reason, qty = "Request qty must be a whole number", None
                elif qty <= 0:
                    reason, qty = "Request qty must be more than 0", None
            except (TypeError, ValueError):
                reason = "Request qty is not a number"
        if reason is None:
            p = info.get(sku)
            if p is None:
                reason = "SKU not found"
            elif not p.is_active:
                reason = "product is not active"
            elif sku in seen:
                reason = "SKU appears twice in the file"
            elif p.inv <= 0:
                reason = "out of stock in the warehouse (OS)"
            elif qty > p.inv:
                reason = f"more than the warehouse quantity ({int(p.inv)})"
            elif already_requested_today(store, int(p.product_id)):
                reason = "this store already requested it today"
            elif len(note) > guardrails.MAX_REQUEST_NOTE_CHARS:
                reason = f"Note is too long (at most {guardrails.MAX_REQUEST_NOTE_CHARS} characters)"
        if reason is None:
            seen.add(sku)
            good.append({"product_id": int(p.product_id), "SKU": p.sku, "Product Name": p.product_name,
                         "Selling price": "-" if pd.isna(p.price) else f"{p.price:,.0f}", "Inventory qty": int(p.inv),
                         "Request qty": qty, "Note": note})
        report.append({"File row": n, "SKU": sku or "-", "Request qty": "" if pd.isna(raw_qty) else str(raw_qty), "Result": "OK" if reason is None else f"Skipped: {reason}"})
    return good, pd.DataFrame(report)
