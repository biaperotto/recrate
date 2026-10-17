from __future__ import annotations

import json
import shutil

import numpy as np
import pytest

from recrate import check_inputs, load_crate, reproduce, run_on_data
from recrate.compare import read_results, summarise
from recrate.crate import CrateError
from recrate.demo import save_calibrated, synth_image
from recrate.draft import build_crate_from_json
from recrate.methods import methods_paragraph
from recrate.record import find_runs, publish_back, summarise_record
from recrate.runcrate import approve, load_run_crate, summarise_run


def test_crate_card(demo):
    card = load_crate(demo["crate"]).summary()
    assert card["expects"]["pixel size"] == "0.65 µm"
    assert card["parameters"]["min_size_px"]["scaling"] == "inverse_pixel_area"
    assert card["tolerances"] == {"count": 0.05, "mean_area": 0.10}
    assert card["has_example_data"] and card["has_reported_results"]


def test_reproduce_matches_reported(demo, tmp_path):
    out = reproduce(demo["crate"], tmp_path, runner="python", agent="Tester")
    assert out.comparison.reproduced
    s = summarise_run(out.run_dir)
    assert s["mode"] == "reproduce" and s["verdict"] == "reproduced" and s["by"] == "Tester"


def test_check_flags_pixel_size_and_rescales(demo):
    report = check_inputs(demo["crate"], demo["my_images"])
    assert report.status == "warn"
    px = next(i for i in report.items if i.name == "Pixel size")
    assert px.status == "warn"
    (adj,) = report.adjustments
    assert adj.parameter == "min_size_px"
    assert adj.new == pytest.approx(12.5)


def test_adjustment_recovers_comparable_results(demo, tmp_path):
    reported = summarise(read_results(load_crate(demo["crate"]).expected_results))
    adjusted = run_on_data(demo["crate"], demo["my_images"], tmp_path, runner="python")
    naive = run_on_data(demo["crate"], demo["my_images"], tmp_path, runner="python", auto_adjust=False)
    a = summarise(read_results(adjusted.run_dir / "outputs" / "results.csv"))
    n = summarise(read_results(naive.run_dir / "outputs" / "results.csv"))
    # With rescaling, mean nucleus area in µm² matches the paper; without it, small nuclei are lost.
    assert abs(a["mean_area"] - reported["mean_area"]) / reported["mean_area"] < 0.10
    assert n["count"] < 0.8 * a["count"]


def test_run_crate_records_adjustment_and_links_to_paper(demo, tmp_path):
    out = run_on_data(demo["crate"], demo["my_images"], tmp_path, runner="python", agent="Anna")
    meta = load_run_crate(out.run_dir)
    idx = {e["@id"]: e for e in meta["@graph"]}
    root = idx["./"]
    assert root["isBasedOn"]["@id"] == load_crate(demo["crate"]).identifier
    assert "https://w3id.org/ro/wfrun/process/0.5" in {c["@id"] for c in root["conformsTo"]}
    assert "Adjusted from 50" in idx["#pv-min-size-px"]["description"]
    files = [e for e in meta["@graph"] if e.get("@type") == "File" and e["@id"].startswith("inputs/")]
    assert len(files) == 3 and all(len(f["sha256"]) == 64 for f in files)
    assert (out.run_dir / "ro-crate-preview.html").exists()
    assert list((out.run_dir / "outputs" / "overlays").glob("*.png"))


def test_approve_methods_and_record(demo, tmp_path):
    runs = tmp_path / "runs"
    crate_copy = tmp_path / "crate"
    shutil.copytree(demo["crate"], crate_copy)
    spec = load_crate(crate_copy)
    r1 = reproduce(spec, runs, runner="python", agent="Anna")
    r2 = run_on_data(spec, demo["my_images"], runs, runner="python", agent="Bia")
    approve(r2.run_dir, "Bia", accepted=True, note="Outlines follow the nuclei.")
    assert summarise_run(r2.run_dir)["approved"] == "accepted"

    text = methods_paragraph(r2.run_dir)
    assert "min_size_px = 12.5" in text and "1.3 µm" in text

    found = find_runs(spec, runs)
    s = summarise_record(found)
    assert s["runs"] == 2 and s["reproduced"] == 1 and s["reused_on_new_data"] == 1
    assert s["people"] == ["Anna", "Bia"]

    publish_back(spec, r1.run_dir)
    record = publish_back(spec, r2.run_dir)
    assert record["summary"]["runs"] == 2
    assert (crate_copy / "reproducibility-record.json").exists()


def test_missing_calibration_fails(demo, tmp_path):
    import tifffile

    d = tmp_path / "uncalibrated"
    d.mkdir()
    tifffile.imwrite(d / "x.tif", synth_image(np.random.default_rng(1)))
    report = check_inputs(demo["crate"], d)
    assert report.status == "fail"
    with pytest.raises(CrateError):
        run_on_data(demo["crate"], d, tmp_path / "runs", runner="python")


def test_mixed_pixel_sizes_fail(demo, tmp_path):
    d = tmp_path / "mixed"
    d.mkdir()
    rng = np.random.default_rng(2)
    save_calibrated(d / "a.tif", synth_image(rng), 0.65)
    save_calibrated(d / "b.tif", synth_image(rng), 1.3)
    assert check_inputs(demo["crate"], d).status == "fail"


def test_draft_roundtrip(demo, tmp_path):
    desc = demo["crate"].parent / "paper_crate_description.json"
    desc_copy = tmp_path / "desc.json"
    data = json.loads(desc.read_text(encoding="utf-8"))
    data["workflow"]["file"] = str(demo["crate"] / "count_nuclei.ijm")
    data["example_data"] = str(demo["crate"] / "data")
    data["expected"] = str(demo["crate"] / "expected" / "results.csv")
    desc_copy.write_text(json.dumps(data), encoding="utf-8")
    out = build_crate_from_json(desc_copy, tmp_path / "rebuilt")
    assert load_crate(out).summary() == load_crate(demo["crate"]).summary()
