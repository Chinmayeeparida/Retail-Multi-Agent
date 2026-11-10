"""The 360 Degree View (the dashboard).      Run:   streamlit run app.py

          [ Product Details ]  [ Inventory View ]  [ Sales Details ]
                      360 DEGREE VIEW (the dashboard in the middle)
 [ Barcode Request App ] [ Agent 1 Packing List ] [ Warehouse Packaging Tracker ] [ Store Receiving Tracker ]

The two working apps open in their own browser tab:  store_app.py (port 8502) and packing_app.py (port 8503).
(August 2025 data, shown with shifted dates; vendor details, rack names, requests and staff checks are SIMULATED.)
"""
import datetime
import html
import os
from urllib.parse import quote

import streamlit as st

import screen_logic as logic
import ui_theme

STORE_APP_URL = os.getenv("STORE_APP_URL", "http://localhost:8502")
PACKING_APP_URL = os.getenv("PACKING_APP_URL", "http://localhost:8503")
STOCK_REQUEST_APP_URL = os.getenv("STOCK_REQUEST_APP_URL", "http://localhost:8504")

st.set_page_config(page_title="360 Degree View", layout="wide")

problem = logic.db_problem()
if problem:
    st.error(problem)
    st.stop()

NEWP = "New Product (Agent 4)"
TOP = ["Product Details", "Inventory View", "Sales Details"]
BOTTOM = ["Store Stock Request Details", "Agent 1 Packing List", "Barcode Request App", "Warehouse Packaging Tracker", "Store Receiving Tracker", NEWP]
HOME = "360 Degree View"
st.session_state.setdefault("page", HOME)
SHIFT_NOTE = ("Demo data: the sales are from 2 to 31 Aug 2025, shown here as the 30 days ending yesterday. "
              "Stores are named Store A to Store H; vendor, rack and staff details are simulated.")


def go(page: str) -> None:
    st.session_state["page"] = page


def sidebar_menu() -> None:
    """The left menu (grouped, like a control room): the active page is filled with colour."""
    with st.sidebar:
        st.markdown('<div class="side-title">Retail Operations</div><div class="side-sub">Multi-agent control room</div>', unsafe_allow_html=True)

        def item(name: str, label: str | None = None) -> None:
            st.button(label or name, key=f"nav_{name}", on_click=go, args=(name,), width="stretch",
                      type="primary" if st.session_state["page"] == name else "secondary")

        st.caption("OVERVIEW")
        item(HOME)
        st.caption("INSIGHTS")
        for name in TOP:
            item(name)
        st.caption("WORKFLOW")
        for name in BOTTOM:
            item(name)
        st.caption("APPS (OPEN IN A NEW TAB)")
        st.link_button("Store Stock Request App", STOCK_REQUEST_APP_URL, width="stretch")
        st.link_button("Warehouse Packaging App", PACKING_APP_URL, width="stretch")
        st.link_button("Store Replenishment App", STORE_APP_URL, width="stretch")


def pick_row(table, key: str, column: str = "Store") -> str | None:
    """Shows the table, and a row of store names to click; returns the store that was clicked."""
    ui_theme.compact_table(table)
    return st.radio("Click a store to see its items", list(table[column]), index=None, horizontal=True, key=key)


def banner(level: str, text: str) -> None:
    """A strong banner, so the eyes go to it first: red for SERIOUS, orange for WARNING, blue for a notice."""
    colour = {"SERIOUS": "#c62828", "WARNING": "#e65100"}.get(level, "#1565c0")
    icon = "&#9888;" if level in ("SERIOUS", "WARNING") else "&#8505;"
    st.markdown(f'<div style="background:{colour};color:#fff;padding:14px 18px;border-radius:8px;font-weight:700;'
                f'font-size:1.05rem;margin:4px 0 10px 0">{icon} {"" if text.upper().startswith(level) else html.escape(level) + ": "}{html.escape(text)}</div>',
                unsafe_allow_html=True)


page = st.session_state["page"]
ui_theme.css("blue")
ui_theme.watch(logic.change_signature)
sidebar_menu()
alerts = logic.open_alerts()
with st.container(key="pinbar"):                      # title bar + news strip: pinned, they never scroll away
    ui_theme.header("360 Degree View" if page == HOME else page,
                    "Retail operations across 8 stores: requests, packing, trips and receiving in one place" if page == HOME else "360 Degree View")
