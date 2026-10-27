"""Workstation helpers: cached checks, progress, the paper's text and environment views."""

from __future__ import annotations

import html
from pathlib import Path

import pandas as pd
import streamlit as st

from recrate.agent import PaperAgent
from recrate.crate import CrateError
from recrate.paper import passages
from recrate.pipeline import check_inputs
from ui.common import ss


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
