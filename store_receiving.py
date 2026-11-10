"""Step 12: store receiving (a LangGraph graph with a human step).

Store staff check the boxes of one trip against that store's packing list and write remarks:
    asks: checker name -> Trip ID -> Store -> shows the list -> staff fix only the lines that differ -> save.
Saving marks the store's boxes 'received'; when every store of the trip is done, the trip becomes 'received'.

    python store_receiving.py                  a person does the check (interactive)
    python store_receiving.py --simulate       SIMULATED staff check every dispatched store (seed 42, marked simulated)
"""
import argparse
import random
import sqlite3
import uuid
from pathlib import Path
from typing import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from alerts import level_for, message_for
from notify import send_report

ROOT = Path(__file__).resolve().parent
DB_FILE = ROOT / "data" / "retail.db"
SIM_STAFF = ["Asha", "Ravi", "Meena", "Karthik"]


class State(TypedDict, total=False):
    trip_code: str
    store_code: str
    checker: str
    simulate: bool
    lines: list
    boxes_expected: int
    answer: dict
    result: dict


ALERT = ("ALERT: Need to be more careful while packing. Cross-check with the team why items are less or damaged, "
         "find where the fault is, and report it.")


def remark_for(expected: int, received: int, damaged: int = 0) -> str:
    """match = received - packed, so less/extra come from the machine; damaged is only what the staff type."""
    parts = []
    if received < expected:
        parts.append(f"{expected - received} less")
    elif received > expected:
        parts.append(f"{received - expected} extra received")
    if damaged:
        parts.append(f"{damaged} damaged")
    if not parts:
        return "Good"
    return ", ".join(parts) + (" - Need careful packaging" if damaged else "")


def build_report(trip: str, store: str, checker: str, boxes: str, items: list, products: dict) -> str:
    """Plain-text report for the warehouse screen and the e-mail."""
    packed = sum(i["expected"] for i in items)
    received = sum(i["received"] for i in items)
    damaged = sum(i["damaged"] for i in items)
    less = [i for i in items if i["received"] < i["expected"]]
    extra = [i for i in items if i["received"] > i["expected"]]
    good = len(items) - len(less) - len(extra)
    out = [f"RECEIVING REPORT  {trip}  |  {store}  |  checked by {checker}",
           f"Boxes received: {boxes}",
           f"Units: packed {packed}, counted {received}, damaged {damaged} (go back to the warehouse), actual received {received - damaged}",
           f"Lines: {len(items)} total - {good} good, {len(less)} less ({sum(i['expected']-i['received'] for i in less)} units short), "
           f"{len(extra)} extra ({sum(i['received']-i['expected'] for i in extra)} units more)"]
    if less or extra or damaged:
        out.append("\nACTION NEEDED - lines with a difference:")
        for i in items:
            if i["received"] != i["expected"] or i["damaged"]:
                out.append(f"  {products[i['pack_id']]}: packed {i['expected']}, received {i['received']}, "
                           f"match {i['received']-i['expected']:+d}, damaged {i['damaged']}  -> {remark_for(i['expected'], i['received'], i['damaged'])}")
        problem = sum(1 for i in items if i["received"] != i["expected"] or i["damaged"])
        level = level_for(problem, len(items))
        out.append("\n" + (message_for(level, store, trip, problem, len(items),
                                         sum(i["expected"] - i["received"] for i in less), damaged) if level else ALERT))
    else:
        out.append("\nEverything matched. No action needed.")
    return "\n".join(out)


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def pending_stores() -> list[tuple[str, str]]:
    conn = connect()
    rows = conn.execute("""SELECT t.trip_code, s.store_code, ts.boxes_count, ts.total_units,
                                  (SELECT COUNT(*) FROM packing_items i WHERE i.trip_id = ts.trip_id AND i.store_id = ts.store_id
                                   AND i.packed_qty > 0) AS items
                           FROM trip_stores ts JOIN trips t USING (trip_id)
                           JOIN stores s USING (store_id) WHERE ts.status != 'received'
                           ORDER BY t.trip_date, s.store_code""").fetchall()
    conn.close()
    return [(r[0], r[1], r[2], r[3], r[4]) for r in rows]


