"""ReCRATE web app: talk to a paper, reproduce it, and run its methods on your images.

    pip install -e ".[app]"
    recrate demo-data examples/demo
    recrate agentify --paper examples/demo/paper.md --crate examples/demo/paper_crate --out examples/demo/paper_agent
    streamlit run app/app.py
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from recrate.agent import PaperAgent
from recrate.crate import CrateError
from recrate.methods import methods_paragraph
from recrate.pipeline import check_inputs, reproduce, run_on_data
from recrate.record import find_runs, headline, publish_back, summarise_record
from recrate.runcrate import approve

st.set_page_config(page_title="ReCRATE", layout="wide")
ROOT = Path(__file__).resolve().parent.parent
BADGE = {"validated": "✅ validated", "differs": "⚠️ differs", "failed": "❌ failed", "not validated": "• not validated"}

st.title("ReCRATE")
st.caption("A paper you can talk to and run on your own data, with checks and a record of every run.")

with st.sidebar:
    who = st.text_input("Your name (recorded in each run)", value="anonymous")
    runner = st.selectbox("Runner", ["auto", "fiji", "reference", "script"],
                          help="auto picks Fiji for macros (Python reference without FIJI_PATH) and the "
                               "paper's own script for Python methods.")

# ---- 1. The paper agent ----------------------------------------------------------------
st.header("1. The paper")
agent_path = st.text_input("Paper agent folder (made with `recrate agentify`), or a crate folder",
                           value=str(ROOT / "examples" / "demo" / "paper_agent"))
try:
    agent = PaperAgent.load(agent_path)
except (CrateError, FileNotFoundError) as e:
    st.info(f"Load a paper agent to start ({e}). Run `recrate demo-data examples/demo` and "
            "`recrate agentify ...` first (see the README).")
    st.stop()

card = agent.card()
st.subheader(card["title"])
if card.get("doi"):
    st.markdown(f"[{card['doi']}]({card['doi']})")
st.caption(f"Crate: {card['crate_source']}" + (" · draft, needs confirming" if card.get("draft") else ""))
if card["methods"]:
    st.table(pd.DataFrame([{"method": m["name"], "language": m["language"], "validation": BADGE.get(m["status"],
                            m["status"]), "detail": m["detail"]} for m in card["methods"]]))
else:
    st.warning("No runnable methods yet: this agent answers from the paper. See DRAFT_ME.md in the agent folder.")

q = st.text_input("Ask the paper", placeholder="e.g. what pixel size were the images? how is debris removed?")
if q:
    for h in agent.search(q, 3):
        st.markdown(f"> {' '.join(h['text'].split())[:700]}")
    st.caption("Connect the agent to Claude (`recrate serve`) for full answers grounded in these passages.")

if not card["methods"]:
    st.stop()

# ---- 2. Method and reproduction ---------------------------------------------------------
st.header("2. Pick a method and reproduce it")
method = st.selectbox("Method", [m["name"] for m in card["methods"]])
spec = agent.spec(method)
mc = spec.summary()
c1, c2 = st.columns(2)
with c1:
    st.markdown(f"**{mc['method']}**: {mc['method_description']}")
    st.markdown(f"Workflow `{mc['workflow']}` ({mc['language']}), declared software: "
                f"{', '.join(mc['software']) or 'not stated'}")
    st.markdown("**Expects**")
    st.table(pd.DataFrame([{"property": k, "value": str(v)} for k, v in mc["expects"].items()]))
with c2:
    st.markdown("**Parameters**")
    st.table(pd.DataFrame([{"name": k, "default": v["default"], "scaling": v.get("scaling", "")}
                           for k, v in mc["parameters"].items()]))
    if st.button("Reproduce on the paper's data", disabled=not mc["has_example_data"]):
        with st.spinner("Running..."):
            st.session_state["repro"] = reproduce(spec, agent.runs_dir, runner=runner, agent=who)
    repro = st.session_state.get("repro")
    if repro and repro.comparison:
        (st.success if repro.comparison.reproduced else st.error)(repro.comparison.headline())

# ---- 3. My images ---------------------------------------------------------------------
st.header("3. Check my images")
mode = st.radio("Your images", ["Folder path", "Upload files"], horizontal=True)
data_dir: Path | None = None
if mode == "Folder path":
    p = st.text_input("Folder with your images", value=str(ROOT / "examples" / "demo" / "my_images"))
    data_dir = Path(p) if p and Path(p).exists() else None
else:
    files = st.file_uploader("TIFF / OME-TIFF images", type=["tif", "tiff"], accept_multiple_files=True)
    if files:
        tmp = Path(st.session_state.setdefault("upload_dir", tempfile.mkdtemp(prefix="recrate-upload-")))
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        for f in files:
            (tmp / f.name).write_bytes(f.getbuffer())
        data_dir = tmp

if data_dir:
    report = check_inputs(spec, data_dir)
    icon = {"ok": "✅", "warn": "⚠️", "fail": "❌"}
    st.table(pd.DataFrame([{**r, "status": f"{icon[r['status']]} {r['status']}"} for r in report.as_rows()]))
    if report.adjustments:
        st.warning("Proposed adjustments so the method measures the same physical sizes:\n\n" +
                   "\n".join(f"- **{a.parameter}**: {a.old} → {a.new} ({a.reason})" for a in report.adjustments))
    if not report.can_run:
        st.error("Your data does not fit this method yet. Fix the failed checks first.")
    else:
        apply = st.checkbox("Apply the adjustments", value=True, disabled=not report.adjustments)
        channel = st.number_input("Channel to analyse (0-based)", min_value=0, value=0, step=1)
        st.header("4. Results and record")
        if st.button(f"Run {method} on my images"):
            with st.spinner("Running..."):
                st.session_state["reuse"] = run_on_data(spec, data_dir, agent.runs_dir, runner=runner, agent=who,
                                                        auto_adjust=apply, channel=int(channel))

reuse = st.session_state.get("reuse")
if reuse:
    run_dir = reuse.run_dir
    st.markdown(f"**Parameters used:** `{reuse.params}`")
    st.dataframe(pd.read_csv(run_dir / "outputs" / "results.csv"), use_container_width=True)
    overlays = sorted((run_dir / "outputs" / "overlays").glob("*.png"))
    if overlays:
        st.image([str(o) for o in overlays], caption=[o.stem for o in overlays], width=260)
        a1, a2 = st.columns(2)
        if a1.button("Looks right: approve"):
            approve(run_dir, who, True)
            st.success("Approval recorded in the run crate.")
        if a2.button("Something is wrong: reject"):
            approve(run_dir, who, False)
            st.warning("Rejection recorded in the run crate.")
    st.subheader("Methods paragraph")
    st.text_area("Copy into your paper", methods_paragraph(run_dir), height=160)
    zip_path = shutil.make_archive(str(run_dir), "zip", run_dir)
    d1, d2 = st.columns(2)
    d1.download_button("Download run crate (.zip)", Path(zip_path).read_bytes(), file_name=Path(zip_path).name)
    if d2.button("Publish back to the paper's record"):
        st.success(headline(publish_back(spec, run_dir)["summary"]))

# ---- Record --------------------------------------------------------------------------
st.header("The paper's reproducibility record")
runs = find_runs(spec, agent.runs_dir)
if runs:
    st.info(headline(summarise_record(runs)))
    st.dataframe(pd.DataFrame([{k: r[k] for k in ("when", "method", "mode", "verdict", "by", "images", "approved")}
                               for r in runs]), use_container_width=True)
else:
    st.caption("No runs yet.")
