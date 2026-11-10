"""Step 9-11: Agent 1, the packing-list agent (a LangGraph graph) with a HUMAN REVIEW step.

Stock is SHARED between stores, so Agent 1 looks at ALL pending restock requests together:
    load requests -> share the stock (allocation.py rules) -> PERSON REVIEWS / EDITS ->
    AI writes a short note per store -> save decisions + raise one barcode request per store.

DESIGN RULES
  * The CODE decides quantities and 'out of stock'. The AI only writes the notes.
  * The business is young, so a PERSON can review and edit the proposal before anything is saved.
    Every edit is stored (agent's number vs the person's number) as data for later learning.

Run from the project folder:
    python agent1_packing.py            run, then pause so you can review / edit (the normal way)
    python agent1_packing.py --auto     no review step (for tests or scheduled runs)
    python agent1_packing.py --detail   also print out-of-stock items and packing lists
    python agent1_packing.py --reset    put everything back to 'pending' (for re-testing)
"""
import os
import sqlite3
import sys
import uuid
from pathlib import Path
from typing import TypedDict

from dotenv import load_dotenv
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from allocation import GOOD_SALES_UNITS, PLENTY_DAYS_BY_TIER, allocate_repeat_item, need_quantity, store_tier
import guardrails

ROOT = Path(__file__).resolve().parent
DB_FILE = ROOT / "data" / "retail.db"
load_dotenv(ROOT / ".env")
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")


# ---------- STATE: the shared notebook for this run ----------
class State(TypedDict, total=False):
    auto: bool            # True = skip the human review
    requests: list        # every pending request (all stores), each a dict
    decisions: list       # one decision per request (made by code, maybe edited by a person)
    original_qty: dict    # request_id -> what the AGENT proposed (kept so edits can be recorded)
    edits: list           # edits made by the person
    cancelled: bool
    stores: dict          # per store: decisions, packing_list, summary, note, note_source, labels


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ---------- NODE 1: load every pending request, with sales, stock and the store's rank ----------
def load_requests(state: State) -> State:
    conn = connect()
    ranking = [r[0] for r in conn.execute("""
        SELECT st.store_code FROM stores st LEFT JOIN sales s ON s.store_id = st.store_id
        WHERE st.is_active = 1 GROUP BY st.store_code ORDER BY COALESCE(SUM(s.quantity), 0) DESC, st.store_code""")]
    rank_of = {code: i + 1 for i, code in enumerate(ranking)}          # 1 = best-selling store
    rows = conn.execute("""
        SELECT r.request_id, r.store_id, s.store_code, p.product_id, p.sku, p.product_name, r.requested_qty,
               COALESCE((SELECT SUM(x.quantity) FROM sales x
                         WHERE x.store_id = r.store_id AND x.product_id = r.product_id), 0) AS sold,
               COALESCE(w.available_qty, 0) AS in_stock, w.rack_name,
               COALESCE((SELECT k.store_qty FROM store_stock k WHERE k.store_id = r.store_id AND k.product_id = r.product_id
                         ORDER BY k.snapshot_date DESC LIMIT 1), 0) AS store_qty,
               (SELECT pr.selling_price FROM product_prices pr WHERE pr.product_id = r.product_id
                ORDER BY pr.effective_from DESC LIMIT 1) AS price, r.request_note AS note,
               COALESCE((SELECT SUM(i.qty_to_pack) FROM packing_items i
                         WHERE i.product_id = r.product_id AND i.trip_id IS NULL), 0) AS already_promised
        FROM restock_requests r
        JOIN stores s ON s.store_id = r.store_id
        JOIN products p ON p.product_id = r.product_id
        LEFT JOIN warehouse_stock w ON w.product_id = r.product_id
        WHERE r.status = 'pending'
        ORDER BY s.store_code, r.request_id""").fetchall()
    conn.close()
    requests = []
    for r in rows:
        d = dict(r)
        d["store_rank"] = rank_of.get(d["store_code"], len(ranking))
        d["tier"], d["tier_weight"] = store_tier(d["store_rank"], len(ranking))
        requests.append(d)
    return {"requests": requests}


