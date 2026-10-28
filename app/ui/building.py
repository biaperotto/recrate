"""Turning what the user gives (a crate, a paper, a link, files) into a research object, and the Start panel."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import streamlit as st

from recrate.agent import PaperAgent, agentify, revalidate
from recrate.chat import ChatTools
from recrate.sources import fetch, from_upload, identify
from ui.common import EXAMPLES, ROOT, slug, ss
from ui.engines import engine_plan, make_engine


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
        env_line = f"\n\nPublished **{env.published or 'on an unknown date'}**." + (
            f" {len(known)} of {len(env.software)} software versions determined"
            + (": " + ", ".join(f"{s.name} {s.version}" for s in known[:5]) if known else "") + "."
            if env.software else " The crate declares no software versions.")
    ss._goto = "validate"
    # "Ready" only when something can run here; a method blocked by a missing tool or runtime is said as such
    blocked = bool(a.methods) and all(m.readiness == "blocked" for m in a.methods)
    reusable = bool(a.methods) and all(m.readiness in {"blocked", "reusable"} for m in a.methods) and not blocked
    head = ("Built, but it can't run here" if blocked else "Reusable, not reproducible" if reusable
            else "Ready" if a.has_methods else "Recreated the RO-Crate")
    say(f"{head}: **{a.card()['title']}**." + search_line + env_line
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
