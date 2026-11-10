"""Step 14: the whole week in ONE graph, with checkpoints.

    Monday : Agent 1 (packing list, human review) -> barcode labels -> warehouse packing (human step)
    Tue-Fri: Agent 2 (that day's trip, human approval) -> store check (human step) -> Agent 3 (manager report)

Every node ends with a CHECKPOINT saved in data/pipeline_checkpoints.db. When a human step is not done yet you can
quit, come back tomorrow and continue exactly where you stopped.

    python pipeline.py                  start this week (Monday to Friday) and walk through it
    python pipeline.py --resume         continue where you stopped
    python pipeline.py --auto           no questions: every human step is simulated (a quick demo of the whole week)
    python pipeline.py --reset          throw the saved progress away and start over
    add --no-ai to skip the AI summary, --monday 2026-11-16 for another week
"""
import argparse
import datetime
import sqlite3
import sys
import uuid
from pathlib import Path
from typing import TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

import agent1_packing as agent1
import agent2_trip as agent2
import agent3_report as agent3
import simulate_warehouse as warehouse
import store_receiving as stores

ROOT = Path(__file__).resolve().parent
CHECKPOINT_DB = ROOT / "data" / "pipeline_checkpoints.db"
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]


class State(TypedDict, total=False):
    auto: bool
    use_ai: bool
    days: list            # the delivery dates of the week (Tue..Fri)
    next_day: int         # which of those days comes next
    date: str             # the day being worked on
    trip: str             # trip code created for that day (empty if none)
    stop: bool            # Agent 1 was cancelled: end the week
    log: list             # one line per finished step


def heading(text: str) -> None:
    print("\n" + "#" * 70 + f"\n# {text}\n" + "#" * 70)


# ---------- Monday ----------
def monday_agent1(state: State) -> State:
    heading("MONDAY - Agent 1: packing list and barcode request")
    from alerts import print_open_alerts
    print_open_alerts()
    final = agent1.run_agent(state["auto"], agent1.ask_in_terminal)
    agent1.show_result(final, False)
    if final.get("cancelled"):
        return {"stop": True, "log": state.get("log", []) + ["Agent 1 cancelled"]}
    n = sum(st["labels"] for st in final.get("stores", {}).values())
    return {"stop": False, "log": state.get("log", []) + [f"Agent 1: {n} labels requested"]}


def monday_warehouse(state: State) -> State:
    heading("MONDAY - warehouse staff: print labels and pack (human step)")
    if state["auto"]:
        answer = "simulate"
    else:
        answer = interrupt({"stage": "warehouse"})
    if answer == "simulate":
        warehouse.print_labels()
        warehouse.pack_items()
        note = "packing simulated"
    else:
        note = "packing done by the staff"
    return {"log": state["log"] + [f"Warehouse: {note}"]}


def after_agent1(state: State) -> str:
    return "end" if state.get("stop") else "warehouse"


# ---------- Tuesday to Friday ----------
def plan_day(state: State) -> State:
    i = state.get("next_day", 0)
    return {"date": state["days"][i], "next_day": i + 1, "trip": ""}


def day_trip(state: State) -> State:
    d = datetime.date.fromisoformat(state["date"])
    heading(f"{d.strftime('%A').upper()} {state['date']} - Agent 2: the day's trip")
    out = agent2.run_agent(state["auto"], state["date"], agent2.ask_in_terminal)["result"]
    code = out.get("trip_code") or ""
    if code:
        print(f"TRIP CREATED: {code}")
        for store, boxes, units in out["stores"]:
            print(f"  {store}: {units} units in {boxes} box(es)")
        line = f"{state['date']}: {code}"
    else:
        print("No trip today. " + (out.get("note") or "No scheduled store has packed items waiting."))
        line = f"{state['date']}: no trip"
    return {"trip": code, "log": state["log"] + [line]}


def after_trip(state: State) -> str:
    return "store_check" if state.get("trip") else "next"


def store_check(state: State) -> State:
    heading(f"{state['trip']} - store staff check the boxes (human step)")
    waiting = [(t, s) for t, s, *_ in stores.pending_stores() if t == state["trip"]]
    answer = "simulate" if state["auto"] else interrupt({"stage": "stores", "trip": state["trip"],
                                                         "stores": [s for _, s in waiting]})
    if answer == "simulate":
        for trip, store in waiting:
            stores.print_result(stores.run(trip, store, True))
        return {"log": state["log"] + [f"{state['trip']}: store checks simulated"]}
    return {"log": state["log"] + [f"{state['trip']}: store checks done by staff"]}