# ============================ 360 DEGREE VIEW ============================
if page == HOME:
    head, picker = st.columns([4, 1])
    head.markdown("#### Operations overview")
    day = picker.date_input("Business date", datetime.date.fromisoformat(logic.default_date())).isoformat()
    tiles = logic.status_tiles(day)
    ui_theme.tiles(tiles["workflow"])
    ui_theme.tiles(tiles["sales"])
    acts, board = st.columns([1.5, 1.7])
    with acts.container(border=True):
        todo = logic.action_items(day)
        st.markdown(f"**Action centre** ({len(todo)} to do)")
        if not todo:
            st.success("All clear: nothing needs action right now.")
        for i, it in enumerate(todo[:7]):
            c1, c2, c3 = st.columns([1.6, 3.2, 0.9], vertical_alignment="center")
            with c1:
                ui_theme.chip(it["who"], it["tone"])
            c2.markdown(f'<div style="font-size:.84rem;line-height:1.3">{html.escape(it["text"])}</div>', unsafe_allow_html=True)
            c3.button("Open", key=f"act_{i}", on_click=go, args=(it["page"],), width="stretch")
        if len(todo) > 7:
            st.caption(f"+ {len(todo) - 7} more")
        if alerts:                                    # the alert itself is a line above (Team lead) and a tile; this clears it once handled
            st.button("Mark all alerts as read", on_click=logic.acknowledge_alerts, key="ack_alerts")
    with board.container(border=True):
        st.markdown("**Store pipeline** (where each store's work is now)")
        ui_theme.pipeline(logic.pipeline_board(), ["Request", "Agent 1", "Labels", "Packing", "Trip", "Receiving"])
    ui_theme.week_plan(logic.schedule_plan(day))

    left, mid, right, far = st.columns([1.0, 1.6, 1.15, 1.25])

    with left.container(border=True):
        act = logic.activities(day)
        st.markdown(f"**Today's activities**  \n{act['today_title']}")
        with st.expander(f"New requests ({len(act['new_requests'])})", expanded=bool(act["new_requests"])):
            if act["new_requests"]:
                ui_theme.small_lines(act["new_requests"])
                st.caption("Waiting for Agent 1 to prepare the packing list.")
            else:
                ui_theme.small_lines(["No new request is waiting."], bullets=False)
        with st.expander(f"Ready to dispatch ({len(act['ready'])})", expanded=bool(act["ready"])):
            ui_theme.small_lines(act["ready"] or ["Nothing is packed and waiting."], bullets=bool(act["ready"]))
        with st.expander(f"Today's work ({len(act['today'])})"):
            ui_theme.small_lines(act["today"] or ["No packing or trip today."], bullets=bool(act["today"]))
        with st.expander(f"Pending work ({len(act['pending'])})"):
            ui_theme.small_lines(act["pending"] or ["Nothing pending."], bullets=bool(act["pending"]))
        with st.expander(f"Yesterday ({len(act['yesterday'])})"):
            st.caption(act["yesterday_title"])
            ui_theme.small_lines(act["yesterday"] or ["Nothing recorded yesterday."], bullets=bool(act["yesterday"]))

    with right.container(border=True):
        st.markdown("**Top Stores** (based on 30 days data)")
        st.dataframe(logic.top_stores(), hide_index=True, width="stretch")

    with mid.container(border=True):
        st.markdown("**Today's Replenishment**")
        trips = logic.trips_on(day)
        if trips.empty:
            st.write("No trip today.")
        else:
            trip = trips["Trip"].iloc[0]
            store = st.radio("Store", list(trips["Store"]), horizontal=True, label_visibility="collapsed")
            items, tot = logic.store_boxes(trip, store)
            st.caption(f"Trip {trip}")
            st.dataframe(items, hide_index=True, width="stretch", height=260)
            a, b, c = st.columns(3)
            a.metric("Total packed", tot["packed"])
            b.metric("Boxes", tot["boxes"])
            c.metric("Received (good)", "-" if tot["actual"] is None else tot["actual"],
                     None if tot["success_pct"] is None else f"{tot['success_pct']}%")

    with far.container(border=True):
        st.markdown("**Best Sellers This Week**")
        n = st.radio("Show", [10, 20, 50], horizontal=True, format_func=lambda v: f"Top {v}", label_visibility="collapsed")
        until, best = logic.best_sellers(n)
        st.caption(f"Last 7 days up to {logic.nice(until)}. Available = units in the warehouse now.")
        st.dataframe(best, hide_index=True, width="stretch", height=300)
    st.caption(SHIFT_NOTE)

