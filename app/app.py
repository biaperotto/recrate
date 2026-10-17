"""ReCRATE web app: open a method, reproduce it, check your images, run and record.

    pip install -e ".[app]"
    recrate demo-data examples/demo
    streamlit run app/app.py
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from recrate.crate import CrateError, load_crate
from recrate.methods import methods_paragraph
from recrate.pipeline import check_inputs, reproduce, run_on_data
from recrate.record import find_runs, headline, publish_back, summarise_record
from recrate.runcrate import approve

st.set_page_config(page_title="ReCRATE", layout="wide")
ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs"

st.title("ReCRATE")
st.caption("Reuse a published method on your data: checked against what the paper declares, and recorded.")

with st.sidebar:
    who = st.text_input("Your name (recorded in each run)", value="anonymous")
    runner = st.selectbox("Runner", ["auto", "fiji", "python"],
                          help="auto uses Fiji when FIJI_PATH is set, otherwise the Python reference runner.")

# ---- 1. Open the method -------------------------------------------------------
st.header("1. Open the method")
crate_path = st.text_input("Input crate folder (from WorkflowHub, Zenodo, or drafted from the paper)",
                           value=str(ROOT / "examples" / "demo" / "paper_crate"))
try:
    spec = load_crate(crate_path)
except (CrateError, FileNotFoundError) as e:
    st.info(f"Load a crate to start. ({e}) Tip: run `recrate demo-data examples/demo` first.")
    st.stop()

card = spec.summary()
c1, c2 = st.columns([3, 2])
with c1:
    st.subheader(card["name"])
    st.write(card["description"])
    if card["paper"]:
        st.markdown(f"**Paper:** [{card['paper']}]({card['paper']})")
    st.markdown(f"**Workflow:** `{card['workflow']}` ({card['language']}), "
                f"declared software: {', '.join(card['software']) or 'not stated'}")
with c2:
    st.markdown("**Expects**")
    st.table(pd.DataFrame([{"property": k, "value": str(v)} for k, v in card["expects"].items()]))
    st.markdown("**Parameters**")
    st.table(pd.DataFrame([{"name": k, "default": v["default"], "scaling": v.get("scaling", "")}
                           for k, v in card["parameters"].items()]))

# ---- 2. Reproduce ---------------------------------------------------------------
st.header("2. Reproduce on the paper's data")
if st.button("Reproduce", disabled=not card["has_example_data"]):
    with st.spinner("Running the workflow on the paper's data..."):
        st.session_state["repro"] = reproduce(spec, RUNS, runner=runner, agent=who)
repro = st.session_state.get("repro")
if repro and repro.comparison:
    comp = repro.comparison
    (st.success if comp.reproduced else st.error)(comp.headline())
    st.table(pd.DataFrame([{"measure": m.measure, "reported": m.reported, "reproduced": m.reproduced,
                            "difference": f"{m.rel_diff:.1%}", "tolerance": f"{m.tolerance:.0%}",
                            "within": "yes" if m.within else "no"} for m in comp.measures]))
    st.caption(f"Run crate: {repro.run_dir}")

# ---- 3. Check my images -----------------------------------------------------------
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
        st.warning("Proposed adjustments so the method means the same physical sizes:\n\n" +
                   "\n".join(f"- **{a.parameter}**: {a.old} → {a.new} ({a.reason})" for a in report.adjustments))
    if not report.can_run:
        st.error("Your data does not fit this method yet. Fix the failed checks first.")
    else:
        apply = st.checkbox("Apply the adjustments", value=True, disabled=not report.adjustments)
        channel = st.number_input("Channel to analyse (0-based)", min_value=0, value=0, step=1)

        # ---- 4. Run and record ------------------------------------------------------
        st.header("4. Results and record")
        if st.button("Run on my images"):
            with st.spinner("Running..."):
                st.session_state["reuse"] = run_on_data(spec, data_dir, RUNS, runner=runner, agent=who,
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
        rec = publish_back(spec, run_dir)
        st.success(headline(rec["summary"]))

# ---- Record -------------------------------------------------------------------------
st.header("The paper's reproducibility record")
runs = find_runs(spec, RUNS)
if runs:
    st.info(headline(summarise_record(runs)))
    st.dataframe(pd.DataFrame([{k: r[k] for k in ("when", "mode", "verdict", "by", "images", "approved")}
                               for r in runs]), use_container_width=True)
else:
    st.caption("No runs yet.")
