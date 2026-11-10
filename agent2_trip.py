"""Step 11: Agent 2, the trip agent (a LangGraph graph).

After the warehouse staff have packed, Agent 2:
    finds the stores whose packing is finished -> VERIFIES each store (items, quantities, damages) ->
    a PERSON decides which stores go (and which are held) -> creates ONE trip with a unique trip ID,
    puts the packed units into boxes, and takes the units out of the warehouse stock.

One vehicle, one trip per delivery day. Which stores go on which weekday comes from the trip_schedule table:
    Mon = packing only (no trip)   Tue/Wed/Thu = that day's stores   Fri = urgent restocks (any store that is packed)   Sat/Sun = no trips (peak sales)

Run from the project folder:
    python agent2_trip.py --date 2026-11-10          the trip for that day, pause to approve or hold
    python agent2_trip.py --date 2026-11-10 --auto   no pause
"""
import argparse
import datetime
import math
import sqlite3
import uuid
from pathlib import Path
from typing import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

import guardrails

ROOT = Path(__file__).resolve().parent
DB_FILE = ROOT / "data" / "retail.db"
_today = datetime.date.today()
DEFAULT_TRIP_DATE = (_today - datetime.timedelta(days=_today.isoweekday() - 1) + datetime.timedelta(days=1)).isoformat()   # this week's Tuesday
BOX_CAPACITY_UNITS = 50          # how many units fit in one box (a simple, changeable assumption)


class State(TypedDict, total=False):
    auto: bool
    trip_date: str
    stores: list          # verification result per store that is ready
    waiting: list         # stores still being packed
    hold: list            # store codes the person decided to hold
    cancelled: bool
    note: str
    result: dict


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ---------- NODE 1 + 2: find stores whose packing is finished, and VERIFY each one (reads only) ----------
def find_and_verify(state: State) -> State:
    conn = connect()
    day, bad_date = guardrails.check_trip_date(state["trip_date"])      # guardrail R11
    if bad_date:
        conn.close()
        return {"stores": [], "waiting": [], "hold": [], "cancelled": False, "note": bad_date}
    weekday = day.isoweekday()
    if weekday in (1, 6, 7):
        conn.close()
        return {"stores": [], "waiting": [], "hold": [], "cancelled": False,
                "note": ("Monday is packing day, so no trip leaves." if weekday == 1 else
                         f"{day.strftime('%A')} is a peak sales day, so there is no replenishment.")}
    if weekday == 5:                       # Friday: urgent restocks, any store that is packed and not yet on a trip
        today_ids = None
    else:
        today_ids = {r[0] for r in conn.execute("SELECT store_id FROM trip_schedule WHERE weekday = ?", (weekday,))}
    ready_ids = [r[0] for r in conn.execute("""
        SELECT store_id FROM packing_items WHERE trip_id IS NULL
        GROUP BY store_id HAVING SUM(pack_status = 'to_pack') = 0 ORDER BY store_id""")
        if today_ids is None or r[0] in today_ids]
    waiting = [r[0] for r in conn.execute("""
        SELECT s.store_code FROM packing_items i JOIN stores s USING (store_id) WHERE i.trip_id IS NULL
        GROUP BY s.store_code HAVING SUM(i.pack_status = 'to_pack') > 0 ORDER BY s.store_code""")]
    if today_ids is not None:
        scheduled = {r[0]: r[1] for r in conn.execute("SELECT store_id, store_code FROM stores")}
        waiting = [c for c in waiting if any(scheduled[i] == c for i in today_ids)]

    stores = []
    for store_id in ready_ids:
        code = conn.execute("SELECT store_code FROM stores WHERE store_id = ?", (store_id,)).fetchone()[0]
        lines = conn.execute("""
            SELECT i.pack_id, i.product_id, i.rack_name, i.qty_to_pack, i.packed_qty, i.damaged_qty, i.staff_note,
                   r.approved_qty, p.sku, p.product_name
            FROM packing_items i JOIN restock_requests r USING (request_id) JOIN products p USING (product_id)
            WHERE i.store_id = ? AND i.trip_id IS NULL ORDER BY i.rack_name, p.sku""", (store_id,)).fetchall()
        lines = [dict(l) for l in lines]
        problems, short_lines = [], []
        for l in lines:
            if l["qty_to_pack"] != l["approved_qty"]:
                problems.append(f"{l['sku']}: packing list says {l['qty_to_pack']} but Agent 1 approved {l['approved_qty']}")
            if l["packed_qty"] + l["damaged_qty"] > l["qty_to_pack"]:
                problems.append(f"{l['sku']}: packed + damaged is more than planned")
            if l["packed_qty"] < l["qty_to_pack"]:
                short_lines.append(l)
        for l in lines:             # guardrail R12: the warehouse must still hold what the trip takes out
            have = conn.execute("SELECT COALESCE(SUM(available_qty), 0) FROM warehouse_stock WHERE product_id = ?", (l["product_id"],)).fetchone()[0]
            if l["packed_qty"] + l["damaged_qty"] > have:
                problems.append(f"{l['sku']}: trip needs {l['packed_qty'] + l['damaged_qty']} but the warehouse has only {have}")
        packed = sum(l["packed_qty"] for l in lines)
        verdict = ("blocked" if problems or packed == 0 else "short" if short_lines else "clean")
        stores.append({"store_id": store_id, "store_code": code, "lines": lines, "planned": sum(l["qty_to_pack"] for l in lines),
                       "packed": packed, "damaged": sum(l["damaged_qty"] for l in lines),
                       "short_lines": len(short_lines), "boxes": math.ceil(packed / BOX_CAPACITY_UNITS) if packed else 0,
                       "problems": problems, "verdict": verdict,
                       "short_detail": [f"{l['sku']} {l['product_name'][:28]}: planned {l['qty_to_pack']}, packed "
                                        f"{l['packed_qty']}, damaged {l['damaged_qty']}" + (f" ({l['staff_note']})" if l["staff_note"] else "")
                                        for l in short_lines]})
    conn.close()
    return {"stores": stores, "waiting": waiting, "hold": [], "cancelled": False}


