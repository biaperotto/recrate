"""What a researcher needs around a run, beyond running it.

* ``whatif``: run a method with given parameters without recording it, e.g. the paper's parameters
  unchanged, to show what an adjustment prevents.
* ``parameter_sources``: each parameter's value, where the paper states it, and the adjustment.
* ``flag_images``: per-image results that stand out from the rest of the run or from the paper.
* ``export_bundle``: the run crate, results, methods text, references and figures in one zip.
"""

from __future__ import annotations

import csv
import json
import re
import shutil
import statistics
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from . import __version__
from .agent import PaperAgent
from .crate import MethodSpec
from .methods import methods_paragraph
from .overlay import make_overlays
from .paper import passages
from .pipeline import OVERLAY_LIMIT
from .runcrate import run_story
from .runners import get_runner


def read_rows(csv_path: Path) -> list[dict[str, str]]:
    with Path(csv_path).open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def numeric_columns(rows: list[dict[str, str]]) -> list[str]:
    def num(v: str) -> bool:
        try:
            float(v)
            return True
        except (TypeError, ValueError):
            return False
    skip = {"pixel_size"}
    cols = [c for c in (rows[0] if rows else {}) if c not in skip and not c.endswith("_true")  # ground truth: shown
            and all(num(r[c]) for r in rows)]                                                   # beside its measure
    first = next(iter(rows[0]), None) if rows else None
    if cols and cols[0] == first and len(cols) > 1 and len({r[first] for r in rows}) == len(rows):
        cols = cols[1:]  # the first column names the rows (e.g. an IoU threshold): a key, not a measure
    return cols


def whatif(spec: MethodSpec, data: Path, params: dict[str, Any], runner: str = "auto", channel: int = 0,
           out_dir: Path | None = None) -> dict[str, Any]:
    """Run without writing a run crate. Returns parameters, per-image rows and overlays by image stem."""
    out = Path(out_dir or tempfile.mkdtemp(prefix="recrate-whatif-"))
    run = get_runner(runner, spec.workflow_language, spec.workflow_path)
    result = run.run(spec.workflow_path, Path(data), out / "out", params, channel=channel)
    rows = read_rows(result.results_csv)
    overlays = (make_overlays(Path(data), result.masks_dir, out / "overlays", channel, limit=OVERLAY_LIMIT)
                if result.masks_dir else [])
    return {"params": dict(params), "rows": rows,
            "overlays": {p.name.removesuffix("_overlay.png"): str(p) for p in overlays}}


# ---- parameters and where the paper states them -------------------------------------------

def _sentences(text: str) -> list[str]:
    out = []
    for para in re.split(r"\n\s*\n|\n(?=#)", text):
        para = " ".join(para.split())
        if not para or para.startswith("#"):
            continue
        out += [s.strip() for s in re.split(r"(?<=[.;])\s+(?=[A-Z(])", para) if s.strip()]
    return out


def quote_for(text: str, terms: list[str], value: Any = None) -> str | None:
    """The sentence of the paper that best states a parameter (its words, ideally its value)."""
    best, best_score = None, 0
    val = None if value is None else f"{float(value):g}" if isinstance(value, (int, float)) else str(value)
    for s in _sentences(text):
        low = s.lower()
        score = sum(2 for t in terms if t in low) + (4 if val and re.search(rf"(?<![\d.]){re.escape(val)}(?![\d])", s) else 0)
        if score > best_score:
            best, best_score = s, score
    return best if best_score >= 3 else None


def parameter_sources(agent: PaperAgent, spec: MethodSpec) -> list[dict[str, Any]]:
    text = agent.paper.text if agent.paper else ""
    out = []
    for p in spec.value_inputs:
        words = [w for w in re.split(r"[_\W]+", p.name.lower()) if len(w) > 2 and w not in {"px", "the"}]
        words += [w for w in re.findall(r"[a-z]{4,}", (p.description or "").lower())][:6]
        out.append({"name": p.name, "default": p.default, "description": p.description or "",
                    "scaling": p.prop("scaling"), "quote": quote_for(text, words, p.default) if text else None})
    return out


def passage_index(agent: PaperAgent, sentence: str | None) -> int | None:
    if not (agent.paper and sentence):
        return None
    key = " ".join(sentence.split())[:80]
    return next((i for i, p in enumerate(passages(agent.paper.text)) if key in " ".join(p.split())), None)


