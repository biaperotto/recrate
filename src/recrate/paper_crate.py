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


def build_paper_crate(p: Paper, env: Environment, out: Path, code_url: str | None = None,
                      paper_file: Path | None = None, paper_id: str | None = None) -> Path:
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
                      "about": {"@id": paper_id}})
    graph.append({"@id": paper_id, "@type": "ScholarlyArticle", "name": p.title,
                  **({"datePublished": env.published} if env.published else {}),
                  **({"identifier": paper_id} if p.doi else {})})
    mentions = []
    if code_url:
        graph.append({"@id": code_url, "@type": "SoftwareSourceCode", "name": "Code of the paper",
                      "codeRepository": code_url})
        mentions.append({"@id": code_url})
    for d in data_links(p):
        graph.append({"@id": d["id"], "@type": "Dataset", "name": f"Data cited by the paper ({d['where']})"})
        mentions.append({"@id": d["id"]})
    graph.append({"@id": "#draft", "@type": "PropertyValue", "name": "draft", "value": True})
    graph.append({"@id": "#domain-profile", "@type": "PropertyValue", "name": "domain profile", "value": "generic"})
    root = {"@id": "./", "@type": "Dataset", "name": p.title,
            "description": "RO-Crate recreated by ReCRATE from the published paper: its software environment as of "
                           "publication, code and cited data. A draft until a person confirms it.",
            "citation": {"@id": paper_id}, "about": {"@id": paper_id}, "hasPart": has_part, "mentions": mentions,
            "additionalProperty": [{"@id": "#draft"}, {"@id": "#domain-profile"}],
            **({"datePublished": env.published} if env.published else {})}
    meta = {"@context": "https://w3id.org/ro/crate/1.1/context",
            "@graph": [{"@id": "ro-crate-metadata.json", "@type": "CreativeWork",
                        "conformsTo": {"@id": "https://w3id.org/ro/crate/1.1"}, "about": {"@id": "./"}}, root, *graph]}
    (out / "ro-crate-metadata.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    add_environment(out, env)
    return out
