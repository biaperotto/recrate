"""Runners for literate documents and workflow engines: Quarto / R Markdown, Jupyter notebooks, Snakemake,
Nextflow and CWL.

Each takes the same contract as a script, in its own idiom, and must leave ``results.csv`` in the output folder:

* **Quarto, R Markdown** (``.qmd``, ``.Rmd``): ``quarto render <doc> -P input_dir:… -P output_dir:… -P <param>:<value>``;
  the document reads ``params$input_dir`` (R) or the injected variables (Python) and writes ``results.csv`` there.
* **Jupyter** (``.ipynb``): ``papermill <nb> <executed nb> -p input_dir … -p output_dir … -p <param> <value>``, in the
  paper's own environment when it has papermill; the notebook has a cell tagged ``parameters``.
* **Snakemake** (``Snakefile``, ``.smk``): ``snakemake --config input_dir=… output_dir=… <param>=<value>``.
* **Nextflow** (``.nf``): ``nextflow run <wf> --input_dir … --output_dir … --<param> <value>``.
* **CWL** (``.cwl``): ``cwltool --outdir <out> <wf> <job>``, the job giving ``input_dir`` as a Directory and each
  parameter by name; ``results.csv`` is taken from the outputs.

The workflow's folder is copied to a scratch folder first, so a render or an engine never writes into the crate.
Everything the run produced besides ``results.csv`` (the executed notebook, the rendered report, engine logs) is
kept as an output of the run crate.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .base import RunResult, Runner, now_iso

TIMEOUT = 6 * 3600


def _version(cmd: list[str]) -> str:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    m = re.search(r"\d+\.\d+(?:\.\d+)*", p.stdout + p.stderr)
    return m.group(0) if m else "unknown"


class EngineRunner(Runner):
    """Common shape: copy the workflow's folder, run the engine's command there, collect results.csv."""

    tool = ""  # the command that must be installed
    software = ""  # its name, as recorded
    version_args: list[str] = ["--version"]
    install_hint = ""

    def available(self) -> bool:
        return shutil.which(self.tool) is not None

    def readiness(self) -> str | None:
        return None if self.available() else f"Needs {self.software} ({self.tool}), not installed here. {self.install_hint}"

    def command(self, workflow: Path, input_dir: Path, output_dir: Path, params: dict[str, Any], work: Path) -> list[str]:
        raise NotImplementedError

    def run(self, workflow: Path, input_dir: Path, output_dir: Path, params: dict[str, Any],
            channel: int = 0, progress=None) -> RunResult:
        if not self.available():
            raise RuntimeError(self.readiness())
        output_dir = Path(output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=f"recrate-{self.name}-") as tmp:
            work = Path(tmp) / "workflow"
            shutil.copytree(Path(workflow).parent, work, ignore=shutil.ignore_patterns(".git", "*_cache", ".snakemake"))
            wf = work / Path(workflow).name
            cmd = self.command(wf, Path(input_dir).resolve(), output_dir, params, work)
            started = now_iso()
            proc = subprocess.run(cmd, cwd=work, capture_output=True, text=True, timeout=TIMEOUT,
                                  env={**os.environ, "RECRATE_OUTPUT_DIR": str(output_dir)})
            ended = now_iso()
            (output_dir / f"{self.name}.log").write_text((proc.stdout + "\n" + proc.stderr)[-200000:], encoding="utf-8")
            if proc.returncode != 0:
                raise RuntimeError(f"{Path(workflow).name} failed in {self.software} (exit {proc.returncode}):\n"
                                   f"{(proc.stderr or proc.stdout)[-2000:]}")
            self.collect(work, output_dir)
        results = output_dir / "results.csv"
        if not results.exists():
            raise RuntimeError(f"{Path(workflow).name} ran in {self.software} but wrote no results.csv in output_dir.")
        extra = [f for f in sorted(output_dir.rglob("*")) if f.is_file() and f.name != "results.csv"]
        return RunResult(results_csv=results, masks_dir=None, started=started, ended=ended, params=dict(params),
                         software=[(self.software, _version([self.tool, *self.version_args]))],
                         log=proc.stdout[-4000:], extra_outputs=extra)

    def collect(self, work: Path, output_dir: Path) -> None:
        """Bring back what the engine left in the scratch folder (e.g. a rendered report). Default: nothing."""


class QuartoRunner(EngineRunner):
    name, tool, software = "quarto", "quarto", "Quarto"
    install_hint = "Install Quarto (quarto.org) and, for R documents, R (cran.r-project.org)."

    def command(self, workflow, input_dir, output_dir, params, work):
        args = ["quarto", "render", str(workflow), "--execute",
                "-P", f"input_dir:{input_dir}", "-P", f"output_dir:{output_dir}"]
        for k, v in params.items():
            args += ["-P", f"{k}:{v}"]
        return args

    def collect(self, work, output_dir):
        for f in work.glob("*.html"):  # the rendered report, as the authors would have seen it
            shutil.copy2(f, output_dir / f.name)


class NotebookRunner(EngineRunner):
    name, tool, software = "notebook", "papermill", "papermill (Jupyter)"
    install_hint = "Install papermill and ipykernel (pip install papermill ipykernel)."
    python: Path | None = None  # the paper's own environment, when it has papermill

    def available(self) -> bool:
        return self._own() is not None or shutil.which("papermill") is not None

    def _own(self) -> list[str] | None:
        if self.python and subprocess.run([str(self.python), "-c", "import papermill, ipykernel"],
                                          capture_output=True).returncode == 0:
            return [str(self.python), "-m", "papermill"]
        return None

    def command(self, workflow, input_dir, output_dir, params, work):
        base = self._own() or ["papermill"]
        args = [*base, str(workflow), str(output_dir / f"executed-{workflow.name}"), "--cwd", str(work),
                "-p", "input_dir", str(input_dir), "-p", "output_dir", str(output_dir)]
        for k, v in params.items():
            args += ["-p", k, str(v)]
        return args


class SnakemakeRunner(EngineRunner):
    name, tool, software = "snakemake", "snakemake", "Snakemake"
    install_hint = "Install Snakemake (conda install -c conda-forge -c bioconda snakemake)."

    def command(self, workflow, input_dir, output_dir, params, work):
        config = [f"input_dir={input_dir}", f"output_dir={output_dir}", *[f"{k}={v}" for k, v in params.items()]]
        return ["snakemake", "-s", str(workflow), "--cores", str(max(1, (os.cpu_count() or 2) - 1)),
                "--directory", str(work), "--config", *config]


class NextflowRunner(EngineRunner):
    name, tool, software = "nextflow", "nextflow", "Nextflow"
    version_args = ["-version"]
    install_hint = "Install Nextflow (conda install -c bioconda nextflow), which brings its own Java."

    def command(self, workflow, input_dir, output_dir, params, work):
        args = ["nextflow", "-q", "run", str(workflow), "-work-dir", str(work / "work"),
                "--input_dir", str(input_dir), "--output_dir", str(output_dir)]
        for k, v in params.items():
            args += [f"--{k}", str(v)]
        return args


class CWLRunner(EngineRunner):
    name, tool, software = "cwl", "cwltool", "cwltool (CWL)"
    install_hint = "Install cwltool (pip install cwltool), and Node.js for workflows with JavaScript expressions."

    def command(self, workflow, input_dir, output_dir, params, work):
        job = {"input_dir": {"class": "Directory", "path": str(input_dir)}, **params}
        (work / "recrate-job.json").write_text(json.dumps(job), encoding="utf-8")
        return ["cwltool", "--quiet", "--outdir", str(output_dir), str(workflow), str(work / "recrate-job.json")]


ENGINES = {"quarto": QuartoRunner, "notebook": NotebookRunner, "snakemake": SnakemakeRunner,
           "nextflow": NextflowRunner, "cwl": CWLRunner}


def engine_for(language: str | None, workflow: Path | None) -> str | None:
    """Which engine runs this workflow, from its language in the crate or its file name."""
    lang = (language or "").lower()
    name = Path(workflow).name.lower() if workflow else ""
    suffix = Path(name).suffix
    if "quarto" in lang or "markdown" in lang or suffix in {".qmd", ".rmd"}:
        return "quarto"
    if "jupyter" in lang or "notebook" in lang or suffix == ".ipynb":
        return "notebook"
    if "snakemake" in lang or name == "snakefile" or suffix == ".smk":
        return "snakemake"
    if "nextflow" in lang or suffix == ".nf":
        return "nextflow"
    if "common workflow language" in lang or re.search(r"\bcwl\b", lang) or suffix == ".cwl":
        return "cwl"
    return None
