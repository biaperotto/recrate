"""ReCRATE web app: FAIR recreation of scientific research.

    pip install -e ".[app]"
    streamlit run app/app.py

Two cards, side by side, with a divider you can drag:

* the chat: give it a research RO-Crate or a paper (DOI, link or file), then talk to the research object. It
  works with an open model on your machine (Ollama, LM Studio: free, no key), with Claude (an API key), or
  with simple commands;
* the workstation: Reproduce the research on its own data, Reuse its methods on yours, and the Record of every
  run (Workflow Run RO-Crates) with an export bundle.
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from recrate.agent import PaperAgent, agentify, revalidate, review_conclusion, runs_own_code
from recrate.chat import (LOCAL_URL, ChatTools, ClaudeChat, ClaudeCodeChat, LocalChat, OpenAICompatChat,
                          claude_available, claude_code_binary, follow_up_questions, local_models,
                          suggested_questions)
from recrate.crate import CrateError
from recrate.figures import Panel, comparison_chart, panel_figure, pixel_size_um
from recrate.paper import passages
from recrate.pipeline import check_inputs, run_on_data
from recrate.profiles.bioimaging import list_images
from recrate.record import find_runs, headline, publish_back, summarise_record
from recrate.runcrate import approve, image_reviews, review_image, run_story, summarise_run
from recrate.sources import fetch, fetch_dataset, from_upload, identify
from recrate.workspace import (comparable_with_paper, export_bundle, flag_images, methods_markdown, numeric_columns,
                               parameter_sources, read_rows, references_bib, references_csl, whatif)

ROOT = Path(__file__).resolve().parent.parent
IMG = ROOT / "docs" / "img"
st.set_page_config(page_title="ReCRATE", page_icon=str(IMG / "favicon.svg"), layout="wide",
                   initial_sidebar_state="collapsed")

WH = ROOT / "examples" / "workflowhub-nuclei-segmentation"
SD = ROOT / "examples" / "stardist-nuclei"
EXAMPLES = {
    "workflowhub": {"title": "Nuclei segmentation · WorkflowHub #771",
                    "about": "The simple one: a published Galaxy workflow (Otsu threshold, count) reproduced exactly on "
                             "its own test image, then reused on 6 open BBBC039 images with their ground truth.",
                    "tags": ["RO-Crate", "Galaxy", "microscopy", "ground truth"],
                    "agent": WH / "paper_agent", "build": {"crate": WH / "crate"},
                    "data": WH / "reuse-bbbc039",
                    "data_note": "6 images from BBBC039 (U2OS nuclei, Hoechst; Broad Institute, CC0), another lab and "
                                 "microscope, with their ground truth in ground-truth/: your counts are shown next to "
                                 "the true ones."},
    "stardist": {"title": "Cell Detection with Star-convex Polygons · StarDist, arXiv 2018",
                 "about": "A segmentation preprint with public code, model and data. Reproduce its Table 1, then reuse "
                          "the same model on a second public nuclei dataset (BBBC039) and compare the segmentations.",
                 "tags": ["preprint", "Python · TensorFlow", "real data"],
                 "agent": SD / "paper_agent",
                 "fetch_paper": "arXiv:1806.03535",  # fetched when opened: the arXiv licence doesn't let us ship it
                 "build": {"crate": SD / "crate", "paper_doi": "10.48550/arXiv.1806.03535"},
                 "env": True, "data": SD / "reuse-sample",
                 "data_note": "6 of BBBC039's 200 images, with their ground truth (CC0): enough to see how the "
                              "model does on another lab's nuclei. For all 200, use *From a link*.",
                 "reuse_links": {"label": "BBBC039 (Broad Institute, CC0): 200 U2OS nuclei images, Hoechst, with "
                                          "ground truth",
                                 "refs": ["https://data.broadinstitute.org/bbbc/BBBC039/images.zip",
                                          "https://data.broadinstitute.org/bbbc/BBBC039/masks.zip"]}},
}
BADGE = {"validated": ":green-badge[reproduced]", "differs": ":orange-badge[differences found]",
         "failed": ":red-badge[failed]", "not validated": ":gray-badge[not run yet]"}
ICON = {"ok": "✅", "warn": "⚠️", "fail": "❌"}
SERIES = ["#2a78d6", "#eb6834"]  # reference categorical slots 1-2 (validated palette)
TOOL_LABEL = {
    "about_paper": "Reading the research object", "search_paper": "Searching the paper",
    "list_tables": "Listing the paper's tables", "get_table": "Reading a table",
    "list_figures": "Listing the paper's figures", "show_figure": "Showing a figure",
    "describe_method": "Reading the method", "list_my_files": "Looking at your data",
    "check_my_data": "Checking your data", "reproduce": "Reproducing the published result",
    "run_on_my_data": "Running on your data", "methods_text": "Writing the methods paragraph",
    "approve_run": "Recording your review", "create_output_crate": "Creating the output RO-Crate",
    "reproducibility_record": "Reading the record", "set_pixel_size": "Setting the pixel size",
    "compare_paper_parameters": "Running with the published values unchanged",
    "followup_checks": "Listing follow-up checks", "run_followup_check": "Running a follow-up check",
    "make_figure": "Plotting from the run", "recreated_figures": "Showing the recreated figures",
    "review_figure": "Recording your figure review",
}


def chip(name: str, args: dict | None) -> str:
    """A tool call as the chat shows it: what it does, and the call itself, e.g. reproduce(size_dimorphism_index)."""
    arg = next((str(v) for k, v in (args or {}).items() if k in ("method", "query", "check", "table", "figure")), "")
    return f"↳ {TOOL_LABEL.get(name, name)} · `{name}({arg})`"
SECTIONS = {"validate": "Reproduce", "reuse": "Reuse", "record": "Record"}

st.html("""<style>
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
[class*="st-key-msg-u-"] {background: var(--dark); color: #fff; border-radius: 14px 14px 4px 14px; padding: .7rem .95rem .8rem;
  max-width: 85%; margin-left: auto; gap: .2rem;}
