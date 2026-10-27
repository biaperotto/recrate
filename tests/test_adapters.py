"""Published crates in their own community's conventions (nf-core, CWL, Snakemake): read into a ReCRATE crate with an
invocation recipe, reported honestly when they can't run here, and run as their authors run them when they can."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from recrate.adapters import detect_ecosystem, import_crate
from recrate.agent import agentify
from recrate.crate import load_crate
from recrate.pipeline import run_on_data
from recrate.runners import readiness

ROOT = {"@id": "ro-crate-metadata.json", "@type": "CreativeWork", "about": {"@id": "./"},
        "conformsTo": {"@id": "https://w3id.org/ro/crate/1.1"}}


def _crate(folder: Path, main: str, language: str, extra: list[dict] | None = None) -> Path:
    """A WorkflowHub-style crate: the workflow is the main entity, nothing else says how to run it."""
    folder.mkdir(parents=True, exist_ok=True)
    graph = [ROOT, {"@id": "./", "@type": "Dataset", "name": f"Test {language} workflow", "license": "MIT",
                    "mainEntity": {"@id": main}, "hasPart": [{"@id": main}], "datePublished": "2025-01-02"},
             {"@id": main, "@type": ["File", "SoftwareSourceCode", "ComputationalWorkflow"], "name": Path(main).stem,
              "programmingLanguage": {"@id": "#lang"}},
             {"@id": "#lang", "@type": "ComputerLanguage", "name": language}, *(extra or [])]
    (folder / "ro-crate-metadata.json").write_text(json.dumps({"@context": "https://w3id.org/ro/crate/1.1/context",
                                                                "@graph": graph}))
    return folder


# ---- nf-core ------------------------------------------------------------------------------------------

NF_MAIN = """
params.input = null
params.outdir = null
params.factor = 1

process TOTAL {
    publishDir params.outdir, mode: 'copy'
    input:
    path sheet
    output:
    path 'total.txt'
    script:
    "awk -F, 'NR>1{s+=\\$2} END{print s*${params.factor}}' $sheet > total.txt"
}

