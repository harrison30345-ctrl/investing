"""
Shared visual system.

One place for colour, type, spacing and the small set of render helpers every
page uses, so the application reads as a single product rather than nine pages
that were styled separately.

DESIGN POSITION
---------------
Restrained, typographic, dense where the data is dense. Hierarchy comes from
type scale, weight and whitespace, not from putting each value in its own box.

Rules this module enforces:
  * Cards are the exception. A hairline rule or plain whitespace groups
    information in most cases; a bordered surface is used only where grouping
    genuinely aids comprehension.
  * Colour carries meaning or it is not used. Scores are rendered in ink with a
    quiet bar, not as red/amber/green traffic lights -- a score of 64 is not a
    warning, it is a number. Positive/negative colour is reserved for values
    that are genuinely directional, such as a price change.
  * Numbers are tabular so columns align and can be scanned.
  * No shadows, no gradients, no glows, no motion. Radius stays at 3-4px.
"""
from __future__ import annotations

import streamlit as st
import streamlit.components.v1 as components

__all__ = [
    "inject", "hover_to_open_sidebar", "metric_table", "list_rows",
    "action_row", "info_dot", "page_header", "section", "score_row", "stat_grid",
    "two_column_list", "hairline", "chart_layout_quiet",
    "INK", "MUTED", "FAINT", "RULE", "BRASS", "POSITIVE", "NEGATIVE", "WARNING",
    "PAPER", "SURFACE", "NAVY",
]

# ── Palette ──────────────────────────────────────────────────────────────────
# Desaturated throughout. The old gold (#b8960c) read as a highlighter next to
# body text; this brass sits back and still reads as the same brand.
PAPER = "#faf9f7"       # warm off-white page
SURFACE = "#ffffff"     # used sparingly
NAVY = "#161a24"        # navigation
INK = "#12161f"         # primary text
MUTED = "#5f6672"       # secondary text
FAINT = "#9aa1ad"       # labels, captions
RULE = "#e7e4dd"        # hairline borders
BRASS = "#8d7434"       # accent, used at small sizes only
POSITIVE = "#2f6b4f"    # muted green
NEGATIVE = "#9b3b3b"    # muted red
WARNING = "#8a6a2f"     # muted amber

