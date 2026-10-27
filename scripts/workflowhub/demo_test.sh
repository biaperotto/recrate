#!/usr/bin/env bash
# nf-core/demo (WorkflowHub #1055) end to end with ReCRATE, on a computer without docker: fetch its crate, read it
# through the nf-core adapter (parameters, the test profile, and the nf-test snapshot as the expected outputs), run
# its published test profile with Nextflow and the first runtime installed (docker, singularity, apptainer; else
# conda, mamba or micromamba, which install each tool into an environment), and compare the outputs with the
# snapshot's md5s.
#
# Needs: nextflow and recrate on PATH, one of the runtimes above, and the network (the test profile fetches its
# samplesheet and reads; the recipe declares that need). With micromamba the first run builds four small
# environments (fastqc, seqtk, multiqc, cowpy: about 1 GB in ~/.cache/recrate) and takes 5 to 15 minutes.
#
#   scripts/workflowhub/demo_test.sh [agents/nfcore-demo]
set -euo pipefail

WID="${DEMO_WORKFLOWHUB_ID:-1055}"
OUT="${1:-agents/nfcore-demo}"
WHO="${WHO:-$(whoami)}"

for c in nextflow recrate; do
    command -v "$c" >/dev/null || { echo "needs $c on PATH" >&2; exit 2; }
done
RUNTIME=""
for r in docker singularity apptainer conda mamba micromamba; do
    command -v "$r" >/dev/null && { RUNTIME="$r"; break; }
done
[ -n "$RUNTIME" ] || { echo "needs docker, singularity, apptainer, conda, mamba or micromamba" >&2; exit 2; }
echo "== WorkflowHub #$WID -> $OUT (runtime: $RUNTIME)"

recrate agentify --crate "https://workflowhub.eu/workflows/$WID" --out "$OUT"
echo "== Before the run"
recrate inspect "$OUT"
echo "== Reproduce: nextflow run ... -profile test,<runtime>, as the authors run their test"
recrate reproduce "$OUT" --who "$WHO" || true      # exit 2 when the numbers differ; the run crate is kept either way
echo "== After the run: the verdict ladder"
recrate inspect "$OUT"
ls "$OUT/runs"
