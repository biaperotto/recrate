"""One Linux layer for every workflow that needs containers: ReCRATE's own VM, made with Lima.

macOS cannot run Linux containers, and most published pipelines run their tools in them (Nextflow with docker or
singularity, Snakemake with apptainer, CWL with docker). ``recrate runtime setup`` makes a small Linux virtual machine
once, in user space with no administrator rights: Lima (lima-vm.io, from its release tarball, into ``~/.local``), an
Ubuntu image, and inside it docker, apptainer, Nextflow, Snakemake and cwltool. From then on a workflow whose runtime
this computer lacks runs in that VM on its own (recrate.remote: ``lima:recrate``), its outputs coming back into the
run crate here; one that runs here directly still does. ``recrate runtime status`` says what is there,
``recrate runtime remove`` deletes the VM, and deleting ``~/.local/bin/limactl`` and ``~/.local/share/lima`` removes
Lima itself.
"""

from __future__ import annotations

import io
import json
import os
import platform
import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path
from typing import Any

VM = "recrate"
RELEASES = "https://api.github.com/repos/lima-vm/lima/releases/latest"
TOOLS = ["docker", "apptainer", "nextflow", "snakemake", "cwltool", "dvc", "rsync"]
# What the VM gets, as its provisioning script: Ubuntu's packages for docker and Java (Nextflow), apptainer from
# Lima's template, the Python engines for this user, Nextflow from its installer.
PROVISION = r"""
set -e
sudo apt-get update -q
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q docker.io python3-pip openjdk-17-jre-headless rsync git curl
sudo usermod -aG docker "$USER"
mkdir -p ~/.local/bin
pip install --user --break-system-packages -q snakemake cwltool "dvc[ssh]" 2>/dev/null || pip install --user -q snakemake cwltool "dvc[ssh]"
cd ~/.local/bin && curl -fsSL https://get.nextflow.io | bash >/dev/null && chmod +x nextflow
grep -q 'local/bin' ~/.profile || echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.profile
"""


def local_bin() -> Path:
    return Path.home() / ".local" / "bin"


def limactl() -> str | None:
    """Lima's command, on PATH or where ``setup`` puts it."""
    return shutil.which("limactl") or (str(local_bin() / "limactl") if (local_bin() / "limactl").exists() else None)


def install_lima(dest: Path | None = None, fetch=None) -> Path:
    """Lima from its latest release tarball, unpacked into ``dest`` (default ~/.local: bin/limactl, share/lima).
    ``fetch(url) -> bytes`` is the downloader (urllib by default)."""
    dest = dest or Path.home() / ".local"
    fetch = fetch or _fetch
    tag = json.loads(fetch(RELEASES).decode("utf-8"))["tag_name"]
    arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x86_64"}.get(platform.machine(), platform.machine())
    system = {"Darwin": "Darwin", "Linux": "Linux"}[platform.system()]
    url = f"https://github.com/lima-vm/lima/releases/download/{tag}/lima-{tag.lstrip('v')}-{system}-{arch}.tar.gz"
    data = fetch(url)
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        tar.extractall(dest, filter="data")
    exe = dest / "bin" / "limactl"
    if not exe.exists():
        raise RuntimeError(f"Lima's tarball ({url}) held no bin/limactl.")
    exe.chmod(exe.stat().st_mode | 0o111)
    return exe


def _fetch(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "recrate"}), timeout=600) as r:
        return r.read()


