"""Step 3: clean the raw CSVs and load the catalogue into retail.db.

Loads these tables:
    categories, vendors, products, product_prices, warehouse_stock, data_quality_log

Run from the project folder (after create_db.py):   python load_catalog.py
"""
import os
import re
import sqlite3
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
RAW = ROOT / "data" / "raw"
DB_FILE = ROOT / "data" / "retail.db"
STOCK_FILE = RAW / "2025-08-28-15_11_58-Stock-Report.csv"
SALES_FILE = RAW / "Aug2025_AllStores_ProductSalesReport.csv.csv"

# The stock report's date is the start of its file name: 2025-08-28-...
SNAPSHOT_DATE = re.match(r"\d{4}-\d{2}-\d{2}", STOCK_FILE.name).group(0)

COLOUR_WORDS = [
    "red", "blue", "green", "black", "white", "pink", "yellow", "orange", "purple",
    "grey", "gray", "brown", "golden", "gold", "silver", "beige", "maroon", "navy",
    "peach", "cream", "violet", "lavender", "teal", "turquoise", "mustard", "olive", "lilac",
]
COLOUR_RE = re.compile(r"\b(" + "|".join(COLOUR_WORDS) + r")\b", re.IGNORECASE)
COLOUR_FIX = {"gray": "Grey", "golden": "Gold"}  # two spellings -> one

# SKU prefixes we choose by hand, where the data has no clear prefix for a category
PREFIX_OVERRIDES = {"Cosmetics": "CS"}

# Vendor given to products that have no vendor in the data
PLACEHOLDER_VENDOR = os.getenv("PLACEHOLDER_VENDOR", "UNASSIGNED VENDOR")   # products with no vendor get this one (set PLACEHOLDER_VENDOR in .env to use another)

quality_log = []  # every fix or flag is noted here, then saved to data_quality_log


def log_issue(issue, count, action):
    """Remember what we found and what we did about it (skip if nothing found)."""
    if int(count) > 0:
        quality_log.append((issue, int(count), action))


# ---------------------------------------------------------------- 1. read
def read_raw():
    # dtype=str keeps SKUs and HSN codes exactly as written (no lost leading zeros)
    stock = pd.read_csv(STOCK_FILE, dtype=str)
    sales = pd.read_csv(SALES_FILE, dtype=str)
    return stock, sales


# ---------------------------------------------------------------- 2. clean stock
def clean_stock(stock):
    stock = stock.copy()

    # Trim stray spaces from the text columns
    before = stock["Product Name"].copy()
    for col in ["Product Name", "SKU", "HSN", "Brand", "Category", "Sub Category"]:
        stock[col] = stock[col].str.strip()
    log_issue("Product names with stray spaces", (before != stock["Product Name"]).sum(), "Trimmed")

    # Text -> numbers
    for col in ["MRP", "MOP", "Cost Price", "GST", "Available Qty"]:
        stock[col] = pd.to_numeric(stock[col], errors="coerce")

    # Negative stock is impossible, so it is a data error
    negative = stock["Available Qty"] < 0
    log_issue("Negative stock quantity", negative.sum(), "Set to 0")
    stock.loc[negative, "Available Qty"] = 0
    stock["Available Qty"] = stock["Available Qty"].fillna(0).astype(int)

    # HSN codes are 4, 6 or 8 digits long
    odd_hsn = stock["HSN"].notna() & ~stock["HSN"].str.len().isin([4, 6, 8])
    log_issue("HSN code not 4, 6 or 8 digits", odd_hsn.sum(), "Kept as given, flagged for review")
    log_issue("Missing HSN code", stock["HSN"].isna().sum(), "Left blank")
    return stock


# ---------------------------------------------------------------- 3. clean sales
def clean_sales(sales):
    sales = sales.copy()

    names_before = sales["Vendor Name"].nunique()
    for col in ["SKU", "Product Name", "HSN", "Vendor Name", "Brand Name",
                "Category Name", "Sub-Category Name"]:
        sales[col] = sales[col].str.strip()
    for col in ["Quantity", "GST %", "Vendor Price", "MRP"]:
        sales[col] = pd.to_numeric(sales[col], errors="coerce")

    # 'ABC Traders' and 'ABC TRADERS' are the same vendor: keep the most common spelling
    named = sales.dropna(subset=["Vendor Name"]).copy()
    named["key"] = named["Vendor Name"].str.lower()
    counts = named.groupby(["key", "Vendor Name"]).size().reset_index(name="n")
    best = counts.sort_values("n", ascending=False).drop_duplicates("key").set_index("key")["Vendor Name"]
    has_vendor = sales["Vendor Name"].notna()
    sales.loc[has_vendor, "Vendor Name"] = sales.loc[has_vendor, "Vendor Name"].str.lower().map(best)
    log_issue("Vendor names written in different spellings", names_before - sales["Vendor Name"].nunique(),
              "Merged into the most common spelling")
    return sales


