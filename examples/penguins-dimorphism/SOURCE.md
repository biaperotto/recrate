# Real paper example: sexual size dimorphism in Antarctic penguins

A published paper with open data, outside imaging, turned into a ReCRATE research object: its Table 2 is
recomputed from the public data and compared with the published values, and the method is reused on new
questions. Retrieved 2026-10-17.

## What is here

| Path | What it is | Source | Licence |
| --- | --- | --- | --- |
| `paper.pdf` | Gorman KB, Williams TD, Fraser WR (2014). *Ecological sexual dimorphism and environmental variability within a community of Antarctic penguins (genus Pygoscelis).* PLOS ONE 9(3): e90081 | [doi:10.1371/journal.pone.0090081](https://doi.org/10.1371/journal.pone.0090081) | CC-BY-4.0 |
| `data/penguins_raw.csv` | The paper's data: 344 adults of three species, Palmer Archipelago, 2007-2009 (Palmer Station LTER datasets 219, 220, 221) | [palmerpenguins](https://github.com/allisonhorst/palmerpenguins) `penguins_raw`, from the [EDI repository](https://portal.edirepository.org/) | CC0 |
| `expected/table2_sdi.csv` | The size dimorphism index (SDI) per species and measurement, as printed in the paper's Table 2 | the paper | CC-BY-4.0 |
| `workflows/size_dimorphism.py` | The method: SDI after Lovich & Gibbons (1992), per group | written for this example | MIT |
| `research-data/adelie_penguins.csv` | "Your data" for reuse: the Adélie penguins only, to compare colonies (islands) | subset of the data above | CC0 |
| `crate_description.json`, `crate/` | The RO-Crate (built with `recrate draft crate_description.json crate`) | | |

The authors used R and published no code, so the method is a re-implementation of the index as the paper
describes it, and the crate is marked as a draft. Table 1 (sex classification by logistic regression) is not
included: its percentages imply a subsample (e.g. 88.64% = 78 of 88 Adélie penguins) the paper doesn't define.

## Running it

```bash
recrate agentify --paper examples/penguins-dimorphism/paper.pdf --crate examples/penguins-dimorphism/crate \
                 --out examples/penguins-dimorphism/paper_agent
```

**Reproduction: partly, and why.** Table 2 prints two decimals, so each value is compared within ±0.006
(source: `paper-rounding`; half a unit of the last decimal is 0.005, and the extra 0.001 is a margin that
`check-description` reports as wider than rounding alone). 9 of 12 values match; three chinstrap values don't:
culmen length −0.097 vs −0.11, flipper length −0.043 vs −0.05, body mass −0.117 vs −0.10.

**Follow-up check: an exclusion the paper states in words.** The paper says "some pairs were excluded from
statistical analyses because a final egg was never observed at the nest", and gives n = 54 chinstraps for the
SDI. The published data records this as `Clutch Completion = No`, which the method as drafted keeps (68
chinstraps). The crate declares the exclusion as a follow-up check (`recrate followup <agent> --run
exclude-pairs-that-did-not-complete-their-clutch`). With it, 12 of 12 values match, and the chinstrap sample is
27 males and 27 females: 54, as the paper states (Adélie 132 and gentoo 112, against 137 and 116 in the paper).
The check is its own run crate, linked to the reproduction; the first result (9 of 12) stays on record. A
second check, rounding the reproduced values as the paper prints them, gives 6 of 12, so rounding alone
doesn't explain the paper's values.

**The paper's figures.** `workflows/isotope_figures.py` (ours, CC0) redraws Figures 3, 4 and 5 from the same data as
their captions describe them: mean δ13C and δ15N by sex and year, ± 1 SE. ReCRATE shows each next to the original, for
a person to judge. Figure 3 shows the same pattern every year, and our ± 1 SE bars match the paper's wider bars; the
paper's narrower inner bars are not explained by the captions. The sample sizes the captions print are compared
exactly and don't come back (Adélie n = 127 / 128, chinstrap 53, gentoo 115; the data gives 139, 68 / 67 and 118 for
every sexed bird, or 126, 54 / 53 and 111 with the clutch exclusion).

**Reuse.** On the Adélie penguins grouped by island (`group_column = Island`, recorded with its reason),
dimorphism is similar on all three colonies (body mass SDI −0.19 to −0.21).
