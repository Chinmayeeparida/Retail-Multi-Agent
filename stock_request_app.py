"""Store Stock Request App - for the staff of ONE store. They see the warehouse quantity and raise a request.
Run:   streamlit run stock_request_app.py --server.port 8504
Each store opens its own link:   http://localhost:8504/?store=Store%20A     (without ?store=, the store is chosen from a list)

How it works:  1. search and pick an item  2. type the Request qty (not more than the warehouse qty) and click Add
               3. the item goes into 'Your request list' below  4. at the end: Raise the request, or Still some changes.
"""
import pandas as pd
import streamlit as st

import screen_logic as logic
import ui_theme

st.set_page_config(page_title="Store Stock Request App", layout="wide")
problem = logic.db_problem()
if problem:
    st.error(problem)
    st.stop()

stores = list(logic.df("SELECT store_code FROM stores WHERE is_active = 1 ORDER BY store_code")["store_code"])
store = st.query_params.get("store", "")
ui_theme.apply("Store Stock Request App", "See the warehouse quantity and raise your store's request", accent="purple")
c1, c2 = st.columns(2)
if store in stores:
    c1.markdown(f"**Store name**  \n### {store}")
else:       # no store link: choose from the list (later each store gets its own link)
    store = c1.selectbox("Store name", stores)
staff = c2.text_input("Store staff (who is raising the request)")

st.session_state.setdefault("req_list", {})        # product_id -> row
st.session_state.setdefault("req_done", None)
mine: dict = st.session_state["req_list"]

# ---------- 1. find the item ----------
st.subheader("1. Find the item")
search = st.text_input("Search by name or SKU", placeholder="e.g. hand mask")
catalog, only_os = logic.request_catalog(search)
if only_os:
    st.warning("OS (out of stock): this item has 0 in the warehouse, so it cannot be requested now.")
elif search.strip() and catalog.empty:
    st.info("No item found with that name or SKU.")
elif not catalog.empty:
    labels = [f"{r['SKU']}  |  {r['Product Name']}  |  Rs {r['Selling price']}  |  warehouse: {int(r['Inventory qty'])}"
              for _, r in catalog.iterrows()]
    pick = st.selectbox(f"Select the item ({len(catalog)} with warehouse stock{', best stocked first' if not search.strip() else ''})", labels)
    item = catalog.iloc[labels.index(pick)]
    inv = int(item["Inventory qty"])
    already = logic.already_requested_today(store, int(item["product_id"]))
    if already:
        st.warning("This store has already requested this item today.")
    a, b, c = st.columns([1, 2, 1])
    qty = a.number_input(f"Request qty (max {inv})", min_value=1, max_value=inv, value=1, step=1)
    note = b.text_input("Note (big order? write it here)")
    c.write("")
    c.write("")
    if c.button("Add", type="primary", disabled=bool(already)):
        mine[int(item["product_id"])] = {"product_id": int(item["product_id"]), "SKU": item["SKU"],
                                         "Product Name": item["Product Name"], "Selling price": item["Selling price"],
                                         "Inventory qty": inv, "Request qty": int(qty), "Note": note.strip()}
        st.session_state["req_done"] = None
        st.rerun()

# ---------- 2. the list ----------
st.subheader("2. Your request list")
if st.session_state["req_done"]:
    st.success(st.session_state["req_done"])
if not mine:
    st.caption("Nothing added yet. Pick an item above and click Add.")
else:
    table = pd.DataFrame(mine.values())
    st.dataframe(table.drop(columns=["product_id"]), hide_index=True, width="stretch")
    st.caption(f"{len(table)} items, {int(table['Request qty'].sum())} units.")
    remove = st.multiselect("Remove items from the list", list(mine), format_func=lambda pid: f"{mine[pid]['SKU']} | {mine[pid]['Product Name']}")
    if remove and st.button("Remove selected"):
        for pid in remove:
            mine.pop(pid, None)
        st.rerun()

    st.markdown("**Do you want to raise the request, or are there still some changes?**")
    x, y, _ = st.columns([1, 1, 3])
    if x.button("Raise the request", type="primary"):
        ok, msg = logic.raise_requests(store, staff, table)
        if ok:
            mine.clear()
            st.session_state["req_done"] = msg
            st.rerun()
        else:
            st.error(msg)
    if y.button("Still some changes"):
        st.info("OK. Add more items above, or remove items from the list, then raise the request.")

# ---------- 3. earlier requests ----------
st.subheader(f"{store}: requests and their status")
st.dataframe(logic.store_request_status(store), hide_index=True, width="stretch")
