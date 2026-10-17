"""Find the parts of a research object from what a person has: a paper, a crate, an identifier, a link.

``identify(text)`` recognises, in free text:

* WorkflowHub workflows: a URL (``workflowhub.eu/workflows/771?version=2``) or DOI (``10.48546/workflowhub.workflow.771.2``)
* Zenodo records: a URL or DOI (``10.5281/zenodo.3362976``)
* arXiv papers: an arXiv URL or identifier
* any other DOI (taken to be a paper)
* code repositories on GitHub, GitLab or Codeberg

``fetch(ref, dest)`` downloads each one into its own new folder and says what it is: a paper, a crate,
code or data. ``from_upload(path, dest)`` does the same for a file a person uploaded (PDF, text, a crate
zip, ``ro-crate-metadata.json``). Downloads are only read here, never executed; archives are unpacked
with path checks.
"""

from __future__ import annotations

import json
import re
import shutil
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

UA = {"User-Agent": "ReCRATE/0.1 (+https://github.com/recrate/recrate)"}
MAX_DOWNLOAD = 200 * 1024 * 1024  # bytes per record; larger data is better pointed at as a folder
PAPER_SUFFIXES = {".pdf", ".md", ".txt", ".markdown", ".html", ".htm"}

WFH_URL = re.compile(r"workflowhub\.eu/workflows/(\d+)(?:[^\s]*?[?&]version=(\d+))?", re.I)
WFH_DOI = re.compile(r"10\.48546/workflowhub\.workflow\.(\d+)(?:\.(\d+))?", re.I)
ZEN = re.compile(r"(?:zenodo\.org/(?:records?|deposit)/|10\.5281/zenodo\.)(\d+)", re.I)
ARXIV = re.compile(r"(?:arxiv\.org/(?:abs|pdf)/|arXiv:\s*)(\d{4}\.\d{4,5}(?:v\d+)?)", re.I)
DOI = re.compile(r"\b(10\.\d{4,9}/[^\s\"'<>]+[^\s\"'<>.,;)\]])", re.I)
REPO = re.compile(r"https?://(?:github\.com|gitlab\.com|codeberg\.org)/[\w.-]+/[\w.-]+", re.I)


@dataclass
class Ref:
    kind: str  # workflowhub | zenodo | arxiv | doi | repo
    id: str
    version: str | None = None
    text: str = ""

    @property
    def label(self) -> str:
        return {"workflowhub": f"WorkflowHub workflow {self.id}" + (f" v{self.version}" if self.version else ""),
                "zenodo": f"Zenodo record {self.id}", "arxiv": f"arXiv {self.id}", "doi": f"DOI {self.id}",
                "repo": self.id}[self.kind]


@dataclass
class Found:
    kind: str  # paper | crate | code | data | none
    label: str
    path: Path | None = None
    url: str | None = None
    notes: list[str] = field(default_factory=list)


def identify(text: str) -> list[Ref]:
    """Every identifier or link in ``text``, most specific first, without duplicates."""
    refs: list[Ref] = []
    taken: list[tuple[int, int]] = []

    def add(m: re.Match, ref: Ref) -> None:
        if any(a <= m.start() < b for a, b in taken):
            return
        taken.append(m.span())
        if not any(r.kind == ref.kind and r.id == ref.id for r in refs):
            refs.append(ref)

    for m in WFH_DOI.finditer(text):
        add(m, Ref("workflowhub", m.group(1), m.group(2), m.group(0)))
    for m in WFH_URL.finditer(text):
        add(m, Ref("workflowhub", m.group(1), m.group(2), m.group(0)))
    for m in ZEN.finditer(text):
        add(m, Ref("zenodo", m.group(1), None, m.group(0)))
    for m in ARXIV.finditer(text):
        add(m, Ref("arxiv", m.group(1), None, m.group(0)))
    for m in DOI.finditer(text):
        if m.group(1).lower().startswith("10.48550/arxiv."):
            add(m, Ref("arxiv", m.group(1).split("arXiv.", 1)[-1].split("arxiv.", 1)[-1], None, m.group(0)))
        else:
            add(m, Ref("doi", m.group(1), None, m.group(0)))
    for m in REPO.finditer(text):
        add(m, Ref("repo", m.group(0).rstrip(".,;:").removesuffix(".git"), None, m.group(0)))
    return refs


