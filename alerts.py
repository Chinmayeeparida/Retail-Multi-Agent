"""Warehouse alerts: when a store finds many problem lines on a trip, the warehouse team gets a stronger warning.

A 'problem line' is a line that arrived less, extra or damaged. The share of problem lines decides the level:
    SERIOUS  30% or more of the lines      WARNING  10% or more      NOTICE  anything below that
Change the two numbers below if the business wants stricter or softer rules.

The alert is saved in the database, so Agent 1 and the warehouse screen show it on the next packing day:
    python alerts.py          list open alerts          python alerts.py ack     mark them all as read
"""
import sqlite3
import sys
from pathlib import Path

DB_FILE = Path(__file__).resolve().parent / "data" / "retail.db"
SERIOUS_PCT = 30
WARNING_PCT = 10


def level_for(problem_lines: int, total_lines: int) -> str | None:
    if problem_lines <= 0 or total_lines <= 0:
        return None
    share = 100 * problem_lines / total_lines
    return "SERIOUS" if share >= SERIOUS_PCT else "WARNING" if share >= WARNING_PCT else "NOTICE"


def message_for(level: str, store: str, trip: str, problem_lines: int, total_lines: int, less_units: int, damaged: int) -> str:
    facts = f"{problem_lines} of {total_lines} lines for {store} on {trip} had problems ({less_units} units less, {damaged} damaged)"
    if level == "SERIOUS":
        return (f"SERIOUS WARNING: {facts}. Be extra alert: count and check every item and box before sealing. "
                "The team lead must find where the fault is and report it.")
    if level == "WARNING":
        return (f"WARNING: {facts}. Be more careful while packing and cross-check with the team why items are less or damaged.")
    return f"NOTICE: {facts}. Be careful while packing {store}."


def print_open_alerts(conn: sqlite3.Connection | None = None) -> None:
    own = conn is None
    conn = conn or sqlite3.connect(DB_FILE)
    try:
        rows = conn.execute("""SELECT a.level, a.message FROM warehouse_alerts a WHERE a.acknowledged = 0
                               ORDER BY CASE a.level WHEN 'SERIOUS' THEN 0 WHEN 'WARNING' THEN 1 ELSE 2 END, a.alert_id""").fetchall()
    except sqlite3.OperationalError:
        rows = []                                  # old database without the alerts table
    finally:
        if own:
            conn.close()
    if not rows:
        return
    print("\n" + "!" * 70 + f"\nWAREHOUSE ALERTS FROM EARLIER TRIPS ({len(rows)} open)\n" + "!" * 70)
    for _, text in rows:
        print("  " + text)
    print("!" * 70 + "\n(Mark them as read with:  python alerts.py ack)\n")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "ack":
        with sqlite3.connect(DB_FILE) as c:
            n = c.execute("UPDATE warehouse_alerts SET acknowledged = 1 WHERE acknowledged = 0").rowcount
        print(f"{n} alert(s) marked as read.")
    else:
        print_open_alerts()
        if not sqlite3.connect(DB_FILE).execute("SELECT 1 FROM warehouse_alerts WHERE acknowledged = 0").fetchone():
            print("No open alerts.")
