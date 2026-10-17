"""A third field, to show nothing in the core is imaging-specific: a (synthetic) signal-processing method.

The method smooths a signal over ``window_samples`` and counts threshold crossings. Its crate declares the
sampling rate of the paper's recordings (100 Hz) and that the window scales with sampling rate. On recordings at
400 Hz the method still runs, and with the published window it counts about twice the events: the same silent
error as a filter size in pixels on images with smaller pixels. The same core (tolerance sources, the ladder,
scaling rules, the parameter plan) handles it, through the ``timeseries`` profile.
"""

from __future__ import annotations

import csv
import math
import random
from pathlib import Path

from recrate.agent import agentify
from recrate.draft import build_crate
from recrate.model import parameter_plan
from recrate.pipeline import check_inputs, run_on_data

METHOD = '''
import argparse, csv
from pathlib import Path
ap = argparse.ArgumentParser()
ap.add_argument("--input_dir"); ap.add_argument("--output_dir")
ap.add_argument("--window_samples", type=float, default=25); ap.add_argument("--threshold", type=float, default=0.5)
a = ap.parse_args()
out = Path(a.output_dir); out.mkdir(parents=True, exist_ok=True)
w = max(1, round(a.window_samples))
with (out / "results.csv").open("w", newline="") as fh:
    wr = csv.writer(fh); wr.writerow(["recording", "events"])
    for f in sorted(Path(a.input_dir).glob("*.csv")):
        x = [float(r["signal"]) for r in csv.DictReader(f.open())]
        s = [sum(x[max(0, i - w + 1):i + 1]) / len(x[max(0, i - w + 1):i + 1]) for i in range(len(x))]
        wr.writerow([f.stem, sum(1 for p, q in zip(s, s[1:]) if p < a.threshold <= q)])
'''


def _record(folder: Path, rate: float, files: int = 2, seconds: int = 30) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for k in range(files):
        rnd = random.Random(1 + k)
        events = [2 + 2.5 * i + rnd.uniform(-0.3, 0.3) for i in range(11)]
        with (folder / f"rec{k + 1}.csv").open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["time", "signal"])
            for i in range(int(seconds * rate)):
                t = i / rate
                w.writerow([f"{t:.5f}", f"{sum(math.exp(-((t - e) / 0.25) ** 2) for e in events) + rnd.gauss(0, 0.35):.4f}"])


def _agent(tmp: Path):
    (tmp / "count_events.py").write_text(METHOD)
    _record(tmp / "paper-data", 100)
    (tmp / "expected.csv").write_text("recording,events\nrec1,11\nrec2,12\n")
    desc = {"name": "Synthetic event counting", "description": "A synthetic signal-processing method (test only).",
            "identifier": "urn:recrate:test:events", "profile": "timeseries", "methods": [{
                "name": "count_events", "workflow": {"file": "count_events.py", "language": "Python"},
                "inputs": [{"id": "in", "name": "input_dir", "type": "File", "format": "text/csv",
                            "properties": {"sampling rate": {"value": 100, "unit": "Hz"}, "channels": "signal",
                                           "time column": "time"}},
                           {"id": "w", "name": "window_samples", "type": "Float", "default": 25, "unit": "sample",
                            "scaling": "scales_with:sampling rate^1"},
                           {"id": "t", "name": "threshold", "type": "Float", "default": 0.5}],
                "outputs": [{"id": "out", "name": "results", "type": "File", "format": "text/csv", "kind": "table",
                             "abs_tolerances": {"events": {"value": 0, "source": "person", "by": "test",
                                                           "reason": "event counts are integers"}}}],
                "example_data": "paper-data", "expected": "expected.csv"}]}
    build_crate(desc, tmp / "crate", base_dir=tmp)
    return agentify(tmp / "agent", crate=tmp / "crate")


def test_same_core_for_signals(tmp_path):
    a = _agent(tmp_path)
    m = a.methods[0]
    assert m.readiness == "ready" and m.ladder["numbers"] == {"status": "passed",
                                                              "reason": "2/2 values within tolerance (person)."}
    mine = tmp_path / "mine"
    _record(mine, 400)
    check = check_inputs(a.spec(), mine)
    rate = next(i for i in check.items if i.name == "Sampling rate")
    assert rate.status == "warn" and [(x.parameter, x.new) for x in check.adjustments] == [("window_samples", 100.0)]
    plan = parameter_plan(a.spec(), check)
    assert [p["name"] for p in plan["adjusted"]] == ["window_samples"] and "25 × 4^1 = 100" in plan["adjusted"][0]["why"]
    assert [p["name"] for p in plan["as_published"]] == ["threshold"]

    def events(out):
        return [int(r.split(",")[1]) for r in (out.run_dir / "outputs" / "results.csv").read_text().splitlines()[1:]]

    adjusted = events(run_on_data(a.spec(), mine, a.runs_dir, agent="test"))
    published = events(run_on_data(a.spec(), mine, a.runs_dir, agent="test", auto_adjust=False))
    assert all(abs(x - y) <= 1 for x, y in zip(adjusted, [11, 12]))  # same events as the paper's recordings
    assert all(p >= 1.5 * x for p, x in zip(published, adjusted))  # the published window: about twice the events
