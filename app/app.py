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
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from recrate.agent import PaperAgent, agentify, revalidate, review_conclusion, runs_own_code
from recrate.chat import (LOCAL_URL, ChatTools, ClaudeChat, LocalChat, OpenAICompatChat, claude_available,
                          local_models)
from recrate.crate import CrateError
from recrate.figures import Panel, comparison_chart, panel_figure, pixel_size_um
from recrate.paper import passages
from recrate.pipeline import check_inputs, run_on_data
from recrate.profiles.bioimaging import list_images
from recrate.record import find_runs, headline, publish_back, summarise_record
from recrate.runcrate import approve, image_reviews, review_image, run_story, summarise_run
from recrate.sources import fetch, from_upload, identify
from recrate.workspace import (comparable_with_paper, export_bundle, flag_images, methods_markdown, numeric_columns,
                               parameter_sources, read_rows, references_bib, references_csl, whatif)

ROOT = Path(__file__).resolve().parent.parent
IMG = ROOT / "docs" / "img"
st.set_page_config(page_title="ReCRATE", page_icon=str(IMG / "favicon.svg"), layout="wide",
                   initial_sidebar_state="collapsed")

WH = ROOT / "examples" / "workflowhub-nuclei-segmentation"
EXAMPLES = {
    "workflowhub": {"title": "Nuclei segmentation · WorkflowHub #771",
                    "about": "A published Galaxy workflow crate, with public microscopy images from Zenodo.",
                    "tags": ["RO-Crate", "Galaxy", "real data"],
                    "agent": WH / "paper_agent", "build": {"crate": WH / "crate"}, "data": WH / "research-data" / "B2"},
    "penguins": {"title": "Penguin size dimorphism · PLOS ONE 2014",
                 "about": "A real paper with open data: its Table 2 recomputed from the data, then reused per island.",
                 "tags": ["paper + crate", "ecology", "tables"],
                 "agent": ROOT / "examples" / "penguins-dimorphism" / "paper_agent",
                 "build": {"paper": ROOT / "examples" / "penguins-dimorphism" / "paper.pdf",
                           "crate": ROOT / "examples" / "penguins-dimorphism" / "crate",
                           "paper_doi": "10.1371/journal.pone.0090081"},
                 "data": ROOT / "examples" / "penguins-dimorphism" / "research-data"},
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
    "make_figure": "Plotting from the run",
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
header[data-testid="stHeader"] {height: 0; background: transparent;}
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
.onramp {max-width: 640px; margin: 2.5rem auto 1rem; text-align: center;}
.onramp h1 {font-size: 2.2rem; margin: .6rem 0 .3rem;}
.onramp .sub {color: var(--muted); margin-bottom: 1.2rem;}
.st-key-sourcebox h1, .st-key-sourcebox h2, .st-key-sourcebox h3 {font-size: 1rem !important;}
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
ss.setdefault("engine_choice", "auto")
ss.setdefault("local_url", LOCAL_URL)
ss.setdefault("section", "validate")
ss.setdefault("stage", "welcome")  # welcome -> review -> workstation
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


def engine_plan() -> tuple[str, str]:
    """(kind, label): claude | local | commands, chosen by the person or automatically (free first)."""
    choice = ss.engine_choice
    has_claude, _ = claude_available(ss.get("api_key"))
    models = available_models(ss.local_url) if choice in ("auto", "local") else []
    model = ss.get("local_model") if ss.get("local_model") in models else pick_model(models)
    if choice == "claude" and has_claude:
        return "claude", "Claude"
    if choice in ("auto", "local") and model:
        return "local", f"{model} · on this computer"
    if choice == "auto" and has_claude:
        return "claude", "Claude"
    return "commands", "Simple commands"


def make_engine(agent: PaperAgent, tools: ChatTools):
    kind, _ = engine_plan()
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
    _, mid, _ = st.columns([1, 2.4, 1])
    with mid:
        status = st.status("Building the research object", expanded=True)
    status.write(label)

    def _cb(key: str, step: str) -> None:  # called at the start of each real build stage
        if key == "done":
            return
        status.update(label=step)
        status.write(step)

    try:
        agentify(out, paper=(ro.get("paper") or {}).get("path"), crate=(ro.get("crate") or {}).get("path"),
                 repo=ro.get("repo"), validate="all" if trusted else "safe",
                 paper_doi=paper_doi(ro.get("paper")), on_progress=_cb)
    except Exception as e:
        status.update(label="Build failed", state="error")
        msg = f"Couldn't build the crate: {e or type(e).__name__}"
        say(msg)
        ss.build_error = msg  # shown once on Welcome (the rerun wipes the status widget)
        ss.stage = "welcome"
        ss.agent_dir = None
        ss.ro = {}
        return
    status.update(label="Built the research object", state="complete", expanded=False)
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
    ss.stage = "review"
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
    built = (ex["agent"] / "environment.json").exists()
    if built and ex["build"].get("paper_doi") and not (ex["agent"] / "paper" / "index.md").exists() \
            and not os.environ.get("RECRATE_OFFLINE"):
        built = False  # built before reading packages: rebuild to get the paper's tables and figures
    if built and any(not m.ladder for m in PaperAgent.load(ex["agent"]).methods):
        built = False  # built before the verdict ladder
    if not built:  # missing, or built by an older ReCRATE
        with st.spinner("Building the example: reading the crate and reproducing each method…"):
            agentify(ex["agent"], **ex["build"])
    load_agent(ex["agent"], ex["data"])
    ss.ro = {"example": key}
    ss._goto = "validate"
    ss.stage = "review"
    a = PaperAgent.load(ex["agent"])
    say(f"Opened **{a.card()['title']}**. {a.card()['verdict']} "
        "Its example data is selected for **Reuse**. Ask me anything, or try *reproduce*, *check my data*, *run*.")


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
        msg = ("I didn't find an RO-Crate, a paper or an identifier in that. Try a DOI, a WorkflowHub or arXiv link, or "
               "upload a PDF or a crate zip.")
        say(msg)
        ss.build_error = msg  # shown once on Welcome (say() is not rendered there)
    else:
        failed = [l for l in lines if l.startswith("⚠️")]
        if failed:  # nothing usable resulted and a fetch failed: surface it on Welcome too
            ss.build_error = "\n\n".join(failed)


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


INTRO = ("Hi! Give me a piece of research and I'll recreate it, so you can **validate** it on its own data or "
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


def chat_card() -> None:
    kind, label = engine_plan()
    color = {"claude": "#d97757", "local": "#2A78D6", "commands": "#b8b3a7"}[kind]
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
    entry = st.chat_input("Ask about the research, or attach your data…" if has_agent else
                          "Paste a DOI or link, or attach a paper or crate…", accept_file="multiple")
    st.markdown("<div class='muted' style='font-size:.72rem;margin-top:-.2rem'>Enter to send · Shift+Enter for a new "
                "line</div>", unsafe_allow_html=True)
    if not entry:
        return
    text, files = entry.text or "", list(entry.files or [])
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
    st.rerun()


# ---- workstation helpers ----------------------------------------------------------------------------
def data_signature(files: list[Path]) -> tuple:
    return tuple((p.name, p.stat().st_mtime_ns) for p in files)


@st.cache_data(show_spinner=False)
def cached_check(agent_dir: str, method: str, data: str, sig: tuple) -> dict:
    from recrate.model import parameter_plan

    a = PaperAgent.load(agent_dir)
    try:
        spec = a.spec(method)
        r = check_inputs(spec, Path(data))
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
    for base in (run_dir / "inputs", data_dir):
        for suf in (".tif", ".tiff"):
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
        if not same:
            st.caption(f"ℹ️ {why}")
            reported = None
        used = {p["name"]: p["value"] for p in run_story(run_dir)["parameters"]}
        changed = [f"{k} {v}" for k, v in used.items() if k in spec.default_params() and str(spec.default_params()[k]) != v]
        lab1 = "Your run" + (f" ({', '.join(changed)})" if changed else "")
        lab2 = "Published values unchanged (" + ", ".join(f"{k} {v}" for k, v in spec.default_params().items()
                                                         if str(v) != used.get(k)) + ")"
        other, ref_rows = (w["rows"] if w else None), reported
    metrics = numeric_columns(rows)
    if not metrics:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        return
    metric = st.segmented_control("Measure", metrics, default=metrics[0], key=f"metric-{s['run']}",
                                  label_visibility="collapsed") or metrics[0]
    mean = lambda rr: sum(float(r[metric]) for r in rr if r.get(metric)) / max(1, len(rr))  # noqa: E731
    c = st.columns(3)
    c[0].metric("This run" if s["mode"] == "reproduce" else "Your run", f"{mean(rows):.4g}", border=True, help=lab1)
    if s["mode"] == "reproduce" and other:
        c[1].metric("Published", f"{mean(other):.4g}", border=True)
    elif other:
        c[1].metric("Published values", f"{mean(other):.4g}", delta=f"{mean(other) - mean(rows):+.3g}",
                    delta_color="off", border=True, help=lab2)
    if ref_rows:
        c[2].metric("Published data", f"{mean(ref_rows):.4g}", border=True, help="Reported on the published data")
    df = pd.DataFrame([{"item": r.get("image") or r[list(r)[0]], "value": float(r[metric]), "series": lab1} for r in rows])
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
    chart_png = comparison_chart([r.get("image") or r[list(r)[0]] for r in rows], [float(r[metric]) for r in rows],
                                 [float(r[metric]) for r in other] if other else None, metric, lab1, lab2, rng)
    c1.download_button("Chart (PNG)", chart_png, file_name=f"{s['run']}-{metric}.png", mime="image/png",
                       width="stretch", icon=":material/download:", key=f"chartdl-{s['run']}-{metric}")
    if c2.button("Add chart to the export", width="stretch", key=f"addchart-{s['run']}-{metric}"):
        ss.figures[f"{s['run']}-{metric}.png"] = chart_png
        st.toast("Added to the export bundle.")
    with st.expander("Table"):
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    ovs = sorted((run_dir / "outputs" / "overlays").glob("*.png"))
    if ovs:
        with st.expander(f"Review each result ({len(ovs)})"):
            review(agent, run_dir, ovs, rows, spec, data_dir)
        with st.expander("Make a figure"):
            figure_builder(agent, run_dir, data_dir)


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
    env = agent.environment
    report = agent.path / "REPORT.md"
    if report.exists():  # written by the /recrate agent: what can be recreated, and what can't
        with st.expander("Report from the ReCRATE agent", expanded=not agent.methods, icon=":material/smart_toy:"):
            st.markdown(report.read_text(encoding="utf-8"))
    if not agent.methods:
        empty_validate(agent)
    else:
        verdict_and_ladder(agent)
    for m in agent.methods:
        spec = agent.spec(m.id)
        with st.container(border=True):
            st.markdown(f"**{m.name}** &nbsp; {BADGE.get(m.status, m.status)} &nbsp; :gray-badge[{m.language}]"
                        + (f" &nbsp; :gray-badge[{m.readiness}]" if m.readiness else ""))
            for g in m.gaps:
                st.caption(("⛔ " if m.readiness == "blocked" else "ℹ️ ") + g)
            if m.run_crate and Path(m.run_crate).exists():
                results_by_value(Path(m.run_crate))
                if m.status == "differs":
                    followups_view(agent, m, spec)
                conclusion_review(agent, m)
            elif m.detail:
                st.markdown(f"<span class='muted'>{html.escape(m.detail)}</span>", unsafe_allow_html=True)
            run = latest(runs, m.name, "reproduce")
            if run and st.toggle("Show the latest reproduction's outputs", key=f"showrep-{m.id}"):
                results_tab(agent, run, data_dir)
    if agent.methods:
        from recrate.model import assumptions

        said = list(dict.fromkeys(a for m in agent.methods for a in assumptions(agent.spec(m.id), env)))
        if said:
            with st.expander(f"What the paper doesn't say ({len(said)})", icon=":material/help:"):
                st.caption("Each is an assumption ReCRATE, or whoever drafted the crate, had to make.")
                st.markdown("\n".join(f"- {html.escape(a)}" for a in said))
    with st.expander("Software environment" + (f" · as of {env.published}" if env and env.published else "")):
        if env and env.published:
            st.caption(f"Publication date from {env.published_source}.")
        environment_table(agent)
    with st.expander("Read the source" + (" · cited passage highlighted" if ss.get("highlight") else ""),
                     expanded=bool(ss.get("highlight"))):
        source_view(agent)


LADDER_BADGE = {"passed": ":green-badge[passed]", "partial": ":orange-badge[partial]",
                "failed": ":red-badge[failed]", "not checked": ":gray-badge[not checked]"}


def verdict_and_ladder(agent: PaperAgent) -> None:
    """The verdict sentence (written from the results, never by the model), then one ladder row per method."""
    from recrate.model import STEPS

    card = agent.card()
    ladders = [m.ladder for m in agent.methods if m.ladder]
    numbers = [lad["numbers"]["status"] for lad in ladders]
    show = (st.success if numbers and all(n == "passed" for n in numbers) else
            st.warning if any(n in ("partial", "failed") for n in numbers) else
            st.error if any(lad["runs"]["status"] == "failed" for lad in ladders) else st.info)
    show(f"**{card['verdict']}**")
    for m in agent.methods:
        if not m.ladder:
            continue
        cols = st.columns([1.1] + [1] * len(STEPS))
        cols[0].markdown(f"**{m.name}**")
        for c, (key, label) in zip(cols[1:], STEPS):
            stp = m.ladder.get(key, {"status": "not checked", "reason": ""})
            c.markdown(f"<span class='eyebrow'>{label}</span>", unsafe_allow_html=True)
            c.markdown(LADDER_BADGE.get(stp["status"], stp["status"]), help=stp["reason"])
    st.caption("“Ready” never means reproduced: a step advances only on evidence from a run, and only a person "
               "marks the last one.")


def results_by_value(run_dir: Path) -> None:
    """Every compared value: published, reproduced, difference, tolerance and where it comes from. Mismatches first."""
    from recrate.figures import comparison_from_run, make_run_figure

    comp = comparison_from_run(run_dir)
    if comp is None or not comp.measures:
        return
    rows = sorted(comp.measures, key=lambda m: m.within)
    df = pd.DataFrame([{"value": m.measure, "published": m.reported, "reproduced": m.reproduced,
                        "difference": f"±{m.rel_diff:.3g}" if m.absolute else f"{m.rel_diff:.1%}",
                        "tolerance (source)": m.tolerance_label, "within": "yes" if m.within else "NO"}
                       for m in rows])
    st.markdown(f"**Same numbers: {comp.count} within tolerance**")
    st.dataframe(df.style.apply(lambda r: ["background-color: #fbe9e2" if r["within"] == "NO" else ""] * len(r),
                                axis=1), hide_index=True, width="stretch")
    fig = run_dir / "outputs" / "figures" / "published_vs_reproduced.png"
    if fig.exists():
        st.image(str(fig), caption="Plotted from this run's outputs (recorded in its run crate)", width="stretch")
    elif st.button("Plot published vs reproduced", key=f"fig-{run_dir.name}", icon=":material/scatter_plot:"):
        make_run_figure(run_dir)
        st.rerun()


def followups_view(agent: PaperAgent, m, spec) -> None:
    """When numbers differ: the follow-up checks, each its own recorded run. All of them stay visible."""
    from recrate.followup import followups_for, propose, run_followup

    st.markdown("**Where could the difference come from?** Each check is its own run, linked to this one; the "
                "result above is never replaced.")
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
    st.info("**Nothing to run yet.** ReCRATE recreated this research's RO-Crate from the paper, but the paper alone "
            "doesn't say how to run its methods: that needs a runnable description (the workflow or script, its "
            "inputs and parameters, and the published results to compare with).")
    with st.container(border=True):
        st.markdown("**What the paper gives**")
        st.markdown(("- Data: " + ", ".join(f"[{e['name'].split('(')[-1].rstrip(')')}]({e['@id']})" for e in data))
                    if data else "- Data: none linked in the paper")
        st.markdown(("- Code: " + ", ".join(f"[{e['@id']}]({e['@id']})" for e in code)) if code else
                    "- Code: no repository linked in the paper")
        env = agent.environment
        st.markdown(f"- Software and models named: {len(env.software) if env else 0} (see *Software environment* below)")
    with st.container(border=True):
        st.markdown("**Make its methods runnable with your Claude subscription** (free with it, no API key)")
        rel = agent.path.relative_to(ROOT) if agent.path.is_relative_to(ROOT) else agent.path
        st.markdown("1. Open a terminal in the ReCRATE folder and start Claude Code:")
        st.code(f'cd "{ROOT}"\nclaude', language="bash")
        st.markdown("2. In Claude Code, run the command below. Claude reads the paper and its code, describes each "
                    "method, runs it on the published data and repairs it until it validates.")
        st.code(f"/agentify {rel}", language=None)
        st.markdown("3. Come back here and reload.")
        if st.button("Reload this research object", icon=":material/refresh:"):
            load_agent(agent.path, ss.tools.data_dir)
            st.rerun()
        if not code:
            st.caption("This paper links no code, so Claude can only describe methods it can implement from the text "
                       "and the data; it will say what it couldn't find.")


def validate_actions(agent: PaperAgent) -> None:
    runnable = [m for m in agent.methods if agent.spec(m.id).example_data and agent.spec(m.id).example_data.exists()]
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
    unproven = [m.name for m in agent.methods if m.status != "validated"]
    if unproven:
        st.warning(f"**{', '.join(unproven)}** did not reproduce the paper's results (see **Reproduce**). You can "
                   "still reuse it, but use its results with care.")
    st.markdown("<div class='eyebrow'>1 · Your data</div>", unsafe_allow_html=True)
    with st.container(border=True):
        mode = st.segmented_control("Data", ["Upload files", "A folder on this computer"], key="data_mode",
                                    default="A folder on this computer" if tools.data_dir else "Upload files",
                                    label_visibility="collapsed")
        if mode == "A folder on this computer":
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
    st.markdown("<div class='eyebrow' style='margin-top:.6rem'>2 · Does it fit each method?</div>", unsafe_allow_html=True)
    missing_px = False
    for m in agent.methods:
        spec = agent.spec(m.id)
        fit = cached_check(str(agent.path), m.name, str(data_dir), data_signature(files)) if files else None
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


def reuse_actions(agent: PaperAgent, data_dir: Path, files: list[Path]) -> None:
    tools: ChatTools = ss.tools
    if not agent.methods:
        return
    names = [m.name for m in agent.methods]
    c1, c2 = st.columns([1.3, 2], vertical_alignment="center")
    name = c1.selectbox("Method", names, key="reuse-method", label_visibility="collapsed") if len(names) > 1 else names[0]
    m = next(x for x in agent.methods if x.name == name)
    fit = cached_check(str(agent.path), m.name, str(data_dir), data_signature(files)) if files else None
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
            progress=tools.progress))
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


