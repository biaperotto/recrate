"""A real WorkflowHub pipeline end to end: nf-core/ampliseq's published test profile, run as its authors run it.

Needs docker (running) and nextflow on PATH, the network, and ``RECRATE_TEST_PIPELINES=1``: it pulls containers and
test data and takes 10 to 30 minutes, so it never runs by default. ``scripts/workflowhub/ampliseq_test.sh`` is the
same path from the command line."""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

AMPLISEQ = "https://workflowhub.eu/workflows/964"
MISSING = [c for c in ("docker", "nextflow") if not shutil.which(c)]


def _docker_running() -> bool:
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=60).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


pytestmark = pytest.mark.skipif(
    bool(MISSING) or os.environ.get("RECRATE_TEST_PIPELINES") != "1",
    reason=(f"needs {', '.join(MISSING)} on PATH; " if MISSING else "") + "set RECRATE_TEST_PIPELINES=1 to run a "
    "real pipeline (nf-core/ampliseq's test profile: containers, test data, 10 to 30 minutes)")


def test_nfcore_ampliseq_test_profile_runs_and_is_compared_with_its_declared_outputs(tmp_path, monkeypatch):
    if not _docker_running():
        pytest.skip("docker is installed but not running")
    monkeypatch.delenv("RECRATE_OFFLINE", raising=False)  # the crate comes from WorkflowHub
    from recrate.agent import agentify
    from recrate.pipeline import reproduce

    a = agentify(tmp_path / "ampliseq", crate=AMPLISEQ, validate="none")
    assert a.card()["crate_source"] == "derived from a Nextflow Workflow RO-Crate"
    spec = a.spec()
    assert spec.invocation and spec.invocation["engine"] == "nextflow" and "-profile" in spec.invocation["args"]
    m = a.methods[0]
    assert m.readiness in {"ready", "ready-with-gaps"}, m.gaps
    out = reproduce(spec, a.runs_dir, agent="test")
    assert out.ladder and out.ladder["runs"]["status"] == "passed", out.ladder
    rows = (out.run_dir / "outputs" / "results.csv").read_text().splitlines()
    assert rows[0] == "file,sha1,size" and len(rows) > 1  # what the pipeline produced, file by file
    if out.comparison and out.comparison.measures:  # the test declares expected outputs: the numbers rung is set
        assert out.ladder["numbers"]["status"] in {"passed", "partial", "failed"}
