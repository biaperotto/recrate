# ReCRATE

**Reuse published methods on your data, checked and recorded.**

Tools already exist that rerun papers and log provenance. ReCRATE adds two things:

1. **It checks that your data actually fits the method before reusing it.** A paper's method declares what its input data looked like (for images: pixel size, channels, 2D/3D). ReCRATE reads the same properties from your data, flags mismatches, and rescales parameters when that keeps the method meaning the same thing, recording why.
2. **Every reproduction is published back.** Each run is a Workflow Run RO-Crate that says which paper it is based on, so a paper builds a living reproducibility record: reproduced N times, on which data, by whom, with which adjustments.

The framework is general; checks come from **domain profiles**. The first profile, and the proof of concept, is **bioimaging with Fiji**, where calibration errors are common and invisible, and where analyses often have no notebooks for tools like Paper2Agent to start from.

Built at the *Reinvent the Wheel* hackathon (London, October 2026).

## How it works

```
                 published back to the paper's reproducibility record
        ┌──────────────────────────────────────────────────────────────┐
        ▼                                                              │
 Input RO-Crate ──► Reproduce on paper's data ──► Compare with ──► Run crate 1
 (supplied by the                                   reported result     ▲
  authors, or drafted                                                   │ links to
  from the paper)                                                       │
        │ declared inputs                                               │
        ▼                                                               │
 Your images ──► Check inputs vs spec ──► Run with recorded ──────► Run crate 2
 (OME metadata)  (domain profile)          adjustments
```

* **Input crate**: a standard [Workflow RO-Crate](https://about.workflowhub.eu/Workflow-RO-Crate/) with the workflow (e.g. a Fiji macro), declared inputs and parameters, the paper's example data and its reported results. It can come from WorkflowHub or Zenodo, or be drafted from a paper that has none (a chat agent reads the paper and fills a description; ReCRATE writes the crate; the user confirms it).
* **Reproduce**: run on the paper's own data and compare with the reported results, with per-measurement tolerances.
* **Check**: the domain profile compares your data with the declared inputs.
* **Reuse**: run on your data with recorded adjustments.
* **Run crates** follow Process Run Crate 0.5, Workflow Run Crate 0.5 and Workflow RO-Crate 1.0, so they are readable by other tools that use the same profiles.
* **The AI never computes the numbers.** The workflow runs deterministically; the agent helps choose, check and explain.

## Quick start

```bash
git clone <this repo> recrate && cd recrate
pip install -e ".[dev]"            # core + tests
pytest                             # 10 tests, about 2 seconds

recrate demo-data examples/demo    # (already included) paper crate + "your images"
recrate inspect   examples/demo/paper_crate
recrate reproduce examples/demo/paper_crate --who "Anna"
recrate check     examples/demo/paper_crate examples/demo/my_images
recrate run       examples/demo/paper_crate examples/demo/my_images --who "Anna"
recrate approve   runs/<run folder> --who "Anna"
recrate methods   runs/<run folder>
recrate publish   examples/demo/paper_crate runs/<run folder>
recrate record    examples/demo/paper_crate
```

What you should see with the demo: the paper's images were taken at 0.65 µm per pixel, yours at 1.3 µm. The paper removes debris with `min_size_px = 50`. Applied unchanged to your images, that silently drops small nuclei (about 13 per field instead of 22). ReCRATE flags the pixel size, rescales the parameter to 12.5 px, and gets 22 nuclei with a mean area within 2 % of the paper's, all recorded in the run crate.

![Left: parameters rescaled by ReCRATE, all nuclei found. Right: the paper's parameter applied unchanged, small nuclei silently dropped.](docs/img/adjusted_vs_unadjusted.png)

*Left: `min_size_px` rescaled to 12.5 for 1.3 µm pixels. Right: the paper's 50 applied unchanged.*

## With Fiji

```bash
pip install -e ".[fiji]"
export FIJI_PATH=/path/to/Fiji.app
recrate reproduce examples/demo/paper_crate --runner fiji
```

Without Fiji, `--runner auto` falls back to a Python reference implementation of the demo macro (scikit-image), so everything can be developed and tested anywhere. The demo's "reported results" were produced with that reference; for a real demo, regenerate them with Fiji or use a public dataset with a published number (see [docs/demo-script.md](docs/demo-script.md)).

## With Claude (MCP)

```bash
pip install -e ".[mcp]"
claude mcp add recrate -- recrate-mcp      # Claude Code
```

or add to Claude Desktop's `claude_desktop_config.json`:

```json
{"mcpServers": {"recrate": {"command": "recrate-mcp",
                            "env": {"RECRATE_RUNS_DIR": "/absolute/path/to/runs"}}}}
```

Tools: `inspect_crate`, `check_inputs`, `reproduce`, `run_on_my_data`, `approve_run`, `methods_text`, `publish_run`, `reproducibility_record`, `draft_crate`.

## Web app

```bash
pip install -e ".[app]"
streamlit run app/app.py
```

Four steps: open the method, reproduce, check my images, results and record (overlays to approve, methods paragraph, run crate download, publish back).

## Repository layout

```
src/recrate/
  crate.py            read an input crate into a MethodSpec
  draft.py            build an input crate from a JSON description (paper-only input)
  profiles/           domain profiles; bioimaging.py reads OME / ImageJ calibration
  runners/            fiji.py (PyImageJ, headless) and python_ref.py (demo reference)
  workflows/          count_nuclei.ijm, the demo Fiji macro
  compare.py          per-measurement tolerances against reported results
  runcrate.py         write Workflow Run Crates, approvals, HTML preview
  record.py           the paper's reproducibility record; publish back
  methods.py          methods paragraph from a run crate
  pipeline.py         reproduce / check / run_on_data
  cli.py, mcp_server.py
app/app.py            Streamlit interface
examples/demo/        demo paper crate and "your images"
docs/                 architecture, crate spec, domain profiles, demo script
tests/
```

## Related work

ReCRATE builds on, and should interoperate with:

* [Paper2Agent](https://github.com/jmiao24/Paper2Agent) (Miao et al., *Nature* 2026): papers and their code as MCP agents. ReCRATE accepts a paper-only input the same way, and adds declared input specs, comparability checks and standard run records.
* [Workflow Run RO-Crate](https://doi.org/10.1371/journal.pone.0309210) and [WorkflowHub](https://about.workflowhub.eu/): the standards and registry we use.
* [FlowProof](https://glama.ai/mcp/servers/ovw3020c8j), [lightcone-cli](https://pypi.org/project/lightcone-cli/), [ECAA-workflow](https://github.com/SuLab/ECAA-workflow): agents with RO-Crate provenance for pipelines; we use the same run-crate profiles.
* [fiji-mcp-server](https://pypi.org/project/fiji-mcp-server/) and [fiji_mcp](https://github.com/NicoKiaru/fiji_mcp): plain-language control of Fiji.

See [docs/architecture.md](docs/architecture.md) for how ReCRATE differs.

## Status and limits

Hackathon proof of concept. One domain profile (bioimaging), one demo workflow, 2D images. Domain-specific crate properties (pixel size, channels, scaling, tolerances) use schema.org `PropertyValue` and are a proposal, not yet an RO-Crate profile. "Publish back" writes a local record and a zip ready for deposit; uploading to Zenodo or WorkflowHub is not automated yet.

## License

[MIT](LICENSE)
