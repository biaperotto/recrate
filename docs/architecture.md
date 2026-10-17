# Architecture

## Inputs: paper only, or paper with a crate

| Input | What happens |
| --- | --- |
| Paper only (as in Paper2Agent) | A chat agent reads the paper and repo and calls the MCP `draft_crate` tool with a description of the method (workflow, inputs, calibration, parameters, reported results). ReCRATE writes a Workflow RO-Crate marked `draft`. The user confirms it. |
| Paper with a crate | The crate's declared spec is used directly, so checks are stricter and nothing is guessed. |

Either way the outputs are the same run crates, so a paper published without a crate leaves with one.

## Flow

1. **Load** the input crate (`crate.py`) into a `MethodSpec`.
2. **Check** the data with the crate's domain profile (`profiles/`). Each check is `ok`, `warn` or `fail`. A failure stops the run. A warning can carry an adjustment (for example rescaling a size parameter).
3. **Run** the workflow with a runner (`runners/`). Fiji runs headless through PyImageJ; the Python reference runner mirrors the demo macro for development and tests.
4. **Compare** with the reported results when reproducing (`compare.py`), with per-measurement tolerances from the crate.
5. **Record** the run as a Workflow Run RO-Crate (`runcrate.py`): workflow, software versions, inputs with SHA-256, parameter values with adjustments and reasons, outputs, overlays, the input check and the comparison as `AssessAction`s, and `isBasedOn` the paper's crate.
6. **Approve**: a person's visual check of the overlays is added as another `AssessAction` with that person as agent.
7. **Publish back** (`record.py`): the run is added to the paper's reproducibility record and zipped for deposit.

## Where things live

| Thing | Where | Why |
| --- | --- | --- |
| The method (recipe) | Workflow RO-Crate on WorkflowHub, with a DOI the paper cites | WorkflowHub registers workflows, not individual runs |
| Each run | Run crate next to the results; deposit on Zenodo with `isBasedOn` → paper crate | Runs are data products |
| The reproducibility record | Collected from run crates that point at the paper's crate | Anyone can rebuild it from the deposits |

In this proof of concept the record is a local `reproducibility-record.json` beside the paper crate, and deposits are zip files. Automating Zenodo upload and a WorkflowHub collection per paper is future work.

## How ReCRATE differs from related tools

Based on their public descriptions; check their repositories for current features.

| | FlowProof | lightcone-cli (ASTRA) | ECAA-workflow | Paper2Agent | ReCRATE |
| --- | --- | --- | --- | --- | --- |
| Starts from | Registered pipelines | Paper, code or question | Description of a new analysis | Paper + repo | Paper, or paper + crate |
| Spec format | Own manifest | Own `astra.yaml` | Own typed RO-Crate | Tutorials as ground truth | Standard Workflow RO-Crate |
| RO-Crate role | Output | Export | Package | None | Input and output |
| Checks data fits the method | Not described | Not described | Not described | LLM judgment via prompts | Declared spec + domain profile |
| GUI tools (Fiji) | No | No | No | No (needs runnable tutorials) | Yes |
| Results back to the paper | No | No | No | No | Reproducibility record |

What we deliberately do not compete on: run provenance itself. We emit the same Workflow Run Crate profiles as FlowProof and lightcone-cli so records are interoperable.

## Design choices

* **Deterministic numbers.** The workflow computes results; the agent only orchestrates and explains.
* **Per-measurement tolerances** instead of one global threshold (Paper2Agent uses 3 % and perceptual hashes for figures).
* **Calibration from metadata**, not from the image content: an LLM cannot tell 0.65 µm from 1.3 µm per pixel by looking.
* **Human check recorded**, not just "human in the loop" in principle.
* **Standards first**: schema.org, Bioschemas `FormalParameter`, Workflow RO-Crate, Workflow Run Crate.