_CSS = f"""
<style>
  /* ── Type ──────────────────────────────────────────────── */
  html, body, [class*="css"], .stApp {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter,
                   "Helvetica Neue", Arial, sans-serif;
      font-feature-settings: "tnum" 1, "cv05" 1;
  }}
  .stApp {{ background: {PAPER}; }}
  .block-container {{
      background: {PAPER};
      max-width: 1320px;
      padding: 1.5rem 2.2rem 3rem 2.2rem;
  }}

  /* ── Streamlit chrome ──────────────────────────────────── */
  #MainMenu, footer, header [data-testid="stToolbar"] {{ visibility: hidden; }}
  [data-testid="stDecoration"] {{ display: none; }}
  .stDeployButton {{ display: none; }}

  /* The header is made transparent but NOT zero-height: the control that
     reopens a collapsed sidebar lives inside it. Collapsing the header hid
     that control, so once the sidebar closed there was no way to bring it
     back. Keep it present and out of the way instead. */
  [data-testid="stHeader"] {{
      background: transparent; height: auto; min-height: 0;
      pointer-events: none;
  }}
  [data-testid="stHeader"] > * {{ pointer-events: auto; }}

  /* Make the reopen control clearly visible against the page. */
  [data-testid="stSidebarCollapsedControl"] {{
      pointer-events: auto !important;
      visibility: visible !important;
      opacity: 1 !important;
  }}
  [data-testid="stSidebarCollapsedControl"] button {{
      background: {SURFACE} !important; border: 1px solid {RULE} !important;
      border-radius: 3px !important; color: {MUTED} !important;
  }}
  [data-testid="stSidebarCollapsedControl"] button:hover {{
      border-color: #cfcabf !important; color: {INK} !important;
  }}
  /* Keep the collapse arrow inside the sidebar reachable too. */
  [data-testid="stSidebarCollapseButton"] {{ visibility: visible !important; }}

  /* Guarantee the expand control is on-screen whenever the sidebar is closed.
     A collapsed sidebar that cannot be reopened strands the user with no
     navigation, so this is pinned rather than left to the default layout. */
  [data-testid="stExpandSidebarButton"] {{
      position: fixed !important; top: 0.55rem !important; left: 0.6rem !important;
      z-index: 999 !important; visibility: visible !important; opacity: 1 !important;
      pointer-events: auto !important;
  }}
  [data-testid="stExpandSidebarButton"] button {{
      background: {SURFACE} !important; border: 1px solid {RULE} !important;
      border-radius: 3px !important; color: {MUTED} !important;
  }}

  /* ── Headings ──────────────────────────────────────────── */
  h1 {{
      font-size: 1.85rem !important; font-weight: 700 !important;
      letter-spacing: -0.022em !important; color: {INK} !important;
      margin: 0 0 0.2rem 0 !important; padding: 0 !important;
  }}
  h2 {{
      font-size: 1.2rem !important; font-weight: 680 !important;
      color: {INK} !important; letter-spacing: -0.012em !important;
      margin: 1.7rem 0 0.6rem 0 !important;
  }}
  /* Section headings. Previously a small faint uppercase label, which read as
     a caption rather than a title and left each block looking untitled. Now
     set in ink at a size that anchors the section it introduces. */
  h3, h4, h5 {{
      font-size: 0.95rem !important; font-weight: 680 !important;
      letter-spacing: -0.005em !important; text-transform: none !important;
      color: {INK} !important; margin: 1.7rem 0 0.7rem 0 !important;
  }}
  .stMarkdown p {{ color: {MUTED}; font-size: 0.875rem; line-height: 1.62; }}
  .stCaption, small, [data-testid="stCaptionContainer"] p {{
      color: {FAINT} !important; font-size: 0.75rem !important; line-height: 1.55 !important;
  }}

  /* ── Rules replace most cards ──────────────────────────── */
  hr {{ border: none; border-top: 1px solid {RULE}; margin: 1.5rem 0 1.1rem 0; }}

  /* ── Sidebar ───────────────────────────────────────────── */
  section[data-testid="stSidebar"] {{
      background: {NAVY};
      border-right: none;
  }}
  /* Width is set on the inner content, never on the sidebar element itself.
     Streamlit sets an inline width on that element to collapse it; overriding
     it with !important left the sidebar half-collapsed and pushed its own
     reopen control off-screen, so once it closed there was no way back. */
  section[data-testid="stSidebar"] > div:first-child {{ min-width: 210px; }}
  section[data-testid="stSidebar"] > div {{ padding-top: 1.4rem; }}
  section[data-testid="stSidebar"] * {{ color: #aeb5c2; }}
  section[data-testid="stSidebar"] .stRadio label {{
      font-size: 0.82rem !important;
      letter-spacing: 0.005em;
      padding: 0.3rem 0.55rem !important;
      border-radius: 3px;
      text-transform: none !important;
      color: #aeb5c2 !important;
  }}
  section[data-testid="stSidebar"] .stRadio label:hover {{ background: rgba(255,255,255,0.05); }}
  section[data-testid="stSidebar"] .stRadio label p {{ font-size: 0.82rem !important; }}
  section[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p,
  section[data-testid="stSidebar"] .stSelectbox label,
  section[data-testid="stSidebar"] .stSlider label,
  section[data-testid="stSidebar"] .stTextArea label,
  section[data-testid="stSidebar"] .stTextInput label {{
      font-size: 0.66rem !important; letter-spacing: 0.08em !important;
      text-transform: uppercase !important; color: #6f7787 !important;
      font-weight: 600 !important;
  }}
  section[data-testid="stSidebar"] hr {{ border-color: #262c3a; margin: 1.1rem 0; }}

  /* ── Buttons ───────────────────────────────────────────── */
  .stButton > button {{
      background: {INK} !important; color: #ffffff !important;
      border: 1px solid {INK} !important; border-radius: 3px !important;
      font-weight: 550 !important; font-size: 0.8rem !important;
      letter-spacing: 0.005em !important; text-transform: none !important;
      padding: 0.4rem 0.95rem !important; box-shadow: none !important;
      transition: background 0.12s ease;
  }}
  .stButton > button:hover {{ background: #2a3040 !important; border-color: #2a3040 !important; }}
  .stButton > button:focus {{ box-shadow: 0 0 0 2px rgba(141,116,52,0.35) !important; }}
  section[data-testid="stSidebar"] .stButton > button {{
      background: transparent !important; color: #cfd4dd !important;
      border: 1px solid #333a4a !important; width: 100%;
  }}
  section[data-testid="stSidebar"] .stButton > button:hover {{
      background: rgba(255,255,255,0.05) !important; border-color: #46506480 !important;
  }}

  /* ── Tabs ──────────────────────────────────────────────── */
  .stTabs [data-baseweb="tab-list"] {{
      border-bottom: 1px solid {RULE}; gap: 1.5rem; background: transparent;
  }}
  .stTabs [data-baseweb="tab"] {{
      font-size: 0.82rem !important; font-weight: 500 !important;
      letter-spacing: 0 !important; text-transform: none !important;
      padding: 0.45rem 0 !important; color: {FAINT} !important;
      background: transparent !important; border: none !important;
  }}
  .stTabs [aria-selected="true"] {{
      color: {INK} !important; border-bottom: 1.5px solid {INK} !important;
  }}

  /* ── Tables ────────────────────────────────────────────── */
  div[data-testid="stDataFrame"] {{
      border: 1px solid {RULE}; border-radius: 3px; box-shadow: none;
  }}
  div[data-testid="stDataFrame"] * {{ font-size: 0.8rem !important; }}

  /* ── Inputs ────────────────────────────────────────────── */
  .stSelectbox div[data-baseweb="select"], .stMultiSelect div[data-baseweb="select"],
  .stTextInput input, .stNumberInput input, .stTextArea textarea {{
      border-radius: 3px !important; font-size: 0.85rem !important;
      border-color: {RULE} !important; background: {SURFACE} !important;
  }}
  .stSelectbox div[data-baseweb="select"]:hover, .stTextInput input:hover {{
      border-color: #cfcabf !important;
  }}
  .stSlider [data-baseweb="slider"] div[role="slider"] {{ box-shadow: none !important; }}
  .stSlider [data-testid="stTickBar"] {{ display: none; }}

  /* ── Alerts: flatten Streamlit's coloured blocks ───────── */
  div[data-testid="stAlert"] {{
      border-radius: 3px; border: 1px solid {RULE};
      background: {SURFACE}; box-shadow: none; padding: 0.7rem 0.9rem;
  }}
  div[data-testid="stAlert"] p {{ font-size: 0.82rem !important; color: {MUTED} !important; }}

  /* ── Expanders ─────────────────────────────────────────── */
  div[data-testid="stExpander"] {{
      border: 1px solid {RULE}; border-radius: 3px; background: transparent;
  }}
  div[data-testid="stExpander"] summary {{ font-size: 0.82rem !important; color: {MUTED}; }}
  div[data-testid="stExpander"] summary:hover {{ color: {INK}; }}

  /* Streamlit leaves generous gaps between every element; a research page
     should read densely, so these are pulled in -- but only the flex gap.
     Zeroing .element-container margins collapsed the space Streamlit relies
     on and elements began overlapping each other. */
  [data-testid="stVerticalBlock"] {{ gap: 0.65rem; }}
  [data-testid="stHorizontalBlock"] {{ gap: 1.6rem; }}

  /* ── Shared components ─────────────────────────────────── */
  .bs-eyebrow {{
      font-size: 0.72rem; font-weight: 650; letter-spacing: 0.08em;
      text-transform: uppercase; color: {MUTED}; margin-bottom: 0.5rem;
  }}
  .bs-sub {{ font-size: 0.83rem; color: {MUTED}; margin: 0.2rem 0 1.1rem 0; }}

  /* Score row: small type, quiet bar, ink not traffic lights */
  .bs-scores {{ display: flex; gap: 2.4rem; flex-wrap: wrap; margin: 0.2rem 0 0.2rem; }}
  .bs-score {{ min-width: 88px; }}
  .bs-score-label {{
      font-size: 0.68rem; letter-spacing: 0.06em; text-transform: uppercase;
      color: {MUTED}; margin-bottom: 0.24rem; white-space: nowrap;
      font-weight: 600;
  }}
  .bs-score-value {{
      font-size: 1.28rem; font-weight: 600; color: {INK}; line-height: 1;
      font-variant-numeric: tabular-nums;
  }}
  .bs-score-value.na {{ font-size: 0.82rem; font-weight: 500; color: {FAINT}; }}
  .bs-bar {{ height: 2px; background: {RULE}; margin-top: 0.45rem; width: 100%; }}
  .bs-bar > i {{ display: block; height: 2px; background: {BRASS}; }}

  /* Compact key/value grid, replaces one-card-per-metric */
  .bs-stats {{
      display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
      gap: 0 2.2rem;
  }}
  .bs-stat {{
      display: flex; justify-content: space-between; align-items: baseline;
      padding: 0.5rem 0; border-bottom: 1px solid {RULE}; gap: 1rem;
  }}
  .bs-stat-k {{ font-size: 0.8rem; color: {MUTED}; }}
  .bs-stat-v {{
      font-size: 0.85rem; color: {INK}; font-weight: 550;
      font-variant-numeric: tabular-nums; white-space: nowrap;
  }}
  .bs-stat-v.na {{ color: {FAINT}; font-weight: 400; }}

  /* Two-column strengths / risks */
  .bs-cols {{ display: grid; grid-template-columns: 1fr 1fr; gap: 0 3rem; }}
  .bs-cols ul {{ margin: 0; padding-left: 1.05rem; }}
  .bs-cols li {{
      font-size: 0.845rem; color: {MUTED}; margin-bottom: 0.42rem; line-height: 1.55;
  }}
  .bs-cols li b {{ color: {INK}; font-weight: 550; }}

  /* Compact list rows, replaces stock cards */
  .bs-row {{
      display: grid; grid-template-columns: 62px 1fr auto;
      gap: 0 1rem; align-items: baseline;
      padding: 0.6rem 0; border-bottom: 1px solid {RULE};
  }}
  .bs-row-t {{ font-size: 0.85rem; font-weight: 600; color: {INK}; }}
  .bs-row-n {{ font-size: 0.82rem; color: {MUTED}; }}
  .bs-row-r {{ font-size: 0.76rem; color: {FAINT}; margin-top: 0.1rem; }}
  .bs-row-s {{
      font-size: 0.95rem; font-weight: 600; color: {INK};
      font-variant-numeric: tabular-nums; text-align: right;
  }}

  .bs-panel {{
      border: 1px solid {RULE}; border-radius: 3px; background: {SURFACE};
      padding: 0.8rem 1rem; margin-bottom: 0.6rem;
  }}
  .bs-panel h3 {{ margin: 0 0 0.3rem 0 !important; }}
  .bs-panel p {{
      margin: 0; font-size: 1.1rem; font-weight: 600; color: {INK};
      font-variant-numeric: tabular-nums;
  }}

  .bs-pos {{ color: {POSITIVE}; }}
  .bs-neg {{ color: {NEGATIVE}; }}
  .bs-warn {{ color: {WARNING}; }}
  .bs-tag {{
      display: inline-block; font-size: 0.68rem; letter-spacing: 0.04em;
      color: {MUTED}; border: 1px solid {RULE}; border-radius: 2px;
      padding: 0.1rem 0.4rem; margin-right: 0.3rem;
  }}

  /* Metric grid */
  .bs-mtable {{
      display: grid; grid-template-columns: repeat(var(--cols, 2), minmax(0, 1fr));
      column-gap: 2.6rem; border-top: 1px solid {RULE};
  }}
  .bs-mrow {{
      display: flex; justify-content: space-between; align-items: baseline;
      gap: 1rem; padding: 0.52rem 0; border-bottom: 1px solid {RULE};
  }}
  .bs-mk {{ font-size: 0.83rem; color: {MUTED}; }}
  .bs-mv {{
      font-size: 0.87rem; font-weight: 600; color: {INK};
      font-variant-numeric: tabular-nums; white-space: nowrap;
  }}
  .bs-mv.na {{ color: {FAINT}; font-weight: 400; font-size: 0.8rem; }}
  .bs-dot {{
      display: inline-flex; align-items: center; justify-content: center;
      width: 13px; height: 13px; margin-left: 0.35rem; border-radius: 50%;
      border: 1px solid {RULE}; color: {FAINT}; font-size: 0.58rem;
      font-style: normal; cursor: help; vertical-align: middle;
  }}
  .bs-dot:hover {{ border-color: {BRASS}; color: {BRASS}; }}

  /* Compact list rows */
  .bs-lrow {{
      display: grid; grid-template-columns: 68px 1fr auto; gap: 0 1rem;
      align-items: baseline; padding: 0.55rem 0; border-bottom: 1px solid {RULE};
  }}
  .bs-lt {{ font-size: 0.85rem; font-weight: 650; color: {INK}; }}
  .bs-ln {{ font-size: 0.83rem; color: {MUTED}; display: block; }}
  .bs-lr {{ display: block; font-size: 0.76rem; color: {FAINT}; margin-top: 0.1rem; }}
  .bs-ls {{
      font-size: 0.95rem; font-weight: 650; color: {INK};
      font-variant-numeric: tabular-nums; text-align: right; white-space: nowrap;
  }}
  .bs-rd {{ font-size: 0.72rem; font-weight: 600; margin-left: 0.4rem; }}

  /* ── Responsive ────────────────────────────────────────── */
  @media (max-width: 900px) {{
      .block-container {{ padding: 1.4rem 1.1rem 3rem 1.1rem; }}
      .bs-cols {{ grid-template-columns: 1fr; gap: 0; }}
      .bs-scores {{ gap: 1.3rem 1.6rem; }}
      .bs-score {{ min-width: 72px; }}
      .bs-stats {{ grid-template-columns: 1fr; gap: 0; }}
      .bs-mtable {{ grid-template-columns: 1fr !important; column-gap: 0; }}
      .bs-lrow {{ grid-template-columns: 58px 1fr auto; }}
      h1 {{ font-size: 1.5rem !important; }}
      h2 {{ font-size: 1.08rem !important; }}
      h3, h4, h5 {{ font-size: 0.9rem !important; }}
  }}
  @media (max-width: 560px) {{
      .bs-row {{ grid-template-columns: 54px 1fr auto; }}
      .bs-score-value {{ font-size: 1.1rem; }}
  }}
  @media (prefers-reduced-motion: reduce) {{
      * {{ transition: none !important; animation: none !important; }}
  }}
</style>
"""


