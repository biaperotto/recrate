"""ReCRATE's own Linux VM for workflows that need containers (``recrate runtime setup``): Lima from its release
tarball into user space, one VM with docker and apptainer and the engines, used on its own when this computer lacks
the runtime. Lima is not run here: its commands are faked and checked."""

from __future__ import annotations

import io
import json
import subprocess
import tarfile
from pathlib import Path

from recrate import runtime_vm
from recrate.remote import Remote


def _tarball() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = b"#!/bin/sh\necho limactl\n"
        info = tarfile.TarInfo("bin/limactl")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
        info = tarfile.TarInfo("share/lima/templates/apptainer.yaml")
        info.size = 3
        tar.addfile(info, io.BytesIO(b"x: 1"[:3]))
    return buf.getvalue()


def test_lima_is_installed_from_its_release_into_user_space(tmp_path, monkeypatch):
    urls = []

    def fetch(url):
        urls.append(url)
        return json.dumps({"tag_name": "v1.2.3"}).encode() if url == runtime_vm.RELEASES else _tarball()

    monkeypatch.setattr(runtime_vm.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(runtime_vm.platform, "system", lambda: "Darwin")
    exe = runtime_vm.install_lima(tmp_path / ".local", fetch=fetch)
    assert exe == tmp_path / ".local" / "bin" / "limactl" and exe.stat().st_mode & 0o111
    assert urls[1] == "https://github.com/lima-vm/lima/releases/download/v1.2.3/lima-1.2.3-Darwin-arm64.tar.gz"
    assert (tmp_path / ".local" / "share" / "lima" / "templates" / "apptainer.yaml").exists()


def test_the_vm_is_made_from_the_apptainer_template_and_provisioned(tmp_path, monkeypatch):
    calls = []
    state = {"vms": {}}

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[1] == "list":
            out = "\n".join(json.dumps({"name": n, "status": st}) for n, st in state["vms"].items())
            return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")
        if cmd[1] == "start":
            state["vms"][cmd[-1] if cmd[2] != "--name" else cmd[3]] = "Running"
        if cmd[1] == "stop":
            state["vms"][cmd[2]] = "Stopped"
        if cmd[1] == "delete":
            state["vms"].pop(cmd[-1], None)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(runtime_vm, "limactl", lambda: "/fake/limactl")
    assert runtime_vm.vm_status("recrate", fake_run) is None
    runtime_vm.create_vm(cpus=2, memory_gb=6, disk_gb=50, run=fake_run, log=lambda *_: None)
    start = next(c for c in calls if c[1] == "start" and "--name" in c)
    assert start[3] == "recrate" and start[start.index("--memory") + 1] == "6" and start[-1] == "template://apptainer"
    shell = next(c for c in calls if c[1] == "shell")
    assert shell[2] == "recrate" and "apt-get install" in shell[-1] and "snakemake" in shell[-1] and "get.nextflow.io" in shell[-1]
    assert [c[1] for c in calls[-2:]] == ["stop", "start"]  # restarted once, so the docker group applies to logins
    assert runtime_vm.vm_status("recrate", fake_run) == "Running"
    # a second setup only starts what exists and provisions again (idempotent scripts)
    n = len(calls)
    runtime_vm.create_vm(run=fake_run, log=lambda *_: None)
    assert not any(c[1] == "start" and "--name" in c for c in calls[n:])
    assert runtime_vm.remove_vm(run=fake_run) and runtime_vm.vm_status("recrate", fake_run) is None


def test_describe_says_what_to_do(monkeypatch):
    monkeypatch.setattr(runtime_vm, "limactl", lambda: None)
    assert "recrate runtime setup" in runtime_vm.describe(runtime_vm.status())
    monkeypatch.setattr(runtime_vm, "limactl", lambda: "/fake/limactl")
    monkeypatch.setattr(runtime_vm, "vm_status", lambda name=runtime_vm.VM, run=None: None)
    assert "does not exist" in runtime_vm.describe(runtime_vm.status())
    text = runtime_vm.describe({"lima": "/fake/limactl", "vm": "recrate", "status": "Running",
                                "tools": {"docker": "/usr/bin/docker", "apptainer": None}})
    assert "tools there: docker" in text and "missing: apptainer" in text


def test_the_vm_is_the_default_remote_only_for_what_this_computer_lacks(tmp_path, monkeypatch):
    """With the VM made, RECRATE_REMOTE unset: a recipe this computer can run stays here; one it cannot goes to the
    VM. An explicit RECRATE_REMOTE always wins."""
    from recrate.runners.engines import SnakemakeRunner

    monkeypatch.delenv("RECRATE_REMOTE", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert Remote.from_env() is None
    (tmp_path / ".lima" / "recrate").mkdir(parents=True)
    r = Remote.from_env()
    assert r and r.auto and r.spec == "lima:recrate"
    monkeypatch.setenv("RECRATE_REMOTE", "me@host")
    assert not Remote.from_env().auto
    runner = SnakemakeRunner()
    monkeypatch.setattr(runtime_vm.shutil, "which", lambda n: "/usr/bin/x" if n in {"snakemake", "docker"} else None)
    import recrate.runners.engines as eng

    monkeypatch.setattr(eng.shutil, "which", lambda n: "/usr/bin/x" if n in {"snakemake", "docker"} else None)
    assert runner._runs_here({"needs": ["docker", "apptainer"]}) and runner._runs_here({"needs": []})
    assert not runner._runs_here({"needs": ["apptainer"]})


def test_the_cli_has_the_runtime_command(monkeypatch, capsys):
    from recrate.cli import main

    monkeypatch.setattr(runtime_vm, "limactl", lambda: None)
    assert main(["runtime", "status"]) == 0
    assert "recrate runtime setup" in capsys.readouterr().out
    monkeypatch.setattr(runtime_vm, "remove_vm", lambda: False)
    assert main(["runtime", "remove"]) == 0 and "No VM" in capsys.readouterr().out
