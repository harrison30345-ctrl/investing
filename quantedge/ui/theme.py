"""
QuantEdge design tokens and global stylesheet.

One source for colour, type, spacing and radius. Pages never hard-code
hex values; they import from here.

Principles
  * Dark navy/charcoal surfaces; borders do the grouping, not shadows.
  * Teal is the single accent: active nav, focus, primary actions, series 1.
  * Green/red only for direction (price change, P&L). Amber only for
    warnings and the DEMO label. Never decorative.
  * Numbers are monospaced/tabular so columns scan cleanly.
  * Density over drama: small headings, 13px body, tight rhythm (4px grid).
"""
from __future__ import annotations

# ── Colour tokens ────────────────────────────────────────────────────────────
BG = "#0a0f15"           # app background
SURFACE = "#0f151d"      # panels
SURFACE_2 = "#141c26"    # raised / hover / table header
BORDER = "#1e2834"
BORDER_STRONG = "#2a3644"
TEXT = "#e3e9f0"
TEXT_2 = "#a3afbd"       # secondary
MUTED = "#6c7a8a"        # labels, captions
ACCENT = "#2bc4c4"       # UI accent (text, focus)
ACCENT_DIM = "#143a3d"
POS = "#2fb67c"
NEG = "#e5534b"
WARN = "#d9a441"
INFO = "#5aa9e6"

# Chart series (validated for the dark surface: lightness band, CVD ΔE ≥ 13.8)
SERIES_1 = "#1fa8a8"     # strategy / primary
SERIES_2 = "#b08840"     # benchmark
GRID = "#18212c"

FONT_SANS = "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"
FONT_MONO = "'JetBrains Mono', 'SF Mono', Menlo, Consolas, monospace"

# Streamlit's own theme options (passed on the command line by `cli dashboard`)
STREAMLIT_THEME = {
    "theme.base": "dark",
    "theme.primaryColor": ACCENT,
    "theme.backgroundColor": BG,
    "theme.secondaryBackgroundColor": SURFACE_2,
    "theme.textColor": TEXT,
    "theme.borderColor": BORDER,
    "theme.baseRadius": "small",
    "theme.font": "sans-serif",
    "theme.showWidgetBorder": "true",
    "client.toolbarMode": "minimal",
}

