"""Step 10c: stock arrives from a vendor. The agent PROPOSES, a HUMAN DECIDES, then the packing lists are made.

Why: the business is still young and there is little data, so we do not let the agent decide on its own yet.
This is "human-in-the-loop": the graph PAUSES at each question (LangGraph's interrupt), waits for your answer,
and then carries on exactly where it stopped.

Questions it asks:
  1. Is the whole quantity going to all stores?      (no -> which stores?)
  2. How should it be shared?   equal / only top stores / different quantity per store
     (top -> how many top stores?   different -> the quantity for each store)
  3. Here is the plan. Approve?   (no -> nothing is saved)

Run from the project folder:
    python distribute_new_stock.py --examples                  show products with no sales history (good for testing)
    python distribute_new_stock.py KIT000659KLBAT 500          500 units of that SKU arrived today
    python distribute_new_stock.py KIT000659KLBAT 500 --date 2025-08-30
"""
import argparse
import sqlite3
import uuid
from pathlib import Path
from typing import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from allocation import MIN_UNITS_PER_STORE, split_equally, split_to_top_stores

ROOT = Path(__file__).resolve().parent
DB_FILE = ROOT / "data" / "retail.db"
DEFAULT_DATE = "2025-08-30"
SHELVES, SLOTS_PER_SHELF = "ABCD", 10


class State(TypedDict, total=False):
    sku: str
    units: int
    receipt_date: str
    product: dict
    ranked: list            # [(store_code, units sold in August)], best seller first
    history_note: str
    chosen: list            # store codes that may receive stock (best seller first)
    method: str             # equal / top / custom
    top_n: int
    custom: dict
    plan: dict              # store_code -> units
    approved: bool
    result: str


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ---------- NODE: load everything we need (reads only: it is safe to run before a pause) ----------
def load_context(state: State) -> State:
    conn = connect()
    product = conn.execute("SELECT product_id, sku, product_name, vendor_id, category_id FROM products WHERE sku = ?",
                           (state["sku"],)).fetchone()
    if product is None:
        raise SystemExit(f"SKU {state['sku']} is not in the products table. (New products will be added by the "
                         f"new-product agent later. For now try: python distribute_new_stock.py --examples)")
    ranked = [(r[0], r[1]) for r in conn.execute("""
        SELECT st.store_code, COALESCE(SUM(s.quantity), 0) AS units
        FROM stores st LEFT JOIN sales s ON s.store_id = st.store_id
        WHERE st.is_active = 1 GROUP BY st.store_code ORDER BY units DESC, st.store_code""")]
    history = conn.execute("SELECT COALESCE(SUM(quantity),0), COUNT(DISTINCT store_id) FROM sales WHERE product_id = ?",
                           (product["product_id"],)).fetchone()
    conn.close()
    note = ("This is a NEW item: it has no sales history." if history[0] == 0 else
            f"Note: this item already sells ({history[0]} units in {history[1]} stores in August). "
            f"Normally Agent 1 shares such items using store sales and requests.")
    return {"product": dict(product), "ranked": ranked, "history_note": note, "chosen": [c for c, _ in ranked]}


# ---------- QUESTION 1: the whole quantity to all stores? ----------
def ask_scope(state: State) -> State:
    p, n = state["product"], len(state["ranked"])
    answer = interrupt({"kind": "choice", "context": state["history_note"],
                        "question": f"{state['units']} units of '{p['product_name']}' ({p['sku']}) received. "
                                    f"Is the whole quantity going to all {n} stores?",
                        "options": ["yes", "no"]})
    return {"chosen": [c for c, _ in state["ranked"]]} if answer == "yes" else {"chosen": []}


def route_scope(state: State) -> str:
    return "ask_method" if state["chosen"] else "ask_stores"


def ask_stores(state: State) -> State:
    codes, error = [c for c, _ in state["ranked"]], None
    while True:
        answer = interrupt({"kind": "stores", "options": codes, "error": error,
                            "question": "Which stores should get this stock?"})
        picked = [c for c in codes if c in answer]
        if picked:
            return {"chosen": picked}
        error = "Please pick at least one store."


# ---------- QUESTION 2: how to share it ----------
def ask_method(state: State) -> State:
    answer = interrupt({"kind": "choice", "question": f"How should it be shared between {len(state['chosen'])} store(s)?",
                        "options": ["equal", "top", "custom"],
                        "explain": {"equal": "the same quantity to every store",
                                    "top": "only to the top-selling stores",
                                    "custom": "every store, but a different quantity each (you type them)"}})
    return {"method": answer}


def route_method(state: State) -> str:
    return {"equal": "build_plan", "top": "ask_top_n", "custom": "ask_quantities"}[state["method"]]