# ---------- CONDITIONAL EDGE: nothing pending? stop early ----------
def anything_to_do(state: State) -> str:
    return "allocate_stock" if state["requests"] else "nothing_to_do"


def nothing_to_do(state: State) -> State:
    return {"stores": {}}


# ---------- helper: group decisions per store (used after allocation and again after edits) ----------
def build_store_views(decisions: list) -> dict:
    stores: dict[str, dict] = {}
    for d in decisions:
        store = stores.setdefault(d["store_code"], {"decisions": [], "packing_list": []})
        store["decisions"].append(d)
        if d["approved_qty"] > 0:
            store["packing_list"].append({"rack": d["rack_name"] or "NO RACK", "sku": d["sku"],
                                          "product": d["product_name"], "pack_qty": d["approved_qty"]})
    for store in stores.values():
        store["packing_list"].sort(key=lambda item: (item["rack"], item["sku"]))   # staff walk the racks in order
        approved = [d for d in store["decisions"] if d["status"] == "approved"]
        store["summary"] = {"processed": len(store["decisions"]), "approved": len(approved),
                            "partial": sum(d["approved_qty"] < d["need"] for d in approved),
                            "out_of_stock": sum(d["status"] == "out_of_stock" for d in store["decisions"]),
                            "not_needed": sum(d["status"] == "not_needed" for d in store["decisions"])}
    return stores


def days_for(r: dict):
    """Days of cover that count as 'plenty'. None = never 'plenty': a top/average store that sells well always gets stock."""
    if r["tier"] in ("top", "average") and r["sold"] >= GOOD_SALES_UNITS:
        return None
    return PLENTY_DAYS_BY_TIER[r["tier"]]


# ---------- NODE 2: share the stock between stores (plain code, no AI) ----------
def allocate_stock(state: State) -> State:
    by_product: dict[int, list] = {}
    for r in state["requests"]:
        by_product.setdefault(r["product_id"], []).append(r)

    granted: dict[int, int] = {}
    for reqs in by_product.values():
        available = max(reqs[0]["in_stock"] - reqs[0]["already_promised"], 0)
        granted.update(allocate_repeat_item(
            available, [(r["request_id"], r["sold"], r["requested_qty"], r["tier_weight"], r["store_qty"], bool(r["note"]), days_for(r)) for r in reqs]))

    total_need = {pid: sum(need_quantity(x["sold"], x["requested_qty"], x["store_qty"], bool(x["note"]), days_for(x)) for x in reqs)
                  for pid, reqs in by_product.items()}
    decisions = []
    for r in state["requests"]:
        qty, asked = granted[r["request_id"]], r["requested_qty"]
        need = need_quantity(r["sold"], asked, r["store_qty"], bool(r["note"]), days_for(r))
        available = max(r["in_stock"] - r["already_promised"], 0)
        if qty == 0 and need == 0:
            status = "not_needed"
            reason = f"Store already has {r['store_qty']} on the shelf (more than {days_for(r)} days of its sales; {r['tier']} stores keep a {days_for(r)}-day buffer): nothing to pack"
        elif qty == 0:
            status = "out_of_stock"
            reason = ("No stock available" if available == 0 else "Stock too low to share: nothing left for this store")
        else:
            status = "approved"
            if qty < need:
                n_stores = len(by_product[r["product_id"]])
                if n_stores == 1:
                    reason = f"Store needs {need}, but the warehouse has only {available} of this item: packing {qty}"
                else:
                    reason = (f"Store needs {need}, but the warehouse has only {available} for {n_stores} stores "
                              f"(together they need {total_need[r['product_id']]}). Shared by store sales ranking ({r['tier']} store): packing {qty}")
            elif r["sold"] <= 0:
                reason = (f"Store has 0 qty of this item and no sales yet: packing what the store asked ({asked})"
                          if r["store_qty"] == 0 else
                          f"No sales yet, store has {r['store_qty']}: packing what the store asked ({asked})")
            elif asked > r["sold"] and r["note"]:
                reason = f"Store asked {asked} (sold {r['sold']} in 30 days); Note explains the demand: {r['note']}"
            elif asked > r["sold"]:
                reason = (f"Store asked {asked}, but it sold only {r['sold']} in the last 30 days and the Note is empty, "
                          f"so there is no reason for extra demand: packing {qty} (the 30-day sales)")
            else:
                reason = f"Good demand: sold {r['sold']} in 30 days, store asked {asked}"
        decisions.append({**r, "need": need, "status": status, "approved_qty": qty, "reason": reason})
    return {"decisions": decisions, "original_qty": {d["request_id"]: d["approved_qty"] for d in decisions},
            "edits": [], "cancelled": False, "stores": build_store_views(decisions)}


