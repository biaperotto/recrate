#!/usr/bin/env bash
# nf-core/ampliseq (WorkflowHub #964) end to end with ReCRATE: fetch its crate, read it through the nf-core adapter,
# run its published test profile with Nextflow and docker, and compare with what the test says it produces.
#
# Needs: docker (running), nextflow and recrate on PATH, and the network (the test profile pulls containers and its
# test data; the crate's invocation recipe declares that need, so the run is allowed it). Takes 10 to 30 minutes.
#
#   scripts/workflowhub/ampliseq_test.sh [agents/ampliseq]
set -euo pipefail

WID="${AMPLISEQ_WORKFLOWHUB_ID:-964}"
OUT="${1:-agents/ampliseq}"
WHO="${WHO:-$(whoami)}"

for c in docker nextflow recrate curl python3; do
    command -v "$c" >/dev/null || { echo "needs $c on PATH" >&2; exit 2; }
done
docker info >/dev/null 2>&1 || { echo "docker is installed but not running" >&2; exit 2; }

VERSION=$(curl -sfL -H "Accept: application/vnd.api+json" "https://workflowhub.eu/workflows/$WID" \
    | python3 -c 'import json, sys; print(json.load(sys.stdin)["data"]["attributes"].get("latest_version") or 1)')
URL="https://workflowhub.eu/workflows/$WID?version=$VERSION"
echo "== WorkflowHub #$WID v$VERSION -> $OUT"

recrate agentify --crate "$URL" --out "$OUT"      # the adapter reads nextflow_schema.json and conf/test.config
echo "== Before the run"
recrate inspect "$OUT"                             # readiness: ready, or what is missing
echo "== Reproduce: nextflow run ... -profile test,docker, as the authors run their test"
recrate reproduce "$OUT" --who "$WHO" || true      # exit 2 when the numbers differ; the run crate is kept either way
echo "== After the run: the verdict ladder"
recrate inspect "$OUT"
echo "== Run crates in $OUT/runs"
ls "$OUT/runs"
