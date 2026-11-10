"""Step 17: Agent 4, the new-product agent (a LangGraph graph) with a HUMAN APPROVAL step.

A vendor brings a product that is not in the catalogue. Agent 4:
    checks the details -> proposes the SKU and the permanent product ID -> a PERSON approves (or corrects) ->
    saves the product, its price and an empty warehouse slot in ONE transaction.

DESIGN RULES (same as the other agents)
  * The CODE makes the SKU and the IDs. No AI ever invents an identifier, so two products can never get the same SKU.
  * A person always approves before anything is saved (the business is young: more human input = better data).
  * Bad details are refused with a plain message; look-alike products are only WARNED about (the person decides).

THE SKU (copied from the existing catalogue):
    <category prefix> + <6-digit product number> + <'IN' for an India vendor> + <letters from the product name>
    e.g. Kitchen + 001313 + IN + OSB  ->  KIT001313INOSB
    A variant of an existing product (another colour or size) can reuse that product's number: pick 'Variant of'.

After the product exists, stock is received with:   python distribute_new_stock.py <SKU> <units>
"""
import datetime
import difflib
import re
import sqlite3
import uuid
from pathlib import Path
from typing import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

ROOT = Path(__file__).resolve().parent
DB_FILE = ROOT / "data" / "retail.db"

GST_RATES = (0, 3, 5, 12, 18, 28)
SERIES_LIMIT = 2000            # catalogue numbers below this are the normal series; a few special codes above it are ignored when counting
COUNTER = "sku_number"
SIMILAR_NAME = 0.88            # names at least this alike get a warning
LOW_MARGIN_PCT = 10            # selling price less than this % above cost gets a warning
SKIP_WORDS = {"and", "of", "the", "with", "for", "a", "an", "in", "by", "to"}
FIELDS = ("name", "vendor", "new_vendor", "new_vendor_india", "category", "sub_category", "brand", "hsn", "gst",
          "colour", "mrp", "cost", "selling", "variant_of")


class State(TypedDict, total=False):
    form: dict
    problems: list
    warnings: list
    proposal: dict
    approved: bool
    approved_by: str
    result: dict


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ---------- what the screen needs for its drop-down lists ----------
def options() -> dict:
    conn = connect()
    out = {"categories": [dict(r) for r in conn.execute(
               "SELECT category_name, sku_prefix FROM categories WHERE sku_prefix IS NOT NULL ORDER BY category_name")],
           "vendors": [r[0] for r in conn.execute("SELECT vendor_name FROM vendors WHERE is_active = 1 ORDER BY vendor_name")],
           "gst": list(GST_RATES)}
    conn.close()
    return out


def suggest_category(name: str) -> str | None:
    """A hint for the person: the category whose existing products share the most words with this name. Code only, no AI."""
    words = {w for w in re.findall(r"[a-z]{3,}", name.lower()) if w not in SKIP_WORDS}
    if not words:
        return None
    conn = connect()
    seen: dict = {}                                   # word -> {category: how many products with that word}
    for product_name, category in conn.execute("""SELECT p.product_name, c.category_name FROM products p
                                                  JOIN categories c USING (category_id) WHERE c.sku_prefix IS NOT NULL"""):
        for word in words & set(re.findall(r"[a-z]{3,}", product_name.lower())):
            seen.setdefault(word, {})
            seen[word][category] = seen[word].get(category, 0) + 1
    conn.close()
    votes: dict = {}
    for per_category in seen.values():                # each word has ONE vote, shared by the categories it appears in
        total = sum(per_category.values())
        for category, n in per_category.items():
            votes[category] = votes.get(category, 0) + n / total
    return max(votes, key=votes.get) if votes else None


# ---------- NODE 1: check the details (reads only) ----------
def tidy(form: dict) -> dict:
    clean = {k: form.get(k) for k in FIELDS}
    for key in ("name", "vendor", "new_vendor", "category", "sub_category", "brand", "colour", "variant_of", "hsn"):
        clean[key] = re.sub(r"\s+", " ", str(clean[key] or "")).strip()
    clean["hsn"] = clean["hsn"].replace(" ", "")
    clean["new_vendor_india"] = bool(clean.get("new_vendor_india", True))
    for key in ("mrp", "cost", "selling", "gst"):
        try:
            clean[key] = float(clean[key])
        except (TypeError, ValueError):
            clean[key] = None
    return clean


