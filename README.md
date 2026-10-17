<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/img/logo-dark.svg">
    <img src="docs/img/logo.svg" alt="ReCRATE" width="460">
  </picture>
</p>

<p align="center"><b>A paper as an agent you can trust: it checks the paper's results before you rely on them, checks your data before you reuse the method, and records every run.</b></p>

<p align="center">Reproduce · Reuse · Record</p>

---

[Paper2Agent](https://github.com/jmiao24/Paper2Agent) showed that a paper's code can become an agent: an MCP server whose tools run the paper's methods, so anyone can apply them by asking. Two problems remain. A tool that runs is not a tool that is right: nothing checks it against the paper's published results first. And a method applied to data unlike the paper's (another pixel size, grouping or sampling rate) usually runs without errors and gives a believable wrong answer.

ReCRATE closes both gaps with the [FAIR principles for research software (FAIR4RS)](https://doi.org/10.1038/s41597-022-01710-x). Each tool is built from an explicit description of the research ([RO-Crate](https://doi.org/10.3233/DS-210053)) instead of inferred from code. It is validated against the published numbers before anyone reuses it, it checks your data against what the method expects, and every run leaves a standard provenance record.

## How it works

```
paper or RO-Crate ──► research object ──────────► agent: MCP server, app chat, CLI
                      workflow, data, parameters    Reproduce · Reuse · Record
                      and published results,               │
                      declared together                    ▼
                                                    one Workflow Run RO-Crate per run
```

1. **Describe.** ReCRATE starts from a published crate (WorkflowHub, Zenodo, a folder), finds one published for the paper, or drafts one from the paper and its code with the `/recrate` agent. A drafted crate stays *draft* until a named person confirms it.
2. **Reproduce: can I trust this result?** Each method runs on the paper's own data and every output is compared with the published value. The method climbs a ladder: *materials found* → *runs* → *same numbers* (e.g. 9/12) → *same conclusion*. When numbers differ, ReCRATE proposes follow-up checks (an inclusion rule, the rounding, a software or data version), each run and recorded on its own.
3. **Reuse: what does this method say about my data?** Your data is checked against the method's declared inputs before the run, and the results against the paper's after it. Parameters fall into three groups: adjusted automatically, with the computation shown; needing your decision, with the paper's sentence; and as published.
4. **Record: who ran what, and how do I cite it?** Every run is a Workflow Run RO-Crate linked to the paper. From it ReCRATE writes a methods paragraph, references (BibTeX, CSL-JSON), figures, and one report page a reviewer can open without installing anything.

## The approach: what every tool guarantees

An agent is only as trustworthy as the rules its tools follow. ReCRATE enforces these in code wherever it can:

* **Numbers come from runs, never from the model.** The model reads, routes and explains. It cannot set a published value, a tolerance or a verdict.
* **Every claim is traceable.** In a crate drafted from a paper, each parameter quotes the sentence that states it and each published value is checked against the paper's text and tables (`recrate check-description`, no model involved); and every tolerance names its source: `paper-rounding`, `paper-stated` (quoted), `method-noise` or `person` (named, with a reason). No source, no verdict on the numbers.
* **Verdicts are graded, and a person has the last word.** "Ready" never means "reproduced"; the ladder advances only on evidence from a run. *Same conclusion* is a person's call: the app and CLI require a name, the chat has no tool for it, and over MCP the tool tells the client to ask the user.
* **Nothing changes silently.** A parameter is adjusted only by a declared rule (`scales_with:<data property>^<exponent>`), and any other change needs a reason. Changing what is compared, such as grouping by island instead of species, is recorded as a different question, never as a reproduction.
* **The record is append-only.** Differing runs, failed runs and follow-up checks stay. Nothing replaces the first result.
* **The core is field-independent.** Field-specific knowledge lives in profiles that read properties from your files. Three exist today: `generic` (files, formats, table columns), `bioimaging` (calibration, channels, bit depth, object size) and `timeseries` (sampling rate, channels). Outputs declare a kind; scalars and tables are compared today, and other kinds are reported as not checked rather than compared the wrong way. See [docs/domain-profiles.md](docs/domain-profiles.md).

## Building on Paper2Agent

ReCRATE keeps Paper2Agent's way of building agents (a coordinator with specialist subagents, and a test of every tool before delivery) and its interface (MCP tools that return a message and file paths). It changes what a tool is built from and what it guarantees:

| | Paper2Agent | ReCRATE |
| --- | --- | --- |
| Built from | A paper and its code repository | An RO-Crate, or one drafted from the paper and its code |
| A tool is | A function from the paper's tutorials | A method declared in the crate: workflow, inputs, parameters, published results |
| Checked by | Tests against the tutorials' outputs | Value-by-value comparison with the published results, with sourced tolerances |
| Your data | Left to the model | Checked against declared input properties by a domain profile |
| Provenance | Not recorded | A Workflow Run RO-Crate per run, linked to the paper |

## FAIR4RS, principle by principle

FAIR4RS asks what makes research software findable, accessible, interoperable and reusable. ReCRATE applies it where reuse happens: to the method being reused, and to each run. This describes the proof of concept, not a certification.

| Principle | How ReCRATE meets it | Status |
| --- | --- | --- |
| **F1** persistent identifiers, per version | Starts from the paper's DOI and the workflow's WorkflowHub or Zenodo identifier, with its version | Done |
| **F2, F3** rich metadata that includes the identifier | Each run crate names the crate it is `isBasedOn`, the paper it cites, the software and versions that ran | Done |
| **F4** metadata searchable and indexable | Run crates are JSON-LD; deposit to a registry is manual | Partial |
| **A1** retrievable by identifier, over open protocols | Sources are fetched by DOI or URL over HTTPS; a published crate is found from the paper's links, WorkflowHub, Zenodo, DataCite or its code | Done |
| **A1.2** authentication where needed | Data behind access requests is recorded, never fetched | Partial (`/recrate` agent) |
| **A2** metadata outlive the software | Each run crate keeps a copy of the workflow and all its metadata | Done |
| **I1** exchange data by community standards | Inputs declare their formats and properties (e.g. OME/ImageJ calibration); your files are checked against them | Done for images, tables, signals |
| **I2** qualified references to other objects | `isBasedOn`, `citation`, `exampleOfWork`, `conformsTo`; follow-ups link the run they follow | Done |
| **R1** rich, accurate description | Parameters carry units, scaling rules, roles and the paper's sentence; outputs carry kinds and sourced tolerances | Done |
| **R1.1** clear licence | Carried into every run crate when the source states one | Partial |
| **R1.2** detailed provenance | Who ran what, when, on which files (SHA-256), with which parameters and why, checked by whom | Done |
| **R2** references to the software used | Software and versions per run; versions as of publication, each with how it was determined | Done |
| **R3** community standards | RO-Crate 1.1, Workflow RO-Crate 1.0, Process and Workflow Run Crate 0.5: all 55 required [validator](https://github.com/crs4/rocrate-validator) checks pass in CI, and 122 of 136 at the recommended level | Done |

## Examples

**Nuclei segmentation** ([WorkflowHub #771](examples/workflowhub-nuclei-segmentation/), Galaxy, imaging). **Same numbers 1/1.** ReCRATE reads the method from the workflow and its published test, and runs it without a Galaxy server through ports of the Galaxy tools at their pinned versions. It finds the test's 20 nuclei, with the same label image pixel for pixel, and 425, 413 and 307 nuclei on three full fields of public images ([Zenodo](https://doi.org/10.5281/zenodo.3362976)). Readiness is *ready-with-gaps*: the research states no pixel size.

**Penguin size dimorphism** ([Gorman et al. 2014, *PLOS ONE*](examples/penguins-dimorphism/), ecology, tables). **Same numbers 9/12.** Recomputing Table 2 from the open data, three chinstrap values differ: culmen length −0.097 vs −0.11, flipper length −0.043 vs −0.05, body mass −0.117 vs −0.10. The paper excludes pairs whose second egg was never observed, and gives n = 54 chinstraps. Applying that exclusion as a follow-up check leaves 27 males and 27 females, and all 12 values match. Both runs stay on record; whether the conclusion holds is for a person to say. One caveat: the ±0.006 margin is 0.001 wider than rounding alone, and `check-description` says so. At ±0.005 the counts are 6/12 and 10/12.

**Reuse at half resolution** ([make_half_resolution.py](examples/workflowhub-nuclei-segmentation/make_half_resolution.py)). Average each 2×2 block of pixels, as from a binned camera. With the published 3-pixel filter the workflow still runs, but neighbouring nuclei merge: 385, 385 and 281 nuclei. ReCRATE warns before the run (the filter is in pixels, and no pixel size is stated) and after it (objects are 0.41× the size found on the paper's data). With `--pixel-ratio 2` the filter becomes 1.5 pixels, the change is recorded with its rule, and the counts are 425, 414 and 306.

![The same half-resolution field. Left: the published filter size; neighbouring nuclei merge. Right: the filter rescaled for the stated pixel ratio; nuclei are separated as at full resolution.](docs/img/adjusted_vs_unadjusted.png)

## Quick start

```bash
git clone <this repo> recrate && cd recrate
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[app,mcp]"
recrate agentify --crate examples/workflowhub-nuclei-segmentation/crate --out agents/nuclei
```

**In Claude or any MCP client.** No API key needed: ReCRATE runs the methods, and the model is your client's.

```bash
claude mcp add nuclei -- "$PWD/.venv/bin/recrate" serve "$PWD/agents/nuclei"
```

Then ask, for example, *"Reproduce this paper"* or *"Check my images in ./data, then run the method on them."*

**From a paper alone**, in Claude Code inside this repository: `/recrate <paper PDF, DOI, arXiv link or crate>` (elsewhere, `recrate install-skill` first). Specialist subagents read the paper and its code and describe each method. ReCRATE reproduces each one, an independent verifier checks every claim, and the result is an agent you serve as above. See [docs/architecture.md](docs/architecture.md).

**In the app.** `streamlit run app/app.py`, then open an example. The chat works with a free local model ([Ollama](https://ollama.com)), with Claude and an API key, or with simple commands.

**From the command line**, every step runs without a model:

```bash
recrate check     agents/nuclei <your-images> --pixel-ratio 2                     # does my data fit?
recrate run       agents/nuclei <your-images> --pixel-ratio 2 --who "Your name"   # reuse, recorded
recrate agentify  --paper examples/penguins-dimorphism/paper.pdf --crate examples/penguins-dimorphism/crate --out agents/penguins
recrate followup  agents/penguins --run exclude-pairs-that-did-not-complete-their-clutch --who "Your name"
recrate review    agents/penguins --who "Your name" --agrees partly --note "..."  # same conclusion?
recrate report    agents/penguins                                                # one HTML page
```

## Limits and next steps

ReCRATE is a proof of concept, built at the *Reinvent the Wheel* hackathon (London, October 2026).

| Today | Next |
| --- | --- |
| Scripts run with the interpreter on your computer, after you confirm you trust their source; only its version is recorded | A container or conda environment built from `environment.json`, with every package version recorded |
| Galaxy runs through ported tools only; other workflows are reported *blocked*, naming the tool | planemo, cwltool, Nextflow and Snakemake |
| Scalars and tables are compared; `timeseries` is tested on synthetic data only | IoU for label images, tests for distributions, reruns to measure method noise; genomics and single-cell profiles; OME-Zarr, IDR and OMERO sources |
| Follow-ups for software and data versions are proposed, not run | Running them in pinned environments |
| A paper alone becomes runnable only through the `/recrate` agent, as far as the paper and its code allow | An evaluation on 20 to 50 WorkflowHub entries with linked papers |
| Deposit is manual (a ready-to-deposit zip) | Automated deposit to Zenodo and WorkflowHub |

## Citing, licence, acknowledgements

Cite ReCRATE with [CITATION.cff](CITATION.cff). Code is under the [MIT licence](LICENSE); example data keep their own licences (see each example's `SOURCE.md`).

ReCRATE builds directly on [Paper2Agent](https://github.com/jmiao24/Paper2Agent) by Jiacheng Miao and colleagues, and on the work of the [RO-Crate](https://www.researchobject.org/ro-crate/) and [Workflow Run Crate](https://www.researchobject.org/workflow-run-crate/) communities, the [RO-Crate validator](https://github.com/crs4/rocrate-validator), [WorkflowHub](https://workflowhub.eu) and the [Galaxy imaging tools](https://github.com/BMCV/galaxy-image-analysis).
