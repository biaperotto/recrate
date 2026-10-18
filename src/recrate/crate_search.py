"""Before recreating a paper's RO-Crate, look for one the authors (or others) already published.

``search_crates`` asks, for a paper's DOI (and its text):

1. **the paper itself**: links to a WorkflowHub workflow or a Zenodo deposit that is an RO-Crate;
2. **WorkflowHub**: publications registered with that DOI and the workflows linked to them, and workflows whose
   metadata mention the DOI;
3. **Zenodo**: deposits that cite the DOI and contain an RO-Crate (``ro-crate-metadata.json``, a ``.crate.zip``,
   or the RO-Crate keyword);
4. **DataCite**: records related to the DOI, held by WorkflowHub or Zenodo;
5. **the paper's code repository** on GitHub: an ``ro-crate-metadata.json`` at its root.

Every candidate says why it is one. Only the best is used, and only when it is tied to the paper itself (the DOI,
or a link in the paper), never by title alone.
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Callable

from .sources import REPO, Ref, identify

WFH = "https://workflowhub.eu"
PRIORITY = {"paper link": 0, "WorkflowHub": 1, "Zenodo": 2, "DataCite": 3, "code repository": 4}


@dataclass
class Candidate:
    where: str  # paper link | WorkflowHub | Zenodo | DataCite | code repository
    ref: Ref
    label: str
    evidence: str
    url: str = ""


@dataclass
class Search:
    doi: str | None
    candidates: list[Candidate] = field(default_factory=list)
    log: list[str] = field(default_factory=list)  # what was asked, and what came back

    @property
    def best(self) -> Candidate | None:
        return min(self.candidates, key=lambda c: PRIORITY.get(c.where, 9)) if self.candidates else None

    def summary(self) -> str:
        if self.best:
            return f"Found a published RO-Crate: {self.best.label} ({self.best.where}: {self.best.evidence})."
        asked = ", ".join(dict.fromkeys(line.split(":")[0] for line in self.log))
        return f"No published RO-Crate found for this paper (searched: {asked})."


def _norm_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    d = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", doi.strip(), flags=re.I)
    return d.rstrip(".").lower() or None


def _is_crate_record(rec: dict) -> bool:
    files = [f.get("key", "").lower() for f in rec.get("files", [])]
    keywords = [k.lower() for k in (rec.get("metadata", {}).get("keywords") or [])]
    return (any(f.endswith("ro-crate-metadata.json") or f.endswith(".crate.zip") for f in files)
            or any("ro-crate" in k or "rocrate" in k for k in keywords))


def search_crates(doi: str | None, text: str = "", repos: list[str] | None = None,
                  fetch_json: Callable[..., dict] | None = None) -> Search:
    if fetch_json is None:
        from .sources import get_json as fetch_json
    doi = _norm_doi(doi)
    s = Search(doi)
    seen: set[tuple[str, str]] = set()

    def add(c: Candidate) -> None:
        key = (c.ref.kind, c.ref.id)
        if key not in seen:
            seen.add(key)
            s.candidates.append(c)

    def ask(name: str, fn) -> None:
        try:
            n = fn()
            s.log.append(f"{name}: {n} found" if n else f"{name}: nothing")
        except Exception as e:  # noqa: BLE001 - a source being down is reported, not fatal
            s.log.append(f"{name}: unavailable ({type(e).__name__})")

    # 1. Links in the paper
    def paper_links() -> int:
        n = 0
        for r in identify(text or ""):
            if r.kind == "workflowhub":
                add(Candidate("paper link", r, r.label, "the paper links this workflow", f"{WFH}/workflows/{r.id}"))
                n += 1
        return n

    ask("the paper's links", paper_links)
    if doi:
        q = urllib.parse.quote(doi)

        # 2. WorkflowHub
        def workflowhub() -> int:
            n = 0
            pubs = fetch_json(f"{WFH}/publications?filter%5Bquery%5D={q}", "application/json").get("data", [])
            for p in pubs[:5]:
                det = fetch_json(f"{WFH}/publications/{p['id']}", "application/json")["data"]
                if _norm_doi(det["attributes"].get("doi")) != doi:
                    continue
                for w in det.get("relationships", {}).get("workflows", {}).get("data", []):
                    add(Candidate("WorkflowHub", Ref("workflowhub", str(w["id"])), f"WorkflowHub workflow {w['id']}",
                                  "registered on WorkflowHub as the workflow of this paper", f"{WFH}/workflows/{w['id']}"))
                    n += 1
            for w in fetch_json(f"{WFH}/workflows?filter%5Bquery%5D={q}", "application/json").get("data", [])[:5]:
                add(Candidate("WorkflowHub", Ref("workflowhub", str(w["id"])),
                              f"{w['attributes'].get('title')} (WorkflowHub #{w['id']})",
                              "its WorkflowHub entry mentions the paper's DOI", f"{WFH}/workflows/{w['id']}"))
                n += 1
            return n

        # 3. Zenodo
        def zenodo() -> int:
            n = 0
            hits = fetch_json("https://zenodo.org/api/records?size=10&q=" + urllib.parse.quote(f'"{doi}"'),
                              "application/json").get("hits", {}).get("hits", [])
            for h in hits:
                if _is_crate_record(h):
                    add(Candidate("Zenodo", Ref("zenodo", str(h["id"])), f"{h['metadata'].get('title')} (Zenodo {h['id']})",
                                  "a Zenodo RO-Crate that cites the paper", f"https://doi.org/{h.get('doi', '')}"))
                    n += 1
            return n

        # 4. DataCite
        def datacite() -> int:
            n = 0
            query = urllib.parse.quote(f'relatedIdentifiers.relatedIdentifier:"{doi}"')
            for rec in fetch_json(f"https://api.datacite.org/dois?query={query}&page%5Bsize%5D=20",
                                  "application/vnd.api+json").get("data", []):
                rid = rec.get("id", "")
                kind = (rec.get("attributes", {}).get("types") or {}).get("resourceTypeGeneral", "")
                for r in identify(rid):
                    if r.kind == "workflowhub" or (r.kind == "zenodo" and kind in {"Workflow", "Software"}):
                        add(Candidate("DataCite", r, r.label, f"DataCite records it as related to the paper ({kind})",
                                      f"https://doi.org/{rid}"))
                        n += 1
            return n

        ask("WorkflowHub", workflowhub)
        ask("Zenodo", zenodo)
        ask("DataCite", datacite)

    # 5. The code repository
    def code() -> int:
        n = 0
        for url in (repos or [])[:3]:
            m = re.match(r"https?://github\.com/([\w.-]+)/([\w.-]+)", url)
            if not m:
                continue
            owner, repo = m.group(1), m.group(2).removesuffix(".git")
            files = fetch_json(f"https://api.github.com/repos/{owner}/{repo}/contents/", "application/vnd.github+json")
            if isinstance(files, list) and any(f.get("name") == "ro-crate-metadata.json" for f in files):
                add(Candidate("code repository", Ref("repo", f"https://github.com/{owner}/{repo}"),
                              f"github.com/{owner}/{repo}", "the paper's code repository is an RO-Crate",
                              f"https://github.com/{owner}/{repo}"))
                n += 1
        return n

    if repos:
        ask("the code repository", code)
    return s


def repos_in(text: str) -> list[str]:
    return list(dict.fromkeys(m.group(0).rstrip(".,;)") for m in REPO.finditer(text or "")))