def inject() -> None:
    """Apply the visual system. Call once, immediately after set_page_config."""
    st.markdown(_CSS, unsafe_allow_html=True)


# ── Render helpers ───────────────────────────────────────────────────────────

def page_header(title: str, subtitle: str = "", eyebrow: str = "") -> None:
    """Page title with optional eyebrow and one-line subtitle. No emoji."""
    html = ""
    if eyebrow:
        html += f'<div class="bs-eyebrow">{eyebrow}</div>'
    html += f"<h1>{title}</h1>"
    if subtitle:
        html += f'<div class="bs-sub">{subtitle}</div>'
    st.markdown(html, unsafe_allow_html=True)


def section(label: str) -> None:
    """A small uppercase section label. Sections are separated by space, not boxes."""
    st.markdown(f"<h3>{label}</h3>", unsafe_allow_html=True)


def hairline() -> None:
    st.markdown(f'<div style="border-top:1px solid {RULE};margin:1.4rem 0 1rem;"></div>',
                unsafe_allow_html=True)


def score_row(scores: list[tuple[str, float | None]]) -> None:
    """One horizontal row of category scores.

    Rendered in ink with a thin brass bar rather than red/amber/green: a score
    of 64 is a number, not a warning, and colouring it as one misleads.
    """
    cells = []
    for label, value in scores:
        if value is None:
            cells.append(
                f'<div class="bs-score"><div class="bs-score-label">{label}</div>'
                f'<div class="bs-score-value na">Not assessed</div>'
                f'<div class="bs-bar"></div></div>'
            )
        else:
            cells.append(
                f'<div class="bs-score"><div class="bs-score-label">{label}</div>'
                f'<div class="bs-score-value">{value:.0f}</div>'
                f'<div class="bs-bar"><i style="width:{max(0, min(100, value)):.0f}%"></i></div>'
                f'</div>'
            )
    st.markdown(f'<div class="bs-scores">{"".join(cells)}</div>', unsafe_allow_html=True)


