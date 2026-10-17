# ReCRATE

**Turn a paper into an agent that runs its methods on your own data, checked and recorded.**

A paper is static: to reuse its method you find the code, install it, guess the inputs, and hope your data is comparable. ReCRATE agentifies the paper, in the spirit of [Paper2Agent](https://github.com/jmiao24/Paper2Agent): you talk to the paper, it reproduces its own results, and it runs its methods on your data. On top of that:

* **RO-Crates as input, when available.** If the authors published an RO-Crate (on WorkflowHub or Zenodo), ReCRATE uses its declared methods, inputs, parameters, example data and reported results directly. If not, it starts from the paper alone, finds the code, and drafts the crate, flagged until a person confirms it.
* **Checks that your data fits the method.** The crate declares what the paper's data looked like (for images: pixel size, channels, 2D/3D). ReCRATE reads the same properties from your data, flags mismatches, and rescales parameters when that keeps the method meaning the same thing, recording why. Checks come from **domain profiles**; bioimaging is the first.
* **The paper keeps evolving.** Every run is a Workflow Run RO-Crate linked to the paper, so the paper builds a living reproducibility record: reproduced N times, reused on which data, by whom, with which adjustments.

Proof of concept: bioimaging, with Fiji macros and Python scripts as methods. Built at the *Reinvent the Wheel* hackathon (London, October 2026).

## How it works

```
 Paper (PDF / text) ──┐
                      ├──► recrate agentify ──► paper agent ──► recrate serve ──► Claude (MCP)
 RO-Crate (optional) ─┘        │                     │
   or drafted from the paper   │ validate each       ├─ resources: paper text, crate, method cards
   + its code                  │ method on the       ├─ tools: search_paper, run_<method>, check_my_data,
                               │ paper's data        │         reproduce, methods_text, record ...
                               ▼                     └─ prompts: reproduce the paper, apply to my data
                         runs/  (Workflow Run Crates, linked back to the paper)
```

| | Paper2Agent | ReCRATE |
| --- | --- | --- |
| Input | Paper + code repository | Paper alone, or paper + RO-Crate (+ code) |
| Methods become | MCP tools extracted from tutorials | MCP tools declared in a standard Workflow RO-Crate (supplied or drafted) |
| Ground truth | Repo tutorial outputs | The paper's reported results, stored in the crate |
| Your data | Adapted by the LLM's judgment | Checked against the paper's declared inputs; adjustments applied and recorded |
| Works without notebooks (Fiji macros, GUI-derived workflows) | No | Yes |
| Run records | Own logs | Workflow Run RO-Crates, compatible with other tools |
| After publication | Static agent | Reproducibility record grows with every run |

## Quick start

```bash
git clone <this repo> recrate && cd recrate
pip install -e ".[dev,mcp]"
pytest                                              # 18 tests

# 1. A demo paper with two methods (a Fiji macro and a Python script), its crate, and "your images"
recrate demo-data examples/demo

# 2. Agentify it (paper + crate). Each method is validated against the paper's reported results.
recrate agentify --paper examples/demo/paper.md --crate examples/demo/paper_crate --out agents/demo

# 3. Talk to it
recrate ask   agents/demo "how is debris removed?"
recrate check agents/demo examples/demo/my_images --method count_nuclei
recrate run   agents/demo examples/demo/my_images --method count_nuclei --who "Anna"
recrate record agents/demo

# 4. Or connect it to Claude
claude mcp add demo-paper -- recrate serve "$PWD/agents/demo"
```

What the demo shows: the paper's images were taken at 0.65 µm per pixel, yours at 1.3 µm. The paper removes debris with `min_size_px = 50`. Applied unchanged to your images, that silently drops small nuclei (about 13 per field instead of 22). ReCRATE flags the pixel size, rescales the parameter to 12.5 px, and finds 22 nuclei with a mean area within about 1 % of the paper's, all recorded in the run crate.

![Left: parameters rescaled by ReCRATE, all nuclei found. Right: the paper's parameter applied unchanged, small nuclei silently dropped.](docs/img/adjusted_vs_unadjusted.png)

*Left: `min_size_px` rescaled to 12.5 for 1.3 µm pixels. Right: the paper's 50 applied unchanged.*

## A paper without a crate

```bash
# Paper only: the agent can answer questions about the paper straight away
recrate agentify --paper paper.pdf --fetch-code --out agents/my_paper

# Make its methods runnable, either automatically with Claude (needs ANTHROPIC_API_KEY) ...
pip install -e ".[draft]"
recrate agentify --paper paper.pdf --fetch-code --draft claude --out agents/my_paper

# ... or interactively in Claude Code, which reads the paper and code, drafts, validates and repairs:
#   /agentify agents/my_paper          (see .claude/commands/agentify.md)
```

A drafted crate is marked `draft` until a person confirms it: it is only as good as what the paper states.

## Talking to a paper agent in Claude

```bash
claude mcp add my-paper -- recrate serve /absolute/path/agents/my_paper     # Claude Code
```

Claude Desktop (`claude_desktop_config.json`):

```json
{"mcpServers": {"my-paper": {"command": "recrate", "args": ["serve", "/absolute/path/agents/my_paper"]}}}
```

Then ask, for example: *"What pixel size did the paper use?"*, *"Reproduce the paper's results."*, *"Run count_nuclei on my images in ~/data/plate3 and explain any adjustments."*, *"Write the methods paragraph for that run."*

## Fiji

```bash
pip install -e ".[fiji]"
export FIJI_PATH=/path/to/Fiji.app
```

Without Fiji, the demo macro falls back to a Python reference implementation (scikit-image), so everything can be developed and tested anywhere. Python methods run the paper's own script (`RECRATE_PYTHON` picks the interpreter).

## Web app

```bash
pip install -e ".[app]"
streamlit run app/app.py
```

The paper (title, methods and their validation, ask the paper), pick a method and reproduce it, check my images, results and record (overlays to approve, methods paragraph, run crate download, publish back).

## Repository layout

```
src/recrate/
  agent.py            agentify: paper (+ crate) → paper agent; validation of each method
  paper.py            paper text, DOI and code links, passage search
  drafter.py          draft a crate from the paper with Claude (optional)
  draft.py            build an input crate from a JSON description (one or more methods)
  crate.py            read an input crate; one MethodSpec per method
  profiles/           domain profiles; bioimaging.py reads OME / ImageJ calibration
  runners/            fiji.py (PyImageJ), script.py (the paper's own code), python_ref.py (demo reference)
  workflows/          demo methods: count_nuclei.ijm, size_distribution.py
  compare.py          per-measurement tolerances against reported results
  runcrate.py         Workflow Run Crates, approvals, HTML preview
  record.py           the paper's reproducibility record; publish back
  methods.py          methods paragraph from a run crate
  pipeline.py         reproduce / check / run_on_data
  mcp_server.py       serve a paper agent over MCP
  cli.py
.claude/commands/agentify.md   Claude Code command: draft, validate and repair a crate from a paper
app/app.py            Streamlit interface
docs/                 architecture, crate spec, domain profiles, demo script
tests/
```

## Related work

* [Paper2Agent](https://github.com/jmiao24/Paper2Agent) (Miao et al., *Nature* 2026): the idea of papers as MCP agents, which ReCRATE follows.
* [Workflow Run RO-Crate](https://doi.org/10.1371/journal.pone.0309210) and [WorkflowHub](https://about.workflowhub.eu/): the standards and registry we use.
* [FlowProof](https://glama.ai/mcp/servers/ovw3020c8j), [lightcone-cli](https://pypi.org/project/lightcone-cli/), [ECAA-workflow](https://github.com/SuLab/ECAA-workflow): agents with RO-Crate provenance; we use the same run-crate profiles.
* [fiji-mcp-server](https://pypi.org/project/fiji-mcp-server/), [fiji_mcp](https://github.com/NicoKiaru/fiji_mcp): plain-language control of Fiji.

See [docs/architecture.md](docs/architecture.md) for the full comparison.

## Status and limits

Hackathon proof of concept. One domain profile (bioimaging, 2D). Drafting from a paper depends on what the paper and code state, and the automatic drafter is untested against real papers. Domain-specific crate properties (pixel size, channels, scaling, tolerances) are schema.org `PropertyValue`s and a proposal, not yet an RO-Crate profile. "Publish back" writes a local record and a zip ready for deposit; upload to Zenodo or WorkflowHub is not automated yet. The Fiji runner needs testing on a machine with Fiji.

## License

[MIT](LICENSE)
