"""Reproduce: each method on the research's own data, the verdict ladder, recreated figures, follow-ups."""

from __future__ import annotations

import html
import shutil
from pathlib import Path

import pandas as pd
import streamlit as st

from recrate.agent import PaperAgent, revalidate, review_conclusion, runs_own_code
from recrate.chat import claude_code_binary
from ui.building import load_agent
from ui.common import BADGE, IMAGE_EXT, ROOT, short, ss
from ui.helpers import environment_table, latest, note_for_chat, run_with_progress, source_view
from ui.results import results_tab


# ---- Reproduce: does the research reproduce on its own data? ------------------------------------------
def validate_section(agent: PaperAgent, runs: list[dict], data_dir: Path) -> None:
    """One answer first (does it reproduce?), then one card per method with only what needs attention; the rest is
    folded away."""
    env = agent.environment
    if not agent.methods:
        empty_validate(agent)
    else:
        verdict(agent)
        for m in agent.methods:
            method_card(agent, m, runs, data_dir)
    st.markdown("<div class='eyebrow' style='margin-top:.8rem'>Details</div>", unsafe_allow_html=True)
    if agent.methods:
        from recrate.model import assumptions

        said = list(dict.fromkeys(a for m in agent.methods for a in assumptions(agent.spec(m.id), env)))
        gaps = list(dict.fromkeys(g for m in agent.methods if m.readiness != "blocked" for g in m.gaps
                                  if not g.startswith("Reusable, not reproducible")))  # said on the method itself
        unsaid = list(dict.fromkeys(gaps + said))  # a gap and an assumption can be the same sentence: once
        if unsaid:
            with st.expander(f"What the paper doesn't say ({len(unsaid)})", icon=":material/help:"):
                st.caption("Each is an assumption ReCRATE, or whoever drafted the crate, had to make.")
                st.markdown("\n".join(f"- {html.escape(a)}" for a in unsaid))
    with st.expander("Software environment" + (f" · as of {env.published}" if env and env.published else ""),
                     icon=":material/inventory_2:"):
        if env and env.published:
            st.caption(f"Publication date from {env.published_source}.")
        environment_table(agent)
    with st.expander("Read the source" + (" · cited passage highlighted" if ss.get("highlight") else ""),
                     expanded=bool(ss.get("highlight")), icon=":material/article:"):
        source_view(agent)
STEP_MARK = {"passed": "✓", "partial": "◐", "failed": "✗", "not checked": "○"}
def verdict(agent: PaperAgent) -> None:
    """The verdict sentence, written from the results (never by the model)."""
    ladders = [m.ladder for m in agent.methods if m.ladder]
    numbers = [lad["numbers"]["status"] for lad in ladders]
    show = (st.success if numbers and all(n == "passed" for n in numbers) else
            st.warning if any(n in ("partial", "failed") for n in numbers) else
            st.error if any(lad["runs"]["status"] == "failed" for lad in ladders) else st.info)
    show(f"**{agent.card()['verdict']}**")
