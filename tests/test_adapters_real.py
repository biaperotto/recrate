"""A real crate through the nf-core adapter: nf-core/ampliseq from WorkflowHub (#964), trimmed to what the adapter
reads (tests/data/ampliseq-crate), and what reading it taught the adapter."""

from __future__ import annotations

from pathlib import Path

from test_adapters import NF_CONFIG, nfcore_crate

from recrate.adapters import _software, detect_ecosystem, import_crate
from recrate.crate import load_crate
from recrate.runners import readiness
from recrate.runners.engines import NextflowRunner

AMPLISEQ = Path(__file__).resolve().parent / "data" / "ampliseq-crate"


def test_the_real_nfcore_ampliseq_crate_is_read(tmp_path, monkeypatch):
    """Authors from the workflow entity's ORCIDs, the test profile's values as defaults, its remote inputs resolved
    to the URLs it fetches, and a reproducible test run."""
    import shutil as _sh

    assert detect_ecosystem(AMPLISEQ) == "nfcore"
    desc = import_crate(AMPLISEQ, tmp_path / "crate")
    assert desc["name"] == "nf-core/ampliseq" and "Daniel Straub" in desc["authors"]
    assert desc["license"] == "https://spdx.org/licenses/MIT"
    spec = load_crate(tmp_path / "crate")
    assert spec.default_params()["primer_fwd"] == "GTGYCAGCMGCCGCGGTAA"  # the test profile's value
    assert spec.default_params()["max_len_asv"] == 255
    url = "https://raw.githubusercontent.com/nf-core/test-datasets/ampliseq/samplesheets/Samplesheet_v3.tsv"
    assert f"input: {url}" in spec.method_description  # resolved through params.pipelines_testdata_base_path
    assert spec.example_data and (spec.example_data / "TEST-PROFILE.md").exists()  # the published run is reproducible
    assert url in (spec.example_data / "TEST-PROFILE.md").read_text()
    inv = spec.invocation
    assert inv["engine"] == "nextflow" and inv["args"][4:6] == ["-profile", "test,{runtime}"] and inv["network"]
    assert ("cutadapt", "5.2") in spec.software
    # the recipe's input flags are dropped when the data folder has no such file: the profile supplies them
    args = NextflowRunner().recipe_command(inv, Path("main.nf"), spec.example_data, tmp_path / "out",
                                           spec.default_params(), tmp_path / "work")
    assert "--input" not in args and args[args.index("--primer_fwd") + 1] == "GTGYCAGCMGCCGCGGTAA"
    real_which = _sh.which
    monkeypatch.setattr(_sh, "which", lambda n: None if n in {"docker", "singularity", "apptainer", "conda", "mamba",
                                                                 "micromamba"} else real_which(n))
    why = readiness(spec.workflow_language, spec.workflow_path, invocation=inv)
    assert why and ("container runtime" in why if real_which("nextflow") else True)


def test_remote_test_inputs_make_a_documented_example_folder(tmp_path):
    c = nfcore_crate(tmp_path)
    (c / "nextflow.config").write_text(NF_CONFIG + "\nparams { pipelines_testdata_base_path = 'https://data.example.org/test/' }\n")
    (c / "conf" / "test.config").write_text("params {\n    input = params.pipelines_testdata_base_path + 'sheets/a.csv'\n"
                                            "    factor = 2\n}\n")
    import_crate(c, tmp_path / "crate")
    spec = load_crate(tmp_path / "crate")
    assert "input: https://data.example.org/test/sheets/a.csv" in spec.method_description
    assert spec.example_data and [p.name for p in spec.example_data.iterdir()] == ["TEST-PROFILE.md"]
    assert spec.default_params() == {"factor": 2}


def test_pinned_module_tools_come_before_a_full_environment_export(tmp_path):
    c = nfcore_crate(tmp_path)
    (c / "modules" / "local" / "big").mkdir(parents=True)
    (c / "modules" / "local" / "big" / "environment.yml").write_text(
        "dependencies:\n" + "".join(f"  - conda-forge::aaa{i:03d}=1.0\n" for i in range(250)))
    sw = _software(c, "nfcore")
    assert len(sw) == 200 and sw[0] == {"name": "aaa000", "version": "1.0"}  # sorted by name within the cap
    assert {"name": "gawk", "version": "5.3.0"} in sw  # the module's pinned tool survives the cap
