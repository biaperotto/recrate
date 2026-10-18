from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from recrate.paper import read_paper
from recrate.sources import from_upload, identify, safe_unzip


def test_identify_recognises_identifiers_and_links():
    refs = identify("Try 10.48546/workflowhub.workflow.771.2, https://workflowhub.eu/workflows/1505?version=1, "
                    "the data at 10.5281/zenodo.3362976, arXiv:2312.07852, the paper 10.1371/journal.pone.0309210 "
                    "and https://github.com/iwc-workflows/fluorescence-nuclei-segmentation-and-counting.")
    got = [(r.kind, r.id, r.version) for r in refs]
    assert got == [("workflowhub", "771", "2"), ("workflowhub", "1505", "1"), ("zenodo", "3362976", None),
                   ("arxiv", "2312.07852", None), ("doi", "10.1371/journal.pone.0309210", None),
                   ("repo", "https://github.com/iwc-workflows/fluorescence-nuclei-segmentation-and-counting", None)]


def test_identify_ignores_plain_text():
    assert identify("how is debris removed?") == []


def test_doi_from_medrxiv_pdf_text_drops_the_watermark():
    t = "medRxiv preprint 10.64898/2026.04.07.26350296doi: this version posted"
    assert [r.id for r in identify(t)] == ["10.64898/2026.04.07.26350296"]


def test_doi_ending_in_a_letter_is_not_truncated():
    assert [r.id for r in identify("see 10.1371/journal.pone.0123456x here")] \
        == ["10.1371/journal.pone.0123456x"]


def test_doi_containing_a_colon_is_kept():
    assert [r.id for r in identify("10.1234/abc:def ")] == ["10.1234/abc:def"]


def test_a_doi_legitimately_ending_in_doi_is_kept():
    assert [r.id for r in identify("10.1234/abcdoi ")] == ["10.1234/abcdoi"]


def test_uploaded_crate_zip_is_found(demo, tmp_path):
    z = tmp_path / "crate.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for f in Path(demo["crate"]).rglob("*"):
            if f.is_file():
                zf.write(f, f"paper_crate/{f.relative_to(demo['crate'])}")
    found = from_upload(z, tmp_path / "out")
    assert found.kind == "crate" and (found.path / "ro-crate-metadata.json").exists()


def test_uploaded_paper(demo, tmp_path):
    found = from_upload(Path(demo["paper"]), tmp_path / "out")
    assert found.kind == "paper" and found.path.exists()


def test_archives_cannot_write_outside_their_folder(tmp_path):
    z = tmp_path / "evil.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("../escaped.txt", "x")
    with pytest.raises(ValueError, match="Unsafe path"):
        safe_unzip(z, tmp_path / "out")
    assert not (tmp_path / "escaped.txt").exists()


def test_read_paper_drops_the_watermark_from_a_doi(tmp_path):
    f = tmp_path / "p.txt"
    f.write_text("medRxiv preprint 10.64898/2026.04.07.26350296doi: this version posted\n")
    assert read_paper(f).dois == ["10.64898/2026.04.07.26350296"]
