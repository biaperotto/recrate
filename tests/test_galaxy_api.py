"""A Galaxy workflow run on a Galaxy server through its API (BioBlend), against a fake server: the data is uploaded
and mapped to the right input steps, the parameters reach their steps, the outputs come back under the labels the
published test uses, and the run crate records the server instead of a sandbox."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from recrate import galaxy
from recrate.agent import agentify
from recrate.pipeline import reproduce, run_on_data
from recrate.runners import galaxy_api, get_runner, readiness

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "workflowhub-nuclei-segmentation"
CRATE = EXAMPLE / "crate"
pytestmark = pytest.mark.skipif(not CRATE.exists(), reason="WorkflowHub example not present")


class FakeGalaxy:
    """What the runner asks of bioblend's GalaxyInstance, answered from the example's published test files."""

    def __init__(self, version: str = "24.1"):
        self.uploads: list[tuple[str, str]] = []  # (file name, history id)
        self.calls: list[dict[str, Any]] = []  # invoke_workflow keyword arguments
        self.histories_made: list[str] = []
        self.store: dict[str, tuple[bytes, str]] = {}  # dataset id -> (bytes, extension)
        self.config = SimpleNamespace(get_version=lambda: {"version_major": version})
        self.histories = SimpleNamespace(create_history=self._create_history)
        self.workflows = SimpleNamespace(import_workflow_dict=lambda wf, publish=False: {"id": "wf-1", "name": wf["name"]},
                                         invoke_workflow=self._invoke)
        self.tools = SimpleNamespace(upload_file=self._upload)
        self.invocations = SimpleNamespace(wait_for_invocation=lambda iid, maxwait=0: {"id": iid, "state": "scheduled"},
                                           show_invocation=self._show)
        self.datasets = SimpleNamespace(wait_for_dataset=self._wait, download_dataset=self._download)

    def _create_history(self, name=None):
        self.histories_made.append(name)
        return {"id": "hist-1", "name": name}

    def _upload(self, path, history_id, **kw):
        did = f"hda-{len(self.store) + 1}"
        self.store[did] = (Path(path).read_bytes(), Path(path).suffix.lstrip("."))
        self.uploads.append((kw.get("file_name") or Path(path).name, history_id))
        return {"outputs": [{"id": did}]}

    def _invoke(self, workflow_id, **kw):
        self.calls.append({"workflow_id": workflow_id, **kw})
        return {"id": f"inv-{len(self.calls)}", "state": "new"}

    def _label_image(self) -> bytes:
        """A label image the shape of the last uploaded image, with the published test's 20 objects."""
        import io

        import numpy as np
        import tifffile

        raw = next(b for b, ext in reversed(list(self.store.values())) if ext in {"tif", "tiff"})
        shape = tifffile.imread(io.BytesIO(raw)).shape[-2:]
        labels = np.zeros(shape, np.uint16)
        for k in range(20):
            labels[5 * k:5 * k + 3, 2:5] = k + 1
        buf = io.BytesIO()
        tifffile.imwrite(buf, labels)
        return buf.getvalue()

    def _show(self, iid):
        outs = {}
        for label, data, ext in [("objects_count", (CRATE / "test-data" / "objects_count.tabular").read_bytes(), "tabular"),
                                 ("label_image", self._label_image(), "tiff"),
                                 ("overlay_image", (CRATE / "test-data" / "overlay_image.png").read_bytes(), "png")]:
            did = f"hda-{len(self.store) + 1}"
            self.store[did] = (data, ext)
            outs[label] = {"id": did, "src": "hda"}
        return {"id": iid, "state": "scheduled", "outputs": outs}

    def _wait(self, did, maxwait=0):
        return {"id": did, "state": "ok", "file_ext": self.store[did][1]}

    def _download(self, did, file_path=None, use_default_filename=True, **kw):
        Path(file_path).write_bytes(self.store[did][0])
        return file_path


@pytest.fixture()
def server(monkeypatch):
    fake = FakeGalaxy()
    monkeypatch.setenv("GALAXY_API_KEY", "test-key")
    monkeypatch.setenv("GALAXY_URL", "https://fake.galaxy/")
    monkeypatch.setattr(galaxy_api, "connect", lambda: fake)
    return fake


def test_a_galaxy_server_reproduces_the_published_count(tmp_path, server):
    a = agentify(tmp_path / "agent", crate=CRATE, validate="none")
    out = reproduce(a.spec(), a.runs_dir, runner="galaxy-api", agent="test")
    assert out.comparison and out.comparison.reproduced and "count 20 vs 20" in out.comparison.headline()
    rows = (out.run_dir / "outputs" / "results.csv").read_text().splitlines()
    assert rows[0] == "image,count,mean_area_px" and rows[1].startswith("input_image.tiff,20,")
    assert server.uploads == [("input_image.tiff", "hist-1")] and server.histories_made[0].startswith("ReCRATE:")
    call = server.calls[0]
    assert call["inputs"] == {"0": {"src": "hda", "id": "hda-1"}} and call["inputs_by"] == "step_index"
    assert call["history_id"] == "hist-1"
    assert call["params"]["1"] == {"filter|filter_type": "gaussian", "filter|size": 3.0}  # the published values, explicit
    outputs = out.run_dir / "outputs"
    assert {p.name for p in outputs.iterdir()} >= {"objects_count.tabular", "label_image.tiff", "overlay_image.png"}
    assert (outputs / "masks" / "input_image_mask.tif").exists()  # from the label image, for the overlays
    meta = (out.run_dir / "ro-crate-metadata.json").read_text()
    graph = json.loads(meta)["@graph"]
    assert any(e.get("name") == "Galaxy server https://fake.galaxy" and e.get("softwareVersion") == "24.1" for e in graph)
    assert any(e.get("name") == "Galaxy tool ip_count_objects" for e in graph)
    assert "ran on the Galaxy server https://fake.galaxy (history https://fake.galaxy/histories/view?id=hist-1)" in meta