# ============================ TOP ROW ============================
elif page == "Product Details":
    st.header("Product Details")
    c1, c2, c3 = st.columns([2, 1, 1])
    search = c1.text_input("Search by name or SKU")
    cat = c2.selectbox("Category", logic.categories())
    active = c3.checkbox("Active products only", value=True)
    t = logic.product_details(search, cat, active)
    st.caption(f"{len(t):,} products. '-' means the price is not available.")
    st.dataframe(t, hide_index=True, width="stretch", height=520)

elif page == "Inventory View":
    st.header("Inventory View")
    c1, c2, c3 = st.columns([2, 1, 1])
    search = c1.text_input("Search by name or SKU")
    cat = c2.selectbox("Category", logic.categories())
    low = c3.checkbox("Low stock only (under 10)")
    t = logic.inventory(search, cat, low)
    m1, m2, m3 = st.columns(3)
    m1.metric("Products in stock", f"{int((t['Available'] > 0).sum()):,}")
    m2.metric("WH Inventory Valuation", f"Rs {t['Value'].sum():,.0f}",
              help="Available quantity x cost price, added up. (This is the value of the stock at cost.)")
    m3.metric("Units available", f"{int(t['Available'].sum()):,}")
    st.caption("Biggest quantity first. 'Damaged (vendor)' = units that came damaged from the vendor (can be returned for a refund).")
    st.dataframe(t, hide_index=True, width="stretch", height=480)

elif page == "Sales Details":
    st.header(f"Sales Details - {logic.nice(logic.today())}")
    st.caption(SHIFT_NOTE)

    cmp_ = logic.month_comparison()
    st.subheader("This month against last month")
    if cmp_ is None:
        st.info("Not enough data for a month comparison yet (the month has just started, or the last month file is missing).")
    else:
        tot = cmp_["total"]
        st.caption(f"{cmp_['month']} so far ({cmp_['days']} days) against {cmp_['prev_month']}. "
                   "Last month comes from the previous month's bill report (net total); stores without last month data are left out of 'All stores'.")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric(f"{cmp_['month']} so far", f"Rs {tot['cur']:,.0f}")
        m2.metric(f"{cmp_['prev_month']} (full month)", f"Rs {tot['prev']:,.0f}")
        m3.metric("Done of last month", f"{tot['pct_of_prev']:.0f}%")
        m4.metric(f"vs same {cmp_['days']} days last month", f"{tot['vs_same']:+.1f}%")
        st.dataframe(cmp_["table"], hide_index=True, width="stretch")

    st.subheader("By store (last 30 days)")
    stores = logic.top_stores()
    c, d = st.columns([1, 1])
    c.bar_chart(stores.set_index("Store")["Units sold"])
    d.dataframe(stores, hide_index=True, width="stretch")
    st.subheader("By category (last 30 days)")
    cat = logic.sales_by_category()
    a, b = st.columns([1, 1])
    a.dataframe(cat, hide_index=True, width="stretch")
    b.bar_chart(cat.set_index("Category")["Units sold"])
    st.subheader("By day (last 7 days)")
    day_t = logic.sales_by_day()
    import altair as alt
    st.altair_chart(alt.Chart(day_t).mark_bar().encode(
        x=alt.X("Day:N", sort=list(day_t["Day"]), title=None), y=alt.Y("Units sold:Q"),
        tooltip=["Day", "Units sold", "Sales (Rs)"]), width="stretch")

# ============================ BOTTOM ROW ============================
elif page == "Store Stock Request Details":
    st.header("Store Stock Request Details")
    st.caption("What the stores asked for. The stores raise requests in the Store Stock Request App.")
    view = st.radio("View", ["Storewise", "Productwise"], horizontal=True, label_visibility="collapsed")
    if view == "Storewise":
        store = pick_row(logic.requests_by_store(), "sr_pick")
        if store:
            st.subheader(f"{store}: requested items")
            ui_theme.compact_table(logic.requests_store_items(store), height=420)
        else:
            st.caption("Click a store to see all its requested items.")
    else:
        st.dataframe(logic.requests_by_product(), hide_index=True, width="stretch", height=480)

