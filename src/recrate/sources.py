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
# bioRxiv / medRxiv: the URL carries the DOI and a version ("…26350296v2"); the version is not part of the DOI
PREPRINT = re.compile(r"(?:www\.)?(biorxiv|medrxiv)\.org/content/(10\.\d{4,9}/[\d.]*\d)(v\d+)?(?=[./?#]|$)", re.I)
REPO = re.compile(r"https?://(?:github\.com|gitlab\.com|codeberg\.org)/[\w.-]+/[\w.-]+", re.I)


@dataclass
class Ref:
    kind: str  # workflowhub | zenodo | arxiv | preprint | doi | repo
    id: str
    version: str | None = None
    text: str = ""

    @property
    def label(self) -> str:
        return {"workflowhub": f"WorkflowHub workflow {self.id}" + (f" v{self.version}" if self.version else ""),
                "zenodo": f"Zenodo record {self.id}", "arxiv": f"arXiv {self.id}", "doi": f"DOI {self.id}",
                "preprint": f"preprint {self.id}" + (f" {self.version}" if self.version else ""),
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
    for m in PREPRINT.finditer(text):  # before DOI: the same text also matches as a DOI, with the version stuck on
        add(m, Ref("preprint", m.group(2), (m.group(3) or "").lower() or None, m.group(1).lower()))
    for m in ARXIV.finditer(text):
        add(m, Ref("arxiv", m.group(1), None, m.group(0)))
    for m in DOI.finditer(text):
        if m.group(1).lower().startswith("10.48550/arxiv."):
            add(m, Ref("arxiv", m.group(1).split("arXiv.", 1)[-1].split("arxiv.", 1)[-1], None, m.group(0)))
        else:  # a version suffix ("…v2", as preprint servers add) is not part of a DOI
            add(m, Ref("doi", re.sub(r"(?<=\d)v\d+$", "", m.group(1)), None, m.group(0)))
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


def get_bytes(url: str) -> bytes:
    return _get(url, timeout=120)[0]


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


def fetch_preprint(server: str, doi: str, version: str | None, dest: Path) -> Found:
    """A bioRxiv or medRxiv preprint: its structured text from Europe PMC when available, else the full-text PDF of
    the version linked (or the latest), with the title from its DOI."""
    dest = Path(dest)
    try:
        found = fetch_doi(doi, dest)
        if found.path and found.path.suffix == ".pdf" or (found.notes and "Europe PMC" in found.notes[0]):
            return found
    except Exception:  # noqa: BLE001 - the DOI's metadata is optional: the PDF is what matters
        found = None
    dest.mkdir(parents=True, exist_ok=True)
    url = f"https://www.{server}.org/content/{doi}{version or ''}.full.pdf"
    data, _ = _get(url)
    if data[:4] != b"%PDF":
        raise ValueError(f"{server} did not return a PDF for {doi}{version or ''}")
    p = dest / "paper.pdf"
    p.write_bytes(data)
    title = found.label if found else doi
    return Found("paper", title, p, f"https://doi.org/{doi}",
                 [f"Full text: the {server} preprint PDF{' (' + version + ')' if version else ''}."])


def fetch_doi(doi: str, dest: Path) -> Found:
    """A paper DOI: its reading package when it is open access in Europe PMC (text, tables, figures,
    supplement); else the full text when the publisher links an open PDF; else its metadata and abstract."""
    from .reading import from_europepmc

    try:
        pkg = from_europepmc(doi, Path(dest) / "package", get_json, get_bytes)
    except Exception:  # noqa: BLE001 - Europe PMC unavailable: fall back to the publisher
        pkg = None
    if pkg:
        meta = json.loads((pkg.parent / "package.json").read_text(encoding="utf-8"))
        n_t, n_f = len(meta.get("tables", [])), len([f for f in meta.get("figures", []) if f.get("file")])
        return Found("paper", meta.get("title") or doi, pkg, f"https://doi.org/{doi}",
                     [f"Full text from Europe PMC, with {n_t} table(s) and {n_f} figure(s)."])
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
    if ref.kind == "preprint":
        return fetch_preprint(ref.text or "biorxiv", ref.id, ref.version, dest)
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


# ---- datasets to reuse a method on ------------------------------------------------------------------------

DATASET_LIMIT = 2 * 2 ** 30  # 2 GB: larger data is read in place, from a folder, not downloaded


def _download(url: str, dest: Path, limit: int = DATASET_LIMIT, progress=None) -> Path:
    """Stream ``url`` to ``dest`` (never more than ``limit`` bytes)."""
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=120, context=_ssl_context()) as r, dest.open("wb") as fh:
        total = int(r.headers.get("Content-Length") or 0)
        if total > limit:
            raise ValueError(f"{url} is {total / 2**30:.1f} GB, over the {limit / 2**30:.0f} GB limit: download it "
                             "yourself and point ReCRATE at the folder.")
        done = 0
        while chunk := r.read(1 << 20):
            done += len(chunk)
            if done > limit:
                raise ValueError(f"{url} is over the {limit / 2**30:.0f} GB limit.")
            fh.write(chunk)
            if progress:
                progress(done, total)
    return dest


def dataset_root(folder: Path) -> Path:
    """The folder a method should read: the one with the most images (or with images/ and masks/ inside)."""
    from collections import Counter

    exts = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".csv", ".tsv", ".nii", ".gz", ".h5", ".zarr"}
    counts = Counter(p.parent for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in exts
                     and "__MACOSX" not in p.parts)
    if not counts:
        return folder
    named = [d for d in counts if d.name.lower() in {"images", "imgs", "raw"}]
    if named:  # images/ (with masks/ beside it): the method reads the folder that holds both
        return max(named, key=lambda d: counts[d]).parent
    return counts.most_common(1)[0][0]


def fetch_dataset(refs: list[str], dest: Path, progress=None) -> Path:
    """Fetch the data to reuse a method on: Zenodo records (by DOI or link) or direct links to files or zips.
    Zips are unpacked (safely) side by side, so ``images.zip`` and ``masks.zip`` give ``images/`` and ``masks/``.
    Returns the folder to run on. Data behind a login is never fetched: give a folder instead.
    Fetching the same links into the same folder again downloads nothing."""
    dest = Path(dest)
    refs = [r.strip() for r in refs if r.strip()]
    marker = dest / ".recrate-fetched"
    if marker.exists() and marker.read_text(encoding="utf-8").splitlines() == refs:
        return dataset_root(dest)
    dest.mkdir(parents=True, exist_ok=True)
    downloads = dest / ".downloads"
    downloads.mkdir(exist_ok=True)
    for ref in refs:
        ids = identify(ref)
        files: list[tuple[str, str]] = []
        if ids and ids[0].kind == "zenodo":
            rec = _json(f"https://zenodo.org/api/records/{ids[0].id}")
            files = [(f["key"], f["links"]["self"]) for f in rec.get("files", [])]
        else:
            files = [(Path(urllib.parse.urlparse(ref).path).name or "data", ref)]
        for name, url in files:
            target = _download(url, downloads / name, progress=progress)
            if zipfile.is_zipfile(target):
                safe_unzip(target, dest)
            else:
                shutil.copy2(target, dest / name)
    shutil.rmtree(downloads, ignore_errors=True)
    shutil.rmtree(dest / "__MACOSX", ignore_errors=True)
    marker.write_text("\n".join(refs), encoding="utf-8")
    return dataset_root(dest)
