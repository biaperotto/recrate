# WorkflowHub pipelines, end to end

Scripts that run a real published pipeline through ReCRATE on a machine that has what it needs. None of them runs in
the test suite by default: `tests/test_workflowhub_pipelines.py` runs the same path only when docker and nextflow are
on PATH and `RECRATE_TEST_PIPELINES=1` is set.

| Script | Pipeline | Needs | What it checks |
| --- | --- | --- | --- |
| `demo_test.sh` | [nf-core/demo](https://workflowhub.eu/workflows/1055) (Nextflow) | nextflow, network, and docker **or** conda / mamba / micromamba (no docker needed) | The crate is read by the nf-core adapter, the published test profile runs with the first runtime installed (with micromamba, each tool's environment is built and cached in `~/.cache/recrate`), and the outputs are compared with the pipeline's nf-test snapshot (md5 per pinned file, tolerance source `test-stated`) |
| `ampliseq_test.sh` | [nf-core/ampliseq](https://workflowhub.eu/workflows/964) (Nextflow) | docker, nextflow, network | The same for a large pipeline (qiime2, DADA2): the published test profile runs as the authors run it (`-profile test,docker`); the outputs are compared with the test's declared files |

**Without docker at hand:** the GitHub Actions workflow `.github/workflows/workflowhub-pipeline.yml` runs any
WorkflowHub pipeline by id on a runner that has docker (start it from the Actions tab; the run crates come back as an
artifact).

Galaxy workflows need no script: with `GALAXY_URL` and `GALAXY_API_KEY` set, any Galaxy workflow from WorkflowHub
runs on that server through its API (`recrate.runners.galaxy_api`), with the IWC Planemo test's inputs as the
published data and its outputs as the expected result.