workflow {
    TOTAL(file(params.input))
}
"""
NF_CONFIG = """
profiles {
    test { params.input = 'assets/samplesheet.csv' }
    docker { docker.enabled = true }
    singularity { singularity.enabled = true }
    conda { conda.enabled = true }
}
"""
NF_SCHEMA = {"$schema": "https://json-schema.org/draft/2020-12/schema", "$defs": {
    "input_output_options": {"properties": {
        "input": {"type": "string", "format": "file-path", "description": "Path to the sample sheet"},
        "outdir": {"type": "string", "format": "directory-path"},
        "factor": {"type": "integer", "default": 1, "description": "Multiply the total by this"}}},
    "generic_options": {"properties": {"help": {"type": "boolean", "hidden": True}}}}}


def nfcore_crate(tmp_path: Path) -> Path:
    c = _crate(tmp_path / "published", "main.nf", "Nextflow")
    (c / "main.nf").write_text(NF_MAIN)
    (c / "nextflow.config").write_text(NF_CONFIG)
    (c / "nextflow_schema.json").write_text(json.dumps(NF_SCHEMA))
    (c / "conf").mkdir()
    (c / "conf" / "test.config").write_text("params {\n    input = 'assets/samplesheet.csv'\n    factor = 2\n}\n")
    (c / "assets").mkdir()
    (c / "assets" / "samplesheet.csv").write_text("sample,x\na,1\nb,2\nc,3\n")
    (c / "modules" / "nf-core" / "awk").mkdir(parents=True)  # nf-core modules pin their tools in environment.yml
    (c / "modules" / "nf-core" / "awk" / "environment.yml").write_text(
        "channels:\n  - conda-forge\ndependencies:\n  - conda-forge::gawk=5.3.0\n")
    return c


def test_nfcore_crate_is_read_with_its_schema_and_test_profile(tmp_path):
    c = nfcore_crate(tmp_path)
    assert detect_ecosystem(c) == "nfcore"
    import_crate(c, tmp_path / "crate")
    spec = load_crate(tmp_path / "crate")
    assert spec.workflow_language == "Nextflow" and spec.workflow_path.exists()
    assert [p.name for p in spec.inputs] == ["input", "factor"]
    assert spec.default_params() == {"factor": 2}  # the published test's value, not the schema default
    assert spec.example_data and (spec.example_data / "samplesheet.csv").exists()
    inv = spec.invocation
    assert inv["engine"] == "nextflow" and inv["network"] and "docker" in inv["needs"]
    assert "-profile" in inv["args"] and inv["inputs"] == {"input": "samplesheet.csv"}
    assert (tmp_path / "crate" / "workflow" / "nextflow.config").exists()  # the whole tree travels with the method
    assert ("gawk", "5.3.0") in spec.software  # from the module's environment.yml
    assert (c / "ro-crate-metadata.json").exists() and not (c / "workflow").exists()  # the original is untouched


def test_nfcore_readiness_names_the_missing_runtime(tmp_path, monkeypatch):
    c = nfcore_crate(tmp_path)
    a = agentify(tmp_path / "agent", crate=c, online=False, validate="none")
    assert a.card()["crate_source"] == "derived from a Nextflow Workflow RO-Crate"
    m = a.card()["methods"][0]
    spec = a.spec()
    real_which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda n: None if n in {"docker", "singularity", "apptainer", "conda", "mamba",
                                                                 "micromamba"} else real_which(n))
    why = readiness(spec.workflow_language, spec.workflow_path, invocation=spec.invocation)
    if real_which("nextflow"):
        assert why and "container runtime" in why and "micromamba" in why
    else:
        assert why
    assert m["readiness"] in {"blocked", "ready-with-gaps", "reusable"}  # reusable: a runtime is here, no test outputs


@pytest.mark.skipif(not shutil.which("nextflow"), reason="needs nextflow")
def test_nfcore_pipeline_runs_as_its_authors_run_it(tmp_path, monkeypatch):
    """With a runtime "installed" (pretended: the test pipeline declares no container, so the docker profile is
    inert), the pipeline runs with -profile test,docker and the run lists what it produced."""
    import shutil as _sh

    c = nfcore_crate(tmp_path)
    a = agentify(tmp_path / "agent", crate=c, online=False, validate="none")
    real_which = _sh.which
    monkeypatch.setattr(_sh, "which", lambda n: "/usr/bin/docker" if n == "docker" else real_which(n))
    spec = a.spec()
    assert readiness(spec.workflow_language, spec.workflow_path, invocation=spec.invocation) is None
    out = run_on_data(spec, spec.example_data, a.runs_dir, agent="test")
    rows = (out.run_dir / "outputs" / "results.csv").read_text().splitlines()
    assert rows[0] == "file,sha1,md5,size" and any(r.startswith("total.txt,") for r in rows)
    assert (out.run_dir / "outputs" / "total.txt").read_text().strip() == "12"  # (1+2+3) * factor 2
    meta = json.loads((out.run_dir / "ro-crate-metadata.json").read_text())
    action = next(e for e in meta["@graph"] if e["@id"] == "#run")
    assert "network allowed" in action["description"] or "unsandboxed" in action["description"] \
        or "no sandbox" in action["description"]


def test_recipe_drops_empty_parameters_and_writes_booleans_for_the_engine(tmp_path):
    """A schema parameter without a default (nf-core's `email`) must not reach the pipeline as `--email None`, which
    its schema validation rejects, and a boolean goes as `true`/`false`."""
    from recrate.runners.engines import NextflowRunner

    recipe = {"engine": "nextflow", "args": ["nextflow", "run", "{workflow}", "--email", "{param:email}",
                                             "--skip_trim", "{param:skip_trim}", "--factor", "{param:factor}"],
              "needs": ["docker"], "inputs": {}}
    (tmp_path / "work").mkdir()
    args = NextflowRunner().recipe_command(recipe, Path("main.nf"), tmp_path, tmp_path / "out",
                                           {"email": None, "skip_trim": False, "factor": 2}, tmp_path / "work")
    assert "--email" not in args and args[args.index("--skip_trim") + 1] == "false" and args[-1] == "2"


def test_plain_nextflow_without_contract_or_recipe_is_blocked(tmp_path):
    c = _crate(tmp_path / "plain", "main.nf", "Nextflow")
    (c / "main.nf").write_text("params.samples = ''\nworkflow { println params.samples }\n")
    assert detect_ecosystem(c) is None
    why = readiness("Nextflow", c / "main.nf")
    if shutil.which("nextflow"):
        assert why and "No adapter" in why and "--input_dir" in why
    else:
        assert why


# ---- CWL ----------------------------------------------------------------------------------------------

CWL_TOOL = """cwlVersion: v1.2
class: CommandLineTool
hints:
  SoftwareRequirement:
    packages:
      gawk:
        version: ["5.3.0"]
baseCommand: [sh, -c]
inputs:
  values: File
  factor:
    type: int
    default: 1
arguments:
  - valueFrom: >-
      awk -F, 'NR>1{s+=$1} END{print s*$(inputs.factor)}' $(inputs.values.path) > total.txt
outputs:
  total:
    type: File
    outputBinding:
      glob: total.txt
"""


def cwl_crate(tmp_path: Path) -> Path:
    params = [{"@id": "#in-values", "@type": "FormalParameter", "name": "values", "additionalType": "File",
               "description": "A CSV with one column x"},
              {"@id": "#in-factor", "@type": "FormalParameter", "name": "factor", "additionalType": "int",
               "defaultValue": "1"}]
    c = _crate(tmp_path / "published", "total.cwl", "Common Workflow Language", params)
    meta = json.loads((c / "ro-crate-metadata.json").read_text())
    wf = next(e for e in meta["@graph"] if e["@id"] == "total.cwl")
    wf["input"] = [{"@id": "#in-values"}, {"@id": "#in-factor"}]
    (c / "ro-crate-metadata.json").write_text(json.dumps(meta))
    (c / "total.cwl").write_text(CWL_TOOL)
    (c / "tests").mkdir()
    (c / "tests" / "data").mkdir()
    (c / "tests" / "data" / "values.csv").write_text("x\n1\n2\n3\n4\n")
    (c / "tests" / "job.yml").write_text("values:\n  class: File\n  path: data/values.csv\nfactor: 3\n")
    sha = hashlib.sha1(b"30\n").hexdigest()
    (c / "tests" / "tests.yml").write_text(
        f"- job: job.yml\n  tool: ../total.cwl\n  doc: total of the values\n  output:\n    total:\n"
        f"      basename: total.txt\n      class: File\n      checksum: sha1${sha}\n      size: 3\n")
    return c


def test_cwl_crate_is_read_with_its_parameters_and_test(tmp_path):
    c = cwl_crate(tmp_path)
    assert detect_ecosystem(c) == "cwl"
    import_crate(c, tmp_path / "crate")
    spec = load_crate(tmp_path / "crate")
    assert [p.name for p in spec.inputs] == ["values", "factor"]
    assert spec.default_params() == {"factor": 3}  # the test job's value
    assert spec.invocation["engine"] == "cwl" and spec.invocation["inputs"] == {"values": "values.csv"}
    assert not spec.invocation["network"] and spec.invocation["needs"] == []
    assert spec.output_kind == "files"
    assert ("gawk", "5.3.0") in spec.software  # the SoftwareRequirement hint
    rows = spec.expected_results.read_text().splitlines()
    assert rows[0] == "file,sha1,size" and rows[1].startswith("total.txt,") and rows[1].endswith(",3")  # cwltest: sha1
    tol = spec.tolerance_specs()
    assert tol and tol[0].source == "test-stated" and "tests.yml" in tol[0].evidence


@pytest.mark.skipif(not shutil.which("cwltool"), reason="needs cwltool")
def test_cwl_workflow_runs_from_its_test_job(tmp_path):
    c = cwl_crate(tmp_path)
    a = agentify(tmp_path / "agent", crate=c, online=False, validate="none")
    assert a.card()["crate_source"] == "derived from a CWL Workflow RO-Crate"
    spec = a.spec()
    assert readiness(spec.workflow_language, spec.workflow_path, invocation=spec.invocation) is None
    out = run_on_data(spec, spec.example_data, a.runs_dir, agent="test")
    assert (out.run_dir / "outputs" / "total.txt").read_text() == "30\n"
    rows = (out.run_dir / "outputs" / "results.csv").read_text().splitlines()
    assert rows[0] == "file,sha1,md5,size"
    produced = next(r for r in rows if r.startswith("total.txt,"))
    sha, md5 = hashlib.sha1(b"30\n").hexdigest(), hashlib.md5(b"30\n").hexdigest()
    assert produced == f"total.txt,{sha},{md5},3"


# ---- Snakemake ----------------------------------------------------------------------------------------

SNAKEFILE = """
rule all:
    input: "results/total.txt"

rule total:
    input: config["values"]
    output: "results/total.txt"
    params: factor=config["factor"]
    shell: "awk -F, 'NR>1{{s+=$1}} END{{print s*{params.factor}}}' {input} > {output}"
"""


def snakemake_crate(tmp_path: Path) -> Path:
    c = _crate(tmp_path / "published", "workflow/Snakefile", "Snakemake")
    (c / "workflow").mkdir()
    (c / "workflow" / "Snakefile").write_text(SNAKEFILE)
    (c / "config").mkdir()
    (c / "config" / "config.yaml").write_text("values: data/values.csv\nfactor: 1\n")
    (c / ".test" / "config").mkdir(parents=True)
    (c / ".test" / "config" / "config.yaml").write_text("values: data/values.csv\nfactor: 5\n")
    (c / ".test" / "data").mkdir()
    (c / ".test" / "data" / "values.csv").write_text("x\n1\n2\n")
    (c / "workflow" / "envs").mkdir()
    (c / "workflow" / "envs" / "awk.yaml").write_text("channels: [conda-forge]\ndependencies:\n  - gawk=5.3.0\n")
    return c


def test_snakemake_crate_is_read_with_its_config_and_test(tmp_path):
    c = snakemake_crate(tmp_path)
    assert detect_ecosystem(c) == "snakemake"
    import_crate(c, tmp_path / "crate")
    spec = load_crate(tmp_path / "crate")
    assert [p.name for p in spec.inputs] == ["values", "factor"]
    assert spec.default_params() == {"factor": 5}
    assert spec.example_data and (spec.example_data / "values.csv").exists()
    inv = spec.invocation
    assert inv["engine"] == "snakemake" and inv["collect"] == "new-files" and not inv["network"]
    assert ("gawk", "5.3.0") in spec.software  # from workflow/envs


def test_workflowhub_titles_give_clean_names(tmp_path):
    """WorkflowHub names a crate "Research Object Crate for <title>"; the title's first clause names the method."""
    c = snakemake_crate(tmp_path)
    meta = json.loads((c / "ro-crate-metadata.json").read_text())
    title = "imputation: a genotype QC and 1000 Genomes imputation Snakemake workflow"
    for e in meta["@graph"]:
        if e["@id"] == "./":
            e["name"] = f"Research Object Crate for {title}"
        if e["@id"] == "workflow/Snakefile":
            e["name"] = title
    (c / "ro-crate-metadata.json").write_text(json.dumps(meta))
    import_crate(c, tmp_path / "crate")
    spec = load_crate(tmp_path / "crate")
    assert spec.name == title and spec.method_name == "imputation"


@pytest.mark.skipif(not shutil.which("snakemake"), reason="needs snakemake")
def test_snakemake_workflow_runs_from_its_test_config(tmp_path):
    c = snakemake_crate(tmp_path)
    a = agentify(tmp_path / "agent", crate=c, online=False, validate="none")
    assert a.card()["crate_source"] == "derived from a Snakemake Workflow RO-Crate"
    spec = a.spec()
    out = run_on_data(spec, spec.example_data, a.runs_dir, agent="test", overrides={"factor": (10, "a test")})
    assert (out.run_dir / "outputs" / "results" / "total.txt").read_text().strip() == "30"
    rows = (out.run_dir / "outputs" / "results.csv").read_text().splitlines()
    assert rows[0] == "file,sha1,md5,size" and any(r.startswith("results/total.txt,") for r in rows)


def test_mcp_tool_names_the_files_a_recipe_expects(tmp_path):
    from recrate.mcp_server import typed_method_tool

    import_crate(cwl_crate(tmp_path), tmp_path / "crate")
    spec = load_crate(tmp_path / "crate")
    fn = typed_method_tool(spec, spec.method_id, lambda **kw: kw)
    import inspect

    desc = str(inspect.signature(fn).parameters["data_path"].annotation)
    assert "values.csv (values)" in desc


def test_nfcore_snapshot_gives_the_expected_outputs_by_md5(tmp_path):
    """The pipeline's nf-test snapshot pins its test outputs: md5 for stable files, names for the others. They become
    the method's expected results (tolerance source test-stated), so a run without docker is still compared."""
    c = nfcore_crate(tmp_path)
    (c / "tests").mkdir()
    (c / "tests" / "default.nf.test.snap").write_text(json.dumps({"-profile test": {"content": [
        {"TOTAL": {"awk": "5.3.0"}, "Workflow": {"x": "v1"}},
        ["total.txt", "logs", "logs/run.log"],
        ["total.txt:md5,2c36f49a5ccef3eacc3cbe5e3a5ed2e8"]]}}))
    import_crate(c, tmp_path / "crate")
    spec = load_crate(tmp_path / "crate")
    assert spec.expected_results and spec.expected_results.exists()
    rows = spec.expected_results.read_text().splitlines()
    assert rows[0] == "file,md5" and "total.txt,2c36f49a5ccef3eacc3cbe5e3a5ed2e8" in rows and "logs/run.log," in rows
    assert "logs," not in rows  # a folder is not a file
    tol = spec.tolerance_specs()
    assert tol and tol[0].column == "md5" and tol[0].source == "test-stated" and "default.nf.test.snap" in tol[0].evidence
    meta = (tmp_path / "crate" / "ro-crate-metadata.json").read_text()
    assert "nf-test snapshot default.nf.test.snap (1 files by md5, 1 by name)" in meta  # said on the method


@pytest.mark.skipif(not shutil.which("nextflow"), reason="needs nextflow")
def test_nfcore_pipeline_runs_with_micromamba_when_there_is_no_docker(tmp_path, monkeypatch):
    """No container runtime, but micromamba: the pipeline's conda profile runs with conda.useMicromamba and a cached
    environment folder (the test pipeline declares no tool, so no environment is built), and the run records the
    runtime and compares the outputs with the snapshot's md5."""
    import shutil as _sh

    c = nfcore_crate(tmp_path)
    (c / "tests").mkdir()
    (c / "tests" / "default.nf.test.snap").write_text(json.dumps({"-profile test": {"content": [
        ["total.txt"], ["total.txt:md5," + __import__("hashlib").md5(b"12\n").hexdigest()]]}}))
    a = agentify(tmp_path / "agent", crate=c, online=False, validate="none")
    real_which = _sh.which
    monkeypatch.setattr(_sh, "which", lambda n: None if n in {"docker", "singularity", "apptainer", "conda", "mamba"}
                        else ("/usr/local/bin/micromamba" if n == "micromamba" else real_which(n)))
    monkeypatch.setenv("RECRATE_CONDA_CACHE", str(tmp_path / "conda-cache"))
    spec = a.spec()
    assert spec.invocation["needs"][-1] == "micromamba"
    assert readiness(spec.workflow_language, spec.workflow_path, invocation=spec.invocation) is None
    from recrate.pipeline import reproduce

    out = reproduce(spec, a.runs_dir, agent="test")
    assert out.ladder["runs"]["status"] == "passed", out.ladder
    assert out.ladder["numbers"]["status"] == "passed" and "test-stated" in out.ladder["numbers"]["reason"]
    log = (out.run_dir / "outputs" / "nextflow.log").read_text() if (out.run_dir / "outputs" / "nextflow.log").exists() else ""
    meta = json.loads((out.run_dir / "ro-crate-metadata.json").read_text())
    names = {e.get("name") for e in meta["@graph"] if "SoftwareApplication" in str(e.get("@type"))}
    assert "micromamba" in names, (names, log[-500:])
    shim = tmp_path / "bin" / "conda"  # the conda stand-in nf-core's start-up check needs, next to the cache
    assert shim.exists() and "exec micromamba" in shim.read_text()
    import subprocess as _sp

    assert _sp.run([str(shim), "config", "--show", "channels"], capture_output=True, text=True).stdout.startswith("channels:")
