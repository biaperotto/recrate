"""What this computer can run: processors, memory, GPU, and the interpreters methods need.

Paper2Agent checks for a GPU before it decides what to run; ReCRATE does the same for every method, so a method
that needs more than this computer has is marked *not runnable here* with what it needs, instead of failing
half-way. ``probe()`` is cheap (no imports of heavy libraries) and never raises.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from typing import Any

INTERPRETERS = {  # name -> (command, arguments that print its version)
    "python": ("python3", ["--version"]),
    "Rscript": ("Rscript", ["--version"]),
    "bash": ("bash", ["--version"]),
    "java": ("java", ["-version"]),
    "docker": ("docker", ["--version"]),
    "apptainer": ("apptainer", ["--version"]),
    "nextflow": ("nextflow", ["-version"]),
    "snakemake": ("snakemake", ["--version"]),
    "galaxy (planemo)": ("planemo", ["--version"]),
}


def _out(cmd: list[str], timeout: float = 5) -> str:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (p.stdout + p.stderr).strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def memory_gb() -> float | None:
    try:
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e9, 1)
    except (ValueError, OSError, AttributeError):
        pass
    m = re.search(r"\d+", _out(["sysctl", "-n", "hw.memsize"]))
    return round(int(m.group(0)) / 1e9, 1) if m else None


def gpus() -> list[dict[str, Any]]:
    out = []
    if shutil.which("nvidia-smi"):
        for line in _out(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"]).splitlines():
            name, _, mem = line.partition(",")
            if name.strip():
                out.append({"name": name.strip(), "kind": "CUDA",
                            "memory_gb": round(float(mem) / 1024, 1) if mem.strip().replace(".", "").isdigit() else None})
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        chip = _out(["sysctl", "-n", "machdep.cpu.brand_string"]) or "Apple silicon"
        out.append({"name": chip, "kind": "Apple MPS", "memory_gb": None})  # shares system memory
    return out


def interpreters() -> dict[str, str | None]:
    found: dict[str, str | None] = {}
    for name, (cmd, args) in INTERPRETERS.items():
        path = shutil.which(cmd)
        found[name] = (_out([path, *args]).splitlines() or [path])[0][:80] if path else None
    fiji = os.environ.get("FIJI_PATH")
    found["Fiji"] = fiji if fiji and os.path.exists(fiji) else None
    return found


def probe() -> dict[str, Any]:
    return {"os": f"{platform.system()} {platform.release()} ({platform.machine()})",
            "cpus": os.cpu_count(), "memory_gb": memory_gb(), "gpus": gpus(),
            "disk_free_gb": round(shutil.disk_usage(".").free / 1e9, 1), "interpreters": interpreters()}


def fits(needs: dict[str, Any], have: dict[str, Any] | None = None) -> list[str]:
    """What this computer lacks for a method's needs, e.g. ``{"gpu": "CUDA", "memory_gb": 64, "interpreter":
    "Rscript", "disk_gb": 500}``. Empty when it fits."""
    have = have or probe()
    missing = []
    gpu = needs.get("gpu")
    if gpu:
        kinds = {g["kind"] for g in have["gpus"]}
        if not kinds or (isinstance(gpu, str) and gpu not in kinds and gpu.lower() not in {"any", "yes", "true"}):
            missing.append(f"a {gpu if isinstance(gpu, str) else ''} GPU".replace("  ", " ") + " (none found)"
                           if not kinds else f"a {gpu} GPU (found {', '.join(sorted(kinds))})")
    if needs.get("memory_gb") and have["memory_gb"] and float(needs["memory_gb"]) > have["memory_gb"]:
        missing.append(f"{needs['memory_gb']} GB of memory (this computer has {have['memory_gb']})")
    if needs.get("disk_gb") and float(needs["disk_gb"]) > have["disk_free_gb"]:
        missing.append(f"{needs['disk_gb']} GB of free disk ({have['disk_free_gb']} free)")
    for name in [needs["interpreter"]] if isinstance(needs.get("interpreter"), str) else needs.get("interpreter") or []:
        if not have["interpreters"].get(name):
            missing.append(f"{name} (not installed)")
    return missing


def describe(h: dict[str, Any]) -> str:
    gpu = "; ".join(f"{g['name']} ({g['kind']}" + (f", {g['memory_gb']} GB)" if g.get("memory_gb") else ")")
                    for g in h["gpus"]) or "none"
    lines = [f"System:  {h['os']}", f"CPUs:    {h['cpus']}", f"Memory:  {h['memory_gb']} GB",
             f"GPU:     {gpu}", f"Disk:    {h['disk_free_gb']} GB free", "Interpreters:"]
    lines += [f"  {'✓' if v else '·'} {k}: {v or 'not found'}" for k, v in h["interpreters"].items()]
    return "\n".join(lines)
