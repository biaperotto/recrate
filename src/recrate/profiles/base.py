"""Domain profiles: what your data must match for a method's reuse to be fair.

A profile knows, for one field:

* which properties of the data matter (pixel size, genome build, ...),
* how to read them from the data itself (for imaging: OME / ImageJ metadata),
* how to handle a mismatch: adjust a parameter, warn, or stop and ask,
* after a run, whether the results look like what the paper reported (flagged, never blocked: a big departure
  may be a real effect, or a mistake).

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


FAR = 10.0  # a mean this many times larger or smaller than the paper's is flagged


def rescale(spec: MethodSpec, params: dict[str, Any], prop: str, ratio: float, why: str) -> list[Adjustment]:
    """Adjust every parameter whose scaling rule names data property ``prop``, so it keeps its meaning on data
    where that property is ``ratio`` times the paper's: ``new = old × ratio ** exponent``. Parameters without a
    rule are never changed. Any field uses this: pixel size for images, sampling rate for signals..."""
    out = []
    for p in spec.value_inputs:
        rule, old = p.scaling_rule, params.get(p.name)
        if rule is None or rule[0] != prop or old is None:
            continue
        exp = rule[1]
        new = round(float(old) * ratio ** exp, 4)
        out.append(Adjustment(p.name, old, new,
                              f"{why}; {p.name} scales as {prop}^{exp:g}: {float(old):g} × {ratio:g}^{exp:g} = {new:g}"))
    return out


def _means(path: Path | None) -> dict[str, float]:
    from ..compare import _num, read_results

    if path is None or not Path(path).exists():
        return {}
    rows = read_results(path)
    if not rows:
        return {}
    key = next(iter(rows[0]))
    out = {}
    for c in rows[0]:
        vals = [v for v in (_num(r.get(c)) for r in rows) if v is not None]
        if c != key and vals:
            out[c] = sum(vals) / len(vals)
    return out


class DomainProfile:
    """Base class. Subclasses implement ``inspect`` and ``check``, and may add to ``check_results`` and
    ``diagnose_inputs``."""

    name = "base"
    description = ""

    def check_results(self, spec: MethodSpec, results_csv: Path, report: CheckReport,
                      reference_csv: Path | None = None, pixel_ratio: float | None = None,
                      skip: set[str] | None = None) -> None:
        """After a run on new data: compare the results with what the paper reported (same order of magnitude).
        Adds items to ``report``. ``reference_csv``: the results of a reproduction on the paper's data. ``skip``:
        columns a profile compares in its own way."""
        paper, mine = _means(spec.expected_results), _means(results_csv)
        common = [c for c in paper if c in mine and paper[c] and not c.startswith("n_") and c not in (skip or set())]
        far = []
        for c in common:
            ratio = mine[c] / paper[c]
            if ratio <= 0 or not (1 / FAR <= ratio <= FAR):
                far.append(f"{c} {mine[c]:.4g} vs {paper[c]:.4g} in the paper")
        if far:
            report.items.append(CheckItem("Results vs the paper's", "same order of magnitude", "; ".join(far), WARN,
                                          "Far outside what the paper reported. This may be a real difference in your "
                                          "data, or a sign the method does not fit it: look at the outputs."))
        elif common:
            report.items.append(CheckItem("Results vs the paper's", "same order of magnitude",
                                          ", ".join(f"{c} {mine[c]:.4g}" for c in common), OK))

    def inspect(self, data_path: Path) -> dict[str, Any]:
        raise NotImplementedError

    def check(self, spec: MethodSpec, data_path: Path, params: dict[str, Any] | None = None,
              pixel_ratio: float | None = None) -> CheckReport:
        raise NotImplementedError

    def diagnose_inputs(self, spec: MethodSpec) -> list[dict[str, str]]:
        """Declare-only checks: are the crate properties this profile needs to
        check user data actually present on the method?

        Returns findings as ``{"severity", "message", "fix"}`` dicts (severity is
        ``warning`` or ``info``). It inspects the crate only; it never reads data
        and never runs the method. The default profile declares nothing to check.
        """
        return []
