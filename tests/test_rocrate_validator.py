"""Run crates validate against the profiles they declare, checked by the community's RO-Crate validator.

Every REQUIRED check of RO-Crate 1.1, Workflow RO-Crate 1.0, Process Run Crate 0.5 and Workflow Run Crate 0.5
must pass, for a reproduction (with its comparison, verdict ladder, a recorded figure and a person's review of the
conclusion) and a reuse (with an adjusted parameter and the checks of its results).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from recrate.agent import agentify
from recrate.figures import make_run_figure
from recrate.pipeline import reproduce, run_on_data
from recrate.runcrate import review_conclusion

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "workflowhub-nuclei-segmentation"
VALIDATOR = shutil.which("rocrate-validator") or str(Path(sys.executable).parent / "rocrate-validator")
pytestmark = pytest.mark.skipif(not Path(VALIDATOR).exists() or not (EXAMPLE / "crate").exists(),
                                reason="needs rocrate-validator (pip install roc-validator) and the example crate")


def validate(run_dir: Path, out: Path) -> dict:
    subprocess.run([VALIDATOR, "validate", "-p", "workflow-run-crate-0.5", "--no-paging", "-f", "json",
                    "-o", str(out), str(run_dir)], capture_output=True, text=True, timeout=600)
    return json.loads(out.read_text())


def test_run_crates_validate(tmp_path):
    a = agentify(tmp_path / "agent", crate=EXAMPLE / "crate", validate="none")
    runs = [reproduce(a.spec(), a.runs_dir, agent="test").run_dir,
            run_on_data(a.spec(), EXAMPLE / "research-data" / "B2", a.runs_dir, agent="test", pixel_ratio=2).run_dir]
    make_run_figure(runs[0])
    review_conclusion(runs[0], "Test Person", "yes", "same count")
    for i, run in enumerate(runs):
        r = validate(run, tmp_path / f"report-{i}.json")
        problems = [f"{x.get('check', {}).get('identifier')}: {x.get('message')}" for x in r.get("issues", [])]
        assert r["passed"], f"{run.name}:\n" + "\n".join(problems)
        assert {"workflow-run-crate-0.5", "process-run-crate-0.5", "ro-crate-1.1"} <= set(r["statistics"]["profiles"])