def validate(conn: sqlite3.Connection, form: dict) -> tuple[list, list]:
    """Returns (problems, warnings). Problems stop the product; warnings only ask the person to look twice."""
    f, problems, warnings = tidy(form), [], []
    if not f["name"]:
        problems.append("Product name is required.")
    elif len(f["name"]) < 3 or len(f["name"]) > 120 or f["name"].isdigit():
        problems.append("Product name must be 3 to 120 characters and cannot be only numbers.")
    if f["new_vendor"] and f["vendor"]:
        problems.append("Choose an existing vendor OR type a new vendor, not both.")
    elif not f["vendor"] and not f["new_vendor"]:
        problems.append("Choose a vendor, or type the name of a new vendor.")
    elif f["vendor"] and not conn.execute("SELECT 1 FROM vendors WHERE vendor_name = ? COLLATE NOCASE", (f["vendor"],)).fetchone():
        problems.append(f"Vendor '{f['vendor']}' does not exist. Type it as a new vendor instead.")
    elif f["new_vendor"]:
        if len(f["new_vendor"]) < 3 or len(f["new_vendor"]) > 80:
            problems.append("A new vendor name must be 3 to 80 characters.")
        elif conn.execute("SELECT 1 FROM vendors WHERE vendor_name = ? COLLATE NOCASE", (f["new_vendor"],)).fetchone():
            problems.append(f"Vendor '{f['new_vendor']}' already exists. Choose it from the list.")
    cat = conn.execute("SELECT category_id, sku_prefix FROM categories WHERE category_name = ?", (f["category"],)).fetchone()
    if cat is None:
        problems.append("Choose a category.")
    elif not cat["sku_prefix"]:
        problems.append(f"Category '{f['category']}' has no SKU prefix, so new products cannot be added to it.")
    for label, key, limit in (("Sub category", "sub_category", 60), ("Brand", "brand", 60), ("Colour", "colour", 30)):
        if len(f[key]) > limit:
            problems.append(f"{label} is too long (at most {limit} characters).")
    if not re.fullmatch(r"\d{4}|\d{6}|\d{8}", f["hsn"]):
        problems.append("HSN code must be 4, 6 or 8 digits.")
    if f["gst"] not in GST_RATES:
        problems.append("GST rate must be one of " + ", ".join(str(g) for g in GST_RATES) + ".")
    prices = [f["mrp"], f["cost"], f["selling"]]
    if any(p is None or p <= 0 for p in prices):
        problems.append("MRP, cost price and selling price must all be numbers above 0.")
    elif not (f["cost"] <= f["selling"] <= f["mrp"]):
        problems.append("Prices must follow: cost price <= selling price <= MRP.")
    elif f["selling"] < f["cost"] * (1 + LOW_MARGIN_PCT / 100):
        warnings.append(f"The selling price is less than {LOW_MARGIN_PCT}% above the cost price: a very thin margin.")
    variant = None
    if f["variant_of"]:
        variant = conn.execute("SELECT product_number, category_id, product_name FROM products WHERE sku = ? COLLATE NOCASE",
                               (f["variant_of"],)).fetchone()
        if variant is None:
            problems.append(f"'Variant of' SKU {f['variant_of']} does not exist.")
        elif cat is not None and variant["category_id"] != cat["category_id"]:
            problems.append("A variant must be in the same category as the product it belongs to.")
    if f["name"] and not problems:
        same = conn.execute("SELECT sku FROM products WHERE lower(product_name) = lower(?)", (f["name"],)).fetchone()
        if same:
            problems.append(f"A product with this exact name already exists ({same[0]}). If it is another colour or size, "
                            "change the name and use 'Variant of'.")
        else:
            names = [(r[0], r[1]) for r in conn.execute("SELECT sku, product_name FROM products WHERE category_id = ?", (cat["category_id"],))]
            close = [(s, n) for s, n in names if difflib.SequenceMatcher(None, f["name"].lower(), n.lower()).ratio() >= SIMILAR_NAME]
            if close:
                warnings.append("Very similar product(s) already exist: " + "; ".join(f"{s} {n}" for s, n in close[:3]) + ". Is this a duplicate?")
    return problems, warnings


