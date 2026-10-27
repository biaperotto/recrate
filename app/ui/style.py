"""The app's CSS and the split-pane script, injected once per run."""

from __future__ import annotations

import streamlit as st

CSS = """<style>
:root {--bg:#f1efea; --card:#ffffff; --line:#e6e3da; --ink:#14181F; --muted:#6b7472; --accent:#2A78D6;
       --accent-soft:#e7f0fb; --bubble:#f3f1ec; --dark:#14181F;}
.stApp {background: var(--bg);}
header[data-testid="stHeader"] {height: 0; background: transparent; pointer-events: none;}
header[data-testid="stHeader"] [data-testid="stToolbar"] {pointer-events: none;}
header[data-testid="stHeader"] [data-testid="stToolbarActions"], header [data-testid="stMainMenu"] {display: none;}
[data-testid="stExpandSidebarButton"] {pointer-events: auto;}
.block-container {padding: .9rem 1.4rem .6rem; max-width: 100%;}
h1, h2, h3, h4, h5 {letter-spacing: -.01em; color: var(--ink);}
.muted {color: var(--muted); font-size: .86rem;}
.eyebrow {font-size: .7rem; letter-spacing: .12em; text-transform: uppercase; color: var(--muted); font-weight: 600;}
.quote {border-left: 3px solid var(--accent); padding: .1rem .7rem; margin: .2rem 0 .4rem; font-size: .86rem; color: #3c4846;}
mark.cite {background: rgba(232,163,61,.3); color: inherit; padding: 0 .1em; border-radius: 2px;}

/* top bar */
.st-key-topbar {background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: .3rem .9rem;
  margin-bottom: .6rem;}
.st-key-topbar button {padding: .25rem .8rem; min-height: 2.1rem;}
.st-key-topbar [data-testid="stImage"] {margin: 0;}

/* the two cards */
[data-testid="stHorizontalBlock"]:has(.st-key-chatcard) {flex-wrap: nowrap !important; align-items: stretch;}
[data-testid="stHorizontalBlock"]:has(.st-key-chatcard) > [data-testid="stColumn"] {min-width: 0;}
.st-key-chatcard, .st-key-workcard {background: var(--card); border: 1px solid var(--line); border-radius: 18px;
  height: calc(100vh - 8.4rem) !important; max-height: calc(100vh - 8.4rem); overflow: hidden;
  padding: 1rem 1.1rem .8rem; flex-wrap: nowrap !important; flex: 0 0 auto !important;}
.st-key-chatcard > *, .st-key-workcard > * {flex: 0 0 auto;}
.st-key-chatcard > *:has(.st-key-chatscroll), .st-key-workcard > *:has(.st-key-workscroll) {flex: 1 1 0 !important;
  min-height: 0 !important; height: auto !important;}
.st-key-chatscroll, .st-key-workscroll {height: 100% !important; overflow-y: auto !important; overflow-x: hidden;
  padding-right: .3rem; flex-wrap: nowrap !important;}
.st-key-workfoot {border-top: 1px solid var(--line); padding-top: .7rem; margin: 0 -1.1rem; padding-left: 1.1rem;
  padding-right: 1.1rem;}

/* chat bubbles */
[class*="st-key-msg-a-"] {background: var(--bubble); border-radius: 14px 14px 14px 4px; padding: .7rem .95rem .8rem;
  max-width: 92%; gap: .3rem;}
[class*="st-key-msg-u-"] {background: var(--accent-soft); color: var(--ink); border-radius: 14px 14px 4px 14px;
  padding: .7rem .95rem .8rem; max-width: 85%; margin-left: auto; gap: .2rem;}
[class*="st-key-msg-u-"] p, [class*="st-key-msg-u-"] code {color: var(--ink) !important;}
[class*="st-key-msg-u-"] a {color: var(--accent) !important;}
[class*="st-key-msg-"] p {margin-bottom: .2rem;}
.ctx {background: var(--bubble); color: var(--ink); border: 1px solid var(--line); border-left: 4px solid var(--accent);
  border-radius: 14px; padding: .8rem 1rem; margin: .1rem 0 .6rem;}
.ctx .eyebrow {color: var(--muted);}
.ctx .t {font-weight: 600; font-size: 1rem; margin: .15rem 0 .2rem; color: var(--ink);}
.ctx .m {font-size: .82rem; color: var(--muted);}
.ctx summary {cursor: pointer; font-size: .8rem; color: var(--accent); margin-top: .3rem;}
.ctx details p {font-size: .82rem; color: var(--ink); margin: .3rem 0 0;}
.ctx a {color: var(--accent);}
.chip {display: inline-flex; align-items: center; gap: .35rem; font-size: .75rem; border: 1px solid var(--line);
  border-radius: 999px; padding: .1rem .55rem; color: var(--muted); background: #fbfaf7;}
.dot {width: 7px; height: 7px; border-radius: 50%; display: inline-block;}

/* section bar = tabs */
.st-key-sectionbar [data-testid="stButtonGroup"] {width: 100%;}
.st-key-sectionbar button {border: none !important; background: transparent !important; border-radius: 0 !important;
  border-bottom: 2px solid transparent !important; padding: .55rem 1.1rem !important; box-shadow: none !important;}
.st-key-sectionbar button p {font-size: 1.05rem; font-weight: 600; color: var(--muted);}
.st-key-sectionbar button[aria-checked="true"], .st-key-sectionbar button[kind="segmented_controlActive"] {
  border-bottom: 2px solid var(--accent) !important;}
.st-key-sectionbar button[aria-checked="true"] p, .st-key-sectionbar button[kind="segmented_controlActive"] p {color: var(--ink);}
.st-key-sectionbar {border-bottom: 1px solid var(--line); margin: 0 -1.1rem; padding: 0 1.1rem;}

/* draggable divider */
.rc-split {flex: 0 0 12px; cursor: col-resize; display: flex; align-items: center; justify-content: center;
  align-self: stretch; margin: 0 -4px;}
.rc-split::after {content: ""; width: 4px; height: 56px; border-radius: 4px; background: #cfcabe; transition: background .15s;}
.rc-split:hover::after, .rc-split.dragging::after {background: var(--accent);}

/* the research object's files */
.tree {font-size: .85rem; line-height: 1.55;}
.tree details > div {margin-left: 1rem; border-left: 1px solid var(--line); padding-left: .55rem;}
.tree summary {cursor: pointer; list-style: none; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;}
.tree summary::-webkit-details-marker {display: none;}
.tree summary::before {content: "▸"; display: inline-block; width: .9rem; color: #8a8985;}
.tree details[open] > summary::before {content: "▾";}
.tree .f {white-space: nowrap; overflow: hidden; text-overflow: ellipsis;}
.tree .tag {font-size: .7rem; color: var(--accent); background: var(--accent-soft); border-radius: 6px; padding: 0 .35rem;
  margin-left: .3rem;}
.tree .n {color: #8a8985; font-size: .78rem;}
.st-key-sourcebox p, .st-key-sourcebox li {font-size: .86rem; line-height: 1.5;}
.st-key-sourcebox h1, .st-key-sourcebox h2, .st-key-sourcebox h3 {font-size: 1rem !important;}
.st-key-landing {padding-top: 7vh;}
.st-key-landing [data-testid="stImage"] {margin-bottom: 1.4rem;}
h1.hero {font-size: 2.3rem; line-height: 1.15; margin: 0 0 .7rem; font-weight: 700; letter-spacing: -.02em;}
.logo svg {max-width: 100%;}
.lede {font-size: 1.08rem; color: var(--muted); max-width: 38rem; margin: 0 0 1.6rem;}
.st-key-landing [class*="st-key-get-"] {background: var(--card); border-radius: 14px; min-height: 8.5rem;}
.st-key-landing .foot {margin-top: 2rem;}
.st-key-landing a {color: var(--accent);}
[class*="st-key-suggest-"] {margin: .4rem 0 .2rem;}
[class*="st-key-suggest-"] button {width: 100%; justify-content: flex-start; text-align: left; border-radius: 12px;
  background: var(--card); border: 1px solid var(--line); padding: .35rem .7rem; min-height: 0; font-size: .86rem;}
[class*="st-key-suggest-"] button:hover {border-color: var(--accent); color: var(--accent);}
[class*="st-key-suggest-"] button p {text-align: left;}
section[data-testid="stSidebar"] {background: var(--card); border-right: 1px solid var(--line);}
/* the sidebar's open button sits inside the top bar, at its left end, instead of floating over its corner */
[data-testid="stExpandSidebarButton"] {position: fixed; top: calc(.9rem + 29px); left: calc(1.4rem + 10px); z-index: 1001;}
.st-key-topbar {padding-left: 2.6rem !important;}
.st-key-topbar .logo {margin: 0; line-height: 0; display: flex; align-items: center; height: 100%;}
.st-key-topbar [data-testid="stMarkdown"], .st-key-topbar [data-testid="stMarkdownContainer"] {margin: 0;}
section[data-testid="stSidebar"] h3 {margin-bottom: 0;}
section[data-testid="stSidebar"] .tree {font-size: .8rem;}
/* smooth page changes: no grey "stale" fade while the next page is computed, and the new page fades in while its
   cards settle to their height, instead of jumping */
[data-stale="true"], .stale-element {opacity: 1 !important; transition: none !important;}
@keyframes rc-in {from {opacity: 0; transform: translateY(6px);} to {opacity: 1; transform: none;}}
.st-key-landing {animation: rc-in .3s ease-out both;}
.element-container:has(.rc-mark), [data-testid="stElementContainer"]:has(.rc-mark) {display: none;}
.stApp:has(.rc-entering) [data-testid="stMain"] {transition: opacity .3s ease-out;}
.stApp[data-test-script-state="running"]:has(.rc-entering) [data-testid="stMain"],
.stApp[data-test-script-state="rerunRequested"]:has(.rc-entering) [data-testid="stMain"] {opacity: 0; transition: none;}
/* a page change stays hidden for at most 1.5 s: after that it shows as it comes, so a slow step never looks frozen */
.stApp[data-test-script-state="running"]:has(.rc-entering) [data-testid="stMain"] {animation: rc-reveal .3s ease-out 1.5s forwards;}
@keyframes rc-reveal {to {opacity: 1;}}
/* a sign of life while the script runs: the header that holds Streamlit's own indicator is collapsed above */
.stApp[data-test-script-state="running"]::after {content: "Working…"; position: fixed; top: .55rem; right: 1rem;
  z-index: 1000; font-size: .78rem; color: var(--muted); background: var(--card); border: 1px solid var(--line);
  border-radius: 999px; padding: .15rem .65rem; pointer-events: none; animation: rc-pulse 1.2s ease-in-out infinite;}
@keyframes rc-pulse {0%, 100% {opacity: .45;} 50% {opacity: 1;}}
.steps-line {white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-size: .8rem; color: var(--muted);}
</style>"""

