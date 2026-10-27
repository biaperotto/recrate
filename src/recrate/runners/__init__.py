"""Runners execute a method's workflow. The runner is chosen from the workflow's language.

* ImageJ Macro Language → Fiji (PyImageJ, headless). Without Fiji, the demo macro
  falls back to the Python reference implementation.
* Python, R, shell, Julia, Octave → the paper's own script, run as a command (see runners/script.py).
* A Python function named by the crate (``recrate entry``, ``module:function``) → called in the paper's own
  interpreter with the method's parameters, its table written as results.csv (see runners/function.py).
* Quarto / R Markdown, Jupyter notebooks, Snakemake, Nextflow, CWL → their engines (see runners/engines.py).
* Galaxy → the workflow's imaging tools, ported to Python (see galaxy.py), or, when a tool isn't ported and a
  Galaxy server is configured (``GALAXY_URL``, ``GALAXY_API_KEY``), the server itself through its API
  (see runners/galaxy_api.py), so any Galaxy workflow runs.

Any other language stops with a clear message rather than being run as something it isn't.
"""

from __future__ import annotations

import re
from pathlib import Path

from .base import Runner, RunResult
from .engines import ENGINES, engine_for
from .fiji import FijiRunner
from .function import FunctionRunner
from .galaxy_api import HOW_TO, GalaxyApiRunner
from .python_ref import SUPPORTED_WORKFLOWS, PythonReferenceRunner
from .script import ScriptRunner

CHOICES = ["auto", "fiji", "reference", "script", "function", "galaxy", "galaxy-api", *ENGINES]


def _galaxy() -> Runner:
    from ..galaxy import GalaxyRunner  # local import: galaxy.py imports this package

    return GalaxyRunner()


def _galaxy_unported(workflow: Path | None) -> list[str]:
    """The tools a Galaxy workflow's results need that ReCRATE has not ported (empty when it can run here)."""
    from ..galaxy import load_workflow, unported_tools

    if workflow is None or not Path(workflow).exists():
        return []
    try:
        return unported_tools(load_workflow(Path(workflow)))
    except (OSError, ValueError, KeyError):
        return []


def get_runner(name: str = "auto", language: str | None = None, workflow: Path | None = None,
               entry: str | None = None) -> Runner:
    """``entry``: the crate's ``recrate entry`` (``module:function``), which makes the workflow a function to call."""
    if name == "fiji":
        return FijiRunner()
    if name in {"reference", "python", "python-reference"}:
        return PythonReferenceRunner()
    if name == "script":
        return ScriptRunner()
    if name == "function":
        return FunctionRunner()
    if name == "galaxy":
        return _galaxy()
    if name == "galaxy-api":
        return GalaxyApiRunner()
    if name in ENGINES:
        return ENGINES[name]()
    if name != "auto":
        raise ValueError(f"Unknown runner {name!r}; use one of {', '.join(CHOICES)}.")

    if entry:
        runner = FunctionRunner()
        runner.entry = entry
        return runner
    lang = (language or "").lower()
    if "galaxy" in lang or (workflow is not None and Path(workflow).suffix == ".ga"):
        # the ported tools when every tool the results need is ported; else a Galaxy server, when one is configured
        if _galaxy_unported(workflow) and GalaxyApiRunner().available():
            return GalaxyApiRunner()
        return _galaxy()
    engine = engine_for(language, workflow)  # before scripts: "R Markdown" is not an R script
    if engine:
        return ENGINES[engine]()
    words = set(re.findall(r"[a-z]+", lang))
    if words & {"python", "r", "rscript", "bash", "shell", "sh", "julia", "octave", "matlab"} or \
            (workflow is not None and Path(workflow).suffix.lower() in {".py", ".r", ".sh", ".jl", ".m"}):
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
                       "(supported: Python, R, shell, Julia and Octave scripts; Quarto and R Markdown; Jupyter "
                       "notebooks; Snakemake, Nextflow and CWL workflows; ImageJ macros; Galaxy imaging workflows).")


def readiness(language: str | None, workflow: Path | None, python: Path | None = None,
              rscript: Path | None = None, invocation: dict | None = None, entry: str | None = None,
              spec=None) -> str | None:
    """None when this computer can run the workflow; otherwise why not, naming what is missing.

    Built from the runners actually available: a Galaxy workflow is ready when every tool its results need is
    ported, or else when a Galaxy server is configured to run it; a script only when its interpreter is installed;
    a function (``entry``) only when it can be imported with that interpreter (``spec`` says where its module is
    looked for); and an engine workflow only when ReCRATE knows how to call it: it follows the ``--input_dir``
    contract, or the crate carries an invocation recipe (``invocation``, from recrate.adapters) whose container
    runtime, if it needs one, is installed.
    """
    try:
        runner = get_runner("auto", language, workflow, entry=entry)
    except RuntimeError as e:
        return str(e)
    if workflow is None or not Path(workflow).exists():
        return "The workflow file is missing."
    if runner.name == "function":
        from .function import check_entry

        return check_entry(str(entry), Path(workflow), python, spec)
    if isinstance(runner, GalaxyApiRunner):
        return runner.readiness()
    if runner.name == "galaxy":
        missing = _galaxy_unported(workflow)
        if missing:
            from .galaxy_api import bioblend_missing

            return ("Galaxy tool(s) not ported to ReCRATE yet: " + ", ".join(missing) + ". To run the workflow on a "
                    f"Galaxy server instead, {HOW_TO}" + (f"; {bioblend_missing()}" if bioblend_missing() else "") + ".")
    if runner.name == "script":
        from .script import interpreter

        try:
            interpreter(Path(workflow), python, rscript)
        except RuntimeError as e:
            return str(e)
    if runner.name in ENGINES:
        if runner.name == "notebook":
            runner.python = python
        why = runner.readiness()
        if why:
            return why
        import shutil

        if runner.name == "quarto" and re.search(r"```\{r[ ,}]", Path(workflow).read_text(errors="replace")):
            if not (rscript or shutil.which("Rscript")):  # an R document also needs R
                return "Needs R (Rscript), not installed here: install R (cran.r-project.org)."
        if invocation:
            needs = invocation.get("needs") or []
            if needs and not any(shutil.which(n) for n in needs):
                return (f"Needs a container runtime ({', '.join(needs)}) to run as its authors do; none is installed "
                        "here.")
        elif "input_dir" not in Path(workflow).read_text(errors="replace"):
            return (f"No adapter knows how to call this {runner.software} workflow: it does not take "
                    "--input_dir/--output_dir and the crate has no invocation recipe.")
    return None


__all__ = ["RunResult", "Runner", "FijiRunner", "FunctionRunner", "PythonReferenceRunner", "ScriptRunner",
           "get_runner", "readiness", "CHOICES"]