elif page == "Barcode Request App":
    st.header("Barcode Request App")
    st.caption("Agent 1's approved list goes here first. Labels are printed (one label per unit to pack), then the list goes to packing.")
    reqs = logic.barcode_requests()
    if reqs.empty:
        st.info("No barcode requests yet. Run Agent 1 first.")
    else:
        shown = reqs.drop(columns=["id", "Request"])
        shown.insert(0, "Request", reqs["Request"])
        shown = shown.drop(columns=["Store"])
        store = pick_row(shown, "bc_pick", column="Request")
        if store:
            rid = int(reqs[reqs["Request"] == store]["id"].iloc[0])
            status = reqs[reqs["Request"] == store]["Status"].iloc[0]
            st.subheader(f"{store}: labels to print")
            ui_theme.compact_table(logic.barcode_lines(rid))
            if status == "requested":
                st.button("Labels printed", on_click=logic.mark_printed, args=(rid,), type="primary")
            else:
                st.success("Labels printed. This list is now in the Warehouse Packaging App.")
        else:
            st.caption("Click a store to see its items.")

elif page == "Agent 1 Packing List":
    st.header("Agent 1 - Packaging List Preparation")
    st.caption("Agent 1 looks at what the store asked for, its sales, what the store already has and what the warehouse has, then proposes "
               "a packing quantity. Staff can change it (with a reason). Done sends the list to the Barcode Request App and the Packing App. "
               "Store stock and requests are simulated.")
    st.dataframe(logic.agent1_overview(), hide_index=True, width="stretch")
    if "a1_state" not in st.session_state:
        st.session_state["a1_state"] = logic.a1_propose()
    a1 = st.session_state["a1_state"]
    waiting = sorted({d["store_code"] for d in a1["decisions"]})
    if not waiting:
        st.success("Every store's packing list has been prepared and sent. New requests will appear here.")
    else:
        store = st.selectbox("Store name", waiting)
        mine = [d for d in a1["decisions"] if d["store_code"] == store]
        st.caption(f"{len(mine)} items requested. Store tier: {mine[0]['tier']}. "
                   "Change Packaging qty if needed and write the reason in Remark.")
        base = logic.a1_table(a1, store)
        edited = st.data_editor(
            base.drop(columns=["request_id"]), hide_index=True, width="stretch", height=420, key=f"a1_{store}",
            disabled=["SKU", "Product Name", "Selling price", "Sales data (30 days)", "Inventory qty", "Store qty", "Request qty"],
            column_config={"Packaging qty": st.column_config.NumberColumn("Packaging qty", min_value=0, step=1, alignment="center"),
                           "Remark": st.column_config.TextColumn("Remark", width="large")})
        if st.button("Done", type="primary", key=f"a1_done_{store}"):
            ok, msg = logic.a1_done(a1, store, base.drop(columns=[]), edited.assign(request_id=base["request_id"].values)[base.columns])
            if ok:
                st.session_state.pop("a1_state", None)
                st.session_state["a1_msg"] = f"{store}: packing list sent to the Barcode Request App and the Packing App. {msg}"
                st.rerun()
            else:
                st.error(msg)
    if "a1_msg" in st.session_state:
        st.success(st.session_state.pop("a1_msg"))

elif page == "Warehouse Packaging Tracker":
    st.header("Warehouse Packaging Tracker")
    st.link_button("Open the Warehouse Packaging App", PACKING_APP_URL)
    prog = logic.packing_progress()
    if prog.empty:
        st.info("Nothing waiting for a trip. Packing lists appear here after Agent 1.")
    else:
        if (prog["Stage"] != "Packing in process").any():
            c1, c2, _ = st.columns([1, 1.4, 3])
            c1.date_input("Trip date", datetime.date.today(), key="trip_day",
                          help="Each store goes on its scheduled weekday (see 'Trip day' in the table)")
            c2.markdown("<div style='height:1.9rem'></div>", unsafe_allow_html=True)
            c2.button("Generate the trip ID now (Agent 2)", on_click=logic.generate_trip, type="primary")
        if "trip_msg" in st.session_state:
            st.success(st.session_state["trip_msg"])
        store = pick_row(prog, "pk_pick")
        if store:
            st.subheader(f"{store}: items")
            ui_theme.compact_table(logic.packing_store_items(store))
        else:
            st.caption("Click a store to see its items.")