def anything_ready(state: State) -> str:
    return "trip_review" if state["stores"] else "nothing_ready"


def nothing_ready(state: State) -> State:
    return {"result": {"trip_code": None, "stores": [], "waiting": state["waiting"], "note": state.get("note")}}


# ---------- NODE 3: a PERSON approves the trip or holds some stores (the graph pauses here) ----------
def trip_review(state: State) -> State:
    blocked = [s["store_code"] for s in state["stores"] if s["verdict"] == "blocked"]
    if state.get("auto"):
        return {"hold": blocked}
    codes = [s["store_code"] for s in state["stores"]]
    error = None
    while True:
        answer = interrupt({"kind": "trip_review", "stores": state["stores"], "waiting": state["waiting"],
                            "blocked": blocked, "error": error})
        if answer.get("action") == "cancel":
            return {"cancelled": True}
        hold = [c for c in answer.get("hold", []) if c in codes]
        unknown = [c for c in answer.get("hold", []) if c not in codes]
        if unknown:
            error = f"Unknown store(s): {', '.join(unknown)}"
            continue
        return {"hold": sorted(set(hold) | set(blocked))}      # blocked stores can never go


def route_review(state: State) -> str:
    going = [s for s in state["stores"] if s["store_code"] not in state["hold"]]
    return "create_trip" if going and not state["cancelled"] else "cancelled"


def cancelled(state: State) -> State:
    return {"result": {"trip_code": None, "stores": [], "waiting": state["waiting"],
                       "cancelled": state.get("cancelled", False), "held": state["hold"]}}


