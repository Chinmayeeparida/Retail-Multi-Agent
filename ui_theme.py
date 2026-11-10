"""One shared look for all four apps (dark navy header, soft blue background, white cards, coloured accents).
Each app calls  ui_theme.apply("App name", "one line about it", accent="blue")  right after st.set_page_config."""
import streamlit as st

ACCENTS = {            # each app gets its own colour, so you always know which one you are in
    "blue":   ("#1E88E5", "#1565C0"),
    "teal":   ("#00897B", "#00695C"),
    "orange": ("#FB8C00", "#E65100"),
    "purple": ("#7E57C2", "#5E35B1"),
}

CSS = """
<style>
:root { --navy:#1E2A3A; --navy2:#2B3B52; --accent:%(a)s; --accent2:%(b)s; }
.stApp { background:#FFFFFF; }
header[data-testid="stHeader"] { background: transparent; }
.block-container { padding-top: 1.2rem; max-width: 1500px; }

/* top header bar */
.topbar { background:#FFFFFF; color:var(--navy); padding:12px 22px; border-radius:12px; margin-bottom:8px;
          border:1px solid #D5DEEB; border-left:8px solid var(--accent); box-shadow:0 2px 10px rgba(30,42,58,.08); }
.topbar .t { font-size:1.55rem; font-weight:700; letter-spacing:.2px; color:var(--navy); }
.topbar .s { font-size:.92rem; color:#5B6B80; margin-top:2px; }

/* the title bar, menu buttons and news strip are pinned: they do not move when the page scrolls */
[data-testid="stLayoutWrapper"] > [data-testid="stVerticalBlock"].st-key-pinbar, .st-key-pinbar { border-top:none !important; position:sticky; top:0; z-index:990; background:#FFFFFF !important; padding:4px 0 6px 0; margin-top:-4px;
                 border:none !important; border-radius:0 !important; box-shadow:0 6px 8px -6px rgba(30,42,58,.35) !important; }
[data-testid="stLayoutWrapper"]:has(> .st-key-pinbar), [data-testid="stElementContainer"]:has(> .st-key-pinbar) { position:sticky; top:0; z-index:990; }


/* rolling news strip */
.ticker { background:#fff; border:1px solid #D5DEEB; border-left:6px solid #F57C00; border-radius:8px; overflow:hidden;
          white-space:nowrap; padding:6px 0; margin:6px 0 2px 0; }
.ticker-track { display:inline-block; padding-left:100%%; animation:roll 55s linear infinite; }
.ticker:hover .ticker-track { animation-play-state:paused; }
@keyframes roll { 0%% { transform:translateX(0); } 100%% { transform:translateX(-100%%); } }
.tk { display:inline-block; margin-right:46px; color:#1B2A3B; font-size:.92rem; }
.tag { display:inline-block; font-size:.72rem; font-weight:700; color:#fff; padding:1px 8px; border-radius:10px; margin-right:7px; background:#546E7A; }
.tag.NEW { background:#F57C00; } .tag.PACKED { background:#1E88E5; } .tag.PENDING { background:#7E57C2; } .tag.TODAY { background:#00897B; }
.tag.SERIOUS { background:#C62828; } .tag.WARNING { background:#E65100; } .tag.NOTICE { background:#1565C0; }

/* weekly delivery plan */
.week { display:flex; gap:8px; margin:4px 0 10px 0; flex-wrap:wrap; }
.wd { flex:1 1 120px; background:#fff; border:1px solid #D5DEEB; border-radius:10px; padding:6px 10px; font-size:.85rem; }
.wd b { color:var(--navy); } .wd span { color:#4A5B70; }
.wd.today { background:linear-gradient(135deg,var(--accent),var(--accent2)); border-color:var(--accent2); }
.wd.today b, .wd.today span { color:#fff; }
.wd.tomorrow { border:2px solid var(--accent); }

.ml { margin:0; padding-left:16px; font-size:.84rem; line-height:1.35; color:#1B2A3B; } .ml li { margin:0 0 5px 0; }
.ml-plain { font-size:.84rem; line-height:1.35; color:#4A5B70; }

/* highlighted updates */
.hl { background:linear-gradient(100deg,#FFF3CD,#FFE69C); border-left:6px solid #F57C00; color:#5A3A00; padding:10px 12px;
      border-radius:8px; font-weight:650; margin:6px 0; box-shadow:0 2px 6px rgba(245,124,0,.25); }

/* headings */
h1 { color:var(--navy); font-weight:700 !important; }
h2, h3 { color:var(--navy); font-weight:650 !important; }
h2 { border-left:6px solid var(--accent); padding-left:12px !important; margin-top:.4rem; }

/* cards (the bordered containers) */
[data-testid="stLayoutWrapper"] > [data-testid="stVerticalBlock"] { background:#fff; border:1px solid #D5DEEB !important;
          border-top:5px solid var(--accent) !important; border-radius:12px !important; box-shadow:0 2px 10px rgba(30,42,58,.10); }

/* metric tiles */
div[data-testid="stMetric"] { background:linear-gradient(135deg,#fff 0%%,#EAF3FE 100%%); border:1px solid #D5DEEB;
          border-left:5px solid var(--accent); border-radius:10px; padding:10px 14px; box-shadow:0 1px 6px rgba(30,42,58,.06); }
div[data-testid="stMetricLabel"] { color:#5B6B80; }
div[data-testid="stMetricValue"] { color:var(--navy); font-weight:700; }

/* buttons: the navigation looks like a dark menu, the active one is bright */
.stButton > button, .stLinkButton > a, .stDownloadButton > button { border-radius:9px; font-weight:600;
          border:1px solid #BCD0EA; background:linear-gradient(180deg,#fff,#E9F1FC); color:var(--navy); transition:all .15s; }
.stButton > button:hover, .stLinkButton > a:hover { border-color:var(--accent); color:var(--accent2); transform:translateY(-1px);
          box-shadow:0 3px 8px rgba(30,42,58,.15); }
.stButton > button[kind="primary"], .stLinkButton > a[kind="primary"] { background:linear-gradient(135deg,var(--accent),var(--accent2));
          color:#fff; border:none; box-shadow:0 3px 10px rgba(30,42,58,.25); }
.stButton > button[kind="primary"]:hover { color:#fff; filter:brightness(1.08); }
.stButton > button:disabled { opacity:.5; }

/* radio "click a store" chips */
div[role="radiogroup"] label { background:#fff; border:1px solid #C5D2E3; border-radius:20px; padding:3px 14px; margin-right:4px; }
div[role="radiogroup"] label:has(input:checked) { background:var(--accent); border-color:var(--accent); }
div[role="radiogroup"] label:has(input:checked) * { color:#fff !important; }

/* tables */
div[data-testid="stDataFrame"], div[data-testid="stDataEditor"] { border:1px solid #D5DEEB; border-radius:10px; overflow:hidden; }
hr { border-color:#D5DEEB; }
div[data-testid="stAlert"] { border-radius:10px; }
section[data-testid="stSidebar"] { background:#FFFFFF; border-right:1px solid #D5DEEB; }
section[data-testid="stSidebar"] .stButton > button { text-align:left; justify-content:flex-start; background:#fff; border:1px solid transparent;
          box-shadow:none; font-weight:550; }
section[data-testid="stSidebar"] .stButton > button > div { justify-content:flex-start !important; text-align:left !important; background:transparent !important; }
section[data-testid="stSidebar"] .stButton > button p { text-align:left !important; }
section[data-testid="stSidebar"] .stButton > button:hover { background:#EEF4FC; border-color:#CFE0F5; transform:none; box-shadow:none; }
section[data-testid="stSidebar"] .stButton > button[kind="primary"] { background:linear-gradient(135deg,var(--accent),var(--accent2)); color:#fff; }
section[data-testid="stSidebar"] .stButton > button[kind="primary"] * { color:#fff !important; }
section[data-testid="stSidebar"] [data-testid="stVerticalBlock"] { gap:.25rem; }
.side-title { font-weight:800; font-size:1.15rem; color:var(--navy); margin:2px 0 0 4px; line-height:1.4; }
.side-sub { font-size:.78rem; color:#7A8AA0; margin:0 0 10px 4px; }
.side-h { font-size:.72rem; font-weight:700; letter-spacing:.8px; color:#8A99AD; margin:0; padding:12px 0 4px 6px; line-height:1.4; text-transform:uppercase; }

/* status tiles */
.tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; margin:6px 0 12px 0; }
.tile { background:#fff; border:1px solid #D5DEEB; border-radius:12px; padding:10px 14px; border-top:5px solid #B0BEC5; box-shadow:0 2px 8px rgba(30,42,58,.07); }
.tile .l { font-size:.78rem; font-weight:650; color:#5B6B80; text-transform:uppercase; letter-spacing:.4px; }
.tile .v { font-size:1.9rem; font-weight:800; line-height:1.15; color:var(--navy); }
.tile .u { font-size:.78rem; color:#7A8AA0; }
.tile.warn { border-top-color:#F57C00; background:linear-gradient(180deg,#FFF8EC,#fff); } .tile.warn .v { color:#E65100; }
.tile.info { border-top-color:#1E88E5; background:linear-gradient(180deg,#EDF5FE,#fff); } .tile.info .v { color:#1565C0; }
.tile.ok   { border-top-color:#2E9E5B; background:linear-gradient(180deg,#EEF9F2,#fff); } .tile.ok .v { color:#1B7F46; }
.tile.bad  { border-top-color:#C62828; background:linear-gradient(180deg,#FDECEC,#fff); } .tile.bad .v { color:#C62828; }

/* store pipeline board */
.pipe { width:100%%; border-collapse:separate; border-spacing:0 4px; font-size:.8rem; }
.pipe-wrap { overflow-x:auto; }
.pipe th { text-align:left; font-size:.72rem; text-transform:uppercase; letter-spacing:.5px; color:#7A8AA0; padding:2px 8px; font-weight:700; }
.pipe td { padding:4px 6px; background:#fff; }
.pipe td.st { white-space:nowrap; font-weight:700; color:var(--navy); padding-left:10px; border-radius:8px 0 0 8px; }
.pill { display:block; padding:3px 6px; border-radius:8px; font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.pill.done { background:#E3F5EA; color:#1B7F46; } .pill.now { background:#DDEBFB; color:#1565C0; }
.pill.wait { background:#FFE9C7; color:#B45309; } .pill.todo { background:#F1F4F8; color:#9AA8BA; }

</style>
"""


