"""A run's results, its overlays reviewed one by one, and the figure builder (shared by Reproduce and Reuse)."""

from __future__ import annotations

from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from recrate.agent import PaperAgent
from recrate.figures import Panel, comparison_chart, panel_figure, pixel_size_um
from recrate.profiles.bioimaging import list_images
from recrate.runcrate import approve, image_reviews, review_image, run_story, summarise_run
from recrate.workspace import comparable_with_paper, flag_images, numeric_columns, read_rows, whatif
from ui.common import IMAGE_EXT, SERIES, ss
from ui.helpers import overlay_source, run_inputs, run_with_progress


# ---- results, review and figures (shared by Reproduce and Reuse) ----------------------------
def results_tab(agent: PaperAgent, run_dir: Path | None, data_dir: Path) -> None:
    if run_dir is None:
        st.caption("No runs yet: reproduce a method or run it on new data.")
        return
    s = summarise_run(run_dir)
    spec = agent.spec(s["method"])
    rows = read_rows(run_dir / "outputs" / "results.csv")
    reported = read_rows(spec.expected_results) if spec.expected_results and spec.expected_results.exists() else None
    w = ss.tools.whatifs.get(run_dir.name)
    changed: list[str] = []
    if s["mode"] == "reproduce":
        (st.success if s["verdict"] == "reproduced" else st.error)(s["headline"])
        other, lab1, lab2, ref_rows = reported, "This run", "Published", None
    else:
        same, why = comparable_with_paper(spec, run_inputs(run_dir, data_dir))
        keyed = bool(reported and rows) and not w and "image" not in rows[0] and \
            [list(r.values())[0] for r in reported] == [list(r.values())[0] for r in rows]  # same rows (e.g. IoU)
        if not same:
            st.caption(f"ℹ️ {why}" + (" Shown beside yours: the paper's values on its own data, to see how the "
                                      "method transfers." if keyed else ""))
            reported = reported if keyed else None
        used = {p["name"]: p["value"] for p in run_story(run_dir)["parameters"]}
        changed = [f"{k} {v}" for k, v in used.items() if k in spec.default_params() and str(spec.default_params()[k]) != v]
        lab1 = "This run" + (f" ({', '.join(changed)})" if changed else "")
        lab2 = "Published values unchanged (" + ", ".join(f"{k} {v}" for k, v in spec.default_params().items()
                                                         if str(v) != used.get(k)) + ")"
        other, ref_rows = (w["rows"] if w else None), reported
        if keyed:
            other, ref_rows, lab2 = reported, None, "The paper's data (published)"
    metrics = numeric_columns(rows)
    if not metrics:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        return
    metric = st.segmented_control("Measure", metrics, default=metrics[0], key=f"metric-{s['run']}",
                                  label_visibility="collapsed") or metrics[0]
    if s["mode"] != "reproduce" and not other and rows and f"{metric}_true" in rows[0]:  # ground truth in your data
        other, lab2 = [{**r, metric: r[f"{metric}_true"]} for r in rows], "Ground truth (in the new data)"
    has = lambda r: r.get(metric) not in (None, "")  # noqa: E731
    if other and not any(has(r) for r in other):  # e.g. the paper publishes the count, not the mean area
        other = None
    if ref_rows and not any(has(r) for r in ref_rows):
        ref_rows = None
    mean = lambda rr: sum(float(r[metric]) for r in rr if has(r)) / max(1, sum(1 for r in rr if has(r)))  # noqa: E731
    c = st.columns(3)
    c[0].metric("This run", f"{mean(rows):.4g}", border=True, help=lab1)
    if s["mode"] == "reproduce" and other:
        c[1].metric("Published", f"{mean(other):.4g}", border=True)
    elif other:
        c[1].metric(lab2.split(" (")[0] if lab2.startswith(("The paper's data", "Ground truth")) else "Published values",
                    f"{mean(other):.4g}", delta=f"{mean(other) - mean(rows):+.3g}",
                    delta_color="off", border=True, help=lab2)
    if ref_rows:
        c[2].metric("Published data", f"{mean(ref_rows):.4g}", border=True, help="Reported on the published data")
    df = pd.DataFrame([{"item": r.get("image") or r[list(r)[0]], "value": float(r[metric]), "series": lab1}
                       for r in rows if has(r)])
    if other:
        df = pd.concat([df, pd.DataFrame([{"item": r.get("image") or r[list(r)[0]], "value": float(r[metric]),
                                           "series": lab2} for r in other if r.get(metric)])])
    domain = [lab1] + ([lab2] if other else [])
    pts = alt.Chart(df).mark_circle(size=110, opacity=1).encode(
        x=alt.X("item:N", title=None, axis=alt.Axis(labelAngle=0, labelLimit=120)),
        xOffset=alt.XOffset("series:N", scale=alt.Scale(domain=domain)),
        y=alt.Y("value:Q", title=metric.replace("_", " "), scale=alt.Scale(zero=False), axis=alt.Axis(format=",.4~r")),
        color=alt.Color("series:N", scale=alt.Scale(domain=domain, range=SERIES[:len(domain)]),
                        legend=alt.Legend(orient="bottom", direction="vertical", title=None, labelLimit=0)),
        tooltip=["item", "series", alt.Tooltip("value:Q", format=".4g")])
    layers, rng = [pts], None
    vals = [float(r[metric]) for r in (ref_rows or []) if r.get(metric)]
    if vals:
        rng = (min(vals), max(vals))
        ref = pd.DataFrame({"lo": [rng[0]], "hi": [rng[1]], "label": ["published range" if rng[1] > rng[0] else "published"]})
        mark = (alt.Chart(ref).mark_rect(color="#8a8985", opacity=0.18).encode(y="lo:Q", y2="hi:Q") if rng[1] > rng[0]
                else alt.Chart(ref).mark_rule(color="#8a8985", strokeDash=[4, 3], strokeWidth=1.5).encode(y="lo:Q"))
        lbl = alt.Chart(ref).mark_text(align="left", dx=4, dy=-7, color="#8a8985", fontSize=11).encode(
            y="hi:Q", x=alt.value(0), text="label:N")
        layers = [mark, lbl, pts]
    st.altair_chart(alt.layer(*layers).properties(height=250), width="stretch")
    if s["mode"] != "reproduce" and not other and changed:
        if st.button("Compare with the published values unchanged", key=f"cmp-{s['run']}"):
            ss.tools.whatifs[s["run"]] = run_with_progress("Running with the published values", lambda: whatif(
                spec, run_inputs(run_dir, data_dir), spec.default_params(), runner="auto"))
            st.rerun()
    c1, c2 = st.columns(2)
    item = lambda r: r.get("image") or r[list(r)[0]]  # noqa: E731
    mine = [r for r in rows if has(r)]
    theirs = {item(r): float(r[metric]) for r in (other or []) if has(r)}
    paired = bool(theirs) and all(item(r) in theirs for r in mine)  # the chart pairs values item by item
    chart_png = comparison_chart([item(r) for r in mine], [float(r[metric]) for r in mine],
                                 [theirs[item(r)] for r in mine] if paired else None, metric, lab1, lab2, rng)
    c1.download_button("Chart (PNG)", chart_png, file_name=f"{s['run']}-{metric}.png", mime="image/png",
                       width="stretch", icon=":material/download:", key=f"chartdl-{s['run']}-{metric}")
    if c2.button("Add chart to the export", width="stretch", key=f"addchart-{s['run']}-{metric}"):
        ss.figures[f"{s['run']}-{metric}.png"] = chart_png
        st.toast("Added to the export bundle.")
    ovs = sorted((run_dir / "outputs" / "overlays").glob("*.png"))
    if ovs:  # what the method found, on each image: the first thing to look at
        st.markdown("**Segmentation on " + ("the new images" if s["mode"] != "reproduce" else "the published images") + "**")
        by = {Path(r.get("image", "")).stem: r for r in rows}
        for i in range(0, min(len(ovs), 6), 3):
            for col, ov in zip(st.columns(3), ovs[i:i + 3]):
                stem = ov.name.removesuffix("_overlay.png")
                r = by.get(stem, {})
                cap = f"{stem[:28]}" + (f" · {r[metric]} {metric.replace('_', ' ')}" if r.get(metric) else "") + \
                    (f" (truth {r[metric + '_true']})" if r.get(f"{metric}_true") else "")
                col.image(str(ov), caption=cap, width="stretch")
        if len(ovs) > 6:
            st.caption(f"First 6 of {len(ovs)} images; all of them under *Review each result*.")
    from recrate.paper_figures import figure_label

    drawn = sorted(f for f in (run_dir / "outputs" / "figures").glob("*")  # the figures the method drew in this run
                   if f.suffix.lower() in IMAGE_EXT and f.stem != "published_vs_reproduced") \
        if (run_dir / "outputs" / "figures").is_dir() else []
    if drawn:
        where = "the new data" if s["mode"] != "reproduce" else "the published data"
        st.markdown(f"**Figures drawn from {where}**")
        for f in drawn:
            label = figure_label(f.name)
            st.image(str(f), width="stretch",
                     caption=(f"The paper's {label}, drawn by the same method from {where}" if label
                              else f"{f.stem}, drawn from {where}"))
    with st.expander("Table"):
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    paper_ovs = paper_overlays(agent, s) if ovs and s["mode"] != "reproduce" else []
    if paper_ovs:
        with st.expander("The paper's data next to yours", expanded=True):
            side_by_side(paper_ovs, ovs, rows, s["run"])
    if ovs:
        with st.expander(f"Review each result ({len(ovs)})"):
            review(agent, run_dir, ovs, rows, spec, data_dir)
        with st.expander("Make a figure"):
            figure_builder(agent, run_dir, data_dir)