# ---------- NODE 4: create the trip, boxes and stock movements (ONE transaction: all or nothing) ----------
def next_trip_code(conn: sqlite3.Connection, trip_date: str) -> str:
    iso = datetime.date.fromisoformat(trip_date).isocalendar()
    name = f"trip_{iso[0]}W{iso[1]:02d}"                  # one counter per week: Trip 1, 2, 3 ... of that week
    row = conn.execute("SELECT next_value FROM id_counters WHERE counter_name = ?", (name,)).fetchone()
    number = row[0] if row else 1
    conn.execute("INSERT INTO id_counters (counter_name, next_value) VALUES (?, ?) "
                 "ON CONFLICT(counter_name) DO UPDATE SET next_value = excluded.next_value", (name, number + 1))
    return f"TRIP-{trip_date.replace('-', '')}-{number:02d}"


def create_trip(state: State) -> State:
    going = [s for s in state["stores"] if s["store_code"] not in state["hold"]]
    conn = connect()
    try:
        with conn:
            code = next_trip_code(conn, state["trip_date"])
            trip_id = conn.execute("INSERT INTO trips (trip_code, trip_date, note) VALUES (?, ?, ?)",
                                   (code, state["trip_date"],
                                    f"{len(going)} store(s); held: {', '.join(state['hold']) or 'none'}")).lastrowid
            box_summary = []
            for st in going:
                boxes, current, room = [], None, 0
                for line in st["lines"]:                      # fill boxes in rack order
                    left = line["packed_qty"]
                    while left > 0:
                        if current is None or room == 0:
                            current, room = {"items": [], "units": 0}, BOX_CAPACITY_UNITS
                            boxes.append(current)
                        put = min(left, room)
                        current["items"].append((line["pack_id"], line["product_id"], put))
                        current["units"] += put
                        left -= put
                        room -= put
                for n, box in enumerate(boxes, 1):
                    box_code = f"{code}-S{st['store_id']}-B{n:02d}"
                    box_id = conn.execute("INSERT INTO boxes (trip_id, store_id, box_code, units) VALUES (?, ?, ?, ?)",
                                          (trip_id, st["store_id"], box_code, box["units"])).lastrowid
                    conn.executemany("INSERT INTO box_items (box_id, packing_item_id, product_id, qty) VALUES (?, ?, ?, ?)",
                                     [(box_id, pid, prod, qty) for pid, prod, qty in box["items"]])
                conn.execute("""INSERT INTO trip_stores (trip_id, store_id, boxes_count, total_units, short_lines, damaged_units)
                                VALUES (?, ?, ?, ?, ?, ?)""",
                             (trip_id, st["store_id"], len(boxes), st["packed"], st["short_lines"], st["damaged"]))
                conn.executemany("UPDATE packing_items SET trip_id = ? WHERE pack_id = ?",
                                 [(trip_id, l["pack_id"]) for l in st["lines"]])
                # the units now leave the available stock. Units damaged while packing are our own mistake: they are written
                # off here and kept in the packing record; Inventory 'Damaged' is only for damage that came from the vendor.
                for l in st["lines"]:
                    conn.execute("UPDATE warehouse_stock SET available_qty = available_qty - ? WHERE product_id = ?",
                                 (l["packed_qty"] + l["damaged_qty"], l["product_id"]))
                conn.execute("""INSERT INTO agent_runs (agent_name, store_id, requests_processed, approved_count,
                                partial_count, out_of_stock_count, note) VALUES ('Agent 2', ?, ?, ?, ?, 0, ?)""",
                             (st["store_id"], len(st["lines"]), len(st["lines"]) - st["short_lines"], st["short_lines"],
                              f"{code}: {st['packed']} units in {len(boxes)} box(es)"))
                box_summary.append((st["store_code"], len(boxes), st["packed"]))
    except sqlite3.IntegrityError as error:
        raise SystemExit(f"Could not create the trip (nothing was changed): {error}")
    finally:
        conn.close()
    return {"result": {"trip_code": code, "stores": box_summary, "waiting": state["waiting"], "held": state["hold"]}}


