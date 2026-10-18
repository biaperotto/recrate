"""What a paper links to, and what that is: the authors' own code and data, or something they cite.

A paper's reference list mixes the authors' own repository ("Jones L. Reproducibility; 2026. github.com/Lee-V-Jones/…")
with tools they used ("rplos: Interface to PLoS Journals search API. github.com/ropensci/rplos"). Only the first is
the code of the paper. A link counts as the authors' own when:

* its owner matches a name in the paper's author list (``Lee-V-Jones`` and "Lee Jones"), or the reference entry that
  gives it starts with an author's name ("Jones L. …"), or
* it sits in a code or data availability statement, or the text says the results "are available on" it.

``survey_code`` then describes a cloned repository without running anything: its languages, README, scripts and
notebooks, dependency files, and the interpreters it needs that this computer lacks.
"""

from __future__ import annotations

import re
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

CODE_HOSTS = ("github.com", "gitlab.com", "codeberg.org", "bitbucket.org")
STOP = {"the", "and", "lab", "group", "org", "code", "data", "team", "project", "main", "dev", "repo", "tools",
        "university", "institute", "center", "centre", "science", "research"}
LANGUAGES = {".r": "R", ".rmd": "R Markdown", ".qmd": "Quarto", ".py": "Python", ".ipynb": "Jupyter notebook",
             ".jl": "Julia", ".m": "MATLAB", ".sh": "Shell", ".do": "Stata", ".sas": "SAS", ".cpp": "C++", ".c": "C",
             ".java": "Java", ".nf": "Nextflow", ".smk": "Snakemake", ".cwl": "CWL", ".ga": "Galaxy", ".ijm": "ImageJ macro"}
INTERPRETER = {"R": "Rscript", "R Markdown": "Rscript", "Quarto": "quarto", "Python": "python3",
               "Jupyter notebook": "python3", "Julia": "julia", "MATLAB": "matlab", "Stata": "stata", "SAS": "sas",
               "Nextflow": "nextflow", "Snakemake": "snakemake", "CWL": "cwltool"}
DEPENDENCY_FILES = ("requirements.txt", "environment.yml", "environment.yaml", "pyproject.toml", "setup.py",
                    "renv.lock", "DESCRIPTION", "install.R", "Project.toml", "Dockerfile", "Makefile", "Snakefile",
                    "nextflow.config", "Pipfile", "conda.yml")


def _header(text: str) -> str:
    """The title and author lines: the start of the paper, before the abstract."""
    head = text[:2000]
    cut = re.search(r"\babstract\b", head, re.I)
    return (head[:cut.start()] if cut else head[:1200]).lower()


def _named(name: str, head: str) -> bool:
    """A name in the author list, also when an affiliation number follows it ("Jones1,2,3")."""
    return bool(re.search(rf"(?<![a-z]){re.escape(name)}(?![a-z])", head))


def _tokens(owner: str) -> list[str]:
    return [t for t in re.split(r"[-_.\s]+", owner.lower()) if len(t) >= 3 and t not in STOP and not t.isdigit()]


def _reference_entry(text: str, link: str) -> str:
    """The reference that gives this link, from its number ("30. Jones L. …") to the link."""
    i = text.rfind(link.split("://", 1)[-1][:40])
    if i < 0:
        return ""
    before = text[max(0, i - 400):i]
    starts = list(re.finditer(r"(?:^|\n)\s*\[?\d{1,3}[.\]]\s+(?=[A-Z])", before))
    return before[starts[-1].end():] if starts else ""


def _references_start(low: str) -> int:
    m = list(re.finditer(r"\n\s*(references|bibliography|literature cited)\s*\n", low))
    return m[-1].start() if m else len(low)


