"""Fiji runner: executes an ImageJ macro headless through PyImageJ.

Setup:
    pip install "recrate[fiji]"
    export FIJI_PATH=/path/to/Fiji.app      # a local Fiji installation

The macro receives the crate's parameters as SciJava script parameters
(``#@ File input_dir``, ``#@ Double min_size_px`` ...), plus ``input_dir`` and
``output_dir``. Fiji's version is recorded in the run crate.

Note: some GUI-only commands fail headless; write macros with
``setBatchMode(true)``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .base import RunResult, Runner, now_iso

_IJ = None  # PyImageJ gateway; starting the JVM is slow, so do it once


def _gateway():
    global _IJ
    if _IJ is None:
        import imagej  # type: ignore

        fiji = os.environ.get("FIJI_PATH")
        if not fiji:
            raise RuntimeError("Set FIJI_PATH to your Fiji.app folder to use the Fiji runner.")
        _IJ = imagej.init(fiji, mode="headless")
    return _IJ


class FijiRunner(Runner):
    name = "fiji"

    def available(self) -> bool:
        if not os.environ.get("FIJI_PATH"):
            return False
        try:
            import imagej  # type: ignore # noqa: F401
        except ImportError:
            return False
        return True

    def run(self, workflow: Path, input_dir: Path, output_dir: Path, params: dict[str, Any],
            channel: int = 0) -> RunResult:
        ij = _gateway()
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        macro = Path(workflow).read_text(encoding="utf-8")
        args = {"input_dir": str(Path(input_dir).resolve()), "output_dir": str(output_dir.resolve()), **params}
        started = now_iso()
        ij.py.run_macro(macro, args)
        ended = now_iso()
        results = output_dir / "results.csv"
        if not results.exists():
            raise RuntimeError("The macro finished but wrote no results.csv; check the macro's output path.")
        masks = output_dir / "masks"
        return RunResult(
            results_csv=results,
            masks_dir=masks if masks.exists() else None,
            software=[("Fiji", str(ij.getVersion()))],
            started=started,
            ended=ended,
            params=dict(params),
        )
