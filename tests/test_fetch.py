from __future__ import annotations

import pytest

from recrate import fetch
from httpfake import FakeHTTP

LANDING = "https://example.org/articles/paper-1"
PDF = "https://example.org/articles/paper-1.pdf"
DOI = "10.1234/demo.5678"

PAGE_WITH_PDF = f"""<html><head>
  <meta name="citation_title" content="A Fine Paper">
  <meta name="citation_doi" content="{DOI}">
  <meta name="citation_pdf_url" content="{PDF}">
  <link rel="describedby" href="https://zenodo.org/api/records/999"
        type="application/ld+json">
</head><body>Abstract only here.</body></html>"""

# A tiny but valid-looking "PDF": read_paper falls back to text for non-.pdf,
# so we save it as .txt by serving HTML-less text through the metadata path.
PAYWALL_PAGE = f"""<html><head>
  <meta name="citation_title" content="A Paywalled Paper">
  <meta name="citation_doi" content="{DOI}">
</head><body>Access denied.</body></html>"""


def test_classify_link_doi_arxiv_url_local(tmp_path):
    assert fetch.classify_link("10.1234/abc") == ("doi", "10.1234/abc")
    assert fetch.classify_link("https://doi.org/10.1234/abc") == ("doi", "10.1234/abc")
    assert fetch.classify_link("https://arxiv.org/abs/2401.01234")[0] == "arxiv"
    assert fetch.classify_link("https://arxiv.org/abs/2401.01234")[1] == "2401.01234"
    assert fetch.classify_link("https://example.org/x") == ("url", "https://example.org/x")
    f = tmp_path / "p.md"
    f.write_text("hi")
    assert fetch.classify_link(str(f)) == ("local", str(f))


def test_fetch_local_paper(tmp_path):
    f = tmp_path / "paper.md"
    f.write_text("# Title\n\nSome methods. doi:10.1234/local.9")
    r = fetch.fetch_paper(str(f), tmp_path / "work", cache_dir=None)
    assert r.kind == "local" and r.full_text and r.paper is not None


def test_fetch_follows_signposting_and_downloads_pdf(tmp_path, monkeypatch):
    fake = FakeHTTP()
    fake.add(f"https://doi.org/{DOI}", html=PAGE_WITH_PDF, final_url=LANDING)
    fake.add(PDF, text="# A Fine Paper\n\nFull methods text. min size 50 pixels.")
    monkeypatch.setattr("recrate.http.get", fake.get)

    r = fetch.fetch_paper(f"https://doi.org/{DOI}", tmp_path / "work", cache_dir=None)

    assert r.full_text is True
    assert r.doi == DOI
    assert r.landing_url == LANDING
    assert r.pdf_url == PDF
    assert any(s["url"] == "https://zenodo.org/api/records/999" and "describedby" in s["rel"]
               for s in r.signposting)
    assert "Full methods text" in r.paper.text


def test_fetch_paywall_is_metadata_only(tmp_path, monkeypatch):
    fake = FakeHTTP()
    fake.add(f"https://doi.org/{DOI}", html=PAYWALL_PAGE, final_url=LANDING)
    monkeypatch.setattr("recrate.http.get", fake.get)

    r = fetch.fetch_paper(f"https://doi.org/{DOI}", tmp_path / "work", cache_dir=None)

    assert r.full_text is False
    assert r.doi == DOI
    assert r.paper.title == "A Paywalled Paper"
    assert "--pdf" in r.message


def test_pdf_override_wins(tmp_path, monkeypatch):
    fake = FakeHTTP()
    fake.add(f"https://doi.org/{DOI}", html=PAYWALL_PAGE, final_url=LANDING)
    monkeypatch.setattr("recrate.http.get", fake.get)
    local_pdf = tmp_path / "mine.md"
    local_pdf.write_text("# Supplied\n\nThe real methods.")

    r = fetch.fetch_paper(f"https://doi.org/{DOI}", tmp_path / "work", pdf=str(local_pdf), cache_dir=None)
    assert r.full_text is True
    assert "The real methods" in r.paper.text


def test_http_cache_saves_a_round_trip(tmp_path, monkeypatch):
    """The real http.get caches: a second call to the same URL hits no network."""
    from recrate import http

    hits = {"n": 0}

    class _FakeResp:
        status = 200
        headers = {"Content-Type": "text/plain"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def geturl(self):
            return "https://example.org/data"

        def read(self):
            return b"payload"

    def fake_urlopen(req, timeout=None):
        hits["n"] += 1
        return _FakeResp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    cache = tmp_path / "cache"
    r1 = http.get("https://example.org/data", cache_dir=cache)
    r2 = http.get("https://example.org/data", cache_dir=cache)
    assert r1.content == r2.content == b"payload"
    assert hits["n"] == 1  # second call served from .recrate-cache