def classify(paper) -> list[dict[str, Any]]:
    """Each code, data or workflow link of the paper: what it is, whether it is the authors' own, and why."""
    from .paper_crate import DATA_DOI

    text, low = paper.text, paper.text.lower()
    head = _header(text)
    body = re.sub(r"\s+", " ", low[:_references_start(low)])  # availability statements are in the body
    links = list(paper.repos) + [f"https://doi.org/{d}" for d in paper.dois if d.split("/")[0] in DATA_DOI]
    out, seen = [], set()
    for url in links:
        key = url.lower().rstrip("/").replace("https://doi.org/10.5281/zenodo.", "zenodo.org/records/")
        if key in seen:
            continue
        seen.add(key)
        kind = "code" if any(h in url for h in CODE_HOSTS) else "workflow" if "workflowhub" in url else \
            "model" if "huggingface.co/" in url and "/datasets/" not in url else "data"
        why, own = "", False
        if kind == "code":
            owner = url.split("://", 1)[-1].split("/")[1]
            hit = [t for t in _tokens(owner) if _named(t, head)]
            if hit:
                own, why = True, f"its owner ({owner}) is an author of the paper"
        entry = _reference_entry(text, url).lower()
        if not own and entry:
            names = re.findall(r"\b([a-z][a-z']{2,})", entry[:40])  # the reference's first author(s)
            hit = [n for n in names if n not in STOP and _named(n, head)]
            if hit:
                own, why = True, f"the reference that gives it is by an author ({hit[0].title()})"
        stub = url.split("://", 1)[-1].lower()[:40]
        for m in re.finditer(re.escape(stub), body):
            window = body[max(0, m.start() - 250):m.start()]
            if re.search(r"(code|data|software|materials?) (and \w+ )?availab|are (publicly )?available (at|on|from)|"
                         r"we (provide|share|release)|our (code|scripts|data)", window):
                own, why = True, why or "the paper says its code or data are available there"
        if kind == "code" and not own:
            why = "software the authors cite or used"
        out.append({"url": url, "kind": kind, "own": own, "why": why or "cited by the paper"})
    return sorted(out, key=lambda l: (not l["own"], l["kind"] != "code"))


def survey_code(code_dir: str | Path) -> dict[str, Any]:
    """What a cloned repository holds, read without running anything."""
    code = Path(code_dir)
    files = [p for p in code.rglob("*") if p.is_file() and ".git" not in p.parts]
    langs = Counter(LANGUAGES[p.suffix.lower()] for p in files if p.suffix.lower() in LANGUAGES)
    readme = next((p for p in sorted(files, key=lambda p: len(p.parts)) if p.name.lower().startswith("readme")), None)
    deps = sorted({p.relative_to(code).as_posix() for p in files if p.name in DEPENDENCY_FILES})
    scripts = sorted((p for p in files if LANGUAGES.get(p.suffix.lower()) in INTERPRETER),
                     key=lambda p: (len(p.parts), p.name))
    needs = {INTERPRETER[l] for l in langs if l in INTERPRETER}
    for doc in [p for p in files if p.suffix.lower() in {".qmd", ".rmd"}][:30]:  # the language inside literate docs
        chunk = doc.read_text(encoding="utf-8", errors="replace")
        if re.search(r"```\{r[ ,}]", chunk, re.I):
            needs.add("Rscript")
        if re.search(r"```\{python[ ,}]", chunk, re.I):
            needs.add("python3")
    needs = sorted(needs)
    return {"files": len(files), "languages": dict(langs.most_common()),
            "readme": readme.relative_to(code).as_posix() if readme else None,
            "entry_points": [p.relative_to(code).as_posix() for p in scripts[:12]], "dependency_files": deps,
            "needs": needs, "missing": [n for n in needs if not shutil.which(n)],
            "data_files": sum(1 for p in files if p.suffix.lower() in {".csv", ".tsv", ".xlsx", ".rds", ".rdata", ".json",
                                                                       ".parquet", ".txt", ".dta", ".sav"})}


def zenodo_title(url: str, get_json) -> dict[str, Any]:
    """Title, type and size of a Zenodo record, from its metadata (nothing is downloaded)."""
    m = re.search(r"zenodo\.(\d+)|zenodo\.org/records?/(\d+)", url)
    if not m:
        return {}
    rec = get_json(f"https://zenodo.org/api/records/{m.group(1) or m.group(2)}")
    meta = rec.get("metadata", {})
    size = sum(f.get("size", 0) for f in rec.get("files", []) or [])
    return {"title": meta.get("title"), "type": (meta.get("resource_type") or {}).get("title"),
            "files": len(rec.get("files") or []), "bytes": size}
