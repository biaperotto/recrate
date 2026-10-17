from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from recrate.diagnose import diagnose_crate

DOI = "10.0000/recrate-demo-paper"


@pytest.fixture()
def crate(demo, tmp_path):
    """A writable copy of the demo crate."""
    dst = tmp_path / "crate"
    shutil.copytree(demo["crate"], dst)
    return dst


def _meta(crate: Path) -> list[dict]:
    return json.loads((crate / "ro-crate-metadata.json").read_text())["@graph"]


def _save(crate: Path, graph: list[dict]) -> None:
    doc = json.loads((crate / "ro-crate-metadata.json").read_text())
    doc["@graph"] = graph
    (crate / "ro-crate-metadata.json").write_text(json.dumps(doc, indent=2))


def test_unchanged_demo_is_ready(crate):
    d = diagnose_crate(crate, paper_doi=DOI, crate_source="found", source_url="https://zenodo.org/x")
    assert d.readiness == "ready"
    assert all(m.validatable and m.reusable for m in d.methods)
    # No tolerance warnings on the complete crate.
    assert not any("tolerance" in f.message.lower() for f in d.all_findings())


def test_removing_reported_results_blocks_validation(crate):
    shutil.rmtree(crate / "expected")
    d = diagnose_crate(crate, paper_doi=DOI)
    assert d.readiness == "blocked"
    assert all(not m.validatable for m in d.methods)
    assert any("reported results" in f.message.lower() for f in d.all_findings())


def test_removing_tolerances_warns(crate):
    graph = [e for e in _meta(crate)
             if not (e.get("@type") == "PropertyValue" and str(e.get("name", "")).startswith("tolerance:"))]
    for e in graph:  # drop dangling references
        ap = e.get("additionalProperty")
        if isinstance(ap, list):
            e["additionalProperty"] = [r for r in ap if not str(r.get("@id", "")).startswith("#tol")]
    _save(crate, graph)
    d = diagnose_crate(crate, paper_doi=DOI)
    assert any("tolerance" in f.message.lower() and f.severity == "warning" for f in d.all_findings())


def test_galaxy_language_has_no_runner(crate):
    graph = _meta(crate)
    for e in graph:
        if e.get("@type") == "ComputerLanguage" and "imagej" in str(e.get("name", "")).lower():
            e["name"] = "Galaxy"
    _save(crate, graph)
    d = diagnose_crate(crate, paper_doi=DOI)
    galaxy = [m for m in d.methods if not m.runnable]
    assert galaxy, "the Galaxy method should be not runnable"
    assert any("no runner" in f.message.lower() and f.severity == "blocking"
               for m in d.methods for f in m.findings)
    assert d.readiness == "ready-with-gaps"  # the Python method is still validatable


def test_wrong_citation_is_blocking(crate):
    graph = _meta(crate)
    for e in graph:
        if e.get("@id") == "./":
            e["citation"] = {"@id": "https://doi.org/10.9999/some-other-paper"}
    _save(crate, graph)
    d = diagnose_crate(crate, paper_doi=DOI)
    assert d.readiness == "blocked"
    assert any("wrong crate" in f.message.lower() and f.severity == "blocking"
               for f in d.crate_findings)


def test_json_and_markdown_written(crate, tmp_path):
    d = diagnose_crate(crate, paper_doi=DOI, crate_source="found")
    jp, mp = d.write(tmp_path / "out")
    assert jp.exists() and mp.exists()
    assert json.loads(jp.read_text())["readiness"] == "ready"
    assert "Ready to validate" in mp.read_text()