# ---------- NODE 1: load what SHOULD be in the boxes (reads only) ----------
def load_expected(state: State) -> State:
    conn = connect()
    head = conn.execute("""SELECT ts.trip_id, ts.store_id, ts.boxes_count, ts.status FROM trip_stores ts
                           JOIN trips t USING (trip_id) JOIN stores s USING (store_id)
                           WHERE t.trip_code = ? AND s.store_code = ?""", (state["trip_code"], state["store_code"])).fetchone()
    if head is None:
        conn.close()
        return {"result": {"error": f"{state['store_code']} is not on {state['trip_code']}."}}
    if head["status"] == "received":
        conn.close()
        return {"result": {"error": f"{state['store_code']} already confirmed receipt of {state['trip_code']}."}}
    lines = [dict(r) for r in conn.execute("""
        SELECT i.pack_id, i.packed_qty AS expected, p.sku, p.product_name
        FROM packing_items i JOIN products p USING (product_id)
        WHERE i.trip_id = ? AND i.store_id = ? AND i.packed_qty > 0 ORDER BY p.sku""",
        (head["trip_id"], head["store_id"]))]
    conn.close()
    return {"lines": lines, "boxes_expected": head["boxes_count"]}


def has_list(state: State) -> str:
    return "end" if "result" in state else "check"


# ---------- NODE 2: a PERSON (or the simulator) checks the boxes (the graph pauses here) ----------
def check_boxes(state: State) -> State:
    if state.get("simulate"):
        rng = random.Random(f"42-{state['trip_code']}-{state['store_code']}")
        items = []
        for l in state["lines"]:
            roll = rng.random()
            got, dmg = l["expected"], 0
            if roll < 0.04 and l["expected"] > 1:
                got = l["expected"] - rng.randint(1, min(2, l["expected"] - 1))     # less than packed
            elif roll < 0.06:
                got = l["expected"] + 1                                             # one extra
            elif roll < 0.09:
                dmg = 1                                                             # one damaged
            items.append({"pack_id": l["pack_id"], "expected": l["expected"], "received": got, "damaged": dmg})
        return {"answer": {"checker": rng.choice(SIM_STAFF), "boxes_received": state["boxes_expected"], "items": items}}
    return {"answer": interrupt({"trip": state["trip_code"], "store": state["store_code"],
                                 "lines": state["lines"], "boxes_expected": state["boxes_expected"]})}


# ---------- NODE 3: save the receipt (ONE transaction) ----------
def save_receipt(state: State) -> State:
    a = state["answer"]
    for i in a["items"]:                       # guardrail R14: checked here as well, not only on the screen
        if i["received"] < 0 or i["damaged"] < 0 or i["damaged"] > i["received"]:
            raise ValueError("Damaged cannot be more than received, and numbers cannot be negative.")
    if a["boxes_received"] < 0:
        raise ValueError("Boxes received cannot be negative.")
    conn = connect()
    try:
        with conn:
            trip_id, store_id = conn.execute("""SELECT t.trip_id, s.store_id FROM trips t, stores s
                                                WHERE t.trip_code = ? AND s.store_code = ?""",
                                             (state["trip_code"], state["store_code"])).fetchone()
            rid = conn.execute("""INSERT INTO store_receipts (trip_id, store_id, checker_name, boxes_expected, boxes_received, is_simulated)
                                  VALUES (?, ?, ?, ?, ?, ?)""",
                               (trip_id, store_id, a["checker"], state["boxes_expected"], a["boxes_received"],
                                1 if state.get("simulate") else 0)).lastrowid
            conn.executemany("""INSERT INTO receipt_items (receipt_id, packing_item_id, expected_qty, received_qty, damaged_qty, remark)
                                VALUES (?, ?, ?, ?, ?, ?)""",
                             [(rid, i["pack_id"], i["expected"], i["received"], i["damaged"], remark_for(i["expected"], i["received"], i["damaged"])) for i in a["items"]])
            # damaged units are returned to the warehouse; they stay in the store receipt (receipt_items.damaged_qty).
            # Inventory 'Damaged' is only for damage that came from the vendor, so it is not changed here.
            problem_lines = sum(1 for i in a["items"] if i["received"] != i["expected"] or i["damaged"])
            level = level_for(problem_lines, len(a["items"]))
            if level:       # a message for Agent 1 and the warehouse screen
                conn.execute("""INSERT INTO warehouse_alerts (trip_id, store_id, level, problem_lines, total_lines, message)
                                VALUES (?, ?, ?, ?, ?, ?)""",
                             (trip_id, store_id, level, problem_lines, len(a["items"]),
                              message_for(level, state["store_code"], state["trip_code"], problem_lines, len(a["items"]),
                                          sum(i["expected"] - i["received"] for i in a["items"] if i["received"] < i["expected"]),
                                          sum(i["damaged"] for i in a["items"]))))
            conn.execute("UPDATE trip_stores SET status = 'received' WHERE trip_id = ? AND store_id = ?", (trip_id, store_id))
            conn.execute("UPDATE boxes SET status = 'received' WHERE trip_id = ? AND store_id = ?", (trip_id, store_id))
            if not conn.execute("SELECT 1 FROM trip_stores WHERE trip_id = ? AND status != 'received'", (trip_id,)).fetchone():
                conn.execute("UPDATE trips SET status = 'received' WHERE trip_id = ?", (trip_id,))
    finally:
        conn.close()
    items = a["items"]
    products = {l["pack_id"]: f"{l['sku']} {l['product_name'][:30]}" for l in state["lines"]}
    boxes = f"{a['boxes_received']}/{state['boxes_expected']}"
    report = build_report(state["trip_code"], state["store_code"], a["checker"], boxes, items, products)
    problem = any(i["received"] != i["expected"] or i["damaged"] for i in items) or a["boxes_received"] != state["boxes_expected"]
    problem_lines = sum(1 for i in items if i["received"] != i["expected"] or i["damaged"])
    level = level_for(problem_lines, len(items))
    subject = (f"[{level if level in ('SERIOUS', 'WARNING') else 'ACTION NEEDED' if problem else 'OK'}] "
               f"{state['trip_code']} {state['store_code']} receiving report")
    mail = send_report(subject, report)
    return {"result": {"trip": state["trip_code"], "store": state["store_code"], "report": report, "mail": mail, "clear": not problem, "level": level}}


