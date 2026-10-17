# Demo script (3 minutes)

## Before the pitch

```bash
pip install -e ".[app,mcp]"
recrate demo-data examples/demo
rm -rf examples/demo/paper_agent
recrate agentify --paper examples/demo/paper.md --crate examples/demo/paper_crate --out examples/demo/paper_agent
streamlit run app/app.py                                   # keep it open in a browser tab
recrate install-mcp examples/demo/paper_agent --client project   # or --client desktop
```

Record a backup video of the full run in case Fiji or the network fails live.

## Story

1. **Problem (30 s).** "Papers are static. To reuse a method I have to find the code, install it, guess the inputs, and hope my images are comparable. Nobody checks, and nothing I learn goes back to the paper."
2. **What exists (30 s).** Paper2Agent turns papers into agents. FlowProof and lightcone-cli record runs as RO-Crates. We build on both: the paper becomes an agent, RO-Crates are its input when the authors provide one, and every run goes back to the paper.
3. **Demo (90 s).**
   * The paper. "This paper is now an agent. Its two methods, a Fiji macro and a Python script, were each rerun on the paper's data and match what it reports. I can ask it: how is debris removed?"
   * Reproduce count_nuclei. "22 nuclei per field, same as reported, within tolerance."
   * My images. "My images were taken at half the resolution. ReCRATE caught it: pixel size 1.3 vs 0.65 µm, so the debris filter of 50 pixels becomes 12.5. Without this, I'd lose almost half my nuclei and never know."
   * Results. "Overlays to check, I approve them, here's my methods paragraph, and the run crate."
   * Publish back. "And the paper now shows: reproduced, reused on new data, by whom, with what."
4. **Next (30 s).** Drafting crates for papers that have none (Claude Code `/agentify`), domain profiles beyond imaging (genome build, single-cell normalisation), OME-Zarr and OMERO data read in place.

## Chat version (30 s)

In Claude with the paper agent connected (`recrate serve`):

* "What does this paper do, and what pixel size were its images?"
* "Reproduce the paper's results."
* "Run count_nuclei on my images in examples/demo/my_images and explain any adjustment."
* "Write the methods paragraph for that run."

## Paper-only version (optional)

```bash
recrate agentify --paper examples/demo/paper.md --out agents/paper_only
recrate ask agents/paper_only "what pixel size were the images?"
```

It answers from the paper straight away; `/agentify agents/paper_only` in Claude Code drafts the crate.

## Using a real dataset instead of the synthetic one

1. Pick a public nuclei dataset with a reported or ground-truth count (for example a BBBC set).
2. Run the macro in Fiji by hand and confirm it gets close to the reported number.
3. Write a description like `examples/demo/paper_crate_description.json` with the real pixel size, the reported results CSV and the paper's DOI.
4. `recrate draft description.json examples/real/paper_crate`, then
   `recrate agentify --paper paper.pdf --crate examples/real/paper_crate --out agents/real`
5. Make "your images" by downsampling a few other images 2× and setting the new pixel size.