# ---- downloading ---------------------------------------------------------------------------------

def _ssl_context():
    """Verified TLS. Uses certifi's bundle when available: python.org builds on macOS ship without one."""
    import ssl

    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _get(url: str, accept: str | None = None, timeout: int = 60) -> tuple[bytes, str]:
    req = urllib.request.Request(url, headers={**UA, **({"Accept": accept} if accept else {})})
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as r:  # noqa: S310
        ctype = r.headers.get("Content-Type", "")
        data = r.read(MAX_DOWNLOAD + 1)
    if len(data) > MAX_DOWNLOAD:
        raise ValueError(f"{url} is larger than {MAX_DOWNLOAD // 2**20} MB; download it yourself and point at the folder.")
    return data, ctype


def _json(url: str) -> dict:
    return json.loads(_get(url, "application/json")[0])


def get_json(url: str, accept: str = "application/json") -> dict:
    """JSON over HTTPS, with verified TLS (used for release histories and DOI metadata)."""
    return json.loads(_get(url, accept, timeout=30)[0])


def safe_unzip(zpath: Path, dest: Path) -> Path:
    """Unpack into ``dest`` (new), refusing members that would land outside it."""
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    with zipfile.ZipFile(zpath) as z:
        for info in z.infolist():
            target = (dest / info.filename).resolve()
            if root not in target.parents and target != root:
                raise ValueError(f"Unsafe path in archive: {info.filename}")
        z.extractall(dest)
    return dest


def crate_root(folder: Path) -> Path | None:
    """The folder holding ``ro-crate-metadata.json``: ``folder`` itself or a single subfolder."""
    if (folder / "ro-crate-metadata.json").exists():
        return folder
    hits = sorted(folder.rglob("ro-crate-metadata.json"), key=lambda p: len(p.parts))
    return hits[0].parent if hits else None


def fetch_workflowhub(wid: str, version: str | None, dest: Path) -> Found:
    info = _json(f"https://workflowhub.eu/workflows/{wid}")["data"]["attributes"]
    version = version or str(info.get("latest_version") or len(info.get("versions") or [1]))
    data, _ = _get(f"https://workflowhub.eu/workflows/{wid}/ro_crate?version={version}")
    z = dest / f"workflowhub-{wid}-v{version}.zip"
    dest.mkdir(parents=True, exist_ok=True)
    z.write_bytes(data)
    crate = safe_unzip(z, dest / "crate")
    z.unlink()
    return Found("crate", f"{info.get('title')} (WorkflowHub #{wid}, v{version})", crate,
                 f"https://workflowhub.eu/workflows/{wid}?version={version}",
                 [f"Licence: {info.get('license')}", f"Workflow type: {(info.get('workflow_class') or {}).get('title')}"])


def fetch_zenodo(rid: str, dest: Path) -> Found:
    rec = _json(f"https://zenodo.org/api/records/{rid}")
    meta, files = rec["metadata"], rec.get("files", [])
    total = sum(f.get("size", 0) for f in files)
    title = meta.get("title", f"Zenodo {rid}")
    url = f"https://doi.org/{rec.get('doi') or f'10.5281/zenodo.{rid}'}"
    if total > MAX_DOWNLOAD:
        return Found("data", title, None, url, [f"{len(files)} files, {total / 2**20:.0f} MB: too large to fetch "
                                                "here. Download what you need and point ReCRATE at the folder."])
    dest.mkdir(parents=True, exist_ok=True)
    for f in files:
        (dest / f["key"]).write_bytes(_get(f["links"]["self"])[0])
    for z in sorted(dest.glob("*.zip")):
        out = safe_unzip(z, dest / z.stem)
        z.unlink()
        if crate_root(out):
            return Found("crate", title, crate_root(out), url, [f"Licence: {(meta.get('license') or {}).get('id')}"])
    if crate_root(dest):
        return Found("crate", title, crate_root(dest), url)
    pdfs = sorted(dest.rglob("*.pdf"))
    if pdfs and len(files) == 1:
        return Found("paper", title, pdfs[0], url)
    return Found("data", title, dest, url, [f"{len(files)} file(s), {total / 2**20:.1f} MB, licence "
                                            f"{(meta.get('license') or {}).get('id')}"])


