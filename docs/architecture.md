# Architecture

## Diagnose: from a paper link to a readiness verdict

Before a paper becomes an agent, `recrate diagnose <link> --out <folder>` answers one question with plain code (no LLM): is everything needed to **validate and reuse** this paper's methods present?

```
paper link (DOI / URL / arXiv / bioRxiv / file)
  ├─ fetch.py   resolve → landing page → open full text (citation_pdf_url / HTML); collect Signposting
  ├─ locate.py  Signposting → Crossref/DataCite relations → paper code links; download the first good crate
  │             (Zenodo/Figshare file, GitHub repo, WorkflowHub RO-Crate); every candidate logged in locate.json
  └─ diagnose.py  checks against docs/crate-spec.md → diagnosis.json + diagnosis.md
```

Each method is reported as **runnable** (workflow file, a runner for its language, parameters), **validatable** (runnable + example data + reported results) and **reusable** (validatable + a known profile and its input properties). Overall readiness is **ready** (every method validatable and reusable), **ready-with-gaps** (at least one validatable) or **blocked** (none). "Ready" means ready to validate, never "reproduced". Profile-specific input-property checks come from a declare-only `DomainProfile.diagnose_inputs` hook, so each domain owns them. When no crate is found, the user uploads one (`--crate folder|zip|url`) or drafts it (`--draft claude`), and the same checks run on the result. Exit codes: 0 diagnosed, 3 no crate found and no fallback, 2 error.

## From paper to paper agent

`recrate agentify` builds a **paper agent** folder:

```
agents/my_paper/
  agent.json      manifest: title, DOI, crate source (found+URL / uploaded / supplied / drafted / none), readiness, diagnosis, methods and validation
  paper/          paper.md (text), paper.json (title, DOI, code and data links)
  crate/          the RO-Crate packaging (supplied or drafted): one ComputationalWorkflow per method
  code/           the paper's repository, if cloned
  recrate/        ReCRATE output: validation runs and every later run, as Workflow Run Crates
```

| Input | What happens |
| --- | --- |
| Paper + RO-Crate | The crate is copied; every method with example data and reported results is validated. |
| Paper only, `--draft claude` | Claude reads the paper (and the cloned code) and writes a JSON description; ReCRATE builds the crate (marked `draft`) and validates it. |
| Paper only, Claude Code | `/agentify <folder>` reads, drafts, validates and repairs interactively (up to 6 attempts per method, as Paper2Agent's test loop). |
| Paper only, no draft | The agent answers questions from the paper (like Paper2Agent's resource layer); `DRAFT_ME.md` explains how to add methods. |

**Validation** reruns each method on the paper's own data and compares with the reported results using per-measurement tolerances from the crate. Statuses: `validated`, `differs`, `failed` (with the error), `not validated` (no example data or no reported results). `--strict` exposes only validated methods.

## Serving

`recrate serve <agent>` exposes the paper agent over MCP, mirroring Paper2Agent's three parts:

| MCP part | ReCRATE |
| --- | --- |
| Resources | `paper://text`, `paper://info`, `paper://crate`, `paper://diagnosis` |
| Tools | `about_paper`, `diagnosis`, `search_paper`, `describe_method`, one `run_<method>` per method, `check_my_data`, `reproduce`, `run_on_my_data`, `approve_run`, `methods_text`, `publish_run`, `reproducibility_record`, `draft_crate` |
| Prompts | `reproduce_paper`, `apply_to_my_data` |

The server's instructions tell the agent to check data before running, explain every adjustment, and never invent numbers.

## Running a method on your data

1. **Check** with the crate's domain profile (`profiles/`): each check is `ok`, `warn` or `fail`. A failure stops the run; a warning can carry an adjustment.
2. **Run** with the runner chosen from the workflow's language (`runners/`): Fiji headless for ImageJ macros, the paper's own script for Python.
3. **Record** a Workflow Run Crate (`runcrate.py`): workflow, software versions, inputs with SHA-256, parameter values with adjustments and reasons, outputs, overlays, the input check and (when reproducing) the comparison, `isBasedOn` the paper's crate.
4. **Approve**: a person's visual check of the overlays is stored as an `AssessAction`.
5. **Publish back** (`record.py`): the run joins the paper's reproducibility record and is zipped for deposit.

## Where things live

| Thing | Where | Why |
| --- | --- | --- |
| The methods (recipe) | Workflow RO-Crate on WorkflowHub, with a DOI the paper cites (`recrate export <agent> <out>` packages `crate/` as a `.crate.zip` WorkflowHub ingests) | WorkflowHub registers workflows |
| Each run | Run crate with the results; deposit on Zenodo with `isBasedOn` → paper crate | Runs are data products |
| The reproducibility record | Collected from run crates pointing at the paper's crate | Anyone can rebuild it |

In the proof of concept the record is a local `reproducibility-record.json` and deposits are zips.

## Compared with related tools

Based on their public descriptions; check their repositories for current features.

| | Paper2Agent | FlowProof | lightcone-cli (ASTRA) | ECAA-workflow | ReCRATE |
| --- | --- | --- | --- | --- | --- |
| Starts from | Paper + repo | Registered pipelines | Paper, code or question | Description of a new analysis | Paper, or paper + crate |
| Spec | Tutorials as ground truth | Own manifest | Own `astra.yaml` | Own typed RO-Crate | Standard Workflow RO-Crate |
| RO-Crate | None | Output | Export | Package | Input and output |
| Your data fits the method? | LLM judgment | Not described | Not described | Not described | Declared spec + domain profile |
| GUI-derived workflows (Fiji) | No | No | No | No | Yes |
| Results back to the paper | No | No | No | No | Reproducibility record |

We do not compete on run provenance itself: run crates follow the same Process / Workflow Run Crate profiles as FlowProof and lightcone-cli.

## Design choices

* **Deterministic numbers.** Methods compute results; the agent orchestrates and explains.
* **Per-measurement tolerances** rather than one global threshold.
* **Calibration from metadata**, not image content: an LLM cannot tell 0.65 µm from 1.3 µm per pixel by looking.
* **Drafts are labelled.** A crate drafted from a paper stays `draft` until a person confirms it.
* **Standards first**: schema.org, Bioschemas `FormalParameter`, Workflow RO-Crate, Workflow Run Crate.
