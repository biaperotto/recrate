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


def test_workflowhub_fetch_saves_the_registry_record(tmp_path, monkeypatch):
    """What the crate itself rarely says (DOI, EDAM topics, parsed inputs) is kept next to it, offline afterwards."""
    import io
    import json

    from recrate import sources

    attrs = {"title": "Nuclei", "license": "MIT", "latest_version": 2, "doi": "10.48546/workflowhub.workflow.771.2",
             "workflow_class": {"title": "Galaxy"}, "tags": ["imaging"],
             "topic_annotations": [{"label": "Microscopy", "identifier": "http://edamontology.org/topic_3382"}],
             "internals": {"inputs": [{"id": "#main/input_image", "type": [{"type": "File"}]}], "outputs": [], "steps": []}}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("ro-crate-metadata.json", json.dumps({"@graph": [{"@id": "./", "@type": "Dataset", "name": "Nuclei"}]}))
    monkeypatch.setattr(sources, "_json", lambda url: {"data": {"attributes": attrs}})
    monkeypatch.setattr(sources, "_get", lambda url, accept=None, timeout=60: (buf.getvalue(), "application/zip"))
    found = sources.fetch_workflowhub("771", None, tmp_path)
    assert found.kind == "crate" and found.path == tmp_path / "crate" and "DOI: 10.48546/workflowhub.workflow.771.2" in found.notes
    rec = json.loads((tmp_path / "workflowhub.json").read_text())
    assert rec["doi"] == attrs["doi"] and rec["version"] == "2" and rec["workflow_class"] == "Galaxy"
    assert rec["topic_annotations"][0]["label"] == "Microscopy" and rec["internals"]["inputs"][0]["id"] == "#main/input_image"


def test_agentify_fetches_a_crate_given_as_a_link(tmp_path, monkeypatch):
    """`recrate agentify --crate https://workflowhub.eu/workflows/771` fetches the crate (here: a fake fetch that
    copies the nuclei example) and records where it came from; an unknown string is refused with a clear message."""
    import shutil
    from pathlib import Path

    import pytest

    import recrate.sources as sources
    from recrate.agent import agentify
    from recrate.crate import CrateError

    example = Path(__file__).resolve().parent.parent / "examples" / "workflowhub-nuclei-segmentation" / "crate"
    if not example.exists():
        pytest.skip("WorkflowHub example not present")

    def fake_fetch(ref, dest):
        assert ref.kind == "workflowhub" and ref.id == "771"
        shutil.copytree(example, Path(dest) / "crate")
        return sources.Found("crate", "Nuclei (WorkflowHub #771, v2)", Path(dest) / "crate",
                             "https://workflowhub.eu/workflows/771?version=2", ["Licence: MIT"])

    monkeypatch.setattr(sources, "fetch", fake_fetch)
    monkeypatch.delenv("RECRATE_OFFLINE", raising=False)  # a link must be fetched: refused when offline
    with pytest.raises(CrateError, match="offline"):
        agentify(tmp_path / "agent0", crate="https://workflowhub.eu/workflows/771", validate="none", online=False)
    a = agentify(tmp_path / "agent", crate="https://workflowhub.eu/workflows/771", validate="none", search_crate=False)
    assert a.methods and a.manifest["crate_fetched"]["from"].startswith("https://workflowhub.eu/workflows/771")
    assert (tmp_path / "agent" / "source" / "crate" / "ro-crate-metadata.json").exists()
    with pytest.raises(CrateError, match="neither a folder"):
        agentify(tmp_path / "agent2", crate="not-a-crate-anywhere", validate="none")
