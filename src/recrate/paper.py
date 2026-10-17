"""The paper itself: text, links, and search, so the agent can answer questions about it.

Paper2Agent exposes the manuscript as an MCP resource; ReCRATE does the same and
adds ``search`` so a chat agent can pull the relevant passages (methods,
parameters, reported numbers) without loading the whole text.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>,;]+[^\s\"<>,;.)\]])", re.I)
REPO_RE = re.compile(
    r"https?://(?:www\.)?(?:github\.com|gitlab\.com|bitbucket\.org|codeberg\.org)/[\w.-]+/[\w.-]+"
    r"|https?://(?:doi\.org/10\.5281/zenodo\.\d+|zenodo\.org/(?:records?|doi)/[\w./-]+)"
    r"|https?://workflowhub\.eu/workflows/\d+"
    r"|https?://huggingface\.co/(?:datasets/|spaces/)?[\w.-]+/[\w.-]+"
    r"|https?://(?:www\.)?(?:figshare\.com|datadryad\.org|osf\.io|physionet\.org|kaggle\.com)/[\w./-]+",
    re.I,
)


@dataclass
class Paper:
    title: str
    text: str
    source: str
    dois: list[str] = field(default_factory=list)
    repos: list[str] = field(default_factory=list)

    @property
    def doi(self) -> str | None:
        return self.dois[0] if self.dois else None

    def to_dict(self) -> dict:
        return {"title": self.title, "source": self.source, "doi": self.doi, "dois": self.dois[:10],
                "code_and_data_links": self.repos[:20], "characters": len(self.text)}


def extract_text(path: Path) -> str:
    path = Path(path)
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)
    return path.read_text(encoding="utf-8", errors="replace")


_JUNK = ("article", "http", "doi", "received", "accepted", "published", "open access", "check for updates")


def _is_title_line(s: str) -> bool:
    low = s.lower()
    return (12 <= len(s) <= 200 and not low.startswith(_JUNK) and "www." not in low and "|" not in s
            and "@" not in s and not s[:1].isdigit())


def _guess_title(text: str) -> str:
    lines = [ln.strip().lstrip("#").strip() for ln in text.splitlines()]
    for i, s in enumerate(lines[:80]):
        if _is_title_line(s):
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            # titles often wrap: join a short continuation line that does not end a sentence
            if nxt and _is_title_line(nxt) and len(s) + len(nxt) < 160 and not s.endswith((".", ":")) \
                    and len(nxt.split()) <= 8:
                return f"{s} {nxt}"
            return s
    return "Untitled paper"


def _pdf_title(path: Path) -> str | None:
    try:
        from pypdf import PdfReader

        title = (PdfReader(str(path)).metadata or {}).get("/Title")
    except Exception:  # noqa: BLE001 - metadata is optional
        return None
    return str(title).strip() if title and len(str(title).strip()) > 10 else None


def _rank_repos(repos: list[str], text: str) -> list[str]:
    """The paper's own code first: links in a code availability section, then most mentioned."""
    low = text.lower()
    sections = [m.end() for m in re.finditer(r"code availab|data and code availab|software availab", low)]

    def score(url: str) -> float:
        tail = url.split("://", 1)[-1].lower()
        name = tail.split("/", 1)[-1]
        s = low.count(name)
        positions = [m.start() for m in re.finditer(re.escape(name[:40]), low)]
        if any(0 <= pos - sec < 600 for pos in positions for sec in sections):
            s += 10
        return s

    return sorted(repos, key=lambda u: -score(u))


def _dedupe(items: list[str]) -> list[str]:
    """Drop near-duplicates caused by line breaks (extra '-' or a trailing fragment)."""
    out: list[str] = []
    for it in items:
        key = it.replace("-", "").lower()
        norm = [o.replace("-", "").lower() for o in out]
        if any(key == k or (key.startswith(k) and len(key) - len(k) < 16) for k in norm):
            continue
        out.append(it)
    return out


def read_paper(path: str | Path) -> Paper:
    path = Path(path)
    text = extract_text(path)
    flat = re.sub(r"-\n(?=[a-z])", "", text)  # undo hyphenation at line breaks
    flat = re.sub(r"(?<=[/._-])\n", "", flat)  # undo URLs broken across lines
    dois = _dedupe(list(dict.fromkeys(m.group(1).rstrip(".") for m in DOI_RE.finditer(flat))))
    repos = _dedupe(list(dict.fromkeys(m.group(0).rstrip(".)") for m in REPO_RE.finditer(flat))))
    repos = _rank_repos(repos, flat)
    title = (_pdf_title(path) if path.suffix.lower() == ".pdf" else None) or _guess_title(text)
    return Paper(title=title, text=text, source=str(path), dois=dois, repos=repos)


def passages(text: str, size: int = 900) -> list[str]:
    """Split text into paragraph-sized passages."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    out, buf = [], ""
    for p in paras:
        if len(buf) + len(p) > size and buf:
            out.append(buf)
            buf = ""
        buf = f"{buf}\n\n{p}" if buf else p
    if buf:
        out.append(buf)
    return out


def search(text: str, query: str, k: int = 5) -> list[dict]:
    """Keyword search over passages; returns the best k with a score."""
    terms = [t for t in re.findall(r"[\w.µ%-]+", query.lower()) if len(t) > 1]
    if not terms:
        return []
    scored = []
    for i, p in enumerate(passages(text)):
        low = p.lower()
        score = sum(low.count(t) for t in terms) + 3 * sum(1 for t in set(terms) if t in low)
        if score:
            scored.append({"passage": i, "score": score, "text": p})
    return sorted(scored, key=lambda d: -d["score"])[:k]


def save_paper(paper: Paper, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "paper.md").write_text(paper.text, encoding="utf-8")
    (out_dir / "paper.json").write_text(json.dumps(paper.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")


def load_saved_paper(paper_dir: Path) -> Paper | None:
    md, meta = paper_dir / "paper.md", paper_dir / "paper.json"
    if not md.exists():
        return None
    info = json.loads(meta.read_text(encoding="utf-8")) if meta.exists() else {}
    return Paper(title=info.get("title", "Paper"), text=md.read_text(encoding="utf-8"),
                 source=info.get("source", str(md)), dois=info.get("dois", []),
                 repos=info.get("code_and_data_links", []))
