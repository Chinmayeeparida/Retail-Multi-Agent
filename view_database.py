"""A small viewer so you can SEE what we have built so far.  Run:   streamlit run view_database.py
(A first taste of Streamlit, which we will use for the real screens in Step 15.)
"""
import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

DB_FILE = Path(__file__).resolve().parent / "data" / "retail.db"

st.set_page_config(page_title="Retail database viewer", layout="wide")
st.title("Retail multi-agent project: what we have built so far")
st.caption("August 2025 data. Vendor details, rack names and restock requests are SIMULATED.")

if not DB_FILE.exists():
    st.error("data/retail.db not found. Run:  python build_database.py")
    st.stop()


@st.cache_data
def query(sql: str, params: tuple = ()) -> pd.DataFrame:
    conn = sqlite3.connect(DB_FILE)
    try:
        return pd.read_sql_query(sql, conn, params=params)
    finally:
        conn.close()


tab_overview, tab_stores, tab_restock, tab_packing, tab_warehouse, tab_products = st.tabs(
    ["Overview", "Stores & sales", "Restock requests", "Barcodes & packing", "Warehouse racks", "Products"])

with tab_overview:
    counts = query("""
        SELECT 'stores' AS what, COUNT(*) AS n FROM stores UNION ALL
        SELECT 'products (active)', COUNT(*) FROM products WHERE is_active = 1 UNION ALL
        SELECT 'products (inactive)', COUNT(*) FROM products WHERE is_active = 0 UNION ALL
        SELECT 'vendors', COUNT(*) FROM vendors UNION ALL
        SELECT 'categories', COUNT(*) FROM categories UNION ALL
        SELECT 'units sold (Aug)', SUM(quantity) FROM sales UNION ALL
        SELECT 'net sales (Rs lakh)', ROUND(SUM(net_amount) / 100000.0, 1) FROM sales UNION ALL
        SELECT 'restock requests', COUNT(*) FROM restock_requests UNION ALL
        SELECT 'products with warehouse stock', COUNT(*) FROM warehouse_stock WHERE available_qty > 0""")
    cols = st.columns(4)
    for i, row in counts.iterrows():
        cols[i % 4].metric(row["what"], f"{row['n']:,.1f}" if "lakh" in row["what"] else f"{int(row['n']):,}")
    st.subheader("Data quality log (what the cleaning scripts fixed or assumed)")
    st.dataframe(query("SELECT issue, affected_count, action_taken FROM data_quality_log"), width="stretch")

with tab_stores:
    per_store = query("""
        SELECT st.store_code AS store, SUM(s.quantity) AS units_sold,
               ROUND(SUM(s.net_amount)) AS net_sales_rs
        FROM sales s JOIN stores st USING (store_id) GROUP BY st.store_code ORDER BY st.store_code""")
    st.dataframe(per_store, width="stretch")
    st.bar_chart(per_store.set_index("store")["units_sold"])
    daily = query("""SELECT sale_date, SUM(quantity) AS units FROM sales GROUP BY sale_date ORDER BY sale_date""")
    st.line_chart(daily.set_index("sale_date")["units"])

with tab_restock:
    store = st.selectbox("Store", query("SELECT store_code FROM stores ORDER BY store_code")["store_code"])
    req = query("""
        SELECT p.sku, p.product_name, r.requested_qty, COALESCE(w.available_qty, 0) AS warehouse_stock,
               CASE WHEN COALESCE(w.available_qty, 0) = 0 THEN 'WILL BE OUT OF STOCK' ELSE 'in stock' END AS check_result,
               r.status
        FROM restock_requests r
        JOIN stores s USING (store_id) JOIN products p USING (product_id)
        LEFT JOIN warehouse_stock w USING (product_id)
        WHERE s.store_code = ? ORDER BY check_result DESC, p.product_name""", (store,))
    st.write(f"{len(req)} requests, {(req['check_result'] != 'in stock').sum()} with zero warehouse stock")
    st.dataframe(req, width="stretch")

with tab_packing:
    st.subheader("Barcode requests (one per store packing list)")
    st.dataframe(query("""
        SELECT b.barcode_request_id AS id, s.store_code AS store, b.total_labels AS labels, b.status, b.requested_at, b.printed_at
        FROM barcode_requests b JOIN stores s USING (store_id) ORDER BY b.barcode_request_id"""), width="stretch")
    st.subheader("Packing lines")
    pack_store = st.selectbox("Store ", query("SELECT store_code FROM stores ORDER BY store_code")["store_code"])
    st.dataframe(query("""
        SELECT i.rack_name, p.sku, p.product_name, i.qty_to_pack, i.packed_qty, i.damaged_qty, i.pack_status, i.staff_note
        FROM packing_items i JOIN stores s USING (store_id) JOIN products p USING (product_id)
        WHERE s.store_code = ? ORDER BY i.rack_name""", (pack_store,)), width="stretch")
    st.caption("Empty tables? Run: python agent1_packing.py --all, then python simulate_warehouse.py")

with tab_warehouse:
    st.write("Rack names look like R03-B-07 = rack 3, shelf B, position 7. Best sellers sit on the lowest racks and shelf A.")
    racks = query("""
        SELECT w.rack_name, p.sku, p.product_name, c.category_name AS category, w.available_qty, w.damaged_qty
        FROM warehouse_stock w JOIN products p USING (product_id)
        LEFT JOIN categories c USING (category_id)
        WHERE w.rack_name IS NOT NULL ORDER BY w.rack_name""")
    search = st.text_input("Search a product or SKU")
    if search:
        racks = racks[racks["product_name"].str.contains(search, case=False, na=False)
                      | racks["sku"].str.contains(search, case=False, na=False)]
    st.dataframe(racks, width="stretch")

with tab_products:
    products = query("""
        SELECT p.sku, p.product_name, c.category_name AS category, v.vendor_name AS vendor, p.colour,
               p.hsn_code, p.gst_rate, pr.selling_price, p.is_active
        FROM products p LEFT JOIN categories c USING (category_id) LEFT JOIN vendors v USING (vendor_id)
        LEFT JOIN product_prices pr USING (product_id)""")
    only_active = st.checkbox("Only active products", value=True)
    if only_active:
        products = products[products["is_active"] == 1]
    st.dataframe(products, width="stretch")
