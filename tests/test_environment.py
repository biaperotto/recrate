"""The software environment as of publication: dates, detected software, versions by release history."""

from __future__ import annotations

import json
from datetime import date

from recrate.environment import (
    date_from_arxiv,
    date_from_text,
    detect_from_repo,
    detect_software,
    environment_for,
    publication_date,
)
from recrate.paper import Paper
from recrate.paper_crate import build_paper_crate

PYPI = {"releases": {
    "0.18.1": [{"upload_time_iso_8601": "2020-12-23T10:00:00Z"}],
    "0.19.0": [{"upload_time_iso_8601": "2021-12-03T10:00:00Z"}],
    "0.19.3rc1": [{"upload_time_iso_8601": "2022-06-01T10:00:00Z"}],  # pre-release: never chosen
    "0.20.0": [{"upload_time_iso_8601": "2022-10-20T10:00:00Z"}],
}}


def fake_fetch(url, accept):
    if "pypi.org" in url:
        return PYPI
    if "doi.org" in url:
        return {"published-online": {"date-parts": [[2022, 3, 15]]}}
    raise OSError("offline")


def test_publication_date_sources():
    assert date_from_text("Received 1 Jan 2022. Published: 12 March 2022. ...") == date(2022, 3, 12)
    assert date_from_text("Published online March 12, 2022") == date(2022, 3, 12)
    assert date_from_arxiv("2312.07852") == date(2023, 12, 1)
    assert publication_date("no date here", doi="10.1/x", fetch_json=fake_fetch) == (date(2022, 3, 15),
                                                                                      "DOI metadata (doi:10.1/x)")


def test_detects_software_and_stated_versions():
    sw = {s.name: s for s in detect_software(
        "Images were analysed in Fiji (ImageJ 1.53t) and with scikit-image version 0.19.2 in Python 3.9. "
        "Statistics were done in R (version 4.1.0). The STAR aligner was not used; a star shape was.")}
    assert sw["scikit-image"].version == "0.19.2" and sw["scikit-image"].how == "stated"
    assert sw["ImageJ"].version == "1.53t"
    assert sw["Python"].version == "3.9" and sw["R"].version == "4.1.0"
    assert "STAR" in sw  # the aligner, written in capitals; the lowercase "star shape" is ignored


def test_versions_as_of_publication(tmp_path):
    env = environment_for("We used scikit-image for segmentation.", date(2022, 3, 15), "test", fetch_json=fake_fetch)
    s = env.software[0]
    assert (s.version, s.how, s.released) == ("0.19.0", "as of publication", "2021-12-03")
    assert "scikit-image==0.19.0" in env.requirements_txt()


def test_repository_pins_win_over_dates(tmp_path):
    (tmp_path / "requirements.txt").write_text("scikit-image==0.18.1\nnumpy>=1.20\n")
    assert [(s.name, s.version, s.how) for s in detect_from_repo(tmp_path)] == [
        ("scikit-image", "0.18.1", "pinned"), ("numpy", None, "unknown")]
    env = environment_for("We used scikit-image.", date(2022, 3, 15), "test", code_dir=tmp_path, fetch_json=fake_fetch)
    assert next(s for s in env.software if s.package == "scikit-image").how == "pinned"


def test_paper_crate_records_how_each_version_was_found(tmp_path):
    p = Paper(title="A paper", text="Published: 15 March 2022. We used scikit-image. Data: 10.5281/zenodo.123.",
              source="paper.md", dois=["10.1234/paper", "10.5281/zenodo.123"])
    env = environment_for(p.text, date(2022, 3, 15), "the paper's text", fetch_json=fake_fetch)
    crate = build_paper_crate(p, env, tmp_path / "crate", code_url="https://github.com/me/code")
    graph = {e["@id"]: e for e in json.loads((crate / "ro-crate-metadata.json").read_text())["@graph"]}
    root = graph["./"]
    assert root["datePublished"] == "2022-03-15" and root["citation"]["@id"] == "https://doi.org/10.1234/paper"
    sk = next(e for e in graph.values() if e.get("name") == "scikit-image")
    assert sk["softwareVersion"] == "0.19.0"
    how = graph[next(p["@id"] for p in sk["additionalProperty"] if p["@id"].endswith("-how"))]["value"]
    assert how == "latest release before publication (2021-12-03)"
    assert "https://doi.org/10.5281/zenodo.123" in graph and "https://github.com/me/code" in graph
    assert (crate / "environment" / "requirements.txt").read_text().count("scikit-image==0.19.0") == 1


def test_software_released_after_the_paper_is_said_so():
    env = environment_for("We used scikit-image.", date(2019, 1, 1), "test", fetch_json=fake_fetch)
    s = env.software[0]
    assert (s.how, s.version) == ("later", "0.18.1") and "no release before publication" in s.note
    assert "scikit-image==" not in env.requirements_txt()  # never pin a version that didn't exist yet


def test_cited_titles_in_the_references_are_not_software():
    text = ("Methods\nWe segmented organs with MONAI.\n\n" + "Results and discussion. " * 40 +
            "\nReferences\n[1] Wasserthal et al. TotalSegmentator: robust segmentation of 104 structures.\n")
    names = {s.name for s in detect_software(text)}
    assert "MONAI" in names and "TotalSegmentator" not in names
