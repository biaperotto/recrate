# Input crate specification (proposal)

An input crate is a standard [Workflow RO-Crate 1.0](https://about.workflowhub.eu/Workflow-RO-Crate/). Most of what ReCRATE needs is already standard; a few domain-specific properties are added as schema.org `PropertyValue`s under `additionalProperty`, so the crate stays valid RO-Crate. Their names are a ReCRATE proposal and could become an RO-Crate profile.

## Standard terms used

| ReCRATE needs | Term | On |
| --- | --- | --- |
| Paper | `citation` → DOI | root Dataset |
| Stable id for linking runs | `identifier` | root Dataset |
| Licence, authors | `license`, `author` | root Dataset |
| The methods | `mainEntity` → the first workflow; every entity typed `ComputationalWorkflow` is one method (one agent tool) | root Dataset |
| Language | `programmingLanguage` → `ComputerLanguage` | workflow |
| Declared software | `softwareRequirements` → `SoftwareApplication` (`softwareVersion`) | workflow |
| Inputs and outputs | `input`, `output` → `FormalParameter` | workflow |
| Format, default, description | `encodingFormat`, `defaultValue`, `description`, `additionalType` | FormalParameter |
| Paper's own data | data entity with `exampleOfWork` → the File input parameter | Dataset/File |
| Paper's reported results | data entity with `exampleOfWork` → the output parameter | File |

## Proposed properties (`additionalProperty` → `PropertyValue`)

| `name` | On | `value` | Meaning |
| --- | --- | --- | --- |
| `domain profile` | root | e.g. `bioimaging` | Which profile checks the data |
| `draft` | root | `true` | Drafted from a paper; needs confirming |
| `pixel size` | File input | number, `unitText` e.g. `µm` | Expected calibration |
| `channels` | File input | integer | Expected number of channels |
| `dimensions` | File input | `2D` / `3D` | Expected dimensionality |
| `scaling` | value input | `inverse_pixel_area` / `inverse_pixel_size` | How the parameter changes with pixel size |
| `tolerance:<column>` | output | fraction, e.g. `0.05` | Allowed relative difference for that measurement (on the mean) |
| `tolerance_abs:<column>` | output | number, e.g. `0.005` | Allowed absolute difference, for every item |
| `tolerance_source:<column>` | output | `paper-rounding` / `paper-stated` / `method-noise` / `person` | Where the tolerance comes from; `description` holds the evidence (quote, person and reason, number of reruns). No source, no verdict |
| `followup` | workflow | JSON: `title`, `kind`, `changes`, `why`, `quote` | A check to run when the numbers differ (see `recrate/followup.py`) |
| `recrate invocation` | workflow | JSON: `engine`, `args`, `root`, `inputs`, `results`, `network`, `needs`, `collect` | How to call a workflow in its own community's idiom (nf-core, CWL, Snakemake), written by `recrate/adapters.py` when a published crate is imported. `args` take the placeholders `{workflow}`, `{input_dir}`, `{output_dir}`, `{work}`, `{runtime}` (the first installed of `needs`), `{param:<name>}` and `{input:<name>}` (the file `inputs` maps that name to, inside the data folder; the flag and value are dropped when the file is absent). `root`: the folder of the crate copied to the scratch folder before running (an engine needs the modules and tools around the workflow). `results`: `{"kind":"files"}` (ReCRATE lists what the run produced as `file,sha1,size`) or `{"kind":"table","path":...}` (that file is the results table). `network`: the engine pulls containers or data. `needs`: commands one of which must exist (container runtimes). `collect: new-files`: files the engine left in the scratch folder are outputs (Snakemake) |
| `recrate entry` | workflow | `module:function`, e.g. `tool:gc_content` | The workflow file is Python and the method is this function, as Paper2Agent wraps a package's API: ReCRATE calls it in the paper's own interpreter (sandboxed) with the method's parameters as keyword arguments, plus `input_dir`, `output_dir` and `channel` when its signature takes them, importing from the workflow's folder, the research object's `code/` and the crate root. Its return value becomes `results.csv` (a DataFrame, a list of dicts, a dict of scalars or of columns, a CSV path, or `None` when it wrote the file itself); a 2-tuple `(table, figures)` also saves each figure (matplotlib, PIL, bytes or a path) as `figures/<name>.png`, `figure-3` recreating the paper's Figure 3. In a crate description: `"workflow": {"file": ..., "language": "Python", "entry": "tool:gc_content"}` (`recrate/runners/function.py`) |

A better long-term option for imaging is to take expected calibration directly from the OME metadata of the paper's example images, so it comes from the OME standard rather than a new field.

## Several methods per paper

Each method is a `["File","SoftwareSourceCode","ComputationalWorkflow"]` with its own `input` / `output`
parameters (ids prefixed by the method, e.g. `#count-nuclei-in-images`), its own example data
(`data/<method>/`) and reported results (`expected/<method>.csv`). The first is the crate's `mainEntity`;
all of them become tools of the paper agent.

## Minimal example

See [`examples/demo/paper_crate/ro-crate-metadata.json`](../examples/demo/paper_crate/ro-crate-metadata.json) and the description it was built from, [`examples/demo/paper_crate_description.json`](../examples/demo/paper_crate_description.json). Build your own with:

```bash
recrate draft my_method.json my_crate/
```

## Run crates

Run crates conform to Process Run Crate 0.5, Workflow Run Crate 0.5 and Workflow RO-Crate 1.0. ReCRATE-specific parts:

| Entity | Type | Content |
| --- | --- | --- |
| `#run` | `CreateAction` | instrument = workflow, agent = person, object = inputs + parameter values, result = outputs |
| `#pv-<param>` | `PropertyValue` | value used; `description` explains any adjustment |
| `#input-check` | `AssessAction` | one `PropertyValue` per check (ok / warn / fail) |
| `#comparison` | `AssessAction` | verdict and per-measurement values (reproduce mode) |
| `#approval-<n>` | `AssessAction` | a person's visual check; result `accepted` / `rejected` |
| root | Dataset | `isBasedOn` → paper crate identifier; `recrate mode`, `recrate verdict` |
