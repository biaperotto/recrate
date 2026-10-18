"""Each research object's own software environment, as Paper2Agent builds one per paper.

    recrate env agents/my_paper [--python 3.11]

builds ``<agent>/env``, a virtual environment with the paper's dependencies, from the first of:

* the code's ``requirements*.txt``, ``pyproject.toml`` / ``setup.py`` (the code installed as a package) or the pip and
  plain package lines of an ``environment.yml``;
* otherwise the Python packages ReCRATE found for the paper, at their versions as of publication
  (``environment.json``).

It uses ``uv`` when installed (fast, and it can fetch the Python version asked for), else ``python -m venv`` and pip.
An existing environment is added to, never deleted (``--rebuild`` starts again).

``recrate env <agent> --r`` builds the R side in ``<agent>/env/r`` with micromamba (or conda) from conda-forge: R at the
version the paper states, and every R package the code loads (``r-<name>``). Packages conda-forge doesn't have are
listed in ``env/recrate-env.json`` (``r_missing``) to install from CRAN. R scripts and Quarto / R Markdown documents
then run with this R, not the one on the computer.
Methods written in Python then run with this environment's interpreter, not ReCRATE's, and every run records the full
list of installed packages (``outputs/packages.txt``) next to the run crate. ``env/recrate-env.json`` records how the
environment was built.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_PYTHON = "3.11"  # broad wheel coverage for scientific packages; override with --python
LIST_PACKAGES = ("import importlib.metadata as m;print('\\n'.join(sorted({f\"{d.metadata['Name']}=={d.version}\" "
                 "for d in m.distributions() if d.metadata['Name']})))")


def uv() -> str | None:
    for c in (shutil.which("uv"), Path.home() / ".local" / "bin" / "uv", Path.home() / ".cargo" / "bin" / "uv"):
        if c and Path(c).exists():
            return str(c)
    return None


def env_rscript(agent_dir: str | Path) -> Path | None:
    """The research object's own R, if its R environment was built."""
    p = Path(agent_dir) / "env" / "r" / "bin" / "Rscript"
    return p if p.exists() else None


def _no_index(folder: Path) -> None:
    """Keep macOS Spotlight from indexing an environment (hundreds of thousands of files slow the whole computer)."""
    try:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / ".metadata_never_index").touch()
    except OSError:
        pass


def conda_tool() -> str | None:
    for c in (shutil.which("micromamba"), Path.home() / ".local" / "bin" / "micromamba", shutil.which("mamba"),
              shutil.which("conda")):
        if c and Path(c).exists():
            return str(c)
    return None


def build_r_env(agent_dir: str | Path, log=print) -> dict[str, Any]:
    """``<agent>/env/r``: R as the paper states (else the latest), and the code's R packages, from conda-forge."""
    from .environment import load

    agent_dir = Path(agent_dir).resolve()
    tool = conda_tool()
    if tool is None:
        raise RuntimeError("Building an R environment needs micromamba, mamba or conda (micro.mamba.pm).")
    env = load(agent_dir / "environment.json")
    sw = env.software if env else []
    r_version = next((s.version for s in sw if s.name == "R" and s.version), None)
    pkgs = [s.name for s in sw if s.registry == "cran"]
    prefix = agent_dir / "env" / "r"
    _no_index(agent_dir / "env")
    base = [f"r-base={r_version}" if r_version else "r-base", "r-knitr", "r-rmarkdown"]
    want = [f"r-{p.lower()}" for p in pkgs]
    verb = "install" if (prefix / "conda-meta").exists() else "create"
    missing: list[str] = []
    for _ in range(8):  # drop what conda-forge doesn't have, and try again
        cmd = [tool, verb, "-y", "-p", str(prefix), "-c", "conda-forge", *base, *want]
        log(f"· R environment: {'R ' + r_version if r_version else 'R'} and {len(want)} package(s) from conda-forge")
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
        if proc.returncode == 0:
            break
        out = proc.stdout + proc.stderr
        gone = set(re.findall(r"\b(r-[a-z0-9.]+)\b[^\n]*(?:does not exist|nothing provides|not found|is missing)", out, re.I))
        gone |= {g for g in re.findall(r"(r-[a-z0-9.]+)", out) if g in want and re.search(rf"{re.escape(g)}.*(?:not|no)", out)}
        gone &= set(want)
        if not gone:
            raise RuntimeError("Could not build the R environment:\n" + out[-1500:])
        missing += sorted(gone)
        want = [w for w in want if w not in gone]
    rscript = env_rscript(agent_dir)
    installed = []
    if rscript:
        q = subprocess.run([str(rscript), "-e", "cat(rownames(installed.packages()), sep='\\n')"], capture_output=True,
                           text=True, timeout=300)
        installed = q.stdout.split()
    r_missing = [p for p in pkgs if p not in installed]
    rec_path = agent_dir / "env" / "recrate-env.json"
    rec = json.loads(rec_path.read_text(encoding="utf-8")) if rec_path.exists() else {}
    rec.update(r=str(prefix), r_version=r_version, r_packages=len(installed), r_missing=r_missing)
    rec_path.parent.mkdir(parents=True, exist_ok=True)
    rec_path.write_text(json.dumps(rec, indent=2), encoding="utf-8")
    log(f"R environment: {len(installed)} packages" + (f"; not on conda-forge, install from CRAN: {', '.join(r_missing)}"
                                                       if r_missing else ""))
    return rec