def end_node(state: State) -> State:
    return {}


builder = StateGraph(State)
builder.add_node("load_expected", load_expected)
builder.add_node("check_boxes", check_boxes)
builder.add_node("save_receipt", save_receipt)
builder.add_node("end_node", end_node)
builder.add_edge(START, "load_expected")
builder.add_conditional_edges("load_expected", has_list, {"check": "check_boxes", "end": "end_node"})
builder.add_edge("check_boxes", "save_receipt")
builder.add_edge("save_receipt", END)
builder.add_edge("end_node", END)
graph = builder.compile(checkpointer=InMemorySaver())


def run(trip_code: str, store_code: str, simulate: bool, ask=None) -> dict:
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    result = graph.invoke({"trip_code": trip_code, "store_code": store_code, "simulate": simulate}, config)
    while "__interrupt__" in result:
        result = graph.invoke(Command(resume=ask(result["__interrupt__"][0].value)), config)
    return result["result"]


# ---------- the person at the store ----------
def ask_in_terminal(q: dict) -> dict:
    lines = q["lines"]
    items = [{"pack_id": l["pack_id"], "expected": l["expected"], "received": None, "damaged": 0} for l in lines]
    boxes = q["boxes_expected"]

    def unchecked() -> list[int]:
        return [n for n, i in enumerate(items, 1) if i["received"] is None]

    def show() -> None:
        print(f"\n{q['trip']}  |  {q['store']}  |  boxes expected: {q['boxes_expected']}  |  "
              f"total items: {len(items)}  |  total packed qty: {sum(i['expected'] for i in items)}")
        print(f"{'no':>3} {'SKU':16} {'product':28} {'packed':>6} {'received':>8} {'match':>6} {'damaged':>8}  remark")
        for n, (l, i) in enumerate(zip(lines, items), 1):
            if i["received"] is None:
                print(f"{n:>3} {l['sku'][:16]:16} {l['product_name'][:28]:28} {i['expected']:>6} {'-':>8} {'':>6} {'':>8}  to check")
            else:
                print(f"{n:>3} {l['sku'][:16]:16} {l['product_name'][:28]:28} {i['expected']:>6} {i['received']:>8} "
                      f"{i['received'] - i['expected']:>+6d} {i['damaged']:>8}  {remark_for(i['expected'], i['received'], i['damaged'])}")
        left = unchecked()
        done_items = [i for i in items if i["received"] is not None]
        print(f"{'TOTAL':>47} {sum(i['expected'] for i in items):>6} {sum(i['received'] for i in done_items):>8} "
              f"{sum(i['received'] - i['expected'] for i in done_items):>+6d} {sum(i['damaged'] for i in done_items):>8}")
        print(f"Boxes received: {boxes}   |   Checked {len(items) - len(left)} of {len(items)} lines")
        if not left:
            print(">>> ALL LINES CHECKED. Type  done  to confirm and send the report.")

    name = ""
    while not name:
        name = input("\nYour name (who is checking)? ").strip()
    show()
    print("\nCheck every line. Count the real items, then:")
    print("  ok 3            line 3 arrived exactly as packed        (ok 1-5  or  ok all  also work)")
    print("  set 5 18        line 5: 18 received (match and remark are worked out by the agent)")
    print("  set 5 18 2      line 5: 18 received, 2 of those 18 are damaged (damaged is ONLY what you type)")
    print("  boxes 4         boxes that really arrived")
    print("  show            show the list again")
    print("  done            appears when ALL lines are checked: confirms and sends the report")
    while True:
        words = input("\n> ").strip().split()
        if not words:
            continue
        cmd = words[0].lower()
        try:
            if cmd == "done":
                left = unchecked()
                if left:
                    print(f"Not yet. {len(left)} line(s) still to check: {', '.join(map(str, left))}")
                    continue
                return {"checker": name, "boxes_received": boxes, "items": items}
            if cmd == "show":
                show()
            elif cmd == "boxes":
                boxes = int(words[1]); show()
            elif cmd == "ok":
                arg = words[1].lower()
                if arg == "all":
                    nums = range(len(items))
                elif "-" in arg:
                    lo, hi = arg.split("-"); nums = range(int(lo) - 1, int(hi))
                else:
                    nums = [int(arg) - 1]
                for n in nums:
                    if not 0 <= n < len(items):
                        raise ValueError
                    items[n]["received"], items[n]["damaged"] = items[n]["expected"], 0
                show()
            elif cmd == "set":
                n = int(words[1]) - 1
                received = int(words[2])
                damaged = int(words[3]) if len(words) > 3 else 0
                if n < 0 or received < 0 or damaged < 0 or damaged > received:
                    raise ValueError
                items[n]["received"], items[n]["damaged"] = received, damaged
                show()
                diff = received - items[n]["expected"]
                print(f"Line {n+1} saved: packed {items[n]['expected']}, received {received}"
                      + (f" -> {-diff} less than packed" if diff < 0 else f" -> {diff} extra" if diff > 0 else " -> matches")
                      + (f"; {damaged} of the {received} counted are damaged, so actual received = {received - damaged}" if damaged else "")
                      + f" (remark: {remark_for(items[n]['expected'], received, damaged)})"
                      + f".  {len(unchecked())} line(s) left.")
            else:
                print("Commands: ok, set, boxes, show, done")
        except (ValueError, IndexError):
            print("That did not work. Examples:  ok 3   |   ok 1-5   |   set 5 18 2  (damaged cannot be more than received)")


