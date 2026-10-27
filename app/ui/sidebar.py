"""The Research Objects sidebar: every research object on this computer, its files and its crate's validation."""

from __future__ import annotations

import html
from pathlib import Path

import streamlit as st

from recrate.agent import PaperAgent
from ui.common import short, ss
from ui.record import open_research_object, research_objects, ro_status, tree_html


def sidebar() -> None:
    """Research Objects: every one on this computer, each as the folder tree of its files, like the Record tab."""
    with st.sidebar:
        st.markdown("### Research Objects")
        items = research_objects()
        if not items:
            st.caption("Add a paper (PDF, DOI, arXiv link) or an RO-Crate, or open an example. Once its RO-Crate is "
                       "found or built, its research object appears here, with its files.")
            return
        st.caption("✅ reproduced · 🟠 partly · ⚪ not checked yet · 📄 no methods yet · ⏳ being made runnable")
        current = Path(ss.agent_dir).resolve() if ss.get("agent_dir") else None
        for d in items:
            try:
                a = PaperAgent.load(d)
            except Exception:  # noqa: BLE001 - a broken folder is skipped, not fatal
                continue
            title = a.manifest.get("title") or a.name
            here = d == current
            with st.expander(f"{ro_status(a)} {short(title, 60)}", expanded=here):
                n_runs = sum(1 for _ in a.runs_dir.glob("*/ro-crate-metadata.json")) if a.runs_dir.exists() else 0
                st.caption(f"{a.manifest.get('crate_source') or 'crate'} · {len(a.methods)} method(s) · {n_runs} run(s)"
                           + (" · draft" if a.manifest.get("draft") else ""))
                if here:
                    st.markdown(":blue-badge[open now]")
                elif st.button("Open", key=f"ro-open-{abs(hash(str(d))) % 10**8}", icon=":material/folder_open:",
                               width="stretch"):
                    open_research_object(d)
                    st.rerun()
                st.markdown(f"<div class='tree'>{tree_html(d, d)}</div>", unsafe_allow_html=True)
                st.divider()
                crate_panel(a, f"side-{abs(hash(str(d))) % 10**8}")
def crate_panel(agent: PaperAgent, key: str) -> None:
    """The research object's RO-Crate: validated with the RO-Crate validator when it was created (and again if it has
    changed since), so its status shows before anything is downloaded; then the download."""
    from recrate.export import _how, crate_validation, export_crate, validator

    if agent.crate_dir is None:
        return
    try:  # the stored result only: the validator itself (a minute or more) never runs while the page draws
        v = crate_validation(agent.path, run=False)
    except Exception as e:  # noqa: BLE001 - shown, not raised
        v = {"error": f"{type(e).__name__}: {e}"}
    st.markdown("**RO-Crate**" + (" &nbsp; :orange-badge[draft]" if agent.manifest.get("draft") else ""))
    st.caption(_how(agent.manifest))
    if v is None or v.get("stale"):
        if st.button("Validate again" if v else "Validate", key=f"validate-{key}", icon=":material/fact_check:",
                     help="Runs the RO-Crate validator, which fetches its profiles: a minute or two"):
            with st.spinner("Validating the RO-Crate…"):
                try:
                    crate_validation(agent.path, refresh=True)
                except Exception as e:  # noqa: BLE001 - shown, not raised
                    st.error(f"Validation could not run: {type(e).__name__}: {e}")
            st.rerun()
    if v is None:
        st.caption("Not validated yet" + ("" if validator() else
                                           ": install the RO-Crate validator (`pip install roc-validator`)") + ".")
    elif v.get("error"):
        st.warning(f"Validation could not run: {v['error']}")
    else:
        req, rec = v["required"], v["recommended"]
        (st.success if v["passed"] else st.error)(
            f"{'Valid' if v['passed'] else 'Not valid'} · {v['profile']} · {req['passed_checks']}/{req['checks']} "
            f"required checks" + (" · changed since validated" if v.get("stale") else ""))
        st.caption(f"Recommended level: {rec['passed_checks']}/{rec['checks']} · validated {str(v.get('when', ''))[:16].replace('T', ' ')}")
        if req["issues"] or rec["issues"]:
            with st.expander(f"Validation details: {len(req['issues'])} problem(s), {len(rec['issues'])} suggestion(s)"):
                for i in req["issues"]:
                    st.markdown(f"❌ {html.escape(i['message'])}")
                for i in rec["issues"]:
                    st.caption(f"· {i['message']}")
    st.download_button("Download RO-Crate (.zip)", data=lambda: Path(export_crate(agent.path)).read_bytes(),
                       file_name=f"{agent.name[:60]}-ro-crate.zip", mime="application/zip", width="stretch",
                       type="primary" if v and v.get("passed") else "secondary", icon=":material/download:",
                       key=f"crate-dl-{key}", on_click="ignore")
