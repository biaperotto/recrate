"""The WorkflowHub example: a published Galaxy Workflow RO-Crate, read, run and reproduced."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
import tifffile

from recrate.agent import agentify
from recrate.crate import load_crate
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
    assert check_inputs(spec, data, scale_ratio=1).status == "ok"
    half = check_inputs(spec, data, scale_ratio=2)
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
    rescaled = run_on_data(a.spec(), half, a.runs_dir, agent="test", scale_ratio=2)
    assert rescaled.params["filter_size"] == 1.5
    assert unchanged == [385, 385, 281] and counts(rescaled) == [425, 414, 306]  # full resolution: 425, 413, 307


def test_reuse_on_bbbc039_with_ground_truth(tmp_path):
    """Another lab's images with ground truth: each run reports the true count next to its own. The published
    workflow, unchanged, finds 472 of BBBC039's 627 nuclei (no step splits touching nuclei)."""
    data = EXAMPLE / "reuse-bbbc039"
    if not data.exists():
        pytest.skip("BBBC039 sample not present")
    a = agentify(tmp_path / "agent", crate=CRATE, validate="none")
    out = run_on_data(a.spec(), data, a.runs_dir, agent="test")
    rows = [r.split(",") for r in (out.run_dir / "outputs" / "results.csv").read_text().splitlines()]
    assert rows[0] == ["image", "count", "mean_area_px", "count_true"]
    assert sum(int(r[1]) for r in rows[1:]) == 472 and sum(int(r[3]) for r in rows[1:]) == 627


# ---- any Galaxy workflow, not only imaging ---------------------------------------------------------------

def _climate_workflow() -> dict:
    """A small non-imaging workflow in the shape WorkflowHub publishes: a table input, an unported tool with
    scalar settings and a connected input, one declared output."""
    return {
        "a_galaxy_workflow": "true", "name": "Climate variable - select and plot", "annotation": "Monthly means.",
        "steps": {
            "0": {"id": 0, "type": "data_input", "label": "Temperature table", "name": "Input dataset",
                  "annotation": "A tabular file of monthly temperatures", "tool_id": None,
                  "tool_state": json.dumps({"optional": False, "format": ["tabular"]}), "input_connections": {},
                  "inputs": [{"name": "Temperature table", "description": "A tabular file"}]},
            "1": {"id": 1, "type": "parameter_input", "label": "Threshold", "name": "Input parameter", "tool_id": None,
                  "tool_state": json.dumps({"parameter_type": "float", "default": 1.5, "optional": False}),
                  "input_connections": {}, "annotation": "Cut-off in degrees"},
            "2": {"id": 2, "type": "tool", "label": None, "name": "Datamash",
                  "tool_id": "toolshed.g2.bx.psu.edu/repos/iuc/datamash_ops/datamash_ops/1.1.0", "tool_version": "1.1.0",
                  "tool_state": json.dumps({"__page__": None, "in_file": json.dumps({"__class__": "ConnectedValue"}),
                                            "header_in": "\"true\"", "need_sort": "\"true\"", "grouping": "\"1\"",
                                            "operations": "[{\"op_column\": \"8\", \"op_name\": \"mean\"}]",
                                            "adv": json.dumps({"__current_case__": 0, "round": "no"})}),
                  "input_connections": {"in_file": {"id": 0, "output_name": "output"}},
                  "workflow_outputs": [{"label": "mean table", "output_name": "out_file"}]},
        },
    }


def _climate_crate(folder: Path) -> Path:
    crate = folder / "crate"
    crate.mkdir(parents=True)
    (crate / "climate.ga").write_text(json.dumps(_climate_workflow()), encoding="utf-8")
    (crate / "ro-crate-metadata.json").write_text(json.dumps({"@context": "https://w3id.org/ro/crate/1.1/context", "@graph": [
        {"@id": "ro-crate-metadata.json", "@type": "CreativeWork", "about": {"@id": "./"}},
        {"@id": "./", "@type": "Dataset", "name": "Research Object Crate for Climate variable - select and plot",
         "mainEntity": {"@id": "climate.ga"}, "license": "https://spdx.org/licenses/Apache-2.0",
         "identifier": "https://workflowhub.eu/workflows/46?version=1", "datePublished": "2020-07-23 19:22:21 +0100",
         "hasPart": [{"@id": "climate.ga"}]},
        {"@id": "climate.ga", "@type": ["File", "SoftwareSourceCode", "ComputationalWorkflow"],
         "name": "Climate variable - select and plot", "programmingLanguage": {"@id": "#galaxy"}},
        {"@id": "#galaxy", "@type": "ComputerLanguage", "name": "Galaxy"},
    ]}), encoding="utf-8")
    (folder / "workflowhub.json").write_text(json.dumps({  # what sources.fetch_workflowhub saves next to the crate
        "id": "46", "doi": "10.48546/workflowhub.workflow.46.1", "tags": ["climate"],
        "topic_annotations": [{"label": "Climate science", "identifier": "http://edamontology.org/topic_3855"}]}))
    return crate


