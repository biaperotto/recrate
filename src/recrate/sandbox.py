"""Run a paper's code confined: no network, and writes only where its run is allowed to write.

ReCRATE runs code it didn't write (the authors' scripts, notebooks, Quarto documents). By default that code runs in
the operating system's own sandbox, around the research object's own environment (so nothing has to be rebuilt in a
container):

* macOS: ``sandbox-exec`` with a profile that denies outbound network (local connections, e.g. to a Jupyter kernel,
  stay allowed) and every file write except the run's output and scratch folders, the temporary folders and tool
  caches (``~/Library/Caches``, ``~/.cache``);
* Linux: ``bwrap`` (bubblewrap): the file system read-only, the run's folders and a private ``/tmp`` writable, no
  network namespace.

The code can still read files, as any program you run can; what it can't do is change or delete them, or send
anything anywhere. A method that needs the network at run time (e.g. to download a model) states it, and the user
allows it with ``RECRATE_SANDBOX_NETWORK=1``.

If no sandbox is available, ReCRATE refuses to run the code rather than run it unconfined. Running unsandboxed is
an explicit choice: ``RECRATE_SANDBOX=off`` (or ``recrate --unsandboxed ...``). Every run crate records which.
"""

from __future__ import annotations

import os
import platform
import shutil
import tempfile
from pathlib import Path

OFF = {"off", "0", "no", "false", "unsandboxed"}
# Tool caches (Quarto's compiled styles, Snakemake, Deno...): writable, as they hold nothing of yours
# (~/.keras: Keras ignores its model cache unless it can write there)
CACHES = [str(Path.home() / "Library" / "Caches"), str(Path.home() / ".cache"), str(Path.home() / ".keras")]


class SandboxUnavailable(RuntimeError):
    pass


def mode() -> str:
    """``auto`` (the default), ``off``, or a backend name."""
    return os.environ.get("RECRATE_SANDBOX", "auto").strip().lower() or "auto"


def network_allowed() -> bool:
    return os.environ.get("RECRATE_SANDBOX_NETWORK", "").strip() == "1"


def backend() -> str | None:
    """The sandbox this computer has, or None."""
    m = mode()
    if m in OFF:
        return None
    if m not in {"auto", "sandbox-exec", "bwrap"}:
        raise SandboxUnavailable(f"Unknown RECRATE_SANDBOX={m!r}: use auto, off, sandbox-exec or bwrap.")
    if m in {"auto", "sandbox-exec"} and platform.system() == "Darwin" and shutil.which("sandbox-exec"):
        return "sandbox-exec"
    if m in {"auto", "bwrap"} and shutil.which("bwrap"):
        return "bwrap"
    return None


def describe() -> str:
    """How code runs here, in one line: recorded in each run crate."""
    b = backend()
    if b is None:
        return "unsandboxed (chosen explicitly: RECRATE_SANDBOX=off)" if mode() in OFF else "no sandbox available"
    net = "network allowed" if network_allowed() else "no network"
    return f"sandboxed with {b}: {net}; writes only to the run's own folders, temporary files and tool caches"


def _real(p: Path | str) -> str:
    return str(Path(p).resolve())


def _sbpl(writable: list[str], network: bool) -> str:
    def q(s: str) -> str:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'

    tmp = {_real(tempfile.gettempdir()), "/private/tmp", "/private/var/folders", *CACHES}
    allow = " ".join(f"(subpath {q(w)})" for w in sorted(set(writable) | tmp))
    return "\n".join([
        "(version 1)",
        "(allow default)",
        *([] if network else ['(deny network-outbound (remote ip))',  # local connections stay (e.g. Jupyter kernels)
                              '(allow network-outbound (remote ip "localhost:*"))']),
        "(deny file-write*)",
        f'(allow file-write* {allow} (literal "/dev/null") (literal "/dev/tty") (regex #"^/dev/fd/"))',
    ])


def wrap(cmd: list[str], writable: list[Path | str]) -> tuple[list[str], dict[str, str]]:
    """The command to run instead of ``cmd``, and environment variables to add. Raises SandboxUnavailable when no
    sandbox is available and running unsandboxed wasn't chosen."""
    b = backend()
    scratch = tempfile.mkdtemp(prefix="recrate-sandbox-")  # tool caches and configs go here, not into your home
    env = {"PYTHONDONTWRITEBYTECODE": "1", "MPLCONFIGDIR": scratch, "JUPYTER_RUNTIME_DIR": scratch,
           "IPYTHONDIR": scratch,
           # Keras keeps its cache in ~/.keras only if it can write to your home folder, else re-downloads into
           # /tmp: say where it is, so a cached model (e.g. StarDist's) is found without the network
           "KERAS_HOME": os.environ.get("KERAS_HOME", str(Path.home() / ".keras"))}
    if b is None:
        if mode() in OFF:
            return list(cmd), env
        raise SandboxUnavailable(
            "ReCRATE runs a paper's code in a sandbox, and none is available here. Install bubblewrap "
            "(e.g. `apt install bubblewrap`) on Linux, or, if you trust this code, run it unsandboxed explicitly: "
            "`recrate --unsandboxed ...` or RECRATE_SANDBOX=off.")
    dirs = [_real(w) for w in writable]
    for d in dirs:
        Path(d).mkdir(parents=True, exist_ok=True)
    if b == "sandbox-exec":
        return ["sandbox-exec", "-p", _sbpl(dirs + [scratch], network_allowed()), *cmd], env
    args = ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp",
            "--die-with-parent"] + ([] if network_allowed() else ["--unshare-net"])
    for d in dirs + [scratch] + [c for c in CACHES if Path(c).is_dir()]:
        args += ["--bind", d, d]
    return [*args, "--", *cmd], env
