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

A crate imported by recrate.adapters carries an **invocation recipe** (``recrate invocation``): the engine's own
command line (``nextflow run … -profile test,docker``, ``cwltool … job.json``, ``snakemake --configfile …``), which
files the data folder must hold, whether the run needs the network and a container runtime, and how to list what
it produced as ``results.csv``. With a recipe, the runner follows it; without one, the contract above applies.
The runtime is the first of the recipe's ``needs`` that is installed: docker, singularity or apptainer, else conda,
mamba or micromamba, with which a Nextflow pipeline runs its conda profile and builds each tool's environment
(cached in ~/.cache/recrate), so a published pipeline runs on a computer without docker.

The workflow's folder is copied to a scratch folder first, so a render or an engine never writes into the crate.
Everything the run produced besides ``results.csv`` (the executed notebook, the rendered report, engine logs) is
kept as an output of the run crate, and the figures a notebook or a Quarto document drew are written to
``figures/`` with their provenance (recrate.notebook_figures), so they reach the run crate like any other figure.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..remote import Remote

from .. import sandbox
from .base import Runner, RunResult, now_iso

TIMEOUT = 6 * 3600
# Runtimes a published pipeline's recipe may need. The conda family installs each tool into an environment instead
# of pulling a container, so a pipeline runs on a computer without docker: nf-core pipelines have ``conda`` and
# ``mamba`` profiles; micromamba (which ReCRATE already uses for R environments) runs the conda profile with
# ``conda.useMicromamba``.
CONDA_RUNTIMES = {"conda": {}, "mamba": {"useMamba": "true"}, "micromamba": {"useMicromamba": "true"}}
NF_PROFILE = {"micromamba": "conda"}  # the Nextflow profile a runtime selects, when its name differs


def conda_stand_in(folder: Path, runtime: str) -> Path:
    """A ``conda`` command for a run that has only micromamba or mamba: nf-core pipelines call ``conda config --show
    channels`` at start-up (their channel check) and stop when no ``conda`` exists, although Nextflow builds the
    environments with micromamba itself. The stand-in answers that one question with the channels nf-core expects
    and forwards anything else to the real runtime. Returns the folder to put first on PATH."""
    folder.mkdir(parents=True, exist_ok=True)
    shim = folder / "conda"
    shim.write_text("#!/bin/sh\n"
                    "# ReCRATE: stands in for conda where only micromamba/mamba is installed (see recrate.runners.engines)\n"
                    'if [ "$1" = "config" ]; then printf "channels:\\n  - conda-forge\\n  - bioconda\\n"; exit 0; fi\n'
                    f'exec {runtime} "$@"\n', encoding="utf-8")
    shim.chmod(0o755)
    return folder


