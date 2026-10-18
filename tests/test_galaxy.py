"""The WorkflowHub example: a published Galaxy Workflow RO-Crate, read, run and reproduced."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
import tifffile

from recrate.agent import agentify
from recrate.galaxy import exposed_parameters, is_galaxy_crate, load_workflow, run_workflow
from recrate.pipeline import check_inputs, run_on_data
from recrate.runners import get_runner

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "workflowhub-nuclei-segmentation"
CRATE = EXAMPLE / "crate"
pytestmark = pytest.mark.skipif(not CRATE.exists(), reason="WorkflowHub example not present")


def _digest(d: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(d.rglob("*")):
        if f.is_file():
            h.update(f.name.encode() + f.read_bytes())
    return h.hexdigest()


def test_port_reproduces_the_published_test_outputs():
    wf = load_workflow(CRATE / "segmentation-and-counting.ga")
    out, log = run_workflow(wf, tifffile.imread(CRATE / "test-data" / "input_image.tiff"))
    published = tifffile.imread(CRATE / "test-data" / "label_image.tiff")
    assert out["objects_count"] == 20
    assert np.array_equal(out["label_image"] > 0, published > 0)  # same pixels as Galaxy's own output
    assert any("skipped" in line and "Overlay" in line for line in log)  # pictures only, not computed


def test_only_parameters_that_change_results_are_exposed():
    names = [p["name"] for p in exposed_parameters(load_workflow(CRATE / "segmentation-and-counting.ga"))]
    assert names == ["filter_type", "filter_size", "threshold_method", "threshold_offset"]


def test_agentify_derives_a_crate_and_validates_against_the_published_count(tmp_path):
    before = _digest(CRATE)
    assert is_galaxy_crate(CRATE)
    a = agentify(tmp_path / "agent", crate=CRATE)
    m = a.card()["methods"][0]
    assert a.card()["crate_source"] == "derived from a Galaxy Workflow RO-Crate"
    assert m["status"] == "validated" and "count 20 vs 20" in m["detail"]
    assert (a.path / "source-crate" / "ro-crate-metadata.json").exists()
    assert a.search("label map")  # answers from the workflow's README
    assert _digest(CRATE) == before  # the published crate is untouched
    root = next(e for e in json.loads((a.crate_dir / "ro-crate-metadata.json").read_text())["@graph"]
                if e["@id"] == "./")
    assert "iwc-workflows" in root["isBasedOn"]["@id"]


def test_runs_on_the_zenodo_images(tmp_path):
    data = EXAMPLE / "research-data" / "B2"
    a = agentify(tmp_path / "agent", crate=CRATE)
    spec = a.spec()
    unstated = check_inputs(spec, data)  # the crate states no pixel size, and filter_size is in pixels
    pixel = next(i for i in unstated.items if i.name == "Pixel size")
    assert unstated.status == "warn" and "filter_size" in pixel.note
    assert next(i for i in unstated.items if i.name == "Bit depth / intensity").status == "ok"  # same as the paper's
    assert check_inputs(spec, data, pixel_ratio=1).status == "ok"
    half = check_inputs(spec, data, pixel_ratio=2)
    assert [(a.parameter, a.new) for a in half.adjustments] == [("filter_size", 1.5)]
    out = run_on_data(spec, data, a.runs_dir, agent="test", overrides={"filter_size": (2.0, "sharper nuclei")})
    rows = (out.run_dir / "outputs" / "results.csv").read_text().splitlines()
    assert len(rows) == 1 + len(list(data.glob("*.tif"))) and all(int(r.split(",")[1]) > 100 for r in rows[1:])
    assert out.params["filter_size"] == 2.0


def test_unknown_workflow_languages_are_refused():
    with pytest.raises(RuntimeError, match="no runner"):
        get_runner("auto", "Fortran", Path("model.f90"))


def test_reuse_at_half_resolution(tmp_path):
    """The README's reuse story: the research images at half resolution. The published filter size merges
    neighbouring nuclei; rescaled for the stated pixel ratio, the counts match full resolution."""
    half = EXAMPLE / "my-data-half-resolution"
    if not half.exists():
        pytest.skip("run examples/workflowhub-nuclei-segmentation/make_half_resolution.py")
    a = agentify(tmp_path / "agent", crate=CRATE)

    def counts(out):
        return [int(r.split(",")[1]) for r in (out.run_dir / "outputs" / "results.csv").read_text().splitlines()[1:]]

    unchanged = counts(run_on_data(a.spec(), half, a.runs_dir, agent="test"))
    rescaled = run_on_data(a.spec(), half, a.runs_dir, agent="test", pixel_ratio=2)
    assert rescaled.params["filter_size"] == 1.5
    assert unchanged == [385, 385, 281] and counts(rescaled) == [425, 414, 306]  # full resolution: 425, 413, 307