[class*="st-key-msg-u-"] p, [class*="st-key-msg-u-"] code {color: #fff !important;}
[class*="st-key-msg-"] p {margin-bottom: .2rem;}
.ctx {background: var(--dark); color: #f1efea; border-radius: 14px; padding: .8rem 1rem; margin: .1rem 0 .6rem;}
.ctx .eyebrow {color: #9fb3ae;}
.ctx .t {font-weight: 600; font-size: 1rem; margin: .15rem 0 .2rem; color: #fff;}
.ctx .m {font-size: .82rem; color: #c9d4d1;}
.ctx summary {cursor: pointer; font-size: .8rem; color: #9fb3ae; margin-top: .3rem;}
.ctx details p {font-size: .82rem; color: #c9d4d1; margin: .3rem 0 0;}
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
.steps-line {white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-size: .8rem; color: var(--muted);}
</style>""")

# The divider between the chat and the workstation: drag to resize, double-click to reset.
st.html("""<script>
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
</script>""", unsafe_allow_javascript=True)
ss = st.session_state


def fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


def short(text: str, limit: int = 240) -> str:
    """The first sentences of a description, leaving out sentences that are only there to give a link."""
    out = ""
    for sent in re.split(r"(?<=[.!?])\s+", " ".join(text.split())):
        if "http" in sent:
            continue
        if out and len(out) + len(sent) > limit:
            break
        out = f"{out} {sent}".strip()
    return out


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50] or "research-object"


def default_name() -> str:
    """Who runs are recorded as: git's user.name, else the computer's user name."""
    import getpass
    import subprocess

    try:
        name = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True, timeout=3).stdout.strip()
    except Exception:  # noqa: BLE001
        name = ""
    return name or getpass.getuser()


ss.setdefault("who", default_name())
ss.setdefault("history", [])
ss.setdefault("ro", {})
ss.setdefault("figures", {})
ss.setdefault("context_notes", [])
ss.setdefault("uploads", Path(tempfile.mkdtemp(prefix="recrate-")))
ss.setdefault("local_url", LOCAL_URL)
ss.setdefault("section", "validate")
who = ss.who
runner = "auto"  # each workflow's language picks its runner (Galaxy, Fiji, Python)


# ---- which model answers in the chat ----------------------------------------------------------
@st.cache_data(ttl=20, show_spinner=False)
def available_models(url: str) -> list[str]:
    return local_models(url)


def pick_model(models: list[str]) -> str | None:
    for pref in ("qwen2.5", "qwen3", "llama3.1", "llama3.2", "mistral", "gemma"):
        hit = next((m for m in models if pref in m.lower()), None)
        if hit:
            return hit
    return models[0] if models else None


AGENTS = {"claude": "Claude", "local": "Local model", "commands": "Simple commands"}


def default_agent() -> str:
    """Claude when it is available (Claude Code signed in, or an API key), else a local model if one runs."""
    if claude_code_binary() or claude_available(ss.get("api_key"))[0]:
        return "claude"
    return "local" if available_models(ss.local_url) else "commands"


def engine_plan() -> tuple[str, str]:
    """(kind, label) for the chosen agent: subscription | claude | local | commands, and the name shown on the chat."""
    choice = ss.engine_choice if ss.engine_choice in AGENTS else "claude"
    if choice == "claude":
        if claude_code_binary():
            return "subscription", "Claude"
        if claude_available(ss.get("api_key"))[0]:
            return "claude", "Claude"
    if choice == "local":
        models = available_models(ss.local_url)
        model = ss.get("local_model") if ss.get("local_model") in models else pick_model(models)
        if model:
            return "local", model
    return "commands", "Simple commands"


def make_engine(agent: PaperAgent, tools: ChatTools):
    kind, _ = engine_plan()
    if kind == "subscription":
        return ClaudeCodeChat(agent, tools)
    if kind == "claude":
        return ClaudeChat(agent, tools, api_key=ss.get("api_key"))
    if kind == "local":
        models = available_models(ss.local_url)
        model = ss.get("local_model") if ss.get("local_model") in models else pick_model(models)
        return OpenAICompatChat(agent, tools, model, ss.local_url)
    return LocalChat(agent, tools)


# ---- building the agent ----------------------------------------------------------------------
def load_agent(path: Path, data: Path | None = None) -> None:
    ss.agent_dir = str(path)
    ss.chat_mark = len(ss.get("history", []))  # the conversation about this research object starts here
    ros = [d for d in ss.get("session_ros", []) if d != str(Path(path).resolve())]
    ss.session_ros = ros + [str(Path(path).resolve())]  # it now appears under Research Objects
    agent = PaperAgent.load(path)
    ss.tools = ChatTools(agent, ss.uploads / "data")
    ss.tools.data_dir = data
    ss.engine = make_engine(agent, ss.tools)
    ss.engine_kind = engine_plan()[0]
    ss.run_name = None
    ss.highlight = None


def say(text: str) -> None:
    ss.history.append({"role": "assistant", "parts": [{"kind": "text", "text": text}]})


def build(trusted: bool) -> None:
    ro = ss.ro
    title = (ro.get("crate") or ro.get("paper") or {}).get("label", "research object")
    out = ROOT / "agents" / slug(title)
    label = ("Reading the RO-Crate, then reproducing each method…" if ro.get("crate") else
             "Looking for a published RO-Crate (WorkflowHub, Zenodo, DataCite, the paper's code); if there is none, "
             "recreating it as of publication…")
    with st.spinner(label):
        agentify(out, paper=(ro.get("paper") or {}).get("path"), crate=(ro.get("crate") or {}).get("path"),
                 repo=ro.get("repo"), validate="all" if trusted else "safe", paper_doi=paper_doi(ro.get("paper")))
    load_agent(out, Path(ro["data"]["path"]) if ro.get("data") else None)
    a = PaperAgent.load(out)
    env = a.environment
    search = a.manifest.get("crate_search")
    found = a.manifest.get("crate_found")
    search_line = ""
    if search:
        search_line = (f"\n\n🔎 **Found its RO-Crate** on {found['where']}: [{found['label']}]({found['url']}), "
                       f"{found['evidence']}." if found else
                       f"\n\n🔎 {search['summary']} So I recreated it as of publication.")
    lines = [f"- **{m.name}**: {m.status}" + (f". {m.detail}" if m.detail else "") for m in a.methods]
    env_line = ""
    if env:
        known = [s for s in env.software if s.version and s.how != "later"]
        env_line = (f"\n\nPublished **{env.published or 'on an unknown date'}**. {len(known)} of {len(env.software)} "
                    "software versions determined" + (": " + ", ".join(f"{s.name} {s.version}" for s in known[:5])
                                                      if known else "") + ".")
    ss._goto = "validate"
    say(f"{'Ready' if a.has_methods else 'Recreated the RO-Crate'}: **{a.card()['title']}**." + search_line + env_line
        + ("\n\n" + "\n".join(lines) if lines else "\n\nNo runnable method yet: see **Reproduce** for what's needed.")
        + "\n\nAsk me anything about it.")


def paper_doi(paper: dict | None) -> str | None:
    """The paper's own DOI, from how it was given (a DOI or an arXiv link); None for an upload."""
    url = (paper or {}).get("url") or ""
    for r in identify(url):
        if r.kind == "doi":
            return r.id
        if r.kind == "arxiv":
            return f"10.48550/arXiv.{r.id}"
    return None


def open_example(key: str) -> None:
    ex = EXAMPLES[key]
    ss.entering = True  # the workspace appears once complete, not piece by piece
    built = (ex["agent"] / "environment.json").exists()
    if built and ex["build"].get("paper") and "links" not in PaperAgent.load(ex["agent"]).manifest \
            and not os.environ.get("RECRATE_OFFLINE"):
        built = False  # built before ReCRATE looked for the paper's code and data: rebuild
    if built and any(not m.ladder for m in PaperAgent.load(ex["agent"]).methods):
        built = False  # built before the verdict ladder
    if built and ex["build"].get("crate"):
        from recrate.crate import list_methods, resolve_crate_dir

        if len(list_methods(resolve_crate_dir(ex["build"]["crate"]))) != len(PaperAgent.load(ex["agent"]).methods):
            built = False  # the example's crate gained (or lost) a method since it was built
    if not built:  # missing, or built by an older ReCRATE
        with st.spinner("Building the example: reading it, finding its code and data, reproducing what can run…"):
            build = dict(ex["build"])
            if ex.get("fetch_paper"):
                build["paper"] = fetch(identify(ex["fetch_paper"])[0], Path(ex["agent"]).parent / "paper-download").path
            agentify(ex["agent"], **build, validate="none" if ex.get("env") else "all")
    if ex.get("env") and not (Path(ex["agent"]) / "env" / "recrate-env.json").exists():
        from recrate.env import build_env

        with st.spinner("Installing the paper's software in its own environment (first time only)…"):
            build_env(ex["agent"], log=lambda *_: None)
        with st.spinner("Reproducing the paper's results…"):
            revalidate(ex["agent"])
    if ex.get("make_data") and not Path(ex["data"]).exists():  # derived from the example's own data, made once
        import subprocess

        subprocess.run([sys.executable, str(ex["make_data"])], check=True, capture_output=True)
    load_agent(ex["agent"], ex["data"])
    ss.ro = {"example": key}
    ss.scale_ratio = None
    ss._goto = "validate"
    a = PaperAgent.load(ex["agent"])
    say(f"Opened **{a.card()['title']}**. {a.card()['verdict']} "
        + ("Its example data is selected for **Reuse**. " if ex["data"] else "")
        + "Ask me anything about it.")


def add_sources(text: str, files: list) -> list[str]:
    """Turn what the person gave into parts of the research object. Returns lines to report."""
    out, dest = [], ss.uploads / f"src-{len(ss.history)}"
    for f in files:
        p = dest / "upload" / f.name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(f.getbuffer())
        out.append(record(from_upload(p, dest / slug(f.name))))
    for ref in identify(text or ""):
        try:
            with st.spinner(f"Fetching {ref.label}…"):
                found = fetch(ref, dest / slug(ref.label))
            if found.kind == "code":
                ss.ro["repo"] = found.url
                out.append(f"💻 **Code**: {found.url}")
            else:
                out.append(record(found))
        except Exception as e:  # noqa: BLE001 - network or format problems are reported, not raised
            out.append(f"⚠️ Couldn't fetch {ref.label}: {e}")
    return out


def record(found) -> str:
    icon = {"paper": "📄 **Paper**", "crate": "📦 **RO-Crate**", "data": "🗂️ **Data**"}.get(found.kind, "❔")
    if found.kind in {"paper", "crate", "data"} and found.path:
        ss.ro[found.kind] = {"label": found.label, "path": str(found.path), "url": found.url}
    notes = (" · " + " · ".join(found.notes)) if found.notes else ""
    link = f" ([link]({found.url}))" if found.url else ""
    return f"{icon}: {found.label}{link}{notes}"


def add_and_build(text: str, files: list) -> None:
    """Add what was given; as soon as there is a crate or a paper, build (one step for the person)."""
    lines = add_sources(text, files)
    if lines:
        say("\n".join(f"- {l}" for l in lines))
    if ss.ro.get("crate") or ss.ro.get("paper"):
        build(trusted=False)
    elif not lines:
        say("I didn't find an RO-Crate, a paper or an identifier in that. Try a DOI, a WorkflowHub or arXiv link, or "
            "upload a PDF or a crate zip.")


# ---- chat --------------------------------------------------------------------------------------
def render_attachment(a: dict, key: str) -> None:
    t = a["type"]
    if t == "passages":
        for h in a["items"][:2]:
            st.markdown(f"<div class='quote'>{html.escape(' '.join(h['text'].split())[:420])}…</div>",
                        unsafe_allow_html=True)
    elif t == "checks":
        st.markdown(f"**Input check: {ICON[a['status']]} {a['status']}**")
        for r in a["rows"]:
            if r["status"] != "ok" or r["note"]:
                st.caption(f"{ICON[r['status']]} {r['check']}: published {r['paper']} · yours {r['your data']}"
                           + (f" · {r['note']}" if r["note"] else ""))
        for adj in a["adjustments"]:
            st.caption(f"↳ {adj['parameter']} {adj['old']} → {adj['new']}: {adj['reason']}")
    elif t == "comparison":
        (st.success if a["ok"] else st.error)(a["headline"])
    elif t == "params":
        st.caption("Parameters: " + ", ".join(f"`{k} = {v}`" for k, v in a["params"].items()))
    elif t == "crate":
        z = Path(a["zip"])
        st.success(f"Output RO-Crate ready. {a['record']}")
        if z.exists():
            st.download_button("Download the RO-Crate (.zip)", z.read_bytes(), file_name=z.name,
                               mime="application/zip", key=f"dl-{key}", icon=":material/download:")
    elif t == "methods_text":
        st.code(a["text"], language=None, wrap_lines=True)
    elif t in {"record", "note"}:
        st.caption(a.get("headline") or a.get("text"))
    elif t == "error":
        st.error(a["text"])
    elif t == "figure":
        if Path(a["path"]).exists():
            st.image(a["path"], caption=short(a.get("caption", ""), 300), width="stretch")
    elif t == "table":
        st.dataframe(pd.DataFrame(a["rows"]), hide_index=True, width="stretch")


def render_message(msg: dict, idx) -> None:
    with st.container(key=f"msg-{'u' if msg['role'] == 'user' else 'a'}-{idx}"):
        for j, part in enumerate(msg["parts"]):
            if part["kind"] == "text":
                st.markdown(part["text"])
            elif part["kind"] == "tool":
                st.caption(chip(part["name"], part.get("args")))
            elif part["kind"] == "attachment":
                render_attachment(part["data"], f"{idx}-{j}")


def after_tool(name: str, result) -> None:
    for a in result.attachments:
        if a["type"] == "passages" and a["items"]:
            from recrate.workspace import quote_for

            terms = re.findall(r"[a-z]{4,}", ss.get("last_prompt", "").lower())
            ss.highlight = quote_for(a["items"][0]["text"], terms) or " ".join(a["items"][0]["text"].split())[:200]
    if name in {"reproduce", "run_on_my_data"} and ss.tools.last_run is not None:
        ss.run_name = ss.tools.last_run.name


INTRO = ("Hi! Give me a piece of research and I'll recreate it, so you can **reproduce** it on its own data or "
         "**reuse** its methods on yours.\n\n"
         "- a research **RO-Crate**: a zip, a WorkflowHub link or DOI, a Zenodo DOI\n"
         "- or a **paper**: a PDF, a DOI or an arXiv link. I'll recreate its RO-Crate with the software as it was "
         "when it was published\n\n"
         "Paste a link below, or use the panel on the right.")


def context_card(agent: PaperAgent) -> None:
    c = agent.card()
    n_ok = sum(1 for m in agent.methods if m.status == "validated")
    meta = " · ".join(x for x in [c.get("published") and f"published {c['published']}",
                                  f"{n_ok}/{len(agent.methods)} reproduced" if agent.methods else "no runnable method yet",
                                  c.get("crate_source")] if x)
    about = short(agent.methods[0].description, 400) if agent.methods and agent.methods[0].description else ""
    src = f"<p><a href='{html.escape(c['doi'])}' style='color:#9fd3c4'>{html.escape(c['doi'])}</a></p>" if c.get("doi") else ""
    st.markdown(f"<div class='ctx'><div class='eyebrow'>Research object</div><div class='t'>{html.escape(c['title'])}"
                f"</div><div class='m'>{html.escape(meta)}</div>"
                + (f"<details><summary>Show more ▾</summary><p>{html.escape(about)}</p>{src}</details>" if about or src else "")
                + "</div>", unsafe_allow_html=True)


def asked_questions() -> set[str]:
    return {m["parts"][0]["text"] for m in ss.history if m["role"] == "user" and m.get("parts")}


def chat_started() -> bool:
    """Whether the person has asked anything since this research object was opened."""
    return any(m["role"] == "user" for m in ss.history[ss.get("chat_mark", 0):])


def question_buttons(qs: list[str], key: str, title: str) -> None:
    if not qs:
        return
    with st.container(key=f"suggest-{key}"):
        st.markdown(f"<div class='eyebrow'>{title}</div>", unsafe_allow_html=True)
        for i, q in enumerate(qs):
            if st.button(q, key=f"suggest-{key}-{i}-{abs(hash(q)) % 10**6}", icon=":material/chat_bubble:"):
                ss.picked_question = q
                st.rerun()


def suggestions(agent: PaperAgent) -> None:
    """Before the conversation: questions to start from, chosen from what this research object contains. Once it has
    started: two questions that follow from the last answer, under it."""
    asked = asked_questions()
    if not chat_started():
        question_buttons([q for q in suggested_questions(agent, limit=8) if q not in asked][:4], "start", "Try asking")
    elif ss.history and ss.history[-1]["role"] == "assistant":
        question_buttons(follow_up_questions(agent, ss.history[-1]["parts"], asked), "next", "Ask next")


def chat_card() -> None:
    kind, label = engine_plan()
    color = {"subscription": "#d97757", "claude": "#d97757", "local": "#2A78D6", "commands": "#b8b3a7"}[kind]
    h1, h2 = st.columns([1, 1.4], vertical_alignment="center")
    h1.markdown("**Chat**")
    h2.markdown(f"<div style='text-align:right'><span class='chip'><span class='dot' style='background:{color}'></span>"
                f"{html.escape(label)}</span></div>", unsafe_allow_html=True)
    has_agent = bool(ss.get("agent_dir"))
    if has_agent and ss.get("engine_kind") != kind:  # the model choice changed: switch engines, keep the transcript
        ss.engine = make_engine(PaperAgent.load(ss.agent_dir), ss.tools)
        ss.engine_kind = kind
    box = st.container(key="chatscroll")
    with box:
        if has_agent:
            context_card(PaperAgent.load(ss.agent_dir))
        if not ss.history:
            render_message({"role": "assistant", "parts": [{"kind": "text", "text": INTRO}]}, "intro")
        for i, msg in enumerate(ss.history):
            render_message(msg, i)
        if has_agent:
            suggestions(PaperAgent.load(ss.agent_dir))
    entry = st.chat_input("Ask about the research, or attach your data…" if has_agent else
                          "Paste a DOI or link, or attach a paper or crate…", accept_file="multiple")
    st.markdown("<div class='muted' style='font-size:.72rem;margin-top:-.2rem'>Enter to send · Shift+Enter for a new "
                "line</div>", unsafe_allow_html=True)
    picked = ss.pop("picked_question", None)
    if not entry and not picked:
        return
    text, files = (entry.text or "", list(entry.files or [])) if entry else (picked, [])
    ss.last_prompt = text
    shown = text + (("\n\n📎 " + ", ".join(f.name for f in files)) if files else "")
    ss.history.append({"role": "user", "parts": [{"kind": "text", "text": shown or "(files)"}]})

    if not has_agent or (identify(text) and not files):
        add_and_build(text, files)
        st.rerun()

    sent = text
    if files:  # attached files become your data
        d = ss.uploads / "data"
        d.mkdir(parents=True, exist_ok=True)
        for f in files:
            (d / f.name).write_bytes(f.getbuffer())
        ss.tools.data_dir = None
        sent = (text or "I added my data.") + f"\n\n[The user attached: {', '.join(f.name for f in files)}. " \
            "Tools use them when data_path is omitted.]"
    if ss.context_notes:
        sent = "[Done in the workstation since my last message: " + "; ".join(ss.context_notes) + "]\n\n" + sent
        ss.context_notes = []
    reply = {"role": "assistant", "parts": []}
    idx = len(ss.history)
    with box:
        render_message(ss.history[-1], idx - 1)
        with st.container(key=f"msg-a-live-{idx}"):
            bar = st.empty()
            ss.tools.progress = lambda i, n, name: bar.progress(i / max(n, 1), text=f"{name} ({i + 1}/{n})")
            try:
                with st.spinner("Thinking…"):
                    for ev in ss.engine.send(sent):
                        if ev[0] == "text":
                            reply["parts"].append({"kind": "text", "text": ev[1]})
                            st.markdown(ev[1])
                        elif ev[0] == "tool":
                            reply["parts"].append({"kind": "tool", "name": ev[1], "args": ev[2]})
                            st.caption(chip(ev[1], ev[2]))
                        elif ev[0] == "result":
                            after_tool(ev[1], ev[2])
                            for a in ev[2].attachments:
                                j = len(reply["parts"])
                                reply["parts"].append({"kind": "attachment", "data": a})
                                render_attachment(a, f"live-{idx}-{j}")
            except Exception as e:  # noqa: BLE001 - connection or model errors are shown, not raised
                msg = f"Something went wrong: {type(e).__name__}: {e}"
                reply["parts"].append({"kind": "attachment", "data": {"type": "error", "text": msg}})
                st.error(msg)
            finally:
                ss.tools.progress = None
                bar.empty()
    ss.history.append(reply)
    st.rerun()  # show the reply in place, with the questions that follow from it
    st.rerun()


# ---- workstation helpers ----------------------------------------------------------------------------
def data_signature(files: list[Path]) -> tuple:
    return tuple((p.name, p.stat().st_mtime_ns) for p in files)


@st.cache_data(show_spinner=False)
def cached_check(agent_dir: str, method: str, data: str, sig: tuple, scale_ratio: float | None = None) -> dict:
    from recrate.model import parameter_plan

    a = PaperAgent.load(agent_dir)
    try:
        spec = a.spec(method)
        r = check_inputs(spec, Path(data), scale_ratio=scale_ratio)
    except CrateError as e:
        return {"status": "fail", "can_run": False, "adjustments": [], "plan": None,
                "rows": [{"check": "Method inputs", "paper": "not declared", "your data": "-", "status": "fail",
                          "note": f"{e} The crate doesn't say what data this method expects."}]}
    return {"status": r.status, "can_run": r.can_run, "rows": r.as_rows(),
            "adjustments": [a.__dict__ for a in r.adjustments], "plan": parameter_plan(spec, r)}


def note_for_chat(text: str) -> None:
    ss.history.append({"role": "assistant", "parts": [{"kind": "text", "text": text}]})
    ss.context_notes.append(text)


def run_with_progress(label: str, fn):
    bar = st.progress(0.0, text=label)
    ss.tools.progress = lambda i, n, name: bar.progress(i / max(n, 1), text=f"{label}: {name} ({i + 1}/{n})")
    try:
        return fn()
    finally:
        ss.tools.progress = None
        bar.empty()


def latest(runs: list[dict], method: str, mode: str) -> Path | None:
    hits = [r for r in runs if r["method"] == method and r["mode"] == mode]
    return Path(hits[-1]["path"]) if hits else None


def run_inputs(run_dir: Path, data_dir: Path) -> Path:
    d = run_dir / "inputs"
    return d if d.exists() and any(d.iterdir()) else data_dir


def overlay_source(overlay: Path, run_dir: Path, data_dir: Path) -> Path | None:
    stem = overlay.name.removesuffix("_overlay.png")
    for base in (run_dir / "inputs", data_dir, run_dir / "inputs" / "images", data_dir / "images"):
        for suf in (".tif", ".tiff", ".png"):
            if (base / f"{stem}{suf}").exists():
                return base / f"{stem}{suf}"
    return None


def source_view(agent: PaperAgent) -> None:
    """The paper as ReCRATE read it: its text, and (from a reading package) its tables, figures and supplement."""
    tables, figures = agent.tables(), agent.figures()
    supp = sorted(p for p in (agent.path / "paper" / "supplement").glob("*") if p.is_file()) \
        if (agent.path / "paper" / "supplement").is_dir() else []
    if not (tables or figures or supp):
        source_text(agent)
        return
    names = ["Text"] + [f"{n} ({len(x)})" for n, x in [("Tables", tables), ("Figures", figures), ("Supplement", supp)] if x]
    tabs = dict(zip([n.split(" ")[0] for n in names], st.tabs(names)))
    with tabs["Text"]:
        source_text(agent)
    if tables:
        with tabs["Tables"]:
            pick = st.selectbox("Table", [t["label"] for t in tables], key="src-table", label_visibility="collapsed")
            t = next(x for x in tables if x["label"] == pick)
            if t["caption"]:
                st.caption(t["caption"])
            st.dataframe(pd.read_csv(agent.path / "paper" / t["file"], dtype=str, keep_default_na=False),
                         hide_index=True, width="stretch")
    if figures:
        with tabs["Figures"]:
            pick = st.selectbox("Figure", [f["label"] for f in figures], key="src-figure", label_visibility="collapsed")
            f = next(x for x in figures if x["label"] == pick)
            st.image(str(agent.path / "paper" / f["file"]), width="stretch")
            if f["caption"]:
                st.caption(f["caption"])
    if supp:
        with tabs["Supplement"]:
            for i, p in enumerate(supp):
                st.download_button(p.name, p.read_bytes(), file_name=p.name, key=f"supp-{i}",
                                   icon=":material/attach_file:")


def source_text(agent: PaperAgent) -> None:
    if not agent.paper:
        st.caption("No paper text in this research object.")
        return
    hl = ss.get("highlight")
    key = " ".join(hl.split())[:60] if hl else None
    with st.container(height=360, border=False, key="sourcebox"):
        for p in passages(agent.paper.text):
            flat = " ".join(p.split())
            if key and key in flat:
                s = html.escape(" ".join(hl.split()))
                st.markdown(html.escape(flat).replace(s, f"<mark class=cite>{s}</mark>"), unsafe_allow_html=True)
            else:
                st.markdown(p)


def environment_table(agent: PaperAgent) -> None:
    env = agent.environment
    if env is None or not env.software:
        st.caption("No software named in the sources.")
        return
    st.dataframe(pd.DataFrame([{"software": s.name, "version": s.version or "?", "how we know": s.note}
                               for s in env.software]), hide_index=True, width="stretch",
                 column_config={"how we know": st.column_config.TextColumn(width="large")})
    req = agent.crate_dir / "environment" / "requirements.txt" if agent.crate_dir else None
    if req and req.exists() and "==" in req.read_text(encoding="utf-8"):
        st.download_button("requirements.txt (pinned as of publication)", req.read_bytes(), file_name="requirements.txt",
                           icon=":material/download:")


# ---- results, review and figures (shared by Reproduce and Reuse) ----------------------------
def results_tab(agent: PaperAgent, run_dir: Path | None, data_dir: Path) -> None:
    if run_dir is None:
        st.caption("No runs yet: reproduce a method or run it on your data.")
        return
    s = summarise_run(run_dir)
    spec = agent.spec(s["method"])
    rows = read_rows(run_dir / "outputs" / "results.csv")
    reported = read_rows(spec.expected_results) if spec.expected_results and spec.expected_results.exists() else None
    w = ss.tools.whatifs.get(run_dir.name)
    changed: list[str] = []
    if s["mode"] == "reproduce":
        (st.success if s["verdict"] == "reproduced" else st.error)(s["headline"])
        other, lab1, lab2, ref_rows = reported, "This run", "Published", None
    else:
        same, why = comparable_with_paper(spec, run_inputs(run_dir, data_dir))
        keyed = bool(reported and rows) and not w and "image" not in rows[0] and \
            [list(r.values())[0] for r in reported] == [list(r.values())[0] for r in rows]  # same rows (e.g. IoU)
        if not same:
            st.caption(f"ℹ️ {why}" + (" Shown beside yours: the paper's values on its own data, to see how the "
                                      "method transfers." if keyed else ""))
            reported = reported if keyed else None
        used = {p["name"]: p["value"] for p in run_story(run_dir)["parameters"]}
        changed = [f"{k} {v}" for k, v in used.items() if k in spec.default_params() and str(spec.default_params()[k]) != v]
        lab1 = "Your run" + (f" ({', '.join(changed)})" if changed else "")
        lab2 = "Published values unchanged (" + ", ".join(f"{k} {v}" for k, v in spec.default_params().items()
                                                         if str(v) != used.get(k)) + ")"
        other, ref_rows = (w["rows"] if w else None), reported
        if keyed:
            other, ref_rows, lab2 = reported, None, "The paper's data (published)"
    metrics = numeric_columns(rows)
    if not metrics:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        return
    metric = st.segmented_control("Measure", metrics, default=metrics[0], key=f"metric-{s['run']}",
                                  label_visibility="collapsed") or metrics[0]
    if s["mode"] != "reproduce" and not other and rows and f"{metric}_true" in rows[0]:  # ground truth in your data
        other, lab2 = [{**r, metric: r[f"{metric}_true"]} for r in rows], "Ground truth (in your data)"
    has = lambda r: r.get(metric) not in (None, "")  # noqa: E731
    if other and not any(has(r) for r in other):  # e.g. the paper publishes the count, not the mean area
        other = None
    if ref_rows and not any(has(r) for r in ref_rows):
        ref_rows = None
    mean = lambda rr: sum(float(r[metric]) for r in rr if has(r)) / max(1, sum(1 for r in rr if has(r)))  # noqa: E731
    c = st.columns(3)
    c[0].metric("This run" if s["mode"] == "reproduce" else "Your run", f"{mean(rows):.4g}", border=True, help=lab1)
    if s["mode"] == "reproduce" and other:
        c[1].metric("Published", f"{mean(other):.4g}", border=True)
    elif other:
        c[1].metric(lab2.split(" (")[0] if lab2.startswith(("The paper's data", "Ground truth")) else "Published values",
                    f"{mean(other):.4g}", delta=f"{mean(other) - mean(rows):+.3g}",
                    delta_color="off", border=True, help=lab2)
    if ref_rows:
        c[2].metric("Published data", f"{mean(ref_rows):.4g}", border=True, help="Reported on the published data")
    df = pd.DataFrame([{"item": r.get("image") or r[list(r)[0]], "value": float(r[metric]), "series": lab1}
                       for r in rows if has(r)])
    if other:
        df = pd.concat([df, pd.DataFrame([{"item": r.get("image") or r[list(r)[0]], "value": float(r[metric]),
                                           "series": lab2} for r in other if r.get(metric)])])
    domain = [lab1] + ([lab2] if other else [])
    pts = alt.Chart(df).mark_circle(size=110, opacity=1).encode(
        x=alt.X("item:N", title=None, axis=alt.Axis(labelAngle=0, labelLimit=120)),
        xOffset=alt.XOffset("series:N", scale=alt.Scale(domain=domain)),
        y=alt.Y("value:Q", title=metric.replace("_", " "), scale=alt.Scale(zero=False), axis=alt.Axis(format=",.4~r")),
        color=alt.Color("series:N", scale=alt.Scale(domain=domain, range=SERIES[:len(domain)]),
                        legend=alt.Legend(orient="bottom", direction="vertical", title=None, labelLimit=0)),
        tooltip=["item", "series", alt.Tooltip("value:Q", format=".4g")])
    layers, rng = [pts], None
    vals = [float(r[metric]) for r in (ref_rows or []) if r.get(metric)]
    if vals:
        rng = (min(vals), max(vals))
        ref = pd.DataFrame({"lo": [rng[0]], "hi": [rng[1]], "label": ["published range" if rng[1] > rng[0] else "published"]})
        mark = (alt.Chart(ref).mark_rect(color="#8a8985", opacity=0.18).encode(y="lo:Q", y2="hi:Q") if rng[1] > rng[0]
                else alt.Chart(ref).mark_rule(color="#8a8985", strokeDash=[4, 3], strokeWidth=1.5).encode(y="lo:Q"))
        lbl = alt.Chart(ref).mark_text(align="left", dx=4, dy=-7, color="#8a8985", fontSize=11).encode(
            y="hi:Q", x=alt.value(0), text="label:N")
        layers = [mark, lbl, pts]
    st.altair_chart(alt.layer(*layers).properties(height=250), width="stretch")
    if s["mode"] != "reproduce" and not other and changed:
        if st.button("Compare with the published values unchanged", key=f"cmp-{s['run']}"):
            ss.tools.whatifs[s["run"]] = run_with_progress("Running with the published values", lambda: whatif(
                spec, run_inputs(run_dir, data_dir), spec.default_params(), runner=runner))
            st.rerun()
    c1, c2 = st.columns(2)
    item = lambda r: r.get("image") or r[list(r)[0]]  # noqa: E731
    mine = [r for r in rows if has(r)]
    theirs = {item(r): float(r[metric]) for r in (other or []) if has(r)}
    paired = bool(theirs) and all(item(r) in theirs for r in mine)  # the chart pairs values item by item
    chart_png = comparison_chart([item(r) for r in mine], [float(r[metric]) for r in mine],
                                 [theirs[item(r)] for r in mine] if paired else None, metric, lab1, lab2, rng)
    c1.download_button("Chart (PNG)", chart_png, file_name=f"{s['run']}-{metric}.png", mime="image/png",
                       width="stretch", icon=":material/download:", key=f"chartdl-{s['run']}-{metric}")
    if c2.button("Add chart to the export", width="stretch", key=f"addchart-{s['run']}-{metric}"):
        ss.figures[f"{s['run']}-{metric}.png"] = chart_png
        st.toast("Added to the export bundle.")
    ovs = sorted((run_dir / "outputs" / "overlays").glob("*.png"))
    if ovs:  # what the method found, on each image: the first thing to look at
        st.markdown("**Segmentation on " + ("your images" if s["mode"] != "reproduce" else "the published images") + "**")
        by = {Path(r.get("image", "")).stem: r for r in rows}
        for i in range(0, min(len(ovs), 6), 3):
            for col, ov in zip(st.columns(3), ovs[i:i + 3]):
                stem = ov.name.removesuffix("_overlay.png")
                r = by.get(stem, {})
                cap = f"{stem[:28]}" + (f" · {r[metric]} {metric.replace('_', ' ')}" if r.get(metric) else "") + \
                    (f" (truth {r[metric + '_true']})" if r.get(f"{metric}_true") else "")
                col.image(str(ov), caption=cap, width="stretch")
        if len(ovs) > 6:
            st.caption(f"First 6 of {len(ovs)} images; all of them under *Review each result*.")
    from recrate.paper_figures import figure_label

    drawn = sorted(f for f in (run_dir / "outputs" / "figures").glob("*")  # the figures the method drew in this run
                   if f.suffix.lower() in IMAGE_EXT and f.stem != "published_vs_reproduced") \
        if (run_dir / "outputs" / "figures").is_dir() else []
    if drawn:
        where = "your data" if s["mode"] != "reproduce" else "the published data"
        st.markdown(f"**Figures drawn from {where}**")
        for f in drawn:
            label = figure_label(f.name)
            st.image(str(f), width="stretch",
                     caption=(f"The paper's {label}, drawn by the same method from {where}" if label
                              else f"{f.stem}, drawn from {where}"))
    with st.expander("Table"):
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    paper_ovs = paper_overlays(agent, s) if ovs and s["mode"] != "reproduce" else []
    if paper_ovs:
        with st.expander("The paper's data next to yours", expanded=True):
            side_by_side(paper_ovs, ovs, rows, s["run"])
    if ovs:
        with st.expander(f"Review each result ({len(ovs)})"):
            review(agent, run_dir, ovs, rows, spec, data_dir)
        with st.expander("Make a figure"):
            figure_builder(agent, run_dir, data_dir)


def paper_overlays(agent: PaperAgent, s: dict) -> list[Path]:
    """The segmentations of the latest reproduction of this method, on the paper's own data."""
    for d in sorted((p for p in agent.runs_dir.glob("*") if p.is_dir()), reverse=True):
        if d.name.endswith("-reuse") or not (d / "outputs" / "overlays").is_dir():
            continue
        r = summarise_run(d)
        if r["mode"] == "reproduce" and r["method"] == s["method"]:
            return sorted((d / "outputs" / "overlays").glob("*.png"))
    return []


def side_by_side(paper: list[Path], yours: list[Path], rows: list[dict], run: str) -> None:
    """One image of the paper's data and one of yours, segmented by the same method with the same settings."""
    name = lambda o: o.name.removesuffix("_overlay.png")  # noqa: E731
    c1, c2 = st.columns(2)
    a = c1.selectbox("The paper's data", paper, format_func=name, key=f"sbs-p-{run}")
    b = c2.selectbox("Your data", yours, format_func=name, key=f"sbs-y-{run}")
    c1.image(str(a), width="stretch")
    c2.image(str(b), width="stretch")
    row = next((r for r in rows if Path(r.get("image") or "").stem == name(b)), None)
    if row:
        c2.caption(" · ".join(f"{k.replace('_', ' ')} {v}" for k, v in row.items() if k != "image"))
    st.caption("Outlines: the nuclei the method found. Same model and thresholds on both sides; look for merged or "
               "split nuclei, and for missed dim ones.")


def review(agent, run_dir: Path, ovs: list[Path], rows: list[dict], spec, data_dir: Path) -> None:
    s = summarise_run(run_dir)
    by = {r.get("image"): r for r in rows}
    metrics = numeric_columns(rows)
    reported = read_rows(spec.expected_results) if s["mode"] == "reuse" and spec.expected_results and \
        spec.expected_results.exists() and comparable_with_paper(spec, run_inputs(run_dir, data_dir))[0] else None
    flags = flag_images(rows, metrics[0], reported, spec.tolerances().get(metrics[0], 0.1)) if metrics else {}
    reviews = image_reviews(run_dir)
    st.caption(f"{len(reviews)} of {len(ovs)} reviewed · {sum(1 for v in flags.values() if v)} flagged · whole run: "
               f"{s['approved'] or 'not reviewed'}")
    c1, c2 = st.columns(2)
    if c1.button("Whole run looks right", key=f"wok-{s['run']}", width="stretch", icon=":material/thumb_up:"):
        approve(run_dir, ss.tools.who, True)
        st.rerun()
    if c2.button("Something's wrong", key=f"wno-{s['run']}", width="stretch", icon=":material/thumb_down:"):
        approve(run_dir, ss.tools.who, False)
        st.rerun()
    order = sorted(ovs, key=lambda o: (not flags.get(o.name.replace("_overlay.png", ".tif")), o.name))
    grid = st.columns(3)
    for k, ov in enumerate(order):
        stem = ov.name.removesuffix("_overlay.png")
        img = next((n for n in by if n and Path(n).stem == stem), f"{stem}.tif")
        with grid[k % 3].container(border=True):
            st.image(str(ov), width="stretch")
            st.caption(f"**{stem}** · " + " · ".join(f"{m} {float(by[img][m]):.3g}" for m in metrics[:2] if img in by))
            for f in flags.get(img, []):
                st.caption(f"⚠️ {f}")
            rv = reviews.get(img)
            if rv:
                st.caption(("✅ accepted" if rv["value"] == "accepted" else "❌ rejected") + f" by {rv['by']}"
                           + (f": {rv['note']}" if rv["note"] else ""))
            b1, b2 = st.columns(2)
            if b1.button("Right", key=f"iok-{s['run']}-{stem}", width="stretch", icon=":material/thumb_up:"):
                review_image(run_dir, ss.tools.who, img, True)
                st.rerun()
            if b2.button("Wrong", key=f"ino-{s['run']}-{stem}", width="stretch", icon=":material/thumb_down:"):
                review_image(run_dir, ss.tools.who, img, False)
                st.rerun()


def figure_builder(agent: PaperAgent, run_dir: Path, data_dir: Path) -> None:
    srcs: dict[str, tuple[str, float | None]] = {}
    for f in list_images(data_dir)[:24] if data_dir.exists() else []:
        srcs[f"Your image · {f.stem}"] = (str(f), None)
    for ov in sorted((run_dir / "outputs" / "overlays").glob("*.png")):
        src = overlay_source(ov, run_dir, data_dir)
        srcs[f"Detected · {ov.stem.removesuffix('_overlay')}"] = (str(ov), pixel_size_um(src) if src else None)
    for stem, p in (ss.tools.whatifs.get(run_dir.name) or {}).get("overlays", {}).items():
        src = overlay_source(Path(p), run_dir, data_dir)
        srcs[f"Detected, published values · {stem}"] = (p, pixel_size_um(src) if src else None)
    for m in agent.methods:
        spec = agent.spec(m.id)
        if spec.example_data and spec.example_data.exists():
            for f in list_images(spec.example_data)[:6]:
                srcs[f"Published image · {f.stem}"] = (str(f), None)
            break
    labels, k = list(srcs), run_dir.name
    if st.button("Preset: published image vs yours, same scale", key=f"fig-preset-{k}"):
        ss[f"fig_pick-{k}"] = [l for l in [next((l for l in labels if l.startswith(p)), None) for p in
                                           ("Published image", "Your image", "Detected ·", "Detected, published")] if l]
        ss[f"fig_same-{k}"] = True
    chosen = st.multiselect("Panels, in order", labels, key=f"fig_pick-{k}", max_selections=12)
    if not chosen:
        return
    c1, c2, c3, c4 = st.columns(4)
    cols = c1.number_input("Columns", 1, 4, value=min(len(chosen), 2), key=f"fig-cols-{k}")
    fmt = c2.selectbox("Format", ["png", "pdf", "svg"], key=f"fig-fmt-{k}")
    same = c3.toggle("Same scale", key=f"fig_same-{k}", help="Resample to one µm per pixel")
    bar = c4.number_input("Scale bar µm", min_value=0.0, value=0.0, step=5.0, help="0 = automatic", key=f"fig-bar-{k}")
    panels = [Panel(srcs[l][0], l.split(" · ")[0], srcs[l][1]) for l in chosen]
    kw = dict(cols=int(cols), width_in=7.0, scale_bar_um=bar or None, same_scale=same, letters=True)
    try:
        preview, notes = panel_figure(panels, fmt="png", dpi=100, **kw)
    except Exception as e:  # noqa: BLE001
        st.error(f"Couldn't build the figure: {e}")
        return
    st.image(preview, width="stretch")
    st.caption(" ".join(notes))
    data, _ = panel_figure(panels, fmt=fmt, dpi=300, **kw)
    name = f"figure-{len(ss.figures) + 1}.{fmt}"
    d1, d2 = st.columns(2)
    d1.download_button(f"Figure ({fmt.upper()})", data, file_name=name, width="stretch", icon=":material/download:",
                       key=f"fig-dl-{k}",
                       mime={"png": "image/png", "pdf": "application/pdf", "svg": "image/svg+xml"}[fmt])
    if d2.button("Add to the export", width="stretch", key=f"fig-add-{k}"):
        ss.figures[name] = data
        st.toast(f"{name} added to the export bundle.")


# ---- Reproduce: does the research reproduce on its own data? ------------------------------------------
def validate_section(agent: PaperAgent, runs: list[dict], data_dir: Path) -> None:
    """One answer first (does it reproduce?), then one card per method with only what needs attention; the rest is
    folded away."""
    env = agent.environment
    if not agent.methods:
        empty_validate(agent)
    else:
        verdict(agent)
        for m in agent.methods:
            method_card(agent, m, runs, data_dir)
    st.markdown("<div class='eyebrow' style='margin-top:.8rem'>Details</div>", unsafe_allow_html=True)
    if agent.methods:
        from recrate.model import assumptions

        said = list(dict.fromkeys(a for m in agent.methods for a in assumptions(agent.spec(m.id), env)))
        gaps = list(dict.fromkeys(g for m in agent.methods if m.readiness != "blocked" for g in m.gaps))
        if said or gaps:
            with st.expander(f"What the paper doesn't say ({len(said) + len(gaps)})", icon=":material/help:"):
                st.caption("Each is an assumption ReCRATE, or whoever drafted the crate, had to make.")
                st.markdown("\n".join(f"- {html.escape(a)}" for a in gaps + said))
    with st.expander("Software environment" + (f" · as of {env.published}" if env and env.published else ""),
                     icon=":material/inventory_2:"):
        if env and env.published:
            st.caption(f"Publication date from {env.published_source}.")
        environment_table(agent)
    with st.expander("Read the source" + (" · cited passage highlighted" if ss.get("highlight") else ""),
                     expanded=bool(ss.get("highlight")), icon=":material/article:"):
        source_view(agent)


STEP_MARK = {"passed": "✓", "partial": "◐", "failed": "✗", "not checked": "○"}


def verdict(agent: PaperAgent) -> None:
    """The verdict sentence, written from the results (never by the model)."""
    ladders = [m.ladder for m in agent.methods if m.ladder]
    numbers = [lad["numbers"]["status"] for lad in ladders]
    show = (st.success if numbers and all(n == "passed" for n in numbers) else
            st.warning if any(n in ("partial", "failed") for n in numbers) else
            st.error if any(lad["runs"]["status"] == "failed" for lad in ladders) else st.info)
    show(f"**{agent.card()['verdict']}**")


def method_card(agent: PaperAgent, m, runs: list[dict], data_dir: Path) -> None:
    from recrate.figures import comparison_from_run, make_run_figure
    from recrate.model import STEPS

    spec = agent.spec(m.id)
    run_dir = Path(m.run_crate) if m.run_crate and Path(m.run_crate).exists() else None
    comp = comparison_from_run(run_dir) if run_dir else None
    with st.container(border=True):
        st.markdown(f"**{m.name}** &nbsp; {BADGE.get(m.status, m.status)}")
        if m.ladder:  # where it stands, in one line: materials · runs · same numbers · same conclusion
            st.markdown(" &nbsp;·&nbsp; ".join(
                f"<span title='{html.escape(m.ladder.get(k, {}).get('reason', ''))}'>"
                f"{STEP_MARK.get(m.ladder.get(k, {}).get('status', 'not checked'), '○')} {label}</span>"
                for k, label in STEPS), unsafe_allow_html=True)
        if m.readiness == "blocked":
            for g in m.gaps:
                st.caption(f"⛔ {g}")
        if comp and comp.measures:
            bad = [x for x in comp.measures if not x.within]
            if bad:
                st.markdown(f"**{len(bad)} of {len(comp.measures)} values differ from the paper:**")
                st.dataframe(pd.DataFrame([{"value": x.measure, "published": x.reported, "recomputed": x.reproduced,
                                            "tolerance": x.tolerance_label} for x in bad]),
                             hide_index=True, width="stretch")
            else:
                st.markdown(f"All **{len(comp.measures)}** published values reproduce, within the paper's tolerance.")
        elif m.detail:
            st.markdown(f"<span class='muted'>{html.escape(m.detail)}</span>", unsafe_allow_html=True)
        if comp and comp.measures:
            with st.expander(f"All {len(comp.measures)} values"):
                st.dataframe(pd.DataFrame([{"value": x.measure, "published": x.reported, "recomputed": x.reproduced,
                                            "difference": f"±{x.rel_diff:.3g}" if x.absolute else f"{x.rel_diff:.1%}",
                                            "tolerance (source)": x.tolerance_label,
                                            "within": "yes" if x.within else "no"}
                                           for x in sorted(comp.measures, key=lambda x: x.within)]),
                             hide_index=True, width="stretch")
        if run_dir:
            figures_block(spec, run_dir, comp)
            paper_figures_view(agent, m, run_dir)
        if m.status == "differs":
            with st.expander("Where could the difference come from?"):
                followups_view(agent, m, spec)
        run = latest(runs, m.name, "reproduce")
        if run and st.toggle("Show the outputs of the latest reproduction", key=f"showrep-{m.id}"):
            results_tab(agent, run, data_dir)  # has its own expanders, which can't sit inside another
        if run_dir:
            conclusion_review(agent, m)


IMAGE_EXT = {".png", ".jpg", ".jpeg"}


def figures_block(spec, run_dir: Path, comp) -> None:
    """The reproduction as pictures: what the method found on the published data (its segmentation drawn on the
    image) next to the published picture when the research has one, and every published value next to the
    recomputed one."""
    from recrate.figures import make_run_figure

    published = sorted(p for p in (spec.path / "expected" / "figures").glob("*") if p.suffix.lower() in IMAGE_EXT) \
        if (spec.path / "expected" / "figures").is_dir() else []
    overlays = sorted((run_dir / "outputs" / "overlays").glob("*.png"))
    plot = run_dir / "outputs" / "figures" / "published_vs_reproduced.png"
    if comp and len(comp.measures) > 1 and not plot.exists():
        try:  # drawn from the run's recorded comparison, and recorded in its run crate
            make_run_figure(run_dir)
        except Exception:  # noqa: BLE001 - no figure is better than a broken page
            pass
    if not (published or overlays or plot.exists()):
        return
    st.markdown("**Figures**")
    if published or overlays:
        shown = ([(p, "Published (" + p.stem.replace("_", " ") + ")") for p in published[:2]]
                 + [(o, "Reproduced: " + o.name.removesuffix("_overlay.png")) for o in overlays[:max(1, 3 - len(published[:2]))]])
        for col, (img, cap) in zip(st.columns(len(shown)), shown):
            col.image(str(img), caption=cap, width="stretch")
        if overlays:
            st.caption("Reproduced images show what the method found, outlined on the published data.")
    if plot.exists():
        st.image(str(plot), caption="Each published value next to the recomputed one (from this run's outputs)",
                 width="stretch")


def paper_figures_view(agent: PaperAgent, m, run_dir: Path) -> None:
    """The paper's figures this method recreated, with the paper's captions. A person says whether they show the same
    thing as the paper's (recorded in the run crate): ReCRATE never decides that itself."""
    from recrate.paper_figures import VERDICTS, recreated, review_figure

    figs = recreated(run_dir, agent.path / "paper")
    if not figs:
        return
    done = sum(1 for f in figs if f["review"])
    with st.expander(f"The paper's figures, recreated ({len(figs)}) · {done} reviewed", icon=":material/compare:",
                     expanded=not done):
        st.caption("Redrawn by this run from the published data, as each caption describes. Does it show the same "
                   "thing as the paper's figure? Only a person can say, and it is recorded in the run crate.")
        for f in figs:
            st.markdown(f"**{f['label']}**" + (f" <span class='muted'>· {html.escape(short(f['caption'], 160))}</span>"
                                               if f["caption"] else ""), unsafe_allow_html=True)
            st.image(str(f["recreated"]), width="stretch")
            r = f["review"]
            if r:
                st.caption(f"{ {'same': '✅', 'differs': '⚠️'}.get(r['value'], '❔')} {r['value']}, by {r['by']}"
                           + (f": {r['note']}" if r["note"] else ""))
            else:
                k = f"figrev-{run_dir.name}-{f['recreated'].name}"
                a1, a2, a3 = st.columns([1.6, 2.2, 0.7], vertical_alignment="bottom")
                verdict = a1.segmented_control("Same figure?", VERDICTS, key=f"{k}-v")
                note = a2.text_input("What you see (recorded)", key=f"{k}-n",
                                     placeholder="e.g. same pattern; the paper also draws an unexplained inner bar")
                if a3.button("Record", key=f"{k}-b", disabled=not verdict or ss.tools.who == "anonymous",
                             help="Set your name in the top bar first" if ss.tools.who == "anonymous" else None):
                    review_figure(run_dir, ss.tools.who, f["recreated"].name, verdict, note)
                    note_for_chat(f"{ss.tools.who} reviewed the recreated {f['label']} of {m.name}: {verdict}.")
                    st.rerun()


def followups_view(agent: PaperAgent, m, spec) -> None:
    """When numbers differ: the follow-up checks, each its own recorded run. All of them stay visible."""
    from recrate.followup import followups_for, propose, run_followup

    st.caption("Each check is its own run, linked to this one; the result above is never replaced.")
    done = followups_for(agent.runs_dir, spec)
    for d in done:
        st.caption(f"✔ {d['followup']}: {d['headline']}")
    for f in propose(spec):
        c1, c2 = st.columns([4, 1], vertical_alignment="center")
        c1.markdown(f"*{f.kind}*: **{f.title}**. {f.why}" + (f' The paper: “{f.quote}”' if f.quote else ""))
        if not f.runnable:
            c2.caption(f"Needs {f.needs}")
        elif c2.button("Run check", key=f"fu-{m.id}-{f.id}", width="stretch"):
            run_with_progress(f"Running '{f.title}'", lambda f=f: run_followup(spec, f.id, agent.runs_dir,
                                                                               who=ss.tools.who,
                                                                               runner=runner))
            note_for_chat(f"Ran the follow-up check '{f.title}' for {m.name}.")
            st.rerun()


def conclusion_review(agent: PaperAgent, m) -> None:
    """The ladder's last step: only a named person can mark it."""
    if m.review:
        st.caption(f"Same conclusion: {m.ladder['conclusion']['status']} ({m.ladder['conclusion']['reason']})")
        return
    with st.popover("Review: same conclusion?", icon=":material/rate_review:"):
        st.caption("Does this reproduction support the paper's conclusion? Recorded with your name in the run crate.")
        who = st.text_input("Your name", value=ss.tools.who if ss.tools.who != "anonymous" else "", key=f"rev-who-{m.id}")
        agrees = st.radio("Same conclusion", ["yes", "partly", "no"], horizontal=True, key=f"rev-a-{m.id}")
        note = st.text_area("Why", key=f"rev-n-{m.id}")
        if st.button("Record review", type="primary", disabled=not who.strip(), key=f"rev-b-{m.id}"):
            review_conclusion(agent.path, m.id, who, agrees, note)
            load_agent(agent.path, ss.tools.data_dir)
            st.rerun()


def empty_validate(agent: PaperAgent) -> None:
    """A research object with no runnable method: say what was found, and exactly how to make it runnable."""
    import json as _json

    g = _json.loads((agent.crate_dir / "ro-crate-metadata.json").read_text(encoding="utf-8"))["@graph"] if agent.crate_dir else []
    data = [e for e in g if e.get("@type") == "Dataset" and e["@id"] != "./"]
    code = [e for e in g if e.get("@type") == "SoftwareSourceCode"]
    links = agent.manifest.get("links") or []
    survey = agent.manifest.get("code_survey")
    if survey:
        langs = ", ".join(f"{n} {k}" for k, n in list(survey["languages"].items())[:3]) or "no recognised scripts"
        st.info(f"**The authors' code is here; its methods aren't tools yet.** ReCRATE found the paper's code and cloned "
                f"it ({survey['files']} files: {langs}"
                + (f", {survey['data_files']} data files" if survey.get("data_files") else "") + "). Which code "
                "produces which published result isn't described yet: **Make runnable** reads the paper and the code, "
                "and turns each method into a tool, checked against the published results.")
        survey["missing"] = [n for n in survey.get("needs", []) if not shutil.which(n)]  # checked now, not when built
        if survey.get("missing"):
            need = {"Rscript": "R ([cran.r-project.org](https://cran.r-project.org))",
                    "quarto": "Quarto ([quarto.org](https://quarto.org))", "julia": "Julia", "matlab": "MATLAB",
                    "python3": "Python"}
            st.warning("This code needs " + " and ".join(need.get(m, m) for m in survey["missing"]) + ", not installed "
                       "on this computer. Install it before **Make runnable**, or the methods will be described but "
                       "not run.")
    else:
        st.info("**Nothing to run yet.** ReCRATE recreated this research's RO-Crate from the paper, but found no code "
                "of the authors to run: its methods can only be re-implemented from the text, and will be said to be.")
    with st.expander(f"What the paper links to ({len(links)})", icon=":material/link:"):
        icon = {"code": "💻", "data": "📦", "workflow": "🔁", "model": "🧠"}
        for l in links:
            what = l.get("title") or l["url"].split("://", 1)[-1]
            role = ("the authors' own" if l["own"] else "cited") + (f": {l['why']}" if l.get("why") else "")
            cloned = " · cloned" if l["url"] == agent.manifest.get("code") else ""
            extra = f" · {l['type'].lower()}" if l.get("type") else ""
            st.markdown(f"{icon.get(l['kind'], '🔗')} [{html.escape(what)}]({l['url']}){extra}{cloned}  \n"
                        f"<span class='muted'>{html.escape(role)}</span>", unsafe_allow_html=True)
        if not links:
            st.markdown(("- Data: " + ", ".join(f"[{e['name']}]({e['@id']})" for e in data)) if data else
                        "- No code or data links found in the paper")
        if agent.manifest.get("code_error"):
            st.caption(f"⚠️ Couldn't clone the code: {agent.manifest['code_error']}")
        env = agent.environment
        st.markdown(f"<span class='muted'>Software and models named: {len(env.software) if env else 0} "
                    "(see *Software environment* below)</span>", unsafe_allow_html=True)
    build_panel(agent, has_code=bool(code))


def build_panel(agent: PaperAgent, has_code: bool = True) -> None:
    """Make the methods runnable from here: the /recrate agent (Paper2Agent's pipeline plus ReCRATE's checks), run by
    Claude Code on your subscription, in the background. Its progress is read from the research object's folder."""
    from recrate import build as builder

    s = builder.state(agent.path)
    with st.expander("Make its methods runnable", icon=":material/build:",
                     expanded=s["state"] == "running" or not agent.methods):
        if s["state"] == "running":
            build_progress(str(agent.path))
            return
        if s["state"] == "incomplete":
            reached = s.get("stages") or builder.stages_done(agent.path)
            st.warning(f"The last build stopped after stage *{reached[-1] if reached else 'none'}* "
                       f"({len(reached)} of 10). Press **Make runnable** again to continue from there.")
        elif s["state"] in {"done", "failed", "stopped"}:
            (st.success if s["state"] == "done" else st.warning)(f"Last build: {s['state']}.")
            if s.get("result"):
                with st.expander("What the agent reported"):
                    st.markdown(s["result"])
        st.caption("Like Paper2Agent: Claude sets up the paper's own environment, turns its methods and its code's "
                   "tutorials into tools, runs each on the published data and tests it. ReCRATE adds the checks: "
                   "every parameter quoted from the paper, every result compared with the published one, an "
                   "independent verifier."
                   + ("" if has_code else " This paper links no code, so Claude can only implement what the text "
                      "describes, and will say so."))
        if not claude_code_binary():
            st.info("Needs Claude Code (or its VS Code extension), signed in to your Claude account.")
            return
        depth = st.radio("How much", ["quick", "full"], horizontal=True, key=f"build-depth-{agent.name}",
                         format_func={"quick": "Quick: the main results, up to 3 methods (about 10–20 min)",
                                      "full": "Full: every method and the code's tutorials (can take an hour or more)"}.get,
                         label_visibility="collapsed")
        trust = st.checkbox("I understand this downloads and runs the paper's code on this computer.",
                            key=f"trust-build-{agent.name}")
        if st.button("Make runnable", type="primary", disabled=not trust, icon=":material/build:",
                     key=f"build-{agent.name}"):
            builder.build(agent.path, wait=False, quick=depth == "quick")
            note_for_chat("Started making the methods runnable (the /recrate agent, in the background).")
            st.rerun()
        with st.expander("Or run it yourself in a terminal"):
            st.code(f'cd "{ROOT}"\n.venv/bin/recrate build "{agent.path}"', language="bash")


@st.fragment(run_every=4)
def build_progress(agent_dir: str) -> None:
    from recrate import build as builder

    s = builder.state(agent_dir)
    if s["state"] != "running":  # finished: reload so the new methods appear
        load_agent(Path(agent_dir), ss.tools.data_dir if "tools" in ss else None)
        st.rerun(scope="app")
    from datetime import datetime, timezone

    events = builder.events(agent_dir, last=30)
    steps = [e for e in events if e.startswith("↳")]
    note = next((e for e in reversed(events) if not e.startswith("↳")), "")
    reached = builder.stages_done(agent_dir)
    try:
        t0 = datetime.fromisoformat(s["started"])
        since = f"since {t0.astimezone().strftime('%H:%M')} ({int((datetime.now(timezone.utc) - t0).total_seconds() // 60)} min)"
    except (KeyError, ValueError):
        since = ""
    now = {None: "Setting up", "setup": "Reading the paper, and surveying its code, data and environment",
           "read": "Surveying its code, data and environment", "survey": "Reading the paper",
           "plan": "Making each method runnable", "implement": "Assembling the RO-Crate",
           "assemble": "Checking every quote and published value against the paper", "check": "Reproducing each method",
           "validate": "Verifying every claim independently", "verify": "Delivering: report, server check, package",
           }.get(reached[-1] if reached else None, "Working")
    st.markdown(f"**{now}** · running {since} · {len(reached)} of 10 stages done")
    line = "  ‹  ".join(html.escape(short(e, 70)) for e in reversed(steps[-6:]))  # newest first, on one line
    st.markdown(f"<div class='steps-line' title='{html.escape(chr(10).join(steps[-12:]))}'>{line}</div>"
                + (f"<div class='steps-line muted'>{html.escape(short(note, 160))}</div>" if note else ""),
                unsafe_allow_html=True)
    st.caption("You can keep using the app; this updates by itself.")


def validate_actions(agent: PaperAgent) -> None:
    runnable = [m for m in agent.methods if not agent.spec(m.id).needs_data
                or (agent.spec(m.id).example_data and agent.spec(m.id).example_data.exists())]
    needs_trust = [m for m in runnable if runs_own_code(m.language) and m.status == "not validated"]
    c1, c2 = st.columns([3, 1], vertical_alignment="center")
    c2.caption(f"{len(runnable)} with published data")
    if needs_trust:
        with c1.popover("Reproduce all", type="primary", width="stretch", icon=":material/replay:"):
            st.markdown("This runs the workflows' own code (" + ", ".join(sorted({m.language for m in needs_trust}))
                        + ") on your computer. Do it only if you trust where it comes from.")
            if st.button("I trust it: run", type="primary", key="trust-all"):
                with st.spinner("Reproducing…"):
                    revalidate(agent.path, runner=runner)
                load_agent(agent.path, ss.tools.data_dir)
                note_for_chat("Reproduced all methods on the published data.")
                st.rerun()
    elif c1.button("Reproduce all", type="primary", width="stretch", icon=":material/replay:", disabled=not runnable):
        heads = []
        for m in runnable:
            res = run_with_progress(f"Reproducing {m.name}", lambda m=m: ss.tools.call("reproduce", {"method": m.name}))
            heads.append(f"**{m.name}**: " + next((a["headline"] for a in res.attachments if a["type"] == "comparison"),
                                                   next((a["text"] for a in res.attachments if a["type"] == "error"), "done")))
        note_for_chat("Reproduced on the published data.\n\n" + "\n".join(f"- {h}" for h in heads))
        st.rerun()


# ---- Reuse: your data, checked, run ---------------------------------------------------------------------
def reuse_section(agent: PaperAgent, runs: list[dict], data_dir: Path, files: list[Path]) -> None:
    tools: ChatTools = ss.tools
    if not agent.methods:
        st.info("No runnable method yet: see **Reproduce**.")
        return
    unproven = [m.name for m in agent.methods if m.status in ("differs", "failed")]
    if unproven:
        st.warning(f"**{', '.join(unproven)}** did not reproduce the paper's results (see **Reproduce**). You can "
                   "still reuse it, but use its results with care.")
    st.markdown("<div class='eyebrow'>1 · Your data</div>", unsafe_allow_html=True)
    with st.container(border=True):
        suggested = EXAMPLES.get(ss.get("ro", {}).get("example", ""), {}).get("reuse_links")
        mode = st.segmented_control("Data", ["Upload files", "A folder on this computer", "From a link"], key="data_mode",
                                    default="A folder on this computer" if tools.data_dir else
                                    "From a link" if suggested else "Upload files", label_visibility="collapsed")
        if mode == "From a link":
            if suggested:
                st.caption(f"Suggested for this paper: **{suggested['label']}**. Same imaging modality, different lab "
                           "and microscope, with its own ground truth.")
            refs = st.text_area("Links", value="\n".join(suggested["refs"]) if suggested else "",
                                placeholder="A Zenodo DOI or record link, or direct links to files or .zip archives, "
                                            "one per line", height=90, label_visibility="collapsed",
                                help="Zips are unpacked side by side (images.zip and masks.zip give images/ and "
                                     "masks/). Data behind a login is never fetched: download it and give the folder.")
            if st.button("Fetch the data", icon=":material/download:", disabled=not refs.strip()):
                import hashlib

                key = hashlib.sha1(refs.strip().encode()).hexdigest()[:12]  # fetched once, kept for later sessions
                dest = Path.home() / ".cache" / "recrate" / "datasets" / key
                try:
                    with st.spinner("Downloading and unpacking…"):
                        tools.data_dir = fetch_dataset(refs.splitlines(), dest)
                    st.rerun()
                except Exception as e:  # noqa: BLE001 - network problems are reported, not raised
                    st.error(f"Couldn't fetch the data: {e}")
        elif mode == "A folder on this computer":
            folder = st.text_input("Folder", value=str(tools.data_dir or ""), placeholder="/path/to/your/data",
                                   help="Nothing is copied: best for large datasets.", label_visibility="collapsed")
            if folder and Path(folder).expanduser().is_dir() and Path(folder).expanduser() != tools.data_dir:
                tools.data_dir = Path(folder).expanduser()
                st.rerun()
        else:
            up = st.file_uploader("Your data files", accept_multiple_files=True, label_visibility="collapsed")
            if up:
                d = ss.uploads / "data"
                d.mkdir(parents=True, exist_ok=True)
                new = [f for f in up if not (d / f.name).exists()]
                for f in new:
                    (d / f.name).write_bytes(f.getbuffer())
                if new or tools.data_dir is not None:
                    tools.data_dir = None
                    st.rerun()
        st.caption(f"{len(files)} file(s) · {fmt_bytes(sum(f.stat().st_size for f in files))} · "
                   + ("uploaded" if data_dir == ss.uploads / "data" else f"folder `{data_dir.name}`") if files else
                   "No data yet: upload files, or point at a folder.")
        ex = EXAMPLES.get(ss.get("ro", {}).get("example", ""), {})
        if ex.get("data_note") and ex.get("data") and data_dir == Path(ex["data"]):
            st.caption(f"Example data: {ex['data_note']}")
    scale_question(agent, data_dir, files)
    st.markdown("<div class='eyebrow' style='margin-top:.6rem'>2 · Does it fit each method?</div>", unsafe_allow_html=True)
    missing_px = False
    for m in agent.methods:
        spec = agent.spec(m.id)
        fit = (cached_check(str(agent.path), m.name, str(data_dir), data_signature(files), ss.get("scale_ratio"))
               if files or not agent.spec(m.id).needs_data else None)
        adj = {a["parameter"]: a for a in (fit["adjustments"] if fit else [])}
        with st.container(border=True):
            verdict = "<span class='muted'>add your data to check</span>" if not fit else ICON[fit["status"]] + " " + {
                "ok": "fits as is", "warn": f"fits with {len(adj)} adjustment(s)" if adj else "fits, with warnings",
                "fail": "doesn't fit yet"}[fit["status"]]
            st.markdown(f"**{m.name}** &nbsp; {verdict}", unsafe_allow_html=True)
            if fit:
                st.dataframe(pd.DataFrame([{"Property": x["check"], "Paper's data": str(x["paper"]),
                                            "Your data": str(x["your data"]),
                                            "Check": f"{ICON[x['status']]} {x['status']}", "Note": x["note"]}
                                           for x in fit["rows"]]), hide_index=True, width="stretch")
                for row in fit["rows"]:
                    if row["status"] == "fail":
                        st.caption(f"❌ {row['check']}: {row['note']}")
                        missing_px |= row["check"] == "Pixel size" and row["your data"] == "missing"
            overrides = {}
            from recrate.model import parameter_plan

            plan = (fit or {}).get("plan") or parameter_plan(spec)
            quotes = {q["name"]: q["quote"] for q in parameter_sources(agent, spec)}

            def param_row(p: dict, group: str) -> None:
                shown = p["published"] if p["published"] not in ("", None) else "(empty)"
                value = f"{shown} → **{p['value']:g}**" if group == "adjusted" else f"**{shown}**"
                unit = f" {p['unit']}" if p.get("unit") else ""
                st.markdown(f"`{p['name']}` = {value}{unit}")
                if p.get("why"):
                    st.caption(p["why"])
                q = p.get("quote") or quotes.get(p["name"])
                if q:
                    c1, c2 = st.columns([6, 1])
                    c1.markdown(f"<div class='quote'>{html.escape(q)}</div>", unsafe_allow_html=True)
                    if c2.button("Show", key=f"show-{m.id}-{p['name']}", help="Highlight it in the source (Reproduce)"):
                        ss.highlight, ss._goto = q, "validate"
                        st.rerun()
                if st.toggle("Use my own value", key=f"own-{m.id}-{p['name']}"):
                    c1, c2 = st.columns([1, 2])
                    num = isinstance(p["published"], (int, float))
                    start = float(p.get("value", p["published"])) if num else 0.0
                    v = (c1.number_input("Value", value=start, key=f"val-{m.id}-{p['name']}") if num else
                         c1.text_input("Value", value=str(p["published"]), key=f"val-{m.id}-{p['name']}"))
                    why = c2.text_input("Why? (recorded)", key=f"why-{m.id}-{p['name']}")
                    overrides[p["name"]] = (v, why)

            if plan["adjusted"]:
                st.markdown("**Adjusted automatically** <span class='muted'>(each has a scaling rule)</span>",
                            unsafe_allow_html=True)
                for p in plan["adjusted"]:
                    param_row(p, "adjusted")
            if plan["decide"]:
                st.markdown("**Needs your decision** <span class='muted'>(as published unless you change it, with "
                            "a reason)</span>", unsafe_allow_html=True)
                for p in plan["decide"]:
                    param_row(p, "decide")
            with st.expander(f"As published ({len(plan['as_published'])})"):
                for p in plan["as_published"]:
                    param_row(p, "as_published")
            ss[f"ov-{m.id}"] = overrides
            missing = [k for k, (_, why) in overrides.items() if not why.strip()]
            if missing:
                st.warning(f"Give a reason for: {', '.join(missing)}.")
            run = latest(runs, m.name, "reuse")
            if run and st.toggle("Show the latest run on your data", key=f"showrun-{m.id}",
                                 value=ss.get("run_name") == run.name):
                results_tab(agent, run, data_dir)
    if missing_px:
        with st.form("pixel-size"):
            st.markdown("**Your images have no pixel size.** Enter it from the acquisition metadata; it is written "
                        "into working copies and recorded as provided by you.")
            c1, c2 = st.columns([2, 1])
            px = c1.number_input("Pixel size", min_value=0.0, value=0.0, step=0.01, format="%.4f")
            unit = c2.selectbox("Unit", ["µm", "nm"])
            src = st.text_input("Where does this value come from?")
            if st.form_submit_button("Set pixel size", type="primary") and px > 0:
                res = tools.call("set_pixel_size", {"pixel_size": px, "unit": unit, "source": src})
                note_for_chat(res.attachments[0]["text"] if res.attachments else "Pixel size set.")
                st.rerun()


def scale_question(agent: PaperAgent, data_dir: Path, files: list[Path]) -> None:
    """When the research doesn't state the scale some parameters depend on (pixel size for images, sampling rate for
    recordings...), ask how the user's compares: the one fact ReCRATE can't read from both sides. Asked only in fields
    that have such a scale, so a paper analysing tables is never asked about pixels."""
    from recrate.profiles import fields_of

    if not files:
        return
    fields = [f for f in fields_of(agent) if f.scale]
    rows = [r for m in agent.methods
            for r in cached_check(str(agent.path), m.name, str(data_dir), data_signature(files),
                                  ss.get("scale_ratio"))["rows"]]
    f = next((f for f in fields if any(r["check"] == f.scale_check and r["paper"] == "not stated" for r in rows)), None)
    if f is None:
        return
    with st.container(border=True):
        st.markdown(f"**{f.scale_question}** The research doesn't state its {f.scale}, and some parameters depend on "
                    "it. If yours differs, they are rescaled to keep their meaning.")
        choice = st.segmented_control("Scale", ["Same", "Different", "I don't know"], key="scale-choice",
                                      default="Same" if ss.get("scale_ratio") == 1 else
                                      "Different" if ss.get("scale_ratio") else "I don't know",
                                      label_visibility="collapsed")
        ratio = 1.0 if choice == "Same" else None
        if choice == "Different":
            ratio = st.number_input(f.scale_ratio_label, min_value=0.05, max_value=20.0, step=0.5,
                                    value=float(ss.get("scale_ratio") or 2.0), help=f.scale_help)
        if ratio != ss.get("scale_ratio"):
            ss.scale_ratio = ratio
            st.rerun()


def reuse_actions(agent: PaperAgent, data_dir: Path, files: list[Path]) -> None:
    tools: ChatTools = ss.tools
    if not agent.methods:
        return
    names = [m.name for m in agent.methods]
    c1, c2 = st.columns([1.3, 2], vertical_alignment="center")
    name = c1.selectbox("Method", names, key="reuse-method", label_visibility="collapsed") if len(names) > 1 else names[0]
    m = next(x for x in agent.methods if x.name == name)
    fit = (cached_check(str(agent.path), m.name, str(data_dir), data_signature(files), ss.get("scale_ratio"))
               if files or not agent.spec(m.id).needs_data else None)
    overrides = ss.get(f"ov-{m.id}", {})
    missing = [k for k, (_, why) in overrides.items() if not str(why).strip()]
    can = bool(fit and fit["can_run"]) and not missing
    why_not = None if can else ("Add your data first" if not fit else
                                "Give a reason for your own values" if missing else "Your data doesn't fit yet")
    if len(names) == 1:
        c1.markdown(f"**{name}**")
    if c2.button(f"Run on my data", type="primary", width="stretch", disabled=not can, icon=":material/play_arrow:",
                 help=why_not):
        spec = agent.spec(m.id)
        adj = fit["adjustments"] if fit else []
        out = run_with_progress(f"Running {m.name}", lambda: run_on_data(
            spec, data_dir, agent.runs_dir, runner=runner, agent=tools.who, overrides=overrides or None,
            progress=tools.progress, scale_ratio=ss.get("scale_ratio")))
        ss.run_name = out.run_dir.name
        tools.last_run = out.run_dir
        if adj or overrides:
            tools.whatifs[out.run_dir.name] = run_with_progress(
                "Running with the published values", lambda: whatif(spec, data_dir, spec.default_params(), runner=runner))
        note_for_chat(f"Ran **{m.name}** on your data ({', '.join(f'{k} = {v}' for k, v in out.params.items())}).")
        st.rerun()
    if why_not:
        st.caption(why_not)


# ---- Record: every run, and what to take away ------------------------------------------------------------
ROLE = [
    (r"(^|/)ro-crate-metadata\.json$", "RO-Crate metadata"), (r"(^|/)ro-crate-preview\.html$", "readable preview"),
    (r"^crate/workflows?/|^crate/[^/]+\.(ga|ijm|cwl|nf|smk|py)$", "workflow"),
    (r"^crate/data/", "published data"), (r"^crate/expected/", "published results"),
    (r"^crate/environment/", "environment"), (r"^crate/[^/]+\.(pdf|md|txt)$", "paper"),
    (r"^paper/", "paper text"), (r"^source-crate/", "original, unchanged"), (r"^code/", "code"),
]
FOLDER_LABEL = {"crate": "RO-Crate", "paper": "Paper", "runs": "Runs", "source-crate": "Original RO-Crate", "code": "Code",
                "data": "published data", "expected": "published results", "environment": "environment"}
HIDE = {"agent.json", "environment.json", "deposits", "exports", "crate_description.json", "DRAFT_ME.md", ".git", "runs"}


def role(rel: str) -> str | None:
    return next((label for pat, label in ROLE if re.search(pat, rel)), None)


TREE_DEPTH, TREE_ITEMS = 4, 25  # a large repository (thousands of folders) stays quick to draw


@st.cache_data(show_spinner=False, max_entries=64)
def _tree_cached(root: str, bucket: int) -> str:
    root_p = Path(root)
    return _tree(root_p, root_p, 0, {})


def tree_html(root: Path, d: Path, depth: int = 0) -> str:
    """The folder tree of a research object. Redrawn at most every 30 s: a running build changes the folder
    all the time, and the tree only reads the folders it shows."""
    return _tree_cached(str(d), int(time.time() // 30))


def _tree(root: Path, d: Path, depth: int, counts: dict[Path, int]) -> str:
    items = sorted([p for p in d.iterdir() if p.name not in HIDE and not p.name.startswith(".")],
                   key=lambda p: (p.is_file(), {"crate": 0, "paper": 1, "source-crate": 2}.get(p.name, 9), p.name))
    out = []
    folders = [p for p in items if p.is_dir()]
    for f in folders[:TREE_ITEMS]:
        if depth == 0 and f.name == "env":  # the paper's own environment: thousands of files, described, not listed
            info = ""
            rec = f / "recrate-env.json"
            if rec.exists():
                e = json.loads(rec.read_text(encoding="utf-8"))
                info = f" Python {e.get('python_version', '?')} · {len(e.get('packages', []))} packages"
            out.append(f"<div class='f'>📁 env<span class='tag'>environment</span><span class='n'>{html.escape(info)}"
                       "</span></div>")
            continue
        try:
            n = sum(1 for x in os.scandir(f) if not x.name.startswith("."))  # what's directly inside: cheap
        except OSError:
            n = 0
        tag = FOLDER_LABEL.get(f.name) if depth > 0 else None
        title = FOLDER_LABEL.get(f.name, f.name) if depth == 0 else f.name
        inner = (_tree(root, f, depth + 1, counts) if depth + 1 < TREE_DEPTH
                 else f"<div class='n'>… {n} more inside</div>")
        out.append(f"<details{' open' if depth == 0 and f.name == 'crate' else ''}><summary>📁 {html.escape(title)}"
                   + (f"<span class='tag'>{tag}</span>" if tag and tag != f.name else "")
                   + f" <span class='n'>{n}</span></summary><div>{inner}</div></details>")
    if len(folders) > TREE_ITEMS:
        out.append(f"<div class='n'>… {len(folders) - TREE_ITEMS} more folders</div>")
    files = [p for p in items if p.is_file()]
    for f in files[:20]:
        r = role(f.relative_to(root).as_posix())
        tag = r if r and r not in {"published data", "code"} else None
        out.append(f"<div class='f'>📄 {html.escape(f.name)}" + (f"<span class='tag'>{tag}</span>" if tag else "") + "</div>")
    if len(files) > 20:
        out.append(f"<div class='n'>… {len(files) - 20} more</div>")
    return "".join(out)


def preview(root: Path, rel: str) -> None:
    f = root / rel
    suf = f.suffix.lower()
    st.caption(f"{rel} · {fmt_bytes(f.stat().st_size)}" + (f" · {role(rel)}" if role(rel) else ""))
    try:
        if suf == ".json":
            st.json(json.loads(f.read_text(encoding="utf-8")), expanded=1)
        elif suf in {".csv", ".tsv", ".tabular"}:
            st.dataframe(pd.read_csv(f, sep="," if suf == ".csv" else "\t"), hide_index=True, width="stretch", height=220)
        elif suf in {".png", ".jpg", ".jpeg"}:
            st.image(str(f), width="stretch")
        elif suf in {".tif", ".tiff"}:
            from recrate.figures import load_display

            st.image(load_display(f), width="stretch", clamp=True)
        elif suf in {".pdf", ".zip"}:
            st.download_button("Download", f.read_bytes(), file_name=f.name)
        else:
            st.code(f.read_text(encoding="utf-8", errors="replace")[:3000], language=None, wrap_lines=True)
    except Exception as e:  # noqa: BLE001
        st.caption(f"Can't preview this file ({e}).")


def research_objects() -> list[Path]:
    """The research objects of this session, newest first: each paper or RO-Crate added (once its crate is found or
    built) and each example opened. Nothing else on the computer is listed."""
    return [Path(d) for d in reversed(ss.get("session_ros", [])) if (Path(d) / "agent.json").exists()]


def ro_status(a: PaperAgent) -> str:
    from recrate import build as builder

    if builder.state(a.path).get("state") == "running":
        return "⏳"
    if not a.methods:
        return "📄"
    numbers = [m.ladder.get("numbers", {}).get("status") for m in a.methods if m.ladder]
    if numbers and all(n == "passed" for n in numbers):
        return "✅"
    if any(n in ("partial", "failed") for n in numbers):
        return "🟠"
    return "⚪"


def open_research_object(d: Path) -> None:
    ex = next((k for k, e in EXAMPLES.items() if Path(e["agent"]).resolve() == d), None)
    ss.landing = False
    if ex:
        open_example(ex)
        return
    load_agent(d)
    ss.ro = {"agent": str(d)}
    ss._goto = "validate"
    say(f"Opened **{PaperAgent.load(d).card()['title']}**. {PaperAgent.load(d).card()['verdict']}")


def sidebar() -> None:
    """Research Objects: every one on this computer, each as the folder tree of its files, like the Record tab."""
    with st.sidebar:
        st.markdown("### Research Objects")
        items = research_objects()
        if not items:
            st.caption("Add a paper (PDF, DOI, arXiv link) or an RO-Crate, or open an example. Once its RO-Crate is "
                       "found or built, its research object appears here, with its files.")
            return
        st.caption("✅ reproduced · 🟠 partly · ⚪ not checked yet · 📄 no methods yet · ⏳ being made runnable")
        current = Path(ss.agent_dir).resolve() if ss.get("agent_dir") else None
        for d in items:
            try:
                a = PaperAgent.load(d)
            except Exception:  # noqa: BLE001 - a broken folder is skipped, not fatal
                continue
            title = a.manifest.get("title") or a.name
            here = d == current
            with st.expander(f"{ro_status(a)} {short(title, 60)}", expanded=here):
                n_runs = sum(1 for _ in a.runs_dir.glob("*/ro-crate-metadata.json")) if a.runs_dir.exists() else 0
                st.caption(f"{a.manifest.get('crate_source') or 'crate'} · {len(a.methods)} method(s) · {n_runs} run(s)"
                           + (" · draft" if a.manifest.get("draft") else ""))
                if here:
                    st.markdown(":blue-badge[open now]")
                elif st.button("Open", key=f"ro-open-{abs(hash(str(d))) % 10**8}", icon=":material/folder_open:",
                               width="stretch"):
                    open_research_object(d)
                    st.rerun()
                st.markdown(f"<div class='tree'>{tree_html(d, d)}</div>", unsafe_allow_html=True)
                st.divider()
                crate_panel(a, f"side-{abs(hash(str(d))) % 10**8}")


def crate_panel(agent: PaperAgent, key: str) -> None:
    """The research object's RO-Crate: validated with the RO-Crate validator when it was created (and again if it has
    changed since), so its status shows before anything is downloaded; then the download."""
    from recrate.export import _how, crate_validation, export_crate

    if agent.crate_dir is None:
        return
    with st.spinner("Validating the RO-Crate…"):
        try:
            v = crate_validation(agent.path)
        except Exception as e:  # noqa: BLE001 - shown, not raised
            v = {"error": f"{type(e).__name__}: {e}"}
    st.markdown("**RO-Crate**" + (" &nbsp; :orange-badge[draft]" if agent.manifest.get("draft") else ""))
    st.caption(_how(agent.manifest))
    if v is None:
        st.caption("Not validated: install the RO-Crate validator (`pip install roc-validator`).")
    elif v.get("error"):
        st.warning(f"Validation could not run: {v['error']}")
    else:
        req, rec = v["required"], v["recommended"]
        (st.success if v["passed"] else st.error)(
            f"{'Valid' if v['passed'] else 'Not valid'} · {v['profile']} · {req['passed_checks']}/{req['checks']} "
            f"required checks" + (" · changed since validated" if v.get("stale") else ""))
        st.caption(f"Recommended level: {rec['passed_checks']}/{rec['checks']} · validated {str(v.get('when', ''))[:16].replace('T', ' ')}")
        if req["issues"] or rec["issues"]:
            with st.expander(f"Validation details: {len(req['issues'])} problem(s), {len(rec['issues'])} suggestion(s)"):
                for i in req["issues"]:
                    st.markdown(f"❌ {html.escape(i['message'])}")
                for i in rec["issues"]:
                    st.caption(f"· {i['message']}")
    st.download_button("Download RO-Crate (.zip)", data=lambda: Path(export_crate(agent.path)).read_bytes(),
                       file_name=f"{agent.name[:60]}-ro-crate.zip", mime="application/zip", width="stretch",
                       type="primary" if v and v.get("passed") else "secondary", icon=":material/download:",
                       key=f"crate-dl-{key}", on_click="ignore")


def run_sentence(r: dict) -> str:
    """One run, in the words a researcher would use."""
    what = {"reproduce": f"Reproduced **{r['method']}** on the published data",
            "follow-up": f"Checked a possible cause of a difference in **{r['method']}**",
            "reuse": f"Ran **{r['method']}** on your data"}.get(r["mode"], f"Ran **{r['method']}**")
    result = (f"{r['images']} file(s)" if r.get("images") else "") if r["mode"] == "reuse" else \
        (r.get("purpose") or "") + " " + (r["verdict"] or "")
    return what + (f": {short(result.strip(), 140)}" if result and result.strip() else "")


def record_section(agent: PaperAgent, runs: list[dict]) -> Path | None:
    """The record, minimal: what was run, when, by whom, with what result. For the selected run, what you changed and
    a methods paragraph for your paper; below, its record to download. The RO-Crate details stay inside that file."""
    if not runs:
        st.info("Nothing recorded yet. Every time you reproduce the research or run a method on your data, it is "
                "recorded here, so you can show exactly what you did.")
        return None
    st.caption("Every run is recorded: what ran, on which data, with which settings, and by whom.")
    shown = list(reversed(runs))
    names = [r["run"] for r in shown]
    sel = st.dataframe(pd.DataFrame([{"when": (r["when"] or "")[:16].replace("T", " "),
                                      "what": run_sentence(r).replace("**", ""), "by": r["by"]} for r in shown]),
                       hide_index=True, width="stretch", height=min(38 * len(shown) + 40, 230), on_select="rerun",
                       selection_mode="single-row", key="runs-table",
                       column_config={"what": st.column_config.TextColumn(width="large")})
    picked = (sel.selection.rows or [None])[0] if sel and hasattr(sel, "selection") else None
    if picked is None:
        picked = names.index(ss.get("run_name")) if ss.get("run_name") in names else 0
    run_dir = agent.runs_dir / names[picked]
    r = run_story(run_dir)
    changed = [p for p in r["parameters"] if p["paper_default"] not in (None, "") and str(p["value"]) != str(p["paper_default"])]
    st.markdown("Settings: " + ("; ".join(f"`{p['name']}` {p['paper_default']} → **{p['value']}**" for p in changed)
                                if changed else "the paper's, unchanged."))
    with st.expander("Methods paragraph for your paper"):
        st.code(methods_markdown(run_dir), language="markdown", wrap_lines=True)
    return run_dir


def record_actions(agent: PaperAgent, run_dir: Path | None) -> None:
    if run_dir is None:
        st.caption("Runs appear here as you reproduce or reuse.")
        return
    import io
    import zipfile

    buf = io.BytesIO()  # the run's RO-Crate: everything about it, readable by people and machines
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(run_dir.rglob("*")):
            if f.is_file():
                z.write(f, Path(run_dir.name) / f.relative_to(run_dir))
    st.download_button("Download this run's record", buf.getvalue(), file_name=f"{run_dir.name}.zip",
                       mime="application/zip", type="primary", width="stretch", icon=":material/download:",
                       help="The run as an RO-Crate: workflow, inputs with checksums, settings, results, figures, "
                            "reviews, and a page that opens in any browser")


# ---- the workstation card ------------------------------------------------------------------------------
def workstation(agent: PaperAgent) -> None:
    tools: ChatTools = ss.tools
    tools.runner, tools.who = runner, who or "anonymous"
    card = agent.card()
    data_dir = tools.current_data()
    files = [p for p in data_dir.rglob("*") if p.is_file() and not p.name.startswith(".")] if data_dir.exists() else []
    runs = find_runs(agent.spec(), agent.runs_dir) if agent.has_methods else []
    n_ok = sum(1 for m in agent.methods if m.status == "validated")
    meta = [x for x in [card.get("doi") and f"[{card['doi'].removeprefix('https://doi.org/').removeprefix('https://')}]"
                        f"({card['doi']})", card.get("published") and f"published {card['published']}",
                        "crate " + str(card.get("crate_source")) + (" (draft)" if card.get("draft") else "")] if x]
    st.markdown(f"#### {card['title']}")
    st.markdown("<span class='muted'>" + " · ".join(meta) + "</span>", unsafe_allow_html=True)
    counts = {"validate": f"Reproduce · {n_ok}/{len(agent.methods)}" if agent.methods else "Reproduce",
              "reuse": "Reuse" + (f" · {len(files)} files" if files else ""),
              "record": f"Record · {len(runs)}" if runs else "Record"}
    if ss.get("_goto"):  # a jump requested elsewhere (e.g. "Show" in Reuse), applied before the bar is drawn
        ss.section = ss.pop("_goto")
    with st.container(key="sectionbar"):
        sec = st.segmented_control("Section", list(SECTIONS), format_func=lambda k: counts[k], key="section",
                                   label_visibility="collapsed", required=True) or "validate"
    run_dir = None
    with st.container(key="workscroll"):
        if sec == "validate":
            validate_section(agent, runs, data_dir)
        elif sec == "reuse":
            reuse_section(agent, runs, data_dir, files)
        else:
            run_dir = record_section(agent, runs)
    with st.container(key="workfoot"):
        if sec == "validate":
            validate_actions(agent)
        elif sec == "reuse":
            reuse_actions(agent, data_dir, files)
        else:
            record_actions(agent, run_dir)


def start(agent_none: bool = True) -> None:
    with st.container(key="workscroll"):
        st.markdown("#### Recreate a piece of research")
        st.markdown("<span class='muted'>Start from its <b>RO-Crate</b> or its <b>paper</b>. For a paper, ReCRATE recreates "
                    "the crate with the software as it was when the paper was published. Then reproduce it on "
                    "its own data, or reuse its methods on yours.</span>", unsafe_allow_html=True)
        with st.form("add-source", clear_on_submit=True, border=False):
            c1, c2 = st.columns([5, 1.2], vertical_alignment="bottom")
            text = c1.text_input("A DOI or link", placeholder="DOI or link: WorkflowHub, Zenodo, a paper DOI, arXiv, GitHub",
                                 label_visibility="collapsed")
            add = c2.form_submit_button("Recreate", width="stretch", type="primary", icon=":material/arrow_forward:")
            files = st.file_uploader("Or upload a paper (PDF, text) or an RO-Crate (zip, ro-crate-metadata.json)",
                                     accept_multiple_files=True)
            if add and (text or files):
                add_and_build(text, files or [])
                st.rerun()
        if ss.ro.get("repo") and not (ss.ro.get("crate") or ss.ro.get("paper")):
            st.caption("Code added. Add the paper or crate it belongs to.")
        st.markdown("<div class='eyebrow' style='margin-top:1.2rem'>Or start from an example</div>", unsafe_allow_html=True)
        for key, ex in EXAMPLES.items():
            with st.container(border=True):
                c1, c2 = st.columns([4, 1], vertical_alignment="center")
                c1.markdown(f"**{ex['title']}**  \n<span class='muted'>{ex['about']}</span>  \n"
                            + " ".join(f"<span class='chip'>{t}</span>" for t in ex["tags"]), unsafe_allow_html=True)
                if c2.button("Open", key=f"ex-{key}", width="stretch"):
                    open_example(key)
                    st.rerun()


# ---- the start page ------------------------------------------------------------------------------------
GET = [("Reproduce", "Each method run on the research's own data, compared with the published result.", "replay"),
       ("Reuse", "Your data checked against what each method expects; parameters adjusted and recorded.", "tune"),
       ("Record", "Every run as a Workflow Run RO-Crate, with an export for your paper.", "inventory_2")]


def logo_html(width: int) -> str:
    """The logo inline (not st.image), so Streamlit doesn't put its image toolbar on it."""
    svg = (IMG / "logo.svg").read_text(encoding="utf-8")
    svg = re.sub(r'width="\d+" height="\d+"', f'width="{width}" style="height:auto;display:block"', svg, count=1)
    return f"<div class='logo'>{svg}</div>"


def landing() -> None:
    """The first thing a visitor sees: what ReCRATE is, what they will get, and one way in."""
    _, mid, _ = st.columns([1, 2.4, 1])
    with mid, st.container(key="landing"):
        st.image(str(IMG / "logo.svg"), width=340)
        st.markdown("<h1 class='hero'>A paper as an agent you can trust</h1>"
                    "<p class='lede'>Give ReCRATE a paper or its RO-Crate. It checks the paper's results before you rely "
                    "on them, checks your data before you reuse the method, and records every run.</p>",
                    unsafe_allow_html=True)
        c1, c2 = st.columns([1, 1])
        c1.button("Start", type="primary", width="stretch", icon=":material/arrow_forward:", key="go",
                  on_click=lambda: ss.update(landing=False, entering=True))  # one run; shown once complete
        with c2.popover("Try an example", width="stretch", icon=":material/science:"):
            for key, ex in EXAMPLES.items():
                st.markdown(f"**{ex['title']}**  \n<span class='muted'>{ex['about']}</span>", unsafe_allow_html=True)
                if st.button("Open", key=f"land-ex-{key}", width="stretch"):
                    ss.landing = False
                    open_example(key)
                    st.rerun()
        st.markdown("<div class='eyebrow' style='margin-top:2.2rem'>What you'll get</div>", unsafe_allow_html=True)
        for col, (t, d, icon) in zip(st.columns(3), GET):
            with col.container(border=True, key=f"get-{t.lower()}"):
                st.markdown(f":material/{icon}: **{t}**  \n<span class='muted'>{d}</span>", unsafe_allow_html=True)
        st.markdown("<p class='muted foot'>Built on <a href='https://github.com/jmiao24/Paper2Agent'>Paper2Agent</a>, "
                    "the <a href='https://doi.org/10.1038/s41597-022-01710-x'>FAIR principles for research software</a> "
                    "and <a href='https://www.researchobject.org/ro-crate/'>RO-Crate</a>. Every number comes from running "
                    "a method, never from a language model.</p>", unsafe_allow_html=True)


# ---- the top bar: connect, who, reset -------------------------------------------------------------------
def agent_box() -> None:
    """Which agent answers in the chat. Nothing else: one choice, and one field only when it needs one."""
    choice = st.radio("Agent", list(AGENTS), key="engine_choice", format_func=AGENTS.get, label_visibility="collapsed")
    if choice == "claude" and not claude_code_binary():
        key = st.text_input("Anthropic API key", type="password", placeholder="Anthropic API key",
                            label_visibility="collapsed", help="Needed without Claude Code. Kept for this session only.")
        if key.strip() and key.strip() != ss.get("api_key"):
            ss.api_key = key.strip()
            st.rerun()
    elif choice == "local":
        models = available_models(ss.local_url)
        if models:
            current = ss.get("local_model") if ss.get("local_model") in models else pick_model(models)
            ss.local_model = st.selectbox("Model", models, index=models.index(current), label_visibility="collapsed")
        else:
            c1, c2 = st.columns([3, 1], vertical_alignment="center")
            c1.caption("No local model running (e.g. Ollama).")
            if c2.button("Retry", key="agent-retry"):
                available_models.clear()
                st.rerun()


def mcp_install(agent_dir: Path, key: str) -> None:
    """Register this research object's agent with an MCP client, as `recrate install-mcp` does, and check it starts."""
    from recrate.install import describe, install, server_entry, server_name

    c1, c2 = st.columns(2)
    target = None
    if c1.button("Add to Claude Desktop", key=f"mcp-desktop-{key}", width="stretch", icon=":material/desktop_mac:"):
        target = "desktop"
    if c2.button("Add to this project", key=f"mcp-project-{key}", width="stretch", icon=":material/folder:",
                 help=f"Writes .mcp.json in {ROOT} (Claude Code and its VS Code extension)"):
        target = "project"
    if target:
        with st.spinner("Registering and starting the server to check it…"):
            try:
                res = install(agent_dir, client=target, project_dir=ROOT)
                (st.success if res["check"]["ok"] else st.error)(describe(res))
            except Exception as e:  # noqa: BLE001 - shown to the person
                st.error(f"{type(e).__name__}: {e}")
    st.caption("Any other MCP client: add this to its configuration.")
    st.code(json.dumps({"mcpServers": {server_name(agent_dir): server_entry(agent_dir)}}, indent=2), language="json")


def topbar() -> None:
    with st.container(key="topbar"):
        c = st.columns([7, 1.2, 1.6, .8], vertical_alignment="center")
        c[0].markdown(logo_html(150), unsafe_allow_html=True)
        kind, label = engine_plan()
        with c[1].popover("Agent", width="stretch", icon=":material/smart_toy:"):
            agent_box()
            if ss.get("agent_dir"):  # the same research object, from Claude or any MCP client
                st.divider()
                st.markdown("**Use it from Claude or any MCP client**")
                mcp_install(Path(ss.agent_dir), "topbar")
        with c[2].popover(ss.who, width="stretch", icon=":material/person:", help="Who runs are recorded as"):
            st.markdown("**Recorded as**")
            st.caption("Every run crate names who ran it (provenance). Defaults to your git user name.")
            new = st.text_input("Name", value=ss.who, label_visibility="collapsed")
            if new.strip() and new.strip() != ss.who and st.button("Save", type="primary"):
                ss.who = new.strip()
                st.rerun()
        if c[3].button("Reset", width="stretch", help="Start over with another piece of research"):
            for k in list(ss.keys()):
                if k not in {"who", "api_key", "engine_choice", "local_url", "local_model"}:
                    del ss[k]
            st.rerun()


# ---- page ---------------------------------------------------------------------------------------------
# A page change (welcome page → workspace, opening an example) is shown once the new page is complete: this marker
# tells the CSS to keep the page hidden while that one run computes, then fade it in. Later runs carry no marker.
st.html(f"<span class='rc-mark {'rc-entering' if ss.pop('entering', False) else 'rc-settled'}'></span>")
if ss.get("engine_choice") not in AGENTS:
    ss.engine_choice = default_agent()
sidebar()
ss.setdefault("landing", not ss.get("agent_dir"))
if ss.landing and not ss.get("agent_dir"):
    landing()
    st.stop()
topbar()
left, right = st.columns([1, 1.25], gap="small")
with right:
    with st.container(key="workcard"):
        if ss.get("agent_dir"):
            workstation(PaperAgent.load(ss.agent_dir))
        else:
            start()
with left:
    with st.container(key="chatcard"):
        chat_card()