def conda_cache_dir() -> Path:
    """Where Nextflow keeps the environments it builds (``RECRATE_CONDA_CACHE``, default ~/.cache/recrate/nextflow-conda):
    a tool cache, so the sandbox lets the run write there, and the environments outlive the run."""
    d = Path(os.environ.get("RECRATE_CONDA_CACHE") or Path.home() / ".cache" / "recrate" / "nextflow-conda")
    d.mkdir(parents=True, exist_ok=True)
    return d


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
    env_vars: dict[str, str] = {}  # e.g. which R a Quarto document runs with
    run_env: dict[str, str] = {}  # set per run by a recipe (e.g. where micromamba keeps its packages)
    runtime: str | None = None  # the container or conda runtime a recipe chose for this run
    remote: "Remote | None" = None  # the machine this run executes on, when not this one (recrate.remote)
    stage: Path | None = None  # where files a command needs (configs, jobs) are written before being copied there
    software = ""  # its name, as recorded

    def _runs_here(self, recipe: dict[str, Any]) -> bool:
        """Whether this computer has the engine and one of the runtimes the recipe needs."""
        needs = recipe.get("needs") or []
        return shutil.which(self.tool) is not None and (not needs or any(shutil.which(n) for n in needs))

    def find(self, name: str) -> str | None:
        """Where ``name`` is installed on the machine this run executes on."""
        return self.remote.has(name) if self.remote is not None else shutil.which(name)

    def _stage_file(self, work: Path, name: str, text: str) -> Path:
        """Write a file the command refers to as ``work/name``: here when the run is local, in the staging folder
        (copied over before the run) when it is remote. Returns the path the command uses."""
        dest = (self.stage or work) / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
        return work / name
    version_args: list[str] = ["--version"]
    install_hint = ""

    def available(self) -> bool:
        return shutil.which(self.tool) is not None

    def readiness(self) -> str | None:
        return None if self.available() else f"Needs {self.software} ({self.tool}), not installed here. {self.install_hint}"

    def command(self, workflow: Path, input_dir: Path, output_dir: Path, params: dict[str, Any], work: Path) -> list[str]:
        raise NotImplementedError

    def recipe(self) -> dict[str, Any] | None:
        """The crate's invocation recipe for this engine, when it has one (recrate.adapters)."""
        inv = getattr(self.spec, "invocation", None)
        return inv if isinstance(inv, dict) and inv.get("engine") == self.name else None

    def run(self, workflow: Path, input_dir: Path, output_dir: Path, params: dict[str, Any],
            channel: int = 0, progress=None) -> RunResult:
        from ..remote import Remote

        recipe = self.recipe()
        self.remote = Remote.from_env() if recipe else None  # a recipe can run on another machine
        if self.remote is not None and self.remote.auto and recipe is not None and self._runs_here(recipe):
            self.remote = None  # ReCRATE's VM is for what this computer lacks; this recipe runs here
        if self.remote is not None and self.remote.lima:
            self.remote.ensure_running()
        if self.remote is None and not self.available():
            raise RuntimeError(self.readiness())
        output_dir = Path(output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        network = recipe.get("network") if recipe else None
        self.run_env, self.runtime, self.stage = {}, None, None
        if self.remote is not None and recipe is not None:
            return self.run_remote(Path(workflow), Path(input_dir).resolve(), output_dir, params, recipe)
        with tempfile.TemporaryDirectory(prefix=f"recrate-{self.name}-") as tmp:
            work = Path(tmp) / "workflow"
            root = Path(workflow).parent
            if recipe and recipe.get("root") and self.spec is not None:
                root = Path(self.spec.path) / recipe["root"]
            shutil.copytree(root, work, ignore=shutil.ignore_patterns(".git", "*_cache", ".snakemake"))
            wf = work / Path(workflow).resolve().relative_to(root.resolve())
            before = {p.relative_to(work) for p in work.rglob("*") if p.is_file()}
            if recipe and recipe.get("steps"):  # the authors' test run: several engine calls, in order
                cmds = self.recipe_steps(recipe, Path(input_dir).resolve(), output_dir, params, work, source=work)
            elif recipe:
                cmds = [(None, self.recipe_command(recipe, wf, Path(input_dir).resolve(), output_dir, params, work))]
            else:
                cmds = [(None, self.command(wf, Path(input_dir).resolve(), output_dir, params, work))]
            started = now_iso()
            log = ""
            for preflight, cmd in cmds:
                for what, c in ([("preflight", preflight)] if preflight else []) + [("step", cmd)]:
                    confined, extra_env = sandbox.wrap(c, [work, output_dir], network=network)  # writes only to these
                    proc = subprocess.run(confined, cwd=work, capture_output=True, text=True, timeout=TIMEOUT,
                                          env=sandbox.environment({**self.env_vars, **extra_env, **self.run_env,
                                                                   "RECRATE_OUTPUT_DIR": str(output_dir)}))
                    log += f"$ {' '.join(c)}\n{proc.stdout}\n{proc.stderr}\n"
                    (output_dir / f"{self.name}.log").write_text(log[-200000:], encoding="utf-8")
                    if proc.returncode != 0:
                        detail = (proc.stderr or proc.stdout)[-2000:]
                        engine_log = work / ".nextflow.log"  # Nextflow's own log says what failed (-q keeps stderr short)
                        if engine_log.exists():
                            lines = engine_log.read_text(errors="replace").splitlines()
                            first = next((i for i, ln in enumerate(lines) if "ERROR" in ln), None)
                            detail += "\n" + "\n".join(lines[first:first + 25] if first is not None else lines[-25:])
                        if what == "preflight":
                            raise RuntimeError(f"The workflow's own preflight ({' '.join(c)}) says the run cannot start "
                                               f"(exit {proc.returncode}):\n{detail}")
                        raise RuntimeError(f"{Path(workflow).name} failed in {self.software} (exit {proc.returncode}):\n{detail}")
            ended = now_iso()
            if recipe and recipe.get("collect") == "new-files":  # what the engine left in its own folder (Snakemake)
                for p in sorted(work.rglob("*")):
                    rel = p.relative_to(work)
                    if p.is_file() and rel not in before and rel.parts[0] not in {".snakemake", "work", ".nextflow"} \
                            and p.name != "recrate-config.yaml":
                        (output_dir / rel).parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(p, output_dir / rel)
            else:
                self.collect(work, output_dir)
        results = output_dir / "results.csv"
        if recipe and not results.exists():
            self.recipe_results(recipe, output_dir)
        if not results.exists():
            raise RuntimeError(f"{Path(workflow).name} ran in {self.software} but wrote no results.csv in output_dir.")
        extra = [f for f in sorted(output_dir.rglob("*")) if f.is_file() and f.name != "results.csv"]
        software = [(self.software, _version([self.tool, *self.version_args]))]
        if self.runtime:  # the container or conda runtime the recipe ran with, as provenance
            software.append((self.runtime, _version([self.runtime, "--version"])))
        return RunResult(results_csv=results, masks_dir=None, started=started, ended=ended, params=dict(params),
                         software=software, log=proc.stdout[-4000:], extra_outputs=extra,
                         sandbox=sandbox.describe(network))

    def run_remote(self, workflow: Path, input_dir: Path, output_dir: Path, params: dict[str, Any],
                   recipe: dict[str, Any]) -> RunResult:
        """The recipe on the remote machine: the workflow's folder and the inputs copied there (and kept, so fetched
        data and built environments are reused), the recipe's ``prepare`` commands, then each step's preflight and
        command, run there; what they produced copied back here. Readiness checked that machine's runtimes."""
        remote = self.remote
        assert remote is not None
        root = Path(self.spec.path) / recipe["root"] if recipe.get("root") and self.spec is not None else workflow.parent
        name = re.sub(r"[^A-Za-z0-9_.-]+", "-", Path(self.spec.path).parent.name if self.spec is not None else root.name)
        rroot, stamp = remote.path(name), subprocess.run(["date", "+%Y%m%dT%H%M%S"], capture_output=True, text=True).stdout.strip()
        rwork, rin, rout = f"{rroot}/workflow", f"{rroot}/input", f"{rroot}/out-{stamp}"
        remote.push(root, rwork)
        remote.push(input_dir, rin)
        self.stage = Path(tempfile.mkdtemp(prefix="recrate-stage-"))
        work = Path(rwork)
        rwf = work / workflow.resolve().relative_to(root.resolve())
        if recipe.get("steps"):
            cmds = self.recipe_steps(recipe, Path(rin), Path(rout), params, work, source=root, probe=input_dir)
        else:
            cmds = [(None, self.recipe_command(recipe, rwf, Path(rin), Path(rout), params, work, probe=input_dir))]
        remote.push(self.stage, rwork)  # the configs and job files the commands refer to
        remote.run(["mkdir", "-p", rout], cwd=".")
        collect_new = recipe.get("collect") == "new-files"
        before = remote.listing(rwork) if collect_new else set()
        prepare = [list(c) for c in recipe.get("prepare") or []]
        todo: list[tuple[str, list[str]]] = [("prepare", c) for c in prepare]
        for pre, cmd in cmds:
            todo += ([("preflight", pre)] if pre else []) + [("step", cmd)]
        started, log = now_iso(), ""
        env = {**self.env_vars, **self.run_env, "RECRATE_OUTPUT_DIR": rout}
        extra_args = shlex.split(os.environ.get("RECRATE_SNAKEMAKE_ARGS", ""))
        for what, c in todo:
            if extra_args and what == "step" and c and c[0] == "snakemake":
                c = [*c, *extra_args]  # e.g. a cluster profile: --profile /path/to/nci
            proc = remote.run(c, cwd=rwork, env=env)
            log += f"$ [{remote.host}] {' '.join(c)}\n{proc.stdout}\n{proc.stderr}\n"
            (output_dir / f"{self.name}.log").write_text(log[-200000:], encoding="utf-8")
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout)[-2000:]
                nf = remote.run(["tail", "-n", "25", ".nextflow.log"], cwd=rwork, timeout=120)
                if nf.returncode == 0 and nf.stdout.strip():
                    detail += "\n" + nf.stdout
                where = f" on {remote.host} (in {rwork})"
                if what == "prepare":
                    raise RuntimeError(f"Preparing the run{where} failed ({' '.join(c)}, exit {proc.returncode}):\n{detail}")
                if what == "preflight":
                    raise RuntimeError(f"The workflow's own preflight{where} says the run cannot start "
                                       f"({' '.join(c)}, exit {proc.returncode}):\n{detail}")
                raise RuntimeError(f"{workflow.name} failed in {self.software}{where} (exit {proc.returncode}):\n{detail}")
        ended = now_iso()
        if collect_new:
            new = {r for r in remote.listing(rwork) - before
                   if r.split("/")[0] not in {".snakemake", "work", ".nextflow"} and not r.startswith("recrate-config")}
            remote.pull_files(rwork, new, output_dir)
        else:
            remote.pull(rout, output_dir)
        results = output_dir / "results.csv"
        if not results.exists():
            self.recipe_results(recipe, output_dir)
        if not results.exists():
            raise RuntimeError(f"{workflow.name} ran in {self.software} on {remote.host} but produced nothing to list.")
        extra = [f for f in sorted(output_dir.rglob("*")) if f.is_file() and f.name != "results.csv"]
        software = [(self.software, remote.version([self.tool, *self.version_args]))]
        if self.runtime:
            software.append((self.runtime, remote.version([self.runtime, "--version"])))
        return RunResult(results_csv=results, masks_dir=None, started=started, ended=ended, params=dict(params),
                         software=software, log=log[-4000:], extra_outputs=extra, sandbox=remote.describe(rwork))

    def recipe_command(self, recipe: dict[str, Any], workflow: Path, input_dir: Path, output_dir: Path,
                       params: dict[str, Any], work: Path, probe: Path | None = None) -> list[str]:
        """The recipe's command line with its placeholders filled; writes the job or config file it names.
        ``probe``: where to test whether an input file exists, when ``input_dir`` is a path on another machine."""
        inputs: dict[str, str] = recipe.get("inputs") or {}
        here = probe or input_dir
        runtime = next((r for r in recipe.get("needs") or [] if self.find(r)), (recipe.get("needs") or ["docker"])[0])
        self.runtime = runtime
        fixed = {"{workflow}": str(workflow), "{input_dir}": str(input_dir), "{output_dir}": str(output_dir),
                 "{work}": str(work), "{runtime}": NF_PROFILE.get(runtime, runtime)}

        def fill(a: str) -> str | None:
            for k, v in fixed.items():
                a = a.replace(k, v)
            m = re.fullmatch(r"\{param:(.+)}", a)
            if m:  # a parameter with no value is left to the workflow's own default; booleans as the engine reads them
                v = params.get(m.group(1))
                if v is None or v == "":
                    return None
                return str(v).lower() if isinstance(v, bool) else str(v)
            m = re.fullmatch(r"\{input:(.+)}", a)
            if m:
                f = inputs.get(m.group(1), m.group(1))
                return str(input_dir / f) if (here / f).exists() else None  # absent: the flag and its value are dropped
            return a

        args: list[str] = []
        for a in recipe.get("args") or []:
            if a == "{sdm}":  # Snakemake's software deployment flags, by the runtime this run has
                args += self.deployment_flags()
                continue
            v = fill(a)
            if v is None:
                if args and args[-1].startswith("-"):
                    args.pop()
                continue
            args.append(v)
        if recipe.get("engine") == "nextflow" and runtime in CONDA_RUNTIMES:
            # no container runtime here: Nextflow builds each tool's conda environment instead (the pipeline's
            # conda profile), with micromamba or mamba when that is what is installed, cached across runs
            cache = conda_cache_dir() if self.remote is None else Path(f"{work.parent}/conda-cache")
            opts = {"enabled": "true", "cacheDir": f"'{cache}'", **CONDA_RUNTIMES[runtime]}
            cfg = self._stage_file(work, "recrate-nextflow.config",
                                   "conda {\n" + "".join(f"    {k} = {v}\n" for k, v in opts.items()) + "}\n")
            at = args.index("run") + 1 if "run" in args else 1
            args[at:at] = ["-c", str(cfg)]
            self.run_env = {"NXF_CONDA_CACHEDIR": str(cache), "MAMBA_ROOT_PREFIX": str(cache.parent / "micromamba"),
                            "CONDA_PKGS_DIRS": str(cache.parent / "conda-pkgs")}
            if runtime != "conda" and self.remote is None and not shutil.which("conda"):
                self.run_env["PATH"] = f"{conda_stand_in(cache.parent / 'bin', runtime)}{os.pathsep}{os.environ.get('PATH', '')}"
        if recipe.get("engine") == "cwl":
            job: dict[str, Any] = dict(recipe.get("job_extra") or {})
            for name, f in inputs.items():
                if (here / f).exists():
                    job[name] = {"class": (recipe.get("classes") or {}).get(name, "File"), "path": str(input_dir / f)}
            job.update({k: v for k, v in params.items() if v is not None and v != ""})
            self._stage_file(work, "recrate-job.json", json.dumps(job, indent=1))
        if recipe.get("engine") == "snakemake":
            import yaml

            cfg = json.loads(json.dumps(recipe.get("config") or {}))  # a deep copy

            def put(key: str, value: Any) -> None:  # dotted keys are nested config
                d = cfg
                parts = key.split(".")
                for k in parts[:-1]:
                    d = d.setdefault(k, {})
                d[parts[-1]] = value

            for name, f in inputs.items():
                if (here / f).exists():
                    put(name, str(input_dir / f))
            for k, v in params.items():
                if v is not None and v != "":
                    put(k, v)
            self._stage_file(work, "recrate-config.yaml", yaml.safe_dump(cfg, sort_keys=False))
        return args

    def deployment_flags(self) -> list[str]:
        """How Snakemake deploys each rule's software with the runtime this run chose: containers through
        singularity/apptainer, conda environments through conda or mamba (micromamba is not a Snakemake front end)."""
        r = self.runtime or ""
        if r in {"singularity", "apptainer"}:
            return ["--use-singularity"]
        if r == "conda":
            return ["--use-conda"]
        if r == "mamba":
            return ["--use-conda", "--conda-frontend", "mamba"]
        return []

    def recipe_steps(self, recipe: dict[str, Any], input_dir: Path, output_dir: Path, params: dict[str, Any],
                     work: Path, source: Path | None = None,
                     probe: Path | None = None) -> list[tuple[list[str] | None, list[str]]]:
        """The authors' test run as ``(preflight, command)`` pairs, one per step: each step's published config, with
        the user's inputs and parameter values put in, and the workflow profile and deployment flags the runtime calls
        for. ``source``: where the published configs are read (``work`` itself, or the local copy when ``work`` is on
        another machine); ``probe``: where input files are looked for. Only Snakemake recipes carry steps today."""
        import yaml

        source, here = source or work, probe or input_dir
        needs = recipe.get("needs") or []
        self.runtime = next((r for r in needs if self.find(r)), needs[0] if needs else None)
        inputs: dict[str, str] = recipe.get("inputs") or {}
        out: list[tuple[list[str] | None, list[str]]] = []
        for i, step in enumerate(recipe["steps"], 1):
            cfg: dict[str, Any] = {}
            if step.get("configfile") and (source / step["configfile"]).exists():
                cfg = yaml.safe_load((source / step["configfile"]).read_text(encoding="utf-8", errors="replace")) or {}

            def put(key: str, value: Any) -> None:  # dotted keys are nested config
                d = cfg
                parts = key.split(".")
                for k in parts[:-1]:
                    d = d.setdefault(k, {})
                d[parts[-1]] = value

            for name, f in inputs.items():
                if (here / f).exists() and self._flat_has(cfg, name):
                    put(name, str(input_dir / f))
            for k, v in params.items():
                if v is not None and v != "" and self._flat_has(cfg, k):
                    put(k, v)
            cfg_file = self._stage_file(work, f"recrate-config-{i}.yaml", yaml.safe_dump(cfg, sort_keys=False))
            cmd = ["snakemake", "--snakefile", str(work / step["snakefile"]), "--cores",
                   str(max(1, (os.cpu_count() or 2) - 1)), "--directory", str(work), "--configfile", str(cfg_file),
                   *self.deployment_flags()]
            if step.get("workflow_profile"):
                cmd += ["--workflow-profile", str(work / step["workflow_profile"])]
            pre = [str(work / a) if a.startswith("./") else a for a in step.get("preflight") or []] or None
            out.append((pre, cmd))
        return out

    @staticmethod
    def _flat_has(cfg: dict[str, Any], key: str) -> bool:
        d: Any = cfg
        for k in key.split("."):
            if not isinstance(d, dict) or k not in d:
                return False
            d = d[k]
        return True

    def recipe_results(self, recipe: dict[str, Any], output_dir: Path) -> None:
        """``results.csv`` as the recipe says: a listing of what the run produced, or one of its tables."""
        results = recipe.get("results") or {"kind": "files"}
        if results.get("kind") == "table" and results.get("path"):
            src = output_dir / results["path"]
            if src.exists():
                shutil.copy2(src, output_dir / "results.csv")
            return
        rows = []
        for f in sorted(output_dir.rglob("*")):
            if f.is_file() and f.name not in {"results.csv", f"{self.name}.log"}:
                data = f.read_bytes()  # sha1 as Planemo tests pin files, md5 as nf-test snapshots do
                rows.append(f"{f.relative_to(output_dir).as_posix()},{hashlib.sha1(data).hexdigest()},"
                            f"{hashlib.md5(data).hexdigest()},{f.stat().st_size}\n")
        (output_dir / "results.csv").write_text("file,sha1,md5,size\n" + "".join(rows), encoding="utf-8")

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
        from ..notebook_figures import collect_quarto_figures

        for f in work.glob("*.html"):  # the rendered report, as the authors would have seen it
            shutil.copy2(f, output_dir / f.name)
        collect_quarto_figures(work, output_dir / "figures")  # the plots it drew, so the run crate keeps them


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

    def collect(self, work, output_dir):
        from ..notebook_figures import extract_notebook_images

        for nb in sorted(output_dir.glob("executed-*.ipynb")):  # the plots in its cell outputs, as files
            extract_notebook_images(nb, output_dir / "figures")


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
