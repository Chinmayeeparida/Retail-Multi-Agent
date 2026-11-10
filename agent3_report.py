"""Step 13: Agent 3, the trip performance reporter (a LangGraph graph).

When every store of a trip has finished its receiving check, Agent 3:
    reads the data -> VERIFIES it (does the trip hang together?) -> calculates the performance figures in CODE ->
    asks the AI to write a short summary (the AI never calculates; its numbers are checked) ->
    saves the report and e-mails it to the operations head and the warehouse manager.

    python agent3_report.py                    report on every finished trip that has no report yet
    python agent3_report.py TRIP-20250902-01   report on one trip
    python agent3_report.py --no-ai            standard summary, no AI
    python agent3_report.py --again            write the report again (replaces the saved one)
"""
import argparse
import json
import os
import re
import sqlite3
import uuid
from pathlib import Path
from typing import TypedDict

from dotenv import load_dotenv
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from alerts import level_for
from notify import send_report

ROOT = Path(__file__).resolve().parent
DB_FILE = ROOT / "data" / "retail.db"
load_dotenv(ROOT / ".env")
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")


class State(TypedDict, total=False):
    trip_code: str
    use_ai: bool
    data: dict            # all figures, calculated by code
    problems: list        # data checks that failed
    summary: str
    summary_source: str
    result: dict


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def pct(a: float, b: float) -> float:
    return round(100 * a / b, 1) if b else 100.0


# ---------- NODE 1: read the trip and VERIFY the data (reads only) ----------
def load_and_verify(state: State) -> State:
    conn = connect()
    trip = conn.execute("SELECT * FROM trips WHERE trip_code = ?", (state["trip_code"],)).fetchone()
    stores, problems = [], []
    for ts in conn.execute("""SELECT ts.*, s.store_code FROM trip_stores ts JOIN stores s USING (store_id)
                              WHERE ts.trip_id = ? ORDER BY s.store_code""", (trip["trip_id"],)).fetchall():
        sid = ts["store_id"]
        pk = conn.execute("""SELECT COUNT(*) lines, SUM(qty_to_pack) planned, SUM(packed_qty) packed, SUM(damaged_qty) wh_damaged,
                                    SUM(packed_qty < qty_to_pack) short_lines
                             FROM packing_items WHERE trip_id = ? AND store_id = ?""", (trip["trip_id"], sid)).fetchone()
        rc = conn.execute("SELECT * FROM store_receipts WHERE trip_id = ? AND store_id = ?", (trip["trip_id"], sid)).fetchone()
        items = conn.execute("""SELECT i.* FROM receipt_items i JOIN store_receipts r USING (receipt_id)
                                WHERE r.trip_id = ? AND r.store_id = ?""", (trip["trip_id"], sid)).fetchall()
        dates = [r[0] for r in conn.execute("""SELECT DISTINCT r.request_date FROM packing_items i
                    JOIN restock_requests r USING (request_id) WHERE i.trip_id = ? AND i.store_id = ?""", (trip["trip_id"], sid))]
        oos = conn.execute(f"""SELECT COUNT(*) FROM restock_requests WHERE store_id = ? AND status = 'out_of_stock'
                               AND request_date IN ({','.join('?' * len(dates)) or "''"})""", (sid, *dates)).fetchone()[0]
        if rc is None:
            problems.append(f"{ts['store_code']} has no receiving check yet")
            continue
        packed_lines = [i for i in items if i["expected_qty"] > 0]
        packed = sum(i["expected_qty"] for i in items)
        if packed != ts["total_units"]:
            problems.append(f"{ts['store_code']}: receipt expects {packed} units but the trip carried {ts['total_units']}")
        if len(items) != sum(1 for _ in conn.execute("SELECT 1 FROM box_items b JOIN boxes x USING (box_id) WHERE x.trip_id = ? AND x.store_id = ? GROUP BY b.packing_item_id", (trip["trip_id"], sid))):
            problems.append(f"{ts['store_code']}: number of checked lines does not match the boxes")
        less = [i for i in items if i["received_qty"] < i["expected_qty"]]
        extra = [i for i in items if i["received_qty"] > i["expected_qty"]]
        dmg = [i for i in items if i["damaged_qty"] > 0]
        clean = [i for i in items if i["received_qty"] == i["expected_qty"] and i["damaged_qty"] == 0]
        stores.append({
            "store": ts["store_code"], "checker": rc["checker_name"], "simulated": bool(rc["is_simulated"]),
            "boxes_sent": ts["boxes_count"], "boxes_received": rc["boxes_received"],
            "lines": len(items), "planned": pk["planned"], "packed": packed, "short_packing_lines": pk["short_lines"],
            "out_of_stock_requests": oos,
            "received": sum(i["received_qty"] for i in items), "damaged": sum(i["damaged_qty"] for i in items),
            "less_lines": len(less), "less_units": sum(i["expected_qty"] - i["received_qty"] for i in less),
            "extra_lines": len(extra), "extra_units": sum(i["received_qty"] - i["expected_qty"] for i in extra),
            "damaged_lines": len(dmg), "clean_lines": len(clean),
            "problem_lines": len(items) - len(clean), "alert": level_for(len(items) - len(clean), len(items))})
    detail = [dict(r) for r in conn.execute("""
        SELECT s.store_code store, p.sku, p.product_name name, i.expected_qty packed, i.received_qty received,
               i.damaged_qty damaged, i.remark FROM receipt_items i JOIN store_receipts r USING (receipt_id)
        JOIN stores s USING (store_id) JOIN packing_items k ON k.pack_id = i.packing_item_id
        JOIN products p ON p.product_id = k.product_id
        WHERE r.trip_id = ? AND (i.received_qty != i.expected_qty OR i.damaged_qty > 0) ORDER BY s.store_code, p.sku""",
        (trip["trip_id"],))]
    conn.close()
    t = {k: sum(s[k] for s in stores) for k in ("boxes_sent", "boxes_received", "lines", "planned", "packed", "received", "damaged",
                                                "less_lines", "less_units", "extra_lines", "extra_units", "clean_lines",
                                                "out_of_stock_requests", "short_packing_lines")}
    t["packing_fill_pct"] = pct(t["packed"], t["planned"])
    t["delivery_accuracy_pct"] = pct(t["clean_lines"], t["lines"])
    t["damage_pct"] = pct(t["damaged"], t["received"])
    return {"data": {"trip": trip["trip_code"], "date": trip["trip_date"], "stores": stores, "totals": t, "detail": detail},
            "problems": problems}


