"""Record: the research object's files, every run, and the export bundle."""

from __future__ import annotations

import html
import json
import os
import re
import time
from pathlib import Path

import pandas as pd
import streamlit as st

from recrate.agent import PaperAgent
from recrate.runcrate import run_story
from recrate.workspace import methods_markdown
from ui.building import load_agent, open_example, say
from ui.common import EXAMPLES, fmt_bytes, short, ss

# ---- Record: every run, and what to take away ------------------------------------------------------------
ROLE = [
    (r"(^|/)ro-crate-metadata\.json$", "RO-Crate metadata"), (r"(^|/)ro-crate-preview\.html$", "readable preview"),
    (r"^crate/workflows?/|^crate/[^/]+\.(ga|ijm|cwl|nf|smk|py)$", "workflow"),
    (r"^crate/data/", "published data"), (r"^crate/expected/", "published results"),
    (r"^crate/environment/", "environment"), (r"^crate/[^/]+\.(pdf|md|txt)$", "paper"),
    (r"^paper/", "paper text"), (r"^source-crate/", "original, unchanged"), (r"^code/", "code"),
]
FOLDER_LABEL = {"crate": "RO-Crate", "paper": "Paper", "runs": "Runs", "source-crate": "Original RO-Crate", "code": "Code",
                "data": "published data", "expected": "published results", "environment": "environment"}
HIDE = {"agent.json", "environment.json", "deposits", "exports", "crate_description.json", "DRAFT_ME.md", ".git", "runs"}
def role(rel: str) -> str | None:
    return next((label for pat, label in ROLE if re.search(pat, rel)), None)
TREE_DEPTH, TREE_ITEMS = 4, 25  # a large repository (thousands of folders) stays quick to draw
@st.cache_data(show_spinner=False, max_entries=64)
def _tree_cached(root: str, bucket: int) -> str:
    root_p = Path(root)
    return _tree(root_p, root_p, 0, {})
