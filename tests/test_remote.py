"""Run a pipeline where it can run: on another machine over SSH (RECRATE_REMOTE), with its runtimes counting for
readiness and its outputs coming back into the run crate. Tested with the ``local:<folder>`` transport, which runs
the same path on this computer, and with the SSH command composition checked without a network."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from test_adapters import two_step_snakemake_crate  # noqa: E402 - the two-stage Snakemake crate with run_test.sh

from recrate.agent import agentify
from recrate.remote import Remote
from recrate.runners import readiness


def test_the_remote_is_read_from_the_environment(monkeypatch):
    monkeypatch.delenv("RECRATE_REMOTE", raising=False)
    assert Remote.from_env() is None
    monkeypatch.setenv("RECRATE_REMOTE", "me@gadi.nci.org.au")
    r = Remote.from_env()
    assert r and r.spec == "me@gadi.nci.org.au" and r.base == "~/recrate-runs" and not r.local and r.host == r.spec
    monkeypatch.setenv("RECRATE_REMOTE_DIR", "/scratch/a56/me/recrate")
    assert Remote.from_env().path("x/workflow") == "/scratch/a56/me/recrate/x/workflow"
    monkeypatch.setenv("RECRATE_REMOTE", "local:/tmp/here")
    monkeypatch.delenv("RECRATE_REMOTE_DIR")
    r = Remote.from_env()
    assert r.local and r.base == "/tmp/here" and "this computer" in r.describe("/tmp/here/w")


def test_ssh_commands_are_composed_safely(monkeypatch):
    """What goes over SSH: a login shell, cd into the folder, the environment, the command, each argument quoted."""
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="/usr/bin/apptainer\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    r = Remote("me@host", "~/recrate-runs")
    r.run(["snakemake", "--snakefile", "work flow/Snakefile", "--cores", "2"], cwd="~/recrate-runs/x/workflow",
          env={"RECRATE_OUTPUT_DIR": "~/recrate-runs/x/out"})
    cmd = calls[-1]
    assert cmd[:4] == ["ssh", "-o", "BatchMode=yes", "me@host"] and cmd[4].startswith("bash -lc ")
    script = cmd[4]
    assert 'cd "$HOME"/recrate-runs/x/workflow && ' in script and "'work flow/Snakefile'" in script  # ~ expands there
    assert 'env RECRATE_OUTPUT_DIR="$HOME"/recrate-runs/x/out snakemake' in script
    assert r.has("apptainer") == "/usr/bin/apptainer" and r.has("apptainer") == "/usr/bin/apptainer"  # cached
    assert len([c for c in calls if "command -v apptainer" in str(c)]) == 1
    r.push(Path("/tmp/src"), "~/recrate-runs/x/workflow")
    assert calls[-1][0] == "rsync" and calls[-1][-1] == "me@host:~/recrate-runs/x/workflow/"
    assert calls[-2][-1] == 'mkdir -p "$HOME"/recrate-runs/x/workflow'


@pytest.mark.skipif(not shutil.which("snakemake"), reason="needs snakemake")
def test_a_pipeline_runs_on_the_remote_machine_and_its_outputs_come_back(tmp_path, monkeypatch):
    """The two-stage Snakemake test run, executed on the "remote" (a folder here): the workflow is copied there and
    kept, the prepare and preflight steps run there, the outputs come back, and the run crate says where it ran."""
    c = two_step_snakemake_crate(tmp_path)
    a = agentify(tmp_path / "agent", crate=c, online=False, validate="none")
    remote_dir = tmp_path / "gadi"
    monkeypatch.setenv("RECRATE_REMOTE", f"local:{remote_dir}")
    monkeypatch.setenv("RECRATE_SNAKEMAKE_ARGS", "--quiet")
    spec = a.spec()
    assert readiness(spec.workflow_language, spec.workflow_path, invocation=spec.invocation) is None
    from recrate.pipeline import reproduce

    out = reproduce(spec, a.runs_dir, agent="test")
    assert out.ladder["runs"]["status"] == "passed", out.ladder
    assert (out.run_dir / "outputs" / "results" / "total.txt").read_text().strip() == "30"  # (1+2+3) * 5, from there
    there = remote_dir / "agent" / "workflow"
    assert (there / "results" / "total.txt").exists() and (there / "recrate-config-1.yaml").exists()  # kept there
    log = (out.run_dir / "outputs" / "snakemake.log").read_text()
    assert "[this computer] " in log and "preflight ok: config/test_qc.yaml" in log and "--quiet" in log
    meta = json.loads((out.run_dir / "ro-crate-metadata.json").read_text())
    action = next(e for e in meta["@graph"] if e["@id"] == "#run")
    assert "ran on this computer" in action["description"] and "not confined by ReCRATE there" in action["description"]
    assert "prepare" not in spec.invocation  # no DVC pointers in this crate: nothing to fetch before the run


def test_readiness_counts_the_remote_machines_runtimes_and_says_how_to_set_one(tmp_path, monkeypatch):
    c = two_step_snakemake_crate(tmp_path)
    (c / "workflow" / "Snakefile_imp").write_text((c / "workflow" / "Snakefile_imp").read_text().replace(
        '    output: "results/total.txt"', '    container: "docker://x/y"\n    output: "results/total.txt"'))
    a = agentify(tmp_path / "agent", crate=c, online=False, validate="none")
    spec = a.spec()
    real_which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda n: None if n in {"singularity", "apptainer"} else real_which(n))
    monkeypatch.delenv("RECRATE_REMOTE", raising=False)
    why = readiness(spec.workflow_language, spec.workflow_path, invocation=spec.invocation)
    assert why and "none is installed here" in why and "RECRATE_REMOTE=user@host" in why and "recrate runtime setup" in why
    monkeypatch.setenv("RECRATE_REMOTE", f"local:{tmp_path / 'remote'}")  # the "remote" is this computer: still none
    why = readiness(spec.workflow_language, spec.workflow_path, invocation=spec.invocation)
    assert why and "nor on this computer" in why and "RECRATE_REMOTE" not in why
    monkeypatch.setattr(Remote, "has", lambda self, name: "/usr/bin/apptainer" if name in {"apptainer", "snakemake"} else None)
    assert readiness(spec.workflow_language, spec.workflow_path, invocation=spec.invocation) is None  # it runs there


def test_a_lima_vm_is_reached_through_limas_ssh_config(monkeypatch):
    """``lima:apptainer``: ssh and rsync use ~/.lima/apptainer/ssh.config and the host alias lima-apptainer; RECRATE_SSH
    overrides the ssh command for any remote."""
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.delenv("RECRATE_SSH", raising=False)
    r = Remote("lima:apptainer", "~/recrate-runs")
    assert r.lima == "apptainer" and r.host == "the Lima VM apptainer" and "Linux VM on this computer" in r.describe()
    r.run(["snakemake", "--version"], cwd="~/recrate-runs")
    assert calls[-1][:3] == ["ssh", "-F", str(Path.home() / ".lima" / "apptainer" / "ssh.config")] and calls[-1][3] == "lima-apptainer"
    r.push(Path("/tmp/src"), "~/recrate-runs/x/workflow")
    assert calls[-1][-1] == "lima-apptainer:~/recrate-runs/x/workflow/" and "ssh.config" in calls[-1][3]
    monkeypatch.setenv("RECRATE_SSH", "ssh -F /etc/ssh/gadi.conf")
    Remote("me@gadi", "~/r").run(["true"], cwd="~/r")
    assert calls[-1][:4] == ["ssh", "-F", "/etc/ssh/gadi.conf", "me@gadi"]


def test_the_cli_takes_the_remote_machine(monkeypatch, capsys):
    import os

    from recrate.cli import main

    monkeypatch.setenv("RECRATE_REMOTE", "")  # recorded, so what main() sets is undone after the test
    monkeypatch.setenv("RECRATE_REMOTE_DIR", "")
    assert main(["--remote", "me@host", "--remote-dir", "/scratch/me", "hardware"]) == 0
    assert os.environ["RECRATE_REMOTE"] == "me@host" and os.environ["RECRATE_REMOTE_DIR"] == "/scratch/me"