def method_card(agent: PaperAgent, m, runs: list[dict], data_dir: Path) -> None:
    from recrate.figures import comparison_from_run
    from recrate.model import STEPS

    spec = agent.spec(m.id)
    run_dir = Path(m.run_crate) if m.run_crate and Path(m.run_crate).exists() else None
    comp = comparison_from_run(run_dir) if run_dir else None
    with st.container(border=True):
        st.markdown(f"**{m.name}** &nbsp; {BADGE.get(m.status, m.status)}")
        if m.ladder:  # where it stands, in one line: materials · runs · same numbers · same conclusion
            st.markdown(" &nbsp;·&nbsp; ".join(
                f"<span title='{html.escape(m.ladder.get(k, {}).get('reason', ''))}'>"
                f"{STEP_MARK.get(m.ladder.get(k, {}).get('status', 'not checked'), '○')} {label}</span>"
                for k, label in STEPS), unsafe_allow_html=True)
        if m.readiness == "blocked":
            for g in m.gaps:
                st.caption(f"⛔ {g}")
        elif m.readiness == "reusable":
            st.caption(f"ℹ️ {m.gaps[0]}")
        if comp and comp.measures:
            bad = [x for x in comp.measures if not x.within]
            if bad:
                st.markdown(f"**{len(bad)} of {len(comp.measures)} values differ from the paper:**")
                st.dataframe(pd.DataFrame([{"value": x.measure, "published": x.reported, "recomputed": x.reproduced,
                                            "tolerance": x.tolerance_label} for x in bad]),
                             hide_index=True, width="stretch")
            else:
                st.markdown(f"All **{len(comp.measures)}** published values reproduce, within the paper's tolerance.")
        elif m.detail:
            st.markdown(f"<span class='muted'>{html.escape(m.detail)}</span>", unsafe_allow_html=True)
        if comp and comp.measures:
            with st.expander(f"All {len(comp.measures)} values"):
                st.dataframe(pd.DataFrame([{"value": x.measure, "published": x.reported, "recomputed": x.reproduced,
                                            "difference": f"±{x.rel_diff:.3g}" if x.absolute else f"{x.rel_diff:.1%}",
                                            "tolerance (source)": x.tolerance_label,
                                            "within": "yes" if x.within else "no"}
                                           for x in sorted(comp.measures, key=lambda x: x.within)]),
                             hide_index=True, width="stretch")
        if run_dir:
            figures_block(spec, run_dir, comp)
            paper_figures_view(agent, m, run_dir)
        if m.status == "differs":
            with st.expander("Where could the difference come from?"):
                followups_view(agent, m, spec)
        run = latest(runs, m.name, "reproduce")
        if run and st.toggle("Show the outputs of the latest reproduction", key=f"showrep-{m.id}"):
            results_tab(agent, run, data_dir)  # has its own expanders, which can't sit inside another
        if run_dir:
            conclusion_review(agent, m)
def figures_block(spec, run_dir: Path, comp) -> None:
    """The reproduction as pictures: what the method found on the published data (its segmentation drawn on the
    image) next to the published picture when the research has one, and every published value next to the
    recomputed one."""
    from recrate.figures import make_run_figure

    published = sorted(p for p in (spec.path / "expected" / "figures").glob("*") if p.suffix.lower() in IMAGE_EXT) \
        if (spec.path / "expected" / "figures").is_dir() else []
    overlays = sorted((run_dir / "outputs" / "overlays").glob("*.png"))
    plot = run_dir / "outputs" / "figures" / "published_vs_reproduced.png"
    if comp and len(comp.measures) > 1 and not plot.exists():
        try:  # drawn from the run's recorded comparison, and recorded in its run crate
            make_run_figure(run_dir)
        except Exception:  # noqa: BLE001 - no figure is better than a broken page
            pass
    if not (published or overlays or plot.exists()):
        return
    st.markdown("**Figures**")
    if published or overlays:
        shown = ([(p, "Published (" + p.stem.replace("_", " ") + ")") for p in published[:2]]
                 + [(o, "Reproduced: " + o.name.removesuffix("_overlay.png")) for o in overlays[:max(1, 3 - len(published[:2]))]])
        for col, (img, cap) in zip(st.columns(len(shown)), shown):
            col.image(str(img), caption=cap, width="stretch")
        if overlays:
            st.caption("Reproduced images show what the method found, outlined on the published data.")
    if plot.exists():
        st.image(str(plot), caption="Each published value next to the recomputed one (from this run's outputs)",
                 width="stretch")
