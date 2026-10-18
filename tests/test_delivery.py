"""Paper2Agent-style delivery checks: hardware, script languages, workflow state, server check, package."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from recrate import hardware
from recrate.runners import ScriptRunner, get_runner
from recrate.runners.script import interpreter
from recrate.workflow_state import check_state, mark

EX = Path(__file__).resolve().parent.parent / "examples" / "penguins-dimorphism"


def test_hardware_fits():
    have = {"gpus": [{"kind": "Apple MPS"}], "memory_gb": 16, "disk_free_gb": 100,
            "interpreters": {"python": "Python 3", "Rscript": None}}
    assert hardware.fits({"memory_gb": 8, "interpreter": "python"}, have) == []
    lacks = hardware.fits({"gpu": "CUDA", "memory_gb": 64, "disk_gb": 500, "interpreter": "Rscript"}, have)
    assert len(lacks) == 4 and "CUDA" in lacks[0]
    assert hardware.fits({"gpu": "any"}, {**have, "gpus": []})[0].endswith("(none found)")
    assert hardware.probe()["cpus"]


def test_scripts_by_language(tmp_path, monkeypatch):
    assert isinstance(get_runner(language="R", workflow=Path("a.R")), ScriptRunner)
    assert isinstance(get_runner(workflow=Path("run.sh")), ScriptRunner)
    assert isinstance(get_runner(language="Julia", workflow=Path("a.jl")), ScriptRunner)
    monkeypatch.setenv("RECRATE_JULIA", str(tmp_path / "no-such-julia"))
    with pytest.raises(RuntimeError, match="not installed"):
        interpreter(Path("a.jl"))
    with pytest.raises(RuntimeError, match="no runner"):
        get_runner(language="Fortran", workflow=Path("model.f90"))
    monkeypatch.setenv("RECRATE_RSCRIPT", str(tmp_path / "no-such-Rscript"))
    with pytest.raises(RuntimeError, match="RECRATE_RSCRIPT"):
        interpreter(Path("a.R"))


def test_shell_script_runs(tmp_path):
    (tmp_path / "in").mkdir()
    (tmp_path / "in" / "x.txt").write_text("hi")
    wf = tmp_path / "count.sh"
    wf.write_text('while [ $# -gt 0 ]; do case "$1" in --input_dir) I="$2";; --output_dir) O="$2";; esac; shift 2; done\n'
                  'printf "image,n\\nx,%s\\n" "$(ls "$I" | wc -l | tr -d " ")" > "$O/results.csv"\n')
    r = ScriptRunner().run(wf, tmp_path / "in", tmp_path / "out", {"k": 1})
    assert r.results_csv.read_text().splitlines()[1] == "x,1" and r.software[0][0] == "Bash"


def test_workflow_state(tmp_path):
    a = tmp_path / "agent"
    (a / "work").mkdir(parents=True)
    (a / "agent.json").write_text("{}")
    mark(a, "setup", "coordinator")
    with pytest.raises(FileNotFoundError):
        mark(a, "read", "paper-reader")  # work/methods.json not written yet
    (a / "work" / "methods.json").write_text("[]")
    (a / "work" / "environment-review.json").write_text("{}")
    mark(a, "read", "paper-reader", "r1")
    mark(a, "survey", "environment", "e1")
    assert check_state(a, run_gates=False)["next"] == "plan"
    (a / "work" / "verification.json").write_text("{}")
    mark(a, "verify", "verifier", "v1")  # out of order
    r = check_state(a, run_gates=False)
    assert not r["ok"] and any("before" in p for p in r["problems"])
    (a / "work" / "methods.json").write_text("[1]")  # changed after it was recorded
    assert any("changed after" in p for p in check_state(a, run_gates=False)["problems"])


def test_verifier_must_be_independent(tmp_path):
    a = tmp_path / "agent"
    (a / "work").mkdir(parents=True)
    for f in ("agent.json", "work/methods.json", "work/environment-review.json", "crate_description.json",
              "work/verification.json"):
        (a / f).write_text("{}")
    for stage, role, i in [("setup", "coordinator", ""), ("read", "paper-reader", "r"), ("survey", "environment", "e"),
                           ("plan", "coordinator", ""), ("implement", "method-implementer", "m1"),
                           ("assemble", "coordinator", ""), ("check", "coordinator", ""),
                           ("validate", "coordinator", ""), ("verify", "verifier", "m1")]:
        mark(a, stage, role, i)
    assert any("independent" in p for p in check_state(a, run_gates=False)["problems"])


@pytest.mark.skipif(not (EX / "crate").exists(), reason="penguin example not present")
def test_server_check_and_package(tmp_path):
    from recrate.agent import agentify
    from recrate.package import package_agent
    from recrate.server_check import check_server

    agent = agentify(tmp_path / "peng", paper=EX / "paper.pdf", crate=EX / "crate")
    rep = check_server(agent.path)
    assert rep["ok"], rep
    assert json.loads((agent.path / "work" / "server-check.json").read_text())["ok"]
    out = package_agent(agent.path, tmp_path / "peng.zip")
    assert out["ok"] and out["check"]["ok"]
    names = zipfile.ZipFile(out["zip"]).namelist()
    assert "peng/USAGE.md" in names and not any(n.startswith("peng/runs/") for n in names)
    m = json.loads(zipfile.ZipFile(out["zip"]).read("peng/agent.json"))
    assert all(meth["run_crate"] is None for meth in m["methods"])
