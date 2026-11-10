"""Warehouse Packaging App - the warehouse staff pack the list.      Run:   streamlit run packing_app.py --server.port 8503

The list for a store appears only after Agent 1 made it and the labels were requested. Staff type only Packed
(and Damaged while packing, if any); Status is calculated. Done saves and lets Agent 2 plan the trip.
"""
import os

import pandas as pd
import streamlit as st

import screen_logic as logic
import ui_theme

st.set_page_config(page_title="Warehouse Packaging App", layout="wide")
problem = logic.db_problem()
if problem:
    st.error(problem)
    st.stop()

ui_theme.apply("Warehouse Packaging App", "Pack the approved list, store by store", accent="orange")
ui_theme.watch(logic.change_signature)          # the page reloads by itself when a new list arrives
ready = logic.lists_ready_to_pack()
done = logic.packing_done_lists()
if ready.empty and done.empty:
    st.info("No packing list yet. It appears here after Agent 1 prepares and approves it.")
    st.stop()

choices = {f"{r.Store}": ("todo", r) for r in ready.itertuples()}
choices.update({f"{r.Store} (done)": ("done", r) for r in done.itertuples()})
label = st.selectbox("Store", list(choices), key="pack_store_pick")
mode, row = choices[label]
store = label.replace(" (done)", "")
is_done = mode == "done"
if st.session_state.get("pack_msg_store") == store and "pack_msg" in st.session_state:
    st.success(st.session_state["pack_msg"])          # stays on the screen until another store is opened
if is_done:
    st.success(f"Done: {int(row.Items)} items, {int(row._3)} units packed by {row._5} on {logic.nice_time(row._4)}. "
               "You can still correct a number and press Update, until the trip is created.")
else:
    st.caption(f"{int(row.Items)} items, {int(row._3)} units to pack, in rack order. Labels: {row.Labels}.")
staff = st.text_input("Your name")

key = f"pack_{store}_{mode}"
if key not in st.session_state:
    st.session_state[key] = logic.packing_lines(store, done=is_done)
base: pd.DataFrame = st.session_state[key]
shown = logic.packing_with_status(base)
edited = st.data_editor(
    shown.drop(columns=["pack_id"]), hide_index=True, width="stretch", key=key + "_ed",
    disabled=["Rack", "SKU", "Product", "To pack", "Status"],
    column_config={"Packed": st.column_config.NumberColumn("Packed", min_value=0, step=1, alignment="center"),
                   "Damaged": st.column_config.NumberColumn("Damaged while packing", min_value=0, step=1, alignment="center",
                                                            help="Units damaged by us while packing")})
new = base.copy()
new["Packed"] = edited["Packed"].astype("Int64").values
new["Damaged"] = edited["Damaged"].fillna(0).astype(int).values
if not new[["Packed", "Damaged"]].equals(base[["Packed", "Damaged"]]):
    st.session_state[key] = new
    st.session_state.pop(key + "_ed", None)
    st.rerun()

left = int(base["Packed"].isna().sum())
if left:
    st.info(f"{left} line(s) still to pack.")
labels_ok = is_done or row.Labels == "printed"
if not labels_ok:
    st.warning(f"The labels of {store} are not marked as printed yet. Open the 360 Degree View > Barcode Request App, "
               f"click {store} and press 'Labels printed'. This page updates by itself.")
    st.link_button("Open the 360 Degree View", os.getenv("APP_URL", "http://localhost:8501"))
elif not is_done:
    st.caption("Nothing is saved until you press Done. The numbers you type stay on this page only.")
if labels_ok and not staff.strip():
    st.caption("Type your name above to enable " + ("Update." if is_done else "Done."))
if st.button("Update" if is_done else "Done", type="primary", disabled=left > 0 or not staff.strip() or not labels_ok):
    ok, msg = logic.save_packing(staff, base, update=is_done)
    if ok:
        st.session_state.pop(key, None)
        st.session_state.pop(key.replace("_done", "_todo"), None)
        st.session_state["pack_msg"] = msg
        st.session_state["pack_msg_store"] = store
        if not is_done:
            st.session_state["pack_store_pick"] = f"{store} (done)"      # the finished list stays on the screen
        st.rerun()
    else:
        st.error(msg)
