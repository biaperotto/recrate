"""M0 — get a paper from a link.

Accepts a DOI, a landing-page URL, an arXiv or bioRxiv link, or a local file,
resolves it to a DOI and a landing page, and downloads the full text where it is
openly available (the ``citation_pdf_url`` the page advertises, or the page HTML).
The downloaded file is then handed to :func:`recrate.paper.read_paper`, so all of
ReCRATE's existing title/DOI/code-link extraction is reused.

While reading the landing page it also collects FAIR **Signposting** links
(``describedby`` / ``item`` / ``cite-as``), which M1 (:mod:`recrate.locate`) uses
to find an RO-Crate. Nothing here decides a diagnosis; it only gathers material.

All network access goes through :mod:`recrate.http`, so tests patch one function.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from . import http
from .paper import Paper, read_paper

DOI_IN_URL = re.compile(r"(10\.\d{4,9}/[^\s?#]+)", re.I)
ARXIV_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/([\w.\-/]+?)(?:\.pdf)?$|arxiv:\s*([\w.\-/]+)", re.I)
BIORXIV_RE = re.compile(r"(biorxiv|medrxiv)\.org/content/", re.I)
SIGNPOST_RELS = {"describedby", "item", "cite-as"}
MIN_FULLTEXT_CHARS = 3000


@dataclass
class FetchResult:
    paper: Paper | None
    doi: str | None
    landing_url: str | None
    full_text: bool
    kind: str                       # local | doi | arxiv | biorxiv | url
    signposting: list[dict] = field(default_factory=list)
    pdf_url: str | None = None
    message: str = ""


# ---- link classification --------------------------------------------------

def classify_link(link: str) -> tuple[str, str]:
    """Return ``(kind, value)`` for a paper link.

    kind is one of local | doi | arxiv | biorxiv | url; value is the local path,
    the DOI, the arXiv id, or the URL.
    """
    if Path(link).expanduser().is_file():
        return "local", str(Path(link).expanduser())
    low = link.strip()
    m = ARXIV_RE.search(low)
    if m:
        return "arxiv", (m.group(1) or m.group(2)).strip("/")
    if BIORXIV_RE.search(low):
        return "biorxiv", low
    if low.lower().startswith(("10.",)) or "doi.org/" in low.lower():
        m = DOI_IN_URL.search(low)
        if m:
            return "doi", m.group(1).rstrip(".")
    if low.lower().startswith(("http://", "https://")):
        return "url", low
    # bare-looking DOI without the 10. guard, last resort
    m = DOI_IN_URL.search(low)
    if m:
        return "doi", m.group(1).rstrip(".")
    raise ValueError(f"Could not understand paper link {link!r}: expected a DOI, URL, arXiv/bioRxiv link, or file.")


# ---- HTML metadata --------------------------------------------------------

class _MetaParser(HTMLParser):
    """Collect <meta name=.. content=..> and <link rel=.. href=.. type=..>."""

    def __init__(self) -> None:
        super().__init__()
        self.metas: dict[str, str] = {}
        self.links: list[dict[str, str]] = []
        self._text: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        d = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            name = (d.get("name") or d.get("property") or "").lower()
            if name and "content" in d and name not in self.metas:
                self.metas[name] = d["content"]
        elif tag == "link":
            self.links.append({"rel": d.get("rel", "").lower(), "href": d.get("href", ""),
                               "type": d.get("type", "")})
        elif tag in ("script", "style"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip and data.strip():
            self._text.append(data)

    @property
    def text(self) -> str:
        return "\n".join(self._text)


def signposting_from(resp: http.Response, metas_links: _MetaParser) -> list[dict]:
    """Signposting links from HTTP Link headers and HTML <link> tags."""
    out: list[dict] = []
    for e in http.link_headers(resp):
        rel = e.get("rel", "").lower()
        if any(r in rel.split() for r in SIGNPOST_RELS):
            out.append({"url": e["url"], "rel": rel, "type": e.get("type", ""), "from": "link-header"})
    for lk in metas_links.links:
        rels = lk["rel"].split()
        if any(r in rels for r in SIGNPOST_RELS) and lk["href"]:
            out.append({"url": lk["href"], "rel": lk["rel"], "type": lk.get("type", ""), "from": "html-link"})
    return out


# ---- fetching -------------------------------------------------------------

def _landing_for(kind: str, value: str) -> tuple[str, str | None, str | None]:
    """Return (landing_url, doi, pdf_url) before fetching anything."""
    if kind == "doi":
        return f"https://doi.org/{value}", value, None
    if kind == "arxiv":
        return (f"https://arxiv.org/abs/{value}", f"10.48550/arXiv.{value}",
                f"https://arxiv.org/pdf/{value}.pdf")
    return value, None, None  # url / biorxiv: discover from the page


def _save(work_dir: Path, name: str, content: bytes) -> Path:
    work_dir.mkdir(parents=True, exist_ok=True)
    p = work_dir / name
    p.write_bytes(content)
    return p


def _save_body(work_dir: Path, content: bytes) -> Path:
    """Save a downloaded body under a name that matches what it actually is.

    A real PDF keeps ``.pdf`` (read_paper uses pypdf); HTML is stripped to text;
    anything else is saved as text. This stops pypdf from choking when a
    ``citation_pdf_url`` redirects to an HTML or text page.
    """
    head = content[:1024].lstrip()
    if content[:5] == b"%PDF-":
        return _save(work_dir, "paper.pdf", content)
    if head[:1] == b"<":
        parser = _MetaParser()
        parser.feed(content.decode("utf-8", errors="replace"))
        return _save(work_dir, "paper.txt", parser.text.encode("utf-8"))
    return _save(work_dir, "paper.txt", content)


def fetch_paper(link: str, work_dir: str | Path, pdf: str | Path | None = None,
                cache_dir: str | Path | None = http.CACHE_DIR) -> FetchResult:
    """Resolve ``link`` to a paper and its landing page. See the module docstring."""
    work_dir = Path(work_dir)
    kind, value = classify_link(link)

    if kind == "local":
        p = read_paper(value)
        return FetchResult(paper=p, doi=p.doi, landing_url=None, full_text=True, kind="local",
                           message=f"Read local paper {value}.")

    landing_url, doi, pdf_url = _landing_for(kind, value)

    # The landing page: metadata, signposting, and a pdf link if advertised.
    metas = _MetaParser()
    signposting: list[dict] = []
    try:
        resp = http.get(landing_url, accept="text/html,application/xhtml+xml", cache_dir=cache_dir)
        landing_url = resp.url
        if "html" in resp.header("content-type", "text/html") or resp.content[:512].lstrip().startswith(b"<"):
            metas.feed(resp.text)
        signposting = signposting_from(resp, metas)
        doi = doi or _doi_from_meta(metas)
        pdf_url = pdf_url or metas.metas.get("citation_pdf_url")
        landing_text = metas.text
    except http.HttpError as e:
        landing_text = ""
        resp = None
        message_fetch = str(e)
    else:
        message_fetch = ""

    # 1. an explicit --pdf wins.
    if pdf is not None:
        p = read_paper(pdf)
        return FetchResult(paper=p, doi=doi or p.doi, landing_url=landing_url, full_text=True, kind=kind,
                           signposting=signposting, pdf_url=pdf_url, message=f"Used supplied PDF {pdf}.")

    # 2. an open PDF advertised by the page (or arXiv's).
    if pdf_url:
        try:
            r = http.get(pdf_url, cache_dir=cache_dir)
            path = _save_body(work_dir, r.content)
            p = read_paper(path)
            if doi and doi not in p.dois:
                p.dois.insert(0, doi)
            return FetchResult(paper=p, doi=doi or p.doi, landing_url=landing_url, full_text=True, kind=kind,
                               signposting=signposting, pdf_url=pdf_url, message="Downloaded the open-access PDF.")
        except http.HttpError:
            pass  # fall through to metadata-only

    # 3. metadata / HTML only.
    if landing_text and len(landing_text) >= MIN_FULLTEXT_CHARS:
        path = _save(work_dir, "paper.txt", landing_text.encode("utf-8"))
        p = read_paper(path)
        title = metas.metas.get("citation_title") or metas.metas.get("og:title")
        if title:
            p.title = title.strip()
        if doi and doi not in p.dois:
            p.dois.insert(0, doi)
        return FetchResult(paper=p, doi=doi or p.doi, landing_url=landing_url, full_text=True, kind=kind,
                           signposting=signposting, pdf_url=pdf_url, message="Read the full text from the page HTML.")

    # Paywall or no body: metadata only.
    title = metas.metas.get("citation_title") or metas.metas.get("og:title") or "Untitled paper"
    p = Paper(title=title.strip(), text=landing_text, source=landing_url or link,
              dois=[doi] if doi else [])
    msg = (f"Full text not available{(' (' + message_fetch + ')') if message_fetch else ''}. "
           "Pass --pdf <file> to add it; continuing with metadata only.")
    return FetchResult(paper=p, doi=doi, landing_url=landing_url, full_text=False, kind=kind,
                       signposting=signposting, pdf_url=pdf_url, message=msg)


def _doi_from_meta(metas: _MetaParser) -> str | None:
    for key in ("citation_doi", "dc.identifier", "doi", "prism.doi"):
        v = metas.metas.get(key)
        if v:
            m = DOI_IN_URL.search(v)
            return m.group(1).rstrip(".") if m else v.strip()
    return None