# ---------- the same steps, for the Agent 1 screen (one store at a time) ----------
def propose() -> dict:
    """Load every pending request and let the agent make its proposal. Nothing is saved yet."""
    state = load_requests({})
    if not state["requests"]:
        return {"decisions": [], "original_qty": {}, "edits": [], "stores": {}}
    state.update(allocate_stock(state))
    return state


def finish_store(state: dict, store_code: str, changes: dict) -> tuple[bool, str]:
    """The person pressed Done for one store. changes = {request_id: (qty, reason)}.
    Applies the edits (same checks as the terminal review), writes the note and saves the packing list + barcode request."""
    decisions = {d["request_id"]: dict(d) for d in state["decisions"]}
    edits: list = []
    for rid, (qty, reason) in changes.items():
        if decisions[rid]["store_code"] != store_code:
            return False, "That line belongs to another store."
        if qty != state["original_qty"][rid]:
            error = apply_edit(decisions, state["original_qty"], edits, {"request_id": rid, "qty": qty, "note": reason})
            if error:
                return False, error
    mine = [d for d in decisions.values() if d["store_code"] == store_code]
    views = build_store_views(mine)
    part = {"stores": views, "edits": edits}
    part["stores"] = write_notes(part)["stores"]
    try:
        save_and_request_barcodes(part)
    except ValueError as error:          # guardrail R09: the live stock changed since the proposal
        return False, str(error)
    return True, part["stores"][store_code]["note"]


# ---------- NODE 3: a PERSON reviews, and can edit any quantity (the graph pauses here) ----------
def apply_edit(decisions: dict, original: dict, edits: list, answer: dict):
    """Returns an error message, or None when the edit was applied."""
    try:
        rid, qty = int(answer["request_id"]), int(answer["qty"])
    except (KeyError, TypeError, ValueError):
        return "An edit needs a request id and a whole-number quantity."
    if rid not in decisions:
        return f"There is no request with id {rid}."
    if qty < 0:
        return "A quantity cannot be negative."
    d = decisions[rid]
    available = max(d["in_stock"] - d["already_promised"], 0)
    others = sum(x["approved_qty"] for x in decisions.values() if x["product_id"] == d["product_id"] and x is not d)
    if others + qty > available:
        return (f"Only {available - others} unit(s) of '{d['product_name']}' are left to give "
                f"(warehouse {d['in_stock']}, other stores are getting {others}).")
    note = (answer.get("note") or "").strip()
    d["approved_qty"] = qty
    d["status"] = "approved" if qty > 0 else ("not_needed" if d["need"] == 0 else "out_of_stock")
    d["reason"] = f"Edited by a person: agent proposed {original[rid]}, person set {qty}" + (f" ({note})" if note else "")
    edits[:] = [e for e in edits if e["request_id"] != rid]
    if qty != original[rid]:
        edits.append({"request_id": rid, "agent_qty": original[rid], "human_qty": qty, "reason": note})
    return None


