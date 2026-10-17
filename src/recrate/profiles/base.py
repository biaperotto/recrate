"""Domain profiles: what your data must match for a method's reuse to be fair.

A profile knows, for one field:

* which properties of the data matter (pixel size, genome build, ...),
* how to read them from the data itself (for imaging: OME / ImageJ metadata),
* how to handle a mismatch: adjust a parameter, warn, or stop and ask.

The framework is general; each field plugs in its own profile.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..crate import MethodSpec

OK, WARN, FAIL = "ok", "warn", "fail"


@dataclass
class CheckItem:
    name: str
    expected: Any
    observed: Any
    status: str  # ok | warn | fail
    note: str = ""


@dataclass
class Adjustment:
    parameter: str
    old: Any
    new: Any
    reason: str


@dataclass
class CheckReport:
    profile: str
    data_path: Path
    items: list[CheckItem] = field(default_factory=list)
    adjustments: list[Adjustment] = field(default_factory=list)
    observed: dict[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> str:
        statuses = {i.status for i in self.items}
        if FAIL in statuses:
            return FAIL
        if WARN in statuses:
            return WARN
        return OK

    @property
    def can_run(self) -> bool:
        return self.status != FAIL

    def adjusted_params(self, params: dict[str, Any]) -> dict[str, Any]:
        new = dict(params)
        for a in self.adjustments:
            new[a.parameter] = a.new
        return new

    def as_rows(self) -> list[dict[str, Any]]:
        return [
            {"check": i.name, "paper": i.expected, "your data": i.observed, "status": i.status, "note": i.note}
            for i in self.items
        ]


class DomainProfile:
    """Base class. Subclasses implement ``inspect`` and ``check``."""

    name = "base"
    description = ""

    def inspect(self, data_path: Path) -> dict[str, Any]:
        raise NotImplementedError

    def check(self, spec: MethodSpec, data_path: Path, params: dict[str, Any] | None = None) -> CheckReport:
        raise NotImplementedError

    def diagnose_inputs(self, spec: MethodSpec) -> list[dict[str, str]]:
        """Declare-only checks: are the crate properties this profile needs to
        check user data actually present on the method?

        Returns findings as ``{"severity", "message", "fix"}`` dicts (severity is
        ``warning`` or ``info``). It inspects the crate only; it never reads data
        and never runs the method. The default profile declares nothing to check.
        """
        return []