def tree_html(root: Path, d: Path, depth: int = 0) -> str:
    """The folder tree of a research object. Redrawn at most every 30 s: a running build changes the folder
    all the time, and the tree only reads the folders it shows."""
    return _tree_cached(str(d), int(time.time() // 30))
def _tree(root: Path, d: Path, depth: int, counts: dict[Path, int]) -> str:
    items = sorted([p for p in d.iterdir() if p.name not in HIDE and not p.name.startswith(".")],
                   key=lambda p: (p.is_file(), {"crate": 0, "paper": 1, "source-crate": 2}.get(p.name, 9), p.name))
    out = []
    folders = [p for p in items if p.is_dir()]
    for f in folders[:TREE_ITEMS]:
        if depth == 0 and f.name == "env":  # the paper's own environment: thousands of files, described, not listed
            info = ""
            rec = f / "recrate-env.json"
            if rec.exists():
                e = json.loads(rec.read_text(encoding="utf-8"))
                info = f" Python {e.get('python_version', '?')} · {len(e.get('packages', []))} packages"
            out.append(f"<div class='f'>📁 env<span class='tag'>environment</span><span class='n'>{html.escape(info)}"
                       "</span></div>")
            continue
        try:
            n = sum(1 for x in os.scandir(f) if not x.name.startswith("."))  # what's directly inside: cheap
        except OSError:
            n = 0
        tag = FOLDER_LABEL.get(f.name) if depth > 0 else None
        title = FOLDER_LABEL.get(f.name, f.name) if depth == 0 else f.name
        inner = (_tree(root, f, depth + 1, counts) if depth + 1 < TREE_DEPTH
                 else f"<div class='n'>… {n} more inside</div>")
        out.append(f"<details{' open' if depth == 0 and f.name == 'crate' else ''}><summary>📁 {html.escape(title)}"
                   + (f"<span class='tag'>{tag}</span>" if tag and tag != f.name else "")
                   + f" <span class='n'>{n}</span></summary><div>{inner}</div></details>")
    if len(folders) > TREE_ITEMS:
        out.append(f"<div class='n'>… {len(folders) - TREE_ITEMS} more folders</div>")
    files = [p for p in items if p.is_file()]
    for f in files[:20]:
        r = role(f.relative_to(root).as_posix())
        tag = r if r and r not in {"published data", "code"} else None
        out.append(f"<div class='f'>📄 {html.escape(f.name)}" + (f"<span class='tag'>{tag}</span>" if tag else "") + "</div>")
    if len(files) > 20:
        out.append(f"<div class='n'>… {len(files) - 20} more</div>")
    return "".join(out)
def preview(root: Path, rel: str) -> None:
    f = root / rel
    suf = f.suffix.lower()
    st.caption(f"{rel} · {fmt_bytes(f.stat().st_size)}" + (f" · {role(rel)}" if role(rel) else ""))
    try:
        if suf == ".json":
            st.json(json.loads(f.read_text(encoding="utf-8")), expanded=1)
        elif suf in {".csv", ".tsv", ".tabular"}:
            st.dataframe(pd.read_csv(f, sep="," if suf == ".csv" else "\t"), hide_index=True, width="stretch", height=220)
        elif suf in {".png", ".jpg", ".jpeg"}:
            st.image(str(f), width="stretch")
        elif suf in {".tif", ".tiff"}:
            from recrate.figures import load_display

            st.image(load_display(f), width="stretch", clamp=True)
        elif suf in {".pdf", ".zip"}:
            st.download_button("Download", f.read_bytes(), file_name=f.name)
        else:
            st.code(f.read_text(encoding="utf-8", errors="replace")[:3000], language=None, wrap_lines=True)
    except Exception as e:  # noqa: BLE001
        st.caption(f"Can't preview this file ({e}).")
def research_objects() -> list[Path]:
    """The research objects of this session, newest first: each paper or RO-Crate added (once its crate is found or
    built) and each example opened. Nothing else on the computer is listed."""
    return [Path(d) for d in reversed(ss.get("session_ros", [])) if (Path(d) / "agent.json").exists()]
def ro_status(a: PaperAgent) -> str:
    from recrate import build as builder

    if builder.state(a.path).get("state") == "running":
        return "⏳"
    if not a.methods:
        return "📄"
    numbers = [m.ladder.get("numbers", {}).get("status") for m in a.methods if m.ladder]
    if numbers and all(n == "passed" for n in numbers):
        return "✅"
    if any(n in ("partial", "failed") for n in numbers):
        return "🟠"
    return "⚪"
def open_research_object(d: Path) -> None:
    ex = next((k for k, e in EXAMPLES.items() if Path(e["agent"]).resolve() == d), None)
    ss.landing = False
    if ex:
        open_example(ex)
        return
    load_agent(d)
    ss.ro = {"agent": str(d)}
    ss._goto = "validate"
    say(f"Opened **{PaperAgent.load(d).card()['title']}**. {PaperAgent.load(d).card()['verdict']}")
def run_sentence(r: dict) -> str:
    """One run, in the words a researcher would use."""
    what = {"reproduce": f"Reproduced **{r['method']}** on the published data",
            "follow-up": f"Checked a possible cause of a difference in **{r['method']}**",
            "reuse": f"Ran **{r['method']}** on new data"}.get(r["mode"], f"Ran **{r['method']}**")
    result = (f"{r['images']} file(s)" if r.get("images") else "") if r["mode"] == "reuse" else \
        (r.get("purpose") or "") + " " + (r["verdict"] or "")
    return what + (f": {short(result.strip(), 140)}" if result and result.strip() else "")
def record_section(agent: PaperAgent, runs: list[dict]) -> Path | None:
    """The record, minimal: what was run, when, by whom, with what result. For the selected run, what you changed and
    a methods paragraph for your paper; below, its record to download. The RO-Crate details stay inside that file."""
    if not runs:
        st.info("Nothing recorded yet. Every time you reproduce the research or run a method on new data, it is "
                "recorded here, so you can show exactly what you did.")
        return None
    st.caption("Every run is recorded: what ran, on which data, with which settings, and by whom.")
    shown = list(reversed(runs))
    names = [r["run"] for r in shown]
    sel = st.dataframe(pd.DataFrame([{"when": (r["when"] or "")[:16].replace("T", " "),
                                      "what": run_sentence(r).replace("**", ""), "by": r["by"]} for r in shown]),
                       hide_index=True, width="stretch", height=min(38 * len(shown) + 40, 230), on_select="rerun",
                       selection_mode="single-row", key="runs-table",
                       column_config={"what": st.column_config.TextColumn(width="large")})
    picked = (sel.selection.rows or [None])[0] if sel and hasattr(sel, "selection") else None
    if picked is None:
        picked = names.index(ss.get("run_name")) if ss.get("run_name") in names else 0
    run_dir = agent.runs_dir / names[picked]
    r = run_story(run_dir)
    changed = [p for p in r["parameters"] if p["paper_default"] not in (None, "") and str(p["value"]) != str(p["paper_default"])]
    st.markdown("Settings: " + ("; ".join(f"`{p['name']}` {p['paper_default']} → **{p['value']}**" for p in changed)
                                if changed else "the paper's, unchanged."))
    with st.expander("Methods paragraph for your paper"):
        st.code(methods_markdown(run_dir), language="markdown", wrap_lines=True)
    return run_dir
def record_actions(agent: PaperAgent, run_dir: Path | None) -> None:
    if run_dir is None:
        st.caption("Runs appear here as you reproduce or reuse.")
        return
    import io
    import zipfile

    buf = io.BytesIO()  # the run's RO-Crate: everything about it, readable by people and machines
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(run_dir.rglob("*")):
            if f.is_file():
                z.write(f, Path(run_dir.name) / f.relative_to(run_dir))
    st.download_button("Download this run's record", buf.getvalue(), file_name=f"{run_dir.name}.zip",
                       mime="application/zip", type="primary", width="stretch", icon=":material/download:",
                       help="The run as an RO-Crate: workflow, inputs with checksums, settings, results, figures, "
                            "reviews, and a page that opens in any browser")
