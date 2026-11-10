"""Store Replenishment App - the store staff check what arrived.      Run:   streamlit run store_app.py --server.port 8502

Opens from the 360 Degree View (link carries ?trip=...&store=...). The staff type only Received and Damaged;
Match and Remark are calculated. Done saves the check and runs the same steps as before (alerts, report, e-mail).
"""
import pandas as pd
import streamlit as st

import screen_logic as logic
import ui_theme

st.set_page_config(page_title="Store Replenishment App", layout="wide")
problem = logic.db_problem()
if problem:
    st.error(problem)
    st.stop()

ui_theme.apply("Store Replenishment App", "Check what arrived at your store", accent="teal")
ui_theme.watch(logic.change_signature)          # the page reloads by itself when a new trip arrives
pending = logic.in_transit()
finished = logic.finished_checks()
if pending.empty and finished.empty:
    st.success("Nothing is waiting to be checked. A trip appears here by itself once Agent 2 creates it.")
    st.stop()

options = [f"{r.Trip} | {r.Store}" for r in pending.itertuples()] + [f"{r.Trip} | {r.Store} (checked)" for r in finished.itertuples()]
want = f"{st.query_params.get('trip', '')} | {st.query_params.get('store', '')}"
choice = st.selectbox("Trip | Store", options, index=options.index(want) if want in options else 0)
checked_view = choice.endswith("(checked)")
trip, store = [x.strip() for x in choice.replace("(checked)", "").split("|")]

if checked_view:                      # a finished check stays here, view-only, so the store can look back at it later
    row = finished[(finished["Trip"] == trip) & (finished["Store"] == store)].iloc[0]
    st.success(f"Check done for {store} ({trip}): checked by {row['checker_name']} on {logic.nice_time(row['received_at'])}. "
               f"Boxes: {int(row['boxes_received'])} of {int(row['Boxes'])}. This is a record: it cannot be changed here.")
    c1, c2 = st.columns(2)
    c1.caption(f"Items reached: {logic.nice_time(row['reached_at'])}")
    c2.caption(f"Checking started: {logic.nice_time(row['checking_at'])}")
    logic_table = logic.receipt_view(trip, store)
    st.dataframe(logic_table, hide_index=True, width="stretch")
    st.stop()

this = pending[(pending["Trip"] == trip) & (pending["Store"] == store)].iloc[0]
status = this["status"]

a, b, c = st.columns([2, 1, 1])
checker = a.text_input("Your name")
if b.button("Items reached", disabled=status != "in_transit", width="stretch"):
    logic.set_store_status(trip, store, "reached")
    st.rerun()
if c.button("Start checking", disabled=status == "checking", width="stretch"):
    logic.set_store_status(trip, store, "checking")
    st.rerun()
st.caption({"in_transit": "Status: items in transit", "reached": "Status: items reached the store",
            "checking": "Status: checking in progress"}[status])
t1, t2 = st.columns(2)
t1.caption(f"Items reached: {logic.nice_time(this['reached_at'])}")
t2.caption(f"Checking started: {logic.nice_time(this['checking_at'])}")

key = f"table_{trip}_{store}"
if key not in st.session_state:
    st.session_state[key], st.session_state[key + "_boxes"] = logic.receiving_lines(trip, store)
base: pd.DataFrame = st.session_state[key]
boxes_expected = st.session_state[key + "_boxes"]
boxes_text = st.text_input(f"Boxes arrived (expected {boxes_expected})", value=str(boxes_expected))
boxes = int(boxes_text) if boxes_text.strip().isdigit() else None
if boxes is None:
    st.error("Please type the number of boxes as a whole number, for example 4.")

if st.button("Everything arrived as packed"):
    base["Received"] = base["Packed"].astype("Int64")
    base["Damaged"] = 0
    st.session_state[key] = logic.with_match(base)
    st.session_state.pop(key + "_ed", None)
    st.rerun()

shown = logic.with_match(base)
edited = st.data_editor(
    shown.drop(columns=["pack_id"]), hide_index=True, width="stretch", key=key + "_ed",
    disabled=["SKU", "Product", "Packed", "Match", "Remark"],
    column_config={"Received": st.column_config.NumberColumn("Received", min_value=0, step=1, alignment="center"),
                   "Damaged": st.column_config.NumberColumn("Damaged", min_value=0, step=1, alignment="center")})
new = base.copy()
new["Received"] = edited["Received"].astype("Int64").values
new["Damaged"] = edited["Damaged"].fillna(0).astype(int).values
if not new[["Received", "Damaged"]].equals(base[["Received", "Damaged"]]):
    st.session_state[key] = logic.with_match(new)
    st.session_state.pop(key + "_ed", None)
    st.rerun()

left = int(base["Received"].isna().sum())
if left:
    st.info(f"{left} line(s) still to check.")
if st.button("Done", type="primary", disabled=left > 0 or not checker.strip() or boxes is None):
    try:
        result = logic.submit_receipt(trip, store, checker, boxes, base)
        st.session_state.pop(key, None)
        st.session_state["last_check"] = (trip, store, result)
        st.success(f"Check saved for {store} ({trip}). It stays in this list as '(checked)', so you can look at it later.")
        st.code(result["report"])
        if result["level"]:
            st.error("Warning sent to the warehouse: please be more careful while packing.")
        st.info(result["mail"])
    except ValueError as e:
        st.error(str(e))