def stat_grid(stats: list[tuple[str, str | None]]) -> None:
    """Compact key/value grid. Replaces one-card-per-metric layouts."""
    items = []
    for key, value in stats:
        if value is None:
            items.append(f'<div class="bs-stat"><span class="bs-stat-k">{key}</span>'
                         f'<span class="bs-stat-v na">Not reported</span></div>')
        else:
            items.append(f'<div class="bs-stat"><span class="bs-stat-k">{key}</span>'
                         f'<span class="bs-stat-v">{value}</span></div>')
    st.markdown(f'<div class="bs-stats">{"".join(items)}</div>', unsafe_allow_html=True)


def two_column_list(left_title: str, left: list[str],
                    right_title: str, right: list[str]) -> None:
    """Strengths / risks side by side, without a container per bullet."""
    def col(title, items):
        if items:
            lis = "".join(f"<li>{i}</li>" for i in items)
        else:
            lis = f'<li style="color:{FAINT};">Nothing notable.</li>'
        return f'<div><h3 style="margin-top:0;">{title}</h3><ul>{lis}</ul></div>'

    st.markdown(f'<div class="bs-cols">{col(left_title, left)}{col(right_title, right)}</div>',
                unsafe_allow_html=True)


def chart_layout_quiet(**kwargs) -> dict:
    """Plotly layout: no legend, no gridlines, one colour, minimal chrome."""
    layout = dict(
        margin=dict(l=8, r=8, t=8, b=24),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
        font=dict(family="-apple-system, BlinkMacSystemFont, Segoe UI, sans-serif",
                  size=11, color=FAINT),
        xaxis=dict(showgrid=False, zeroline=False, linecolor=RULE,
                   tickfont=dict(size=10, color=FAINT)),
        yaxis=dict(showgrid=True, gridcolor=RULE, zeroline=False,
                   linecolor="rgba(0,0,0,0)", tickfont=dict(size=10, color=FAINT)),
        hoverlabel=dict(bgcolor=SURFACE, font_size=11, bordercolor=RULE),
    )
    layout.update(kwargs)
    return layout

