"""The chat card: messages, tool-call chips, attachments (figures, tables, run crates), suggested questions.

An answer is computed in a background thread and shown as it grows (``live_reply``): Streamlit reruns the whole
script on any click, and an answer computed in the script would stop with it, so a click in the workstation
while the chat thinks would cut it off."""

from __future__ import annotations

import html
import re
import threading
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
def render_parts(parts: list[dict], idx) -> None:
    for j, part in enumerate(parts):
        if part["kind"] == "text":
            st.markdown(part["text"])
        elif part["kind"] == "tool":
            st.caption(chip(part["name"], part.get("args")))
        elif part["kind"] == "attachment":
            render_attachment(part["data"], f"{idx}-{j}")
def render_message(msg: dict, idx) -> None:
    with st.container(key=f"msg-{'u' if msg['role'] == 'user' else 'a'}-{idx}"):
        render_parts(msg["parts"], idx)
# ---- the answer, computed in a thread and shown as it grows ----------------------------------------
def effects_of(name: str, result, tools, prompt: str) -> dict:
    """What a tool result changes outside the chat: the passage to highlight in the paper's text, the run the
    workstation shows. Computed where the tool ran (the answer's thread), applied to the session by apply_effects."""
    out: dict = {}
    for a in result.attachments:
        if a["type"] == "passages" and a["items"]:
            from recrate.workspace import quote_for

            terms = re.findall(r"[a-z]{4,}", prompt.lower())
            out["highlight"] = quote_for(a["items"][0]["text"], terms) or " ".join(a["items"][0]["text"].split())[:200]
    if name in {"reproduce", "run_on_my_data"} and tools.last_run is not None:
        out["run_name"] = tools.last_run.name
    return out
def apply_effects(effects: list[dict]) -> None:
    for e in effects:
        if "highlight" in e:
            ss.highlight = e["highlight"]
        if "run_name" in e:
            ss.run_name = e["run_name"]
def start_reply(sent: str) -> None:
    """Answer in a background thread. It writes into ``ss.live`` (plain Python objects, never Streamlit calls, which
    need the script's thread): the reply's parts as they come, the tool progress, the effects to apply, and done."""
    engine, tools, prompt = ss.engine, ss.tools, ss.get("last_prompt", "")
    live: dict = {"parts": [], "effects": [], "applied": 0, "progress": None, "done": False}

    def progress(i: int, n: int, name: str) -> None:
        live["progress"] = (i, n, name)

    def work() -> None:
        tools.progress = progress
        try:
            for ev in engine.send(sent):
                if ev[0] == "text":
                    live["parts"].append({"kind": "text", "text": ev[1]})
                elif ev[0] == "tool":
                    live["parts"].append({"kind": "tool", "name": ev[1], "args": ev[2]})
                elif ev[0] == "result":
                    live["effects"].append(effects_of(ev[1], ev[2], tools, prompt))
                    live["parts"] += [{"kind": "attachment", "data": a} for a in ev[2].attachments]
                    live["progress"] = None
        except Exception as e:  # noqa: BLE001 - connection or model errors are shown, not raised
            live["parts"].append({"kind": "attachment", "data": {
                "type": "error", "text": f"Something went wrong: {type(e).__name__}: {e}"}})
        finally:
            if tools.progress is progress:  # not a workstation action's bar, if one is running meanwhile
                tools.progress = None
            live["done"] = True

    ss.live = live
    threading.Thread(target=work, daemon=True, name="recrate-chat").start()
@st.fragment(run_every=0.4)
def live_reply() -> None:
    """The answer being written, redrawn from the thread's output every 0.4 s without rerunning the page; filed into
    the history once complete, then the page is rerun so the questions that follow it appear under it."""
    live = ss.get("live")
    if not live:
        return
    idx = len(ss.history)
    with st.container(key=f"msg-a-{idx}"):
        render_parts(list(live["parts"]), idx)
        if not live["done"]:
            p = live["progress"]
            if p:
                st.progress(p[0] / max(p[1], 1), text=f"{p[2]} ({p[0] + 1}/{p[1]})")
            else:
                st.markdown("<span class='muted rc-thinking'>Thinking</span>", unsafe_allow_html=True)
    apply_effects(live["effects"][live["applied"]:])
    live["applied"] = len(live["effects"])
    if live["done"]:
        ss.history.append({"role": "assistant", "parts": list(live["parts"])})
        del ss.live
        st.rerun()
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
def offer(qs: list[str]) -> None:
    """Remember what was suggested: a suggestion not taken comes after new ones under the next answer."""
    ss.offered = list(dict.fromkeys([*ss.get("offered", []), *qs]))
def suggestions(agent: PaperAgent) -> None:
    """Before the conversation: questions to start from, chosen from what this research object contains. Once it has
    started: two questions that follow from the last answer, under it. They are chosen once per answer (not on every
    rerun of the page), so they hold still while the person works in the workstation."""
    asked = asked_questions()
    if not chat_started():
        qs = [q for q in suggested_questions(agent, limit=8) if q not in asked][:4]
        offer(qs)
        question_buttons(qs, "start", "Try asking")
    elif ss.history and ss.history[-1]["role"] == "assistant":
        mark = len(ss.history)
        if (ss.get("next_qs") or (None, []))[0] != mark:
            question = next((m["parts"][0]["text"] for m in reversed(ss.history) if m["role"] == "user" and m.get("parts")), "")
            qs = follow_up_questions(agent, ss.history[-1]["parts"], asked, question=question,
                                     offered=set(ss.get("offered", [])))
            ss.next_qs = (mark, qs)
            offer(qs)
        question_buttons(ss.next_qs[1], "next", "Ask next")
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
    answering = bool(ss.get("live"))
    box = st.container(key="chatscroll")
    with box:
        if has_agent:
            context_card(PaperAgent.load(ss.agent_dir))
        if not ss.history:
            render_message({"role": "assistant", "parts": [{"kind": "text", "text": INTRO}]}, "intro")
        for i, msg in enumerate(ss.history):
            render_message(msg, i)
        if answering:
            live_reply()
        elif has_agent:
            suggestions(PaperAgent.load(ss.agent_dir))
    entry = st.chat_input("Ask about the research, or attach new data…" if has_agent else
                          "Paste a DOI or link, or attach a paper or crate…", accept_file="multiple",
                          disabled=answering)
    st.markdown("<div class='muted' style='font-size:.72rem;margin-top:-.2rem'>Enter to send · Shift+Enter for a new "
                "line</div>", unsafe_allow_html=True)
    picked = ss.pop("picked_question", None)
    if answering or (not entry and not picked):
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
    start_reply(sent)
    st.rerun()  # the question in place, and the answer under it as it is written
