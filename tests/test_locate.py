from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from httpfake import FakeHTTP
from recrate import locate
from recrate.paper import Paper

DOI = "10.0000/recrate-demo-paper"          # the demo crate's root citation
ZENODO_REC = "https://zenodo.org/api/records/999"
ZENODO_FILE = "https://zenodo.org/api/records/999/files/demo.crate.zip/content"


def _zip_dir(crate_dir: Path, top: str = "") -> bytes:
    """Zip a crate folder; with ``top`` set, nest it under a single top folder
    (as GitHub's zipball does)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for f in sorted(crate_dir.rglob("*")):
            if f.is_file():
                arc = f.relative_to(crate_dir).as_posix()
                zf.writestr(f"{top}{arc}" if top else arc, f.read_bytes())
    return buf.getvalue()


@pytest.fixture()
def paper():
    return Paper(title="Demo", text="see our data at zenodo", source="x", dois=[DOI])


@pytest.fixture()
def crate_zip(demo):
    return _zip_dir(Path(demo["crate"]))


def _zenodo_record_json() -> str:
    return json.dumps({"files": [{"key": "demo.crate.zip", "links": {"self": ZENODO_FILE}}]})


def test_locate_via_signposting_zenodo(paper, crate_zip, tmp_path, monkeypatch):
    fake = FakeHTTP()
    fake.add(ZENODO_REC, text=_zenodo_record_json())
    fake.add(ZENODO_FILE, content=crate_zip)
    monkeypatch.setattr("recrate.http.get", fake.get)

    signposting = [{"url": ZENODO_REC, "rel": "describedby", "from": "html-link"}]
    res = locate.locate_crate(paper, signposting, out_dir=tmp_path / "crate", cache_dir=None)

    assert res.found
    assert res.source_url == ZENODO_REC
    assert (res.crate_dir / "ro-crate-metadata.json").exists()
    assert res.candidates[0].accepted


def test_locate_via_crossref_relation(paper, crate_zip, tmp_path, monkeypatch):
    fake = FakeHTTP()
    fake.add(f"https://api.crossref.org/works/{DOI}",
             text=json.dumps({"message": {"relation": {
                 "is-supplemented-by": [{"id": "10.5281/zenodo.999", "id-type": "doi"}]}}}))
    fake.add(ZENODO_REC, text=_zenodo_record_json())
    fake.add(ZENODO_FILE, content=crate_zip)
    monkeypatch.setattr("recrate.http.get", fake.get)

    res = locate.locate_crate(paper, signposting=[], out_dir=tmp_path / "crate", cache_dir=None)

    assert res.found
    assert any(c.origin == "crossref" and c.accepted for c in res.candidates)


def test_locate_via_github_zipball(crate_zip, demo, tmp_path, monkeypatch):
    gh_zip = _zip_dir(Path(demo["crate"]), top="repo-main/")
    fake = FakeHTTP()
    fake.add("https://codeload.github.com/acme/repo/zip/refs/heads/main", content=gh_zip)
    monkeypatch.setattr("recrate.http.get", fake.get)

    paper = Paper(title="Demo", text="code", source="x", dois=[DOI],
                  repos=["https://github.com/acme/repo"])
    res = locate.locate_crate(paper, signposting=[], out_dir=tmp_path / "crate", cache_dir=None)

    assert res.found
    assert (res.crate_dir / "ro-crate-metadata.json").exists()


def test_nothing_found_lists_every_candidate(paper, tmp_path, monkeypatch):
    fake = FakeHTTP()  # crossref/datacite/zenodo all 404
    monkeypatch.setattr("recrate.http.get", fake.get)

    signposting = [{"url": "https://example.org/not-a-crate", "rel": "describedby"}]
    res = locate.locate_crate(paper, signposting, out_dir=tmp_path / "crate", cache_dir=None)

    assert not res.found
    assert res.crate_dir is None
    assert any(c.origin == "signposting" and not c.accepted for c in res.candidates)
    assert all(c.reason for c in res.candidates)  # every try explained


def test_zip_slip_is_refused(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../escape.txt", b"nope")
    with pytest.raises(ValueError, match="escapes the target folder"):
        locate._safe_unzip(buf.getvalue(), tmp_path / "crate")