def paper_figures_view(agent: PaperAgent, m, run_dir: Path) -> None:
    """The paper's figures this method recreated, with the paper's captions. A person says whether they show the same
    thing as the paper's (recorded in the run crate): ReCRATE never decides that itself."""
    from recrate.paper_figures import VERDICTS, recreated, review_figure

    figs = recreated(run_dir, agent.path / "paper")
    if not figs:
        return
    done = sum(1 for f in figs if f["review"])
    with st.expander(f"The paper's figures, recreated ({len(figs)}) · {done} reviewed", icon=":material/compare:",
                     expanded=not done):
        st.caption("Redrawn by this run from the published data, as each caption describes. Does it show the same "
                   "thing as the paper's figure? Only a person can say, and it is recorded in the run crate.")
        for f in figs:
            st.markdown(f"**{f['label']}**" + (f" <span class='muted'>· {html.escape(short(f['caption'], 160))}</span>"
                                               if f["caption"] else ""), unsafe_allow_html=True)
            st.image(str(f["recreated"]), width="stretch")
            r = f["review"]
            if r:
                st.caption(f"{ {'same': '✅', 'differs': '⚠️'}.get(r['value'], '❔')} {r['value']}, by {r['by']}"
                           + (f": {r['note']}" if r["note"] else ""))
            else:
                k = f"figrev-{run_dir.name}-{f['recreated'].name}"
                a1, a2, a3 = st.columns([1.6, 2.2, 0.7], vertical_alignment="bottom")
                verdict = a1.segmented_control("Same figure?", VERDICTS, key=f"{k}-v")
                note = a2.text_input("What you see (recorded)", key=f"{k}-n",
                                     placeholder="e.g. same pattern; the paper also draws an unexplained inner bar")
                if a3.button("Record", key=f"{k}-b", disabled=not verdict or ss.tools.who == "anonymous",
                             help="Set your name in the top bar first" if ss.tools.who == "anonymous" else None):
                    review_figure(run_dir, ss.tools.who, f["recreated"].name, verdict, note)
                    note_for_chat(f"{ss.tools.who} reviewed the recreated {f['label']} of {m.name}: {verdict}.")
                    st.rerun()
def followups_view(agent: PaperAgent, m, spec) -> None:
    """When numbers differ: the follow-up checks, each its own recorded run. All of them stay visible."""
    from recrate.followup import followups_for, propose, run_followup

    st.caption("Each check is its own run, linked to this one; the result above is never replaced.")
    done = followups_for(agent.runs_dir, spec)
    for d in done:
        st.caption(f"✔ {d['followup']}: {d['headline']}")
    for f in propose(spec):
        c1, c2 = st.columns([4, 1], vertical_alignment="center")
        c1.markdown(f"*{f.kind}*: **{f.title}**. {f.why}" + (f' The paper: “{f.quote}”' if f.quote else ""))
        if not f.runnable:
            c2.caption(f"Needs {f.needs}")
        elif c2.button("Run check", key=f"fu-{m.id}-{f.id}", width="stretch"):
            run_with_progress(f"Running '{f.title}'", lambda f=f: run_followup(spec, f.id, agent.runs_dir,
                                                                               who=ss.tools.who,
                                                                               runner="auto"))
            note_for_chat(f"Ran the follow-up check '{f.title}' for {m.name}.")
            st.rerun()
def conclusion_review(agent: PaperAgent, m) -> None:
    """The ladder's last step: only a named person can mark it."""
    if m.review:
        st.caption(f"Same conclusion: {m.ladder['conclusion']['status']} ({m.ladder['conclusion']['reason']})")
        return
    with st.popover("Review: same conclusion?", icon=":material/rate_review:"):
        st.caption("Does this reproduction support the paper's conclusion? Recorded with your name in the run crate.")
        who = st.text_input("Your name", value=ss.tools.who if ss.tools.who != "anonymous" else "", key=f"rev-who-{m.id}")
        agrees = st.radio("Same conclusion", ["yes", "partly", "no"], horizontal=True, key=f"rev-a-{m.id}")
        note = st.text_area("Why", key=f"rev-n-{m.id}")
        if st.button("Record review", type="primary", disabled=not who.strip(), key=f"rev-b-{m.id}"):
            review_conclusion(agent.path, m.id, who, agrees, note)
            load_agent(agent.path, ss.tools.data_dir)
            st.rerun()