# ---------- NODE 2: propose the SKU (reads only; the number is only RESERVED when the person approves) ----------
def next_number(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT next_value FROM id_counters WHERE counter_name = ?", (COUNTER,)).fetchone()
    if row:
        return row[0]
    top = conn.execute("SELECT COALESCE(MAX(product_number), 0) FROM products WHERE product_number < ?", (SERIES_LIMIT,)).fetchone()[0]
    return top + 1


def name_letters(name: str) -> str:
    words = [w for w in re.findall(r"[A-Za-z]+", name) if w.lower() not in SKIP_WORDS]
    letters = "".join(w[0] for w in words)[:5].upper()
    return letters if len(letters) >= 2 else re.sub(r"[^A-Za-z]", "", name)[:3].upper()


def build_sku(conn: sqlite3.Connection, f: dict, number: int) -> str:
    prefix = conn.execute("SELECT sku_prefix FROM categories WHERE category_name = ?", (f["category"],)).fetchone()[0]
    if f["vendor"]:
        india = bool(conn.execute("SELECT is_india_vendor FROM vendors WHERE vendor_name = ? COLLATE NOCASE", (f["vendor"],)).fetchone()[0])
    else:
        india = f["new_vendor_india"]
    base = f"{prefix}{number:06d}{'IN' if india else ''}{name_letters(f['name'])}"
    sku, n = base, 1
    while conn.execute("SELECT 1 FROM products WHERE sku = ?", (sku,)).fetchone():     # never reuse an existing SKU
        n += 1
        sku = f"{base}{n}"
    return sku


def propose(form: dict) -> dict:
    """Check the details and make the proposal. Nothing is saved."""
    conn = connect()
    try:
        problems, warnings = validate(conn, form)
        if problems:
            return {"form": tidy(form), "problems": problems, "warnings": warnings}
        f = tidy(form)
        variant = conn.execute("SELECT product_number FROM products WHERE sku = ? COLLATE NOCASE", (f["variant_of"],)).fetchone() if f["variant_of"] else None
        number = variant[0] if variant else next_number(conn)
        return {"form": f, "problems": [], "warnings": warnings,
                "proposal": {"sku": build_sku(conn, f, number), "product_number": number, "reuses_number": bool(variant),
                             "vendor": f["vendor"] or f["new_vendor"], "new_vendor": bool(f["new_vendor"]),
                             "category": f["category"], "margin_pct": round(100 * (f["selling"] - f["cost"]) / f["selling"], 1)}}
    finally:
        conn.close()


# ---------- NODE 4: save everything (ONE transaction: all or nothing) ----------
def save(state: dict, approved_by: str) -> dict:
    """Run after the person approved. The details are checked AGAIN against the live database, and the number is reserved now."""
    if not approved_by or not approved_by.strip():
        return {"saved": False, "message": "Please type your name: a person must approve a new product."}
    f = state["form"]
    conn = connect()
    try:
        with conn:
            problems, _ = validate(conn, f)                                  # the catalogue may have changed since the proposal
            if problems:
                return {"saved": False, "message": " ".join(problems)}
            if f["variant_of"]:
                number = conn.execute("SELECT product_number FROM products WHERE sku = ? COLLATE NOCASE", (f["variant_of"],)).fetchone()[0]
            else:
                number = next_number(conn)
                conn.execute("INSERT INTO id_counters (counter_name, next_value) VALUES (?, ?) "
                             "ON CONFLICT(counter_name) DO UPDATE SET next_value = excluded.next_value", (COUNTER, number + 1))
            sku = build_sku(conn, f, number)
            if f["new_vendor"]:
                vendor_id = conn.execute("INSERT INTO vendors (vendor_name, is_india_vendor) VALUES (?, ?)",
                                         (f["new_vendor"], 1 if f["new_vendor_india"] else 0)).lastrowid
            else:
                vendor_id = conn.execute("SELECT vendor_id FROM vendors WHERE vendor_name = ? COLLATE NOCASE", (f["vendor"],)).fetchone()[0]
            category_id = conn.execute("SELECT category_id FROM categories WHERE category_name = ?", (f["category"],)).fetchone()[0]
            product_id = conn.execute(
                """INSERT INTO products (sku, product_number, product_name, vendor_id, category_id, sub_category, brand,
                                         hsn_code, gst_rate, colour, is_active) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
                (sku, number, f["name"], vendor_id, category_id, f["sub_category"] or None, f["brand"] or None,
                 f["hsn"], f["gst"], f["colour"] or None)).lastrowid
            today = datetime.date.today().isoformat()
            conn.execute("INSERT INTO product_prices (product_id, mrp, cost_price, selling_price, effective_from) VALUES (?, ?, ?, ?, ?)",
                         (product_id, f["mrp"], f["cost"], f["selling"], today))
            snapshot = conn.execute("SELECT MAX(snapshot_date) FROM warehouse_stock").fetchone()[0] or today
            conn.execute("INSERT INTO warehouse_stock (product_id, snapshot_date, rack_name, available_qty, damaged_qty) VALUES (?, ?, NULL, 0, 0)",
                         (product_id, snapshot))                              # an empty slot: stock arrives later through distribute_new_stock.py
            conn.execute("INSERT INTO agent_runs (agent_name, requests_processed, approved_count, partial_count, out_of_stock_count, note) "
                         "VALUES ('Agent 4', 1, 1, 0, 0, ?)", (f"{sku} {f['name']} approved by {approved_by.strip()}",))
    except sqlite3.IntegrityError as error:
        return {"saved": False, "message": f"Could not save (nothing was changed): {error}"}
    finally:
        conn.close()
    return {"saved": True, "sku": sku, "product_id": product_id, "product_number": number,
            "message": f"Product added: {sku} (product ID {product_id}). It has no stock yet: when it arrives run  python distribute_new_stock.py {sku} <units>"}


# ---------- the graph: check -> propose -> PERSON approves -> save ----------
def node_propose(state: State) -> State:
    out = propose(state["form"])
    return {"form": out["form"], "problems": out["problems"], "warnings": out["warnings"], "proposal": out.get("proposal", {})}


def after_propose(state: State) -> str:
    return "rejected" if state["problems"] else "review"


def node_rejected(state: State) -> State:
    return {"result": {"saved": False, "message": " ".join(state["problems"])}}


def node_review(state: State) -> State:
    answer = interrupt({"kind": "approve_product", "form": state["form"], "proposal": state["proposal"], "warnings": state["warnings"]})
    return {"approved": answer.get("action") == "approve", "approved_by": answer.get("by", "")}


def after_review(state: State) -> str:
    return "save" if state["approved"] else "cancelled"


def node_cancelled(state: State) -> State:
    return {"result": {"saved": False, "message": "Cancelled. Nothing was saved."}}


def node_save(state: State) -> State:
    return {"result": save(state, state["approved_by"])}


builder = StateGraph(State)
builder.add_node("propose", node_propose)
builder.add_node("rejected", node_rejected)
builder.add_node("review", node_review)
builder.add_node("cancelled", node_cancelled)
builder.add_node("save", node_save)
builder.add_edge(START, "propose")
builder.add_conditional_edges("propose", after_propose, ["rejected", "review"])
builder.add_conditional_edges("review", after_review, ["save", "cancelled"])
builder.add_edge("save", END)
builder.add_edge("rejected", END)
builder.add_edge("cancelled", END)
graph = builder.compile(checkpointer=InMemorySaver())


def run_agent(form: dict, ask) -> dict:
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    result = graph.invoke({"form": form}, config)
    while "__interrupt__" in result:
        result = graph.invoke(Command(resume=ask(result["__interrupt__"][0].value)), config)
    return result["result"]


# ---------- the person at the keyboard (an example product; nothing is saved until you type ok) ----------
def ask_in_terminal(q: dict) -> dict:
    p, f = q["proposal"], q["form"]
    print(f"\nAGENT 4 PROPOSAL  (nothing is saved until you type ok)\n  SKU      : {p['sku']}\n  Product  : {f['name']}\n"
          f"  Category : {f['category']}    Vendor: {p['vendor']}{' (NEW)' if p['new_vendor'] else ''}\n"
          f"  Prices   : cost {f['cost']:g}  selling {f['selling']:g}  MRP {f['mrp']:g}  (margin {p['margin_pct']}%)")
    for w in q["warnings"]:
        print("  !!", w)
    if input("\nType ok to approve, anything else to cancel > ").strip().lower() != "ok":
        return {"action": "cancel"}
    return {"action": "approve", "by": input("Your name > ").strip()}


if __name__ == "__main__":
    opts = options()
    example = {"name": "Silicone Spatula Set", "vendor": opts["vendors"][0], "category": "Kitchen", "hsn": "39241090", "gst": 18,
               "mrp": 299, "cost": 120, "selling": 199, "sub_category": "Utensils"}
    print("Example product (you can change it in this file):", example["name"])
    print(run_agent(example, ask_in_terminal)["message"])
