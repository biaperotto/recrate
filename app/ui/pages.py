"""The pages: the workstation card, the welcome page and the top bar."""

from __future__ import annotations

import json
import re
from pathlib import Path

import streamlit as st

from recrate.agent import PaperAgent
from recrate.chat import ChatTools, claude_code_binary
from recrate.record import find_runs
from ui.building import open_example
from ui.common import EXAMPLES, IMG, ROOT, SECTIONS, ss
from ui.engines import AGENTS, available_models, engine_plan, pick_model
from ui.record import record_actions, record_section
from ui.reproduce import validate_actions, validate_section
from ui.reuse import reuse_actions, reuse_section


# ---- the workstation card ------------------------------------------------------------------------------
def workstation(agent: PaperAgent) -> None:
    tools: ChatTools = ss.tools
    tools.runner, tools.who = "auto", ss.who or "anonymous"  # each workflow's language picks its runner
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
# ---- the start page ------------------------------------------------------------------------------------
GET = [("Reproduce", "Each method run on the research's own data, compared with the published result.", "replay"),
       ("Reuse", "New data checked against what each method expects; parameters adjusted and recorded.", "tune"),
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
                    "on them, checks new data before you reuse the method, and records every run.</p>",
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
# ---- the top bar: connect, reset -------------------------------------------------------------------
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
            ss.landing = False  # stay on the workspace (chat and Start panel), not the welcome page
            st.rerun()