def paper_overlays(agent: PaperAgent, s: dict) -> list[Path]:
    """The segmentations of the latest reproduction of this method, on the paper's own data."""
    for d in sorted((p for p in agent.runs_dir.glob("*") if p.is_dir()), reverse=True):
        if d.name.endswith("-reuse") or not (d / "outputs" / "overlays").is_dir():
            continue
        r = summarise_run(d)
        if r["mode"] == "reproduce" and r["method"] == s["method"]:
            return sorted((d / "outputs" / "overlays").glob("*.png"))
    return []
def side_by_side(paper: list[Path], yours: list[Path], rows: list[dict], run: str) -> None:
    """One image of the paper's data and one of yours, segmented by the same method with the same settings."""
    name = lambda o: o.name.removesuffix("_overlay.png")  # noqa: E731
    c1, c2 = st.columns(2)
    a = c1.selectbox("The paper's data", paper, format_func=name, key=f"sbs-p-{run}")
    b = c2.selectbox("New data", yours, format_func=name, key=f"sbs-y-{run}")
    c1.image(str(a), width="stretch")
    c2.image(str(b), width="stretch")
    row = next((r for r in rows if Path(r.get("image") or "").stem == name(b)), None)
    if row:
        c2.caption(" · ".join(f"{k.replace('_', ' ')} {v}" for k, v in row.items() if k != "image"))
    st.caption("Outlines: the nuclei the method found. Same model and thresholds on both sides; look for merged or "
               "split nuclei, and for missed dim ones.")