def css(accent: str = "blue") -> None:
    a, b = ACCENTS.get(accent, ACCENTS["blue"])
    st.markdown(CSS % {"a": a, "b": b}, unsafe_allow_html=True)


def header(title: str, subtitle: str = "") -> None:
    sub = f'<div class="s">{subtitle}</div>' if subtitle else ""
    st.markdown(f'<div class="topbar"><div class="t">{title}</div>{sub}</div>', unsafe_allow_html=True)


def apply(title: str, subtitle: str = "", accent: str = "blue") -> None:
    css(accent)
    header(title, subtitle)


def compact_table(df, height=None) -> None:
    """A table that keeps the numbers next to the names (not pushed to the far right): number columns are narrow,
    names and remarks are wider, and the table is only as wide as its columns."""
    import pandas as pd
    config = {}
    for col in df.columns:
        if pd.api.types.is_numeric_dtype(df[col]):
            config[col] = st.column_config.NumberColumn(width="small" if len(str(col)) <= 9 else "medium", alignment="center")
        elif any(k in str(col).lower() for k in ("item", "product", "remark", "why", "note", "stage", "requested on", "packed at")):
            config[col] = st.column_config.Column(width="large")
        else:
            config[col] = st.column_config.Column(width="medium")
    st.dataframe(df, hide_index=True, width="content", column_config=config, **({"height": height} if height else {}))