def env_python(agent_dir: str | Path) -> Path | None:
    """The research object's own interpreter, if its environment was built."""
    env = Path(agent_dir) / "env"
    for p in (env / "bin" / "python", env / "Scripts" / "python.exe"):
        if p.exists():
            return p
    return None


def installed_packages(python: str | Path) -> list[str]:
    proc = subprocess.run([str(python), "-c", LIST_PACKAGES], capture_output=True, text=True, timeout=120)
    return [line for line in proc.stdout.splitlines() if "==" in line]


def _sources(agent_dir: Path) -> list[tuple[str, list[str]]]:
    """(what, install arguments) in order of preference."""
    code = agent_dir / "code"
    out: list[tuple[str, list[str]]] = []
    if code.exists():
        reqs = sorted(p for p in code.rglob("requirements*.txt") if ".git" not in p.parts and "test" not in p.name)
        for r in reqs[:3]:
            out.append((f"code/{r.relative_to(code)}", ["-r", str(r)]))
        for f in ("pyproject.toml", "setup.py"):
            if (code / f).exists():
                out.append((f"code/{f} (the code, installed as a package)", ["-e", str(code)]))
                break
        yml = next((p for p in (code / "environment.yml", code / "environment.yaml") if p.exists()), None)
        if yml and not out:
            pkgs = _from_environment_yml(yml)
            if pkgs:
                out.append((f"code/{yml.name} (pip and plain package lines)", pkgs))
    crate = agent_dir / "crate"  # requirements declared with the crate's workflows
    if crate.exists():
        for r in sorted(crate.rglob("requirements*.txt"))[:3]:
            out.append((f"crate/{r.relative_to(crate)}", ["-r", str(r)]))
    if not out:
        env_json = agent_dir / "environment.json"
        if env_json.exists():
            from .environment import load

            env = load(env_json)
            reqs = [line.split("#")[0].strip() for line in env.requirements_txt().splitlines()
                    if line and not line.startswith("#")]
            if reqs:
                out.append(("environment.json (versions as of publication)", reqs))
    return out


def _from_environment_yml(path: Path) -> list[str]:
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out = []
    for d in data.get("dependencies") or []:
        if isinstance(d, dict):
            out += [str(x) for x in d.get("pip") or []]
        elif isinstance(d, str) and not d.startswith(("python", "pip", "r-", "_")):
            out.append(re.sub(r"=(?=[\d*])", "==", d.split("::")[-1]).replace("===", "=="))
    return out


def build_env(agent_dir: str | Path, python: str | None = None, extra: list[str] | None = None,
              log=print, rebuild: bool = False) -> dict[str, Any]:
    """Build ``<agent>/env``, or add to it. Returns the record written to ``env/recrate-env.json``."""
    agent_dir = Path(agent_dir).resolve()
    env = agent_dir / "env"
    if rebuild and env.exists():
        shutil.rmtree(env)
    tool = uv()
    want = python or DEFAULT_PYTHON
    steps: list[dict[str, Any]] = []

    def run(cmd: list[str], what: str) -> bool:
        log(f"· {what}")
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        steps.append({"what": what, "command": " ".join(cmd), "ok": proc.returncode == 0,
                      "output": (proc.stdout + proc.stderr)[-3000:]})
        if proc.returncode != 0:
            log(f"  failed: {(proc.stderr or proc.stdout).strip()[-400:]}")
        return proc.returncode == 0

    if env_python(agent_dir) is not None:  # already there (perhaps with the R side, env/r): add to it
        made = True
        log("· the environment exists: adding to it")
    elif tool:
        made = run([tool, "venv", str(env), "--python", want, "--seed", "--allow-existing"],
                   f"virtual environment with Python {want} (uv)")
    else:
        made = run([sys.executable, "-m", "venv", str(env)], f"virtual environment with {sys.executable}")
    py = env_python(agent_dir)
    _no_index(env)
    if not made or py is None:
        raise RuntimeError("Could not create the environment: " + (steps[-1]["output"][-400:] if steps else ""))

    def install(args: list[str], what: str) -> bool:
        cmd = ([tool, "pip", "install", "--python", str(py)] if tool else [str(py), "-m", "pip", "install"]) + args
        return run(cmd, what)

    sources = _sources(agent_dir)
    for what, args in sources:
        install(args, f"install from {what}")
    if extra:
        install(extra, "install " + " ".join(extra))
    record = {"built": datetime.now(timezone.utc).replace(microsecond=0).isoformat(), "python": str(py),
              "python_version": subprocess.run([str(py), "-c", "import platform;print(platform.python_version())"],
                                               capture_output=True, text=True).stdout.strip(),
              "tool": "uv" if tool else "venv + pip", "from": [w for w, _ in sources] + (extra or []),
              "ok": all(s["ok"] for s in steps), "steps": steps, "packages": installed_packages(py)}
    old = json.loads((env / "recrate-env.json").read_text(encoding="utf-8")) if (env / "recrate-env.json").exists() else {}
    record = {**{k: v for k, v in old.items() if k.startswith("r")}, **record}  # keep what the R side recorded
    (env / "recrate-env.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    log(f"{'Built' if record['ok'] else 'Built with errors'}: Python {record['python_version']}, "
        f"{len(record['packages'])} packages, from {', '.join(record['from']) or 'nothing (no dependencies found)'}")
    return record


def describe_env(agent_dir: str | Path) -> dict[str, Any] | None:
    f = Path(agent_dir) / "env" / "recrate-env.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