elif page == "Store Receiving Tracker":
    st.header("Store Receiving Tracker")
    st.link_button("Open the Store Replenishment App", STORE_APP_URL)
    codes = logic.trip_codes()
    if not codes:
        st.info("No trips yet. Run Agent 2.")
    else:
        trip = st.selectbox("Trip ID", codes, help="Type to search")
        for al in logic.trip_alerts(trip):                          # strong banners first, so the eyes go to them
            banner(al["level"], al["message"])
        st.subheader(f"Trip ID: {trip}")
        t = logic.trip_stores_table(trip)
        shown = t.drop(columns=["status"])
        store = pick_row(shown, "rc_pick")
        if store:
            status = t[t["Store"] == store]["status"].iloc[0]
            st.subheader(f"{store}: items")
            ui_theme.compact_table(logic.trip_store_items(trip, store))
            if status != "received":
                st.link_button("Open the Store Replenishment App for this store",
                               f"{STORE_APP_URL}/?trip={quote(trip)}&store={quote(store)}")
        else:
            st.caption("Click a store to see its items (packed, received, match, damaged, status).")

elif page == NEWP:
    st.header("Agent 4 - New Product")
    st.caption("A vendor brought a product that is not in the catalogue. Fill in the details; Agent 4 checks them and proposes the SKU and "
               "the product ID. A person approves, then the product, its price and an empty warehouse slot are saved. "
               "When the stock arrives, share it with  python distribute_new_stock.py <SKU> <units>.")
    if "np_msg" in st.session_state:
        st.success(st.session_state.pop("np_msg"))
    opt = logic.a4_options()
    with st.form("np_form"):
        c1, c2 = st.columns(2)
        name = c1.text_input("Product name")
        category = c2.selectbox("Category", [c["category_name"] for c in opt["categories"]], index=None, placeholder="Choose a category")
        vendor = c1.selectbox("Vendor (existing)", opt["vendors"], index=None, placeholder="Choose, or type a new vendor on the right")
        new_vendor = c2.text_input("New vendor (only if not in the list)")
        india = c2.checkbox("New vendor is from India", value=True)
        sub_category = c1.text_input("Sub category (optional)")
        colour = c2.text_input("Colour (optional)")
        hsn = c1.text_input("HSN code (4, 6 or 8 digits)")
        gst = c2.selectbox("GST rate %", opt["gst"], index=None, placeholder="Choose")
        p1, p2, p3 = st.columns(3)
        cost = p1.number_input("Cost price", min_value=0.0, step=1.0)
        selling = p2.number_input("Selling price", min_value=0.0, step=1.0)
        mrp = p3.number_input("MRP", min_value=0.0, step=1.0)
        variant = st.text_input("Variant of an existing SKU (optional: another colour or size of that product)")
        checked = st.form_submit_button("Check the details", type="primary")
    if name.strip():
        hint = logic.a4_suggest(name)
        if hint and not category:
            st.caption(f"Hint: products with similar names are in the category '{hint}'.")
    if checked:
        st.session_state["np_out"] = logic.a4_propose({"name": name, "vendor": vendor, "new_vendor": new_vendor, "new_vendor_india": india,
                                                      "category": category, "sub_category": sub_category, "colour": colour, "hsn": hsn,
                                                      "gst": gst, "mrp": mrp, "cost": cost, "selling": selling, "variant_of": variant})
    out = st.session_state.get("np_out")
    if out:
        for problem in out["problems"]:
            st.error(problem)
        if not out["problems"]:
            p = out["proposal"]
            st.subheader("Agent 4 proposal")
            st.write(f"**SKU:** {p['sku']}   |   **Product number:** {p['product_number']}" + (" (same as the product it is a variant of)" if p["reuses_number"] else "")
                     + f"   |   **Vendor:** {p['vendor']}" + (" (new)" if p["new_vendor"] else "") + f"   |   **Margin:** {p['margin_pct']}%")
            st.caption("The permanent product ID is given when it is saved.")
            for w in out["warnings"]:
                st.warning(w)
            who = st.text_input("Your name (a person must approve)", key="np_who")
            a, b = st.columns(2)
            if a.button("Approve and add the product", type="primary"):
                res = logic.a4_save(out, who)
                if res["saved"]:
                    st.session_state.pop("np_out", None)
                    st.session_state["np_msg"] = res["message"]
                    st.rerun()
                else:
                    st.error(res["message"])
            if b.button("Cancel"):
                st.session_state.pop("np_out", None)
                st.rerun()
