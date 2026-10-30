<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/img/logo-dark.svg">
    <img src="docs/img/logo.svg" alt="ReCRATE" width="460">
  </picture>
</p>

<p align="center"><b>A paper, or a published workflow, as an agent you can trust.</b><br>
It checks the published results before you rely on them, checks your data before you reuse the method, and records every run.</p>

<p align="center">Reproduce · Reuse · Record</p>

---

## 📚 Contents

- [🔎 What ReCRATE does](#-what-recrate-does)
- [🚀 Quick start](#-quick-start)
- [🧭 Use it](#-use-it)
  - [🤖 In Claude or any MCP client](#-in-claude-or-any-mcp-client)
  - [🖥️ In the app](#️-in-the-app)
  - [⌨️ From the command line](#️-from-the-command-line)
- [⚙️ How it works](#️-how-it-works)
- [🛡️ What every tool guarantees](#️-what-every-tool-guarantees)
- [🌐 Published workflows from WorkflowHub](#-published-workflows-from-workflowhub)
- [🧪 Examples](#-examples)
- [🧱 RO-Crate, FAIR and Paper2Agent](#-ro-crate-fair-and-paper2agent)
- [🚧 Limits and next steps](#-limits-and-next-steps)
- [📖 Documentation](#-documentation)
- [📝 Citing, licence, acknowledgements](#-citing-licence-acknowledgements)

## 🔎 What ReCRATE does

[Paper2Agent](https://github.com/jmiao24/Paper2Agent) showed that a paper's code can become an agent: an MCP server whose tools run the paper's methods. ReCRATE takes that idea and adds what reusing a method safely needs.

| | What you get |
| --- | --- |
| **Checked against the paper** | Each method runs on the paper's own data. The output is compared with the numbers the paper published. |
| **Checked against your data** | Your data is checked against what the method expects (a pixel size, a grouping, a sampling rate) before a run. Results far from the paper's are flagged after it. |
| **Recorded** | Every run leaves a Workflow Run RO-Crate: who ran what, when, on which files, with which software. |

It starts from a paper (PDF, DOI or link), a published RO-Crate (WorkflowHub, Zenodo or a folder) or a WorkflowHub link. The research is described as an [RO-Crate](https://doi.org/10.3233/DS-210053): which workflow, data, parameters and published results belong together. ReCRATE follows the [FAIR principles for research software](https://doi.org/10.1038/s41597-022-01710-x).

## 🚀 Quick start

Python 3.10 or later.

```bash
git clone https://github.com/biaperotto/recrate.git && cd recrate
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[app,mcp]"       # exact tested versions: pip install -r requirements.lock -e . --no-deps
streamlit run app/app.py          # the app: press Start, or Try an example
```

Or build an agent from a published workflow and check that it reproduces its own test:

```bash
recrate agentify --crate examples/workflowhub-nuclei-segmentation/crate --out agents/nuclei
```

The same command takes a WorkflowHub link (`--crate https://workflowhub.eu/workflows/1055`) or a paper (`--paper paper.pdf`).

Extras: `app`, `mcp`, `galaxy` (Galaxy servers), `draft` (drafting a crate with Claude), `fiji` (ImageJ macros), `dev` (tests), `all`.

## 🧭 Use it

### 🤖 In Claude or any MCP client

No API key is needed: ReCRATE runs the methods, and the model is your client's. One command registers the agent and checks that it starts:

```bash
recrate install-mcp agents/nuclei --client desktop   # Claude Desktop (quit and reopen it)
recrate install-mcp agents/nuclei --client project   # .mcp.json in this folder: Claude Code and its VS Code extension
recrate install-mcp agents/nuclei                    # print the JSON for any other client
```

Then ask: *"Reproduce this paper"*, *"Check my images in ./data, then run the method on them"*, *"Plot the counts per image"*. A run's figures come back as images.

To share an agent, `recrate serve agents/nuclei --http --token <secret>` serves it at `http://127.0.0.1:8765/mcp` (localhost only unless you say otherwise), and `recrate install-mcp agents/nuclei --client desktop --transport http --token <secret>` registers it.

### 🖥️ In the app

`streamlit run app/app.py`, then press **Start** or **Try an example**. The chat is on the left. **Reproduce**, **Reuse** and **Record** are on the right, with a divider you can drag. The chat answers with Claude when Claude Code (or its VS Code extension) is signed in, with no API key; otherwise with a local model ([Ollama](https://ollama.com)), an API key, or simple commands.

**From a paper alone**, add the paper and press **Make runnable**. Claude, on your subscription, runs the `/recrate` pipeline (also `recrate build agents/<paper>`): it clones the code, builds the paper's environment, turns the reported methods into tools, runs each on the published data, and has an independent verifier check every claim. It runs the paper's code on your computer, so start it only for code you are willing to run.

### ⌨️ From the command line

Every step runs without a model.

| Command | What it does |
| --- | --- |
| `recrate agentify --crate … \| --paper … --out agents/x` | Build an agent, run its methods on the published data, compare |
| `recrate validate agents/x` | Run the methods again |
| `recrate check agents/x <your-data> [--pixel-ratio 2]` | Does my data fit the method, and what does that change? |
| `recrate run agents/x <your-data> --who "Your name"` | Reuse on your data, recorded |
| `recrate followup agents/x --run <check> --who "Your name"` | Run a proposed follow-up check, recorded on its own |
| `recrate review agents/x --who "Your name" --agrees partly --note "…"` | A person's verdict on *same conclusion* |
| `recrate report agents/x` | One HTML page a reviewer opens without installing anything |
| `recrate export-crate agents/x` · `recrate check-crate agents/x` | The RO-Crate as a zip · the RO-Crate validator's verdict |
| `recrate serve agents/x [--http]` | The MCP server |
| `recrate runtime setup` | A small Linux VM for workflows that need containers |

`recrate --help` lists the rest.

## ⚙️ How it works

```
paper, RO-Crate or WorkflowHub link ──► research object ──────────► agent: MCP server, app chat, CLI
                                        workflow, data, parameters    Reproduce · Reuse · Record
                                        and published results,               │
                                        declared together                    ▼
                                                              one Workflow Run RO-Crate per run
```

1. **Describe.** Start from a published crate, find one for the paper (WorkflowHub, Zenodo, DataCite, the paper's code), or draft one from the paper and its code. A draft stays *draft* until a named person confirms it.
2. **Reproduce: can I trust this result?** Each method runs on the paper's data; every output is compared with the published value. The verdict climbs a ladder: *materials found* → *runs* → *same numbers* (a count, such as 9/12) → *same conclusion*. When numbers differ, ReCRATE proposes follow-up checks (an inclusion rule, the rounding, a software version), each recorded on its own.
3. **Reuse: what does this method say about my data?** Your data is checked against the method's declared inputs before the run, and the results against the paper's after it. A parameter is adjusted by a declared rule, left to your decision with the paper's sentence, or kept as published.
4. **Record: who ran what, and how do I cite it?** Every run is a Workflow Run RO-Crate linked to the paper, from which ReCRATE writes a methods paragraph, references (BibTeX, CSL-JSON), figures and a report page.

Before anything runs, each method has a readiness, with the reason:

| Readiness | Meaning |
| --- | --- |
| ✅ `ready` | Runs on your machine, with published results to compare with |
| ⚠️ `ready-with-gaps` | Runs on your machine; something the research does not state (a pixel size, a version) is assumed |
| `reusable` | Runs on your machine, checks your data and records each run; the research publishes nothing to compare with |
| ❌ `blocked` | Cannot run on your machine, and the reason is named: a missing runtime, or a crate that lacks the files its steps run |

## 🛡️ What every tool guarantees

* **Numbers come from runs, never from the model.** The model reads, routes and explains. It cannot set a published value, a tolerance or a verdict.
* **Every claim is traceable.** A drafted parameter quotes the sentence that states it (`recrate check-description`). Every tolerance names its source: `paper-rounding`, `paper-stated`, `method-noise`, `test-stated`, `code-stated` or `person`. No source, no verdict.
* **A person has the last word.** *Ready* never means *reproduced*. *Same conclusion* needs a named person; over MCP the tool records only a verdict the user gives.
* **Nothing changes silently.** A parameter is adjusted only by a declared rule (`scales_with:<data property>^<exponent>`); any other change needs a reason. Comparing something else (islands instead of species) is a different question, never a reproduction.
* **The record is append-only.** Differing runs, failed runs and follow-up checks stay.
* **Code runs sandboxed.** No network, writes only to the run's folders (`sandbox-exec` on macOS, `bubblewrap` on Linux). `recrate --unsandboxed` or `RECRATE_SANDBOX=off` is an explicit choice, recorded in the run crate; `RECRATE_SANDBOX_NETWORK=1` allows the network to a method that needs it. API keys are stripped from the code's environment.
* **The core is field-independent.** Field knowledge lives in profiles: `generic`, `bioimaging` and `timeseries` ([docs/domain-profiles.md](docs/domain-profiles.md)).

## 🌐 Published workflows from WorkflowHub

Every WorkflowHub entry is a Workflow RO-Crate, so `recrate agentify --crate <link>` takes any of them. An adapter per ecosystem reads the community's own conventions: the parameters, the published test, and what that test must produce.

| Ecosystem | Readable from | Runs with | Compared with |
| --- | --- | --- | --- |
| Galaxy | The `.ga` file and its Planemo test | Six imaging tools ported into ReCRATE, or any Galaxy server through its API (`recrate[galaxy]`, `GALAXY_URL`, `GALAXY_API_KEY`) | The test's outputs by sha1, or an object count |
| nf-core / Nextflow | `nextflow_schema.json` and the test profile | docker, singularity or apptainer, or the conda profile with conda, mamba or micromamba | The nf-test snapshot, md5 per file |
| CWL | FormalParameters and the cwltest job | cwltool, plus a container runtime when required | The cwltest checksums |
| Snakemake | `config/` and `.test/`, or the authors' `run_test.sh` | singularity or apptainer for container rules; conda for conda rules | The test's declared files |
| Jupyter | The notebook's path variable, injected with papermill | papermill in the notebook's own environment | Each cell's stored output and figures |

These five types are about four entries in five on WorkflowHub. Readiness is decided per entry: most have neither a paper nor a test, so they are *reusable, not reproducible*.

**Where a workflow runs.** On your machine, sandboxed, when the engine and runtime it needs are installed (Nextflow and docker, say). Otherwise in ReCRATE's Linux VM (`recrate runtime setup`), on another machine over SSH (`RECRATE_REMOTE=user@host`), on a Galaxy server, or on a GitHub runner with docker ([workflowhub-pipeline.yml](.github/workflows/workflowhub-pipeline.yml)). Every run crate says where it ran. Details: [scripts/workflowhub/README.md](scripts/workflowhub/README.md).

## 🧪 Examples

| Example | Verdict | In short |
| --- | --- | --- |
| [Nuclei segmentation](examples/workflowhub-nuclei-segmentation/) · WorkflowHub #771, Galaxy | ✅ 1/1 | The test's 20 nuclei found, with the same label image pixel for pixel. On three full public fields: 425, 413 and 307 nuclei. |
| nf-core/demo · WorkflowHub #1055, Nextflow ([script](scripts/workflowhub/demo_test.sh)) | ⚠️ 29/35 | Ran on a Mac without docker through the conda profile. The 6 that differ are gzipped files whose headers carry timestamps. |
| [Penguin size dimorphism](examples/penguins-dimorphism/) · Gorman et al. 2014, ecology | ⚠️ 9/12 → ✅ 12/12 | Three chinstrap values differ from Table 2. The paper's exclusion of pairs that did not complete their clutch, run as a follow-up check, makes all 12 match. Both runs stay on record. |
| [StarDist nuclei detection](examples/stardist-nuclei/) · Schmidt et al. 2018, imaging | ⚠️ 3/9 | Table 1's average precision exact at three IoU thresholds, within 0.006 at six. Reused on [BBBC039](https://bbbc.broadinstitute.org/BBBC039): 0.912 at IoU 0.5, against the paper's 0.864 on its own data. |
| [Inferential reproducibility](examples/medrxiv-inferential-reproducibility/) · a preprint with code, no crate | draft | The crate is recreated from the paper and its code, with the software versions as of publication, and stays draft until a person confirms it. |
| [Demo](examples/demo/) · synthetic nuclei, ImageJ macro | ✅ 2/2 | `recrate demo-data` makes the paper, its crate and "your images". |

**Reuse at half resolution** ([make_half_resolution.py](examples/workflowhub-nuclei-segmentation/make_half_resolution.py)): with each 2×2 block of pixels averaged, the published 3-pixel filter still runs but merges neighbouring nuclei. ReCRATE warns before the run (the filter is in pixels and no pixel size is stated) and after it (the objects are smaller than on the paper's data). With `--pixel-ratio 2` the filter becomes 1.5 pixels, recorded with its rule.

![The same half-resolution field. Left: the published filter size, 385 nuclei, neighbours merged. Right: the filter rescaled for the stated pixel ratio, 425 nuclei, as at full resolution.](docs/img/adjusted_vs_unadjusted.png)

## 🧱 RO-Crate, FAIR and Paper2Agent

An RO-Crate is one folder with a machine-readable description of what belongs together. With one, "reproduce" has a single meaning: run this workflow on this data and compare with these results. Without one, an agent can only guess.

* **In:** the paper's crate, found (WorkflowHub, Zenodo, DataCite, the paper's code), supplied, or recreated as of publication, and validated with the [RO-Crate validator](https://github.com/crs4/rocrate-validator) whenever it changes.
* **Out:** one Workflow Run RO-Crate per run, linked to the paper's crate, exported as a zip.
* **FAIR:** persistent identifiers throughout (DOIs, WorkflowHub and Zenodo ids, ORCIDs) in JSON-LD; RO-Crate 1.1, Workflow RO-Crate and Workflow Run Crate; licence, authors and provenance in every record. Principle by principle: [docs/fair4rs.md](docs/fair4rs.md).

ReCRATE is its own implementation and keeps Paper2Agent's interface (MCP tools that return a message and file paths). What changes: the research is described by a validated RO-Crate instead of being inferred from the code; tools are tested against the published results or the workflow's own test, with sourced tolerances; your data is checked against the declared inputs; and every run is recorded.

## 🚧 Limits and next steps

ReCRATE is a proof of concept, built at the *Reinvent the Wheel* hackathon (London, October 2026).

| Today | Next |
| --- | --- |
| Python runs in the paper's own `uv` environment; R, Quarto and shell use your interpreters. Sandboxed, but the code can still read your files | R environments per paper, and containers |
| Five WorkflowHub ecosystems; most entries are reusable, not reproducible | WDL, COMPSs and KNIME; a published run crate as the expected result; a readiness census of the registry |
| Scalars and tables are compared; `timeseries` is tested on synthetic data only | IoU for label images, tests for distributions, reruns to measure method noise |
| Follow-ups for software and data versions are proposed, not run | Running them in pinned environments |
| Methods needing a GPU, a cluster, very large or restricted data, or a paid API are described, not run | An evaluation on 20 to 50 papers and WorkflowHub entries |
| Deposit is manual (a ready-to-deposit zip) | Automated deposit to Zenodo and WorkflowHub |

## 📖 Documentation

* [docs/architecture.md](docs/architecture.md): modules, the `/recrate` pipeline and its subagents, where runs happen.
* [docs/crate-spec.md](docs/crate-spec.md): what an input crate declares, including a workflow's invocation recipe.
* [docs/domain-profiles.md](docs/domain-profiles.md): the data checks per field, and how to add a profile.
* [docs/fair4rs.md](docs/fair4rs.md): FAIR4RS, principle by principle.
* [docs/demo-script.md](docs/demo-script.md): a three-minute demo.
* [scripts/workflowhub/README.md](scripts/workflowhub/README.md): real pipelines end to end, locally, in the VM, on a remote machine and in CI.
* [CONTRIBUTING.md](CONTRIBUTING.md): `ruff check src app tests`, `mypy` and `pytest` before a pull request.

## 📝 Citing, licence, acknowledgements

Cite ReCRATE with [CITATION.cff](CITATION.cff). Code is under the [MIT licence](LICENSE). Example data and papers keep their own licences: see each example's `SOURCE.md`.

ReCRATE takes the idea of [Paper2Agent](https://github.com/jmiao24/Paper2Agent) by Jiacheng Miao and colleagues and builds it on the FAIR principles for research software and RO-Crate, drawing on the [RO-Crate](https://www.researchobject.org/ro-crate/) and [Workflow Run Crate](https://www.researchobject.org/workflow-run-crate/) communities, the [RO-Crate validator](https://github.com/crs4/rocrate-validator), [WorkflowHub](https://workflowhub.eu) and the [Galaxy imaging tools](https://github.com/BMCV/galaxy-image-analysis).