def hover_to_open_sidebar(open_delay_ms: int = 260, close_delay_ms: int = 420) -> None:
    """Open the sidebar on hover and close it when the pointer leaves.

    Implemented by driving Streamlit's own collapse/expand controls, so the
    sidebar state genuinely changes and the page reflows. A CSS-only reveal
    would slide a clipped, non-interactive copy over the content -- the
    collapsed sidebar sits outside an overflow:hidden parent.

    Both directions are deliberately delayed. Opening the instant a cursor
    crosses the edge feels twitchy and fires when someone is just moving
    towards the page; closing instantly punishes a cursor that strays a few
    pixels outside while reading. The timers are cancelled if the pointer
    returns, so only a sustained hover counts.

    Runs inside a components iframe and reaches into the parent document, which
    is same-origin. If that access is blocked the guard below does nothing and
    the buttons still work as normal.
    """
    components.html(
        f"""
        <script>
        (function () {{
          let doc;
          try {{ doc = window.parent.document; }} catch (e) {{ return; }}
          if (!doc || doc.getElementById('bs-hover-open')) return;

          const OPEN_DELAY = {open_delay_ms};
          const CLOSE_DELAY = {close_delay_ms};

          const marker = doc.createElement('div');
          marker.id = 'bs-hover-open';
          marker.style.display = 'none';
          doc.body.appendChild(marker);

          // Ease the slide in both directions. Streamlit animates the width;
          // this simply makes that motion unhurried.
          const style = doc.createElement('style');
          style.textContent = `
            section[data-testid="stSidebar"] {{
              transition: width 320ms cubic-bezier(.4,0,.2,1),
                          transform 320ms cubic-bezier(.4,0,.2,1),
                          min-width 320ms cubic-bezier(.4,0,.2,1) !important;
            }}
            @media (prefers-reduced-motion: reduce) {{
              section[data-testid="stSidebar"] {{ transition: none !important; }}
            }}
          `;
          doc.head.appendChild(style);

          const sidebar = () => doc.querySelector('[data-testid="stSidebar"]');
          const isOpen = () => {{
            const sb = sidebar();
            return !!sb && sb.getAttribute('aria-expanded') !== 'false';
          }};
          const expandBtn = () =>
            doc.querySelector('[data-testid="stExpandSidebarButton"] button') ||
            doc.querySelector('[data-testid="stExpandSidebarButton"]');
          const collapseBtn = () =>
            doc.querySelector('[data-testid="stSidebarCollapseButton"] button') ||
            doc.querySelector('[data-testid="stSidebarCollapseButton"]');

          let openTimer = null, closeTimer = null;
          const cancel = () => {{
            if (openTimer) {{ clearTimeout(openTimer); openTimer = null; }}
            if (closeTimer) {{ clearTimeout(closeTimer); closeTimer = null; }}
          }};
          const scheduleOpen = () => {{
            cancel();
            if (isOpen()) return;
            openTimer = setTimeout(() => {{
              const b = expandBtn(); if (b && !isOpen()) b.click();
            }}, OPEN_DELAY);
          }};
          const scheduleClose = () => {{
            cancel();
            if (!isOpen()) return;
            closeTimer = setTimeout(() => {{
              // Do not close while a control inside the sidebar has focus or a
              // dropdown is open -- that would yank the panel away mid-use.
              const sb = sidebar();
              if (!sb) return;
              if (sb.contains(doc.activeElement)) return;
              if (doc.querySelector('[data-baseweb="popover"], [data-baseweb="menu"]')) return;
              const b = collapseBtn(); if (b && isOpen()) b.click();
            }}, CLOSE_DELAY);
          }};

          // Left-edge strip: only live while the sidebar is closed.
          const strip = doc.createElement('div');
          strip.id = 'bs-hover-strip';
          Object.assign(strip.style, {{
            position: 'fixed', left: '0', top: '0', width: '16px', height: '100vh',
            zIndex: '998', background: 'transparent',
          }});
          strip.addEventListener('mouseenter', scheduleOpen);
          strip.addEventListener('mouseleave', cancel);
          doc.body.appendChild(strip);

          // Choosing a page should put the sidebar away. The pointer is still
          // over the panel after a click, so the leave-trigger never fires, and
          // the freshly focused radio would block the close guard as well.
          // Only the navigation group does this -- the per-page settings below
          // it are controls the reader is working with, not a destination.
          const closeAfterNav = () => {{
            cancel();
            setTimeout(() => {{
              const sb = sidebar();
              if (sb && doc.activeElement && sb.contains(doc.activeElement)) {{
                doc.activeElement.blur();
              }}
              const b = collapseBtn();
              if (b && isOpen()) b.click();
            }}, 380);
          }};

          const wire = () => {{
            const sb = sidebar();
            if (sb && !sb.dataset.bsHover) {{
              sb.dataset.bsHover = '1';
              sb.addEventListener('mouseenter', cancel);
              sb.addEventListener('mouseleave', scheduleClose);
            }}

            const nav = sb && sb.querySelector('[role="radiogroup"]');
            if (nav && !nav.dataset.bsNav) {{
              nav.dataset.bsNav = '1';
              nav.addEventListener('click', closeAfterNav);
            }}
            const eb = doc.querySelector('[data-testid="stExpandSidebarButton"]');
            if (eb && !eb.dataset.bsHover) {{
              eb.dataset.bsHover = '1';
              eb.addEventListener('mouseenter', scheduleOpen);
              eb.addEventListener('mouseleave', cancel);
            }}
            strip.style.pointerEvents = isOpen() ? 'none' : 'auto';
          }};
          wire();
          new MutationObserver(wire).observe(doc.body, {{
            childList: true, subtree: true,
            attributes: true, attributeFilter: ['aria-expanded'],
          }});
        }})();
        </script>
        """,
        height=0,
    )