def human_review(state: State) -> State:
    if state.get("auto"):
        return {}
    decisions = {d["request_id"]: dict(d) for d in state["decisions"]}
    original, edits, error = state["original_qty"], [], None
    while True:
        lines = [{"request_id": d["request_id"], "store": d["store_code"], "sku": d["sku"], "product": d["product_name"],
                  "sold": d["sold"], "asked": d["requested_qty"], "need": d["need"], "wh": d["in_stock"],
                  "proposed": d["approved_qty"],
                  "tier": d["tier"], "reason": d["reason"]} for d in decisions.values()]
        answer = interrupt({"kind": "review", "lines": lines, "edits": len(edits), "error": error})
        error = None
        action = answer.get("action")
        if action == "approve":
            break
        if action == "cancel":
            return {"cancelled": True}
        if action == "edit":
            error = apply_edit(decisions, original, edits, answer)
    final = list(decisions.values())
    return {"decisions": final, "edits": edits, "stores": build_store_views(final)}


def route_review(state: State) -> str:
    return "cancelled" if state.get("cancelled") else "write_notes"


def cancelled(state: State) -> State:
    return {"stores": {}}


# ---------- NODE 4: the AI writes a short note per store (it cannot change any decision) ----------
def standard_note(code: str, s: dict) -> str:
    return (f"{code}: {s['approved']} of {s['processed']} items can be packed "
            f"({s['partial']} short of what the store needs). {s['out_of_stock']} items are out of stock"
            + (f", {s['not_needed']} not needed (the store has enough)." if s.get("not_needed") else "."))


def write_notes(state: State) -> State:
    llm = None
    try:
        if os.getenv("GOOGLE_API_KEY"):
            from langchain_google_genai import ChatGoogleGenerativeAI
            llm = ChatGoogleGenerativeAI(model=MODEL, max_output_tokens=300, timeout=30, max_retries=1)
    except Exception:
        llm = None

    for code, store in state["stores"].items():
        s, decisions = store["summary"], store["decisions"]
        store["note"], store["note_source"] = standard_note(code, s), "standard"
        if llm is None:
            continue
        facts = [f"Store: {code}",
                 f"Requests: {s['processed']}, packable: {s['approved']}, short of what the store needs: {s['partial']}, "
                 f"out of stock: {s['out_of_stock']}",
                 "Out of stock items: " + ("; ".join(d["product_name"] for d in decisions
                                                     if d["status"] == "out_of_stock")[:600] or "none"),
                 "Short items: " + ("; ".join(f"{d['product_name']} ({d['approved_qty']}/{d['need']})"
                                              for d in decisions if d["status"] == "approved"
                                              and d["approved_qty"] < d["need"])[:600] or "none")]
        prompt = ("You help a retail warehouse team. Write a packing note of at most 4 short sentences for the "
                  "staff packing this store's order. Mention what is missing and what to tell the store. "
                  "Use ONLY the facts below. Do not invent numbers or products.\n\n" + "\n".join(facts))
        try:
            text = llm.invoke(prompt).text.strip()
            if guardrails.check_ai_text(text, "\n".join(facts)) is None:      # guardrail R08: no invented numbers, no links, not too long
                store["note"], store["note_source"] = text, "AI"
        except Exception:        # AI unavailable? The agent still works, with the standard note.
            pass
    return {"stores": state["stores"]}


