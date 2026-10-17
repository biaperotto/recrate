"""Each research object's own software environment, as Paper2Agent builds one per paper.

    recrate env agents/my_paper [--python 3.11]

builds ``<agent>/env``, a virtual environment with the paper's dependencies, from the first of:

* the code's ``requirements*.txt``, ``pyproject.toml`` / ``setup.py`` (the code installed as a package) or the pip and
  plain package lines of an ``environment.yml``;
* otherwise the Python packages ReCRATE found for the paper, at their versions as of publication
  (``environment.json``).

It uses ``uv`` when installed (fast, and it can fetch the Python version asked for), else ``python -m venv`` and pip.
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
              log=print) -> dict[str, Any]:
    """Build (or rebuild) ``<agent>/env``. Returns the record written to ``env/recrate-env.json``."""
    agent_dir = Path(agent_dir).resolve()
    env = agent_dir / "env"
    if env.exists():
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

    if tool:
        made = run([tool, "venv", str(env), "--python", want, "--seed"], f"virtual environment with Python {want} (uv)")
    else:
        made = run([sys.executable, "-m", "venv", str(env)], f"virtual environment with {sys.executable}")
    py = env_python(agent_dir)
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
    (env / "recrate-env.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    log(f"{'Built' if record['ok'] else 'Built with errors'}: Python {record['python_version']}, "
        f"{len(record['packages'])} packages, from {', '.join(record['from']) or 'nothing (no dependencies found)'}")
    return record


def describe_env(agent_dir: str | Path) -> dict[str, Any] | None:
    f = Path(agent_dir) / "env" / "recrate-env.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


