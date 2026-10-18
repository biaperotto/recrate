"""Stable isotope signatures by sex and year, as in Gorman, Williams & Fraser (2014), Figures 3, 4 and 5.

For each species, one figure with a panel per year: the mean δ13C (x) and δ15N (y) of males and of females, with
± 1 SE error bars, as the captions state. The authors used R and published no code; this redraws the figures from
the published data as the captions describe them. The paper's figures also show a second, narrower error bar inside
the ± 1 SE one, which the captions don't explain; it is not drawn here.

ReCRATE's script contract:
    python isotope_figures.py --input_dir DIR --output_dir DIR [--species_column Species] [--sex_column Sex]
           [--year_column "Date Egg"] [--keep_rows "Column=Value;..."] [--figures "Adelie:3;Chinstrap:4;Gentoo:5"]
Writes output_dir/figures/figure-<N>.png (one per species, named after the paper's figure it recreates),
output_dir/results.csv (the sample sizes per species, which the captions print) and output_dir/means.csv (the
plotted means and standard errors).
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from statistics import mean, stdev

C13, N15 = "Delta 13 C (o/oo)", "Delta 15 N (o/oo)"
COLOURS = {"MALE": "#00007f", "FEMALE": "#ff4500"}  # as in the paper: navy males, orange-red females


def number(v: str | None) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def se(xs: list[float]) -> float:
    return stdev(xs) / len(xs) ** 0.5 if len(xs) > 1 else 0.0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--input_dir", required=True)
    ap.add_argument("--output_dir", required=True)
    ap.add_argument("--species_column", default="Species")
    ap.add_argument("--sex_column", default="Sex")
    ap.add_argument("--year_column", default="Date Egg", help="Its first four characters are the year")
    ap.add_argument("--keep_rows", default="", help="Inclusion rule: 'Column=Value' conditions separated by ';'")
    ap.add_argument("--figures", default="Adelie:3;Chinstrap:4;Gentoo:5",
                    help="Species (start of its name) : the paper's figure number")
    a = ap.parse_args()

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = []
    for f in sorted(Path(a.input_dir).glob("*.csv")):
        with f.open(newline="", encoding="utf-8") as fh:
            rows += list(csv.DictReader(fh))
    for cond in filter(None, (c.strip() for c in a.keep_rows.split(";"))):
        col, _, value = cond.partition("=")
        rows = [r for r in rows if r.get(col.strip()) == value.strip()]
    rows = [r for r in rows if r.get(a.sex_column) in COLOURS]

    out = Path(a.output_dir)
    (out / "figures").mkdir(parents=True, exist_ok=True)
    sizes, means = [], []
    for item in filter(None, (x.strip() for x in a.figures.split(";"))):
        prefix, _, fig = item.partition(":")
        sp = [r for r in rows if (r.get(a.species_column) or "").startswith(prefix.strip())]
        if not sp:
            continue
        sizes.append({"species": prefix.strip(),
                      "n_d13c": sum(1 for r in sp if number(r.get(C13)) is not None),
                      "n_d15n": sum(1 for r in sp if number(r.get(N15)) is not None)})
        years = sorted({(r.get(a.year_column) or "")[:4] for r in sp} - {""})
        fig_, axes = plt.subplots(1, len(years), figsize=(2.6 * len(years) + 1.6, 4.6), sharex=True, sharey=True)
        axes = axes if len(years) > 1 else [axes]
        for ax, year in zip(axes, years):
            for sex, colour in COLOURS.items():
                g = [r for r in sp if (r.get(a.year_column) or "").startswith(year) and r.get(a.sex_column) == sex]
                x = [v for v in (number(r.get(C13)) for r in g) if v is not None]
                y = [v for v in (number(r.get(N15)) for r in g) if v is not None]
                if not x or not y:
                    continue
                ax.errorbar(mean(x), mean(y), xerr=se(x), yerr=se(y), fmt="o", color=colour, ms=5, capsize=4,
                            label=sex.capitalize())
                means.append({"species": prefix.strip(), "year": year, "sex": sex, "n_d13c": len(x), "n_d15n": len(y),
                              "mean_d13c": round(mean(x), 4), "se_d13c": round(se(x), 4),
                              "mean_d15n": round(mean(y), 4), "se_d15n": round(se(y), 4)})
            ax.set_title(year, fontweight="bold")
            ax.set_xlabel("δ$^{13}$C (‰)")
        axes[0].set_ylabel("δ$^{15}$N (‰)")
        axes[-1].legend(loc="upper left", bbox_to_anchor=(1.02, 1), frameon=True)
        fig_.suptitle(f"{prefix.strip()} penguins: mean ± 1 SE (redrawn from the published data)", fontsize=9, y=0.995)
        fig_.tight_layout()
        fig_.savefig(out / "figures" / f"figure-{fig.strip()}.png", dpi=150)
        plt.close(fig_)

    for name, table in (("results.csv", sizes), ("means.csv", means)):
        with (out / name).open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(table[0]) if table else ["species"])
            w.writeheader()
            w.writerows(table)


if __name__ == "__main__":
    main()
