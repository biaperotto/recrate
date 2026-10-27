"""Runner interface: execute a method's workflow deterministically.

The AI never computes the numbers. A runner executes the declared workflow
(e.g. a Fiji macro) and reports what it produced and with which software.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class RunResult:
    results_csv: Path
    masks_dir: Path | None
    software: list[tuple[str, str]]
    started: str
    ended: str
    params: dict[str, Any]
    log: str = ""
    extra_outputs: list[Path] = field(default_factory=list)
    sandbox: str = ""  # how the paper's code was confined (sandbox.describe()); empty for ReCRATE's own code


class Runner:
    name = "base"
    spec: Any = None  # the MethodSpec being run, set by pipeline.execute (engines read its invocation recipe)

    def available(self) -> bool:
        return True

    def run(self, workflow: Path, input_dir: Path, output_dir: Path, params: dict[str, Any],
            channel: int = 0, progress=None) -> RunResult:
        """``progress(done, total, name)`` is called per image by runners that can report it."""
        raise NotImplementedError
