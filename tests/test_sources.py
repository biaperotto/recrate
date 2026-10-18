from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

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


def test_preprint_links_keep_the_version_out_of_the_doi():
    from recrate.sources import identify

    med = identify("https://www.medrxiv.org/content/10.64898/2026.04.07.26350296v2")
    assert [(r.kind, r.id, r.version, r.text) for r in med] == [("preprint", "10.64898/2026.04.07.26350296", "v2", "medrxiv")]
    bio = identify("https://www.biorxiv.org/content/10.1101/2023.01.02.522345v1.full")
    assert (bio[0].kind, bio[0].id, bio[0].version) == ("preprint", "10.1101/2023.01.02.522345", "v1")
    assert identify("10.1101/2023.01.02.522345v3")[0].id == "10.1101/2023.01.02.522345"  # a bare DOI too
