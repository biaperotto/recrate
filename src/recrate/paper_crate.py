"""An RO-Crate for a paper that was published without one.

``build_paper_crate`` describes the research object from what the paper and its code say: the paper (with
its DOI and publication date), the software environment as it was at publication (``environment.py``),
the code repository, and the datasets the paper cites. It is marked as a draft: it is only as good as what
was published, and a person should confirm it. It has no runnable method until one is described (by the
drafter, or by hand); the agent can already answer from the paper.

``add_environment`` adds the dated environment to any crate (e.g. one drafted from the paper), filling
software versions the crate leaves out.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .environment import Environment, Software, save
from .paper import Paper

DATA_DOI = {"10.5281": "Zenodo", "10.6084": "figshare", "10.5061": "Dryad", "10.17632": "Mendeley Data",
            "10.7910": "Harvard Dataverse", "10.6019": "EMBL-EBI", "10.17867": "BioImage Archive",
            "10.48546": "WorkflowHub", "10.18112": "OpenNeuro", "10.15468": "GBIF"}
DATA_HOSTS = ("huggingface.co/datasets", "huggingface.co", "zenodo.org", "figshare.com", "datadryad.org", "ebi.ac.uk",
              "idr.openmicroscopy.org", "osf.io", "dataverse", "openneuro.org", "ncbi.nlm.nih.gov/geo", "physionet.org",
              "kaggle.com", "workflowhub.eu")
HOST_NAME = {"huggingface.co/datasets": "Hugging Face dataset", "huggingface.co": "Hugging Face model",
             "physionet.org": "PhysioNet", "kaggle.com": "Kaggle"}


def data_links(p: Paper) -> list[dict[str, str]]:
    """Datasets (and workflows) the paper cites, by DOI prefix or host."""
    out, seen = [], set()
    for doi in p.dois:
        repo = DATA_DOI.get(doi.split("/")[0])
        if repo and doi not in seen:
            seen.add(doi)
            out.append({"id": f"https://doi.org/{doi}", "where": repo})
    for url in p.repos:
        if any(h in url for h in DATA_HOSTS) and url not in seen:
            seen.add(url)
            host = next(h for h in DATA_HOSTS if h in url)
            out.append({"id": url, "where": HOST_NAME.get(host, host)})
    return out


def _software_entity(i: int, s: Software) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    sid = f"#software-{re.sub(r'[^a-z0-9]+', '-', s.name.lower()).strip('-') or i}"
    props = [{"@id": f"{sid}-how", "@type": "PropertyValue", "name": "version source", "value": s.note}]
    if s.evidence:
        props.append({"@id": f"{sid}-evidence", "@type": "PropertyValue", "name": "evidence", "value": s.evidence})
    if s.released:
        props.append({"@id": f"{sid}-released", "@type": "PropertyValue", "name": "release date", "value": s.released})
    e = {"@id": sid, "@type": "SoftwareApplication", "name": s.name,
         "additionalProperty": [{"@id": p["@id"]} for p in props]}
    if s.version:
        e["softwareVersion"] = s.version
    if s.url:
        e["url"] = s.url
    return e, props


def add_environment(crate_dir: Path, env: Environment) -> None:
    """Add the dated environment to a crate: software entities, environment.json, requirements.txt."""
    crate_dir = Path(crate_dir)
    mpath = crate_dir / "ro-crate-metadata.json"
    meta = json.loads(mpath.read_text(encoding="utf-8"))
    graph = meta["@graph"]
    ids = {e["@id"]: e for e in graph}
    root = ids["./"]
    by_name = {s.name.lower(): s for s in env.software}
    # 1. Fill versions the crate leaves out
    for e in graph:
        if e.get("@type") == "SoftwareApplication" and not e.get("softwareVersion"):
            s = by_name.get(str(e.get("name", "")).lower().replace("galaxy tool ", ""))
            if s and s.version:
                e["softwareVersion"] = s.version
                ent, props = _software_entity(0, s)
                e.setdefault("additionalProperty", []).extend(ent["additionalProperty"])
                graph.extend(p for p in props if p["@id"] not in ids)
    # 2. Software the crate doesn't mention yet
    named = {str(e.get("name", "")).lower() for e in graph if e.get("@type") == "SoftwareApplication"}
    refs = []
    for i, s in enumerate(env.software):
        if s.name.lower() in named:
            continue
        ent, props = _software_entity(i, s)
        if ent["@id"] in ids:
            continue
        graph.append(ent)
        graph.extend(props)
        refs.append({"@id": ent["@id"]})
    # 3. Files that let anyone rebuild it
    (crate_dir / "environment").mkdir(exist_ok=True)
    save(env, crate_dir / "environment" / "environment.json")
    (crate_dir / "environment" / "requirements.txt").write_text(env.requirements_txt(), encoding="utf-8")
    for fid, name, fmt in (("environment/environment.json", "Software environment at publication", "application/json"),
                           ("environment/requirements.txt", "Python packages pinned as of publication", "text/plain")):
        if fid not in ids:
            graph.append({"@id": fid, "@type": "File", "name": name, "encodingFormat": fmt,
                          "description": f"Recreated by ReCRATE for {env.published or 'an unknown date'} "
                                         f"(publication date from {env.published_source})."})
            root.setdefault("hasPart", []).append({"@id": fid})
    root["mentions"] = root.get("mentions", []) + refs
    if env.published and not root.get("datePublished"):
        root["datePublished"] = env.published
    mpath.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")


LICENCE_NAMES = {"by": "CC BY", "by-sa": "CC BY-SA", "by-nc": "CC BY-NC", "by-nd": "CC BY-ND", "by-nc-sa": "CC BY-NC-SA",
                 "by-nc-nd": "CC BY-NC-ND", "zero": "CC0"}


def licence_entity(url: str | None, source: str) -> dict[str, Any]:
    """A contextual entity for the crate's licence (RO-Crate 1.1 requires one): the paper's, from its DOI metadata,
    or one saying plainly that no licence could be found."""
    if not url:
        return {"@id": "#licence-not-stated", "@type": "CreativeWork", "name": "Licence not stated",
                "description": "No licence could be found for this paper or its materials. Check the publisher's "
                               "terms before reusing its files."}
    m = re.search(r"creativecommons\.org/(?:licenses|publicdomain)/([a-z-]+)/(\d\.\d)", url)
    name = f"{LICENCE_NAMES.get(m.group(1), m.group(1).upper())} {m.group(2)}" if m else url
    return {"@id": url, "@type": "CreativeWork", "name": name,
            "description": f"The paper's licence ({source}). It applies to the paper and, unless stated otherwise, to "
                           "this description of it."}


def build_paper_crate(p: Paper, env: Environment, out: Path, code_url: str | None = None,
                      paper_file: Path | None = None, paper_id: str | None = None,
                      links: list[dict[str, Any]] | None = None, meta: dict[str, Any] | None = None) -> Path:
    """A draft crate describing a paper published without one. Returns the crate folder."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    graph: list[dict[str, Any]] = []
    has_part: list[dict[str, str]] = []
    paper_id = paper_id or (f"https://doi.org/{p.doi}" if p.doi else "#paper")
    if paper_file and Path(paper_file).exists():
        import shutil

        shutil.copy2(paper_file, out / Path(paper_file).name)
        has_part.append({"@id": Path(paper_file).name})
        graph.append({"@id": Path(paper_file).name, "@type": ["File", "ScholarlyArticle"], "name": p.title,
                      "about": {"@id": paper_id},
                      "encodingFormat": {".pdf": "application/pdf", ".md": "text/markdown"}.get(
                          Path(paper_file).suffix.lower(), "text/plain")})
    graph.append({"@id": paper_id, "@type": "ScholarlyArticle", "name": p.title,
                  **({"datePublished": env.published} if env.published else {}),
                  **({"identifier": paper_id} if p.doi else {})})
    mentions = []
    by_url = {l["url"]: l for l in links or []}
    code = [l for l in links or [] if l["kind"] == "code"]
    if code_url and code_url not in by_url:
        code.insert(0, {"url": code_url, "own": True, "why": "given with the paper"})
    for l in code:  # the authors' own code, and software they cite: told apart
        graph.append({"@id": l["url"], "@type": "SoftwareSourceCode", "codeRepository": l["url"],
                      "name": "Code of the paper" if l["own"] else f"Software cited by the paper ({l['url'].split('/')[-1]})",
                      "description": l.get("why", "")})
        mentions.append({"@id": l["url"]})
    for d in data_links(p):
        l = by_url.get(d["id"], {})
        name = l.get("title") or f"Data cited by the paper ({d['where']})"
        typ = "SoftwareSourceCode" if (l.get("type") or "").lower() == "software" else "Dataset"
        graph.append({"@id": d["id"], "@type": typ, "name": name,
                      "description": (f"{'By the authors' if l.get('own') else 'Cited by the paper'} ({d['where']}"
                                      + (f", {l['type'].lower()}" if l.get("type") else "") + ")."),
                      **({"contentSize": str(l["bytes"])} if l.get("bytes") else {})})
        mentions.append({"@id": d["id"]})
    meta = meta or {}
    licence = licence_entity(meta.get("license"), "from its DOI metadata")
    graph.append(licence)
    authors = []
    for i, a in enumerate(meta.get("authors") or []):  # the paper's authors, with their ORCID when given
        aid = a.get("orcid") or f"#author-{i}"
        graph.append({"@id": aid, "@type": "Person", "name": a["name"]})
        authors.append({"@id": aid})
    graph.append({"@id": "#draft", "@type": "PropertyValue", "name": "draft", "value": True})
    graph.append({"@id": "#domain-profile", "@type": "PropertyValue", "name": "domain profile", "value": "generic"})
    root = {"@id": "./", "@type": "Dataset", "name": p.title,
            "description": "RO-Crate recreated by ReCRATE from the published paper: its software environment as of "
                           "publication, code and cited data. A draft until a person confirms it.",
            "citation": {"@id": paper_id}, "about": {"@id": paper_id}, "hasPart": has_part, "mentions": mentions,
            "additionalProperty": [{"@id": "#draft"}, {"@id": "#domain-profile"}],
            "license": {"@id": licence["@id"]}, **({"author": authors} if authors else {}),
            **({"datePublished": env.published} if env.published else {})}
    meta = {"@context": "https://w3id.org/ro/crate/1.1/context",
            "@graph": [{"@id": "ro-crate-metadata.json", "@type": "CreativeWork",
                        "conformsTo": {"@id": "https://w3id.org/ro/crate/1.1"}, "about": {"@id": "./"}}, root, *graph]}
    (out / "ro-crate-metadata.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    add_environment(out, env)
    return out