def review(agent, run_dir: Path, ovs: list[Path], rows: list[dict], spec, data_dir: Path) -> None:
    s = summarise_run(run_dir)
    by = {r.get("image"): r for r in rows}
    metrics = numeric_columns(rows)
    reported = read_rows(spec.expected_results) if s["mode"] == "reuse" and spec.expected_results and \
        spec.expected_results.exists() and comparable_with_paper(spec, run_inputs(run_dir, data_dir))[0] else None
    flags = flag_images(rows, metrics[0], reported, spec.tolerances().get(metrics[0], 0.1)) if metrics else {}
    reviews = image_reviews(run_dir)
    st.caption(f"{len(reviews)} of {len(ovs)} reviewed · {sum(1 for v in flags.values() if v)} flagged · whole run: "
               f"{s['approved'] or 'not reviewed'}")
    c1, c2 = st.columns(2)
    if c1.button("Whole run looks right", key=f"wok-{s['run']}", width="stretch", icon=":material/thumb_up:"):
        approve(run_dir, ss.tools.who, True)
        st.rerun()
    if c2.button("Something's wrong", key=f"wno-{s['run']}", width="stretch", icon=":material/thumb_down:"):
        approve(run_dir, ss.tools.who, False)
        st.rerun()
    order = sorted(ovs, key=lambda o: (not flags.get(o.name.replace("_overlay.png", ".tif")), o.name))
    grid = st.columns(3)
    for k, ov in enumerate(order):
        stem = ov.name.removesuffix("_overlay.png")
        img = next((n for n in by if n and Path(n).stem == stem), f"{stem}.tif")
        with grid[k % 3].container(border=True):
            st.image(str(ov), width="stretch")
            st.caption(f"**{stem}** · " + " · ".join(f"{m} {float(by[img][m]):.3g}" for m in metrics[:2] if img in by))
            for f in flags.get(img, []):
                st.caption(f"⚠️ {f}")
            rv = reviews.get(img)
            if rv:
                st.caption(("✅ accepted" if rv["value"] == "accepted" else "❌ rejected") + f" by {rv['by']}"
                           + (f": {rv['note']}" if rv["note"] else ""))
            b1, b2 = st.columns(2)
            if b1.button("Right", key=f"iok-{s['run']}-{stem}", width="stretch", icon=":material/thumb_up:"):
                review_image(run_dir, ss.tools.who, img, True)
                st.rerun()
            if b2.button("Wrong", key=f"ino-{s['run']}-{stem}", width="stretch", icon=":material/thumb_down:"):
                review_image(run_dir, ss.tools.who, img, False)
                st.rerun()
