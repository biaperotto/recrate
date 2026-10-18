"""Every runner, end to end: the same small method (the sum of a column, times a parameter) written for each engine,
reproduced against its published result and reused with a changed parameter, through the full pipeline."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from recrate.agent import agentify
from recrate.draft import build_crate
from recrate.pipeline import run_on_data

QUARTO = """---
title: total
params:
  input_dir: ""
  output_dir: ""
  factor: 1
---

```{r}
d <- read.csv(file.path(params$input_dir, "values.csv"))
out <- data.frame(item = "all", total = sum(d$x) * as.numeric(params$factor))
write.csv(out, file.path(params$output_dir, "results.csv"), row.names = FALSE, quote = FALSE)
```
"""

NOTEBOOK = {"nbformat": 4, "nbformat_minor": 5, "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3",
                                                                            "language": "python"}},
            "cells": [{"cell_type": "code", "metadata": {"tags": ["parameters"]}, "execution_count": None, "outputs": [],
                       "source": "input_dir = ''\noutput_dir = ''\nfactor = 1"},
                      {"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [],
                       "source": "import csv, os\nxs = [float(r['x']) for r in csv.DictReader(open(os.path.join(input_dir,"
                                 " 'values.csv')))]\nopen(os.path.join(output_dir, 'results.csv'), 'w').write("
                                 "f'item,total\\nall,{sum(xs) * float(factor):g}\\n')"}]}

SNAKEFILE = """
rule all:
    output: config["output_dir"] + "/results.csv"
    run:
        import csv
        xs = [float(r["x"]) for r in csv.DictReader(open(config["input_dir"] + "/values.csv"))]
        open(output[0], "w").write("item,total\\nall,%g\\n" % (sum(xs) * float(config["factor"])))
"""

NEXTFLOW = r"""
params.input_dir = ''
params.output_dir = ''
params.factor = 1

process total {
    publishDir params.output_dir, mode: 'copy'
    input:
    path values
    output:
    path 'results.csv'
    script:
    '''
    awk -F, 'NR>1{s+=$1} END{print "item,total"; print "all," s*FACTOR}' values.csv > results.csv
    '''.replace('FACTOR', params.factor.toString())
}

workflow {
    total(file("${params.input_dir}/values.csv"))
}
"""

CWL = """cwlVersion: v1.2
class: CommandLineTool
requirements:
  ShellCommandRequirement: {}
inputs:
  input_dir: Directory
  factor: int
arguments:
  - shellQuote: false
    valueFrom: >-
      awk -F, 'NR>1{s+=$1} END{print "item,total"; print "all," s*$(inputs.factor)}'
      $(inputs.input_dir.path)/values.csv > results.csv
outputs:
  results:
    type: File
    outputBinding:
      glob: results.csv
"""

CASES = {  # file name, contents, language, the commands it needs
    "quarto": ("total.qmd", QUARTO, "Quarto", ["quarto", "Rscript"]),
    "notebook": ("total.ipynb", json.dumps(NOTEBOOK), "Jupyter notebook", ["papermill"]),
    "snakemake": ("Snakefile", SNAKEFILE, "Snakemake", ["snakemake"]),
    "nextflow": ("main.nf", NEXTFLOW, "Nextflow", ["nextflow"]),
    "cwl": ("total.cwl", CWL, "Common Workflow Language", ["cwltool"]),
}


@pytest.mark.parametrize("engine", list(CASES))
def test_engine_reproduces_and_reuses(engine, tmp_path):
    name, content, language, needs = CASES[engine]
    missing = [n for n in needs if not shutil.which(n)]
    if missing:
        pytest.skip(f"needs {', '.join(missing)}")
    (tmp_path / "wf").mkdir()
    (tmp_path / "wf" / name).write_text(content)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "values.csv").write_text("x\n1\n2\n3\n4\n")
    (tmp_path / "expected.csv").write_text("item,total\nall,20\n")
    desc = {"name": f"Total ({language})", "identifier": f"urn:recrate:test:{engine}", "methods": [{
        "name": "total", "workflow": {"file": f"wf/{name}", "language": language},
        "inputs": [{"id": "in", "name": "input_dir", "type": "File", "format": "text/csv"},
                   {"id": "f", "name": "factor", "type": "Integer", "default": 2, "role": "free"}],
        "outputs": [{"id": "out", "name": "results", "type": "File", "format": "text/csv",
                     "abs_tolerances": {"total": {"value": 0, "source": "person", "by": "test", "reason": "exact"}}}],
        "example_data": "data", "expected": "expected.csv"}]}
    build_crate(desc, tmp_path / "crate", base_dir=tmp_path)
    a = agentify(tmp_path / "agent", crate=tmp_path / "crate")
    m = a.methods[0]
    assert m.readiness == "ready", m.gaps
    assert m.status == "validated", m.detail  # 1+2+3+4 = 10, times 2: the published 20
    out = run_on_data(a.spec(), tmp_path / "data", a.runs_dir, agent="test", overrides={"factor": (3, "a new scale")})
    assert (out.run_dir / "outputs" / "results.csv").read_text().strip().splitlines()[-1] == "all,30"
    assert (out.run_dir / "outputs" / f"{engine}.log").exists()  # the engine's own log, kept with the run
