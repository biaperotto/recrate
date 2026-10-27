# Architecture

## From sources to a research object

`recrate agentify` builds an agent folder:

```
agents/my_paper/
  agent.json        manifest: title, DOI, crate source, and per method its readiness and verdict ladder
  paper/            the reading package: paper.md (text by section), tables as CSV, figures, supplement
  crate/            the input RO-Crate: one ComputationalWorkflow per method (supplied, derived or drafted)
  source-crate/     a Galaxy Workflow RO-Crate as published, unchanged (when the crate was derived from one)
  environment.json  software and versions as of publication, each with how the version was determined
  code/             the paper's repository, if cloned
  env/              the paper's own Python environment (recrate env), with recrate-env.json: how it was built
  work/             the /recrate agent's stages (state.json) and, when run from the app, build.json and build.jsonl
  runs/             every run: reproductions, follow-up checks and reuses, as Workflow Run Crates
```

| Input | What happens |
| --- | --- |
| RO-Crate (folder, zip, WorkflowHub, Zenodo) | Used as published when it is a ReCRATE crate. A Workflow RO-Crate from WorkflowHub is first *derived* into one: for Galaxy, inputs, outputs and parameters from the `.ga` file and the published data and expected outputs from its Planemo test (`galaxy.py`); for nf-core, CWL and Snakemake, from their own conventions (`adapters.py`): parameters, the published test as example data and expected results, and an *invocation recipe* that says how the engine is called, which named inputs map to which files, and whether the run needs the network or a container runtime. The original stays in `source-crate/`, and `workflowhub.json` keeps what the registry knows (DOI, EDAM topics, parsed inputs). |
| Paper, with a crate published somewhere | `crate_search.py` looks in the paper's links, WorkflowHub, Zenodo, DataCite and the code repository, and uses what it finds, saying where. |
| Paper only | `paper_crate.py` recreates a crate as of publication (date, software, data), marked draft. It has no runnable method until one is described (by the `/recrate` agent, or `--draft claude`). |

## What "reproduced" means

`model.py` defines the vocabulary everything else uses.

