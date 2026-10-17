"""Runners execute a method's workflow. The runner is chosen from the workflow's language.

* ImageJ Macro Language → Fiji (PyImageJ, headless). Without Fiji, the demo macro
  falls back to the Python reference implementation.
* Python → the paper's own script, run as a command (see runners/script.py).
* Galaxy → the workflow's imaging tools, ported to Python (see galaxy.py).

Any other language stops with a clear message rather than being run as something it isn't.
"""

from __future__ import annotations

from pathlib import Path

from .base import RunResult, Runner
from .fiji import FijiRunner
from .python_ref import SUPPORTED_WORKFLOWS, PythonReferenceRunner
from .script import ScriptRunner

CHOICES = ["auto", "fiji", "reference", "script", "galaxy"]


def _galaxy() -> Runner:
    from ..galaxy import GalaxyRunner  # local import: galaxy.py imports this package

    return GalaxyRunner()


def get_runner(name: str = "auto", language: str | None = None, workflow: Path | None = None) -> Runner:
    if name == "fiji":
        return FijiRunner()
    if name in {"reference", "python", "python-reference"}:
        return PythonReferenceRunner()
    if name == "script":
        return ScriptRunner()
    if name == "galaxy":
        return _galaxy()
    if name != "auto":
        raise ValueError(f"Unknown runner {name!r}; use one of {', '.join(CHOICES)}.")

    lang = (language or "").lower()
    if "galaxy" in lang or (workflow is not None and Path(workflow).suffix == ".ga"):
        return _galaxy()
    if "python" in lang or (workflow is not None and Path(workflow).suffix == ".py"):
        return ScriptRunner()
    if "imagej" in lang or (workflow is not None and Path(workflow).suffix == ".ijm"):
        fiji = FijiRunner()
        if fiji.available():
            return fiji
        if workflow is not None and Path(workflow).name in SUPPORTED_WORKFLOWS:
            return PythonReferenceRunner()
        raise RuntimeError("This method is a Fiji macro: install recrate[fiji] and set FIJI_PATH.")
    if workflow is not None and Path(workflow).name in SUPPORTED_WORKFLOWS:
        return PythonReferenceRunner()
    raise RuntimeError(f"ReCRATE has no runner for {language or Path(str(workflow)).suffix or 'this workflow'} yet "
                       "(supported: ImageJ macros, Python scripts, Galaxy imaging workflows).")


__all__ = ["RunResult", "Runner", "FijiRunner", "PythonReferenceRunner", "ScriptRunner", "get_runner", "CHOICES"]
