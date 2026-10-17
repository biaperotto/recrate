"""The software environment of a piece of research, as it was when it was published.

A paper rarely pins every version. To recreate it faithfully, each piece of software gets a version and a
record of how that version was determined, most reliable first:

1. **stated**: the paper gives it ("scikit-image 0.19.2", "Fiji (ImageJ 1.53t)");
2. **pinned**: the code repository pins it (``requirements.txt``, ``environment.yml``, ``pyproject.toml``);
3. **as of publication**: the latest release on or before the publication date, from the software's
   registry (PyPI, conda-forge or GitHub releases);
4. **unknown**: the software has no release history ReCRATE can read.

The publication date comes from the DOI's metadata, the paper's text ("Published: 12 March 2021"), an
arXiv identifier, or the PDF's metadata, in that order, and its source is recorded too.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable

# name as written in papers -> (registry, package id). Registries: pypi, conda (conda-forge), github, none.
CATALOG: dict[str, tuple[str, str]] = {
    "python": ("conda", "python"), "numpy": ("pypi", "numpy"), "scipy": ("pypi", "scipy"),
    "pandas": ("pypi", "pandas"), "matplotlib": ("pypi", "matplotlib"), "scikit-image": ("pypi", "scikit-image"),
    "skimage": ("pypi", "scikit-image"), "scikit-learn": ("pypi", "scikit-learn"), "sklearn": ("pypi", "scikit-learn"),
    "opencv": ("pypi", "opencv-python"), "tifffile": ("pypi", "tifffile"), "napari": ("pypi", "napari"),
    "cellpose": ("pypi", "cellpose"), "stardist": ("pypi", "stardist"), "tensorflow": ("pypi", "tensorflow"),
    "pytorch": ("pypi", "torch"), "torch": ("pypi", "torch"), "keras": ("pypi", "keras"),
    "cellprofiler": ("github", "CellProfiler/CellProfiler"), "ilastik": ("github", "ilastik/ilastik"),
    "qupath": ("github", "qupath/qupath"), "imagej": ("none", "ImageJ"), "fiji": ("none", "Fiji"),
    "trackmate": ("github", "trackmate-sc/TrackMate"), "bioformats": ("github", "ome/bioformats"),
    "bio-formats": ("github", "ome/bioformats"), "omero": ("pypi", "omero-py"), "galaxy": ("github", "galaxyproject/galaxy"),
    "nextflow": ("github", "nextflow-io/nextflow"), "snakemake": ("pypi", "snakemake"), "r": ("conda", "r-base"),
    "seurat": ("conda", "r-seurat"), "scanpy": ("pypi", "scanpy"), "anndata": ("pypi", "anndata"),
    "samtools": ("conda", "samtools"), "bwa": ("conda", "bwa"), "star": ("conda", "star"), "gatk": ("conda", "gatk4"),
    "deseq2": ("conda", "bioconductor-deseq2"), "jupyter": ("pypi", "jupyter"), "xarray": ("pypi", "xarray"),
    "statsmodels": ("pypi", "statsmodels"), "seaborn": ("pypi", "seaborn"), "biopython": ("pypi", "biopython"),
    # machine learning and medical imaging
    "monai": ("pypi", "monai"), "nnu-net": ("pypi", "nnunetv2"), "nnunet": ("pypi", "nnunetv2"),
    "totalsegmentator": ("pypi", "TotalSegmentator"), "simpleitk": ("pypi", "SimpleITK"), "itk": ("pypi", "itk"),
    "nibabel": ("pypi", "nibabel"), "pydicom": ("pypi", "pydicom"), "3d slicer": ("github", "Slicer/Slicer"),
    "transformers": ("pypi", "transformers"), "hugging face transformers": ("pypi", "transformers"),
    "segment anything": ("pypi", "segment-anything"), "jax": ("pypi", "jax"), "xgboost": ("pypi", "xgboost"),
    "lightgbm": ("pypi", "lightgbm"), "pytorch lightning": ("pypi", "lightning"), "timm": ("pypi", "timm"),
    "albumentations": ("pypi", "albumentations"), "huggingface datasets": ("pypi", "datasets"),
    # models are named, not released as versions: recorded so the environment shows them
    "gpt-4o": ("none", "GPT-4o"), "gpt-4": ("none", "GPT-4"), "gpt-3.5": ("none", "GPT-3.5"),
    "chatgpt": ("none", "ChatGPT"), "claude": ("none", "Claude"), "llama": ("none", "Llama"),
    "bert": ("none", "BERT"), "u-net": ("none", "U-Net"),
}
DISPLAY = {"skimage": "scikit-image", "sklearn": "scikit-learn", "torch": "PyTorch", "pytorch": "PyTorch",
           "imagej": "ImageJ", "fiji": "Fiji", "r": "R", "opencv": "OpenCV", "bio-formats": "Bio-Formats",
           "bioformats": "Bio-Formats", "cellprofiler": "CellProfiler", "qupath": "QuPath", "trackmate": "TrackMate",
           "star": "STAR", "bwa": "BWA", "gatk": "GATK", "deseq2": "DESeq2", "python": "Python", "galaxy": "Galaxy",
           "monai": "MONAI", "nnu-net": "nnU-Net", "nnunet": "nnU-Net", "totalsegmentator": "TotalSegmentator",
           "simpleitk": "SimpleITK", "itk": "ITK", "3d slicer": "3D Slicer", "gpt-4o": "GPT-4o", "gpt-4": "GPT-4",
           "gpt-3.5": "GPT-3.5", "chatgpt": "ChatGPT", "claude": "Claude", "llama": "Llama", "bert": "BERT",
           "u-net": "U-Net", "segment anything": "Segment Anything", "pytorch lightning": "PyTorch Lightning",
           "transformers": "transformers", "jax": "JAX", "xgboost": "XGBoost", "lightgbm": "LightGBM"}
VERSION = r"v?(\d+(?:\.\d+){0,3}[a-z]?\d*)"
MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"


@dataclass
class Software:
    name: str
    version: str | None
    how: str  # declared | stated | pinned | as of publication | later | history | unknown
    registry: str | None = None
    package: str | None = None
    released: str | None = None  # release date of that version, when known
    evidence: str = ""  # the sentence or file line it came from
    url: str | None = None

    @property
    def note(self) -> str:
        return {"declared": "declared in the RO-Crate", "stated": "stated in the paper", "pinned": "pinned in the code repository",
                "as of publication": f"latest release before publication ({self.released})" if self.released
                else "latest release before publication",
                "later": f"no release before publication; first release {self.released} ({self.version})",
                "history": f"the registry's records start {self.released}, after the publication: version unknown",
                "unknown": "a model, named without a version" if self.registry == "none" and self.package and
                self.package[0].isupper() and self.package not in {"Fiji", "ImageJ"} else "no release history available"}[self.how]


@dataclass
class Environment:
    published: str | None
    published_source: str
    software: list[Software] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"published": self.published, "published_source": self.published_source,
                "software": [asdict(s) for s in self.software]}

    def requirements_txt(self) -> str:
        lines = [f"# Python packages as of {self.published or 'unknown date'}, recreated by ReCRATE"]
        for s in self.software:
            if s.registry == "pypi" and s.version and s.how != "later":
                lines.append(f"{s.package}=={s.version}  # {s.note}")
        return "\n".join(lines) + "\n"


# ---- publication date ------------------------------------------------------------------------------

def _parse_date(text: str) -> date | None:
    for fmt in ("%d %B %Y", "%B %d, %Y", "%B %d %Y", "%Y-%m-%d", "%d %b %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(text.strip().rstrip("."), fmt).date()
        except ValueError:
            continue
    return None


def date_from_text(text: str) -> date | None:
    """'Published: 12 March 2021', 'Published online March 12, 2021', 'Accepted ... Published ...'."""
    pat = re.compile(rf"(?:published|publication date|available online)[^\n\d]{{0,30}}?"
                     rf"(\d{{1,2}} (?:{MONTHS}) \d{{4}}|(?:{MONTHS}) \d{{1,2}},? \d{{4}}|\d{{4}}-\d{{2}}-\d{{2}})", re.I)
    m = pat.search(text[:20000])
    return _parse_date(m.group(1)) if m else None


def date_from_arxiv(aid: str) -> date | None:
    m = re.match(r"(\d{2})(\d{2})\.", aid)
    return date(2000 + int(m.group(1)), int(m.group(2)), 1) if m else None


def date_from_csl(meta: dict) -> date | None:
    for key in ("published-online", "published-print", "issued", "published"):
        parts = ((meta.get(key) or {}).get("date-parts") or [[None]])[0]
        if parts and parts[0]:
            y, m, d = (list(parts) + [1, 1])[:3]
            return date(int(y), int(m or 1), int(d or 1))
    return None


def publication_date(text: str = "", doi: str | None = None, arxiv: str | None = None, pdf: Path | None = None,
                     fetch_json: Callable[[str, str], dict] | None = None) -> tuple[date | None, str]:
    if doi and fetch_json:
        try:
            d = date_from_csl(fetch_json(f"https://doi.org/{doi}", "application/vnd.citationstyles.csl+json"))
            if d:
                return d, f"DOI metadata (doi:{doi})"
        except Exception:  # noqa: BLE001 - fall through to the other sources
            pass
    d = date_from_text(text)
    if d:
        return d, "the paper's text"
    if arxiv and date_from_arxiv(arxiv):
        return date_from_arxiv(arxiv), f"arXiv identifier {arxiv} (month)"
    if pdf and Path(pdf).suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader

            raw = str((PdfReader(str(pdf)).metadata or {}).get("/CreationDate", ""))
            m = re.search(r"(\d{4})(\d{2})(\d{2})", raw)
            if m:
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3))), "the PDF's creation date"
        except Exception:  # noqa: BLE001
            pass
    return None, "unknown"


# ---- software mentioned ----------------------------------------------------------------------------

def body_text(text: str) -> str:
    """The paper without its reference list: a cited title ("TotalSegmentator: robust segmentation...") is not
    software the paper used."""
    m = None
    for m in re.finditer(r"(?im)^\s*(?:#+\s*)?(?:\d+\.?\s*)?(references|bibliography|works cited)\s*$", text):
        pass
    return text[:m.start()] if m and m.start() > len(text) * 0.3 else text


def detect_software(text: str) -> list[Software]:
    """Software the paper names (outside its reference list), with the version when the paper states one."""
    flat = " ".join(body_text(text).split())
    found: dict[str, Software] = {}
    names = sorted(CATALOG, key=len, reverse=True)
    for name in names:
        word = re.escape(name).replace(r"\-", r"[- ]?")
        for m in re.finditer(rf"(?<![\w-]){word}(?![\w-])", flat, re.I):
            if name == "r" and not re.match(r"R(?= (?:version |v)?\d| \(|,? ?(?:package|software|statistical))", flat[m.start():m.start() + 30]):
                continue  # "R" alone is too ambiguous
            if name in {"star", "galaxy", "keras", "itk", "llama", "bert", "claude", "jax"} and not flat[m.start()].isupper():
                continue
            after = flat[m.end():m.end() + 40]
            v = re.match(rf"\s*(?:\(|,)?\s*(?:version|ver\.|v)?\s*{VERSION}", after, re.I)
            reg, pkg = CATALOG[name]
            key = pkg
            sent = flat[max(0, flat.rfind(". ", 0, m.start()) + 2):flat.find(". ", m.end()) + 1 or None][:300]
            version = v.group(1) if v and re.search(r"\d\.\d|\d{2,}", v.group(1)) else None
            if key not in found or (version and not found[key].version):
                found[key] = Software(DISPLAY.get(name, name if name.islower() and len(name) > 3 else name),
                                      version, "stated" if version else "unknown", reg, pkg, evidence=sent)
        # ImageJ versions are often written inside the Fiji mention: "Fiji (ImageJ 1.53t)"
    return list(found.values())


REQ_LINE = re.compile(r"^\s*([A-Za-z0-9_.\-]+)\s*(?:\[[^\]]*\])?\s*(==|>=|~=|=)?\s*([0-9][\w.\-]*)?")


def detect_from_repo(code_dir: Path) -> list[Software]:
    """Dependencies declared by the code: requirements*.txt, environment.yml, pyproject.toml."""
    out: list[Software] = []
    code_dir = Path(code_dir)
    files = sorted(code_dir.glob("requirements*.txt")) + sorted(code_dir.glob("environment*.y*ml"))
    for f in files:
        for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.split("#")[0].strip().lstrip("- ").strip()
            if not line or line.startswith(("-", "name:", "channels:", "dependencies:", "pip:")) or ":" in line:
                continue
            m = REQ_LINE.match(line)
            if m:
                pinned = m.group(2) in {"==", "="} and m.group(3)
                out.append(Software(m.group(1), m.group(3) if pinned else None, "pinned" if pinned else "unknown",
                                    "pypi" if f.suffix == ".txt" else "conda", m.group(1).lower(),
                                    evidence=f"{f.name}: {line}"))
    pp = code_dir / "pyproject.toml"
    if pp.exists():
        deps = re.search(r"dependencies\s*=\s*\[(.*?)\]", pp.read_text(encoding="utf-8", errors="ignore"), re.S)
        for d in re.findall(r"\"([^\"]+)\"", deps.group(1) if deps else ""):
            m = REQ_LINE.match(d)
            if m:
                pinned = m.group(2) == "==" and m.group(3)
                out.append(Software(m.group(1), m.group(3) if pinned else None, "pinned" if pinned else "unknown",
                                    "pypi", m.group(1).lower(), evidence=f"pyproject.toml: {d}"))
    return out


# ---- release histories ------------------------------------------------------------------------------

def _is_release(v: str) -> bool:
    """A final release: digits and dots, not a placeholder such as 0.0.0."""
    return bool(re.fullmatch(r"v?\d+(\.\d+)*", v)) and any(int(x) for x in re.findall(r"\d+", v))


def releases(registry: str, package: str, fetch_json: Callable[[str, str], dict]) -> list[tuple[str, date]]:
    """(version, release date) for every final release, oldest first."""
    out: list[tuple[str, date]] = []
    if registry == "pypi":
        data = fetch_json(f"https://pypi.org/pypi/{package}/json", "application/json")
        for v, files in (data.get("releases") or {}).items():
            times = [f.get("upload_time_iso_8601") or f.get("upload_time") for f in files if not f.get("yanked")]
            if times and _is_release(v):
                out.append((v, datetime.fromisoformat(min(times).replace("Z", "+00:00")).date()))
    elif registry == "conda":
        data: dict = {}
        for channel in ("conda-forge", "bioconda"):
            try:
                data = fetch_json(f"https://api.anaconda.org/package/{channel}/{package}", "application/json")
            except Exception:  # noqa: BLE001 - not on this channel
                continue
            if data.get("files"):
                break
        seen: dict[str, date] = {}
        for f in data.get("files") or []:
            parts = str(f.get("full_name", "")).split("/")  # channel/package/version/platform/file
            v, t = (f.get("version") or (parts[2] if len(parts) > 2 else None)), f.get("upload_time")
            if v and t and _is_release(v):
                d = datetime.fromisoformat(str(t).replace(" ", "T")).date()
                seen[v] = min(d, seen.get(v, d))
        out = list(seen.items())
    elif registry == "github":
        for page in (1, 2, 3):
            data = fetch_json(f"https://api.github.com/repos/{package}/releases?per_page=100&page={page}",
                              "application/vnd.github+json")
            rows = data if isinstance(data, list) else []
            for r in rows:
                v = (r.get("tag_name") or "").lstrip("v")
                if r.get("draft") or r.get("prerelease") or not _is_release(v) or not r.get("published_at"):
                    continue
                out.append((v, datetime.fromisoformat(r["published_at"].replace("Z", "+00:00")).date()))
            if len(rows) < 100:
                break
    return sorted(out, key=lambda x: x[1])


def _vkey(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v))


def version_as_of(registry: str, package: str, when: date, fetch_json) -> tuple[str, date] | None:
    """The highest version released on or before ``when`` (projects maintain several lines at once:
    a 3.9 patch can come out after 3.10.0, and 3.10 is still the newest available)."""
    rel = [r for r in releases(registry, package, fetch_json) if r[1] <= when]
    return max(rel, key=lambda r: _vkey(r[0])) if rel else None


def registry_url(registry: str | None, package: str | None) -> str | None:
    return {"pypi": f"https://pypi.org/project/{package}/", "conda": f"https://anaconda.org/conda-forge/{package}",
            "github": f"https://github.com/{package}/releases"}.get(registry or "") if package else None


def resolve(software: list[Software], when: date | None, fetch_json=None) -> list[Software]:
    """Fill each missing version with the latest release on or before ``when``."""
    for s in software:
        s.url = registry_url(s.registry, s.package)
        if s.version or not when or not fetch_json or s.registry in (None, "none"):
            if not s.version:
                s.how = "unknown"
            continue
        try:
            history = releases(s.registry, s.package, fetch_json)
        except Exception:  # noqa: BLE001 - registry unreachable: leave it unknown, say so
            history = []
        before = [r for r in history if r[1] <= when]
        if before:
            hit = max(before, key=lambda r: _vkey(r[0]))
            s.version, s.released, s.how = hit[0], hit[1].isoformat(), "as of publication"
        elif history and s.registry == "conda":  # conda-forge (2016-) and Bioconda (2015-) started late: their
            s.released, s.how = history[0][1].isoformat(), "history"  # first record is not the first release
        elif history:  # the method was published before its software was released
            s.version, s.released, s.how = history[0][0], history[0][1].isoformat(), "later"
        else:
            s.how = "unknown"
    return software


def environment_for(text: str, when: date | None, when_source: str, code_dir: Path | None = None,
                    fetch_json=None) -> Environment:
    sw = detect_software(text)
    if code_dir and Path(code_dir).exists():
        have = {s.package for s in sw}
        for s in detect_from_repo(code_dir):
            match = next((x for x in sw if x.package == s.package), None)
            if match and s.version and not match.version:
                match.version, match.how, match.evidence = s.version, "pinned", s.evidence
            elif s.package not in have:
                sw.append(s)
    return Environment(when.isoformat() if when else None, when_source, resolve(sw, when, fetch_json))


def save(env: Environment, path: Path) -> None:
    Path(path).write_text(json.dumps(env.to_dict(), indent=2), encoding="utf-8")


def load(path: Path) -> Environment | None:
    p = Path(path)
    if not p.exists():
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    return Environment(d.get("published"), d.get("published_source", ""), [Software(**s) for s in d.get("software", [])])


def from_crate(crate_dir: Path) -> Environment:
    """The environment a crate declares: its software with versions, and its publication date."""
    meta = json.loads((Path(crate_dir) / "ro-crate-metadata.json").read_text(encoding="utf-8"))
    graph = meta.get("@graph", [])
    root = next((e for e in graph if e.get("@id") == "./"), {})
    sw, seen = [], set()
    for e in graph:
        if e.get("@type") == "SoftwareApplication" and e.get("name") and e["name"] not in seen:
            seen.add(e["name"])
            if e["name"] == "ReCRATE":
                continue
            sw.append(Software(e["name"], e.get("softwareVersion"), "declared" if e.get("softwareVersion") else "unknown",
                               url=e.get("url")))
    published = str(root.get("datePublished") or "")[:10] or None
    return Environment(published, "the RO-Crate" if published else "unknown", sw)
