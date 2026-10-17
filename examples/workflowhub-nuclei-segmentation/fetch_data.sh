#!/usr/bin/env bash
# Download wells of the siRNA screen (DAPI-stained HeLa nuclei) from Zenodo, CC-BY-4.0.
# Gunkel, M. (2019). Microscope Image Analysis Course Sept 2019 -- Images siRNA Screen.
# https://doi.org/10.5281/zenodo.3362976
# Usage: ./fetch_data.sh [WELL ...]     e.g. ./fetch_data.sh B2 B3 C4   (default: B2, all 9 fields)
set -euo pipefail
cd "$(dirname "$0")/research-data"
for well in "${@:-B2}"; do
  mkdir -p "$well"
  curl -fL -o "$well.zip" "https://zenodo.org/records/3362976/files/$well.zip"
  unzip -o -q "$well.zip" -d "$well" && rm "$well.zip"
  echo "$well: $(ls "$well" | wc -l | tr -d ' ') images"
done