def empty_validate(agent: PaperAgent) -> None:
    """A research object with no runnable method: say what was found, and exactly how to make it runnable."""
    import json as _json

    g = _json.loads((agent.crate_dir / "ro-crate-metadata.json").read_text(encoding="utf-8"))["@graph"] if agent.crate_dir else []
    data = [e for e in g if e.get("@type") == "Dataset" and e["@id"] != "./"]
    code = [e for e in g if e.get("@type") == "SoftwareSourceCode"]
    links = agent.manifest.get("links") or []
    survey = agent.manifest.get("code_survey")
    if survey:
        langs = ", ".join(f"{n} {k}" for k, n in list(survey["languages"].items())[:3]) or "no recognised scripts"
        st.info(f"**The authors' code is here; its methods aren't tools yet.** ReCRATE found the paper's code and cloned "
                f"it ({survey['files']} files: {langs}"
                + (f", {survey['data_files']} data files" if survey.get("data_files") else "") + "). Which code "
                "produces which published result isn't described yet: **Make runnable** reads the paper and the code, "
                "and turns each method into a tool, checked against the published results.")
        survey["missing"] = [n for n in survey.get("needs", []) if not shutil.which(n)]  # checked now, not when built
        if survey.get("missing"):
            need = {"Rscript": "R ([cran.r-project.org](https://cran.r-project.org))",
                    "quarto": "Quarto ([quarto.org](https://quarto.org))", "julia": "Julia", "matlab": "MATLAB",
                    "python3": "Python"}
            st.warning("This code needs " + " and ".join(need.get(m, m) for m in survey["missing"]) + ", not installed "
                       "on this computer. Install it before **Make runnable**, or the methods will be described but "
                       "not run.")
    else:
        st.info("**Nothing to run yet.** ReCRATE recreated this research's RO-Crate from the paper, but found no code "
                "of the authors to run: its methods can only be re-implemented from the text, and will be said to be.")
    with st.expander(f"What the paper links to ({len(links)})", icon=":material/link:"):
        icon = {"code": "💻", "data": "📦", "workflow": "🔁", "model": "🧠"}
        for l in links:
            what = l.get("title") or l["url"].split("://", 1)[-1]
            role = ("the authors' own" if l["own"] else "cited") + (f": {l['why']}" if l.get("why") else "")
            cloned = " · cloned" if l["url"] == agent.manifest.get("code") else ""
            extra = f" · {l['type'].lower()}" if l.get("type") else ""
            st.markdown(f"{icon.get(l['kind'], '🔗')} [{html.escape(what)}]({l['url']}){extra}{cloned}  \n"
                        f"<span class='muted'>{html.escape(role)}</span>", unsafe_allow_html=True)
        if not links:
            st.markdown(("- Data: " + ", ".join(f"[{e['name']}]({e['@id']})" for e in data)) if data else
                        "- No code or data links found in the paper")
        if agent.manifest.get("code_error"):
            st.caption(f"⚠️ Couldn't clone the code: {agent.manifest['code_error']}")
        env = agent.environment
        st.markdown(f"<span class='muted'>Software and models named: {len(env.software) if env else 0} "
                    "(see *Software environment* below)</span>", unsafe_allow_html=True)
    build_panel(agent, has_code=bool(code))
def build_panel(agent: PaperAgent, has_code: bool = True) -> None:
    """Make the methods runnable from here: the /recrate agent (Paper2Agent's pipeline plus ReCRATE's checks), run by
    Claude Code on your subscription, in the background. Its progress is read from the research object's folder."""
    from recrate import build as builder

    s = builder.state(agent.path)
    with st.expander("Make its methods runnable", icon=":material/build:",
                     expanded=s["state"] == "running" or not agent.methods):
        if s["state"] == "running":
            build_progress(str(agent.path))
            return
        if s["state"] == "incomplete":
            reached = s.get("stages") or builder.stages_done(agent.path)
            st.warning(f"The last build stopped after stage *{reached[-1] if reached else 'none'}* "
                       f"({len(reached)} of 10). Press **Make runnable** again to continue from there.")
        elif s["state"] in {"done", "failed", "stopped"}:
            (st.success if s["state"] == "done" else st.warning)(f"Last build: {s['state']}.")
            if s.get("result"):
                with st.expander("What the agent reported"):
                    st.markdown(s["result"])
        st.caption("Like Paper2Agent: Claude sets up the paper's own environment, turns its methods and its code's "
                   "tutorials into tools, runs each on the published data and tests it. ReCRATE adds the checks: "
                   "every parameter quoted from the paper, every result compared with the published one, an "
                   "independent verifier."
                   + ("" if has_code else " This paper links no code, so Claude can only implement what the text "
                      "describes, and will say so."))
        if not claude_code_binary():
            st.info("Needs Claude Code (or its VS Code extension), signed in to your Claude account.")
            return
        depth = st.radio("How much", ["quick", "full"], horizontal=True, key=f"build-depth-{agent.name}",
                         format_func={"quick": "Quick: the main results, up to 3 methods (about 10–20 min)",
                                      "full": "Full: every method and the code's tutorials (can take an hour or more)"}.get,
                         label_visibility="collapsed")
        trust = st.checkbox("I understand this downloads and runs the paper's code on this computer.",
                            key=f"trust-build-{agent.name}")
        if st.button("Make runnable", type="primary", disabled=not trust, icon=":material/build:",
                     key=f"build-{agent.name}"):
            builder.build(agent.path, wait=False, quick=depth == "quick")
            note_for_chat("Started making the methods runnable (the /recrate agent, in the background).")
            st.rerun()
        with st.expander("Or run it yourself in a terminal"):
            st.code(f'cd "{ROOT}"\n.venv/bin/recrate build "{agent.path}"', language="bash")
