"""Compare a run's results with the paper's reported results.

Tolerances are per measurement and come from the crate
(``tolerance:<column>`` on the output parameter), e.g. count ±5 %, area ±10 %. For values a paper rounds
(e.g. to two decimals), ``tolerance_abs:<column>`` checks every item within an absolute margin instead.
Each tolerance carries its source (``tolerance_source:<column>``, see model.py), shown next to every value.
Rows are matched on the first column (``image`` for images; a species, a sample, a gene...).
The verdict is on dataset-level means, as papers usually report; per-image
differences are listed for inspection.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from statistics import mean
from typing import Any

DEFAULT_TOLERANCE = 0.05


def read_results(path: Path) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _num(x: Any) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def summarise(rows: list[dict[str, str]], columns: list[str] | None = None) -> dict[str, float]:
    """Mean of each numeric column, plus the number of images."""
    if not rows:
        return {"images": 0}
    key = "image" if "image" in rows[0] else next(iter(rows[0]))
    cols = columns or [c for c in rows[0] if c != key and _num(rows[0][c]) is not None]
    out: dict[str, float] = {"images": len(rows)}
    for c in cols:
        vals = [v for v in (_num(r.get(c)) for r in rows) if v is not None]
        if vals:
            out[c] = round(mean(vals), 4)
    return out


@dataclass
class MeasureComparison:
    measure: str
    reported: float
    reproduced: float
    rel_diff: float  # relative difference, or the absolute one when ``absolute``
    tolerance: float
    absolute: bool = False
    source: str | None = None  # where the tolerance comes from (model.TOLERANCE_SOURCES)

    @property
    def within(self) -> bool:
        return self.rel_diff <= self.tolerance + 1e-12

    @property
    def tolerance_label(self) -> str:
        t = f"±{self.tolerance:g}" if self.absolute else f"{self.tolerance:.0%}"
        return f"{t}, {self.source}" if self.source else f"{t}, no source"

    def describe(self) -> str:
        if self.absolute:
            return (f"{self.measure} {self.reproduced:g} vs {self.reported:g} reported "
                    f"(±{self.rel_diff:.3g}, tolerance {self.tolerance_label})")
        return (f"{self.measure} {self.reproduced:g} vs {self.reported:g} reported "
                f"({self.rel_diff:.1%}, tolerance {self.tolerance_label})")


@dataclass
class Comparison:
    measures: list[MeasureComparison] = field(default_factory=list)
    per_image: list[dict[str, Any]] = field(default_factory=list)
    missing_images: list[str] = field(default_factory=list)

    @property
    def reproduced(self) -> bool:
        return bool(self.measures) and all(m.within for m in self.measures) and not self.missing_images

    @property
    def count(self) -> str:
        """``9/12``: values within tolerance, of those compared."""
        return f"{sum(1 for m in self.measures if m.within)}/{len(self.measures)}"

    def headline(self) -> str:
        if not self.measures:
            return "Nothing to compare: no reported results."
        # A difference is a finding about the published result, said as such: which values, by how much.
        bad = [m for m in self.measures if not m.within]
        n = len(self.measures)
        if any(m.absolute for m in self.measures):
            if not bad:
                return f"Reproduced: {n} of {n} values within tolerance"
            return (f"Found {len(bad)} of {n} published values that the research's own data doesn't give "
                    + (f"(the other {n - len(bad)} reproduce within tolerance): " if len(bad) < n else ": ")
                    + "; ".join(m.describe() for m in bad))
        if not bad and not self.missing_images:
            return "Reproduced: " + "; ".join(m.describe() for m in self.measures)
        return "Found a difference from the published result: " + "; ".join(m.describe() for m in self.measures)


def round_as_printed(value: float, printed: str) -> float:
    """``value`` rounded half-up to as many decimals as ``printed`` shows (how a paper's table rounds)."""
    dec = len(printed.strip().split(".")[1]) if "." in printed else 0
    return float(Decimal(str(value)).quantize(Decimal(1).scaleb(-dec), rounding=ROUND_HALF_UP))


def compare_results(produced: Path, expected: Path, tolerances: dict[str, float],
                    abs_tolerances: dict[str, float] | None = None, sources: dict[str, str | None] | None = None,
                    rounded: bool = False) -> Comparison:
    """``sources``: column -> where its tolerance comes from. ``rounded``: round each reproduced item as the
    paper prints it before comparing (a follow-up check of the rounding convention)."""
    got = read_results(produced)
    want = read_results(expected)
    abs_tolerances = abs_tolerances or {}
    sources = sources or {}
    key = "image" if "image" in want[0] else next(iter(want[0]))
    cols = list(tolerances) or [c for c in want[0] if c != key and c not in abs_tolerances
                                and _num(want[0][c]) is not None]
    comp = Comparison()

    got_by = {r.get(key): r for r in got}
    for w in want:
        g = got_by.get(w[key])
        if g is None:
            comp.missing_images.append(w[key])
            continue
        for c, tol in abs_tolerances.items():  # every item, each within an absolute margin
            a, b = _num(w.get(c)), _num(g.get(c))
            if a is not None and b is not None:
                if rounded:
                    b = round_as_printed(b, w[c])
                comp.measures.append(MeasureComparison(f"{c} · {w[key]}", a, b, abs(b - a), tol, absolute=True,
                                                       source=sources.get(c)))
        row: dict[str, Any] = {key: w[key]}
        for c in cols:
            a, b = _num(w.get(c)), _num(g.get(c))
            if a is not None and b is not None:
                row[c] = {"reported": a, "reproduced": b,
                          "rel_diff": abs(b - a) / abs(a) if a else float(b != a)}
        comp.per_image.append(row)

    if abs_tolerances and not tolerances:
        return comp
    rep, pro = summarise(want, cols), summarise(got, cols)
    for c in cols:
        if c in rep and c in pro:
            a, b = rep[c], pro[c]
            comp.measures.append(MeasureComparison(
                measure=c, reported=a, reproduced=b,
                rel_diff=abs(b - a) / abs(a) if a else float(b != a),
                tolerance=tolerances.get(c, DEFAULT_TOLERANCE), source=sources.get(c),
            ))
    return comp