def print_result(r: dict) -> None:
    if "error" in r:
        print(r["error"]); return
    print("\n" + "=" * 70 + "\n" + r["report"] + "\n" + "-" * 70 + "\n" + r["mail"])
    print(f"DONE: the warehouse has been told that {r['store']} finished checking {r['trip']} "
          f"({'CLEAR' if r['clear'] else 'CHECKED - ACTION NEEDED'}).")
    if r.get("level") == "SERIOUS":
        print("\n" + "!" * 70 + "\n!!! SERIOUS WARNING TO THE WAREHOUSE TEAM !!!")
        print(r["report"].splitlines()[-1] + "\n" + "!" * 70)
    elif not r["clear"]:
        print(r["report"].splitlines()[-1])
    print("=" * 70)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--simulate", action="store_true")
    args = parser.parse_args()
    todo = pending_stores()
    if not todo:
        raise SystemExit("Nothing is waiting to be received (run agent2_trip.py first).")
    if args.simulate:
        for trip, store, *_ in todo:
            print_result(run(trip, store, True))
    else:
        print("Waiting to be received:")
        for trip, store, boxes, units, items in todo:
            print(f"  {trip}  {store:8} total items {items}, total packed qty {units}, total boxes {boxes}")
        trip = input("\nTrip ID? ").strip().upper()
        letter = input("Store (for example C)? ").strip()
        store = letter if letter.lower().startswith("store") else f"Store {letter.upper()}"
        print_result(run(trip, store, False, ask_in_terminal))
