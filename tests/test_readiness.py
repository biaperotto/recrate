"""Readiness says what a method is for: a workflow with nothing published to compare with is reusable, not
reproducible, and the app, the CLI and the card say so instead of listing what the crate lacks."""

from __future__ import annotations

from recrate.agent import agentify, diagnose
from recrate.draft import build_crate

SCRIPT = '''
import argparse, csv
from pathlib import Path
ap = argparse.ArgumentParser(); ap.add_argument("--input_dir"); ap.add_argument("--output_dir"); a = ap.parse_args()
Path(a.output_dir).mkdir(parents=True, exist_ok=True)
n = len(list(Path(a.input_dir).glob("*.csv")))
with open(Path(a.output_dir) / "results.csv", "w", newline="") as fh:
    w = csv.writer(fh); w.writerow(["item", "files"]); w.writerow(["all", n])
'''


def _crate(tmp, expected: bool, data: bool):
    (tmp / "count.py").write_text(SCRIPT)
    if data:
        (tmp / "data").mkdir(exist_ok=True)
        (tmp / "data" / "a.csv").write_text("x\n1\n")
    if expected:
        (tmp / "expected.csv").write_text("item,files\nall,1\n")
    desc = {"name": "Count files", "identifier": "urn:recrate:test:count", "methods": [{
        "name": "count_files", "workflow": {"file": "count.py", "language": "Python"},
        "inputs": [{"id": "in", "name": "input_dir", "type": "File", "format": "text/csv"}],
        "outputs": [{"id": "out", "name": "results", "type": "File", "format": "text/csv", "kind": "scalar",
                     "abs_tolerances": {"files": {"value": 0, "source": "person", "by": "t", "reason": "exact"}}}],
        **({"example_data": "data"} if data else {}), **({"expected": "expected.csv"} if expected else {})}]}
    build_crate(desc, tmp / "crate", base_dir=tmp)
    return tmp / "crate"


def test_a_workflow_with_nothing_published_is_reusable_not_reproducible(tmp_path):
    a = agentify(tmp_path / "agent", crate=_crate(tmp_path, expected=False, data=True))
    m = a.methods[0]
    assert m.readiness == "reusable" and m.gaps[0].startswith("Reusable, not reproducible")
    assert "published data can still be run" in m.gaps[0]
    assert m.ladder["runs"]["status"] == "passed" and m.ladder["numbers"]["status"] == "not checked"
    assert a.card()["verdict"].startswith("Reusable, not reproducible: 1 method(s) run here")


def test_without_data_either_it_says_so(tmp_path):
    readiness, gaps = diagnose(__import__("recrate.crate", fromlist=["load_crate"]).load_crate(
        _crate(tmp_path, expected=False, data=False)))
    assert readiness == "reusable" and "no published data either" in gaps[0]


def test_with_published_results_it_is_ready(tmp_path):
    a = agentify(tmp_path / "agent", crate=_crate(tmp_path, expected=True, data=True))
    m = a.methods[0]
    assert m.readiness == "ready" and m.status == "validated", (m.readiness, m.gaps, m.detail)