@st.fragment(run_every=4)
def build_progress(agent_dir: str) -> None:
    from recrate import build as builder

    s = builder.state(agent_dir)
    if s["state"] != "running":  # finished: reload so the new methods appear
        load_agent(Path(agent_dir), ss.tools.data_dir if "tools" in ss else None)
        st.rerun(scope="app")
    from datetime import datetime, timezone

    events = builder.events(agent_dir, last=30)
    steps = [e for e in events if e.startswith("↳")]
    note = next((e for e in reversed(events) if not e.startswith("↳")), "")
    reached = builder.stages_done(agent_dir)
    try:
        t0 = datetime.fromisoformat(s["started"])
        since = f"since {t0.astimezone().strftime('%H:%M')} ({int((datetime.now(timezone.utc) - t0).total_seconds() // 60)} min)"
    except (KeyError, ValueError):
        since = ""
    now = {None: "Setting up", "setup": "Reading the paper, and surveying its code, data and environment",
           "read": "Surveying its code, data and environment", "survey": "Reading the paper",
           "plan": "Making each method runnable", "implement": "Assembling the RO-Crate",
           "assemble": "Checking every quote and published value against the paper", "check": "Reproducing each method",
           "validate": "Verifying every claim independently", "verify": "Delivering: report, server check, package",
           }.get(reached[-1] if reached else None, "Working")
    st.markdown(f"**{now}** · running {since} · {len(reached)} of 10 stages done")
    line = "  ‹  ".join(html.escape(short(e, 70)) for e in reversed(steps[-6:]))  # newest first, on one line
    st.markdown(f"<div class='steps-line' title='{html.escape(chr(10).join(steps[-12:]))}'>{line}</div>"
                + (f"<div class='steps-line muted'>{html.escape(short(note, 160))}</div>" if note else ""),
                unsafe_allow_html=True)
    st.caption("You can keep using the app; this updates by itself.")
def validate_actions(agent: PaperAgent) -> None:
    runnable = [m for m in agent.methods if not agent.spec(m.id).needs_data
                or (agent.spec(m.id).example_data and agent.spec(m.id).example_data.exists())]
    needs_trust = [m for m in runnable if runs_own_code(m.language) and m.status == "not validated"]
    c1, c2 = st.columns([3, 1], vertical_alignment="center")
    c2.caption(f"{len(runnable)} with published data")
    if needs_trust:
        with c1.popover("Reproduce all", type="primary", width="stretch", icon=":material/replay:"):
            st.markdown("This runs the workflows' own code (" + ", ".join(sorted({m.language for m in needs_trust}))
                        + ") on your computer. Do it only if you trust where it comes from.")
            if st.button("I trust it: run", type="primary", key="trust-all"):
                with st.spinner("Reproducing…"):
                    revalidate(agent.path, runner="auto")
                load_agent(agent.path, ss.tools.data_dir)
                note_for_chat("Reproduced all methods on the published data.")
                st.rerun()
    elif c1.button("Reproduce all", type="primary", width="stretch", icon=":material/replay:", disabled=not runnable):
        heads = []
        for m in runnable:
            res = run_with_progress(f"Reproducing {m.name}", lambda m=m: ss.tools.call("reproduce", {"method": m.name}))
            heads.append(f"**{m.name}**: " + next((a["headline"] for a in res.attachments if a["type"] == "comparison"),
                                                   next((a["text"] for a in res.attachments if a["type"] == "error"), "done")))
        note_for_chat("Reproduced on the published data.\n\n" + "\n".join(f"- {h}" for h in heads))
        st.rerun()
