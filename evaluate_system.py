"""Step 16: the evaluation. It pretends to be store staff, warehouse staff and a store checker, sends many SIMULATED
orders (some of them deliberately bad) through the real agents, and checks that every guardrail rule held.

It works on a COPY of the database in a temporary folder: your real data (data/retail.db) is never changed, no e-mail is
sent and the AI is not called. Run it from the project folder:

    python evaluate_system.py

Result: a table on the screen and the file  evaluation_report.md  (put it in the README as evidence).
"""
import datetime
import os
import random
import shutil
import sqlite3
import sys
import tempfile
import types
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import guardrails
import allocation
import agent1_packing as a1
import agent2_trip as a2
import agent3_report as a3
import agent4_new_product as a4
import alerts
import notify
import screen_logic as sl
import store_receiving as rcv
import warehouse_view

# ---------- safety: work on a copy, never send e-mail, never call the AI ----------
WORK = Path(tempfile.mkdtemp(prefix="retail_eval_"))
COPY = WORK / "retail_copy.db"
if not (ROOT / "data" / "retail.db").exists():
    raise SystemExit("data/retail.db not found. Run:  python build_database.py")
shutil.copy(ROOT / "data" / "retail.db", COPY)
for module in (a1, a2, a3, a4, alerts, rcv, sl, warehouse_view):
    module.DB_FILE = COPY
notify.ROOT = WORK                                   # e-mail copies go to the temp folder
for key in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "GOOGLE_API_KEY", "OPS_HEAD_EMAIL", "WH_MANAGER_EMAIL"):
    os.environ.pop(key, None)

RESULTS: list = []          # (rule id, scenario, passed, detail)