def figure_builder(agent: PaperAgent, run_dir: Path, data_dir: Path) -> None:
    srcs: dict[str, tuple[str, float | None]] = {}
    for f in list_images(data_dir)[:24] if data_dir.exists() else []:
        srcs[f"Your image · {f.stem}"] = (str(f), None)
    for ov in sorted((run_dir / "outputs" / "overlays").glob("*.png")):
        src = overlay_source(ov, run_dir, data_dir)
        srcs[f"Detected · {ov.stem.removesuffix('_overlay')}"] = (str(ov), pixel_size_um(src) if src else None)
    for stem, p in (ss.tools.whatifs.get(run_dir.name) or {}).get("overlays", {}).items():
        src = overlay_source(Path(p), run_dir, data_dir)
        srcs[f"Detected, published values · {stem}"] = (p, pixel_size_um(src) if src else None)
    for m in agent.methods:
        spec = agent.spec(m.id)
        if spec.example_data and spec.example_data.exists():
            for f in list_images(spec.example_data)[:6]:
                srcs[f"Published image · {f.stem}"] = (str(f), None)
            break
    labels, k = list(srcs), run_dir.name
    if st.button("Preset: published image vs yours, same scale", key=f"fig-preset-{k}"):
        ss[f"fig_pick-{k}"] = [l for l in [next((l for l in labels if l.startswith(p)), None) for p in
                                           ("Published image", "Your image", "Detected ·", "Detected, published")] if l]
        ss[f"fig_same-{k}"] = True
    chosen = st.multiselect("Panels, in order", labels, key=f"fig_pick-{k}", max_selections=12)
    if not chosen:
        return
    c1, c2, c3, c4 = st.columns(4)
    cols = c1.number_input("Columns", 1, 4, value=min(len(chosen), 2), key=f"fig-cols-{k}")
    fmt = c2.selectbox("Format", ["png", "pdf", "svg"], key=f"fig-fmt-{k}")
    same = c3.toggle("Same scale", key=f"fig_same-{k}", help="Resample to one µm per pixel")
    bar = c4.number_input("Scale bar µm", min_value=0.0, value=0.0, step=5.0, help="0 = automatic", key=f"fig-bar-{k}")
    panels = [Panel(srcs[l][0], l.split(" · ")[0], srcs[l][1]) for l in chosen]
    kw = dict(cols=int(cols), width_in=7.0, scale_bar_um=bar or None, same_scale=same, letters=True)
    try:
        preview, notes = panel_figure(panels, fmt="png", dpi=100, **kw)
    except Exception as e:  # noqa: BLE001
        st.error(f"Couldn't build the figure: {e}")
        return
    st.image(preview, width="stretch")
    st.caption(" ".join(notes))
    data, _ = panel_figure(panels, fmt=fmt, dpi=300, **kw)
    name = f"figure-{len(ss.figures) + 1}.{fmt}"
    d1, d2 = st.columns(2)
    d1.download_button(f"Figure ({fmt.upper()})", data, file_name=name, width="stretch", icon=":material/download:",
                       key=f"fig-dl-{k}",
                       mime={"png": "image/png", "pdf": "application/pdf", "svg": "image/svg+xml"}[fmt])
    if d2.button("Add to the export", width="stretch", key=f"fig-add-{k}"):
        ss.figures[name] = data
        st.toast(f"{name} added to the export bundle.")
