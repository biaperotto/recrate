"""M1 — locate an RO-Crate for a paper.

Given a paper (its DOI, landing page and the Signposting links M0 gathered),
search for a published Workflow RO-Crate, in this order, stopping at the first
good one:

1. **FAIR Signposting** on the landing page (``describedby`` / ``item`` /
   ``cite-as`` — how RO-Crate 1.2 is retrieved).
2. **Paper metadata** — Crossref / DataCite related identifiers (e.g.
   ``is-supplemented-by``) and links to Zenodo, Figshare, WorkflowHub or GitHub.
3. **Links in the paper text** — ``paper.repos``.

Each candidate is resolved to a downloadable crate (a Zenodo/Figshare file named
``ro-crate-metadata.json`` or ``*.crate.zip``, a GitHub repo with root metadata,
or a WorkflowHub RO-Crate download), unzipped **safely** into the target folder,
and checked: it must parse with :mod:`recrate.crate`, declare at least one
``ComputationalWorkflow``, and belong to this paper. Every candidate tried is
recorded in ``locate.json`` with why it was accepted or rejected.

No diagnosis is made here and no LLM is used; this only finds a crate.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import zipfile
from dataclasses import asdict, dataclass, field
from io import BytesIO
from pathlib import Path

from . import http
from .crate import CrateError, METADATA_FILE, list_methods, load_crate, resolve_crate_dir
from .paper import Paper

ZENODO_DOI = re.compile(r"10\.5281/zenodo\.(\d+)", re.I)
ZENODO_URL = re.compile(r"zenodo\.org/(?:api/)?records?/(\d+)", re.I)
FIGSHARE_URL = re.compile(r"figshare\.com/(?:articles/[^/]+/[^/]+/|.*?/)(\d+)", re.I)
WORKFLOWHUB_URL = re.compile(r"workflowhub\.eu/workflows/(\d+)", re.I)
GITHUB_URL = re.compile(r"github\.com/([\w.\-]+)/([\w.\-]+?)(?:\.git|/|$)", re.I)


@dataclass
class Candidate:
    origin: str          # signposting | crossref | datacite | paper-text
    url: str
    accepted: bool = False
    reason: str = ""


@dataclass
class LocateResult:
    crate_dir: Path | None
    source_url: str | None
    candidates: list[Candidate] = field(default_factory=list)

    @property
    def found(self) -> bool:
        return self.crate_dir is not None

    def write_json(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps({
            "found": self.found,
            "source_url": self.source_url,
            "candidates": [asdict(c) for c in self.candidates],
        }, indent=2, ensure_ascii=False), encoding="utf-8")


# ---- DOI helpers ----------------------------------------------------------

def norm_doi(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip().lower()
    v = re.sub(r"^https?://(dx\.)?doi\.org/", "", v)
    v = v.split("#", 1)[0]
    return v.rstrip("/.") or None


# ---- candidate discovery --------------------------------------------------

def _candidate_urls(paper: Paper, signposting: list[dict], cache_dir) -> list[Candidate]:
    cands: list[Candidate] = []
    seen: set[str] = set()

    def add(origin: str, url: str) -> None:
        url = (url or "").strip()
        if url and url not in seen:
            seen.add(url)
            cands.append(Candidate(origin=origin, url=url))

    for s in signposting:
        add("signposting", s.get("url", ""))

    for origin, ids in _metadata_relations(paper.doi, cache_dir):
        for ident in ids:
            add(origin, ident)

    for repo in paper.repos:
        add("paper-text", repo)

    return cands


def _metadata_relations(doi: str | None, cache_dir) -> list[tuple[str, list[str]]]:
    if not doi:
        return []
    out: list[tuple[str, list[str]]] = []
    # Crossref
    try:
        msg = http.get(f"https://api.crossref.org/works/{doi}", accept="application/json",
                       cache_dir=cache_dir).json().get("message", {})
        ids = []
        for _rel, entries in (msg.get("relation") or {}).items():
            for e in entries if isinstance(entries, list) else [entries]:
                ids.append(_relation_to_url(e.get("id", ""), e.get("id-type", "")))
        out.append(("crossref", [i for i in ids if i]))
    except (http.HttpError, ValueError):
        pass
    # DataCite (fallback / complement)
    try:
        attrs = http.get(f"https://api.datacite.org/dois/{doi}", accept="application/json",
                         cache_dir=cache_dir).json().get("data", {}).get("attributes", {})
        ids = [_relation_to_url(r.get("relatedIdentifier", ""), r.get("relatedIdentifierType", ""))
               for r in attrs.get("relatedIdentifiers", [])]
        out.append(("datacite", [i for i in ids if i]))
    except (http.HttpError, ValueError):
        pass
    return out


def _relation_to_url(ident: str, id_type: str) -> str:
    ident = (ident or "").strip()
    if not ident:
        return ""
    m = ZENODO_DOI.search(ident)
    if m:
        return f"https://zenodo.org/api/records/{m.group(1)}"
    if (id_type or "").lower() == "doi" and ident.lower().startswith("10."):
        return f"https://doi.org/{ident}"
    return ident


# ---- resolving one candidate to a crate folder ----------------------------

def _resolve(url: str, dest: Path, cache_dir) -> str:
    """Download the candidate into ``dest`` and return a short note.

    Raises :class:`http.HttpError` or :class:`ValueError` if nothing usable.
    """
    m = ZENODO_URL.search(url)
    if m:
        return _from_zenodo(m.group(1), dest, cache_dir)
    m = FIGSHARE_URL.search(url)
    if m and "figshare.com" in url.lower():
        return _from_figshare(m.group(1), dest, cache_dir)
    m = WORKFLOWHUB_URL.search(url)
    if m:
        return _from_workflowhub(m.group(1), dest, cache_dir)
    m = GITHUB_URL.search(url)
    if m and "github.com" in url.lower():
        return _from_github(m.group(1), m.group(2), dest, cache_dir)
    if url.lower().endswith((".crate.zip", ".zip")):
        _safe_unzip(http.get(url, cache_dir=cache_dir).content, dest)
        return "downloaded and unzipped archive"
    if url.lower().endswith(METADATA_FILE):
        dest.mkdir(parents=True, exist_ok=True)
        (dest / METADATA_FILE).write_bytes(http.get(url, cache_dir=cache_dir).content)
        return "downloaded ro-crate-metadata.json"
    # A bare DOI/landing: try content negotiation for the crate metadata.
    r = http.get(url, accept="application/ld+json", cache_dir=cache_dir)
    if "json" in r.header("content-type") and r.content.lstrip()[:1] == b"{":
        dest.mkdir(parents=True, exist_ok=True)
        (dest / METADATA_FILE).write_bytes(r.content)
        return "retrieved crate metadata by content negotiation"
    raise ValueError("not a recognised crate location")


def _pick_files(files: list[dict], name_key: str, link_fn) -> list[tuple[str, str]]:
    """From a file listing, the ro-crate-metadata.json or *.crate.zip entries."""
    out = []
    for f in files:
        name = str(f.get(name_key, ""))
        low = name.lower()
        if low == METADATA_FILE or low.endswith(".crate.zip"):
            out.append((name, link_fn(f)))
    return out


def _from_zenodo(record_id: str, dest: Path, cache_dir) -> str:
    rec = http.get(f"https://zenodo.org/api/records/{record_id}", accept="application/json",
                   cache_dir=cache_dir).json()
    files = rec.get("files", [])
    picks = _pick_files(files, "key", lambda f: f.get("links", {}).get("self")
                        or f.get("links", {}).get("download", ""))
    return _download_picks(picks, dest, cache_dir, f"Zenodo record {record_id}")


def _from_figshare(article_id: str, dest: Path, cache_dir) -> str:
    art = http.get(f"https://api.figshare.com/v2/articles/{article_id}", accept="application/json",
                   cache_dir=cache_dir).json()
    picks = _pick_files(art.get("files", []), "name", lambda f: f.get("download_url", ""))
    return _download_picks(picks, dest, cache_dir, f"Figshare article {article_id}")


def _from_workflowhub(workflow_id: str, dest: Path, cache_dir) -> str:
    # WorkflowHub serves the Workflow RO-Crate as a zip at this path.
    url = f"https://workflowhub.eu/workflows/{workflow_id}/ro_crate?version="
    _safe_unzip(http.get(url, accept="application/zip", cache_dir=cache_dir).content, dest)
    return f"WorkflowHub workflow {workflow_id} RO-Crate"


def _from_github(owner: str, repo: str, dest: Path, cache_dir) -> str:
    repo = repo.removesuffix(".git")
    last = None
    for branch in ("main", "master"):
        try:
            data = http.get(f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/{branch}",
                            cache_dir=cache_dir).content
        except http.HttpError as e:
            last = e
            continue
        _safe_unzip(data, dest, strip_top_level=True)
        return f"GitHub {owner}/{repo}@{branch}"
    raise last or ValueError("could not download the GitHub repository")


def _download_picks(picks: list[tuple[str, str]], dest: Path, cache_dir, origin: str) -> str:
    if not picks:
        raise ValueError(f"{origin}: no ro-crate-metadata.json or *.crate.zip among its files")
    # Prefer a zipped crate (self-contained) over a lone metadata file.
    picks.sort(key=lambda nl: 0 if nl[0].lower().endswith(".crate.zip") else 1)
    name, link = picks[0]
    if not link:
        raise ValueError(f"{origin}: file {name} has no download link")
    content = http.get(link, cache_dir=cache_dir).content
    if name.lower().endswith(".crate.zip"):
        _safe_unzip(content, dest)
    else:
        dest.mkdir(parents=True, exist_ok=True)
        (dest / METADATA_FILE).write_bytes(content)
    return f"{origin}: {name}"


# ---- safe unzip -----------------------------------------------------------

def _safe_unzip(data: bytes, dest: Path, strip_top_level: bool = False) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    dest_root = dest.resolve()
    with zipfile.ZipFile(BytesIO(data)) as zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]
        top = ""
        if strip_top_level and names:
            firsts = {n.split("/", 1)[0] for n in names}
            if len(firsts) == 1:
                top = next(iter(firsts)) + "/"
        for name in names:
            rel = name[len(top):] if top and name.startswith(top) else name
            if not rel:
                continue
            target = (dest_root / rel).resolve()
            if not str(target).startswith(str(dest_root) + "/") and target != dest_root:
                raise ValueError(f"refusing to extract {name!r}: escapes the target folder")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(name))


# ---- is it a good crate for this paper? -----------------------------------

def _good_crate(folder: Path, paper: Paper) -> tuple[bool, str]:
    try:
        crate_dir = resolve_crate_dir(folder)
        methods = list_methods(crate_dir)
    except (CrateError, ValueError, json.JSONDecodeError) as e:
        return False, f"does not parse as an RO-Crate ({type(e).__name__})"
    if not methods:
        return False, "valid RO-Crate but declares no ComputationalWorkflow"
    try:
        root_doi = norm_doi(load_crate(crate_dir).citation)
    except CrateError:
        root_doi = None
    paper_doi = norm_doi(paper.doi)
    if root_doi and paper_doi and root_doi != paper_doi:
        # The paper linked here, but the crate cites a different work.
        return True, (f"accepted (paper links it); note: root citation {root_doi} "
                      f"differs from the paper DOI {paper_doi}")
    match = "root citation matches the paper DOI" if (root_doi and root_doi == paper_doi) \
        else "linked from the paper"
    return True, f"valid RO-Crate with {len(methods)} workflow(s); {match}"


# ---- the entry point ------------------------------------------------------

def locate_crate(paper: Paper, signposting: list[dict] | None = None, out_dir: str | Path = "crate",
                 cache_dir: str | Path | None = http.CACHE_DIR) -> LocateResult:
    """Find and download an RO-Crate for ``paper``. See the module docstring."""
    out_dir = Path(out_dir)
    candidates = _candidate_urls(paper, signposting or [], cache_dir)
    result = LocateResult(crate_dir=None, source_url=None, candidates=candidates)

    for cand in candidates:
        with tempfile.TemporaryDirectory(prefix="recrate-locate-") as tmp:
            staging = Path(tmp) / "crate"
            try:
                note = _resolve(cand.url, staging, cache_dir)
            except (http.HttpError, ValueError, zipfile.BadZipFile) as e:
                cand.reason = str(e)
                continue
            good, why = _good_crate(staging, paper)
            if not good:
                cand.reason = f"{note}; {why}"
                continue
            if out_dir.exists():
                shutil.rmtree(out_dir)
            shutil.copytree(resolve_crate_dir(staging), out_dir)
            cand.accepted = True
            cand.reason = f"{note}; {why}"
            result.crate_dir = out_dir
            result.source_url = cand.url
            return result
    return result