# ---------- NODE 5: save decisions, edits and barcode requests (ONE transaction for everything) ----------
def save_and_request_barcodes(state: State) -> State:
    conn = connect()
    wanted = [(d["product_id"], d["approved_qty"]) for store in state["stores"].values() for d in store["decisions"] if d["approved_qty"] > 0]
    problems = guardrails.check_live_stock(conn, wanted)           # guardrail R09: look at the warehouse as it is right now
    if problems:
        conn.close()
        raise ValueError(" ".join(problems))
    with conn:
        for code, store in state["stores"].items():
            s = store["summary"]
            store_id = conn.execute("SELECT store_id FROM stores WHERE store_code = ?", (code,)).fetchone()[0]
            conn.executemany(
                "UPDATE restock_requests SET status = ?, approved_qty = ?, decision_reason = ? WHERE request_id = ?",
                [(d["status"], d["approved_qty"], d["reason"], d["request_id"]) for d in store["decisions"]])
            conn.execute(
                """INSERT INTO agent_runs (agent_name, store_id, requests_processed, approved_count, partial_count,
                                           out_of_stock_count, note) VALUES ('Agent 1', ?, ?, ?, ?, ?, ?)""",
                (store_id, s["processed"], s["approved"], s["partial"], s["out_of_stock"], store["note"]))
            to_pack = [d for d in store["decisions"] if d["approved_qty"] > 0]
            store["labels"] = sum(d["approved_qty"] for d in to_pack)          # one label per unit
            if to_pack:
                open_one = conn.execute("""SELECT barcode_request_id FROM barcode_requests
                                           WHERE store_id = ? AND status = 'requested' ORDER BY barcode_request_id LIMIT 1""",
                                        (store_id,)).fetchone()
                if open_one:       # labels of this store are not printed yet: add to that request, so the store has ONE request to print
                    barcode_id = open_one[0]
                    conn.execute("UPDATE barcode_requests SET total_labels = total_labels + ? WHERE barcode_request_id = ?",
                                 (store["labels"], barcode_id))
                else:
                    barcode_id = conn.execute("INSERT INTO barcode_requests (store_id, total_labels) VALUES (?, ?)",
                                              (store_id, store["labels"])).lastrowid
                conn.executemany(
                    """INSERT INTO packing_items (barcode_request_id, request_id, store_id, product_id, rack_name,
                                                  qty_to_pack) VALUES (?, ?, ?, ?, ?, ?)""",
                    [(barcode_id, d["request_id"], store_id, d["product_id"], d["rack_name"], d["approved_qty"])
                     for d in sorted(to_pack, key=lambda d: (d["rack_name"] or "~", d["sku"]))])
        conn.executemany(
            "INSERT INTO allocation_edits (request_id, agent_qty, human_qty, reason) VALUES (?, ?, ?, ?)",
            [(e["request_id"], e["agent_qty"], e["human_qty"], e["reason"]) for e in state.get("edits", [])])
    conn.close()
    return {"stores": state["stores"]}


# ---------- wire the graph ----------
builder = StateGraph(State)
builder.add_node("load_requests", load_requests)
builder.add_node("allocate_stock", allocate_stock)
builder.add_node("human_review", human_review)
builder.add_node("write_notes", write_notes)
builder.add_node("save_and_request_barcodes", save_and_request_barcodes)
builder.add_node("nothing_to_do", nothing_to_do)
builder.add_node("cancelled", cancelled)

builder.add_edge(START, "load_requests")
builder.add_conditional_edges("load_requests", anything_to_do, ["allocate_stock", "nothing_to_do"])
builder.add_edge("allocate_stock", "human_review")
builder.add_conditional_edges("human_review", route_review, ["write_notes", "cancelled"])
builder.add_edge("write_notes", "save_and_request_barcodes")
builder.add_edge("save_and_request_barcodes", END)
builder.add_edge("nothing_to_do", END)
builder.add_edge("cancelled", END)
graph = builder.compile(checkpointer=InMemorySaver())      # the checkpointer lets the graph pause and resume


def run_agent(auto: bool, ask) -> dict:
    """Runs the graph; each time it pauses for the person, `ask` supplies the answer."""
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    result = graph.invoke({"auto": auto}, config)
    while "__interrupt__" in result:
        result = graph.invoke(Command(resume=ask(result["__interrupt__"][0].value)), config)
    return result


# ---------- the person at the keyboard ----------
HELP = """  Commands:
    show A            list Store A's lines (id, product, sold, asked, warehouse qty, agent's proposal, tier)
    show reduced      list every line where the agent gives less than the store needs (warehouse short)
    edit 57 8 note    change line 57 to 8 units (the note is optional, e.g. 'store has a promotion')
    ok                approve everything as it stands now
    cancel            stop; nothing is saved"""