def tree_html(root: Path, d: Path, depth: int = 0) -> str:
    items = sorted([p for p in d.iterdir() if p.name not in HIDE and not p.name.startswith(".")],
                   key=lambda p: (p.is_file(), {"crate": 0, "paper": 1, "source-crate": 2}.get(p.name, 9), p.name))
    out = []
    for f in [p for p in items if p.is_dir()]:
        n = sum(1 for x in f.rglob("*") if x.is_file())
        tag = FOLDER_LABEL.get(f.name) if depth > 0 else None
        title = FOLDER_LABEL.get(f.name, f.name) if depth == 0 else f.name
        out.append(f"<details{' open' if depth == 0 and f.name == 'crate' else ''}><summary>📁 {html.escape(title)}"
                   + (f"<span class='tag'>{tag}</span>" if tag and tag != f.name else "")
                   + f" <span class='n'>{n}</span></summary><div>{tree_html(root, f, depth + 1)}</div></details>")
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


def record_section(agent: PaperAgent, runs: list[dict]) -> Path | None:
    run_dir = None
    if not runs:
        st.info("No runs yet. Every reproduction and every run on your data is written here as a Workflow Run "
                "RO-Crate: what ran, on which data, with which settings, checked by whom.")
    else:
        st.markdown(f"**{headline(summarise_record(runs))}**")
        kinds = {"reproduce": "reproduction", "follow-up": "follow-up check", "reuse": "your data"}
        c1, c2 = st.columns(2)
        show_kinds = c1.multiselect("Kind", list(kinds.values()), default=list(kinds.values()), key="rec-kinds",
                                    label_visibility="collapsed", placeholder="Kind")
        methods_ = sorted({r["method"] for r in runs if r["method"]})
        show_methods = c2.multiselect("Method", methods_, default=methods_, key="rec-methods",
                                      label_visibility="collapsed", placeholder="Method") if len(methods_) > 1 else methods_
        shown = [r for r in reversed(runs) if kinds.get(r["mode"], r["mode"]) in show_kinds
                 and (r["method"] in show_methods or not r["method"])]
        if not shown:
            st.caption("No run matches these filters.")
            return None
        rows = [{"when": (r["when"] or "")[:16].replace("T", " "), "kind": kinds.get(r["mode"], r["mode"]),
                 "method": r["method"], "result": short((r.get("purpose") or "") + " " + (r["verdict"] if r["mode"] != "reuse"
                                                       else r["headline"] or ""), 120),
                 "by": r["by"], "review": r.get("conclusion") or r["approved"] or ""} for r in shown]
        names = [r["run"] for r in shown]
        sel = st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=min(38 * len(rows) + 40, 260),
                           on_select="rerun", selection_mode="single-row", key="runs-table")
        picked = (sel.selection.rows or [None])[0] if sel and hasattr(sel, "selection") else None
        if picked is None:
            want = ss.get("run_name")
            picked = names.index(want) if want in names else 0
        run_dir = agent.runs_dir / names[picked]
        r = run_story(run_dir)
        with st.container(border=True):
            st.markdown(f"**{r['title']}**")
            st.markdown(f"<span class='muted'>{html.escape(r['summary'] or '')}</span>", unsafe_allow_html=True)
            c1, c2 = st.columns(2)
            c1.markdown(f"**Who** {r['who']}  \n**When** {(r['ended'] or '')[:19].replace('T', ' ')}")
            c2.markdown(f"**Workflow** `{Path(r['workflow']['file'] or '').name}` ({r['workflow']['language']})")
            st.caption("Software: " + "; ".join(r["software"]))
            st.dataframe(pd.DataFrame([{"parameter": p["name"], "value": str(p["value"]), "published": str(p["paper_default"]),
                                        "why": p["note"]} for p in r["parameters"]]), hide_index=True, width="stretch")
            with st.expander(f"Inputs ({len(r['inputs'])}), with checksums"):
                st.dataframe(pd.DataFrame([{"file": i["file"], "size": fmt_bytes(i["size_bytes"]),
                                            "sha256": i["sha256"][:16] + "…"} for i in r["inputs"]]),
                             hide_index=True, width="stretch")
            if r["checks"] or r["comparison"] or r["reviews"]:
                with st.expander("Checks, comparison and reviews"):
                    for title, rows_ in (("Input checks", r["checks"]), ("Comparison with the published result",
                                                                         r["comparison"]), ("Reviews", r["reviews"])):
                        if rows_:
                            st.markdown(f"**{title}**")
                            st.dataframe(pd.DataFrame([{k: str(v) for k, v in x.items()} for x in rows_]),
                                         hide_index=True, width="stretch")
            if r["figures"]:
                with st.expander(f"Figures ({len(r['figures'])}), each with its crate entry"):
                    for f in r["figures"]:
                        st.image(str(run_dir / f["file"]), caption=f"{f['name']}: {f['description']}  ·  `{f['file']}`")
            with st.expander("Methods paragraph and references"):
                spec = agent.spec(summarise_run(run_dir)["method"])
                st.code(methods_markdown(run_dir), language="markdown", wrap_lines=True)
                t1, t2 = st.tabs(["BibTeX", "CSL-JSON"])
                t1.code(references_bib(agent, spec, run_dir), language="bibtex")
                t2.code(json.dumps(references_csl(agent, spec, run_dir), indent=2, ensure_ascii=False), language="json")
            if st.toggle("View as RO-Crate", key=f"asjson-{run_dir.name}"):
                st.markdown(f"<div class='tree'>{tree_html(run_dir, run_dir)}</div>", unsafe_allow_html=True)
                st.json(json.loads((run_dir / "ro-crate-metadata.json").read_text(encoding="utf-8")), expanded=1)
    with st.expander("Connect from Claude or any MCP client"):
        st.markdown("Run this research object as an agent: ask about the paper, check your data, reproduce, "
                    "reuse and record, from a chat.")
        st.code(f'claude mcp add {agent.name[:40]} -- recrate serve "{agent.path}"', language="bash")
        st.markdown("- **Read the paper**: about_paper, search_paper, describe_method, tables and figures\n"
                    "- **Check and run**: check_my_data, reproduce, run_on_my_data, follow-up checks\n"
                    "- **Figures**: make_figure\n"
                    "- **Record**: review_conclusion, approve_run, methods_text, publish_run, report_page")
    with st.expander("Files of this research object"):
        root = agent.path
        st.markdown(f"<div class='tree'>{tree_html(root, root)}</div>", unsafe_allow_html=True)
        files = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()
                       and not any(part in HIDE or part.startswith(".") for part in p.relative_to(root).parts))
        pick = st.selectbox("Preview a file", files, index=None, placeholder="Preview a file…",
                            label_visibility="collapsed")
        if pick:
            preview(root, pick)
    return run_dir