def comparable_with_paper(spec: MethodSpec, data: Path) -> tuple[bool, str]:
    """Per-image values (counts especially) only compare when images cover a similar field.
    Returns (comparable, reason shown when not)."""
    from .profiles.bioimaging import list_images, read_image_metadata, to_um

    if not (spec.example_data and spec.example_data.exists()):
        return True, ""

    def field(d: Path) -> tuple[float, float | None, str]:
        metas = [read_image_metadata(f) for f in list_images(d)[:12]]
        if not metas:
            return 0.0, None, ""
        px = [to_um(m["pixel_size"], m["unit"]) for m in metas]
        area = sum(m["shape"][-2] * m["shape"][-1] for m in metas) / len(metas)
        h, w = metas[0]["shape"][-2:]
        um = (sum(px) / len(px)) if all(px) else None
        return area, um, f"{w}×{h} px" + (f" at {um:g} µm" if um else "")

    a_t, px_t, t_txt = field(spec.example_data)
    a_m, px_m, m_txt = field(Path(data))
    if not a_t or not a_m:
        return True, ""
    ratio = (a_m * px_m ** 2) / (a_t * px_t ** 2) if px_t and px_m else a_m / a_t  # physical field if known
    if 0.5 <= ratio <= 2:
        return True, ""
    return False, (f"The paper's reported values come from images of {t_txt} and yours are {m_txt}: they cover "
                   f"fields about {ratio:.0f}× different in size, so per-image values such as counts aren't "
                   "comparable. They are not shown or used for flags." if ratio > 1 else
                   f"The paper's reported values come from images of {t_txt} and yours are {m_txt}: your fields are "
                   "much smaller, so per-image values such as counts aren't comparable. They are not shown or used "
                   "for flags.")


# ---- per-image flags ----------------------------------------------------------------------

def flag_images(rows: list[dict[str, str]], metric: str, reported: list[dict[str, str]] | None = None,
                tolerance: float = 0.1) -> dict[str, list[str]]:
    """Reasons to look twice at an image: far from the rest of this run (robust z > 3.5), or outside
    the range the paper reports (which may be real biology: check the overlay)."""
    if not rows or "image" not in rows[0]:  # results per threshold, per group...: no image to flag
        return {}
    vals = {r["image"]: float(r[metric]) for r in rows if r.get(metric) not in (None, "")}
    flags: dict[str, list[str]] = {k: [] for k in vals}
    if len(vals) >= 3:
        med = statistics.median(vals.values())
        mad = statistics.median(abs(v - med) for v in vals.values())
        if mad > 0:
            for k, v in vals.items():
                z = 0.6745 * (v - med) / mad
                if abs(z) > 3.5:
                    flags[k].append(f"{metric} {v:g} is far from the other images (median {med:g})")
    ref = [float(r[metric]) for r in (reported or []) if r.get(metric) not in (None, "")]
    if ref:
        lo, hi = min(ref), max(ref)
        for k, v in vals.items():
            if v < lo * (1 - tolerance) or v > hi * (1 + tolerance):
                flags[k].append(f"{metric} {v:g} is outside the paper's range ({lo:g}–{hi:g})")
    return flags


# ---- references and export ----------------------------------------------------------------

KNOWN_SOFTWARE = {
    "fiji": ("schindelin2012fiji", "Schindelin, J. and others", "Fiji: an open-source platform for "
             "biological-image analysis", "Nature Methods", "2012", "10.1038/nmeth.2019"),
    "scikit-image": ("vanderwalt2014scikit", "van der Walt, S. and others", "scikit-image: image processing "
                     "in Python", "PeerJ", "2014", "10.7717/peerj.453"),
}
STANDARDS = [
    ("soilandreyes2022rocrate", "Soiland-Reyes, S. and others", "Packaging research artefacts with RO-Crate",
     "Data Science", "2022", "10.3233/DS-210053"),
    ("leo2024wfrun", "Leo, S. and others", "Recording provenance of workflow runs with RO-Crate", "PLOS ONE",
     "2024", "10.1371/journal.pone.0309210"),
]


def _bib(key: str, author: str, title: str, journal: str, year: str, doi: str) -> str:
    return (f"@article{{{key},\n  author = {{{author}}},\n  title = {{{title}}},\n  journal = {{{journal}}},\n"
            f"  year = {{{year}}},\n  doi = {{{doi}}}\n}}\n")


def references_bib(agent: PaperAgent, spec: MethodSpec, run_dir: Path) -> str:
    title = agent.manifest.get("title") or spec.name
    doi = (spec.citation or agent.manifest.get("doi") or "").removeprefix("https://doi.org/")
    authors = " and ".join(spec.authors) or "Unknown"
    out = [f"@article{{paper,\n  author = {{{authors}}},\n  title = {{{title}}},\n"
           + (f"  doi = {{{doi}}},\n" if doi else "") + "  note = {The paper whose method was reused}\n}\n"]
    story = run_story(run_dir)
    seen = set()
    for s in story["software"]:
        k = next((k for k in KNOWN_SOFTWARE if s.lower().startswith(k)), None)
        if k and k not in seen:
            seen.add(k)
            out.append(_bib(*KNOWN_SOFTWARE[k]))
    out.append(f"@software{{recrate,\n  author = {{The ReCRATE contributors}},\n  title = {{ReCRATE}},\n"
               f"  version = {{{__version__}}},\n  year = {{2026}}\n}}\n")
    out += [_bib(*s) for s in STANDARDS]
    return "\n".join(out)


