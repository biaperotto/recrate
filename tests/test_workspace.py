from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

import numpy as np
import tifffile

from recrate.figures import Panel, comparison_chart, panel_figure
from recrate.pipeline import check_inputs, run_on_data
from recrate.profiles.bioimaging import CALIBRATION_FILE, set_calibration
from recrate.runcrate import image_reviews, review_image, run_story
from recrate.workspace import export_bundle, flag_images, parameter_sources, whatif


def _mean(rows, col):
    return sum(float(r[col]) for r in rows) / len(rows)


def test_whatif_shows_what_the_adjustment_prevents(agent, demo):
    spec = agent.spec("count_nuclei")
    as_is = whatif(spec, demo["my_images"], spec.default_params())
    adjusted = whatif(spec, demo["my_images"], {**spec.default_params(), "min_size_px": 12.5})
    assert _mean(as_is["rows"], "count") < _mean(adjusted["rows"], "count")
    assert as_is["overlays"] and not list(agent.runs_dir.glob("*-reuse"))  # nothing recorded


def test_overrides_win_and_are_recorded(agent, demo):
    out = run_on_data(agent.spec("count_nuclei"), demo["my_images"], agent.runs_dir, agent="Anna",
                      overrides={"min_size_px": (10, "smallest nuclei in my cells are ~10 px")})
    assert out.params["min_size_px"] == 10
    p = next(p for p in run_story(out.run_dir)["parameters"] if p["name"] == "min_size_px")
    assert "Anna" in p["note"] and "~10 px" in p["note"] and "12.5" in p["note"] and p["paper_default"] == "50"


def test_missing_pixel_size_can_be_supplied_and_is_recorded(agent, demo, tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    for f in sorted(Path(demo["my_images"]).glob("*.tif")):
        tifffile.imwrite(raw / f.name, tifffile.imread(f))  # same pixels, calibration dropped
    spec = agent.spec("count_nuclei")
    px = next(i for i in check_inputs(spec, raw).items if i.name == "Pixel size")
    assert px.status == "fail"

    work = tmp_path / "calibrated"
    assert len(set_calibration(raw, work, 1.3, "µm", who="Anna", source="microscope settings")) == 3
    px = next(i for i in check_inputs(spec, work).items if i.name == "Pixel size")
    assert px.status == "warn" and "user-provided by Anna" in px.note
    out = run_on_data(spec, work, agent.runs_dir, agent="Anna")
    assert out.params["min_size_px"] == 12.5
    assert any(i["file"] == CALIBRATION_FILE for i in run_story(out.run_dir)["inputs"])


def test_per_image_review_and_story(agent, demo):
    out = run_on_data(agent.spec("count_nuclei"), demo["my_images"], agent.runs_dir, agent="Anna")
    review_image(out.run_dir, "Anna", "my_field_02.tif", False, "two nuclei merged")
    assert image_reviews(out.run_dir)["my_field_02.tif"] == {"value": "rejected", "note": "two nuclei merged",
                                                            "by": "Anna"}
    story = run_story(out.run_dir)
    assert story["software"] and story["inputs"][0]["sha256"] and story["checks"]
    assert any(r["value"] == "rejected" for r in story["reviews"])


def test_flags_outliers_and_values_outside_the_paper():
    rows = [{"image": f"i{k}.tif", "count": str(v)} for k, v in enumerate([20, 21, 22, 21, 60])]
    flags = flag_images(rows, "count", reported=[{"count": "20"}, {"count": "23"}])
    assert len(flags["i4.tif"]) == 2 and flags["i0.tif"] == []


def test_parameter_sources_quote_the_paper(agent):
    src = {s["name"]: s for s in parameter_sources(agent, agent.spec("count_nuclei"))}
    assert "50" in (src["min_size_px"]["quote"] or "")


def test_export_bundle(agent, demo):
    out = run_on_data(agent.spec("count_nuclei"), demo["my_images"], agent.runs_dir, agent="Anna")
    z = export_bundle(agent, agent.spec("count_nuclei"), out.run_dir, figures={"fig1.png": b"png"})
    names = zipfile.ZipFile(z).namelist()
    assert {"results.csv", "methods.md", "references.bib", "figures/fig1.png", "README.txt",
            "run-crate/ro-crate-metadata.json"} <= set(names)
    bib = zipfile.ZipFile(z).read("references.bib").decode()
    assert "10.0000/recrate-demo-paper" in bib and "10.7717/peerj.453" in bib


def test_figures(demo):
    mine = sorted(Path(demo["my_images"]).glob("*.tif"))
    paper = sorted(Path(demo["crate"], "data", "count-nuclei").glob("*.tif"))
    png, notes = panel_figure([Panel(str(paper[0]), "paper"), Panel(str(mine[0]), "yours")], same_scale=True)
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and notes[0].startswith("Scale bars")
    pdf, _ = panel_figure([Panel(str(mine[0]))], fmt="pdf")
    assert pdf[:4] == b"%PDF"
    chart = comparison_chart(["a.tif", "b.tif"], [22, 21], [13, 12], "count", "adjusted", "as-is", (20, 24))
    assert chart[:4] == b"\x89PNG"
