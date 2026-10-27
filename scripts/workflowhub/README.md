# WorkflowHub pipelines, end to end

Scripts that run a real published pipeline through ReCRATE on a machine that has what it needs. None of them runs in
the test suite by default: `tests/test_workflowhub_pipelines.py` runs the same path only when docker and nextflow are
on PATH and `RECRATE_TEST_PIPELINES=1` is set.

| Script | Pipeline | Needs | What it checks |
| --- | --- | --- | --- |
| `ampliseq_test.sh` | [nf-core/ampliseq](https://workflowhub.eu/workflows/964) (Nextflow) | docker, nextflow, network | The crate is read by the nf-core adapter (`recrate.adapters`), the published test profile runs as the authors run it (`-profile test,docker`), and the run's outputs are compared with what the test declares (`file,sha1,size`, tolerance source `test-stated`) |

Galaxy workflows need no script: with `GALAXY_URL` and `GALAXY_API_KEY` set, any Galaxy workflow from WorkflowHub
runs on that server through its API (`recrate.runners.galaxy_api`), with the IWC Planemo test's inputs as the
published data and its outputs as the expected result.