def references_csl(agent: PaperAgent, spec: MethodSpec, run_dir: Path) -> list[dict[str, Any]]:
    """The same references as CSL-JSON (for Zotero, Pandoc, most reference managers), plus the run crate itself."""
    def person(name: str) -> dict[str, str]:
        family, _, given = name.partition(", ") if ", " in name else (name.rsplit(" ", 1)[-1], "", name.rsplit(" ", 1)[0])
        return {"family": family, "given": given}

    doi = (spec.citation or agent.manifest.get("doi") or "").removeprefix("https://doi.org/")
    items = [{"id": "paper", "type": "article-journal", "title": agent.manifest.get("title") or spec.name,
              "author": [person(a) for a in spec.authors], **({"DOI": doi} if doi and "/" in doi else {}),
              "note": "The paper whose method was checked or reused"}]
    story = run_story(run_dir)
    seen = set()
    for sw in story["software"]:
        k = next((k for k in KNOWN_SOFTWARE if sw.lower().startswith(k)), None)
        if k and k not in seen:
            seen.add(k)
            key, author, title, journal, year, d = KNOWN_SOFTWARE[k]
            items.append({"id": key, "type": "article-journal", "title": title, "container-title": journal,
                          "author": [{"literal": author}], "issued": {"date-parts": [[int(year)]]}, "DOI": d})
    items.append({"id": "recrate", "type": "software", "title": "ReCRATE", "version": __version__,
                  "author": [{"literal": "The ReCRATE contributors"}]})
    items.append({"id": "run", "type": "dataset", "title": story["title"], "note": "Workflow Run RO-Crate: "
                  + Path(run_dir).name, "issued": {"date-parts": [[int((story["ended"] or "2026")[:4])]]}})
    for key, author, title, journal, year, d in STANDARDS:
        items.append({"id": key, "type": "article-journal", "title": title, "container-title": journal,
                      "author": [{"literal": author}], "issued": {"date-parts": [[int(year)]]}, "DOI": d})
    return items


def methods_markdown(run_dir: Path) -> str:
    story = run_story(run_dir)
    params = "\n".join(f"- `{p['name']}` = {p['value']}" + (f" (paper: {p['paper_default']})"
                       if p["paper_default"] not in (None, str(p["value"])) else "")
                       + (f". {p['note']}" if p["note"] else "") for p in story["parameters"])
    return (f"# Methods\n\n{methods_paragraph(run_dir)}\n\n## Parameters\n\n{params}\n\n"
            f"## Software\n\n" + "\n".join(f"- {s}" for s in story["software"]) +
            f"\n\nRun record: `{Path(run_dir).name}` (Workflow Run RO-Crate, included in this bundle).\n")


def export_bundle(agent: PaperAgent, spec: MethodSpec, run_dir: Path, figures: dict[str, bytes] | None = None,
                  include: set[str] | None = None, out_dir: Path | None = None) -> Path:
    """One zip for a paper, a supplement or a repository deposit."""
    include = include or {"crate", "results", "methods", "references", "figures"}
    run_dir = Path(run_dir)
    out_dir = Path(out_dir or run_dir.parent / "exports")
    out_dir.mkdir(parents=True, exist_ok=True)
    zpath = out_dir / f"{run_dir.name}-bundle.zip"
    contents = []
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        if "crate" in include:
            for f in sorted(run_dir.rglob("*")):
                if f.is_file():
                    z.write(f, f"run-crate/{f.relative_to(run_dir)}")
            contents.append("run-crate/   the Workflow Run RO-Crate (open ro-crate-preview.html)")
        if "results" in include:
            z.write(run_dir / "outputs" / "results.csv", "results.csv")
            contents.append("results.csv  per-image results")
        if "methods" in include:
            z.writestr("methods.md", methods_markdown(run_dir))
            contents.append("methods.md   methods paragraph, parameters with reasons, software")
        if "references" in include:
            z.writestr("references.bib", references_bib(agent, spec, run_dir))
            z.writestr("references.json", json.dumps(references_csl(agent, spec, run_dir), indent=2, ensure_ascii=False))
            contents.append("references.bib, references.json  the paper, the software, the standards and the run "
                            "crate (BibTeX and CSL-JSON)")
        if "figures" in include and figures:
            for name, data in figures.items():
                z.writestr(f"figures/{name}", data)
            contents.append("figures/     figures made in ReCRATE")
        z.writestr("README.txt", f"ReCRATE export of run {run_dir.name}\n\n" + "\n".join(contents) + "\n")
    return zpath


def folder_summary(path: Path) -> dict[str, Any]:
    from .profiles.bioimaging import list_images

    files = list_images(path) if Path(path).exists() else []
    return {"images": len(files), "bytes": sum(f.stat().st_size for f in files)}


def copy_tree(src: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for f in Path(src).iterdir():
        if f.is_file():
            shutil.copy2(f, dest / f.name)
