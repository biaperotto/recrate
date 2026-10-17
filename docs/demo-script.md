# Demo script (3 minutes)

## Before the pitch

```bash
pip install -e ".[app,mcp]"
rm -rf runs && recrate demo-data examples/demo
streamlit run app/app.py          # keep it open in a browser tab
```

Record a backup video of the full run in case Fiji or the network fails live.

## Story

1. **Problem (30 s).** "I analysed my images in Fiji by clicking. Months later a reviewer asks exactly what I did, and I can't tell them or get the same numbers. And when I reuse someone else's method on my images, nobody checks whether my images even fit it."
2. **What exists (30 s).** Paper2Agent turns papers into agents. FlowProof and lightcone-cli rerun analyses and record provenance as RO-Crates. Fiji has MCP servers. We build on these and use the same standards.
3. **Demo (90 s).**
   * Step 1: open the method. "Here's a paper's method, read from its RO-Crate: what it expects, which parameters, which software."
   * Step 2: Reproduce. "Does it reproduce? Yes: 22 nuclei per field, same as reported, within tolerance."
   * Step 3: my images. "My images were taken at half the resolution. ReCRATE caught it: pixel size 1.3 vs 0.65 µm, so the debris filter of 50 pixels becomes 12.5. Without this, I'd lose almost half my nuclei and never know."
   * Step 4: results. "Overlays to check, I approve them, here's my methods paragraph, and the run crate."
   * Publish back. "And the paper now shows: reproduced once, reused once, by whom, with what."
4. **Next (30 s).** Domain profiles beyond imaging (genome build, single-cell normalisation); OME-Zarr and OMERO data read in place; authors publishing input crates with their papers.

## Chat version (optional, 30 s)

In Claude with the ReCRATE MCP server connected:

* "Reproduce the result from the crate in examples/demo/paper_crate."
* "Check my images in examples/demo/my_images and run the method on them."
* "Write the methods paragraph for that run."

## Using a real dataset instead of the synthetic one

1. Pick a public nuclei dataset with a reported or ground-truth count (for example a BBBC set).
2. Run the macro in Fiji by hand and confirm it gets close to the reported number.
3. Write a description like `examples/demo/paper_crate_description.json` with the real pixel size, the reported results CSV and the paper's DOI.
4. `recrate draft description.json examples/real/paper_crate`
5. Make "your images" by downsampling a few other images 2× and setting the new pixel size.
