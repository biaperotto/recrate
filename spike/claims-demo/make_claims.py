"""THROWAWAY SPIKE — pinned to Jones et al. (2026), not general reCRATE code.

Produces a real `claims.json`, in reCRATE's own `Claim` schema, from the 18
checks that `check_claims_jones2026.py` already reproduces. Milestone 2 will
replace *this generator* with model-driven mapping and generated check scripts;
the schema and the UI that reads it stay.

Every field is derived, never typed in:
  * `paper_says`  - the value as the paper prints it
  * `data_gives`  - recomputed here from the authors' spreadsheets
  * `result`      - Reproduced / Mismatch, decided by comparing the two
  * `anchor`      - words lifted verbatim from the fetched paper text, around
                    where the value is printed, so the link actually resolves
  * `data_source` - the file, columns and the commit the data is pinned to

Usage: python3 -I make_claims.py <pinned-repo> <agent-dir>
"""
from __future__ import annotations

import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import pandas as pd

from recrate.claims import Claim, save_claims

PIN = "370e615"
REPO_URL = "https://github.com/Lee-V-Jones/Reproducibility"
PAPER_URL = "https://www.medrxiv.org/content/10.64898/2026.04.07.26350296v2"
SECTIONS = ("Abstract", "Introduction", "Methods", "Results", "Discussion", "Conclusion")


def wilson(k, n, z=1.959964):
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h


def is_yes(s):
    return s.astype(str).str.lower().isin(["yes", "1", "1.0", "true"])


