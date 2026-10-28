"""Run a published pipeline where it can run: on another machine, over SSH.

A workflow that needs apptainer, a cluster, or data that lives on one machine (WorkflowHub #2343: singularity
containers, 40 GB of references and DVC-tracked inputs on NCI's Gadi) cannot run on a laptop. With
``RECRATE_REMOTE=user@host`` (and ``RECRATE_REMOTE_DIR``, default ``~/recrate-runs``), or the app's *Run on another
machine* setting, ReCRATE copies the workflow's folder there (rsync), runs the recipe's commands there with that
machine's runtime, and brings what they produced back into the run crate here. Readiness then reflects that machine.
The workflow's folder persists there between runs, so fetched data and built environments are reused.

``RECRATE_REMOTE=local:<folder>`` runs the same path on this computer (a shared filesystem, or the tests), and
``RECRATE_REMOTE=lima:<vm>`` a Linux virtual machine made with Lima (lima-vm.io) on this Mac, reached through Lima's
own SSH configuration: ``limactl start template://apptainer`` gives a VM with apptainer, the container runtime a
Snakemake workflow with ``container:`` rules needs and that macOS itself cannot run. ``RECRATE_SSH`` overrides the
ssh command (e.g. ``ssh -F ~/.ssh/config.gadi``).

The run is not confined by ReCRATE on the other machine; the run crate says where it ran and that it was not.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

TIMEOUT = 6 * 3600
DEFAULT_DIR = "~/recrate-runs"


@dataclass
class Remote:
    spec: str  # user@host, or local:<folder>
    base: str = DEFAULT_DIR  # where the workflows and runs live there
    _has: dict[str, str | None] = field(default_factory=dict, repr=False)

    auto: bool = False  # ReCRATE's own VM (recrate runtime setup), used only when this computer lacks the runtime

    @classmethod
    def from_env(cls) -> Remote | None:
        """RECRATE_REMOTE, else ReCRATE's own Linux VM when `recrate runtime setup` made one (``auto``)."""
        spec = os.environ.get("RECRATE_REMOTE", "").strip()
        if not spec:
            if (Path.home() / ".lima" / "recrate").is_dir():
                return cls("lima:recrate", DEFAULT_DIR, auto=True)
            return None
        base = os.environ.get("RECRATE_REMOTE_DIR", "").strip() or (spec[6:] if spec.startswith("local:") else DEFAULT_DIR)
        return cls(spec, base)

    def ensure_running(self) -> None:
        """A Lima VM that is stopped is started (``limactl start``), so a run after a reboot just works."""
        if self.lima:
            from .runtime_vm import start_vm, vm_status

            if vm_status(self.lima) not in (None, "Running"):
                start_vm(self.lima)

    @property
    def local(self) -> bool:
        return self.spec.startswith("local:")

    @property
    def lima(self) -> str | None:
        """The Lima VM's name, when the remote is one (``lima:<name>``)."""
        return self.spec[5:] if self.spec.startswith("lima:") else None

    @property
    def host(self) -> str:
        return "this computer" if self.local else f"the Lima VM {self.lima}" if self.lima else self.spec

    def ssh(self) -> list[str]:
        """The ssh command for this remote: RECRATE_SSH, Lima's own configuration for a VM, else plain ssh."""
        custom = os.environ.get("RECRATE_SSH", "").strip()
        if custom:
            return shlex.split(custom)
        if self.lima:
            return ["ssh", "-F", str(Path.home() / ".lima" / self.lima / "ssh.config")]
        return ["ssh", "-o", "BatchMode=yes"]

    @property
    def target(self) -> str:
        """What ssh and rsync address: user@host, or Lima's host alias for a VM."""
        return f"lima-{self.lima}" if self.lima else self.spec

    def describe(self, where: str = "") -> str:
        how = "a folder on this computer" if self.local else "a Linux VM on this computer, over SSH" if self.lima else "over SSH"
        return f"ran on {self.host} ({how})" + (f" in {where}" if where else "") + "; not confined by ReCRATE there"

    # ---- paths ------------------------------------------------------------------------------------------
    def path(self, rel: str) -> str:
        return f"{self.base.rstrip('/')}/{rel}"

    def _local(self, p: str) -> Path:
        return Path(os.path.expanduser(p))

    # ---- commands ---------------------------------------------------------------------------------------
    @staticmethod
    def q(s: str) -> str:
        """``s`` quoted for the remote shell; a leading ``~/`` stays expandable (it would be literal in quotes)."""
        return '"$HOME"/' + shlex.quote(s[2:]) if s.startswith("~/") else "$HOME" if s == "~" else shlex.quote(s)

    def run(self, cmd: list[str], cwd: str, env: dict[str, str] | None = None,
            timeout: int = TIMEOUT) -> subprocess.CompletedProcess:
        """Run ``cmd`` there, in ``cwd``, with ``env`` added to the login shell's environment."""
        if self.local:
            return subprocess.run(cmd, cwd=self._local(cwd), capture_output=True, text=True, timeout=timeout,
                                  env={**os.environ, **(env or {})})
        prefix = ("env " + " ".join(f"{k}={self.q(v)}" for k, v in env.items()) + " ") if env else ""
        script = f"cd {self.q(cwd)} && {prefix}{' '.join(self.q(a) for a in cmd)}"
        return subprocess.run([*self.ssh(), self.target, "bash -lc " + shlex.quote(script)],
                              capture_output=True, text=True, timeout=timeout)

    def has(self, name: str) -> str | None:
        """The command's path there, or None (cached for the process)."""
        if name not in self._has:
            if self.local:
                self._has[name] = shutil.which(name)
            else:
                if self.lima:
                    try:
                        self.ensure_running()
                    except RuntimeError:
                        self._has[name] = None
                        return None
                p = self.run(["bash", "-lc", f"command -v {shlex.quote(name)}"], cwd=".", timeout=120)
                self._has[name] = p.stdout.strip() or None if p.returncode == 0 else None
        return self._has[name]

    def version(self, cmd: list[str]) -> str:
        import re

        try:
            p = self.run(cmd, cwd=".", timeout=300)
        except (OSError, subprocess.TimeoutExpired):
            return "unknown"
        m = re.search(r"\d+\.\d+(?:\.\d+)*", p.stdout + p.stderr)
        return m.group(0) if m else "unknown"

    # ---- files ------------------------------------------------------------------------------------------
    def push(self, src: Path, dest: str) -> None:
        """Copy a folder there (added to what is there already; nothing deleted)."""
        if self.local:
            shutil.copytree(src, self._local(dest), dirs_exist_ok=True)
            return
        self._check(subprocess.run([*self.ssh(), self.target, f"mkdir -p {self.q(dest)}"],
                                   capture_output=True, text=True, timeout=300), "mkdir")
        self._check(subprocess.run(["rsync", "-az", "-e", self._rsync_ssh(), f"{src}/", f"{self.target}:{dest}/"],
                                   capture_output=True, text=True, timeout=TIMEOUT), "rsync (push)")

    def pull(self, src: str, dest: Path) -> None:
        if self.local:
            if self._local(src).exists():
                shutil.copytree(self._local(src), dest, dirs_exist_ok=True)
            return
        dest.mkdir(parents=True, exist_ok=True)
        self._check(subprocess.run(["rsync", "-az", "-e", self._rsync_ssh(), f"{self.target}:{src}/", f"{dest}/"],
                                   capture_output=True, text=True, timeout=TIMEOUT), "rsync (pull)")

    def pull_files(self, src: str, rels: set[str], dest: Path) -> None:
        """Copy the named files (paths relative to ``src``) into ``dest``, keeping their paths."""
        if not rels:
            return
        if self.local:
            for r in sorted(rels):
                (dest / r).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(self._local(src) / r, dest / r)
            return
        dest.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", suffix=".list", delete=False) as fh:
            fh.write("".join(f"{r}\n" for r in sorted(rels)))
        self._check(subprocess.run(["rsync", "-az", "-e", self._rsync_ssh(), f"--files-from={fh.name}",
                                    f"{self.target}:{src}/", f"{dest}/"], capture_output=True, text=True, timeout=TIMEOUT),
                    "rsync (pull)")

    def _rsync_ssh(self) -> str:
        return " ".join(shlex.quote(a) for a in self.ssh())

    def listing(self, path: str) -> set[str]:
        """Every file under ``path`` there, as paths relative to it."""
        if self.local:
            root = self._local(path)
            return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()} if root.exists() else set()
        p = self.run(["find", ".", "-type", "f"], cwd=path, timeout=600)
        return {ln[2:] if ln.startswith("./") else ln for ln in p.stdout.splitlines() if ln.strip()} \
            if p.returncode == 0 else set()

    @staticmethod
    def _check(proc: subprocess.CompletedProcess, what: str) -> None:
        if proc.returncode != 0:
            raise RuntimeError(f"{what} to the remote machine failed (exit {proc.returncode}): "
                               f"{(proc.stderr or proc.stdout).strip()[-800:]}")
