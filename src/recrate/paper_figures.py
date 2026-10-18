"""The paper's own figures, recreated: each next to the original, and judged by a person.

A method that recreates one of the paper's figures writes it into ``output_dir/figures/``, named after it:
``figure-3.png`` (or ``figure-3b.png`` for a panel, ``fig3.png``...). ReCRATE pairs it with that figure in the
paper's reading package (``paper/figures/``, with its caption), records in the run crate which figure it recreates,
and shows the two side by side.

Whether two plots show the same thing is not a number, so ReCRATE doesn't decide it: a named person does (same /
differs / can't tell, and why), and the review is recorded in the run crate. The numbers behind a figure (its
sample sizes, the values it plots) can still be compared like any other result.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FIGURE_FILE = re.compile(r"^fig(?:ure)?[-_ ]?(?:s(?=\d))?(\d+)([a-z]?)\b", re.I)
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".svg", ".gif"}
VERDICTS = ("same", "differs", "can't tell")


def figure_label(filename: str) -> str | None:
    """'figure-3.png' -> 'Figure 3'; 'fig2b.png' -> 'Figure 2' (panel b). None if the name names no figure."""
    m = FIGURE_FILE.match(Path(filename).stem)
    return f"Figure {int(m.group(1))}" if m else None


def paper_figures(paper_dir: Path) -> dict[str, dict[str, Any]]:
    """The paper's figures by label, from its reading package: {'Figure 3': {'file': Path, 'caption': ...}}."""
    meta = Path(paper_dir) / "package.json"
    out: dict[str, dict[str, Any]] = {}
    if meta.exists():
        for f in json.loads(meta.read_text(encoding="utf-8")).get("figures", []):
            label = re.sub(r"\s+", " ", f.get("label") or "").strip().rstrip(".")
            m = re.match(r"(?:Fig\.?|Figure)\s*(\d+)", label, re.I)
            if m:
                out[f"Figure {int(m.group(1))}"] = {"file": Path(paper_dir) / f["file"] if f.get("file") else None,
                                                    "caption": f.get("caption", "")}
    return out


def recreated(run_dir: Path, paper_dir: Path | None = None) -> list[dict[str, Any]]:
    """The figures a run made that recreate one of the paper's, each with the original (when the paper's reading
    package has it) and the latest review."""
    figs = sorted(p for p in (Path(run_dir) / "outputs" / "figures").glob("*") if p.suffix.lower() in IMAGE_SUFFIXES)
    originals = paper_figures(paper_dir) if paper_dir else {}
    reviews = figure_reviews(run_dir)
    out = []
    for f in figs:
        label = figure_label(f.name)
        if label is None:
            continue
        orig = originals.get(label, {})
        out.append({"label": label, "recreated": f, "original": orig.get("file"), "caption": orig.get("caption", ""),
                    "review": reviews.get(f.name)})
    return out


def review_figure(run_dir: Path, who: str, figure: str, verdict: str, note: str = "") -> None:
    """Record a person's judgement of one recreated figure against the paper's, as an AssessAction in the run crate."""
    from .runcrate import _person, entity_index, load_run_crate, save_crate

    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {', '.join(VERDICTS)}")
    if not who.strip() or who.strip().lower() == "anonymous":
        raise ValueError("A figure review needs the reviewer's name.")
    meta = load_run_crate(run_dir)
    idx = entity_index(meta)
    target = f"outputs/figures/{Path(figure).name}"
    person = _person(who)
    if person["@id"] not in idx:
        meta["@graph"].append(person)
    n = sum(1 for e in meta["@graph"] if str(e["@id"]).startswith("#figure-review-") and e.get("@type") == "AssessAction")
    rid = f"#figure-review-{n}"
    label = figure_label(figure) or Path(figure).name
    meta["@graph"].extend([
        {"@id": rid, "@type": "AssessAction", "name": f"Comparison of the recreated {label} with the paper's",
         "agent": {"@id": person["@id"]}, "object": [{"@id": target}],
         "endTime": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
         "actionStatus": {"@id": "http://schema.org/CompletedActionStatus"}, "result": {"@id": f"{rid}-result"}},
        {"@id": f"{rid}-result", "@type": "PropertyValue", "name": f"figure review of {Path(figure).name}",
         "value": verdict, **({"description": note} if note else {})},
    ])
    idx["./"].setdefault("mentions", []).append({"@id": rid})
    save_crate(run_dir, meta)


def figure_reviews(run_dir: Path) -> dict[str, dict[str, Any]]:
    """Latest review per recreated figure file: {file: {"value": same|differs|can't tell, "note", "by"}}."""
    from .runcrate import entity_index, load_run_crate

    meta = load_run_crate(run_dir)
    idx = entity_index(meta)
    out: dict[str, dict[str, Any]] = {}
    for e in meta["@graph"]:
        if str(e["@id"]).startswith("#figure-review-") and e.get("@type") == "AssessAction":
            res = idx.get(e["result"]["@id"], {})
            out[res.get("name", "").removeprefix("figure review of ")] = {
                "value": res.get("value"), "note": res.get("description", ""),
                "by": idx.get(e["agent"]["@id"], {}).get("name")}
    return out
