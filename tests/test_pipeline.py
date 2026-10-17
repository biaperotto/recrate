from __future__ import annotations

import shutil

import numpy as np
import pytest

from recrate import check_inputs, load_crate, reproduce, run_on_data
from recrate.compare import read_results, summarise
from recrate.crate import CrateError
from recrate.demo import save_calibrated, synth_image
from recrate.methods import methods_paragraph
from recrate.record import find_runs, publish_back, summarise_record
from recrate.runcrate import approve, load_run_crate, summarise_run


def test_crate_card(demo):
    card = load_crate(demo["crate"], "count_nuclei").summary()
    assert card["expects"]["pixel size"] == "0.65 µm"
    assert card["parameters"]["min_size_px"]["scaling"] == "inverse_pixel_area"
    assert card["tolerances"] == {"count": 0.05, "mean_area": 0.10}


@pytest.mark.parametrize("method", ["count_nuclei", "size_distribution"])
def test_reproduce_matches_reported(demo, tmp_path, method):
    out = reproduce(demo["crate"], tmp_path, agent="Tester", method=method)
    assert out.comparison.reproduced
    s = summarise_run(out.run_dir)
    assert s["mode"] == "reproduce" and s["verdict"] == "reproduced" and s["method"] == method


def test_check_flags_pixel_size_and_rescales(demo):
    report = check_inputs(demo["crate"], demo["my_images"], method="count_nuclei")
    assert report.status == "warn"
    (adj,) = report.adjustments
    assert adj.parameter == "min_size_px" and adj.new == pytest.approx(12.5)


def test_adjustment_recovers_comparable_results(demo, tmp_path):
    spec = load_crate(demo["crate"], "count_nuclei")
    reported = summarise(read_results(spec.expected_results))
    adjusted = run_on_data(spec, demo["my_images"], tmp_path)
    naive = run_on_data(spec, demo["my_images"], tmp_path, auto_adjust=False)
    a = summarise(read_results(adjusted.run_dir / "outputs" / "results.csv"))
    n = summarise(read_results(naive.run_dir / "outputs" / "results.csv"))
    assert abs(a["mean_area"] - reported["mean_area"]) / reported["mean_area"] < 0.10
    assert n["count"] < 0.8 * a["count"]


def test_python_method_runs_on_my_data(demo, tmp_path):
    out = run_on_data(demo["crate"], demo["my_images"], tmp_path, method="size_distribution")
    assert out.params["min_size_px"] == pytest.approx(12.5)
    rows = read_results(out.run_dir / "outputs" / "results.csv")
    assert len(rows) == 3 and float(rows[0]["median_area"]) > 0
    meta = load_run_crate(out.run_dir)
    software = [e for e in meta["@graph"] if e.get("@type") == "SoftwareApplication" and e["name"] == "Python"]
    assert software


def test_run_crate_records_adjustment_and_links_to_paper(demo, tmp_path):
    out = run_on_data(demo["crate"], demo["my_images"], tmp_path, agent="Anna", method="count_nuclei")
    idx = {e["@id"]: e for e in load_run_crate(out.run_dir)["@graph"]}
    root = idx["./"]
    assert root["isBasedOn"]["@id"] == load_crate(demo["crate"]).identifier
    assert "https://w3id.org/ro/wfrun/process/0.5" in {c["@id"] for c in root["conformsTo"]}
    assert "Adjusted from 50" in idx["#pv-min-size-px"]["description"]
    files = [e for e in idx.values() if e.get("@type") == "File" and e["@id"].startswith("inputs/")]
    assert len(files) == 3 and all(len(f["sha256"]) == 64 for f in files)
    assert (out.run_dir / "ro-crate-preview.html").exists()
    assert list((out.run_dir / "outputs" / "overlays").glob("*.png"))


def test_approve_methods_and_record(demo, tmp_path):
    runs = tmp_path / "runs"
    crate_copy = tmp_path / "crate"
    shutil.copytree(demo["crate"], crate_copy)
    spec = load_crate(crate_copy, "count_nuclei")
    r1 = reproduce(spec, runs, agent="Anna")
    r2 = run_on_data(spec, demo["my_images"], runs, agent="Bia")
    approve(r2.run_dir, "Bia", accepted=True, note="Outlines follow the nuclei.")
    assert summarise_run(r2.run_dir)["approved"] == "accepted"

    text = methods_paragraph(r2.run_dir)
    assert "min_size_px = 12.5" in text and "1.3 µm" in text

    s = summarise_record(find_runs(spec, runs))
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
    assert check_inputs(demo["crate"], d).status == "fail"
    with pytest.raises(CrateError):
        run_on_data(demo["crate"], d, tmp_path / "runs")


def test_mixed_pixel_sizes_fail(demo, tmp_path):
    d = tmp_path / "mixed"
    d.mkdir()
    rng = np.random.default_rng(2)
    save_calibrated(d / "a.tif", synth_image(rng), 0.65)
    save_calibrated(d / "b.tif", synth_image(rng), 1.3)
    assert check_inputs(demo["crate"], d).status == "fail"