def vendor_for_each_sku(sales):
    """One vendor per SKU (the stock report has no vendor column, so we use the sales file)."""
    named = sales.dropna(subset=["Vendor Name"])
    per_sku = named.groupby(["SKU", "Vendor Name"]).size().reset_index(name="n")
    log_issue("SKUs sold under more than one vendor", (per_sku.groupby("SKU").size() > 1).sum(),
              "Used the vendor with the most sales rows")
    per_sku = per_sku.sort_values(["SKU", "n"], ascending=[True, False]).drop_duplicates("SKU")
    return per_sku.set_index("SKU")["Vendor Name"]


# ---------------------------------------------------------------- 4. build the product list
def find_colour(name):
    match = COLOUR_RE.search(name or "")
    if not match:
        return None
    word = match.group(1).lower()
    return COLOUR_FIX.get(word, word.title())


def build_products(stock, sales, sku_vendor):
    sold = sales.groupby("SKU")["Quantity"].sum()

    # (a) every SKU in the stock report
    from_stock = pd.DataFrame({
        "sku": stock["SKU"], "product_name": stock["Product Name"], "hsn_code": stock["HSN"],
        "brand": stock["Brand"], "category": stock["Category"], "sub_category": stock["Sub Category"],
        "gst_rate": stock["GST"], "mrp": stock["MRP"], "cost_price": stock["Cost Price"],
        "selling_price": stock["MOP"], "available_qty": stock["Available Qty"],
    })

    # (b) SKUs that were sold but are missing from the stock report
    missing = sales[~sales["SKU"].isin(stock["SKU"])]
    missing = missing.sort_values("Quantity", ascending=False).drop_duplicates("SKU")
    from_sales = pd.DataFrame({
        "sku": missing["SKU"], "product_name": missing["Product Name"], "hsn_code": missing["HSN"],
        "brand": missing["Brand Name"], "category": missing["Category Name"],
        "sub_category": missing["Sub-Category Name"], "gst_rate": missing["GST %"].abs(),
        "mrp": missing["MRP"], "cost_price": missing["Vendor Price"],
        "selling_price": None, "available_qty": 0,
    })
    log_issue("SKUs sold but missing from the stock report", len(from_sales),
              "Added to products with warehouse stock 0")

    products = pd.concat([from_stock, from_sales], ignore_index=True)
    products["sold_qty"] = products["sku"].map(sold).fillna(0)

    # Active = has stock now, or sold in August
    products["is_active"] = ~((products["available_qty"] == 0) & (products["sold_qty"] <= 0))
    log_issue("SKUs with zero stock and zero sales", (~products["is_active"]).sum(),
              "Marked inactive (kept in the database, ignored by the agents)")

    products["product_number"] = products["sku"].str.extract(r"^[A-Za-z]+(\d+)")[0].astype(float).astype("Int64")
    products["sku_prefix"] = products["sku"].str.extract(r"^([A-Za-z]+)\d")[0]

    products["colour"] = products["product_name"].map(find_colour)
    log_issue("Colour read from the product name (approximate)", products["colour"].notna().sum(),
              "Saved in colour; blank where the name has no colour word")

    products["vendor"] = products["sku"].map(sku_vendor)
    log_issue("Products with no vendor (not in the sales file or no vendor name)", products["vendor"].isna().sum(),
              f"Given the placeholder vendor '{PLACEHOLDER_VENDOR}'")
    products["vendor"] = products["vendor"].fillna(PLACEHOLDER_VENDOR)
    log_issue("Assumption: MOP column treated as selling price", len(from_stock), "Saved as selling_price")
    return products