def data_ok(state: State) -> str:
    return "end" if state["problems"] else "summary"


def stop_with_problems(state: State) -> State:
    return {"result": {"trip": state["trip_code"], "problems": state["problems"]}}


# ---------- NODE 2: the summary (AI writes it, a standard text is the fallback) ----------
def standard_summary(d: dict) -> str:
    t = d["totals"]
    worst = max(d["stores"], key=lambda s: s["less_units"] + s["extra_units"] + s["damaged"])
    text = (f"{len(d['stores'])} store(s) received {t['received']} of {t['packed']} packed units across {t['boxes_received']} of "
            f"{t['boxes_sent']} boxes. {t['clean_lines']} of {t['lines']} lines arrived exactly as packed "
            f"({t['delivery_accuracy_pct']}%). {t['less_units']} unit(s) were less, {t['extra_units']} were extra and "
            f"{t['damaged']} were damaged.")
    if worst["less_units"] + worst["extra_units"] + worst["damaged"]:
        text += f" {worst['store']} needs the most attention."
    return text


def numbers_in(text: str) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?", text.replace(",", "")))


def write_summary(state: State) -> State:
    d = state["data"]
    facts = json.dumps({"trip": d["trip"], "date": d["date"], "totals": d["totals"],
                        "stores": [{k: v for k, v in s.items() if k not in ("checker", "simulated")} for s in d["stores"]]})
    if state.get("use_ai") and os.getenv("GOOGLE_API_KEY"):
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
            llm = ChatGoogleGenerativeAI(model=MODEL, max_output_tokens=400, timeout=30, max_retries=1)
            reply = llm.invoke(
                "You write a short performance summary of one warehouse-to-store delivery trip for a manager. "
                "Use ONLY the figures in the JSON below; never calculate or invent a number. Write 3 or 4 plain sentences: "
                "overall result, the biggest problem (less, extra or damaged units), which store(s) need attention, "
                "and one suggested action. No bullet points, no headings.\n\n" + facts)
            text = reply.text.strip()
            allowed = numbers_in(facts) | {"1", "2", "3", "4"}
            if text and numbers_in(text) <= allowed:          # guardrail: every number must come from the data
                return {"summary": text, "summary_source": "ai"}
        except Exception:
            pass
    return {"summary": standard_summary(d), "summary_source": "standard"}