def record_actions(agent: PaperAgent, run_dir: Path | None) -> None:
    if run_dir is None:
        st.caption("Runs appear here as you validate or reuse.")
        return
    spec = agent.spec(summarise_run(run_dir)["method"])
    c1, c2, c3 = st.columns([1, 1.3, 1.3], vertical_alignment="center")
    with c1.popover("Include", width="stretch", icon=":material/tune:"):
        inc = {k for k, on in {
            "crate": st.checkbox("Run crate", value=True), "results": st.checkbox("Results", value=True),
            "methods": st.checkbox("Methods paragraph", value=True), "references": st.checkbox("References", value=True),
            "figures": st.checkbox(f"Figures ({len(ss.figures)})", value=bool(ss.figures), disabled=not ss.figures),
        }.items() if on}
    ss.export_include = inc
    from recrate.report import build_report

    st.download_button("Report page (HTML, opens anywhere)", build_report(agent.path).read_bytes(),
                       file_name=f"{agent.name[:40]}-report.html", mime="text/html", width="stretch",
                       icon=":material/description:",
                       help="One page with the verdict, every compared value, figures, follow-ups and every run")
    if c2.button("Add to the record", width="stretch", icon=":material/bookmark_add:",
                 help="Register this run in the research object's reproducibility record"):
        st.toast(headline(publish_back(spec, run_dir)["summary"]))
    bundle = ss.get("bundle")
    if bundle and Path(bundle).exists() and Path(bundle).name.startswith(run_dir.name):
        c3.download_button("Download bundle", Path(bundle).read_bytes(), file_name=Path(bundle).name,
                           mime="application/zip", width="stretch", type="primary", icon=":material/download:")
    elif c3.button("Export bundle", type="primary", width="stretch", icon=":material/inventory_2:"):
        ss.bundle = str(export_bundle(agent, spec, run_dir, figures=ss.figures, include=ss.export_include))
        st.rerun()


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
    diag = agent.diagnosis() if hasattr(agent, "diagnosis") else None
    if diag:
        readiness = diag.get("readiness", "unknown")
        banner = {"ready": st.success, "ready-with-gaps": st.warning}.get(readiness, st.error)
        banner(f"Readiness: {readiness}")
        fixes = [f for f in (diag.get("crate_findings", [])
                             + [x for m in diag.get("methods", []) for x in m.get("findings", [])])
                 if f.get("severity") in ("blocking", "warning")]
        if fixes:
            with st.expander(f"What's missing and how to fix it ({len(fixes)})"):
                st.table(pd.DataFrame([{"severity": f["severity"],
                                        "finding": f["message"], "fix": f.get("fix", "")} for f in fixes]))
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


