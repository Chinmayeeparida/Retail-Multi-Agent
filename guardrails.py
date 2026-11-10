"""Step 16: the guardrails (safety rules) of the whole system, in ONE place.

A guardrail is a rule that stops a bad number or a bad text before it is saved. Most rules are enforced right where the
data is handled (screen_logic.py, agent1_packing.py, agent2_trip.py, store_receiving.py, agent3_report.py);
this file holds the shared checks that were missing, and the RULES list that evaluate_system.py tests one by one.

Plain Python, no AI. Run the evidence report with:   python evaluate_system.py
"""
import re

# ---------- the list of rules (the evaluation report is built from this list) ----------
# (id, area, what the rule says in plain words)
RULES = [
    ("R01", "Store request", "A store cannot request more than the warehouse holds, and zero or negative quantities are refused."),
    ("R02", "Store request", "A store can request an item only once per day."),
    ("R03", "Agent 1", "Agent 1 never packs more than the warehouse can give (stock already promised to other lists is counted)."),
    ("R04", "Agent 1", "Agent 1 never packs more than the store asked, and never a negative number."),
    ("R05", "Agent 1", "When stock is short it is shared by store tier (top 21 : average 14 : low 9) and nobody gets more than it needs."),
    ("R06", "Agent 1", "A store that already has plenty on the shelf gets nothing, unless the Note explains the demand."),
    ("R07", "Agent 1", "A person's edit cannot be negative or take more stock than is left, and a changed quantity needs a reason."),
    ("R08", "Agent 1", "The AI only writes the note: a note with a number that is not in the facts, a link or a very long text is refused."),
    ("R09", "Agent 1", "A packing list is checked against the LIVE warehouse stock again just before it is saved."),
    ("R10", "Warehouse packing", "Packed + damaged can never be more than the quantity to pack, and numbers cannot be negative."),
    ("R11", "Agent 2", "A trip leaves only on a store's scheduled weekday (Mon packing only, Fri urgent only, Sat/Sun none)."),
    ("R12", "Agent 2", "A store with a problem (or nothing packed, or not enough warehouse stock) is blocked and cannot go on a trip."),
    ("R13", "Agent 2", "A trip is saved all or nothing; the warehouse stock goes down by exactly what was packed + damaged."),
    ("R14", "Store receiving", "Damaged cannot be more than received, numbers cannot be negative, and a store cannot confirm the same trip twice."),
    ("R15", "Store receiving", "A problem on a trip raises a Notice / Warning / Serious alert for the warehouse (10% and 30% of lines)."),
    ("R16", "Agent 3", "The manager summary is refused if it contains a number that is not in the data (a standard text is used instead)."),
    ("R17", "Agent 4", "A new product with bad details is refused: HSN not 4/6/8 digits, GST not a real slab, prices not cost <= selling <= MRP, no category or vendor, empty or duplicate name."),
    ("R18", "Agent 4", "The SKU and product ID are made by code, never by the AI: unique, in the catalogue format, and never reused (also when two people add products at the same time)."),
    ("R19", "Agent 4", "A product is saved only after a named person approves; cancel or a failure saves nothing."),
    ("R20", "Agent 4", "A new product gets its price and an empty warehouse slot, and cannot be requested by stores until stock arrives."),
]

MAX_NOTE_CHARS = 700
MAX_NAME_CHARS = 60
MAX_REQUEST_NOTE_CHARS = 200
ALWAYS_OK_NUMBERS = {"1", "2", "3", "4"}      # 'one', 'two' ... written as digits are not facts


# ---------- shared SQL: stock that is already promised to a packing list but has not left on a trip yet ----------
PROMISED_SQL = """COALESCE((SELECT SUM(i.qty_to_pack) FROM packing_items i
                             WHERE i.product_id = {pid} AND i.trip_id IS NULL), 0)"""


def numbers_in(text: str) -> set:
    return set(re.findall(r"\d+(?:\.\d+)?", str(text).replace(",", "")))


def check_ai_text(text: str, facts: str, max_chars: int = MAX_NOTE_CHARS) -> str | None:
    """R08 / R16. Returns None when the AI text is fine, otherwise the reason it is refused."""
    if not text or not text.strip():
        return "the text is empty"
    if len(text) > max_chars:
        return f"the text is too long ({len(text)} characters, the limit is {max_chars})"
    if re.search(r"https?://|www\.|@\w+\.\w+", text):
        return "the text contains a link or an e-mail address"
    extra = numbers_in(text) - numbers_in(facts) - ALWAYS_OK_NUMBERS
    if extra:
        return "the text contains number(s) that are not in the data: " + ", ".join(sorted(extra))
    return None


def check_live_stock(conn, lines: list) -> list:
    """R09. lines = [(product_id, qty), ...] for the packing lists about to be saved.
    Returns a list of problems (empty = fine). Reads the database as it is NOW, so two screens cannot both take the last units."""
    want: dict = {}
    for product_id, qty in lines:
        if qty < 0:
            return ["A packing quantity cannot be negative."]
        want[product_id] = want.get(product_id, 0) + qty
    problems = []
    for product_id, qty in want.items():
        row = conn.execute(f"""SELECT p.product_name, COALESCE((SELECT SUM(w.available_qty) FROM warehouse_stock w
                                                               WHERE w.product_id = p.product_id), 0), {PROMISED_SQL.format(pid='p.product_id')}
                               FROM products p WHERE p.product_id = ?""", (product_id,)).fetchone()
        if row is None:
            problems.append(f"Product {product_id} does not exist.")
            continue
        left = max(row[1] - row[2], 0)
        if qty > left:
            problems.append(f"'{row[0]}': packing {qty} but only {left} left in the warehouse "
                            f"(warehouse {row[1]}, already promised to other lists {row[2]}). Run Agent 1 again.")
    return problems


def check_request_text(name: str, note: str) -> str | None:
    """R01 helper: the free-text fields of a store request."""
    if len(name.strip()) > MAX_NAME_CHARS:
        return f"The name is too long (at most {MAX_NAME_CHARS} characters)."
    if len(note or "") > MAX_REQUEST_NOTE_CHARS:
        return f"A Note is too long (at most {MAX_REQUEST_NOTE_CHARS} characters)."
    return None


def check_trip_date(text: str):
    """R11 helper: returns (date, None) or (None, message)."""
    import datetime
    try:
        return datetime.date.fromisoformat(str(text)), None
    except ValueError:
        return None, f"'{text}' is not a date. Use the form YYYY-MM-DD, for example 2026-11-11."