# ---------- NODE 3: build the report, save it, e-mail it (ONE transaction for the saving) ----------
def build_report(d: dict, summary: str, source: str) -> str:
    t = d["totals"]
    out = [f"TRIP PERFORMANCE REPORT  {d['trip']}  ({d['date']})", "",
           "SUMMARY" + (" (written by AI, figures checked by code)" if source == "ai" else ""), summary, "",
           "TRIP TOTALS",
           f"  Stores {len(d['stores'])} | boxes sent {t['boxes_sent']}, received {t['boxes_received']}",
           f"  Units: planned {t['planned']}, packed {t['packed']}, counted at store {t['received']}, damaged {t['damaged']} "
           f"(back to warehouse), actual received {t['received'] - t['damaged']}",
           f"  Less: {t['less_lines']} line(s), {t['less_units']} unit(s) | Extra: {t['extra_lines']} line(s), {t['extra_units']} unit(s)",
           f"  Packing fill rate {t['packing_fill_pct']}% (packed / planned) | Delivery accuracy {t['delivery_accuracy_pct']}% "
           f"(lines with no difference) | Damage {t['damage_pct']}% of received",
           f"  Out-of-stock requests for these stores: {t['out_of_stock_requests']}", "",
           "BY STORE",
           f"  {'store':9} {'boxes':>7} {'packed':>7} {'recvd':>6} {'less':>5} {'extra':>6} {'dmg':>4} {'accuracy':>9}  {'problem lines':>13}  checked by"]
    for s in d["stores"]:
        out.append(f"  {s['store']:9} {str(s['boxes_received']) + '/' + str(s['boxes_sent']):>7} {s['packed']:>7} {s['received']:>6} "
                   f"{s['less_units']:>5} {s['extra_units']:>6} {s['damaged']:>4} {pct(s['clean_lines'], s['lines']):>8}%  "
                   f"{str(s['problem_lines']) + '/' + str(s['lines']) + (' ' + s['alert'] if s['alert'] in ('SERIOUS', 'WARNING') else ''):>13}  "
                   f"{s['checker']}{' (simulated)' if s['simulated'] else ''}")
    out.append("")
    if d["detail"]:
        out.append("ACTION NEEDED - lines with a difference")
        for x in d["detail"]:
            out.append(f"  {x['store']:8} {x['sku'][:16]:16} {x['name'][:26]:26} packed {x['packed']}, received {x['received']}, "
                       f"damaged {x['damaged']}  -> {x['remark']}")
        out.append("")
        out.append("ALERT: Need to be more careful while packing. Cross-check with the team why items are less or damaged, "
                   "find where the fault is, and report it.")
    else:
        out.append("No action needed: every line arrived exactly as packed.")
    return "\n".join(out)


def save_and_send(state: State) -> State:
    d = state["data"]
    report = build_report(d, state["summary"], state["summary_source"])
    conn = connect()
    try:
        with conn:
            trip_id = conn.execute("SELECT trip_id FROM trips WHERE trip_code = ?", (d["trip"],)).fetchone()[0]
            conn.execute("INSERT OR REPLACE INTO trip_reports (trip_id, report_text, summary_source) VALUES (?, ?, ?)",
                         (trip_id, report, state["summary_source"]))
            conn.execute("""INSERT INTO agent_runs (agent_name, requests_processed, approved_count, partial_count, out_of_stock_count, note)
                            VALUES ('Agent 3', ?, ?, ?, ?, ?)""",
                         (d["totals"]["lines"], d["totals"]["clean_lines"], d["totals"]["less_lines"],
                          d["totals"]["out_of_stock_requests"], f"{d['trip']} report ({state['summary_source']} summary)"))
    finally:
        conn.close()
    t = d["totals"]
    flag = "ACTION NEEDED" if d["detail"] else "OK"
    mail = send_report(f"[{flag}] Trip performance report {d['trip']}", report)
    return {"result": {"trip": d["trip"], "report": report, "mail": mail}}


builder = StateGraph(State)
builder.add_node("load_and_verify", load_and_verify)
builder.add_node("write_summary", write_summary)
builder.add_node("save_and_send", save_and_send)
builder.add_node("stop_with_problems", stop_with_problems)
builder.add_edge(START, "load_and_verify")
builder.add_conditional_edges("load_and_verify", data_ok, {"summary": "write_summary", "end": "stop_with_problems"})
builder.add_edge("write_summary", "save_and_send")
builder.add_edge("save_and_send", END)
builder.add_edge("stop_with_problems", END)
graph = builder.compile(checkpointer=InMemorySaver())


def run(trip_code: str, use_ai: bool) -> dict:
    out = graph.invoke({"trip_code": trip_code, "use_ai": use_ai}, {"configurable": {"thread_id": str(uuid.uuid4())}})
    return out["result"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("trip", nargs="?")
    parser.add_argument("--no-ai", action="store_true")
    parser.add_argument("--again", action="store_true")
    args = parser.parse_args()
    conn = connect()
    if args.trip:
        rows = conn.execute("SELECT trip_code, status FROM trips WHERE trip_code = ?", (args.trip.upper(),)).fetchall()
    else:
        rows = conn.execute("""SELECT trip_code, status FROM trips t WHERE status = 'received'
                               AND (? OR NOT EXISTS (SELECT 1 FROM trip_reports r WHERE r.trip_id = t.trip_id))
                               ORDER BY trip_date""", (1 if args.again else 0,)).fetchall()
    pending = {}
    for code, status in rows:
        pending[code] = [r[0] for r in conn.execute("""SELECT s.store_code FROM trip_stores ts JOIN stores s USING (store_id)
            JOIN trips t USING (trip_id) WHERE t.trip_code = ? AND ts.status != 'received'""", (code,))]
    conn.close()
    if not rows:
        raise SystemExit("No finished trip is waiting for a report (a trip is finished when every store has pressed done).")
    for code, status in rows:
        if pending[code]:
            print(f"{code}: not finished yet. Still to check: {', '.join(pending[code])}\n")
            continue
        r = run(code, not args.no_ai)
        if r.get("problems"):
            print(f"{code}: the data does not add up, no report was made:\n  - " + "\n  - ".join(r["problems"]))
        else:
            print(r["report"] + "\n" + "-" * 70 + "\n" + r["mail"] + "\n")
