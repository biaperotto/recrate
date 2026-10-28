"""A method as a Python function (the crate's ``recrate entry``), the way Paper2Agent wraps a package's API: no
argparse, no CSV boilerplate. ReCRATE's wrapper calls it in the paper's own interpreter, sandboxed, writes its table
as results.csv and its figures into the run crate, and the tool is checked against the published value, exposed
over MCP with typed arguments, and reused with recorded changes like any other method."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from recrate.agent import agentify
from recrate.draft import build_crate
from recrate.pipeline import run_on_data
from recrate.runcrate import summarise_run

TOOL = '''
"""The authors' code as they would write it: functions, not a command line."""


def gc_content(sequence="GATTACA"):
    s = sequence.upper()
    return {"sequence": "query", "gc": round((s.count("G") + s.count("C")) / len(s), 4)}


def totals(input_dir, factor=1.0):
    import csv

    import pandas as pd

    xs = [float(r["x"]) for r in csv.DictReader(open(f"{input_dir}/values.csv"))]
    return pd.DataFrame([{"item": "all", "total": sum(xs) * factor}])


def totals_with_figure(input_dir, factor=1.0):
    import csv

    import matplotlib.pyplot as plt

    xs = [float(r["x"]) for r in csv.DictReader(open(f"{input_dir}/values.csv"))]
    fig, ax = plt.subplots()
    ax.bar(range(len(xs)), xs)
    return [{"item": "all", "total": sum(xs) * factor}], {"figure-2": fig}
'''

FACTOR = {"id": "factor", "name": "factor", "type": "Float", "default": 1, "role": "free", "description": "Multiplier"}
VALUES = {"id": "in", "name": "input_dir", "type": "File", "format": "text/csv", "description": "values.csv with x",
          "properties": {"columns": {"value": "x"}}}


def _agent(tmp: Path, entry: str, inputs: list[dict], expected: str, abs_tol: dict, data: bool = False):
    (tmp / "tool.py").write_text(TOOL)
    (tmp / "expected.csv").write_text(expected)
    desc = {"name": "Functions", "identifier": f"urn:recrate:test:{entry}", "methods": [{
        "name": entry.split(":")[1], "workflow": {"file": "tool.py", "language": "Python", "entry": entry},
        "inputs": inputs,
        "outputs": [{"id": "out", "name": "results", "type": "File", "format": "text/csv", "kind": "scalar",
                     "abs_tolerances": {k: {"value": v, "source": "paper-rounding"} for k, v in abs_tol.items()}}],
        "expected": "expected.csv"}]}
    if data:
        (tmp / "data").mkdir(exist_ok=True)
        (tmp / "data" / "values.csv").write_text("x\n1\n2\n3\n")
        desc["methods"][0]["example_data"] = "data/"
    build_crate(desc, tmp / "crate", base_dir=tmp)
    return agentify(tmp / "agent", crate=tmp / "crate")


def test_a_value_only_function_is_checked_against_the_published_value_and_typed_over_mcp(tmp_path):
    seq = {"id": "seq", "name": "sequence", "type": "Text", "default": "GATTACA", "role": "free",
           "description": "A DNA sequence"}
    a = _agent(tmp_path, "tool:gc_content", [seq], "sequence,gc\nquery,0.2857\n", {"gc": 0.00005})
    m = a.methods[0]
    assert m.readiness == "ready", m.gaps
    assert m.ladder["numbers"]["status"] == "passed"  # the function's dict became results.csv and matched
    assert a.spec().entry == "tool:gc_content" and a.spec().summary()["entry"] == "tool:gc_content"

    out = run_on_data(a.spec(), None, a.runs_dir, agent="test", overrides={"sequence": ("GGCC", "my primer")})
    assert (out.run_dir / "outputs" / "results.csv").read_text().splitlines()[1] == "query,1.0"
    assert "Set to GGCC by test" in summarise_run(out.run_dir)["adjustments"][0]  # the change, with its reason

    pytest.importorskip("mcp")
    from recrate.mcp_server import build_server

    tools = {t.name: t for t in asyncio.run(build_server(a.path).list_tools())}
    schema = getattr(tools["run_gc_content"], "inputSchema", None) or tools["run_gc_content"].input_schema
    assert "data_path" not in schema["properties"] and schema["properties"]["sequence"]["default"] == "GATTACA"


def test_a_function_taking_data_returns_a_dataframe(tmp_path):
    pytest.importorskip("pandas")
    a = _agent(tmp_path, "tool:totals", [VALUES, FACTOR], "item,total\nall,6\n", {"total": 0.5}, data=True)
    m = a.methods[0]
    assert m.readiness == "ready", m.gaps
    assert m.ladder["numbers"]["status"] == "passed"
    mine = tmp_path / "mine"
    mine.mkdir()
    (mine / "values.csv").write_text("x\n10\n20\n")
    out = run_on_data(a.spec(), mine, a.runs_dir, agent="test", overrides={"factor": (2.0, "doubling")})
    rows = (out.run_dir / "outputs" / "results.csv").read_text().splitlines()
    assert rows[0] == "item,total" and float(rows[1].split(",")[1]) == 60.0
    assert (out.run_dir / "outputs" / "packages.txt").exists()  # the interpreter's packages, as for a script


def test_a_figure_the_function_returns_lands_in_the_run_crate(tmp_path):
    pytest.importorskip("matplotlib")
    a = _agent(tmp_path, "tool:totals_with_figure", [VALUES, FACTOR], "item,total\nall,6\n", {"total": 0.5},
               data=True)
    m = a.methods[0]
    assert m.ladder["numbers"]["status"] == "passed", m.gaps
    run_dir = Path(m.run_crate)
    fig = run_dir / "outputs" / "figures" / "figure-2.png"
    assert fig.exists() and fig.stat().st_size > 1000
    graph = json.loads((run_dir / "ro-crate-metadata.json").read_text())["@graph"]
    img = next(e for e in graph if e["@id"] == "outputs/figures/figure-2.png")
    assert "ImageObject" in img["@type"]  # recorded as an output of the run, paired with the paper's Figure 2


def test_readiness_names_a_function_that_cannot_be_loaded(tmp_path):
    from recrate.runners import readiness

    (tmp_path / "tool.py").write_text(TOOL)
    why = readiness("Python", tmp_path / "tool.py", entry="tool:nowhere")
    assert why and "nowhere" in why and "recrate entry" in why
    assert readiness("Python", tmp_path / "tool.py", entry="tool:gc_content") is None

    seq = {"id": "seq", "name": "sequence", "type": "Text", "default": "GATTACA", "description": "A DNA sequence"}
    a = _agent(tmp_path, "tool:nowhere", [seq], "sequence,gc\nquery,0.2857\n", {"gc": 0.00005})
    m = a.methods[0]
    assert m.readiness == "blocked" and "nowhere" in m.gaps[0]


def test_a_parameter_the_function_does_not_take_is_an_error_not_a_silent_drop(tmp_path):
    from recrate.runners.function import FunctionRunner

    (tmp_path / "tool.py").write_text(TOOL)
    r = FunctionRunner()
    r.entry = "tool:gc_content"
    with pytest.raises(RuntimeError, match="does not take the parameter"):
        r.run(tmp_path / "tool.py", tmp_path, tmp_path / "out", {"sequence": "GG", "window": 3})