def welcome_screen() -> None:
    _, mid, _ = st.columns([1, 2.4, 1])
    with mid:
        if ss.get("build_error"):
            st.error(ss.pop("build_error"))
        st.html("<div class='onramp'><h1>Welcome to ReCRATE</h1><div class='sub'>Paste a preprint link and we rebuild "
                "it into a research object you can check.</div></div>")
        with st.form("welcome-link", border=False):
            text = st.text_input("A DOI or link", label_visibility="collapsed",
                                 placeholder="A DOI or link: arXiv, bioRxiv, a paper DOI, WorkflowHub, Zenodo")
            go = st.form_submit_button("Start", type="primary", width="stretch")
            if go and text.strip():
                add_and_build(text.strip(), [])
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
        st.markdown("<div class='eyebrow' style='margin-top:1.2rem'>What you'll get</div>", unsafe_allow_html=True)
        c = st.columns(3)
        for col, (t, d) in zip(c, [("Reproduce", "Each method run on the research's own data, compared with the "
                                    "published result."),
                                   ("Reuse", "Your data checked against what each method expects; parameters adjusted "
                                    "and recorded."),
                                   ("Record", "Every run as a Workflow Run RO-Crate, with an export for your paper.")]):
            col.markdown(f"**{t}**  \n<span class='muted'>{d}</span>", unsafe_allow_html=True)


