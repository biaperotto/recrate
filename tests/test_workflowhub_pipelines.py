"""Real WorkflowHub pipelines end to end, run as their authors run their published test profile.

Never by default: each pulls a crate from WorkflowHub, then test data and tools from the network, and takes minutes to
half an hour, so ``RECRATE_TEST_PIPELINES=1`` is required. nf-core/demo runs with docker or with conda, mamba or
micromamba (each tool's environment built on first use); nf-core/ampliseq needs docker (running). The scripts in
``scripts/workflowhub/`` are the same paths from the command line, and ``.github/workflows/workflowhub-pipeline.yml``
runs any pipeline by id on a runner with docker."""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

DEMO = "https://workflowhub.eu/workflows/1055"
AMPLISEQ = "https://workflowhub.eu/workflows/964"
CONDA = ("conda", "mamba", "micromamba")


def _docker_running() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=60).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


pytestmark = pytest.mark.skipif(
    os.environ.get("RECRATE_TEST_PIPELINES") != "1" or not shutil.which("nextflow"),
    reason="set RECRATE_TEST_PIPELINES=1 (and have nextflow on PATH) to run real pipelines from WorkflowHub")


def _run(tmp_path, url: str, monkeypatch):
    monkeypatch.delenv("RECRATE_OFFLINE", raising=False)  # the crate comes from WorkflowHub
    from recrate.agent import agentify
    from recrate.pipeline import reproduce

    a = agentify(tmp_path / "pipeline", crate=url, validate="none")
    assert a.card()["crate_source"] == "derived from a Nextflow Workflow RO-Crate"
    spec = a.spec()
    assert spec.invocation and spec.invocation["engine"] == "nextflow" and "-profile" in spec.invocation["args"]
    m = a.methods[0]
    assert m.readiness in {"ready", "ready-with-gaps", "reusable"}, m.gaps
    out = reproduce(spec, a.runs_dir, agent="test")
    assert out.ladder and out.ladder["runs"]["status"] == "passed", out.ladder
    rows = (out.run_dir / "outputs" / "results.csv").read_text().splitlines()
    assert rows[0] == "file,sha1,md5,size" and len(rows) > 1  # what the pipeline produced, file by file
    return a, out


def test_nfcore_demo_runs_without_docker_and_matches_its_snapshot(tmp_path, monkeypatch):
    """nf-core/demo: with docker, or with conda/mamba/micromamba (the pipeline's conda profile, environments built by
    Nextflow and cached in ~/.cache/recrate). The nf-test snapshot gives the expected outputs by md5."""
    if not (_docker_running() or any(shutil.which(c) for c in CONDA)):
        pytest.skip("needs docker (running) or conda, mamba or micromamba")
    a, out = _run(tmp_path, DEMO, monkeypatch)
    assert a.spec().expected_results and a.spec().expected_results.exists()  # from tests/default.nf.test.snap
    assert out.comparison is not None and out.comparison.measures
    assert out.ladder["numbers"]["status"] in {"passed", "partial", "failed"}
    assert "test-stated" in out.ladder["numbers"]["reason"]


def test_nfcore_ampliseq_test_profile_runs_and_is_compared_with_its_declared_outputs(tmp_path, monkeypatch):
    if not _docker_running():
        pytest.skip("needs docker (running): ampliseq's test profile is too heavy for conda environments here")
    _, out = _run(tmp_path, AMPLISEQ, monkeypatch)
    if out.comparison and out.comparison.measures:  # the test declares expected outputs: the numbers rung is set
        assert out.ladder["numbers"]["status"] in {"passed", "partial", "failed"}