# ---------- wire the graph ----------
builder = StateGraph(State)
builder.add_node("find_and_verify", find_and_verify)
builder.add_node("trip_review", trip_review)
builder.add_node("create_trip", create_trip)
builder.add_node("nothing_ready", nothing_ready)
builder.add_node("cancelled", cancelled)
builder.add_edge(START, "find_and_verify")
builder.add_conditional_edges("find_and_verify", anything_ready, ["trip_review", "nothing_ready"])
builder.add_conditional_edges("trip_review", route_review, ["create_trip", "cancelled"])
builder.add_edge("create_trip", END)
builder.add_edge("nothing_ready", END)
builder.add_edge("cancelled", END)
graph = builder.compile(checkpointer=InMemorySaver())


def run_agent(auto: bool, trip_date: str, ask) -> dict:
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    result = graph.invoke({"auto": auto, "trip_date": trip_date}, config)
    while "__interrupt__" in result:
        result = graph.invoke(Command(resume=ask(result["__interrupt__"][0].value)), config)
    return result


# ---------- the person at the keyboard ----------
def print_verification(stores: list, waiting: list) -> None:
    print(f"\n{'store':9} {'lines':>5} {'planned':>8} {'packed':>7} {'damaged':>8} {'short lines':>12} {'boxes':>6}  verdict")
    for s in stores:
        print(f"{s['store_code']:9} {len(s['lines']):>5} {s['planned']:>8} {s['packed']:>7} {s['damaged']:>8} "
              f"{s['short_lines']:>12} {s['boxes']:>6}  {s['verdict'].upper()}")
    if waiting:
        print(f"\nStill being packed (not on this trip): {', '.join(waiting)}")


def ask_in_terminal(q: dict) -> dict:
    print("\nAGENT 2 VERIFICATION (nothing is created until you type ok)")
    print_verification(q["stores"], q["waiting"])
    print("\n  clean = everything packed as planned     short = some lines packed less than planned (will go, flagged)")
    print("  blocked = problem or nothing packed (cannot go)")
    if q.get("error"):
        print(f"!! {q['error']}")
    print("\n  Commands:  ok  |  show F  (short lines of a store)  |  hold F H  (keep stores back)  |  cancel")
    hold: list = []
    while True:
        words = input(f"\n[{', '.join(hold) or 'none'} held] > ").strip().split()
        if not words:
            continue
        cmd = words[0].lower()
        if cmd == "ok":
            return {"action": "approve", "hold": hold}
        if cmd == "cancel":
            return {"action": "cancel"}
        if cmd == "hold":
            hold = [w if w.lower().startswith("store") else f"Store {w.upper()}" for w in words[1:]]
        elif cmd == "show" and len(words) > 1:
            code = f"Store {words[1].upper()}"
            for s in q["stores"]:
                if s["store_code"] == code:
                    print(f"  {code}: " + ("; ".join(s["problems"]) if s["problems"] else f"{s['short_lines']} short line(s)"))
                    for d in s["short_detail"]:
                        print("   -", d)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--auto", action="store_true")
    parser.add_argument("--date", default=DEFAULT_TRIP_DATE)
    args = parser.parse_args()
    out = run_agent(args.auto, args.date, ask_in_terminal)["result"]
    if out.get("cancelled"):
        print("Cancelled. Nothing was created.")
    elif not out["trip_code"]:
        print("No trip created. " + (out.get("note") or
              (f"Scheduled but still being packed: {', '.join(out['waiting'])}." if out["waiting"] else
               "No scheduled store has packed items waiting (run Agent 1 and the warehouse packing first).")))
    else:
        print(f"\nTRIP CREATED: {out['trip_code']}")
        for code, boxes, units in out["stores"]:
            print(f"  {code}: {units} units in {boxes} box(es)")
        if out["held"]:
            print(f"  Held back: {', '.join(out['held'])}")
