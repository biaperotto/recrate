"""Compare a run's results with the paper's reported results.

Tolerances are per measurement and come from the crate
(``tolerance:<column>`` on the output parameter), e.g. count ±5 %, area ±10 %.
The verdict is on dataset-level means, as papers usually report; per-image
differences are listed for inspection.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
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
    cols = columns or [c for c in rows[0] if c != "image" and _num(rows[0][c]) is not None]
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
    rel_diff: float
    tolerance: float

    @property
    def within(self) -> bool:
        return self.rel_diff <= self.tolerance


@dataclass
class Comparison:
    measures: list[MeasureComparison] = field(default_factory=list)
    per_image: list[dict[str, Any]] = field(default_factory=list)
    missing_images: list[str] = field(default_factory=list)

    @property
    def reproduced(self) -> bool:
        return bool(self.measures) and all(m.within for m in self.measures) and not self.missing_images

    def headline(self) -> str:
        if not self.measures:
            return "Nothing to compare: no reported results."
        parts = [f"{m.measure} {m.reproduced:g} vs {m.reported:g} reported "
                 f"({m.rel_diff:.1%}, tolerance {m.tolerance:.0%})" for m in self.measures]
        status = "Reproduced" if self.reproduced else "Differs"
        return f"{status}: " + "; ".join(parts)


def compare_results(produced: Path, expected: Path, tolerances: dict[str, float]) -> Comparison:
    got = read_results(produced)
    want = read_results(expected)
    cols = list(tolerances) or [c for c in want[0] if c != "image" and _num(want[0][c]) is not None]
    comp = Comparison()

    got_by = {r["image"]: r for r in got}
    for w in want:
        g = got_by.get(w["image"])
        if g is None:
            comp.missing_images.append(w["image"])
            continue
        row: dict[str, Any] = {"image": w["image"]}
        for c in cols:
            a, b = _num(w.get(c)), _num(g.get(c))
            if a is not None and b is not None:
                row[c] = {"reported": a, "reproduced": b,
                          "rel_diff": abs(b - a) / abs(a) if a else float(b != a)}
        comp.per_image.append(row)

    rep, pro = summarise(want, cols), summarise(got, cols)
    for c in cols:
        if c in rep and c in pro:
            a, b = rep[c], pro[c]
            comp.measures.append(MeasureComparison(
                measure=c, reported=a, reproduced=b,
                rel_diff=abs(b - a) / abs(a) if a else float(b != a),
                tolerance=tolerances.get(c, DEFAULT_TOLERANCE),
            ))
    return comp