**Readiness** (before running, `agent.diagnose`): `blocked` when no runner here can run the workflow, naming
what is missing (an unported Galaxy tool, an interpreter that isn't installed); `reusable` when it runs but the
research publishes no result to compare a run with (most WorkflowHub entries: a workflow registered without a paper
or a test), so the deliverable is "runs, checks your data, records", not reproduction; `ready-with-gaps` with the
gaps (no published data, a tolerance without a source, an input property the crate doesn't state); otherwise
`ready`. Ready never means reproduced. For a workflow engine, ready also means ReCRATE knows how to call the
workflow: either the crate carries an invocation recipe or the workflow follows ReCRATE's own contract; a pipeline
that takes neither is `blocked` ("no adapter knows how to call this pipeline"), as is a recipe whose container
runtime is not installed.

**The verdict ladder**, per method, each step `passed`, `partial`, `failed` or `not checked`:

1. *Materials found*: workflow, published data, published results.
2. *Runs*: the workflow ran on the published data.
3. *Same numbers*: within tolerance, as a count (`9/12`).
4. *Same conclusion*: set only by a person (`review_conclusion`), stored as an `AssessAction`.

The older single status (`validated`, `differs`, `failed`, `not validated`) is derived from the ladder and kept in
`agent.json` for compatibility. The verdict sentence at the top of the app is written from the ladders.

**Tolerances with a source.** Every tolerance is `tolerance:<column>` (relative, on the mean) or
`tolerance_abs:<column>` (absolute, per item), with `tolerance_source:<column>`: `paper-rounding`,
`paper-stated` (with the quote), `method-noise` (with the number of reruns) or `person` (with name and reason).
A tolerance without a source gives no verdict on those numbers. `recrate check-description` rejects a drafted
tolerance without one, checks quotes against the paper, and compares `paper-rounding` with the decimals printed.

**Output kinds.** Each output declares a `kind`; `scalar`, `table` and `files` (one row per produced file: sha1 exact,
else size, as a workflow's own test states them, tolerance source `test-stated`) are compared, any other kind leaves
"same numbers" at "not checked", with the reason.

**Parameters with meaning.** Each parameter may declare a `unit`, a `scaling` rule (`scales_with:<property>^<exp>`,
or the older `inverse_pixel_size` / `inverse_pixel_area`), a `role` (`free`, `fixed`), `sensitive_to` (a check whose
warning makes it the user's decision), `changes_question` (changing it asks a different question; the run is
labelled so) and the paper's `quote`. `model.parameter_plan` sorts them into adjusted, needs your decision, and as
published; the app, `recrate check` and the MCP `check_my_data` tool all show the same plan. Changing a value
always needs a reason.

**Follow-up checks** (`followup.py`). When the numbers differ, ReCRATE proposes checks: those declared in the
crate (e.g. an exclusion the paper states in words), the rounding convention, and the software and data versions
(listed with what they need until they can run). Each is its own run crate, linked to the reproduction by
`recrate follow-up of`, and kept whether or not it closes the gap. A follow-up never changes the method's verdict.

## Running a method

1. **Check** the data with the crate's domain profile (`profiles/`): `ok`, `warn` or `fail`. A failure stops the
   run; a warning can carry a parameter adjustment.
2. **Run** with the runner chosen from the workflow's language (`runners/`): Galaxy (ported tools), Fiji for ImageJ
   macros, or the paper's own Python, R or shell script. Unknown languages are refused.
3. **Compare** (reproduction, follow-up) with the published results, or **check the results** (reuse) against
   what the paper reported: same order of magnitude, and for images objects per area and object size.
4. **Record** a Workflow Run Crate (`runcrate.py`): the workflow, software versions, inputs with SHA-256,
   parameter values with their source and reason, outputs, overlays, figures (each with the action that made it),
   the input check, the comparison (`comparison.csv`, per value: published, reproduced, tolerance and source),
   the ladder, and `isBasedOn` the paper's crate.
5. **Review**: a person's check of the overlays, or of the conclusion, is an `AssessAction` with their name.
6. **Publish back** (`record.py`): the run joins the research object's record and is zipped for deposit.

Run crates pass every required check of the RO-Crate validator for RO-Crate 1.1, Workflow RO-Crate 1.0, Process
Run Crate 0.5 and Workflow Run Crate 0.5 (CI runs it). At the recommended level they pass 121 to 123 of 136 checks.

## One set of functions, three front ends

The app (`app/app.py`), the CLI (`cli.py`), the chat (`chat.py`) and the MCP server (`mcp_server.py`) call the same
core functions: `agentify`, `diagnose`, `reproduce`, `run_on_data`, `check_inputs`, `run_followup`,
`make_run_figure`, `review_conclusion`, `publish_back`.

The MCP server mirrors Paper2Agent's three parts:

| MCP part | ReCRATE |
| --- | --- |
| Resources | `paper://text`, `paper://info`, `paper://crate` |
| Tools: read the paper | `about_paper` (with the verdict and ladders), `search_paper`, `describe_method`, `list_tables`, `get_table`, `list_figures`, `get_figure` |
| Tools: check and run | `check_my_data`, `reproduce`, `run_on_my_data`, one `run_<method>` per method, `followup_checks`, `run_followup_check` |
| Tools: figures | `make_figure` |
| Tools: record | `review_conclusion`, `approve_run`, `methods_text`, `publish_run`, `reproducibility_record`, `report_page`, `draft_crate` |
| Prompts | `reproduce_paper`, `apply_to_my_data` |

Tools that produce files return `{"message", "artifacts": [{"description", "path"}]}` with absolute paths, as
Paper2Agent's tools do. `tests/test_mcp_server.py` starts the server over stdio and calls every tool on the nuclei
example, checking the count (20 nuclei), not only that the tool answers.

## The paper's own environment

`recrate env <agent>` (env.py) builds `<agent>/env` with `uv` (or venv and pip): from the code's requirements or
package files, else the Python packages and versions ReCRATE found as of publication. Python methods then run with
that interpreter, never ReCRATE's, and every run writes `outputs/packages.txt` and lists each installed package as
software in the run crate (FAIR4RS R2).

## Tools as Paper2Agent makes them

Each method is one MCP tool with a typed signature built from the crate (`typed_method_tool`): every parameter is an
argument with its type, unit, the paper's sentence and the published value as its default. A method may declare no
data input at all (a question-style tool, e.g. "score this sequence"). A value that differs from the published one
is passed on as a recorded override and needs a reason.

## The `/recrate` agent (Claude Code)

A paper alone rarely says how to run its methods. The `/recrate` skill (`src/recrate/skill/`) is built the way
Paper2Agent builds its agents: a coordinator hands the work to specialist subagents, each in its own context, so
every claim is checked by someone who didn't make it.

1. A **paper reader** and a **code and environment** specialist work in parallel: methods, parameters with the
   sentences that state them, published results read from the tables; code, dependencies, data (size, licence,
   access) and what each method needs against this computer (`recrate hardware`).
2. One **method implementer** per runnable method: a thin wrapper around the authors' code, or a re-implementation
   when there is none (said as such), the published values as printed, tolerances with their sources, and
   follow-up checks for steps the paper states in words.
3. `recrate check-description` checks quotes, published values and tolerance sources without a model; ReCRATE
   builds the crate and reproduces each method.
4. A fresh **verifier** re-derives everything from the paper and code, reviews each run, and runs follow-up checks.
It runs from Claude Code (`/recrate`), from the command line (`recrate build <agent>`, build.py), or from the app's
**Make runnable** button, which starts the same build in the background on the person's Claude subscription and
shows its progress (`work/build.jsonl`). The environment specialist builds the paper's environment and lists the
code's tutorials; tutorials a user would want become tools too, tested against their recorded outputs
(tolerance source `code-stated`).

5. Before delivery: `recrate workflow-check` (stages in order, files unchanged since checked, verifier independent),
   `recrate verify-server` (the MCP server, as a client uses it) and `recrate package` (a zip with `USAGE.md`).

Methods that need a GPU, a cluster, data too large to fetch, or access that must be requested are described and
marked *not runnable here*, with what is missing. Restricted data is never fetched.

## Code map

```
src/recrate/
  model.py         the field-independent core: verdict ladder, tolerance sources, output kinds, parameter roles
                   and the three-group parameter plan, what the paper doesn't say
  sources.py       identifiers and uploads → paper, crate, code, data (WorkflowHub, Zenodo, DOI, arXiv)
  crate_search.py  a published RO-Crate for a paper: its links, WorkflowHub, Zenodo, DataCite, its code repository
  reading.py       the paper's reading package: text by section, tables as CSV, figures, supplement (Europe PMC)
  paper_crate.py   an RO-Crate recreated for a paper published without one
  environment.py   publication date; software named by the paper and code; versions as of publication
  agent.py         sources → agent; readiness, reproduction and the ladder per method; conclusion reviews
  galaxy.py        Galaxy Workflow RO-Crates: derive a ReCRATE crate (any workflow); run ported Galaxy imaging tools
  adapters.py      nf-core, CWL and Snakemake crates from WorkflowHub: parameters, test data and expected outputs from
                   their own conventions, and the invocation recipe the engine runner follows
  sandbox.py       the paper's code runs confined (sandbox-exec, bubblewrap): no network unless the recipe needs it,
                   writes only to the run's folders, no secrets in its environment
  crate.py         read an input crate (one method per workflow); draft.py builds one from a description
  profiles/        how data is checked: generic, bioimaging (calibration, intensity, object size), timeseries
                   (sampling rate, channels); base.rescale applies every field's scaling rules
  runners/         Galaxy (ported tools), ImageJ macros, Python, R and shell scripts; readiness()
  pipeline.py      reproduce / check / run on your data
  followup.py      follow-up checks when the numbers differ, each its own recorded run
  compare.py       published vs reproduced, with tolerances and their sources
  runcrate.py      Workflow Run Crates, figures, reviews, readable summaries; record.py: the record
  figures.py       figures plotted from run outputs: published vs reproduced, image panels, comparison charts
  report.py        one self-contained HTML report page per research object
  workspace.py     comparison runs, parameter sources, flags, export bundle
  chat.py          the chat: tools; open models (OpenAI-compatible), Claude, or simple commands
  mcp_server.py    the agent over MCP; server_check.py checks it over stdio; package.py zips it with USAGE.md
  verify.py        check-description: quotes, published values and tolerance sources against the paper
  hardware.py      CPUs, memory, GPU, interpreters; whether a method's needs fit this computer
  workflow_state.py  the /recrate skill's stages, checksums and independence check (work/state.json)
  skill/           the /recrate Claude Code skill and its specialist subagents
app/app.py         the web app: Reproduce, Reuse, Record, and the chat
```

Downloaded workflows never run without your say: ReCRATE's own ports run straight away, a crate's own script or
macro only after you confirm you trust its source.

## Where things live

| Thing | Where | Why |
| --- | --- | --- |
| The methods | Workflow RO-Crate on WorkflowHub, with a DOI the paper cites | WorkflowHub registers workflows |
| Each run | A run crate; deposit on Zenodo with `isBasedOn` → the paper's crate | Runs are data products |
| The record | Collected from run crates pointing at the paper's crate | Anyone can rebuild it |

In the proof of concept the record is a local `reproducibility-record.json` and deposits are zips.

## Compared with related tools

Based on their public descriptions; check their repositories for current features.

| | Paper2Agent | FlowProof | lightcone-cli (ASTRA) | ECAA-workflow | ReCRATE |
| --- | --- | --- | --- | --- | --- |
| Starts from | Paper + repo | Registered pipelines | Paper, code or question | Description of a new analysis | Paper, or paper + crate |
| Spec | Tutorials as ground truth | Own manifest | Own `astra.yaml` | Own typed RO-Crate | Standard Workflow RO-Crate |
| RO-Crate | None | Output | Export | Package | Input and output |
| Your data fits the method? | LLM judgment | Not described | Not described | Not described | Declared spec + domain profile |
| Results back to the paper | No | No | No | No | Reproducibility record |

Run crates follow the same Process / Workflow Run Crate profiles as FlowProof and lightcone-cli.

## Design choices

* **Deterministic numbers.** Methods compute results; the model orchestrates and explains. It never sets a verdict,
  a tolerance or a published value.
* **Per-measurement tolerances, each with a source,** rather than one global threshold.
* **Calibration from metadata**, not image content: a model cannot tell 0.65 µm from 1.3 µm per pixel by looking.
* **Drafts are labelled.** A crate drafted from a paper stays `draft` until a person confirms it.
* **Append-only record.** Follow-ups and reviews add runs and actions; nothing replaces the first result.
* **Standards first**: schema.org, Bioschemas `FormalParameter`, Workflow RO-Crate, Workflow Run Crate.
