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

## Running a pipeline where it can run (e.g. WorkflowHub #2343 on NCI)

**From this computer, on another machine.** Set the machine once (key-based SSH login) and ReCRATE runs the
recipe there: the workflow's folder is copied over with rsync and kept (fetched data and built environments are
reused), the recipe's prepare commands (`dvc pull`, `prep.sh`), each step's preflight and the steps run there, and
the outputs come back into the run crate here. Readiness reflects that machine's runtimes.

```bash
export RECRATE_REMOTE=user@gadi.nci.org.au RECRATE_REMOTE_DIR=/scratch/a56/user/recrate
export RECRATE_SNAKEMAKE_ARGS="--profile /path/to/profiles/global/nci"                   # optional: a cluster profile
recrate agentify --crate https://workflowhub.eu/workflows/2343 --out agents/imputation    # readiness: ready (apptainer is there)
recrate reproduce agents/imputation --who "Your name"                                     # dvc pull and prep.sh there, then QC, then imputation
```

The crate carries the test inputs as DVC pointers (`test/data/*.dvc`; the files live in the authors' DVC remote on
Gadi), so the first run's `dvc pull` needs an account with access to that remote. If you have the test files yourself,
put them in a folder and give it as the data (`recrate run agents/imputation <folder>`, or the app's Reuse): they are
uploaded and used in place of the pointers.

**A container runtime on a Mac: a small Linux VM with apptainer (Lima).** macOS cannot run singularity or apptainer
itself; a Snakemake workflow whose rules say `container:` needs a Linux machine. [Lima](https://lima-vm.io) makes one on
the Mac in a few minutes (no Homebrew needed: the release tarball unpacks into `~/.local`), with apptainer already in
its `apptainer` template; ReCRATE then treats the VM as the other machine (`RECRATE_REMOTE=lima:apptainer`), reaching
it through Lima's own SSH configuration.

```bash
# 1. Lima (about 100 MB), from its release tarball                      https://github.com/lima-vm/lima/releases
curl -fsSL "https://github.com/lima-vm/lima/releases/latest/download/lima-$(curl -fsSL https://api.github.com/repos/lima-vm/lima/releases/latest | python3 -c 'import json,sys; print(json.load(sys.stdin)["tag_name"][1:])')-Darwin-arm64.tar.gz" | tar -xz -C ~/.local
# 2. The VM with apptainer (an Ubuntu image, about 600 MB; 4 CPUs and 4 GB of RAM while it runs, disk grows as used)
limactl start --name apptainer --cpus 4 --memory 8 --disk 100 template://apptainer
# 3. What the workflow needs inside it: snakemake, dvc (for its DVC-tracked data), git
limactl shell apptainer -- bash -lc 'sudo apt-get install -y python3-pip git && pip install --user snakemake "dvc[ssh]"'
# 4. ReCRATE runs the workflow in the VM; readiness reflects the VM
export RECRATE_REMOTE=lima:apptainer RECRATE_REMOTE_DIR='~/recrate-runs'
recrate agentify --crate https://workflowhub.eu/workflows/2343 --out agents/imputation
recrate reproduce agents/imputation --who "Your name"
```

What that costs for #2343: `dvc pull` inside the VM needs your NCI account (the test inputs are on Gadi's DVC remote;
put your Gadi SSH key in the VM, or copy the four test files into the VM's `~/recrate-runs/imputation/workflow/test/data/`),
and `prep.sh` fetches and unpacks about 40 GB of public reference data into the VM's disk on the first run. The
imputation stage asks for up to 40 GB of RAM for one rule (`harmonize_hg38`) in its workflow profile; on a 17 GB
Mac give the VM what you can (`--memory 12`) and expect that stage to be slow or to need the `set-resources` lowered.

**Or entirely on that machine**, with the outputs served from there:

Some published pipelines can only run on the machine their authors run them on: the [imputation
workflow](https://workflowhub.eu/workflows/2343) (Snakemake, two stages) needs singularity or apptainer, about 40 GB of
public reference data, and its test inputs and golden results are DVC pointers to a remote on NCI's Gadi
(`ssh://gadi-dm.nci.org.au/g/data/a56/...`). ReCRATE says so (`recrate inspect`: readiness *blocked* here, the method's
description names the DVC inputs and the remote) and runs it as the authors do once it is where that exists:

```bash
# on the machine with access (a Gadi login node or an interactive job; apptainer and snakemake on PATH)
python3 -m venv recrate-env && . recrate-env/bin/activate && pip install "recrate[mcp] @ git+https://github.com/biaperotto/recrate"
recrate agentify --crate https://workflowhub.eu/workflows/2343 --out imputation   # the crate, read through the adapter
(cd imputation/crate/workflow && dvc pull && ./prep.sh)                            # test inputs, references (once)
recrate reproduce imputation --who "Your name"   # prep.sh --what check, then Snakefile_qc, then Snakefile_imputation,
                                                # each with --use-singularity and the bundled workflow profile
recrate inspect imputation                      # the ladder; the run crate is in imputation/runs/
```

The run is recorded there. To use the agent from your own computer, either copy `imputation/runs/` (and
`agent.json`) back, or serve it from the remote machine and reach it through an SSH tunnel:

```bash
# remote
recrate serve imputation --http --port 8765 --token "$(openssl rand -hex 16)"   # prints the URL; keep the token
# local
ssh -N -L 8765:127.0.0.1:8765 user@gadi.nci.org.au
recrate install-mcp imputation --client desktop --transport http --url http://127.0.0.1:8765/mcp --token <the token>
```

Without access to the DVC remote the published test cannot be reproduced by anyone (its inputs come from a private
repository, `git@github.com:swarbricklab/genotyping.git`); the method is then *reusable* on your own VCF, psam and id
map, with the public references fetched by `dvc update` as the workflow's README describes.
