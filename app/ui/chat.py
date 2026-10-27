"""The chat card: messages, tool-call chips, attachments (figures, tables, run crates), suggested questions."""

from __future__ import annotations

import html
import re
from pathlib import Path

import pandas as pd
import streamlit as st

from recrate.agent import PaperAgent
from recrate.chat import follow_up_questions, suggested_questions
from recrate.sources import identify
from ui.building import add_and_build
from ui.common import ICON, chip, short, ss
from ui.engines import engine_plan, make_engine


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
                st.caption(f"{ICON[r['status']]} {r['check']}: published {r['paper']} · new data {r['your data']}"
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
    src = f"<p><a href='{html.escape(c['doi'])}' style='color:var(--accent)'>{html.escape(c['doi'])}</a></p>" if c.get("doi") else ""
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
    entry = st.chat_input("Ask about the research, or attach new data…" if has_agent else
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
