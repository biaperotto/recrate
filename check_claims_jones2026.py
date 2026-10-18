"""Recompute the headline claims of Jones et al. (2026), "An Empirical Assessment of
Inferential Reproducibility of Linear Regression in Health and Biomedical Research Papers"
(medRxiv 10.64898/2026.04.07.26350296 v2) from the authors' own data files.

Usage:
    git clone --depth 1 https://github.com/Lee-V-Jones/Reproducibility.git
    python -I check_claims_jones2026.py Reproducibility

Needs pandas + openpyxl. Not checked here: the Bayesian mixed-model results
(marginal probability 0.34, ICC 0.59), which need R + brms/Stan.
"""
import math
import sys

import pandas as pd

R = sys.argv[1] if len(sys.argv) > 1 else "Reproducibility"
wide = pd.read_excel(f"{R}/data/Reproducibility_wide_with_dictionary.xlsx", "Data")
long = pd.read_excel(f"{R}/data/Reproducibility_long_with_dictionary.xlsx", "Data")
comp = pd.read_excel(f"{R}/data/compare_data_long_with_dictionary.xlsx", "Data")


def is_yes(s):
    return s.astype(str).str.lower().isin(["yes", "1", "1.0", "true"])


def wilson(k, n, z=1.959964):
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h


results = []


def check(label, claim, got):
    ok = str(claim) == str(got)
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'} | {label:45} | paper: {claim:>14} | data: {got}")


check("Data reported available (of 95)", 68, (wide.Data_Notionally_Available == "Yes").sum())
check("Data actually accessible", 43, (wide.Data_Available == "Yes").sum())
check("Assessed for computational repro", 20, wide.Comp_reproduce.isin(["Yes", "No"]).sum())
check("Computationally reproduced papers", 8, (wide.Comp_reproduce == "Yes").sum())

ir = wide.Infer_reproduce.value_counts()
k, n = ir.get("Yes", 0), ir.get("Yes", 0) + ir.get("No", 0)
lo, hi = wilson(k, n)
check("Papers assessed for inferential repro", 14, n)
check("Papers inferentially reproducible", 3, k)
check("Paper-level % (Wilson 95% CI)", "21% (8%, 48%)",
      f"{round(100 * k / n)}% ({round(100 * lo)}%, {round(100 * hi)}%)")

models = long.dropna(subset=["Reproduce"])
yes = is_yes(models.Reproduce).sum()
check("Models inferentially reproducible", "14/32 (44%)",
      f"{yes}/{len(models)} ({round(100 * yes / len(models))}%)")

check("Papers with normality violation", 8, long[is_yes(long.normal_violation)].paper_ID.nunique())
check("Papers with independence violation", 8, long[is_yes(long.indep_violation)].paper_ID.nunique())
check("Models with independence violation", 16, is_yes(long.indep_violation).sum())
lv = long[is_yes(long.linear_violation)]
check("Linearity violations: papers / models", "5 / 8", f"{lv.paper_ID.nunique()} / {len(lv)}")
dist = long.Distribution.value_counts()
check("Gaussian / Tweedie / Inverse Gaussian", "28 / 3 / 1",
      f"{dist.get('Gaussian', 0)} / {dist.get('Tweedie', 0)} / {dist.get('Inverse Gaussian', 0)}")

check("Coefficient ranges evaluated", 97, len(comp))
for col, claim in [("Range_10pct", "30 / 9 of 11"), ("Range_Diff_0.1", "12 / 5 of 11"),
                   ("Range_Diff_0.2", "2 / 2 of 11")]:
    per_paper = comp.groupby("paper_ID")[col].apply(lambda s: (s == "Yes").any())
    check(f"{col}: ranges / papers", claim,
          f"{(comp[col] == 'Yes').sum()} / {per_paper.sum()} of {len(per_paper)}")

sig = comp.SigChange.value_counts()
check("Significance: shifted / stayed sig / stayed non-sig", "1 / 40 / 56",
      f"{len(comp) - sig.get('Remains significant', 0) - sig.get('Remains non-sig', 0)}"
      f" / {sig.get('Remains significant', 0)} / {sig.get('Remains non-sig', 0)}")

print(f"\n{sum(results)}/{len(results)} claims reproduced from the shared data.")
sys.exit(0 if all(results) else 1)