def choose_category_prefixes(products):
    """Give a category a SKU prefix only if that prefix mostly belongs to that category."""
    counts = products.dropna(subset=["category", "sku_prefix"]).groupby(["category", "sku_prefix"]).size()
    counts = counts.reset_index(name="n")
    home = counts.sort_values("n", ascending=False).drop_duplicates("sku_prefix").set_index("sku_prefix")["category"]
    result = {}
    for category in sorted(products["category"].dropna().unique()):
        own = counts[(counts["category"] == category) & (counts["sku_prefix"].map(home) == category)]
        result[category] = own.sort_values("n", ascending=False)["sku_prefix"].iloc[0] if len(own) else None
    result.update(PREFIX_OVERRIDES)  # our own choices win over what the data suggests
    return result


# ---------------------------------------------------------------- 5. save to the database
def none_for_nan(df):
    """SQLite wants None, not NaN, for empty cells."""
    return df.astype(object).where(df.notna(), None)


def load_into_database(products, category_prefix, vendor_names):
    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        if conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] > 0:
            raise SystemExit("Products are already loaded. To start again: delete data\\retail.db, "
                             "run create_db.py, then run this script.")
        with conn:  # one transaction: everything is saved, or nothing is
            conn.executemany("INSERT INTO categories (category_name, sku_prefix) VALUES (?, ?)",
                             list(category_prefix.items()))
            category_id = dict(conn.execute("SELECT category_name, category_id FROM categories"))

            conn.executemany("INSERT INTO vendors (vendor_name) VALUES (?)", [(v,) for v in vendor_names])
            vendor_id = dict(conn.execute("SELECT vendor_name, vendor_id FROM vendors"))

            rows = none_for_nan(products).to_dict("records")
            conn.executemany(
                """INSERT INTO products (sku, product_number, product_name, vendor_id, category_id,
                                         sub_category, brand, hsn_code, gst_rate, colour, is_active)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [(r["sku"], r["product_number"], r["product_name"], vendor_id.get(r["vendor"]),
                  category_id.get(r["category"]), r["sub_category"], r["brand"], r["hsn_code"],
                  r["gst_rate"], r["colour"], int(r["is_active"])) for r in rows])
            product_id = dict(conn.execute("SELECT sku, product_id FROM products"))

            conn.executemany(
                """INSERT INTO product_prices (product_id, mrp, cost_price, selling_price, effective_from)
                   VALUES (?, ?, ?, ?, ?)""",
                [(product_id[r["sku"]], r["mrp"], r["cost_price"], r["selling_price"], SNAPSHOT_DATE)
                 for r in rows if any(r[k] is not None for k in ("mrp", "cost_price", "selling_price"))])

            conn.executemany(
                """INSERT INTO warehouse_stock (product_id, snapshot_date, rack_name, available_qty, damaged_qty)
                   VALUES (?, ?, NULL, ?, 0)""",
                [(product_id[r["sku"]], SNAPSHOT_DATE, int(r["available_qty"])) for r in rows])

            conn.executemany(
                "INSERT INTO data_quality_log (issue, affected_count, action_taken) VALUES (?, ?, ?)",
                quality_log)
    finally:
        conn.close()


def main():
    if not DB_FILE.exists():
        raise SystemExit("data\\retail.db not found. Run create_db.py first.")

    stock, sales = read_raw()
    stock = clean_stock(stock)
    sales = clean_sales(sales)
    sku_vendor = vendor_for_each_sku(sales)
    products = build_products(stock, sales, sku_vendor)
    category_prefix = choose_category_prefixes(products)
    vendor_names = sorted(set(sales["Vendor Name"].dropna().unique()) | {PLACEHOLDER_VENDOR})

    load_into_database(products, category_prefix, vendor_names)

    print(f"Loaded from the stock snapshot of {SNAPSHOT_DATE}:")
    print(f"  products        : {len(products)}  ({int(products['is_active'].sum())} active, "
          f"{int((~products['is_active']).sum())} inactive)")
    print(f"  categories      : {len(category_prefix)}")
    print(f"  vendors         : {len(vendor_names)}")
    print(f"  warehouse_stock : {len(products)} rows, {int((products['available_qty'] > 0).sum())} with stock")
    print("\nData quality log:")
    for issue, count, action in quality_log:
        print(f"  - {issue}: {count}  ->  {action}")


if __name__ == "__main__":
    main()