# ── Data presentation ────────────────────────────────────────────────────────

def metric_table(rows: list, columns: int = 2) -> None:
    """Financial snapshot as a compact grid of metric/value pairs.

    Missing values read "Not available" rather than 0, a blank, or a bare dash:
    a reader cannot tell an unreported figure from a genuine zero otherwise,
    and the difference matters.
    """
    cells = []
    for label, value, hint in rows:
        shown = value if value not in (None, "") else "Not available"
        na = ' na' if value in (None, "") else ""
        tip = f' title="{hint}"' if hint else ""
        dot = f'<span class="bs-dot"{tip}>i</span>' if hint else ""
        cells.append(
            f'<div class="bs-mrow"><span class="bs-mk">{label}{dot}</span>'
            f'<span class="bs-mv{na}">{shown}</span></div>'
        )
    st.markdown(
        f'<div class="bs-mtable" style="--cols:{columns};">{"".join(cells)}</div>',
        unsafe_allow_html=True,
    )


def list_rows(rows: list) -> None:
    """Compact list rows for Discover, Watchlist and search results.

    A table rather than one card per company: cards force the eye to re-orient
    for every entry, which is the opposite of what scanning a list needs.
    """
    html = []
    for r in rows:
        delta = ""
        if r.get("delta") not in (None, ""):
            cls = "bs-pos" if r["delta"] >= 0 else "bs-neg"
            delta = f'<span class="bs-rd {cls}">{r["delta"]:+.0f}</span>'
        html.append(
            f'<div class="bs-lrow">'
            f'<span class="bs-lt">{r["ticker"]}</span>'
            f'<span class="bs-ln">{r.get("name", "")}'
            + (f'<span class="bs-lr">{r["reason"]}</span>' if r.get("reason") else "")
            + f'</span>'
            f'<span class="bs-ls">{r["score"]}{delta}</span></div>'
        )
    st.markdown("".join(html), unsafe_allow_html=True)
