#!/usr/bin/env bash
# THROWAWAY SPIKE — pinned to Jones et al. (2026), not general reCRATE code.
#
# Regenerates the paper's swimmer plot by running the authors' own R code,
# at their own pinned R version, in Docker, from their own shared data —
# then puts it beside the figure they published.
#
#   ./run.sh            # clone at the pinned commit, build, run
#   ./run.sh <repo-dir> # reuse an existing clone
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_URL="https://github.com/Lee-V-Jones/Reproducibility.git"
PIN="370e615"
OUT="$HERE/out"
IMAGE="recrate-spike-figures:r4.4.2"
# amd64 so rocker pulls prebuilt package binaries; on Apple Silicon this runs
# under emulation, which is far faster than compiling ggplot2 from source.
PLATFORM="linux/amd64"

REPO="${1:-$HERE/.repo}"
if [ ! -d "$REPO/.git" ]; then
  echo "==> Cloning the authors' repository, pinned to $PIN"
  rm -rf "$REPO"
  git clone --quiet "$REPO_URL" "$REPO"
  git -C "$REPO" checkout --quiet "$PIN"
fi
echo "==> Repository at commit $(git -C "$REPO" rev-parse --short HEAD) (pinned: $PIN)"

echo "==> Building the image (R 4.4.2, the version the paper's session info records)"
docker build --platform "$PLATFORM" -t "$IMAGE" "$HERE"

mkdir -p "$OUT"
echo "==> Running the authors' plotting code in the container"
docker run --rm --platform "$PLATFORM" \
  -v "$REPO:/repo:ro" -v "$OUT:/out" "$IMAGE" /repo /out

PUB="$REPO/figures/swimmerB.png"
GOT="$OUT/swimmerB.regenerated.png"
echo
echo "================= result ================="
echo "Published by the authors : $PUB"
[ -f "$PUB" ] && echo "                           $(wc -c <"$PUB" | tr -d ' ') bytes"
echo "Regenerated here         : $GOT"
[ -f "$GOT" ] && echo "                           $(wc -c <"$GOT" | tr -d ' ') bytes"
echo "=========================================="
echo
echo "A person decides whether these match — reCRATE states only that the run"
echo "succeeded and produced the file. Open them side by side:"
echo "  open \"$PUB\" \"$GOT\""