def manager_report(state: State) -> State:
    heading(f"{state['trip']} - Agent 3: report to the manager")
    still = [s for t, s, *_ in stores.pending_stores() if t == state["trip"]]
    if still:
        print(f"Not finished: {', '.join(still)} have not pressed done. No report yet "
              f"(run  python agent3_report.py  later).")
        return {"log": state["log"] + [f"{state['trip']}: report waiting for {', '.join(still)}"]}
    r = agent3.run(state["trip"], state["use_ai"])
    if r.get("problems"):
        print("The data does not add up:\n  - " + "\n  - ".join(r["problems"]))
        return {"log": state["log"] + [f"{state['trip']}: report blocked, data problem"]}
    print(r["report"] + "\n" + "-" * 70 + "\n" + r["mail"])
    return {"log": state["log"] + [f"{state['trip']}: report sent"]}


def more_days(state: State) -> str:
    return "plan_day" if state["next_day"] < len(state["days"]) else "finish"


def finish(state: State) -> State:
    heading("THE WEEK IS FINISHED")
    for line in state["log"]:
        print("  -", line)
    return {}


def build_graph(saver):
    b = StateGraph(State)
    for name, fn in [("agent1", monday_agent1), ("warehouse", monday_warehouse), ("plan_day", plan_day),
                     ("trip", day_trip), ("store_check", store_check), ("report", manager_report), ("finish", finish)]:
        b.add_node(name, fn)
    b.add_edge(START, "agent1")
    b.add_conditional_edges("agent1", after_agent1, {"warehouse": "warehouse", "end": END})
    b.add_edge("warehouse", "plan_day")
    b.add_edge("plan_day", "trip")
    b.add_node("after_day", lambda s: {})
    b.add_conditional_edges("trip", after_trip, {"store_check": "store_check", "next": "after_day"})
    b.add_edge("store_check", "report")
    b.add_edge("report", "after_day")
    b.add_conditional_edges("after_day", more_days, {"plan_day": "plan_day", "finish": "finish"})
    b.add_edge("finish", END)
    return b.compile(checkpointer=saver)


# ---------- the person at the keyboard ----------
def ask_human(payload: dict) -> str:
    if payload["stage"] == "warehouse":
        print("The warehouse staff must print the barcode labels and pack the items.")
        print("  s = simulate it now (test data)   d = the staff have done it (python simulate_warehouse.py or the real app)")
        print("  q = quit and continue later  (python pipeline.py --resume)")
    else:
        print(f"Stores to check for {payload['trip']}: {', '.join(payload['stores'])}")
        print("  s = simulate all store checks now   d = the stores have done it themselves (python store_receiving.py)")
        print("  q = quit and continue later  (python pipeline.py --resume)")
    while True:
        a = input("> ").strip().lower()
        if a in ("s", "simulate"):
            return "simulate"
        if a in ("d", "done"):
            return "done"
        if a in ("q", "quit"):
            print("Progress saved. Run  python pipeline.py --resume  when you are ready.")
            raise SystemExit(0)


def drive(graph, config, first_input) -> None:
    result = graph.invoke(first_input, config)
    while "__interrupt__" in result:
        result = graph.invoke(Command(resume=ask_human(result["__interrupt__"][0].value)), config)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--monday", default=(datetime.date.today() - datetime.timedelta(days=datetime.date.today().isoweekday() - 1)).isoformat(),
                   help="the Monday of the week (default: this week)")
    p.add_argument("--auto", action="store_true")
    p.add_argument("--no-ai", action="store_true")
    p.add_argument("--resume", action="store_true")
    p.add_argument("--reset", action="store_true")
    args = p.parse_args()
    if args.reset:
        CHECKPOINT_DB.unlink(missing_ok=True)
        raise SystemExit("Saved progress deleted. Run  python pipeline.py  to start the week again.")
    monday = datetime.date.fromisoformat(args.monday)
    if monday.isoweekday() != 1:
        raise SystemExit(f"{args.monday} is not a Monday.")
    days = [(monday + datetime.timedelta(days=n)).isoformat() for n in range(1, 5)]
    config = {"configurable": {"thread_id": f"week-{args.monday}"}, "recursion_limit": 60}
    with SqliteSaver.from_conn_string(str(CHECKPOINT_DB)) as saver:
        graph = build_graph(saver)
        saved = graph.get_state(config)
        if saved.next and not args.resume:
            raise SystemExit("There is saved progress for this week. Run  python pipeline.py --resume  to continue, "
                             "or  python pipeline.py --reset  to start over.")
        if args.resume:
            if not saved.next:
                raise SystemExit("Nothing to resume (the week has not started, or it is already finished).")
            drive(graph, config, None)
        else:
            if saved.values and not saved.next:
                raise SystemExit("This week is already finished. Use  --reset  to run it again.")
            drive(graph, config, {"auto": args.auto, "use_ai": not args.no_ai, "days": days, "next_day": 0, "log": []})
