"""A Jupyter notebook published as a workflow (WorkflowHub's Jupyter class) runs as the authors ran it: papermill in
a copy of the crate, the data folder injected into its path variable, its printed cell outputs compared with the ones
the authors' run stored, its stored figures kept as the published ones."""

from __future__ import annotations

import base64
import io
import json
import shutil
from pathlib import Path

import pytest
from test_adapters import _crate  # noqa: E402

from recrate.adapters import _nb_analysis, detect_ecosystem, import_crate
from recrate.agent import agentify
from recrate.crate import load_crate

ABR = Path(__file__).resolve().parent.parent / "agents" / "preparing-a-data-set-for-deep-learning-from-zipped" / "crate"


def _png() -> str:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (1, 2, 3)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def notebook_crate(tmp_path: Path) -> Path:
    c = _crate(tmp_path / "published", "analysis.ipynb", "Jupyter")
    cells = [
        {"cell_type": "markdown", "metadata": {}, "source": "# Count the values"},
        {"cell_type": "code", "metadata": {}, "execution_count": 1, "outputs": [], "source": "import csv, glob, os"},
        {"cell_type": "code", "metadata": {}, "execution_count": 2, "outputs": [],
         "source": '"""Define the path to the data"""\npath2data = \'...\''},
        {"cell_type": "code", "metadata": {}, "execution_count": 3,
         "outputs": [{"output_type": "stream", "name": "stdout", "text": "Files: 1\nTotal: 10\n"}],
         "source": "files = glob.glob(os.path.join(path2data, '*.csv'))\nprint('Files: %d' % len(files))\n"
                   "xs = [float(r['x']) for f in files for r in csv.DictReader(open(f))]\nprint('Total: %g' % sum(xs))\n"
                   "open('summary.csv', 'w').write('total\\n%g\\n' % sum(xs))"},
        {"cell_type": "code", "metadata": {}, "execution_count": 4,
         "outputs": [{"output_type": "display_data", "data": {"image/png": _png(), "text/plain": "<Figure>"}}],
         "source": "import base64\nfrom IPython.display import Image, display\n"
                   f"display(Image(data=base64.b64decode({_png()!r})))  # the plot, as a stored image"},
    ]
    (c / "analysis.ipynb").write_text(json.dumps({"nbformat": 4, "nbformat_minor": 5, "cells": cells, "metadata": {
        "kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"}}}))
    return c


def test_the_notebook_is_read_as_a_method_with_its_stored_outputs_as_expected(tmp_path):
    c = notebook_crate(tmp_path)
    assert detect_ecosystem(c) == "notebook"
    import_crate(c, tmp_path / "crate")
    spec = load_crate(tmp_path / "crate")
    inv = spec.invocation
    assert inv["engine"] == "notebook" and inv["notebook"] == {"parameters_cell": 2, "path_params": ["path2data"]}
    assert inv["args"][:2] == ["papermill", "{workflow}"] and inv["args"][-3:] == ["-p", "path2data", "{input_dir}"]
    assert [p.name for p in spec.inputs] == ["input_dir"] and "*.csv" in spec.inputs[0].description
    assert spec.output_kind == "files" and spec.expected_results and spec.expected_results.exists()
    rows = spec.expected_results.read_text().splitlines()
    assert rows[0] == "file,sha1,size" and rows[1].startswith("cell-3,") and len(rows) == 2  # one cell printed
    assert spec.tolerance_specs()[0].source == "code-stated"
    assert any(n == "papermill" for n, _ in spec.software)  # what the run needs, installed by recrate env
    assert (tmp_path / "crate" / "expected" / "figures" / "figure_1.png").exists()  # the stored figure
    assert "cell 2 is tagged as the parameters cell" in spec.method_description


def test_imports_become_the_packages_to_install():
    nb = {"cells": [{"cell_type": "code", "source": "import os\nimport pandas as pd\nfrom sklearn.model_selection import x\n"
                                                    "pd.read_excel('a.xlsx', engine='openpyxl')", "outputs": []}]}
    a = _nb_analysis(nb)
    assert a["packages"] == ["openpyxl", "pandas", "scikit-learn"] and a["params_cell"] is None


@pytest.mark.skipif(not shutil.which("papermill"), reason="needs papermill")
def test_the_notebook_runs_on_a_data_folder_and_its_printed_output_is_compared(tmp_path):
    from recrate.compare import compare_results
    from recrate.pipeline import run_on_data

    c = notebook_crate(tmp_path)
    a = agentify(tmp_path / "agent", crate=c, online=False, validate="none")
    m = a.methods[0]
    assert m.readiness in {"ready-with-gaps", "ready"}, m.gaps  # no published data in the crate: reusable, comparable
    assert any("No published data" in g for g in m.gaps)
    data = tmp_path / "data"
    data.mkdir()
    (data / "values.csv").write_text("x\n4\n6\n")
    out = run_on_data(a.spec(), data, a.runs_dir, agent="test")
    outputs = out.run_dir / "outputs"
    assert (outputs / "summary.csv").read_text().strip().splitlines()[-1] == "10"  # written by the notebook, kept
    assert (outputs / "figures" / "figure_1.png").exists()  # drawn in this run, extracted from the executed notebook
    rows = (outputs / "results.csv").read_text().splitlines()
    assert rows[0] == "file,sha1,size" and rows[1].startswith("cell-3,")
    c = compare_results(outputs / "results.csv", a.spec().expected_results, {"sha1": 0.0}, sources={"sha1": "code-stated"})
    assert c.count == "1/1" and c.measures[0].note == "identical (sha1)"  # the same text printed as the authors'
    executed = json.loads(next(outputs.glob("executed-*.ipynb")).read_text())
    assert any("injected-parameters" in (x.get("metadata", {}).get("tags") or []) for x in executed["cells"])
    published = json.loads((tmp_path / "published" / "analysis.ipynb").read_text())
    assert published["cells"][2]["metadata"] == {}  # the published notebook is untouched: tagged only in the run's copy


@pytest.mark.skipif(not ABR.exists(), reason="the ABR notebook crate (WorkflowHub #216) is not here")
def test_the_real_abr_notebook_crate(tmp_path):
    nb = json.loads((ABR / "ABR_dataset_preparation.ipynb").read_text())
    a = _nb_analysis(nb)
    assert a["params_cell"] == 6 and a["path_params"] == ["path2data"]
    assert "pandas" in a["packages"] and "openpyxl" in a["packages"] and "os" not in a["packages"]
    assert len(a["printed"]) > 20 and a["images"] >= 1
    import_crate(ABR, tmp_path / "crate")
    spec = load_crate(tmp_path / "crate")
    assert "ABR_RESOURCE_Mouse ID.xlsx" in spec.inputs[0].description and "*.zip" in spec.inputs[0].description