def review_screen() -> None:
    agent = PaperAgent.load(ss.agent_dir)
    _, mid, _ = st.columns([1, 2.4, 1])
    with mid:
        st.html("<div class='onramp'><h1>Review the crate</h1><div class='sub'>Here is what we built. "
                "Check it looks right before validating.</div></div>")
        ups = st.file_uploader("or upload a crate you already have", type=["zip", "json", "pdf"],
                               accept_multiple_files=True, key="review-upload")
        sig = [(u.name, u.size) for u in ups or []]
        if not ups:
            ss._review_upload_sig = None  # empty uploader: forget the last upload so a re-upload is processed
        if ups and sig != ss.get("_review_upload_sig"):
            ss._review_upload_sig = sig  # handle each new upload once, not on every rerun
            add_and_build("", list(ups))
            st.rerun()
        st.markdown("#### What's in the crate")
        st.html(tree_html(agent.path, agent.path))
        diag = agent.diagnosis() if hasattr(agent, "diagnosis") else None
        flags = []
        if diag:
            flags = [f for f in (diag.get("crate_findings", [])
                                 + [x for m in diag.get("methods", []) for x in m.get("findings", [])])
                     if f.get("severity") in ("blocking", "warning")]
        if flags:
            for f in flags:
                st.markdown(f"\u26a0 {f['message']}")
        else:
            st.markdown("<span class='muted'>No problems spotted</span>", unsafe_allow_html=True)
        ok, off = st.columns([2, 1])
        if ok.button("Looks good \u2014 validate output \u2192", type="primary", width="stretch"):
            ss.stage = "workstation"
            st.rerun()
        # Settled decision: "Something's off" returns to Welcome (simplest useful action), clearing the build.
        if off.button("Something's off", width="stretch"):
            ss.stage = "welcome"
            ss.agent_dir = None
            ss.ro = {}
            st.rerun()


