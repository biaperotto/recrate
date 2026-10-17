"""Script runner: runs a paper's own Python (or other command-line) code.

Contract for a script workflow, the same idea Paper2Agent uses when it wraps
repository code as tools, but declared in the crate instead of inferred:

    python <script> --input_dir DIR --output_dir DIR --<param> <value> ...

The script writes ``results.csv`` (one row per input item, first column
``image`` or another key) into ``output_dir``, and optionally ``masks/``.

The interpreter comes from ``RECRATE_PYTHON`` (default: the current Python), so
a paper's code can run in its own environment.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

from .base import RunResult, Runner, now_iso


class ScriptRunner(Runner):
    name = "script"

    def run(self, workflow: Path, input_dir: Path, output_dir: Path, params: dict[str, Any],
            channel: int = 0, progress=None) -> RunResult:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        python = os.environ.get("RECRATE_PYTHON", sys.executable)
        cmd = [python, str(Path(workflow).resolve()),
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
        version = subprocess.run([python, "-c", "import platform;print(platform.python_version())"],
                                 capture_output=True, text=True).stdout.strip() or platform.python_version()
        masks = output_dir / "masks"
        return RunResult(
            results_csv=results,
            masks_dir=masks if masks.exists() else None,
            software=[("Python", version)],
            started=started,
            ended=ended,
            params=dict(params),
            log=proc.stdout[-4000:],
        )