def ask_top_n(state: State) -> State:
    biggest, error = len(state["chosen"]), None
    suggestion = min(biggest, max(state["units"] // MIN_UNITS_PER_STORE, 1))
    while True:
        answer = interrupt({"kind": "number", "min": 1, "max": biggest, "error": error,
                            "question": f"How many top-selling stores should get it? (1 to {biggest}; "
                                        f"suggestion: {suggestion}, so each gets at least {MIN_UNITS_PER_STORE} units)"})
        if isinstance(answer, int) and 1 <= answer <= biggest:
            return {"top_n": answer}
        error = f"Please enter a whole number from 1 to {biggest}."


def ask_quantities(state: State) -> State:
    error = None
    while True:
        answer = interrupt({"kind": "quantities", "stores": state["chosen"], "units": state["units"], "error": error,
                            "question": f"Quantity for each store (you have {state['units']} units to share)"})
        try:
            qty = {c: int(answer.get(c, 0)) for c in state["chosen"]}
        except (TypeError, ValueError):
            error = "Quantities must be whole numbers."
            continue
        if any(v < 0 for v in qty.values()):
            error = "Quantities cannot be negative."
        elif sum(qty.values()) == 0:
            error = "At least one store must get something."
        elif sum(qty.values()) > state["units"]:
            error = f"That adds up to {sum(qty.values())}, but only {state['units']} units were received."
        else:
            return {"custom": qty}


# ---------- the plan is worked out by CODE ----------
def build_plan(state: State) -> State:
    stores = [(c, dict(state["ranked"])[c]) for c in state["chosen"]]
    if state["method"] == "equal":
        plan = split_equally(state["units"], stores)
    elif state["method"] == "top":
        plan = split_to_top_stores(state["units"], stores, state["top_n"])
    else:
        plan = {c: q for c, q in state["custom"].items() if q > 0}
    return {"plan": plan}


# ---------- QUESTION 3: approve the plan ----------
def confirm(state: State) -> State:
    plan, units = state["plan"], state["units"]
    warnings = [f"{c} would get only {q} unit(s), below the usual minimum pack of {MIN_UNITS_PER_STORE}."
                for c, q in plan.items() if q < MIN_UNITS_PER_STORE]
    answer = interrupt({"kind": "confirm", "question": "Approve this plan?", "plan": plan,
                        "kept": units - sum(plan.values()), "warnings": warnings, "options": ["yes", "no"]})
    return {"approved": answer == "yes"}


def route_confirm(state: State) -> str:
    return "save_plan" if state["approved"] else "cancelled"


def cancelled(state: State) -> State:
    return {"result": "Cancelled. Nothing was saved."}


# ---------- finally: save (ONE transaction: all or nothing) ----------
def assign_rack(conn: sqlite3.Connection, category_id: int) -> str:
    """First free slot in a rack that already holds this category; a new rack if those are full."""
    used = {r[0] for r in conn.execute("SELECT rack_name FROM warehouse_stock WHERE rack_name IS NOT NULL")}
    racks = sorted({int(r[0][1:3]) for r in conn.execute(
        """SELECT w.rack_name FROM warehouse_stock w JOIN products p USING (product_id)
           WHERE p.category_id = ? AND w.rack_name IS NOT NULL""", (category_id,))})
    for rack in racks:
        for shelf in SHELVES:
            for pos in range(1, SLOTS_PER_SHELF + 1):
                name = f"R{rack:02d}-{shelf}-{pos:02d}"
                if name not in used:
                    return name
    newest = max([int(u[1:3]) for u in used] + [0]) + 1
    return f"R{newest:02d}-A-01"


def save_plan(state: State) -> State:
    p, plan = state["product"], state["plan"]
    sent = sum(plan.values())
    conn = connect()
    try:
        with conn:
            conn.execute(
                """INSERT INTO goods_receipts (receipt_date, product_id, vendor_id, qty_received, distribution_method,
                                               qty_sent_to_stores) VALUES (?, ?, ?, ?, ?, ?)""",
                (state["receipt_date"], p["product_id"], p["vendor_id"], state["units"], state["method"], sent))
            conn.execute("UPDATE warehouse_stock SET available_qty = available_qty + ? WHERE product_id = ?",
                         (state["units"], p["product_id"]))
            rack = conn.execute("SELECT rack_name FROM warehouse_stock WHERE product_id = ?", (p["product_id"],)).fetchone()[0]
            if rack is None:
                rack = assign_rack(conn, p["category_id"])
                conn.execute("UPDATE warehouse_stock SET rack_name = ? WHERE product_id = ?", (rack, p["product_id"]))
            conn.execute("UPDATE products SET is_active = 1 WHERE product_id = ?", (p["product_id"],))

            for code, qty in plan.items():
                store_id = conn.execute("SELECT store_id FROM stores WHERE store_code = ?", (code,)).fetchone()[0]
                req_id = conn.execute(
                    """INSERT INTO restock_requests (request_date, store_id, product_id, requested_qty, status,
                                                     approved_qty, decision_reason, source)
                       VALUES (?, ?, ?, ?, 'approved', ?, ?, 'vendor_receipt')""",
                    (state["receipt_date"], store_id, p["product_id"], qty, qty,
                     f"Vendor stock shared by a person ({state['method']})")).lastrowid
                barcode_id = conn.execute("INSERT INTO barcode_requests (store_id, total_labels) VALUES (?, ?)",
                                          (store_id, qty)).lastrowid
                conn.execute(
                    """INSERT INTO packing_items (barcode_request_id, request_id, store_id, product_id, rack_name,
                                                  qty_to_pack) VALUES (?, ?, ?, ?, ?, ?)""",
                    (barcode_id, req_id, store_id, p["product_id"], rack, qty))
    except sqlite3.IntegrityError as error:
        raise SystemExit(f"Could not save (nothing was changed): {error}. "
                         f"A store may already have a request for this product on {state['receipt_date']}; "
                         f"try another --date.")
    finally:
        conn.close()
    return {"result": f"Saved. {sent} units sent to {len(plan)} store(s), {state['units'] - sent} kept in the "
                      f"warehouse at rack {rack}. {sum(plan.values())} barcode labels requested."}


# ---------- wire the graph ----------
builder = StateGraph(State)
for name, fn in [("load_context", load_context), ("ask_scope", ask_scope), ("ask_stores", ask_stores),
                 ("ask_method", ask_method), ("ask_top_n", ask_top_n), ("ask_quantities", ask_quantities),
                 ("build_plan", build_plan), ("confirm", confirm), ("save_plan", save_plan),
                 ("cancelled", cancelled)]:
    builder.add_node(name, fn)
builder.add_edge(START, "load_context")
builder.add_edge("load_context", "ask_scope")
builder.add_conditional_edges("ask_scope", route_scope, ["ask_method", "ask_stores"])
builder.add_edge("ask_stores", "ask_method")
builder.add_conditional_edges("ask_method", route_method, ["build_plan", "ask_top_n", "ask_quantities"])
builder.add_edge("ask_top_n", "build_plan")
builder.add_edge("ask_quantities", "build_plan")
builder.add_edge("build_plan", "confirm")
builder.add_conditional_edges("confirm", route_confirm, ["save_plan", "cancelled"])
builder.add_edge("save_plan", END)
builder.add_edge("cancelled", END)
# The checkpointer is the graph's memory: it lets the graph pause at a question and continue later.
graph = builder.compile(checkpointer=InMemorySaver())


def run_conversation(sku: str, units: int, receipt_date: str, ask) -> str:
    """Runs the graph; every time it pauses, `ask` (a person, or a test) supplies the answer."""
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    result = graph.invoke({"sku": sku, "units": units, "receipt_date": receipt_date}, config)
    while "__interrupt__" in result:
        question = result["__interrupt__"][0].value
        result = graph.invoke(Command(resume=ask(question)), config)
    return result["result"]


# ---------- the person at the keyboard ----------
def ask_in_terminal(q: dict):
    print()
    if q.get("context"):
        print(q["context"])
    if q.get("error"):
        print(f"!! {q['error']}")
    kind = q["kind"]
    if kind == "choice":
        print(q["question"])
        for i, option in enumerate(q["options"], 1):
            print(f"  {i}. {option}" + (f"  ({q['explain'][option]})" if q.get("explain") else ""))
        while True:
            raw = input("Your answer (number or word): ").strip().lower()
            if raw.isdigit() and 1 <= int(raw) <= len(q["options"]):
                return q["options"][int(raw) - 1]
            if raw in q["options"]:
                return raw
    if kind == "stores":
        print(q["question"], "  Options:", ", ".join(q["options"]))
        raw = input("Type store names or letters, comma separated (e.g. A, C, F): ")
        names = [c.strip().title() for c in raw.split(",") if c.strip()]
        return [n if n.startswith("Store") else f"Store {n}" for n in names]
    if kind == "number":
        raw = input(q["question"] + " ").strip()
        return int(raw) if raw.isdigit() else None
    if kind == "quantities":
        print(q["question"])
        answer = {}
        for store in q["stores"]:
            raw = input(f"  {store}: ").strip()
            answer[store] = raw if raw else "0"
        return answer
    if kind == "confirm":
        print("PLAN")
        for store, qty in q["plan"].items():
            print(f"  {store}: {qty} units")
        print(f"  Kept in the warehouse: {q['kept']} units")
        for w in q["warnings"]:
            print(f"  WARNING: {w}")
        while True:
            raw = input("Approve this plan? (yes / no): ").strip().lower()
            if raw in ("yes", "no"):
                return raw


def show_examples() -> None:
    conn = connect()
    print("Products with no sales history (these behave as NEW items):")
    for r in conn.execute("""SELECT p.sku, p.product_name, c.category_name FROM products p
                             LEFT JOIN categories c USING (category_id)
                             WHERE NOT EXISTS (SELECT 1 FROM sales s WHERE s.product_id = p.product_id)
                             ORDER BY p.sku LIMIT 8"""):
        print(f"  {r['sku']:16} {r['product_name'][:40]:40} {r['category_name']}")
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("sku", nargs="?")
    parser.add_argument("units", nargs="?", type=int)
    parser.add_argument("--date", default=DEFAULT_DATE)
    parser.add_argument("--examples", action="store_true")
    args = parser.parse_args()
    if args.examples or not (args.sku and args.units):
        show_examples()
    else:
        print(run_conversation(args.sku, args.units, args.date, ask_in_terminal))