def test_parameters_reach_their_steps_and_each_image_is_one_invocation(tmp_path, server):
    a = agentify(tmp_path / "agent", crate=CRATE, validate="none")
    data = EXAMPLE / "research-data" / "B2"
    out = run_on_data(a.spec(), data, a.runs_dir, agent="test", runner="galaxy-api",
                      overrides={"filter_size": (2.0, "sharper nuclei")})
    images = sorted(p.name for p in data.glob("*.tif"))
    assert len(server.calls) == len(images) == 3
    assert [u[0] for u in server.uploads] == images
    p = next(x for x in galaxy.exposed_parameters(galaxy.load_workflow(a.spec().workflow_path)) if x["name"] == "filter_size")
    for call in server.calls:
        assert call["params"][p["step"]]["|".join(p["path"])] == 2.0  # on the filter step, as filter|size
        assert call["params"][p["step"]].get("filter|filter_type") == "gaussian"  # the published value travels too
    rows = (out.run_dir / "outputs" / "results.csv").read_text().splitlines()
    assert [r.split(",")[0] for r in rows[1:]] == images and all(r.split(",")[1] == "20" for r in rows[1:])
    assert (out.run_dir / "outputs" / Path(images[0]).stem / "objects_count.tabular").exists()  # one folder per image


def test_file_listing_results_when_the_workflow_counts_nothing(tmp_path, server, monkeypatch):
    """A workflow without a counting tool: results are what it produced, file by file, named by the output labels
    the published test uses, so they compare with the test's sha1 rows."""
    monkeypatch.setattr(galaxy, "imaging_workflow", lambda wf: False)
    a = agentify(tmp_path / "agent", crate=CRATE, validate="none")
    out = run_on_data(a.spec(), a.spec().example_data, a.runs_dir, agent="test", runner="galaxy-api")
    rows = (out.run_dir / "outputs" / "results.csv").read_text().splitlines()
    assert rows[0] == "file,sha1,size" and sorted(r.split(",")[0] for r in rows[1:]) == \
        ["label_image", "objects_count", "overlay_image"]
    import hashlib

    published = hashlib.sha1((CRATE / "test-data" / "objects_count.tabular").read_bytes()).hexdigest()
    assert any(r.startswith(f"objects_count,{published},") for r in rows)


def test_the_server_is_used_only_for_tools_that_are_not_ported(tmp_path, monkeypatch):
    a = agentify(tmp_path / "agent", crate=CRATE, validate="none")
    spec = a.spec()
    monkeypatch.delenv("GALAXY_API_KEY", raising=False)
    assert get_runner("auto", spec.workflow_language, spec.workflow_path).name == "galaxy"  # every tool is ported
    assert readiness(spec.workflow_language, spec.workflow_path) is None
    monkeypatch.setattr(galaxy, "TOOLS", {k: v for k, v in galaxy.TOOLS.items() if k != "ip_count_objects"})
    why = readiness(spec.workflow_language, spec.workflow_path)
    assert why and "ip_count_objects 0.0.5-2" in why and "GALAXY_API_KEY" in why  # blocked, and how to unblock it
    assert get_runner("auto", spec.workflow_language, spec.workflow_path).name == "galaxy"
    monkeypatch.setenv("GALAXY_API_KEY", "k")
    monkeypatch.setattr(galaxy_api, "bioblend_missing", lambda: "Install BioBlend (pip install 'recrate[galaxy]').")
    why = readiness(spec.workflow_language, spec.workflow_path)
    assert why and "recrate[galaxy]" in why
    monkeypatch.setattr(galaxy_api, "bioblend_missing", lambda: None)
    assert readiness(spec.workflow_language, spec.workflow_path) is None
    assert get_runner("auto", spec.workflow_language, spec.workflow_path).name == "galaxy-api"


def test_an_explicit_server_runner_without_a_key_says_how_to_configure_it(monkeypatch, tmp_path):
    monkeypatch.delenv("GALAXY_API_KEY", raising=False)
    monkeypatch.setattr(galaxy_api, "bioblend_missing", lambda: None)
    r = get_runner("galaxy-api")
    assert r.name == "galaxy-api" and not r.available() and "GALAXY_API_KEY" in (r.readiness() or "")
    shutil.copy2(CRATE / "test-data" / "input_image.tiff", tmp_path / "input_image.tiff")
    with pytest.raises(RuntimeError, match="GALAXY_API_KEY"):
        r.run(CRATE / "segmentation-and-counting.ga", tmp_path, tmp_path / "out", {})


def test_files_map_to_input_steps_by_label_then_by_order(tmp_path):
    files = [tmp_path / n for n in ("reads_2.fastq", "reference.fa", "reads_1.fastq")]
    for f in files:
        f.write_text("x")
    inputs = [("0", "reference"), ("1", "reads_1"), ("2", "reads_2"), ("3", "annotations")]
    mapped = galaxy_api.map_files(inputs, sorted(files))
    assert {k: v.name for k, v in mapped.items()} == {"0": "reference.fa", "1": "reads_1.fastq", "2": "reads_2.fastq"}
    mapped = galaxy_api.map_files([("0", "a"), ("1", "b")], sorted(files))  # no label matches: in order
    assert [v.name for v in mapped.values()] == ["reads_1.fastq", "reads_2.fastq"]