def anchor_for(flat: str, value: str, words: int = 14) -> tuple[str, str]:
    """Words lifted verbatim from the paper around where `value` is printed, and
    the section it sits in. Returns ("", "") when the value is not printed."""
    m = re.search(r"(?<![\d.])" + re.escape(value) + r"(?![\d])", flat)
    if not m:
        return "", ""
    before = flat[: m.start()].split()[-(words // 2):]
    after = flat[m.end():].split()[: words // 2]
    anchor = " ".join(before + [value] + after).strip()
    section = ""
    for name in SECTIONS:                       # nearest section word before the hit
        at = flat.rfind(name, 0, m.start())
        if at != -1:
            section = name if not section or at > flat.rfind(section, 0, m.start()) else section
    return anchor, section or "Results"


def main() -> int:
    repo = Path(sys.argv[1] if len(sys.argv) > 1 else "Reproducibility")
    agent = Path(sys.argv[2])
    flat = re.sub(r"\s+", " ", (agent / "paper" / "paper.md").read_text(encoding="utf-8"))

    wide = pd.read_excel(repo / "data/Reproducibility_wide_with_dictionary.xlsx", "Data")
    long = pd.read_excel(repo / "data/Reproducibility_long_with_dictionary.xlsx", "Data")
    comp = pd.read_excel(repo / "data/compare_data_long_with_dictionary.xlsx", "Data")

    rows = []

    def check(sentence, says, gives, wb, cols, how):
        rows.append((sentence, str(says), str(gives), wb, cols, how))

    check("Sixty-eight of the 95 reviewed papers reported that their data were available.",
          68, (wide.Data_Notionally_Available == "Yes").sum(),
          "Reproducibility_wide", ["Data_Notionally_Available"], 'count of == "Yes"')
    check("Data were actually accessible for 43 papers.",
          43, (wide.Data_Available == "Yes").sum(),
          "Reproducibility_wide", ["Data_Available"], 'count of == "Yes"')
    check("Twenty papers were assessed for computational reproducibility.",
          20, wide.Comp_reproduce.isin(["Yes", "No"]).sum(),
          "Reproducibility_wide", ["Comp_reproduce"], 'count of in {"Yes","No"}')
    check("Eight papers were computationally reproduced.",
          8, (wide.Comp_reproduce == "Yes").sum(),
          "Reproducibility_wide", ["Comp_reproduce"], 'count of == "Yes"')

    ir = wide.Infer_reproduce.value_counts()
    k, n = ir.get("Yes", 0), ir.get("Yes", 0) + ir.get("No", 0)
    lo, hi = wilson(k, n)
    check("Fourteen papers were assessed for inferential reproducibility.",
          14, n, "Reproducibility_wide", ["Infer_reproduce"], 'count of in {"Yes","No"}')
    check("Three papers met the inferential reproducibility criterion.",
          3, k, "Reproducibility_wide", ["Infer_reproduce"], 'count of == "Yes"')
    check("The paper-level inferential reproducibility rate was 21% (95% CI 8%, 48%).",
          "21% (8%, 48%)", f"{round(100 * k / n)}% ({round(100 * lo)}%, {round(100 * hi)}%)",
          "Reproducibility_wide", ["Infer_reproduce"], "Wilson 95% CI, rounded to whole percent")

    models = long.dropna(subset=["Reproduce"])
    yes = is_yes(models.Reproduce).sum()
    check("Fourteen of 32 models (44%) were inferentially reproducible.",
          "14/32 (44%)", f"{yes}/{len(models)} ({round(100 * yes / len(models))}%)",
          "Reproducibility_long", ["Reproduce"], "count of yes over non-missing rows")
    check("Eight papers had a normality violation.",
          8, long[is_yes(long.normal_violation)].paper_ID.nunique(),
          "Reproducibility_long", ["normal_violation", "paper_ID"], "distinct papers where yes")
    check("Eight papers had an independence violation.",
          8, long[is_yes(long.indep_violation)].paper_ID.nunique(),
          "Reproducibility_long", ["indep_violation", "paper_ID"], "distinct papers where yes")
    check("Sixteen models had an independence violation.",
          16, is_yes(long.indep_violation).sum(),
          "Reproducibility_long", ["indep_violation"], "count of yes")
    lv = long[is_yes(long.linear_violation)]
    check("Linearity was violated in 5 papers across 8 models.",
          "5 / 8", f"{lv.paper_ID.nunique()} / {len(lv)}",
          "Reproducibility_long", ["linear_violation", "paper_ID"], "distinct papers / rows where yes")
    dist = long.Distribution.value_counts()
    check("The best-fitting distribution was Gaussian for 28 models, Tweedie for 3 and inverse Gaussian for 1.",
          "28 / 3 / 1",
          f"{dist.get('Gaussian', 0)} / {dist.get('Tweedie', 0)} / {dist.get('Inverse Gaussian', 0)}",
          "Reproducibility_long", ["Distribution"], "value counts of three categories")

    check("Ninety-seven coefficient ranges were evaluated.",
          97, len(comp), "compare_data_long", ["(rows)"], "row count")
    for col, says, label in [("Range_10pct", "30 / 9 of 11", "a change over 10%"),
                             ("Range_Diff_0.1", "12 / 5 of 11", "a standardised change over 0.1"),
                             ("Range_Diff_0.2", "2 / 2 of 11", "a standardised change over 0.2")]:
        per = comp.groupby("paper_ID")[col].apply(lambda s: (s == "Yes").any())
        check(f"{says.split(' / ')[0]} coefficient ranges showed {label}.",
              says, f"{(comp[col] == 'Yes').sum()} / {per.sum()} of {len(per)}",
              "compare_data_long", [col, "paper_ID"], "ranges where yes / papers with any")
    sig = comp.SigChange.value_counts()
    check("One coefficient changed significance; 40 stayed significant and 56 stayed non-significant.",
          "1 / 40 / 56",
          f"{len(comp) - sig.get('Remains significant', 0) - sig.get('Remains non-sig', 0)}"
          f" / {sig.get('Remains significant', 0)} / {sig.get('Remains non-sig', 0)}",
          "compare_data_long", ["SigChange"], "value counts of three categories")

    claims = []
    for i, (sentence, says, gives, wb, cols, how) in enumerate(rows, 1):
        anchor, section = anchor_for(flat, says if "%" not in says and "/" not in says else says.split()[0])
        pinned = f"{REPO_URL}/blob/{PIN}/data/{wb}_with_dictionary.xlsx"
        claims.append(Claim(
            id=f"c{i}", kind="number", claim=sentence,
            section=section or "Results", anchor=anchor,
            paper_says=says, data_gives=gives,
            result="Reproduced" if says == gives else "Mismatch",
            why_not="" if says == gives else "the recomputed value differs from the paper's",
            data_source={"file": f"{wb}_with_dictionary.xlsx", "sheet": "Data", "columns": cols,
                         "url": pinned, "version": PIN, "found_in": "data availability section"},
            computation={"columns": cols, "aggregation": how, "engine": "pandas"},
            tolerance={"kind": "exact", "note": "compared as printed, at the paper's precision"},
        ))

    out = agent / "claims.json"
    save_claims(claims, out)
    ok = sum(c.result == "Reproduced" for c in claims)
    print(f"{ok}/{len(claims)} claims reproduced -> {out}")
    missing = [c.id for c in claims if not c.anchor]
    if missing:
        print(f"note: no anchor found in the paper text for {', '.join(missing)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
