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

import streamlit as st

from recrate.agent import PaperAgent
from ui import common, style
from ui.building import start
from ui.chat import chat_card
from ui.common import IMG, ss
from ui.engines import AGENTS, default_agent
from ui.pages import landing, topbar, workstation
from ui.sidebar import sidebar

st.set_page_config(page_title="ReCRATE", page_icon=str(IMG / "favicon.svg"), layout="wide",
                   initial_sidebar_state="collapsed")
style.inject()
common.init_state()

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
