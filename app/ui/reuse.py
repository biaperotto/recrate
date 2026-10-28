"""Reuse: the user's data checked against the method, parameters explained, then run and recorded."""

from __future__ import annotations

import html
from pathlib import Path

import pandas as pd
import streamlit as st

from recrate.agent import PaperAgent
from recrate.chat import ChatTools
from recrate.pipeline import run_on_data
from recrate.sources import fetch_dataset
from recrate.workspace import parameter_sources, whatif
from ui.common import EXAMPLES, ICON, fmt_bytes, ss
from ui.helpers import cached_check, data_signature, latest, note_for_chat, run_with_progress
from ui.results import results_tab


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
    st.markdown("<div class='eyebrow'>1 · New data: yours, or any data that isn't the paper's</div>", unsafe_allow_html=True)
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
            up = st.file_uploader("Data files", accept_multiple_files=True, label_visibility="collapsed")
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
            verdict = "<span class='muted'>add data to check</span>" if not fit else ICON[fit["status"]] + " " + {
                "ok": "fits as is", "warn": f"fits with {len(adj)} adjustment(s)" if adj else "fits, with warnings",
                "fail": "doesn't fit yet"}[fit["status"]]
            st.markdown(f"**{m.name}** &nbsp; {verdict}", unsafe_allow_html=True)
            if fit:
                st.dataframe(pd.DataFrame([{"Property": x["check"], "Paper's data": str(x["paper"]),
                                            "New data": str(x["your data"]),
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
            # keyed by the run: a new run gets a new switch, open, since Streamlit applies `value` only once
            if run and st.toggle("Show the latest run on new data", key=f"showrun-{m.id}-{run.name}",
                                 value=ss.get("run_name") == run.name):
                results_tab(agent, run, data_dir)
    if missing_px:
        with st.form("pixel-size"):
            st.markdown("**These images have no pixel size.** Enter it from the acquisition metadata; it is written "
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
    why_not = None if can else ("Add the data first" if not fit else
                                "Give a reason for your own values" if missing else "The data doesn't fit yet")
    if len(names) == 1:
        c1.markdown(f"**{name}**")
    if c2.button("Run on new data", type="primary", width="stretch", disabled=not can, icon=":material/play_arrow:",
                 help=why_not):
        spec = agent.spec(m.id)
        adj = fit["adjustments"] if fit else []
        out = run_with_progress(f"Running {m.name}", lambda: run_on_data(
            spec, data_dir, agent.runs_dir, runner="auto", agent=tools.who, overrides=overrides or None,
            progress=tools.progress, scale_ratio=ss.get("scale_ratio")))
        ss.run_name = out.run_dir.name
        tools.last_run = out.run_dir
        if adj or overrides:
            tools.whatifs[out.run_dir.name] = run_with_progress(
                "Running with the published values", lambda: whatif(spec, data_dir, spec.default_params(), runner="auto"))
        note_for_chat(f"Ran **{m.name}** on new data ({', '.join(f'{k} = {v}' for k, v in out.params.items())}).")
        st.rerun()
    if why_not:
        st.caption(why_not)