def vm_status(name: str = VM, run=None) -> str | None:
    """``Running``, ``Stopped``..., or None when the VM does not exist (or Lima is missing)."""
    exe = limactl()
    if exe is None:
        return None
    run = run or subprocess.run
    p = run([exe, "list", "--json"], capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        return None
    for line in p.stdout.splitlines():
        try:
            vm = json.loads(line)
        except ValueError:
            continue
        if vm.get("name") == name:
            return str(vm.get("status"))
    return None


def create_vm(name: str = VM, cpus: int = 4, memory_gb: int = 8, disk_gb: int = 100, run=None,
              log=print) -> None:
    """The VM from Lima's apptainer template, then its tools (PROVISION). Idempotent: an existing VM is started."""
    exe = limactl()
    if exe is None:
        raise RuntimeError("Lima is not installed: run `recrate runtime setup`.")
    run = run or subprocess.run
    status = vm_status(name, run)
    if status is None:
        log(f"Making the VM {name} (Ubuntu, {cpus} CPUs, {memory_gb} GB RAM, {disk_gb} GB disk; a few minutes)…")
        p = run([exe, "start", "--name", name, "--cpus", str(cpus), "--memory", str(memory_gb), "--disk", str(disk_gb),
                 "--tty=false", "template://apptainer"], capture_output=True, text=True, timeout=3600)
        if p.returncode != 0:
            raise RuntimeError(f"limactl start failed (exit {p.returncode}):\n{(p.stderr or p.stdout)[-2000:]}")
    elif status != "Running":
        start_vm(name, run)
    log("Installing docker, Nextflow, Snakemake, cwltool and dvc in the VM (a few minutes)…")
    p = run([exe, "shell", name, "--", "bash", "-lc", PROVISION], capture_output=True, text=True, timeout=3600)
    if p.returncode != 0:
        raise RuntimeError(f"Provisioning the VM failed (exit {p.returncode}):\n{(p.stderr or p.stdout)[-2000:]}")
    # docker group membership applies to new logins: restart once so every later ssh session has it
    run([exe, "stop", name], capture_output=True, text=True, timeout=600)
    start_vm(name, run)


def start_vm(name: str = VM, run=None) -> None:
    exe = limactl()
    if exe is None:
        raise RuntimeError("Lima is not installed: run `recrate runtime setup`.")
    run = run or subprocess.run
    p = run([exe, "start", "--tty=false", name], capture_output=True, text=True, timeout=1800)
    if p.returncode != 0:
        raise RuntimeError(f"limactl start {name} failed (exit {p.returncode}):\n{(p.stderr or p.stdout)[-1500:]}")


def remove_vm(name: str = VM, run=None) -> bool:
    exe = limactl()
    if exe is None or vm_status(name, run) is None:
        return False
    run = run or subprocess.run
    p = run([exe, "delete", "--force", name], capture_output=True, text=True, timeout=600)
    return p.returncode == 0


def status(name: str = VM) -> dict[str, Any]:
    """Lima, the VM and which tools it has (asked over its SSH when it runs)."""
    from .remote import Remote

    out: dict[str, Any] = {"lima": limactl(), "vm": name, "status": vm_status(name), "tools": {}}
    if out["status"] == "Running":
        r = Remote(f"lima:{name}")
        out["tools"] = {t: r.has(t) for t in TOOLS}
    return out


def describe(s: dict[str, Any]) -> str:
    if not s["lima"]:
        return "No Linux runtime VM: Lima is not installed. `recrate runtime setup` makes one (user space, no admin rights)."
    if s["status"] is None:
        return f"Lima is installed ({s['lima']}) but the VM {s['vm']} does not exist: `recrate runtime setup`."
    lines = [f"VM {s['vm']}: {s['status']} (Lima at {s['lima']})"]
    if s["tools"]:
        have = [t for t, p in s["tools"].items() if p]
        missing = [t for t, p in s["tools"].items() if not p]
        lines.append("  tools there: " + (", ".join(have) or "none") + (f"; missing: {', '.join(missing)}" if missing else ""))
        lines.append("  Workflows that need docker or apptainer run in it on their own (RECRATE_REMOTE unset).")
    return "\n".join(lines)


def setup(cpus: int = 4, memory_gb: int = 8, disk_gb: int = 100, log=print) -> dict[str, Any]:
    """Lima (if missing), the VM (if missing) and its tools; then the status."""
    if limactl() is None:
        log("Installing Lima into ~/.local (about 100 MB, from its release tarball)…")
        exe = install_lima()
        os.environ["PATH"] = f"{exe.parent}{os.pathsep}{os.environ.get('PATH', '')}"
    create_vm(cpus=cpus, memory_gb=memory_gb, disk_gb=disk_gb, log=log)
    return status()
