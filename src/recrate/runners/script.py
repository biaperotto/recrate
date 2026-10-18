"""Script runner: runs a paper's own code, in Python, R or a shell script.

Contract for a script workflow, the same idea Paper2Agent uses when it wraps
repository code as tools, but declared in the crate instead of inferred:

    <interpreter> <script> --input_dir DIR --output_dir DIR --<param> <value> ...

The script writes ``results.csv`` (one row per input item, first column
``image`` or another key) into ``output_dir``, and optionally ``masks/``.

The interpreter follows the script's suffix (``.py`` Python, ``.R`` Rscript, ``.sh`` bash) and can be set with
``RECRATE_PYTHON``, ``RECRATE_RSCRIPT`` or ``RECRATE_BASH``, so a paper's code can run in its own environment.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from .base import RunResult, Runner, now_iso

# suffix -> (software name, environment variable, default command, how to print its version)
LANGUAGES = {
    ".py": ("Python", "RECRATE_PYTHON", sys.executable, ["-c", "import platform;print(platform.python_version())"]),
    ".r": ("R", "RECRATE_RSCRIPT", "Rscript", ["--version"]),
    ".sh": ("Bash", "RECRATE_BASH", "bash", ["--version"]),
    ".jl": ("Julia", "RECRATE_JULIA", "julia", ["--version"]),
    ".m": ("Octave", "RECRATE_OCTAVE", "octave-cli", ["--version"]),
}


def interpreter(workflow: Path, python: str | Path | None = None) -> tuple[str, str, list[str]]:
    """(software name, command, version arguments) for a script; raises if the interpreter is missing. ``python``:
    the research object's own environment (env.py), used for Python scripts when it was built."""
    suffix = Path(workflow).suffix.lower()
    if suffix not in LANGUAGES:
        raise RuntimeError(f"No interpreter for {Path(workflow).name} (scripts: .py, .R, .sh, .jl, .m).")
    name, var, default, version = LANGUAGES[suffix]
    cmd = str(python) if python and suffix == ".py" else os.environ.get(var, default)
    if not (Path(cmd).exists() or shutil.which(cmd)):
        raise RuntimeError(f"{Path(workflow).name} is {name} code, but {cmd} is not installed here "
                           f"(install it, or set {var} to its path).")
    return name, cmd, version


class ScriptRunner(Runner):
    name = "script"
    python: Path | None = None  # the research object's own environment, when built (env.py)

    def run(self, workflow: Path, input_dir: Path, output_dir: Path, params: dict[str, Any],
            channel: int = 0, progress=None) -> RunResult:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        lang, cmd0, version_args = interpreter(workflow, self.python)
        cmd = [cmd0, str(Path(workflow).resolve()),
               "--input_dir", str(Path(input_dir).resolve()), "--output_dir", str(output_dir.resolve())]
        for k, v in params.items():
            cmd += [f"--{k}", str(v)]
        if channel:
            cmd += ["--channel", str(channel)]
        started = now_iso()
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        ended = now_iso()
        if proc.returncode != 0:
            raise RuntimeError(f"{Path(workflow).name} failed (exit {proc.returncode}):\n{proc.stderr[-2000:]}")
        results = output_dir / "results.csv"
        if not results.exists():
            raise RuntimeError(f"{Path(workflow).name} finished but wrote no results.csv in output_dir.")
        v = subprocess.run([cmd0, *version_args], capture_output=True, text=True)
        m = re.search(r"\d+\.\d+(\.\d+)?", v.stdout + v.stderr)
        version = m.group(0) if m else (platform.python_version() if lang == "Python" else "unknown")
        masks = output_dir / "masks"
        extra, software = [], [(lang, version)]
        total = 0  # every other file the method wrote (e.g. a random split, a fitted model's table), up to 50 MB
        for f in sorted(output_dir.rglob("*")):
            if (f.is_file() and f.name != "results.csv" and not f.is_relative_to(masks)
                    and not f.is_relative_to(output_dir / "figures") and total + f.stat().st_size <= 50 * 2 ** 20):
                total += f.stat().st_size
                extra.append(f)
        if lang == "Python":  # every installed package, as provenance (FAIR4RS R2)
            from ..env import installed_packages

            pkgs = installed_packages(cmd0)
            if pkgs:
                (output_dir / "packages.txt").write_text("\n".join(pkgs) + "\n", encoding="utf-8")
                extra.append(output_dir / "packages.txt")
                software += [tuple(p.split("==", 1)) for p in pkgs][:300]
        return RunResult(
            results_csv=results,
            masks_dir=masks if masks.exists() else None,
            software=software,
            started=started,
            ended=ended,
            params=dict(params),
            log=proc.stdout[-4000:],
            extra_outputs=extra,
        )