def fetch_arxiv(aid: str, dest: Path) -> Found:
    data, _ = _get(f"https://arxiv.org/pdf/{aid}")
    dest.mkdir(parents=True, exist_ok=True)
    p = dest / f"arxiv-{aid.replace('/', '_')}.pdf"
    p.write_bytes(data)
    return Found("paper", f"arXiv {aid}", p, f"https://arxiv.org/abs/{aid}")


def fetch_doi(doi: str, dest: Path) -> Found:
    """A paper DOI: its metadata and abstract, and the full text when the publisher links an open PDF."""
    meta = json.loads(_get(f"https://doi.org/{urllib.parse.quote(doi)}", "application/vnd.citationstyles.csl+json")[0])
    title = meta.get("title") if isinstance(meta.get("title"), str) else (meta.get("title") or ["Untitled"])[0]
    dest.mkdir(parents=True, exist_ok=True)
    for link in meta.get("link") or []:
        if "pdf" in (link.get("content-type") or "") and link.get("URL"):
            try:
                data, ctype = _get(link["URL"])
                if data[:4] == b"%PDF":
                    p = dest / "paper.pdf"
                    p.write_bytes(data)
                    return Found("paper", title, p, f"https://doi.org/{doi}", ["Full text (open access PDF)."])
            except Exception:  # noqa: BLE001 - fall back to the abstract
                pass
    authors = ", ".join(f"{a.get('given', '')} {a.get('family', '')}".strip() for a in meta.get("author", []))
    abstract = re.sub(r"<[^>]+>", "", meta.get("abstract") or "").strip()
    year = ((meta.get("issued") or {}).get("date-parts") or [[None]])[0][0]
    journal = meta.get("container-title") if isinstance(meta.get("container-title"), str) else \
        (meta.get("container-title") or [""])[0]
    md = (f"# {title}\n\n{authors}. {journal} ({year}). https://doi.org/{doi}\n\n## Abstract\n\n"
          f"{abstract or 'The publisher does not provide an abstract.'}\n")
    p = dest / "paper.md"
    p.write_text(md, encoding="utf-8")
    return Found("paper", title, p, f"https://doi.org/{doi}",
                 ["Only the metadata and abstract are open: upload the PDF for full text."])


def fetch(ref: Ref, dest: Path) -> Found:
    dest = Path(dest)
    if ref.kind == "workflowhub":
        return fetch_workflowhub(ref.id, ref.version, dest)
    if ref.kind == "zenodo":
        return fetch_zenodo(ref.id, dest)
    if ref.kind == "arxiv":
        return fetch_arxiv(ref.id, dest)
    if ref.kind == "doi":
        return fetch_doi(ref.id, dest)
    if ref.kind == "repo":
        return Found("code", ref.id, None, ref.id, ["Cloned when the agent is built."])
    return Found("none", ref.text)


def from_upload(path: Path, dest: Path) -> Found:
    """A file a person uploaded: a paper, a crate (zip or metadata file), or data."""
    path, dest = Path(path), Path(dest)
    suffix = path.suffix.lower()
    if path.name == "ro-crate-metadata.json":
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest / path.name)
        return Found("crate", "RO-Crate (metadata only)", dest, notes=["Only the metadata file: files it refers to "
                                                                      "are missing unless uploaded as a zip."])
    if suffix == ".zip":
        out = safe_unzip(path, dest / path.stem)
        root = crate_root(out)
        if root:
            name = json.loads((root / "ro-crate-metadata.json").read_text(encoding="utf-8"))
            title = next((e.get("name") for e in name.get("@graph", []) if e.get("@id") == "./"), None)
            return Found("crate", title or path.stem, root)
        return Found("data", path.stem, out)
    if suffix in PAPER_SUFFIXES:
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest / path.name)
        return Found("paper", path.stem, dest / path.name)
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dest / path.name)
    return Found("data", path.name, dest)
