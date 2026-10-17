"""Runners execute a method's workflow. ``auto`` prefers Fiji when configured."""

from __future__ import annotations

from .base import RunResult, Runner
from .fiji import FijiRunner
from .python_ref import PythonReferenceRunner


def get_runner(name: str = "auto") -> Runner:
    if name == "fiji":
        return FijiRunner()
    if name in {"python", "python-reference"}:
        return PythonReferenceRunner()
    if name == "auto":
        fiji = FijiRunner()
        return fiji if fiji.available() else PythonReferenceRunner()
    raise ValueError(f"Unknown runner {name!r}; use auto, fiji or python.")


__all__ = ["RunResult", "Runner", "FijiRunner", "PythonReferenceRunner", "get_runner"]
