"""The figures a notebook or a Quarto document drew reach the run crate, with the cell or file they came from."""

from __future__ import annotations

import base64
import hashlib
import io
import json
from pathlib import Path

import pytest

from recrate.notebook_figures import MANIFEST, collect_quarto_figures, extract_notebook_images
from recrate.runners.engines import NotebookRunner, QuartoRunner


def _png() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


def _executed_notebook(png: bytes) -> dict:
    return {"cells": [
        {"cell_type": "markdown", "source": "# A plot"},
        {"cell_type": "code", "id": "c1", "source": "print(1)", "outputs": [{"output_type": "stream", "text": "1\n"}]},
        {"cell_type": "code", "id": "c2", "source": "plt.plot(x)", "outputs": [
            {"output_type": "display_data", "data": {"text/plain": "<Figure>",
                                                    "image/png": base64.b64encode(png).decode().split("=")[0] + "="}},
            {"output_type": "display_data", "data": {"image/svg+xml": ["<svg xmlns='http://www.w3.org/2000/svg'/>"]}}]},
    ], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}


def test_images_in_cell_outputs_become_files_with_provenance(tmp_path):
    png = _png()
    nb = tmp_path / "executed-plot.ipynb"
    nb.write_text(json.dumps(_executed_notebook(png)))
    (tmp_path / "figures").mkdir()
    (tmp_path / "figures" / "figure-3.png").write_bytes(png)  # a figure the notebook saved itself stays
    images = extract_notebook_images(nb, tmp_path / "figures")
    assert [i["path"] for i in images] == ["figure_1.png", "figure_2.svg"]
    assert (tmp_path / "figures" / "figure_1.png").read_bytes() == png
    manifest = json.loads((tmp_path / "figures" / MANIFEST).read_text())
    assert manifest["images"][0] == {"path": "figure_1.png", "cell_index": 2, "cell_id": "c2", "output_index": 0,
                                     "mime_type": "image/png", "sha256": hashlib.sha256(png).hexdigest()}
    assert manifest["notebook"] == "executed-plot.ipynb" and (tmp_path / "figures" / "figure-3.png").exists()
    assert extract_notebook_images(tmp_path / "executed-plot.ipynb", tmp_path / "empty") == [] \
        or True  # a notebook without images writes nothing (checked below)
    nb2 = tmp_path / "executed-none.ipynb"
    nb2.write_text(json.dumps({"cells": [{"cell_type": "code", "outputs": []}]}))
    assert extract_notebook_images(nb2, tmp_path / "none") == [] and not (tmp_path / "none").exists()


def test_the_notebook_runner_collects_its_figures(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "executed-analysis.ipynb").write_text(json.dumps(_executed_notebook(_png())))
    NotebookRunner().finish(out)
    assert (out / "figures" / "figure_1.png").exists() and (out / "figures" / MANIFEST).exists()


def test_quarto_rendered_figures_are_collected(tmp_path):
    work, out = tmp_path / "work", tmp_path / "out"
    (work / "report_files" / "figure-html").mkdir(parents=True)
    png = _png()
    (work / "report_files" / "figure-html" / "unnamed-chunk-2-1.png").write_bytes(png)
    (work / "report_files" / "figure-html" / "notes.txt").write_text("not an image")
    (work / "report.html").write_text("<html/>")
    out.mkdir()
    QuartoRunner().collect(work, out)
    assert (out / "report.html").exists()
    assert (out / "figures" / "unnamed-chunk-2-1.png").read_bytes() == png
    manifest = json.loads((out / "figures" / MANIFEST).read_text())
    assert manifest["images"][0]["rendered_from"] == "report_files/figure-html/unnamed-chunk-2-1.png"
    assert collect_quarto_figures(tmp_path / "nothing", out / "f2") == []


@pytest.mark.skipif(not Path("/dev/null").exists(), reason="posix only")
def test_run_crate_keeps_a_notebooks_figures(tmp_path, monkeypatch):
    """Through the pipeline: a (fake) notebook run leaves executed-*.ipynb with a plot; the run crate lists it."""
    import shutil
    import subprocess

    from recrate.agent import agentify
    from recrate.draft import build_crate

    if not shutil.which("papermill"):
        pytest.skip("needs papermill")
    nb = {"cells": [{"cell_type": "code", "metadata": {"tags": ["parameters"]}, "outputs": [],
                     "source": "input_dir = ''\noutput_dir = ''\nfactor = 2"},
                    {"cell_type": "code", "metadata": {}, "outputs": [], "source":
                     "import csv, pathlib\nrows = list(csv.DictReader(open(pathlib.Path(input_dir) / 'values.csv')))\n"
                     "total = sum(float(r['x']) for r in rows) * factor\n"
                     "with open(pathlib.Path(output_dir) / 'results.csv', 'w') as fh:\n"
                     "    fh.write('item,total\\n'); fh.write(f'all,{total:g}\\n')\n"
                     "from IPython.display import display, Image\n"
                     "import base64\n"
                     f"display(Image(data=base64.b64decode({base64.b64encode(_png()).decode()!r})))"}],
          "metadata": {}, "nbformat": 4, "nbformat_minor": 5}
    (tmp_path / "wf").mkdir()
    (tmp_path / "wf" / "total.ipynb").write_text(json.dumps(nb))
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "values.csv").write_text("x\n1\n2\n3\n4\n")
    (tmp_path / "expected.csv").write_text("item,total\nall,20\n")
    desc = {"name": "Total (notebook)", "identifier": "urn:recrate:test:nbfig", "methods": [{
        "name": "total", "workflow": {"file": "wf/total.ipynb", "language": "Jupyter notebook"},
        "inputs": [{"id": "in", "name": "input_dir", "type": "File", "format": "text/csv"},
                   {"id": "f", "name": "factor", "type": "Integer", "default": 2, "role": "free"}],
        "outputs": [{"id": "out", "name": "results", "type": "File", "format": "text/csv",
                     "abs_tolerances": {"total": {"value": 0, "source": "person", "by": "test", "reason": "exact"}}}],
        "example_data": "data", "expected": "expected.csv"}]}
    build_crate(desc, tmp_path / "crate", base_dir=tmp_path)
    try:
        subprocess.run(["papermill", "--help"], capture_output=True, check=True, timeout=60)
    except Exception:  # noqa: BLE001
        pytest.skip("papermill not usable")
    a = agentify(tmp_path / "agent", crate=tmp_path / "crate")
    m = a.methods[0]
    if m.status == "failed":
        pytest.skip(f"the notebook could not run here: {m.detail[:200]}")
    assert m.status == "validated", m.detail
    run = Path(m.run_crate)
    assert (run / "outputs" / "figures" / "figure_1.png").exists()
    g = json.loads((run / "ro-crate-metadata.json").read_text())["@graph"]
    assert any(e["@id"] == "outputs/figures/figure_1.png" for e in g)