def ask_in_terminal(q: dict) -> dict:
    lines = q["lines"]
    if not getattr(ask_in_terminal, "shown", False):
        ask_in_terminal.shown = True
        print("\nAGENT 1 PROPOSAL (nothing is saved until you type ok)")
        for store in sorted({l["store"] for l in lines}):
            mine = [l for l in lines if l["store"] == store]
            print(f"  {store}: {len(mine)} lines, {sum(0 < l['proposed'] < l['need'] for l in mine)} short on stock, "
                  f"{sum(l['proposed'] == 0 for l in mine)} out of stock, {sum(l['proposed'] for l in mine)} units")
        print(HELP)
    if q.get("error"):
        print(f"!! {q['error']}")
    while True:
        raw = input(f"\n[{q['edits']} edit(s)] > ").strip()
        words = raw.split()
        if not words:
            continue
        cmd = words[0].lower()
        if cmd in ("ok", "approve", "yes"):
            return {"action": "approve"}
        if cmd == "cancel":
            return {"action": "cancel"}
        if cmd == "edit" and len(words) >= 3:
            return {"action": "edit", "request_id": words[1], "qty": words[2], "note": " ".join(words[3:])}
        if cmd == "show" and len(words) >= 2:
            what = " ".join(words[1:]).title().replace("Store ", "")
            chosen = ([l for l in lines if l["proposed"] < l["need"]][:40] if what.lower() == "reduced"
                      else [l for l in lines if l["store"] == f"Store {what}"])
            print(f"  {'id':>4} {'store':8} {'product':30} {'sold':>5} {'asked':>6} {'wh qty':>7} {'agent':>6}  tier")
            for l in chosen:
                print(f"  {l['request_id']:>4} {l['store']:8} {l['product'][:30]:30} {l['sold']:>5} {l['asked']:>6} "
                      f"{l['wh']:>7} {l['proposed']:>6}  {l['tier']}")
            continue
        print(HELP)


def show_result(final: dict, detail: bool) -> None:
    if final.get("cancelled"):
        print("Cancelled. Nothing was saved.")
        return
    if not final["stores"]:
        print("No pending requests. Nothing to do.")
        return
    totals = {"processed": 0, "approved": 0, "out_of_stock": 0, "labels": 0}
    for code, st in sorted(final["stores"].items()):
        s = st["summary"]
        print(f"\n=== {code} ===")
        print(f"Requests {s['processed']}: approved {s['approved']} (short {s['partial']}), out of stock {s['out_of_stock']}")
        print(f"Barcode request raised: {st['labels']} labels (one per unit to pack)")
        print(f"Packing note ({st['note_source']}): {st['note']}")
        if detail:
            print("\nOut of stock:")
            for d in st["decisions"]:
                if d["status"] == "out_of_stock":
                    print(f"  {d['sku']:15} {d['product_name'][:40]:40} asked {d['requested_qty']:>3} -> {d['reason']}")
            print("\nPacking list (in rack order, first 8):")
            for item in st["packing_list"][:8]:
                print(f"  {item['rack']:9} {item['sku']:16} {item['product'][:36]:36} pack {item['pack_qty']}")
        totals["processed"] += s["processed"]; totals["approved"] += s["approved"]
        totals["out_of_stock"] += s["out_of_stock"]; totals["labels"] += st["labels"]
    print(f"\nALL STORES: {totals['processed']} requests, {totals['approved']} approved, "
          f"{totals['out_of_stock']} out of stock, {totals['labels']} labels requested, "
          f"{len(final.get('edits', []))} edit(s) by a person")


def reset_requests() -> None:
    conn = connect()
    with conn:
        conn.execute("DELETE FROM allocation_edits")
        conn.execute("DELETE FROM packing_items")
        conn.execute("DELETE FROM barcode_requests")
        conn.execute("UPDATE restock_requests SET status='pending', approved_qty=NULL, decision_reason=NULL "
                     "WHERE source = 'store_request'")
        conn.execute("DELETE FROM agent_runs WHERE agent_name = 'Agent 1'")
    conn.close()
    print("All store restock requests are back to 'pending'.")


if __name__ == "__main__":
    if "--reset" in sys.argv:
        reset_requests()
    else:
        from alerts import print_open_alerts      # warnings sent back by the stores about earlier trips
        print_open_alerts()
        show_result(run_agent("--auto" in sys.argv, ask_in_terminal), "--detail" in sys.argv)
