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

**Validation: differs, honestly.** The paper rounds to two decimals, so each value must be within ±0.006 of
the published one. 9 of 12 values reproduce; three chinstrap values don't: culmen length −0.097 vs −0.11,
flipper length −0.043 vs −0.05, body mass −0.117 vs −0.10. The data have the same number of sexed chinstrap
penguins as the paper (68), so the difference is in how the paper computed those three values, which it
doesn't say. This is what validation is for: it shows exactly where a published result can't be recovered
from the published data.

**Reuse.** On the Adélie penguins grouped by island (`group_column = Island`, recorded with its reason),
dimorphism is similar on all three colonies (body mass SDI −0.19 to −0.21).
