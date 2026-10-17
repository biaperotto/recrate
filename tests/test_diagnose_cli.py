from __future__ import annotations

import json
from pathlib import Path

import pytest

from httpfake import FakeHTTP
from recrate.cli import main

DOI = "10.0000/recrate-demo-paper"


def test_diagnose_with_uploaded_crate(demo, tmp_path, capsys):
    """--crate skips locate; exit 0 and a diagnosis is written."""
    out = tmp_path / "out"
    paper = str(demo["paper"])
    rc = main(["diagnose", paper, "--crate", str(demo["crate"]), "--out", str(out)])
    printed = capsys.readouterr().out
    assert rc == 0
    assert "Readiness:" in printed
    j = json.loads((out / "diagnosis.json").read_text())
    assert j["crate_source"] == "uploaded"
    assert (out / "diagnosis.md").exists()


def test_diagnose_no_crate_exits_3(tmp_path, capsys, monkeypatch):
    """A URL paper with no locatable crate and no fallback: exit 3 with guidance."""
    landing = "https://example.org/p"
    html = ("<html><head><meta name='citation_doi' content='" + DOI + "'></head><body>"
            + "x" * 4000 + "</body></html>")
    fake = FakeHTTP()
    fake.add(landing, html=html, final_url=landing)  # no signposting, crossref/datacite 404
    monkeypatch.setattr("recrate.http.get", fake.get)

    rc = main(["diagnose", landing, "--out", str(tmp_path / "out")])
    printed = capsys.readouterr().out
    assert rc == 3
    assert "No RO-Crate found" in printed
    assert "--crate" in printed
    assert (tmp_path / "out" / "locate.json").exists()