# ---- the top bar: connect, who, reset -------------------------------------------------------------------
def connect_box() -> None:
    kind, label = engine_plan()
    st.markdown(f"**Chat:** {label}")
    choice = st.radio("Who answers in the chat", ["auto", "local", "claude", "commands"], key="engine_choice",
                      format_func={"auto": "Automatic: a free local model if one is running, else Claude if a key is set",
                                   "local": "An open model on this computer (free, no key)",
                                   "claude": "Claude (API key)", "commands": "Simple commands only"}.get)
    if choice in ("auto", "local"):
        models = available_models(ss.local_url)
        if models:
            current = ss.get("local_model") if ss.get("local_model") in models else pick_model(models)
            ss.local_model = st.selectbox("Model", models, index=models.index(current))
            st.caption("Models need tool calling: qwen2.5, llama3.1, mistral-nemo work well.")
        else:
            st.markdown("No local model server found. To use an open model for free:")
            st.code("brew install ollama        # or download it from ollama.com\nollama serve\n"
                    "ollama pull qwen2.5:7b", language="bash")
            st.caption("LM Studio and llama.cpp work too: start their server and set the address below.")
        url = st.text_input("Server address (OpenAI-compatible)", value=ss.local_url)
        if url != ss.local_url:
            ss.local_url = url
            available_models.clear()
            st.rerun()
        if st.button("Check again", icon=":material/refresh:"):
            available_models.clear()
            st.rerun()
    if choice == "claude":
        key = st.text_input("Anthropic API key", type="password", placeholder="sk-ant-…",
                            help="Used for this session only; never saved.")
        if key and st.button("Use this key", type="primary"):
            ss.api_key = key.strip()
            st.rerun()
        if ss.get("api_key") and st.button("Forget the key"):
            ss.api_key = None
            st.rerun()
    st.divider()
    st.markdown("**Or talk to it in Claude Code / Claude Desktop** (your Claude subscription, no key):")
    if ss.get("agent_dir"):
        exe = str(Path(sys.executable).with_name("recrate"))
        st.code(f'claude mcp add {slug(Path(ss.agent_dir).name)[:30]} -- "{exe}" serve "{ss.agent_dir}"',
                language="bash", wrap_lines=True)
    else:
        st.caption("Open a research object first; the command to copy appears here.")


def topbar() -> None:
    with st.container(key="topbar"):
        c = st.columns([7, 1.2, 1.6, .8], vertical_alignment="center")
        c[0].image(str(IMG / "logo.svg"), width=180)
        kind, label = engine_plan()
        with c[1].popover("Connect", width="stretch", icon=":material/hub:", help=f"Chat: {label}"):
            connect_box()
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
topbar()
if ss.stage == "workstation" and ss.get("agent_dir"):
    left, right = st.columns([1, 1.25], gap="small")
    with right:
        with st.container(key="workcard"):
            workstation(PaperAgent.load(ss.agent_dir))
    with left:
        with st.container(key="chatcard"):
            chat_card()
elif ss.stage == "review" and ss.get("agent_dir"):
    review_screen()
else:
    welcome_screen()
