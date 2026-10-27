"""Function runner: a method whose workflow is a Python function, not a script.

Paper2Agent wraps a package's own API directly: a tool is a function that calls the authors' code and returns its
result. ReCRATE accepts the same: the crate names the function (``recrate entry``, ``module:function``, next to the
workflow file), and the implementer writes no argparse and no CSV. ``function_wrapper.py`` runs in the paper's own
interpreter (``<agent>/env``, when built), sandboxed like a script, imports the function from the workflow's folder,
the research object's ``code/`` (the authors' repository) and the crate root, calls it with the method's parameters,
and writes its table as ``results.csv`` and its figures into ``figures/``. From there on a function is a method like
any other: compared with the published values, recorded in a run crate, exposed as a typed MCP tool.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from .. import sandbox
from .base import Runner, RunResult
from .script import interpreter, run_command

WRAPPER = Path(__file__).with_name("function_wrapper.py")


def search_path(workflow: Path, spec: Any = None) -> list[str]:
    """Where the function's module is looked for: the workflow file's folder, the research object's ``code/`` (the
    authors' repository, when cloned) and the crate root."""
    paths = [Path(workflow).resolve().parent]
    if spec is not None and getattr(spec, "path", None) is not None:
        crate = Path(spec.path)
        for p in (crate.parent / "code", crate):
            if p.is_dir():
                paths.append(p.resolve())
    out: list[str] = []
    for p in paths:
        if str(p) not in out:
            out.append(str(p))
    return out


def check_entry(entry: str, workflow: Path, python: str | Path | None = None, spec: Any = None) -> str | None:
    """None when the function can be imported with the paper's interpreter (a dry import, sandboxed as a run would
    be, since importing a module runs its code); otherwise why not, naming what is missing."""
    try:
        _, cmd0, _ = interpreter(workflow, python)
    except RuntimeError as e:
        return str(e)
    cmd = [cmd0, str(WRAPPER), "--entry", entry, "--check"]
    for p in search_path(workflow, spec):
        cmd += ["--path", p]
    try:
        confined, extra_env = sandbox.wrap(cmd, [])
        proc = subprocess.run(confined, capture_output=True, text=True, timeout=600, env=sandbox.environment(extra_env))
    except sandbox.SandboxUnavailable as e:
        return str(e)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"Could not check the function {entry!r}: {e}"
    if proc.returncode != 0:
        lines = [ln for ln in proc.stderr.strip().splitlines() if ln.strip()]
        return f"The function {entry!r} (recrate entry) can't be loaded: {lines[-1] if lines else 'no detail'}"
    return None


class FunctionRunner(Runner):
    name = "function"
    python: Path | None = None  # the research object's own environment, when built (env.py)
    entry: str | None = None  # set when no spec carries it (tests, workspace runs)

    def run(self, workflow: Path, input_dir: Path, output_dir: Path, params: dict[str, Any],
            channel: int = 0, progress=None) -> RunResult:
        entry = getattr(self.spec, "entry", None) or self.entry
        if not entry:
            raise RuntimeError("This method names no function to call (no 'recrate entry' in the crate).")
        output_dir = Path(output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        lang, cmd0, version_args = interpreter(workflow, self.python)
        cmd = [cmd0, str(WRAPPER), "--entry", entry, "--input_dir", str(Path(input_dir).resolve()),
               "--output_dir", str(output_dir), "--params", json.dumps(params, default=str)]
        if channel:
            cmd += ["--channel", str(channel)]
        for p in search_path(workflow, self.spec):
            cmd += ["--path", p]
        return run_command(cmd, cmd0, lang, version_args, output_dir, params, f"{Path(workflow).name}:{entry}",
                           extra_env={"MPLBACKEND": "Agg"})  # figures are files; no display is ever needed
