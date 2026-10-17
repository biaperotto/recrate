"""Sexual size dimorphism index (SDI) per group, as in Gorman, Williams & Fraser (2014), Table 2.

SDI after Lovich & Gibbons (1992): (mean of the larger sex / mean of the smaller sex) - 1, negative when
males are the larger sex. Computed per group (by default per species) for each measurement, on adults with
a known sex. The authors used R and published no code; this re-implements the index as the paper describes it.

ReCRATE's script contract:
    python size_dimorphism.py --input_dir DIR --output_dir DIR [--group_column Species] [--sex_column Sex]
           [--male_label MALE] [--female_label FEMALE] [--measure_columns "Culmen Length (mm),..."]
Reads every .csv in input_dir; writes output_dir/results.csv with one row per group.
"""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path
from statistics import mean

DEFAULT_MEASURES = "Culmen Length (mm),Culmen Depth (mm),Flipper Length (mm),Body Mass (g)"


def short(column: str) -> str:
    """'Culmen Length (mm)' -> 'culmen_length'."""
    return re.sub(r"[^a-z0-9]+", "_", re.sub(r"\(.*?\)", "", column).lower()).strip("_")


def sdi(male: float, female: float) -> float:
    return -(male / female - 1) if male > female else female / male - 1


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--input_dir", required=True)
    ap.add_argument("--output_dir", required=True)
    ap.add_argument("--group_column", default="Species")
    ap.add_argument("--sex_column", default="Sex")
    ap.add_argument("--male_label", default="MALE")
    ap.add_argument("--female_label", default="FEMALE")
    ap.add_argument("--measure_columns", default=DEFAULT_MEASURES)
    a = ap.parse_args()

    measures = [c.strip() for c in a.measure_columns.split(",") if c.strip()]
    rows = []
    for f in sorted(Path(a.input_dir).glob("*.csv")):
        with f.open(newline="", encoding="utf-8") as fh:
            rows += list(csv.DictReader(fh))

    def values(group: str, sex: str, col: str) -> list[float]:
        out = []
        for r in rows:
            if r.get(a.group_column) == group and r.get(a.sex_column) == sex:
                try:
                    out.append(float(r[col]))
                except (TypeError, ValueError, KeyError):
                    pass  # missing measurement
        return out

    out_dir = Path(a.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "results.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["group"] + [short(c) for c in measures] + ["n_male", "n_female"])
        for group in sorted({r.get(a.group_column) for r in rows if r.get(a.group_column)}):
            m_n = len([r for r in rows if r.get(a.group_column) == group and r.get(a.sex_column) == a.male_label])
            f_n = len([r for r in rows if r.get(a.group_column) == group and r.get(a.sex_column) == a.female_label])
            line = [group]
            for col in measures:
                m, f = values(group, a.male_label, col), values(group, a.female_label, col)
                line.append(f"{sdi(mean(m), mean(f)):.4f}" if m and f else "")
            w.writerow(line + [m_n, f_n])


if __name__ == "__main__":
    main()
