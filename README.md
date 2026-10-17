<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/img/logo-dark.svg">
    <img src="docs/img/logo.svg" alt="ReCRATE: FAIR recreation of scientific research" width="460">
  </picture>
</p>

<p align="center"><b>Recreate a piece of published research, validate it on its own data, and reuse its methods on yours: checked and recorded.</b></p>

---

A published method is hard to reuse. Its paper, workflow, code and data live in different places; the software it ran on has moved on; and when you apply it to your own data it usually still runs, and gives a believable wrong answer when your data differs from the original.

ReCRATE puts the [FAIR principles for research software (FAIR4RS)](https://doi.org/10.1038/s41597-022-01710-x) to work for the person who wants to reuse a method. It starts from the idea, introduced by [Paper2Agent](https://github.com/jmiao24/Paper2Agent), of turning a paper into an agent you can talk to, and builds it on open standards for describing research ([RO-Crate](https://doi.org/10.3233/DS-210053)) and for recording what was run ([Workflow Run RO-Crate](https://doi.org/10.1371/journal.pone.0309210)).

## How it works

**1. Start from the research.** Give ReCRATE what you have:

* **its RO-Crate**: a zip or folder, a WorkflowHub link or DOI, a Zenodo DOI. It is used as published: workflow, inputs, parameters, software, example data and results. For Galaxy workflows, ReCRATE also reads the parameters and tests inside the workflow files;
* **or its paper**: a PDF, a DOI or an arXiv link. ReCRATE first **looks for an RO-Crate already published for it**: a link in the paper, a workflow WorkflowHub registers for the paper's DOI, a Zenodo deposit that cites it and is an RO-Crate, a related record in DataCite, or a crate in the paper's code repository. If it finds one, it uses it, and says where and why. If not, it recreates the paper's RO-Crate *as the research stood when the paper was published*: the publication date, the software and models the paper names, its code and the data it cites (Zenodo, Hugging Face, figshare...). Each piece of software gets a version and a note on how it was determined: stated in the paper, pinned in the code, or the latest release available on the publication date (PyPI, conda-forge, Bioconda, GitHub). Software first released after the paper is reported as such, never back-dated.

  The paper is kept as a **reading package**, so neither people nor agents have to work from garbled PDF text. When it is open access in Europe PMC, that means its text by section, every table as a CSV with its caption, every figure as an image, and its supplementary files. For any other PDF, it means the text plus the figures embedded in it. Published values are checked against the table cells.

**2. Validate the research** on its own data: each method is run and compared with the published result, measurement by measurement, within a stated tolerance.

**3. Reuse its methods on your data.** Your data is checked against what each method expects (format, dimensions, channels, pixel size...). Parameters are adjusted where that keeps their meaning, for example a filter size given in pixels when your pixels are larger, and every change is recorded with its reason. When the research doesn't state its pixel size, ReCRATE says so, and you state how yours compares. You can set any value yourself, with a reason.

**4. Keep the record.** Every run, validation or reuse, is a Workflow Run RO-Crate (checked with the RO-Crate validator) linked to the original: what ran, with which software, on which data (with checksums), with which settings, and who reviewed it. The research object builds a reproducibility record over time, and each run exports as a bundle for your own paper: run crate, methods paragraph, references and figures.

## Proof of concept: one workflow, end to end

[WorkflowHub #771](https://workflowhub.eu/workflows/771) is a published Galaxy workflow that segments and counts cell nuclei in fluorescence images, with a test that says what it should find. [examples/workflowhub-nuclei-segmentation/](examples/workflowhub-nuclei-segmentation/) holds it unmodified, with three full fields of public images from an siRNA screen ([Zenodo 10.5281/zenodo.3362976](https://doi.org/10.5281/zenodo.3362976)).

**1. Reproduced exactly.** ReCRATE reads the method from the workflow and its test, and runs it without a Galaxy server: each step is a port of the Galaxy tool's own code at the pinned version, run in the workflow's own wiring. It finds the published 20 nuclei, with the same label image pixel for pixel. On the full research images it finds 425, 413 and 307 nuclei per field.

**2. Reused without the silent failure.** Now take the same three fields at half the resolution (each 2×2 block of pixels averaged, as from a binned camera or a lower magnification: [make_half_resolution.py](examples/workflowhub-nuclei-segmentation/make_half_resolution.py)). The workflow still runs, and with its published smoothing filter of 3 pixels it reports 385, 385 and 281 nuclei: 7 to 9% of the nuclei missing, because neighbouring nuclei merge, and the merged objects inflate the mean nucleus size by 20 to 29%. Nothing in the output says anything is wrong.

ReCRATE knows from the crate that the filter size is a length in pixels. The research states no pixel size, so ReCRATE says so before running. Once you state that your pixels are twice as large (`--pixel-ratio 2`), it rescales the filter to 1.5 pixels and records why. The counts come back to 425, 414 and 306, matching full resolution to within one nucleus per field.

![The same half-resolution field, in the region where the two runs differ most. Left: the published filter size, unchanged; neighbouring nuclei merge. Right: the filter rescaled by ReCRATE for the stated pixel ratio; the nuclei are separated as at full resolution.](docs/img/adjusted_vs_unadjusted.png)

**3. Recorded, and the record validates.** Both runs are Workflow Run Crates: the workflow, the software versions, the images with checksums, the parameters used, and the adjustment with its reason. They pass every required check of the [RO-Crate validator](https://github.com/crs4/rocrate-validator) for RO-Crate 1.1, Workflow RO-Crate 1.0, Process Run Crate 0.5 and Workflow Run Crate 0.5, and CI checks this on every push.

```bash
recrate agentify --crate examples/workflowhub-nuclei-segmentation/crate --out agents/nuclei     # 1. reproduce
python examples/workflowhub-nuclei-segmentation/make_half_resolution.py
recrate run agents/nuclei examples/workflowhub-nuclei-segmentation/my-data-half-resolution \
    --pixel-ratio 2 --who "Your name"                                                           # 2. reuse
rocrate-validator validate -p workflow-run-crate-0.5 agents/nuclei/runs/<run>                   # 3. validate
```

### When the published result doesn't come back

[examples/penguins-dimorphism/](examples/penguins-dimorphism/) is Gorman, Williams & Fraser (2014, *PLOS ONE*, [doi:10.1371/journal.pone.0090081](https://doi.org/10.1371/journal.pone.0090081)) on sexual size dimorphism in Antarctic penguins, with its open data (CC0). ReCRATE recomputes the paper's Table 2 from that data, with the method as the paper states it, and compares every value within the paper's rounding.

ReCRATE found three values in Table 2 that the published data doesn't give: the chinstrap size dimorphism index for culmen length (−0.097 recomputed vs −0.11 published), flipper length (−0.043 vs −0.05) and body mass (−0.117 vs −0.10). The other nine values, including every Adélie and gentoo value, reproduce. The data holds the same 68 sexed chinstraps as the paper, so the difference lies in a step of the calculation that the paper doesn't describe. That is what validation is for: it says exactly where a published result can't be recovered from the published data.

### Papers without a crate

From *Cell Detection with Star-convex Polygons* ([arXiv:1806.03535](https://arxiv.org/abs/1806.03535)), ReCRATE recreates the crate: published June 2018, and StarDist's first release (0.1.0, 26 June 2018) came *after* the paper, which the crate says rather than inventing a version. From *ReXGroundingCT* ([arXiv:2507.22030](https://arxiv.org/abs/2507.22030)) it records the dataset on Hugging Face and the models the paper used (GPT-4, GPT-4o). A paper alone rarely says how to run its methods; the next step, `/recrate` in Claude Code, reads the paper and its code and describes them so they can be validated.

## Run it

```bash
git clone <this repo> recrate && cd recrate
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[app]"
streamlit run app/app.py
```

The app has two cards; drag the divider between them to give either more room.

* **Chat.** Paste a DOI or link, or attach a paper or crate. Once the research object is open, ask about it, or say *reproduce*, *check my data*, *run*.
* **Workstation**, with one main action at the bottom of each section:
  * **Validate** · *Reproduce all*: the verdict first, then each method against its published result; the software environment as of publication; the source (text, tables, figures, supplement).
  * **Reuse** · *Run on my data*: your data (upload, or point at a folder for large datasets), whether it fits each method, the parameters next to the sentence of the paper that states them, and the results, with a per-item review and a figure builder for images.
  * **Record** · *Export bundle*: every run, the selected one as a readable run crate, and the research object's files.

**No API key needed.** The chat works with an open model on your own computer, for free: install [Ollama](https://ollama.com), run `ollama pull qwen2.5:7b`, and ReCRATE finds it (LM Studio and llama.cpp work too). Without a model it understands simple commands. **Connect** in the header shows the options, including Claude with an API key. Either way, the numbers come from running the methods, never from the model.

**Making a paper's methods runnable: the `/recrate` agent.** A paper alone rarely says how to run its methods, so ReCRATE ships a Claude Code skill, built the way [Paper2Agent](https://github.com/jmiao24/Paper2Agent) builds its agents: a coordinator that hands the work to specialist subagents, each in its own context, so that every claim is checked by someone who didn't make it.

1. a **paper reader** and a **code and environment** specialist work in parallel. The reader finds the methods, their parameters with the sentences that state them, and the published results, read from the tables. The specialist covers the code and its dependencies, the data (size, licence, access), and what each method needs against what this computer has (`recrate hardware`: CPUs, memory, CUDA or Apple GPU, Python, R, Docker, Fiji…);
2. one **method implementer** per runnable method, in parallel: a thin wrapper around the authors' code in its own language (Python, R or shell), or a re-implementation when there is no code, said as such. It records the published values exactly as printed and makes a first run on the published data;
3. `recrate check-description` checks, without a model, that every quoted parameter and every published value is really in the paper (a row's values printed together, as in a table), then ReCRATE builds the crate and validates it;
4. a fresh **verifier** re-derives everything from the paper and code and reviews each run; its corrections go back through the check and the validation;
5. before delivery, three checks:
   * `recrate workflow-check` confirms every stage ran in order, that no file changed after it was checked, and that the verifier was not an implementer. Each stage is recorded in `work/state.json` with checksums, so an interrupted run resumes where it stopped;
   * `recrate verify-server` launches the agent's MCP server the way a client will and calls its tools, including a reproduction of each method;
   * `recrate package` zips the agent with a `USAGE.md` and checks it again from a fresh folder;
6. the result: the RO-Crate (a draft until a person confirms it), a `REPORT.md` with each method's verdict and what the paper doesn't state, and the agent, ready to talk to or to share as a zip.

Methods that need a GPU, a cluster, data too large to fetch or access that must be requested are described and marked *not runnable here*, with exactly what is missing. In this repository the skill is ready (`/recrate <paper PDF, DOI, arXiv link or crate>` in Claude Code); elsewhere, `recrate install-skill` (or `--user` for all your projects). It runs on your Claude subscription; then press *Reload* in the app.

From the command line:

```bash
recrate agentify --crate examples/workflowhub-nuclei-segmentation/crate --out agents/nuclei   # or --paper paper.pdf
recrate run agents/nuclei examples/workflowhub-nuclei-segmentation/research-data/B2 --who "Your name"
recrate record agents/nuclei
recrate verify-server agents/nuclei --reproduce   # the agent's MCP server, checked as a client uses it
recrate package agents/nuclei                     # agents/nuclei.zip with USAGE.md, checked from a fresh folder
recrate hardware                                  # what this computer can run
pytest
```

## FAIR4RS in practice

What each principle looks like when a method is reused through ReCRATE (what the proof of concept does today, not a certification):

* **Findable.** It starts from persistent identifiers (paper DOI, WorkflowHub DOI with version, Zenodo DOI), and every run crate keeps the identifier it is based on. Run crates are JSON-LD that registries can index.
* **Accessible.** Sources are fetched by identifier over open protocols; a run crate keeps a copy of the workflow and its metadata, so what was run stays documented if the source moves.
* **Interoperable.** RO-Crate 1.1, Workflow RO-Crate and the Workflow Run Crate profiles, with run crates checked by the RO-Crate validator in CI; workflows are read as published (Galaxy, scripts, macros); runs link to the source, the paper, the software and the person.
* **Reusable.** Methods declare what their input must look like, how parameters scale and the tolerance of each result; your data is checked against them. Licence and provenance travel with every run: who ran what, when, on which data, with which changes and why.

## Under the hood

```
src/recrate/
  sources.py       identifiers and uploads → paper, crate, code, data (WorkflowHub, Zenodo, DOI, arXiv)
  crate_search.py  a published RO-Crate for a paper: its links, WorkflowHub, Zenodo, DataCite, its code repository
  reading.py       the paper's reading package: text by section, tables as CSV, figures, supplement (Europe PMC)
  paper_crate.py   an RO-Crate recreated for a paper published without one
  environment.py   publication date; software named by the paper and code; versions as of publication
  agent.py         sources → agent; validation of each method
  galaxy.py        Galaxy Workflow RO-Crates: derive a ReCRATE crate; run ported Galaxy imaging tools
  crate.py         read an input crate (one method per workflow); draft.py builds one from a description
  profiles/        how data is checked: generic (any workflow), bioimaging (calibration, channels, 2D/3D)
  runners/         Galaxy (ported tools), ImageJ macros, Python, R and shell scripts; unknown languages are refused
  hardware.py      CPUs, memory, GPU, interpreters; whether a method's needs fit this computer
  pipeline.py      reproduce / check / run on your data
  runcrate.py      Workflow Run Crates, reviews, readable summaries; record.py: the reproducibility record
  workspace.py     comparison runs, parameter sources, flags, export bundle; figures.py: figure panels
  chat.py          the chat: tools; open models (OpenAI-compatible), Claude, or simple commands
  mcp_server.py    the agent over MCP; server_check.py checks it over stdio; package.py zips it with USAGE.md
  verify.py        check-description: quotes and published values against the paper and its tables
  workflow_state.py  the /recrate skill's stages, checksums and independence check (work/state.json)
  skill/           the /recrate Claude Code skill and its specialist subagents
app/app.py         the web app
examples/          workflowhub-nuclei-segmentation (real crate and data), penguins-dimorphism (paper and data)
```

Downloaded workflows are never executed without your say: ReCRATE's own ports run straight away, a crate's own script or macro only after you confirm you trust its source.

## Status and limits

Built at the *Reinvent the Wheel* hackathon (London, October 2026); a proof of concept.

* Galaxy support covers the imaging tools of the WorkflowHub example.
* Two data profiles: generic and bioimaging (2D).
* From a paper DOI, the full text comes from Europe PMC (open-access papers, with tables and figures) or an open PDF the publisher links; otherwise only the abstract. A PDF or arXiv link gives the full text.
* Software is detected from a catalogue of common scientific tools and the code's dependency files, outside the paper's reference list; anything else keeps an unknown version unless the paper states one.
* A paper alone rarely makes its methods runnable: describing them takes the `/recrate` agent (Claude Code) and depends on what the paper and its code state.
* The crate properties for inputs, parameter scaling and tolerances are a proposal, not yet an RO-Crate profile.
* Run crates pass every required check of the RO-Crate validator. At the recommended level they pass 122 of 136 checks. The gaps include a version and URL for each piece of software, `encodingFormat` on every file, and a licence and author on the run crate itself.
* "Add to the record" writes a local record and a zip ready to deposit; uploading to Zenodo or WorkflowHub is not automated.

## License

[MIT](LICENSE). Example data keep their own licences (see each example's `SOURCE.md`).