CSS = f"""
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root {{
  --qe-bg:{BG}; --qe-surface:{SURFACE}; --qe-surface-2:{SURFACE_2}; --qe-border:{BORDER};
  --qe-border-strong:{BORDER_STRONG}; --qe-text:{TEXT}; --qe-text-2:{TEXT_2}; --qe-muted:{MUTED};
  --qe-accent:{ACCENT}; --qe-accent-dim:{ACCENT_DIM}; --qe-pos:{POS}; --qe-neg:{NEG}; --qe-warn:{WARN};
  --qe-info:{INFO}; --qe-mono:{FONT_MONO};
}}
html, body, .stApp, button, input, textarea, select {{ font-family:{FONT_SANS}; }}
[data-testid="stIconMaterial"] {{ font-family:"Material Symbols Rounded" !important; }}
.stApp {{ background:var(--qe-bg); color:var(--qe-text); }}
.block-container {{ padding:0.9rem 1.4rem 3rem 1.4rem !important; max-width:1680px; }}
[data-testid="stHeader"] {{ background:transparent; height:2.2rem; }}
[data-testid="stDecoration"], .stDeployButton, [data-testid="stAppDeployButton"], #MainMenu, footer {{ display:none !important; }}
[data-testid="stAppViewContainer"] p, .stMarkdown p {{ font-size:13px; line-height:1.5; }}
h1,h2,h3,h4 {{ letter-spacing:-0.01em; }}
[data-testid="stVerticalBlock"] {{ gap:0.6rem; }}

/* Sidebar */
[data-testid="stSidebar"] {{ background:#080c11; border-right:1px solid var(--qe-border); }}
[data-testid="stSidebar"] [data-testid="stSidebarContent"] {{ padding-top:0.4rem; }}
[data-testid="stSidebarNav"] a, [data-testid="stSidebarNavLink"] {{ border-radius:4px; }}
[data-testid="stSidebarNavLink"] span {{ font-size:13px; color:var(--qe-text-2); }}
[data-testid="stSidebarNavLink"][aria-current="page"] {{ background:var(--qe-accent-dim); }}
[data-testid="stSidebarNavLink"][aria-current="page"] span {{ color:var(--qe-text); font-weight:600; }}
[data-testid="stNavSectionHeader"] {{ font-size:10.5px; letter-spacing:.09em; text-transform:uppercase;
  color:var(--qe-muted); margin-top:.6rem; }}

/* Panels: st.container(key="panel_…") gets the class st-key-panel_… */
[class*="st-key-panel"] {{ background:var(--qe-surface); border:1px solid var(--qe-border); border-radius:6px;
  padding:12px 14px; }}

/* Widgets */
.stButton > button, .stDownloadButton > button {{ font-size:12.5px; padding:0.25rem 0.75rem; min-height:32px;
  border-radius:4px; border:1px solid var(--qe-border-strong); background:var(--qe-surface-2); }}
.stButton > button:hover {{ border-color:var(--qe-accent); color:var(--qe-accent); }}
.stButton > button[kind="primary"] {{ background:var(--qe-accent); color:#04201f; border-color:var(--qe-accent); font-weight:600; }}
.stButton > button[kind="primary"]:hover {{ filter:brightness(1.08); color:#04201f; }}
[data-baseweb="select"] > div, [data-baseweb="input"] > div {{ background:var(--qe-surface) !important; min-height:34px; }}
[data-testid="stWidgetLabel"] p {{ font-size:11.5px !important; color:var(--qe-muted); }}
[data-testid="stSegmentedControl"] button, [data-testid="stButtonGroup"] button {{ font-size:12px; min-height:30px; padding:0 .6rem; }}
.stTabs [data-baseweb="tab-list"] {{ gap:2px; border-bottom:1px solid var(--qe-border); }}
.stTabs [data-baseweb="tab"] {{ font-size:12.5px; padding:6px 10px; height:auto; }}
[data-testid="stExpander"] details {{ border:1px solid var(--qe-border); border-radius:6px; background:var(--qe-surface); }}
[data-testid="stDataFrame"] {{ border:1px solid var(--qe-border); border-radius:4px; }}
[data-testid="stMetricValue"] {{ font-family:var(--qe-mono); font-size:20px; }}
[data-testid="stCaptionContainer"] {{ color:var(--qe-muted); font-size:11.5px; }}
[data-testid="stAlert"] {{ border-radius:4px; }}
code {{ font-family:var(--qe-mono); font-size:12px; background:var(--qe-surface-2); color:var(--qe-text-2); }}

/* QuantEdge components */
.qe-mono {{ font-family:var(--qe-mono); font-variant-numeric:tabular-nums; }}
.qe-pos {{ color:var(--qe-pos); }} .qe-neg {{ color:var(--qe-neg); }} .qe-muted {{ color:var(--qe-muted); }}
.qe-brand {{ display:flex; align-items:center; gap:8px; padding:6px 4px 10px 4px; }}
.qe-brand b {{ font-size:15px; letter-spacing:.02em; }}
.qe-brand span {{ font-size:10px; color:var(--qe-muted); letter-spacing:.08em; text-transform:uppercase; }}
.qe-topbar-status {{ display:flex; flex-wrap:wrap; gap:6px; justify-content:flex-end; align-items:center; min-height:34px; }}
.qe-pill {{ display:inline-flex; align-items:center; gap:6px; font-size:11.5px; line-height:1; padding:6px 8px;
  border:1px solid var(--qe-border); border-radius:4px; background:var(--qe-surface); color:var(--qe-text-2); white-space:nowrap; }}
.qe-pill b {{ color:var(--qe-text); font-weight:500; }}
.qe-dot {{ width:7px; height:7px; border-radius:50%; background:var(--qe-muted); flex:none; }}
.qe-dot.ok {{ background:var(--qe-pos); }} .qe-dot.warn {{ background:var(--qe-warn); }}
.qe-dot.err {{ background:var(--qe-neg); }} .qe-dot.off {{ background:transparent; border:1px solid var(--qe-muted); }}
.qe-tag {{ display:inline-block; font-size:10px; font-weight:600; letter-spacing:.06em; text-transform:uppercase;
  padding:2px 6px; border-radius:3px; border:1px solid var(--qe-border-strong); color:var(--qe-text-2); }}
.qe-tag.demo {{ color:var(--qe-warn); border-color:var(--qe-warn); }}
.qe-tag.accent {{ color:var(--qe-accent); border-color:#1d5a5c; }}
.qe-tag.pos {{ color:var(--qe-pos); border-color:#1f5a40; }} .qe-tag.neg {{ color:var(--qe-neg); border-color:#6b2a27; }}
.qe-banner {{ border:1px solid var(--qe-warn); background:repeating-linear-gradient(135deg,#1d1709 0 10px,#191407 10px 20px);
  color:#f0d9a6; border-radius:4px; padding:8px 12px; font-size:12.5px; }}
.qe-banner b {{ color:var(--qe-warn); letter-spacing:.06em; }}
.qe-page-head {{ display:flex; align-items:baseline; gap:12px; margin:4px 0 2px 0; }}
.qe-page-head h1 {{ font-size:19px; font-weight:600; margin:0; padding:0; }}
.qe-page-head span {{ font-size:12px; color:var(--qe-muted); }}
.qe-panel-head {{ display:flex; justify-content:space-between; align-items:baseline; margin:-2px 0 6px 0; gap:8px; }}
.qe-panel-head h3 {{ font-size:12px; font-weight:600; letter-spacing:.07em; text-transform:uppercase; color:var(--qe-text-2); margin:0; padding:0; }}
.qe-panel-head span {{ font-size:11px; color:var(--qe-muted); }}
.qe-tiles {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); gap:8px; }}
.qe-tile {{ background:var(--qe-surface); border:1px solid var(--qe-border); border-radius:6px; padding:10px 12px 8px 12px; }}
.qe-tile .lbl {{ display:flex; justify-content:space-between; font-size:11px; color:var(--qe-muted); letter-spacing:.03em; }}
.qe-tile .val {{ font-family:var(--qe-mono); font-size:19px; color:var(--qe-text); margin-top:3px; }}
.qe-tile .chg {{ font-family:var(--qe-mono); font-size:12px; margin-top:1px; }}
.qe-tile svg {{ display:block; width:100%; height:30px; margin-top:6px; }}
.qe-tile.off .val {{ color:var(--qe-muted); font-size:14px; font-family:inherit; }}
.qe-kv {{ display:grid; grid-template-columns:auto 1fr; gap:5px 14px; margin:0; }}
.qe-kv dt, .qe-kv dd {{ font-size:12.5px !important; line-height:1.45 !important; }}
.qe-kv dt {{ color:var(--qe-muted); }} .qe-kv dd {{ margin:0; font-family:var(--qe-mono); color:var(--qe-text); text-align:right; }}
.qe-list {{ list-style:none; margin:0; padding:0; font-size:12.5px; }}
.qe-list li {{ display:flex; gap:10px; justify-content:space-between; padding:6px 0; border-bottom:1px solid var(--qe-border); }}
.qe-list li:last-child {{ border-bottom:none; }}
.qe-list li, .qe-list div {{ font-size:12.5px !important; line-height:1.4 !important; margin:0 !important; }}
.qe-list .t {{ color:var(--qe-text); }} .qe-list .s {{ color:var(--qe-muted); font-size:11.5px !important; }}
.qe-list .r {{ text-align:right; white-space:nowrap; color:var(--qe-text-2); font-size:11.5px !important; }}
.qe-empty {{ border:1px dashed var(--qe-border-strong); border-radius:6px; padding:16px; text-align:left; }}
.qe-empty b {{ display:block; font-size:13px; color:var(--qe-text); margin-bottom:4px; font-weight:600; }}
.qe-empty p {{ font-size:12.5px !important; color:var(--qe-text-2); margin:0; }}
.qe-empty code {{ font-size:11.5px; }}
.qe-metrics {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(110px,1fr)); gap:10px 14px; }}
.qe-metrics div span {{ display:block; font-size:10.5px; color:var(--qe-muted); letter-spacing:.04em; text-transform:uppercase; }}
.qe-metrics div b {{ display:block; font-family:var(--qe-mono); font-size:15px; font-weight:500; margin-top:2px; }}
.qe-sidefoot {{ font-size:11px; color:var(--qe-muted); border-top:1px solid var(--qe-border); padding-top:10px; margin-top:8px; line-height:1.55; }}
@media (max-width: 900px) {{
  .block-container {{ padding:0.6rem 0.8rem 2rem 0.8rem !important; }}
  .qe-topbar-status {{ justify-content:flex-start; }}
  .qe-tiles {{ grid-template-columns:repeat(2,minmax(0,1fr)); }}
}}
</style>
"""