JS = """<script>
(function () {
  const KEY = "recrate-split";
  function apply(col, right, px) {
    col.style.flex = "0 0 " + px + "px"; col.style.width = px + "px"; col.style.maxWidth = "none";
    right.style.flex = "1 1 0"; right.style.minWidth = "0"; right.style.width = "auto";
  }
  function setup() {
    const chat = document.querySelector(".st-key-chatcard");
    if (!chat) return;
    const col = chat.closest('[data-testid="stColumn"]');
    if (!col) return;
    const row = col.parentElement, right = col.nextElementSibling && col.nextElementSibling.classList.contains("rc-split")
      ? col.nextElementSibling.nextElementSibling : col.nextElementSibling;
    if (!right) return;
    const saved = parseFloat(localStorage.getItem(KEY) || "");
    if (!row.querySelector(":scope > .rc-split")) {
      const bar = document.createElement("div");
      bar.className = "rc-split"; bar.title = "Drag to resize · double-click to reset";
      row.insertBefore(bar, right);
      bar.addEventListener("mousedown", function (e) {
        e.preventDefault(); bar.classList.add("dragging");
        const x0 = e.clientX, w0 = col.getBoundingClientRect().width;
        function move(ev) {
          const total = row.getBoundingClientRect().width;
          const px = Math.min(Math.max(w0 + ev.clientX - x0, 320), total - 420);
          apply(col, right, px); localStorage.setItem(KEY, px);
        }
        function up() { bar.classList.remove("dragging"); document.removeEventListener("mousemove", move);
          document.removeEventListener("mouseup", up); document.body.style.cursor = ""; }
        document.addEventListener("mousemove", move); document.addEventListener("mouseup", up);
        document.body.style.cursor = "col-resize";
      });
      bar.addEventListener("dblclick", function () { localStorage.removeItem(KEY); col.removeAttribute("style");
        right.removeAttribute("style"); });
    }
    if (saved && !col.style.flex) apply(col, right, saved);
  }
  setup();
  if (!window.__recrateSplit) { window.__recrateSplit = new MutationObserver(setup);
    window.__recrateSplit.observe(document.body, {childList: true, subtree: true}); }
})();
</script>"""


def inject() -> None:
    """The app's style and the split-pane script; run on every rerun, as the page is."""
    st.html(CSS)
    st.html(JS, unsafe_allow_javascript=True)