def highlight(text: str) -> None:
    import html
    st.markdown(f'<div class="hl">&#9733; {html.escape(text)}</div>', unsafe_allow_html=True)


def watch(signature_fn, seconds: int = 8) -> None:
    """Looks at the database every few seconds and reloads the page by itself when something changed
    (a new request, a finished list, a new trip), so nobody needs to press F5."""
    @st.fragment(run_every=seconds)
    def _watch():
        sig = signature_fn()
        old = st.session_state.get("_watch_sig")
        st.session_state["_watch_sig"] = sig
        if old is not None and old != sig:
            st.rerun()
    _watch()


def ticker(items) -> None:
    """A rolling news line (it pauses when the mouse is on it)."""
    import html
    if not items:
        items = [("TODAY", "No new updates.")]
    one = "".join(f'<span class="tk"><span class="tag {html.escape(t)}">{html.escape(t)}</span>{html.escape(x)}</span>' for t, x in items)
    st.markdown(f'<div class="ticker"><div class="ticker-track">{one}{one}</div></div>', unsafe_allow_html=True)


def week_plan(plan) -> None:
    import html
    boxes = ""
    for p in plan:
        cls = "wd today" if p["today"] else "wd tomorrow" if p["tomorrow"] else "wd"
        mark = " (today)" if p["today"] else " (tomorrow)" if p["tomorrow"] else ""
        boxes += f'<div class="{cls}"><b>{p["day"]}{mark}</b><br><span>{html.escape(p["text"])}</span></div>'
    st.markdown(f'<div class="week">{boxes}</div>', unsafe_allow_html=True)