def test_every_tool_is_needed_when_no_outputs_are_declared():
    from recrate.galaxy import result_steps, unported_tools
    from recrate.runners import readiness

    wf = _climate_workflow()
    assert result_steps(wf) == {"0", "2"}  # the declared output and what feeds it
    del wf["steps"]["2"]["workflow_outputs"]
    assert result_steps(wf) == {"0", "2"}  # nothing declared: every tool counts
    assert unported_tools(wf) == ["datamash_ops 1.1.0"]
    assert readiness("Galaxy", Path("x.ga")) == "The workflow file is missing."


def test_non_imaging_galaxy_crate_is_imported_generically(tmp_path):
    from recrate.crate import load_crate
    from recrate.galaxy import imaging_workflow, import_galaxy_crate
    from recrate.runners import readiness

    crate = _climate_crate(tmp_path)
    assert is_galaxy_crate(crate) and not imaging_workflow(_climate_workflow())
    import_galaxy_crate(crate, tmp_path / "out")
    spec = load_crate(tmp_path / "out")
    assert spec.profile == "generic" and spec.method_name == "climate_variable_select_and_plot"
    assert spec.citation == "https://doi.org/10.48546/workflowhub.workflow.46.1"
    assert "Fluorescence" not in json.dumps(spec.raw) and "  (" not in spec.description
    assert "workflowhub.eu/workflows/46" in spec.description
    d = spec.data_input
    assert d.name == "temperature_table" and d.type == "File" and d.format == "text/tab-separated-values"
    assert "monthly temperatures" in (d.description or "")
    names = {p.name: p for p in spec.value_inputs}
    assert names["threshold"].default == "1.5" and names["threshold"].role == "free"
    assert names["datamash_ops_header_in"].default == "true" and names["datamash_ops_adv_round"].default == "no"
    assert "in_file" not in json.dumps(list(names))  # connected inputs are not parameters
    assert spec.outputs[0].name == "mean_table" and spec.output_kind == "files"
    # honest readiness: nothing is ported, so it is blocked, naming the tool
    why = readiness(spec.workflow_language, spec.workflow_path)
    assert why and "not ported" in why and "datamash_ops 1.1.0" in why


def test_galaxy_crate_whose_root_is_a_url_is_recognised_and_imported(tmp_path):
    """WorkflowHub #1532, a Galaxy Training Network workflow: the metadata descriptor is ``about`` the tutorial's
    URL, not ``./``, so the root dataset (and its mainEntity) must be found through the descriptor."""
    from recrate.galaxy import import_galaxy_crate

    crate = _climate_crate(tmp_path)
    meta = json.loads((crate / "ro-crate-metadata.json").read_text())
    url = "https://training.galaxyproject.org/training-material/topics/climate/tutorials/x/workflows/x.html"
    for e in meta["@graph"]:
        if e["@id"] == "./":
            e["@id"] = url
        if e["@id"] == "ro-crate-metadata.json":
            e["about"] = {"@id": url}
    (crate / "ro-crate-metadata.json").write_text(json.dumps(meta))
    assert is_galaxy_crate(crate)
    import_galaxy_crate(crate, tmp_path / "derived")
    spec = load_crate(tmp_path / "derived")
    assert spec.workflow_path.name == "climate.ga" and spec.inputs  # the method is read, not an empty crate


def test_galaxy_crate_recognised_by_language_and_rejected_without_main_entity(tmp_path):
    crate = _climate_crate(tmp_path)
    meta = json.loads((crate / "ro-crate-metadata.json").read_text())
    for e in meta["@graph"]:
        if e["@id"] == "./":
            e["mainEntity"] = {"@id": "climate.json"}
        if e["@id"] == "climate.ga":
            e["@id"] = "climate.json"
    (crate / "climate.ga").rename(crate / "climate.json")
    (crate / "ro-crate-metadata.json").write_text(json.dumps(meta))
    assert is_galaxy_crate(crate)  # not .ga, but its language is Galaxy
    for e in meta["@graph"]:
        e.pop("mainEntity", None)
    (crate / "ro-crate-metadata.json").write_text(json.dumps(meta))
    assert not is_galaxy_crate(crate)