def check(rule: str, scenario: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((rule, scenario, bool(ok), detail))
    if not ok:
        print(f"  FAILED  {rule}  {scenario}  {detail}")
    return bool(ok)


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(COPY)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def one(sql: str, params: tuple = ()):
    conn = db()
    try:
        row = conn.execute(sql, params).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def run_sql(sql: str, params: tuple = ()) -> None:
    conn = db()
    with conn:
        conn.execute(sql, params)
    conn.close()


def stock_snapshot() -> dict:
    conn = db()
    rows = {r[0]: r[1] for r in conn.execute("SELECT product_id, SUM(available_qty) FROM warehouse_stock GROUP BY product_id")}
    conn.close()
    return rows


def counts(*tables: str) -> tuple:
    return tuple(one(f"SELECT COUNT(*) FROM {t}") for t in tables)


# ---------- 0. start from a clean 'nothing processed yet' state (the copy only) ----------
def reset_copy() -> None:
    conn = db()
    with conn:
        conn.execute("""UPDATE warehouse_stock SET available_qty = available_qty + COALESCE(
            (SELECT SUM(i.packed_qty + i.damaged_qty) FROM packing_items i
             WHERE i.product_id = warehouse_stock.product_id AND i.trip_id IS NOT NULL), 0)""")
        for table in ("receipt_items", "store_receipts", "warehouse_alerts", "trip_reports", "box_items", "boxes", "trip_stores",
                      "packing_items", "trips", "barcode_requests", "allocation_edits", "agent_runs", "id_counters"):
            conn.execute(f"DELETE FROM {table}")
        conn.execute("UPDATE restock_requests SET status='pending', approved_qty=NULL, decision_reason=NULL WHERE source='store_request'")
    conn.close()


# ---------- 1. the allocation rules (pure code): thousands of random cases ----------
def scenario_allocation_rules() -> None:
    rng = random.Random(42)
    bad = []
    for n in range(3000):
        stock = rng.choice([0, 1, 2, 5, 10, 37, 100, 500])
        reqs = []
        for rid in range(rng.randint(1, 6)):
            reqs.append((rid, rng.randint(0, 40), rng.randint(1, 40), rng.choice([21, 14, 9]),
                         rng.choice([0, 0, 3, 20, 80]), rng.random() < 0.15, rng.choice([None, 28, 14, 10])))
        got = allocation.allocate_repeat_item(stock, reqs)
        need = {r[0]: allocation.need_quantity(r[1], r[2], r[4], bool(r[5]), r[6]) for r in reqs}
        again = allocation.allocate_repeat_item(stock, reqs)
        if (sum(got.values()) > stock or any(v < 0 or v > need[k] or not isinstance(v, int) for k, v in got.items())
                or got != again or (sum(need.values()) >= stock and sum(got.values()) != min(stock, sum(need.values())))):
            bad.append((stock, reqs, got))
    check("R03", "3000 random allocations: total handed out never above the stock", not bad, str(bad[:1]))
    check("R04", "3000 random allocations: nobody gets more than it needs, never negative, whole numbers", not bad)
    check("R05", "3000 random allocations: same input gives the same answer, and no stock is left unused while a store still needs it", not bad)
    T, A, L = 21, 14, 9
    check("R05", "short stock 10 -> top+average 6:4, top+low 7:3, top+top 5:5",
          allocation.allocate_repeat_item(10, [(1, 10, 10, T), (2, 10, 10, A)]) == {1: 6, 2: 4}
          and allocation.allocate_repeat_item(10, [(1, 10, 10, T), (2, 10, 10, L)]) == {1: 7, 2: 3}
          and allocation.allocate_repeat_item(10, [(1, 10, 10, T), (2, 10, 10, T)]) == {1: 5, 2: 5})
    check("R06", "store already holds plenty (8 on shelf, sold 3) -> need 0; with an explaining Note -> packs",
          allocation.need_quantity(3, 10, store_qty=8) == 0 and allocation.need_quantity(3, 10, store_qty=8, has_note=True) == 10)
    check("R04", "asked 30 but sold 12, no Note -> 12; with Note -> 30; asked 10 sold 12 -> 10",
          allocation.need_quantity(12, 30) == 12 and allocation.need_quantity(12, 30, has_note=True) == 30
          and allocation.need_quantity(12, 10) == 10)


# ---------- 2. store staff raise requests (good and bad) ----------
def request_table(store_filter: str = "") -> pd.DataFrame:
    t, _ = sl.request_catalog(store_filter)
    return t.reset_index(drop=True)


def scenario_store_requests() -> None:
    table = request_table()
    check("R01", "request catalogue lists only items the warehouse can give", (table["Inventory qty"] > 0).all())
    before = counts("restock_requests")
    pick = table.sort_values("Inventory qty", ascending=False).iloc[0]
    row = lambda qty, note="": pd.DataFrame([{"product_id": pick["product_id"], "SKU": pick["SKU"], "Inventory qty": pick["Inventory qty"],
                                              "Request qty": qty, "Note": note}])
    check("R01", "request MORE than the warehouse holds is refused", sl.raise_requests("Store A", "Asha", row(int(pick["Inventory qty"]) + 1))[0] is False)
    check("R01", "request of 0 is refused", sl.raise_requests("Store A", "Asha", row(0))[0] is False)
    check("R01", "negative request is refused", sl.raise_requests("Store A", "Asha", row(-5))[0] is False)
    check("R01", "fractional request (2.5) is refused", sl.raise_requests("Store A", "Asha", row(2.5))[0] is False)
    check("R01", "empty staff name is refused", sl.raise_requests("Store A", "  ", row(1))[0] is False)
    check("R01", "a 500-character Note is refused", sl.raise_requests("Store A", "Asha", row(1, "x" * 500))[0] is False)
    check("R01", "a 200-character staff name is refused", sl.raise_requests("Store A", "A" * 200, row(1))[0] is False)
    check("R01", "none of the refused requests was saved", counts("restock_requests") == before)
    ok, msg = sl.raise_requests("Store A", "Asha", row(1))
    check("R01", "a valid request is saved, with a date and time", ok and counts("restock_requests")[0] == before[0] + 1
          and one("SELECT requested_at FROM restock_requests ORDER BY request_id DESC LIMIT 1") is not None, msg)
    after_ok = counts("restock_requests")
    check("R02", "the same item for the same store on the same day is refused", sl.raise_requests("Store A", "Asha", row(1))[0] is False)
    two = pd.concat([row(1), table[table["product_id"] != pick["product_id"]].head(1).assign(**{"Request qty": 1, "Note": ""})[row(1).columns]])
    ok, _ = sl.raise_requests("Store A", "Asha", two)
    check("R02", "a list with one duplicate saves NOTHING (all or nothing)", ok is False and counts("restock_requests") == after_ok)


# ---------- 3. tricky orders written straight into the copy ----------
TRICKY: dict = {}


def scenario_tricky_orders() -> None:
    conn = db()
    today = datetime.date.today().isoformat()
    stores = [r[0] for r in conn.execute("SELECT store_code FROM stores ORDER BY store_code")]
    ids = {r[1]: r[0] for r in conn.execute("SELECT store_id, store_code FROM stores")}
    free = [r[0] for r in conn.execute("""SELECT product_id FROM warehouse_stock WHERE available_qty > 0 AND product_id NOT IN
                                         (SELECT product_id FROM restock_requests) ORDER BY product_id LIMIT 60""")]
    zero = [r[0] for r in conn.execute("""SELECT w.product_id FROM warehouse_stock w WHERE w.available_qty = 0 AND w.product_id NOT IN
                                         (SELECT product_id FROM restock_requests) ORDER BY w.product_id LIMIT 5""")]
    with conn:
        # a) contention: ONE product with 6 units, all 8 stores ask for 20
        contested = free[0]
        conn.execute("UPDATE warehouse_stock SET available_qty = 6 WHERE product_id = ?", (contested,))
        for s in stores:
            conn.execute("INSERT INTO restock_requests (request_date, requested_at, store_id, product_id, requested_qty, requested_by) "
                         "VALUES (?, ?, ?, ?, 20, 'Eval')", (today, f"{today} 10:00:00", ids[s], contested))
        # b) an item that is out of stock in the warehouse
        for p in zero[:2]:
            conn.execute("INSERT INTO restock_requests (request_date, requested_at, store_id, product_id, requested_qty, requested_by) "
                         "VALUES (?, ?, ?, ?, 5, 'Eval')", (today, f"{today} 10:05:00", ids["Store B"], p))
        # c) store already has plenty (500 on the shelf), small sales, no Note / with Note
        plenty_a, plenty_b = free[1], free[2]
        for p, note in ((plenty_a, None), (plenty_b, "Big weekend sale, please send")):
            conn.execute("INSERT INTO restock_requests (request_date, requested_at, store_id, product_id, requested_qty, requested_by, request_note) "
                         "VALUES (?, ?, ?, ?, 10, 'Eval', ?)", (today, f"{today} 10:10:00", ids["Store G"], p, note))
            conn.execute("INSERT INTO sales (sale_date, store_id, product_id, quantity, net_amount) VALUES ('2025-08-20', ?, ?, 2, 200)",
                         (ids["Store G"], p))                         # it sold only 2 in the month, so 500 on the shelf is far too much
            conn.execute("INSERT OR REPLACE INTO store_stock (store_id, product_id, snapshot_date, store_qty) VALUES (?, ?, '2099-01-01', 500)",
                         (ids["Store G"], p))
            conn.execute("UPDATE warehouse_stock SET available_qty = 100 WHERE product_id = ?", (p,))
        # d) a huge ask for an item with little stock
        huge = free[3]
        conn.execute("UPDATE warehouse_stock SET available_qty = 7 WHERE product_id = ?", (huge,))
        conn.execute("INSERT INTO restock_requests (request_date, requested_at, store_id, product_id, requested_qty, requested_by) "
                     "VALUES (?, ?, ?, ?, 7, 'Eval')", (today, f"{today} 10:15:00", ids["Store C"], huge))
    conn.close()
    TRICKY.update(contested=contested, zero=zero[:2], plenty_a=plenty_a, plenty_b=plenty_b, huge=huge)


# ---------- 4. Agent 1 proposes; a person edits; the live-stock guard ----------
STATE: dict = {}


def scenario_agent1() -> None:
    state = a1.propose()
    STATE["state"] = state
    decisions = state["decisions"]
    check("R04", f"Agent 1 produced a decision for every pending request ({len(decisions)})",
          len(decisions) == one("SELECT COUNT(*) FROM restock_requests WHERE status = 'pending'"))
    check("R04", "no proposal is negative, above what the store asked, or not a whole number",
          all(isinstance(d["approved_qty"], int) and 0 <= d["approved_qty"] <= d["requested_qty"] for d in decisions))
    check("R04", "status and quantity agree (approved only when quantity > 0)",
          all((d["status"] == "approved") == (d["approved_qty"] > 0) for d in decisions))
    by_product: dict = {}
    for d in decisions:
        by_product.setdefault(d["product_id"], []).append(d)
    over = [p for p, ds in by_product.items() if sum(d["approved_qty"] for d in ds) > max(ds[0]["in_stock"] - ds[0]["already_promised"], 0)]
    check("R03", f"in all {len(by_product)} products the total handed out is within the warehouse stock", not over, str(over[:3]))
    zero_ok = all(d["approved_qty"] == 0 and d["status"] == "out_of_stock" for p in TRICKY["zero"] for d in by_product.get(p, []))
    check("R03", "an item with zero warehouse stock is marked 'out of stock' and nothing is packed", zero_ok)
    c = by_product[TRICKY["contested"]]
    check("R03", "contested item: 6 units, 8 stores each ask 20 -> exactly 6 units are handed out", sum(d["approved_qty"] for d in c) == 6,
          str([(d["store_code"], d["approved_qty"]) for d in c]))
    rank = {"top": 3, "average": 2, "low": 1}
    ordered = sorted(c, key=lambda d: -rank[d["tier"]])
    best_low = max((d["approved_qty"] for d in c if d["tier"] == "low"), default=0)
    best_top = max((d["approved_qty"] for d in c if d["tier"] == "top"), default=0)
    check("R05", "contested item: a top store is never given less than a low store", best_top >= best_low, str([(d["store_code"], d["tier"], d["approved_qty"]) for d in ordered]))
    pa = by_product[TRICKY["plenty_a"]][0]
    pb = by_product[TRICKY["plenty_b"]][0]
    check("R06", "store with 500 on the shelf and no Note: nothing is packed ('not needed')", pa["approved_qty"] == 0 and pa["status"] == "not_needed", pa["reason"])
    check("R06", "same store, but the Note explains a big sale: Agent 1 packs it", pb["approved_qty"] > 0, pb["reason"])
    h = by_product[TRICKY["huge"]][0]
    check("R03", "a request equal to the whole stock never gets more than the stock", h["approved_qty"] <= 7)
    check("R04", "every proposal has a reason in plain words", all(len(d["reason"]) > 10 for d in decisions))

    # --- a person edits (R07) ---
    cdec = c[0]
    d_state = {k: (dict(v) if isinstance(v, dict) else v) for k, v in state.items()}
    dec = {d["request_id"]: dict(d) for d in decisions}
    errors = []
    msg = a1.apply_edit(dec, state["original_qty"], errors, {"request_id": cdec["request_id"], "qty": -3})
    check("R07", "edit to a negative number is refused", msg is not None, str(msg))
    msg = a1.apply_edit(dec, state["original_qty"], errors, {"request_id": cdec["request_id"], "qty": 500})
    check("R07", "edit that takes more than the stock that is left is refused", msg is not None, str(msg))
    msg = a1.apply_edit(dec, state["original_qty"], errors, {"request_id": cdec["request_id"], "qty": "abc"})
    check("R07", "edit with a word instead of a number is refused", msg is not None, str(msg))
    msg = a1.apply_edit(dec, state["original_qty"], errors, {"request_id": 99999999, "qty": 1})
    check("R07", "edit of a request that does not exist is refused", msg is not None, str(msg))
    check("R07", "a refused edit changed nothing", dec[cdec["request_id"]]["approved_qty"] == cdec["approved_qty"])
    # the screen rule: a changed quantity needs a reason
    store = "Store G"
    original = sl.a1_table(state, store)
    edited = original.copy()
    edited.loc[edited.index[0], "Packaging qty"] = int(edited.loc[edited.index[0], "Packaging qty"]) + 0
    first = edited.index[0]
    edited.loc[first, "Packaging qty"] = max(int(original.loc[first, "Packaging qty"]) - 1, 0) if original.loc[first, "Packaging qty"] > 0 else 0
    if original.loc[first, "Packaging qty"] > 0:
        ok, msg = sl.a1_done(state, store, original, edited)
        check("R07", "changing a quantity without a reason in Remark is refused", ok is False and "reason" in msg.lower(), msg)
    else:
        check("R07", "changing a quantity without a reason in Remark is refused", True, "skipped: first line had 0")
    bad_edit = original.copy()
    bad_edit.loc[bad_edit.index[0], "Packaging qty"] = -1
    ok, msg = sl.a1_done(state, store, original, bad_edit)
    check("R07", "a negative Packaging qty on the screen is refused", ok is False, msg)
    check("R07", "nothing was saved by the refused edits", one("SELECT COUNT(*) FROM packing_items") == 0)

    # --- R09: another screen took the stock after this proposal was made ---
    victim = next(d for d in decisions if d["store_code"] == "Store G" and d["approved_qty"] > 0)
    run_sql("UPDATE warehouse_stock SET available_qty = 0 WHERE product_id = ?", (victim["product_id"],))
    ok, msg = a1.finish_store(state, "Store G", {})
    check("R09", "stock disappeared after the proposal -> Done is refused with a clear message", ok is False and "left in the warehouse" in msg, msg)
    check("R09", "...and nothing was saved (requests still pending, no packing list)",
          one("SELECT COUNT(*) FROM packing_items WHERE store_id = (SELECT store_id FROM stores WHERE store_code = 'Store G')") == 0
          and one("SELECT COUNT(*) FROM restock_requests WHERE status != 'pending'") == 0)
    run_sql("UPDATE warehouse_stock SET available_qty = ? WHERE product_id = ?", (victim["in_stock"], victim["product_id"]))


# ---------- 5. the AI note: the guard is tested alone, and inside Agent 1 with a fake AI ----------
def scenario_ai_text() -> None:
    facts = "Store: Store A\nRequests: 12, packable: 9, short of what the store needs: 2, out of stock: 3"
    good = "Store A can pack 9 of 12 items. 3 items are out of stock, so tell the store."
    check("R08", "a note that uses only numbers from the facts is accepted", guardrails.check_ai_text(good, facts) is None)
    check("R08", "a note with an invented number (45) is refused", guardrails.check_ai_text("Pack 45 items for Store A.", facts) is not None)
    check("R08", "a note with a link is refused", guardrails.check_ai_text("See https://example.com for 9 items", facts) is not None)
    check("R08", "a very long note is refused", guardrails.check_ai_text("word " * 400, facts) is not None)
    check("R08", "an empty note is refused", guardrails.check_ai_text("   ", facts) is not None)
    check("R16", "the manager summary guard (Agent 3) uses the same number rule", a3.numbers_in("97.5% of 40") == {"97.5", "40"})

    # inside Agent 1: a fake AI that invents a number, then one that behaves
    outputs = {"text": "Please pack 777 extra items."}

    class FakeAI:
        def __init__(self, *args, **kwargs): pass
        def invoke(self, prompt): return types.SimpleNamespace(text=outputs["text"])

    fake = types.ModuleType("langchain_google_genai")
    fake.ChatGoogleGenerativeAI = FakeAI
    sys.modules["langchain_google_genai"] = fake
    os.environ["GOOGLE_API_KEY"] = "fake"
    view = a1.build_store_views([d for d in STATE["state"]["decisions"] if d["store_code"] == "Store A"])
    bad = a1.write_notes({"stores": {"Store A": view["Store A"]}})["stores"]["Store A"]
    check("R08", "inside Agent 1: an AI note with an invented number is replaced by the standard note", bad["note_source"] == "standard", bad["note"])
    s = view["Store A"]["summary"]
    outputs["text"] = f"Store A can pack {s['approved']} of {s['processed']} items."
    view = a1.build_store_views([d for d in STATE["state"]["decisions"] if d["store_code"] == "Store A"])
    good_note = a1.write_notes({"stores": {"Store A": view["Store A"]}})["stores"]["Store A"]
    check("R08", "inside Agent 1: a correct AI note is kept", good_note["note_source"] == "AI", good_note["note"])
    sys.modules.pop("langchain_google_genai", None)
    os.environ.pop("GOOGLE_API_KEY", None)


# ---------- 6. the person presses Done for every store; labels are printed ----------
def scenario_done_and_labels() -> None:
    state = STATE["state"]
    stores = sorted({d["store_code"] for d in state["decisions"]})
    saved = []
    for code in stores:
        ok, msg = a1.finish_store(state, code, {})
        saved.append(ok)
        if not ok:
            print("  Done refused:", code, msg)
    check("R09", f"Done accepted for all {len(stores)} stores with a live stock check", all(saved))
    total_units = sum(d["approved_qty"] for d in state["decisions"])
    check("R03", "packing lists hold exactly the units Agent 1 approved", one("SELECT SUM(qty_to_pack) FROM packing_items") == total_units)
    check("R03", "one open barcode request per store, labels = units to pack",
          one("SELECT COUNT(*) FROM barcode_requests WHERE status = 'requested'") == len({d["store_code"] for d in state["decisions"] if d["approved_qty"] > 0})
          and one("SELECT SUM(total_labels) FROM barcode_requests") == total_units)
    check("R03", "no request is left 'pending' after Done", one("SELECT COUNT(*) FROM restock_requests WHERE status = 'pending'") == 0)
    check("R03", "running Agent 1 again finds nothing to do (no double packing)", a1.propose()["decisions"] == [])
    for rid in [r[0] for r in db().execute("SELECT barcode_request_id FROM barcode_requests")]:
        sl.mark_printed(rid)
    ready = sl.lists_ready_to_pack()
    check("R03", "after labels are printed every store shows Labels = printed", (ready["Labels"] == "printed").all())


# ---------- 7. warehouse staff pack (good and bad numbers) ----------
def scenario_packing() -> None:
    stores = sl.lists_ready_to_pack()["Store"].tolist()
    first = stores[0]
    t = sl.packing_lines(first)
    bad = t.copy(); bad["Packed"] = bad["To pack"] + 1
    check("R10", "packed more than planned is refused", sl.save_packing("Ravi", bad)[0] is False)
    bad = t.copy(); bad["Packed"] = bad["To pack"]; bad["Damaged"] = 1
    check("R10", "packed + damaged more than planned is refused", sl.save_packing("Ravi", bad)[0] is False)
    bad = t.copy(); bad["Packed"] = -1
    check("R10", "negative packed number is refused", sl.save_packing("Ravi", bad)[0] is False)
    check("R10", "empty staff name is refused", sl.save_packing(" ", t.assign(Packed=t["To pack"]))[0] is False)
    check("R10", "a line with no Packed number is refused", sl.save_packing("Ravi", t)[0] is False)
    check("R10", "nothing was saved by the refused packing", one("SELECT COUNT(*) FROM packing_items WHERE pack_status != 'to_pack'") == 0)
    for code in stores:
        t = sl.packing_lines(code)
        t["Packed"] = t["To pack"]
        t["Damaged"] = 0
        if len(t) > 2:                        # two lines have one damaged unit while packing
            for i in (0, 1):
                if t.loc[i, "To pack"] >= 2:
                    t.loc[i, "Packed"] = t.loc[i, "To pack"] - 1
                    t.loc[i, "Damaged"] = 1
        ok, msg = sl.save_packing("Ravi", t)
        check("R10", f"{code}: valid packing (with damage while packing) is saved", ok, msg)
    prog = sl.packing_progress()
    check("R10", "every store shows 'Packed and ready to dispatch'", (prog["Stage"] == "Packed and ready to dispatch").all())
    check("R10", "packed + damaged never above planned, for all saved lines",
          one("SELECT COUNT(*) FROM packing_items WHERE packed_qty + damaged_qty > qty_to_pack") == 0)


# ---------- 8. Agent 2: the trip days, blocked stores, all-or-nothing, stock bookkeeping ----------
def trip_stats(trip_code: str) -> dict:
    conn = db()
    try:
        tid = conn.execute("SELECT trip_id FROM trips WHERE trip_code = ?", (trip_code,)).fetchone()[0]
        stores = [r[0] for r in conn.execute("SELECT s.store_code FROM trip_stores ts JOIN stores s USING (store_id) WHERE ts.trip_id = ?", (tid,))]
        used = {r[0]: r[1] for r in conn.execute("SELECT product_id, SUM(packed_qty + damaged_qty) FROM packing_items WHERE trip_id = ? GROUP BY product_id", (tid,))}
        box_units = conn.execute("SELECT COALESCE(SUM(units), 0) FROM boxes WHERE trip_id = ?", (tid,)).fetchone()[0]
        item_units = conn.execute("SELECT COALESCE(SUM(qty), 0) FROM box_items bi JOIN boxes b USING (box_id) WHERE b.trip_id = ?", (tid,)).fetchone()[0]
        packed = conn.execute("SELECT COALESCE(SUM(packed_qty), 0) FROM packing_items WHERE trip_id = ?", (tid,)).fetchone()[0]
        return {"stores": stores, "used": used, "box_units": box_units, "item_units": item_units, "packed": packed}
    finally:
        conn.close()


def run_trip(day: str) -> dict:
    return a2.run_agent(True, day, lambda q: {})["result"]


def scenario_agent2() -> None:
    n_trips = lambda: one("SELECT COUNT(*) FROM trips")
    for day, name in (("2026-11-09", "Monday"), ("2026-11-14", "Saturday"), ("2026-11-15", "Sunday")):
        before = (n_trips(), stock_snapshot())
        out = run_trip(day)
        check("R11", f"{name}: no trip is created", out["trip_code"] is None and n_trips() == before[0] and stock_snapshot() == before[1], str(out.get("note")))
    for bad in ("not-a-date", "2026-13-45", ""):
        try:
            out = run_trip(bad)
            check("R11", f"a wrong date ('{bad}') gives a clear message, no crash", out["trip_code"] is None and n_trips() == 0, str(out.get("note")))
        except Exception as error:                                    # a crash would be a failure
            check("R11", f"a wrong date ('{bad}') gives a clear message, no crash", False, repr(error))

    # Tuesday: only C, D, E, G may go
    before = stock_snapshot()
    out = run_trip("2026-11-10")
    stats = trip_stats(out["trip_code"]) if out["trip_code"] else {"stores": []}
    check("R11", "Tuesday: a trip is created", out["trip_code"] is not None)
    check("R11", "Tuesday: only the scheduled stores (C, D, E, G) are on it", set(stats["stores"]) <= {"Store C", "Store D", "Store E", "Store G"} and len(stats["stores"]) == 4, str(stats["stores"]))
    check("R11", "stores of other days (A, B, F, H) are still waiting, not on the trip",
          one("SELECT COUNT(DISTINCT store_id) FROM packing_items WHERE trip_id IS NULL") == 4)
    after = stock_snapshot()
    delta = {p: before[p] - after[p] for p in before if before[p] != after[p]}
    check("R13", "Tuesday: warehouse stock fell by exactly packed + damaged, item by item", delta == stats.get("used", {}), f"{len(delta)} vs {len(stats.get('used', {}))}")
    check("R13", "Tuesday: units in boxes = units in box lines = units packed", stats["box_units"] == stats["item_units"] == stats["packed"])
    check("R13", "no warehouse stock below zero anywhere", one("SELECT COUNT(*) FROM warehouse_stock WHERE available_qty < 0") == 0)
    again = run_trip("2026-11-10")
    check("R13", "running Tuesday again does not create a second trip or move stock twice", again["trip_code"] is None and n_trips() == 1)

    # Wednesday: A ok, B has nothing packed (blocked), F's item is gone from the warehouse (blocked)
    conn = db()
    with conn:
        conn.execute("UPDATE packing_items SET packed_qty = 0, damaged_qty = 0, pack_status = 'short' WHERE trip_id IS NULL AND store_id = (SELECT store_id FROM stores WHERE store_code='Store B')")
        pid = conn.execute("SELECT product_id FROM packing_items WHERE trip_id IS NULL AND store_id = (SELECT store_id FROM stores WHERE store_code='Store F') LIMIT 1").fetchone()[0]
        conn.execute("UPDATE warehouse_stock SET available_qty = 0 WHERE product_id = ?", (pid,))
    conn.close()
    before = stock_snapshot()
    out = run_trip("2026-11-11")
    stats = trip_stats(out["trip_code"]) if out["trip_code"] else {"stores": []}
    check("R12", "Wednesday: Store B (nothing packed) is blocked and not on the trip", "Store B" not in stats["stores"], str(stats["stores"]))
    check("R12", "Wednesday: Store F (warehouse no longer holds an item) is blocked and not on the trip", "Store F" not in stats["stores"], str(stats["stores"]))
    check("R11", "Wednesday: Store A, which is scheduled and fine, goes", stats["stores"] == ["Store A"], str(stats["stores"]))
    after = stock_snapshot()
    delta = {p: before[p] - after[p] for p in before if before[p] != after[p]}
    check("R13", "Wednesday: stock fell by exactly what Store A took", delta == stats.get("used", {}))
    check("R12", "blocked stores keep their lists (not lost, not shipped)",
          one("SELECT COUNT(*) FROM packing_items WHERE trip_id IS NULL AND store_id IN (SELECT store_id FROM stores WHERE store_code IN ('Store B','Store F'))") > 0)

    # Thursday: all-or-nothing. A trip with the same code already exists -> the new trip must change NOTHING
    week = datetime.date(2026, 11, 12).isocalendar()
    number = one("SELECT next_value FROM id_counters WHERE counter_name = ?", (f"trip_{week[0]}W{week[1]:02d}",)) or 1
    blocker = f"TRIP-20261112-{number:02d}"                 # the code the next trip WOULD get: it is already taken
    run_sql("INSERT INTO trips (trip_code, trip_date) VALUES (?, '2026-11-12')", (blocker,))
    snap = (counts("trips", "boxes", "box_items", "trip_stores", "agent_runs"), stock_snapshot(), one("SELECT COUNT(*) FROM packing_items WHERE trip_id IS NULL"))
    try:
        run_trip("2026-11-12")
        refused = False
    except SystemExit:
        refused = True
    snap2 = (counts("trips", "boxes", "box_items", "trip_stores", "agent_runs"), stock_snapshot(), one("SELECT COUNT(*) FROM packing_items WHERE trip_id IS NULL"))
    check("R13", "a trip that fails half-way changes NOTHING (all or nothing)", refused and snap == snap2)
    run_sql("DELETE FROM trips WHERE trip_code = ?", (blocker,))
    out = run_trip("2026-11-12")
    stats = trip_stats(out["trip_code"]) if out["trip_code"] else {"stores": []}
    check("R11", "Thursday: Store H goes, after the failed attempt was cleared", stats["stores"] == ["Store H"], str(stats["stores"]))


# ---------- 9. the store checks the boxes ----------
def scenario_receiving() -> None:
    trips = [r for r in db().execute("SELECT trip_code FROM trips ORDER BY trip_date")]
    tcode = trips[0][0]
    stores = [r[0] for r in db().execute("SELECT s.store_code FROM trip_stores ts JOIN trips t USING (trip_id) JOIN stores s USING (store_id) WHERE t.trip_code = ? ORDER BY 1", (tcode,))]

    def lines_of(trip, store):
        return rcv.load_expected({"trip_code": trip, "store_code": store})

    def answer(trip, store, mode):
        st = lines_of(trip, store)
        items = []
        for n, l in enumerate(st["lines"]):
            got, dmg = l["expected"], 0
            if mode == "problems" and n % 2 == 0:                       # half of the lines have a problem
                got = max(l["expected"] - 1, 0) if n % 4 == 0 else l["expected"]
                dmg = 1 if (n % 4 == 2 and l["expected"] >= 1) else 0
            if mode == "negative":
                got = -1
            if mode == "damaged_more":
                got, dmg = 1, 5
            items.append({"pack_id": l["pack_id"], "expected": l["expected"], "received": got, "damaged": dmg})
        return {"checker": "Eval checker", "boxes_received": st["boxes_expected"], "items": items}

    s0 = stores[0]
    for mode, text in (("negative", "negative received number"), ("damaged_more", "damaged more than received")):
        before = counts("store_receipts", "receipt_items", "warehouse_alerts")
        try:
            rcv.run(tcode, s0, False, lambda q, m=mode: answer(tcode, s0, m))
            refused = False
        except ValueError:
            refused = True
        check("R14", f"{text} is refused", refused and counts("store_receipts", "receipt_items", "warehouse_alerts") == before)
    # screen-side checks
    table = sl.receiving_lines(tcode, s0)[0]
    t = table.copy(); t["Received"] = pd.array([None] * len(t), dtype="Int64")
    try:
        sl.submit_receipt(tcode, s0, "Eval", 1, t); ok = False
    except ValueError:
        ok = True
    check("R14", "a line that is not checked yet blocks Done", ok)
    t = table.copy(); t["Received"] = t["Packed"]; t["Damaged"] = t["Received"] + 1
    try:
        sl.submit_receipt(tcode, s0, "Eval", 1, t); ok = False
    except ValueError:
        ok = True
    check("R14", "on the screen, damaged more than received blocks Done", ok)
    try:
        sl.submit_receipt(tcode, s0, "  ", 1, table.assign(Received=table["Packed"])); ok = False
    except ValueError:
        ok = True
    check("R14", "empty checker name blocks Done", ok)

    # the alert levels
    check("R15", "alert levels: 0 of 10 none, 1 of 20 NOTICE, 1 of 10 WARNING, 3 of 10 SERIOUS",
          alerts.level_for(0, 10) is None and alerts.level_for(1, 20) == "NOTICE" and alerts.level_for(1, 10) == "WARNING" and alerts.level_for(3, 10) == "SERIOUS")
    # one store with problems, the others clean
    for i, store in enumerate(stores):
        mode = "problems" if i == 0 else "clean"
        out = rcv.run(tcode, store, False, lambda q, s=store, m=mode: answer(tcode, s, m))
        if i == 0:
            check("R15", f"{store}: many problem lines -> an alert is saved for the warehouse",
                  one("SELECT COUNT(*) FROM warehouse_alerts WHERE store_id = (SELECT store_id FROM stores WHERE store_code = ?)", (store,)) == 1, str(out.get("level")))
            check("R15", f"{store}: the alert level matches the share of problem lines",
                  one("SELECT level FROM warehouse_alerts ORDER BY alert_id DESC LIMIT 1") == out.get("level") and out.get("level") in ("SERIOUS", "WARNING", "NOTICE"))
        else:
            check("R15", f"{store}: a clean delivery raises no alert", out.get("level") is None and out.get("clear") is True)
    dup = rcv.run(tcode, stores[0], False, lambda q: answer(tcode, stores[0], "clean"))
    check("R14", "the same store cannot confirm the same trip twice", "error" in dup and one("SELECT COUNT(*) FROM store_receipts WHERE trip_id = (SELECT trip_id FROM trips WHERE trip_code = ?) AND store_id = (SELECT store_id FROM stores WHERE store_code = ?)", (tcode, stores[0])) == 1, str(dup))
    check("R14", "when every store has checked, the trip becomes 'received'", one("SELECT status FROM trips WHERE trip_code = ?", (tcode,)) == "received")
    for code in [r[0] for r in db().execute("SELECT trip_code FROM trips WHERE status != 'received' ORDER BY trip_date")]:
        for store in [r[0] for r in db().execute("SELECT s.store_code FROM trip_stores ts JOIN trips t USING (trip_id) JOIN stores s USING (store_id) WHERE t.trip_code = ?", (code,))]:
            rcv.run(code, store, False, lambda q, c=code, s=store: answer(c, s, "clean"))
    STATE["first_trip"] = tcode


# ---------- 10. Agent 3 report ----------
def scenario_agent3() -> None:
    tcode = STATE["first_trip"]
    out = a3.run(tcode, False)
    check("R16", "Agent 3 makes the manager report for a finished trip (standard text, AI off)", not out.get("problems") and "report" in out, str(out.get("problems")))
    check("R16", "the report numbers add up: totals in the text come from the database",
          a3.numbers_in(out.get("report", "")) >= a3.numbers_in(str(one("SELECT SUM(total_units) FROM trip_stores WHERE trip_id = (SELECT trip_id FROM trips WHERE trip_code = ?)", (tcode,)))))
    run_sql("UPDATE trip_stores SET total_units = total_units + 1 WHERE trip_id = (SELECT trip_id FROM trips WHERE trip_code = ?) AND store_id = (SELECT MIN(store_id) FROM trip_stores WHERE trip_id = (SELECT trip_id FROM trips WHERE trip_code = ?))", (tcode, tcode))
    out = a3.run(tcode, False)
    check("R16", "if the data does not add up (1 unit off), Agent 3 stops and lists the problem instead of reporting", bool(out.get("problems")), str(out))
    run_sql("UPDATE trip_stores SET total_units = total_units - 1 WHERE trip_id = (SELECT trip_id FROM trips WHERE trip_code = ?) AND store_id = (SELECT MIN(store_id) FROM trip_stores WHERE trip_id = (SELECT trip_id FROM trips WHERE trip_code = ?))", (tcode, tcode))


# ---------- 11. the NEXT week: stock that already left must not be counted twice ----------
def scenario_second_cycle() -> None:
    conn = db()
    row = conn.execute("""SELECT i.product_id, SUM(i.packed_qty + i.damaged_qty) AS shipped,
                                 (SELECT SUM(available_qty) FROM warehouse_stock w WHERE w.product_id = i.product_id) AS left
                          FROM packing_items i WHERE i.trip_id IS NOT NULL GROUP BY i.product_id
                          HAVING left > 0 AND shipped > 0 ORDER BY left DESC LIMIT 1""").fetchone()
    if row is None:
        conn.close()
        check("R03", "next-week check: a shipped item that still has stock", False, "no such item found")
        return
    pid, shipped, left = row["product_id"], row["shipped"], row["left"]
    free_store = conn.execute("""SELECT store_id FROM stores WHERE store_id NOT IN
                                 (SELECT store_id FROM restock_requests WHERE product_id = ? AND request_date = ?) LIMIT 1""",
                              (pid, datetime.date.today().isoformat())).fetchone()[0]
    with conn:
        conn.execute("INSERT INTO restock_requests (request_date, requested_at, store_id, product_id, requested_qty, requested_by, request_note) "
                     "VALUES (?, ?, ?, ?, ?, 'Eval', 'Next week sale')", (datetime.date.today().isoformat(), datetime.datetime.now().isoformat(sep=' ', timespec='seconds'), free_store, pid, left))
    conn.close()
    state = a1.propose()
    mine = [d for d in state["decisions"] if d["product_id"] == pid]
    got = sum(d["approved_qty"] for d in mine)
    check("R03", f"next week: stock that already left on a trip ({shipped} units) is NOT taken off the warehouse quantity again",
          got == left, f"warehouse has {left}, Agent 1 offered {got}")
    check("R03", "next week: the agent still never offers more than the warehouse holds", got <= left)


# ---------- 12. Agent 4: a new product ----------
def scenario_new_product() -> None:
    opts = a4.options()
    vendor = opts["vendors"][0]
    good = {"name": "Eval Silicone Spatula Set", "vendor": vendor, "category": "Kitchen", "hsn": "39241090", "gst": 18,
            "mrp": 299, "cost": 120, "selling": 199}
    products_before = counts("products", "product_prices", "warehouse_stock")
    bad_cases = {"HSN with 2 digits": {**good, "hsn": "12"}, "HSN with letters": {**good, "hsn": "ABCD"}, "GST 7%": {**good, "gst": 7},
                 "selling below cost": {**good, "selling": 100}, "MRP below selling": {**good, "mrp": 150},
                 "zero cost": {**good, "cost": 0}, "negative price": {**good, "selling": -5}, "no category": {**good, "category": ""},
                 "category without SKU prefix": {**good, "category": "Store Use"}, "empty name": {**good, "name": "  "},
                 "name of only numbers": {**good, "name": "12345"}, "unknown vendor": {**good, "vendor": "No Such Vendor Ltd"},
                 "no vendor at all": {**good, "vendor": ""}, "vendor chosen AND typed": {**good, "new_vendor": "Another Vendor"},
                 "variant of a SKU that does not exist": {**good, "variant_of": "ZZZ999999"}}
    for label, form in bad_cases.items():
        out = a4.propose(form)
        check("R17", f"refused: {label}", bool(out["problems"]), str(out.get("proposal")))
    existing = one("SELECT product_name FROM products LIMIT 1")
    check("R17", "refused: a name that already exists", bool(a4.propose({**good, "name": existing.upper()})["problems"]))
    check("R17", "nothing was saved by the refused products", counts("products", "product_prices", "warehouse_stock") == products_before)

    # cancel and missing approver
    out = a4.run_agent(good, lambda q: {"action": "cancel"})
    check("R19", "cancel saves nothing", out["saved"] is False and counts("products", "product_prices", "warehouse_stock") == products_before)
    out = a4.run_agent(good, lambda q: {"action": "approve", "by": "  "})
    check("R19", "approval without a name saves nothing", out["saved"] is False and counts("products", "product_prices", "warehouse_stock") == products_before)

    # approve
    prefix = one("SELECT sku_prefix FROM categories WHERE category_name = 'Kitchen'")
    first = a4.run_agent(good, lambda q: {"action": "approve", "by": "Eval person"})
    import re as _re
    check("R19", "approved by a named person: the product is saved", first["saved"] is True, first.get("message", ""))
    sku = first.get("sku", "")
    check("R18", "SKU has the catalogue format: category prefix + 6 digits + letters", bool(_re.fullmatch(prefix + r"\d{6}[A-Z0-9]+", sku)), sku)
    check("R18", "the SKU matches the product number that was saved", sku.startswith(prefix + f"{first.get('product_number', 0):06d}"))
    check("R18", "the product ID is new and the SKU is unique", one("SELECT COUNT(*) FROM products WHERE sku = ?", (sku,)) == 1 and first["product_id"] > one("SELECT COUNT(*) FROM products") - 5)
    check("R20", "price saved with the product (cost, selling, MRP)",
          tuple(db().execute("SELECT cost_price, selling_price, mrp FROM product_prices WHERE product_id = ?", (first["product_id"],)).fetchone()) == (120.0, 199.0, 299.0))
    check("R20", "an empty warehouse slot (0 units, no rack) was created",
          tuple(db().execute("SELECT available_qty, damaged_qty, rack_name FROM warehouse_stock WHERE product_id = ?", (first["product_id"],)).fetchone()) == (0, 0, None))
    catalog, _ = sl.request_catalog("Eval Silicone")
    check("R20", "stores cannot request it yet (no stock): it is not in the request list", len(catalog) == 0)
    check("R19", "the approval is written in the agent log", one("SELECT COUNT(*) FROM agent_runs WHERE agent_name = 'Agent 4' AND note LIKE '%Eval person%'") == 1)

    # same name again, a variant, a new vendor
    check("R17", "adding the same product again is refused", a4.run_agent(good, lambda q: {"action": "approve", "by": "X"})["saved"] is False)
    variant = a4.run_agent({**good, "name": "Eval Silicone Spatula Set Red", "colour": "Red", "variant_of": sku}, lambda q: {"action": "approve", "by": "X"})
    check("R18", "a variant reuses the product number but gets its own, different SKU",
          variant["saved"] and variant["product_number"] == first["product_number"] and variant["sku"] != sku)
    new_v = a4.run_agent({**good, "name": "Eval Wooden Rack", "vendor": "", "new_vendor": "Eval New Vendor Pvt Ltd", "new_vendor_india": False},
                         lambda q: {"action": "approve", "by": "X"})
    check("R18", "a new vendor is created with the product; a non-India vendor gets no 'IN' in the SKU",
          new_v["saved"] and one("SELECT is_india_vendor FROM vendors WHERE vendor_name = 'Eval New Vendor Pvt Ltd'") == 0 and "IN" not in new_v["sku"][len(prefix) + 6:len(prefix) + 8])
    check("R18", "product numbers keep going up (never reused)", new_v["product_number"] > first["product_number"])

    # two people add a product at the same time: both proposals show the same number, but the saves must not collide
    p1 = a4.propose({**good, "name": "Eval Parallel Item One"})
    p2 = a4.propose({**good, "name": "Eval Parallel Item Two"})
    r1, r2 = a4.save(p1, "Person 1"), a4.save(p2, "Person 2")
    check("R18", "two proposals made at the same time: both save, with different product numbers",
          p1["proposal"]["product_number"] == p2["proposal"]["product_number"] and r1["saved"] and r2["saved"] and r1["product_number"] != r2["product_number"])
    check("R18", "all SKUs in the catalogue are still unique", one("SELECT COUNT(*) - COUNT(DISTINCT sku) FROM products") == 0)

    # the catalogue changed after the proposal: saving re-checks, and a failure saves nothing
    p3 = a4.propose({**good, "name": "Eval Late Duplicate"})
    a4.run_agent({**good, "name": "Eval Late Duplicate"}, lambda q: {"action": "approve", "by": "X"})
    before = counts("products", "product_prices", "warehouse_stock", "vendors")
    late = a4.save(p3, "Person 3")
    check("R19", "a proposal that became a duplicate before approval is refused and saves nothing", late["saved"] is False and counts("products", "product_prices", "warehouse_stock", "vendors") == before)
    check("R20", "Agent 1 still runs normally with the new products in the catalogue", isinstance(a1.propose(), dict))


# ---------- run everything and write the report ----------
def main() -> int:
    print("Preparing a copy of the database (your real data is not touched) ...")
    reset_copy()
    steps = [("Allocation rules (3000 random cases)", scenario_allocation_rules),
             ("Store staff raise good and bad requests", scenario_store_requests),
             ("Tricky orders are added (contested item, out of stock, plenty on shelf, huge ask)", scenario_tricky_orders),
             ("Agent 1 proposes, a person edits, live stock check", scenario_agent1),
             ("AI note guard", scenario_ai_text),
             ("Done for every store, labels printed", scenario_done_and_labels),
             ("Warehouse packing (good and bad numbers)", scenario_packing),
             ("Agent 2 trips (days, blocked stores, all-or-nothing, stock)", scenario_agent2),
             ("Store receiving and alerts", scenario_receiving),
             ("Agent 3 manager report", scenario_agent3),
             ("Next week: stock counted once", scenario_second_cycle),
             ("Agent 4 new product", scenario_new_product)]
    for title, fn in steps:
        print(f"- {title}")
        try:
            fn()
        except Exception as error:               # a crash in a scenario is a failed check, and the others still run
            import traceback
            traceback.print_exc()
            check("CRASH", title, False, repr(error))

    total, passed = len(RESULTS), sum(1 for r in RESULTS if r[2])
    lines = ["# Evaluation report", "",
             f"Run on {datetime.date.today().isoformat()} on a COPY of the simulated database (seed 42). No real data, no e-mail, no AI call.", "",
             f"**{passed} of {total} checks passed.**", "",
             "| Rule | Area | What the rule says | Checks | Passed |", "|---|---|---|---:|---:|"]
    for rid, area, text in guardrails.RULES:
        mine = [r for r in RESULTS if r[0] == rid]
        lines.append(f"| {rid} | {area} | {text} | {len(mine)} | {sum(1 for r in mine if r[2])} |")
    crashes = [r for r in RESULTS if r[0] == "CRASH"]
    if crashes:
        lines.append(f"| CRASH | - | A scenario stopped with an error | {len(crashes)} | 0 |")
    lines += ["", "## Every check", "", "| Result | Rule | Check |", "|---|---|---|"]
    for rid, scenario, ok, detail in RESULTS:
        lines.append(f"| {'PASS' if ok else 'FAIL'} | {rid} | {scenario}" + (f" ({detail})" if (not ok and detail) else "") + " |")
    (ROOT / "evaluation_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"\n{'Rule':5} {'Area':18} {'Checks':>7} {'Passed':>7}")
    for rid, area, _ in guardrails.RULES:
        mine = [r for r in RESULTS if r[0] == rid]
        print(f"{rid:5} {area:18} {len(mine):>7} {sum(1 for r in mine if r[2]):>7}")
    print(f"\n{passed} of {total} checks passed.  Report saved: evaluation_report.md")
    shutil.rmtree(WORK, ignore_errors=True)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