def small_lines(lines, bullets: bool = True) -> None:
    import html
    if bullets:
        st.markdown('<ul class="ml">' + "".join(f"<li>{html.escape(x)}</li>" for x in lines) + "</ul>", unsafe_allow_html=True)
    else:
        st.markdown('<div class="ml-plain">' + "<br>".join(html.escape(x) for x in lines) + "</div>", unsafe_allow_html=True)


def tiles(items) -> None:
    import html
    boxes = "".join(f'<div class="tile {html.escape(t["tone"])}"><div class="l">{html.escape(str(t["label"]))}</div>'
                    f'<div class="v">{html.escape(str(t["value"]))}</div><div class="u">{html.escape(str(t["sub"]))}</div></div>' for t in items)
    st.markdown(f'<div class="tiles">{boxes}</div>', unsafe_allow_html=True)


def pipeline(rows, headers) -> None:
    import html
    head = "".join(f"<th>{html.escape(h)}</th>" for h in ["Store"] + headers)
    body = ""
    for r in rows:
        cells = "".join(f'<td><span class="pill {state}">{html.escape(text)}</span></td>' for text, state in r["cells"])
        body += f'<tr><td class="st">{html.escape(r["store"])}</td>{cells}</tr>'
    st.markdown(f'<div class="pipe-wrap"><table class="pipe"><tr>{head}</tr>{body}</table></div>', unsafe_allow_html=True)


def chip(text: str, tone: str = "info") -> None:
    import html
    colour = {"bad": "#C62828", "warn": "#F57C00", "info": "#1E88E5", "ok": "#2E9E5B"}[tone]
    st.markdown(f'<span style="background:{colour};color:#fff;padding:2px 10px;border-radius:10px;font-size:.74rem;font-weight:700;white-space:nowrap;display:inline-block">'
                f'{html.escape(text)}</span>', unsafe_allow_html=True)
