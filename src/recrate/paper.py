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


def _clean_link(link: str) -> str:
    """Drop what PDF text glues onto the end of a DOI or URL: a following word ("…26350296doi:", "….August"),
    a reference number ("…/rplos.20" from "rplos.\n20.") or a trailing full stop."""
    link = link.rstrip(".")
    link = re.sub(r"(?<=\d)(?:doi|https?|available|retrieved|accessed):?$", "", link, flags=re.I)
    link = re.sub(r"\.(?:January|February|March|April|May|June|July|August|September|October|November|December|"
                  r"[A-Z][a-z]{2,})$", "", link)
    if re.search(r"github\.com|gitlab\.com|codeberg\.org|bitbucket\.org", link):
        link = re.sub(r"\.\d{1,3}$", "", link)  # repository names don't end in a reference number
    return link.rstrip(".")


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
    # undo URLs broken across lines, but not into the next reference's number ("…10620146.\n31. Harris…")
    flat = re.sub(r"(?<=[/._-])\n(?!\s*\d{1,3}\.\s)", "", flat)
    dois = _dedupe(list(dict.fromkeys(_clean_link(m.group(1)) for m in DOI_RE.finditer(flat))))
    repos = _dedupe(list(dict.fromkeys(_clean_link(m.group(0).rstrip(".)")) for m in REPO_RE.finditer(flat))))
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


STOPWORDS = set("""a an and are as at be been by can could did do does for from had has have how in into is it its
of on or our that the their them then there these they this to was were what when where which who why will with would
you your paper method methods study""".split())


def _stem(word: str) -> str:
    """A crude stem, enough for a paper's vocabulary: 'excluded', 'exclusion', 'excluding' all match 'exclu'."""
    return word[:5] if len(word) >= 6 else word


def search(text: str, query: str, k: int = 5) -> list[dict]:
    """Keyword search over passages, weighting rare words more (IDF) and matching word stems; best k with a score."""
    import math

    terms = list(dict.fromkeys(_stem(t) for t in re.findall(r"[\w.µ%-]+", query.lower())
                               if len(t) > 1 and t not in STOPWORDS))
    if not terms:
        return []
    ps = passages(text)
    pats = {t: re.compile(rf"(?<![\w]){re.escape(t)}") for t in terms}
    counts = [{t: len(pats[t].findall(p.lower())) for t in terms} for p in ps]
    idf = {t: math.log((len(ps) + 1) / (1 + sum(1 for c in counts if c[t]))) + 1 for t in terms}
    scored = []
    for i, (p, c) in enumerate(zip(ps, counts)):
        score = sum(idf[t] * (1 + math.log(c[t])) for t in terms if c[t])
        if score:
            scored.append({"passage": i, "score": round(score, 3), "text": p})
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
