"""Generate the demo: a short 'paper', its RO-Crate with two methods, and 'your images'.

Everything is synthetic so the demo needs no downloads:

* paper.md: a short methods paper with two methods and reported numbers
* paper_crate/: input RO-Crate with
    - count_nuclei: a Fiji macro (count and mean area per field)
    - size_distribution: a Python script (median area, fraction of large nuclei)
  plus the paper's 6 images (256×256, 0.65 µm pixels) and its reported results
* my_images/: 3 new images of the same kind, imaged at half the resolution
  (128×128, 1.3 µm pixels). The paper's ``min_size_px = 50`` would silently drop
  real nuclei on them; ReCRATE catches the mismatch and rescales it.

Reported results come from the Python reference runner and the script itself.
For a real demo, swap in a public dataset and a published number.
"""

from __future__ import annotations

import csv
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from statistics import mean

import numpy as np
import tifffile
from skimage.filters import gaussian
from skimage.transform import downscale_local_mean

from .draft import build_crate
from .runners.python_ref import PythonReferenceRunner

WORKFLOWS = Path(__file__).parent / "workflows"
MACRO = WORKFLOWS / "count_nuclei.ijm"
SCRIPT = WORKFLOWS / "size_distribution.py"
DOI = "10.0000/recrate-demo-paper"


def synth_image(rng: np.random.Generator, size: int = 256, n_nuclei: int = 22, n_debris: int = 25) -> np.ndarray:
    img = np.full((size, size), 100.0)
    yy, xx = np.mgrid[:size, :size]
    centres: list[tuple[float, float, float]] = []
    tries = 0
    while len(centres) < n_nuclei and tries < 5000:
        tries += 1
        r = rng.uniform(6, 12)
        cy, cx = rng.uniform(r + 2, size - r - 2, size=2)
        if all((cy - y) ** 2 + (cx - x) ** 2 > (r + rr + 3) ** 2 for y, x, rr in centres):
            centres.append((cy, cx, r))
            ry, rx = r * rng.uniform(0.85, 1.15), r * rng.uniform(0.85, 1.15)
            img[((yy - cy) / ry) ** 2 + ((xx - cx) / rx) ** 2 <= 1] = rng.uniform(900, 1300)
    for _ in range(n_debris):
        r = rng.uniform(1.5, 3.0)
        cy, cx = rng.uniform(4, size - 4, size=2)
        if all((cy - y) ** 2 + (cx - x) ** 2 > (rr + r + 3) ** 2 for y, x, rr in centres):
            img[(yy - cy) ** 2 + (xx - cx) ** 2 <= r ** 2] = rng.uniform(800, 1100)
    img = gaussian(img, sigma=1.2, preserve_range=True)
    img += rng.normal(0, 25, img.shape)
    return np.clip(img, 0, 65535).astype(np.uint16)


def save_calibrated(path: Path, img: np.ndarray, pixel_size_um: float) -> None:
    tifffile.imwrite(path, img, imagej=True, resolution=(1 / pixel_size_um, 1 / pixel_size_um),
                     metadata={"unit": "um"})


def _col_mean(csv_path: Path, col: str) -> float:
    with csv_path.open(newline="") as fh:
        return mean(float(r[col]) for r in csv.DictReader(fh))


AGREEMENT = ("Repeated acquisitions of the same fields gave counts within 5%, areas within 10% and fractions of "
             "large nuclei within 15%.")


def _stated(value: float) -> dict:
    return {"value": value, "source": "paper-stated", "quote": AGREEMENT}


PAPER = """# Counting and sizing nuclei in 2D fluorescence images with Fiji

ReCRATE demo authors. Demo Journal of Bioimage Methods (2026). https://doi.org/{doi}

## Abstract

We describe two simple, reusable methods for 2D fluorescence images of stained nuclei: a Fiji macro that
counts nuclei per field and reports their mean area, and a Python script that reports the nuclear size
distribution. On our dataset of six fields we find on average {count:.0f} nuclei per field with a mean
nuclear area of {area:.1f} µm².

## Data

Six fields of view, 256 × 256 pixels, single channel (nuclear stain), acquired at a pixel size of
0.65 µm. Images are calibrated TIFF files.

## Methods

### Nuclei counting (Fiji)

Images were thresholded with the Otsu method (dark background) and converted to masks. Particles were
analysed with Analyze Particles, keeping particles of at least 50 pixels to exclude debris. For each
field we report the number of nuclei and their mean area in µm². The macro is count_nuclei.ijm and was
run in Fiji 2.16.0.

### Nuclear size distribution (Python)

The same segmentation (Otsu threshold, 8-connected objects of at least 50 pixels) was done in Python with
scikit-image. For each field we report the median nuclear area and the fraction of nuclei larger than
150 µm². The script is size_distribution.py.

## Results

Across the six fields, the mean count was {count:.1f} nuclei per field and the mean nuclear area was
{area:.1f} µm². The median nuclear area per field averaged {median:.1f} µm², and on average
{frac:.0%} of nuclei were larger than 150 µm². Per-field values are given in the supplementary tables.
Repeated acquisitions of the same fields gave counts within 5%, areas within 10% and fractions of large
nuclei within 15%.

## Data and code availability

Images, the Fiji macro, the Python script and per-field results are packaged as an RO-Crate.
"""


def make_demo(out_dir: str | Path, seed: int = 7) -> dict[str, Path]:
    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    rng = np.random.default_rng(seed)

    with tempfile.TemporaryDirectory(prefix="recrate-demo-") as tmp:
        tmp = Path(tmp)
        imgs = tmp / "images"
        imgs.mkdir()
        for i in range(6):
            save_calibrated(imgs / f"field_{i + 1:02d}.tif", synth_image(rng), 0.65)
        shutil.copy2(MACRO, tmp / MACRO.name)
        shutil.copy2(SCRIPT, tmp / SCRIPT.name)

        # The paper's reported results
        res = PythonReferenceRunner().run(MACRO, imgs, tmp / "out_count",
                                          {"threshold_method": "Otsu", "min_size_px": 50})
        shutil.copy2(res.results_csv, tmp / "table_counts.csv")
        subprocess.run([sys.executable, str(SCRIPT), "--input_dir", str(imgs), "--output_dir",
                        str(tmp / "out_size")], check=True)
        shutil.copy2(tmp / "out_size" / "results.csv", tmp / "table_sizes.csv")

        image_input = {"id": "in-images", "name": "input_dir", "type": "File", "format": "image/tiff",
                       "description": "2D single-channel fluorescence images of nuclei, calibrated",
                       "properties": {"pixel size": {"value": 0.65, "unit": "µm"},
                                      "channels": {"value": 1}, "dimensions": {"value": "2D"}}}
        min_size = {"id": "in-min-size", "name": "min_size_px", "type": "Float", "default": 50,
                    "description": "Minimum object area in pixels; removes debris", "scaling": "inverse_pixel_area"}
        desc = {
            "name": "Counting and sizing nuclei in 2D fluorescence images with Fiji",
            "description": "Synthetic demo paper: two methods for 2D fluorescence images of nuclei.",
            "identifier": f"https://doi.org/{DOI}#crate",
            "citation": f"https://doi.org/{DOI}",
            "license": "https://spdx.org/licenses/CC-BY-4.0",
            "authors": ["ReCRATE demo authors"],
            "profile": "bioimaging",
            "methods": [
                {
                    "name": "count_nuclei",
                    "description": "Counts nuclei per field and their mean area (Otsu threshold, debris filter).",
                    "workflow": {"file": MACRO.name, "language": "ImageJ Macro Language",
                                 "software": [{"name": "Fiji", "version": "2.16.0"}]},
                    "inputs": [image_input,
                               {"id": "in-threshold", "name": "threshold_method", "type": "Text",
                                "default": "Otsu", "description": "ImageJ auto-threshold method"},
                               min_size],
                    "outputs": [{"id": "out-results", "name": "results", "type": "File", "format": "text/csv",
                                 "description": "Per field: count and mean area (µm²)",
                                 "tolerances": {"count": _stated(0.05), "mean_area": _stated(0.10)}}],
                    "example_data": "images",
                    "expected": "table_counts.csv",
                },
                {
                    "name": "size_distribution",
                    "description": "Median nuclear area and fraction of large nuclei per field.",
                    "workflow": {"file": SCRIPT.name, "language": "Python",
                                 "software": [{"name": "Python", "version": "3.10+"},
                                              {"name": "scikit-image", "version": "0.21+"}]},
                    "inputs": [image_input, min_size,
                               {"id": "in-large", "name": "large_area_um2", "type": "Float", "default": 150,
                                "description": "Area above which a nucleus counts as large (µm²)"}],
                    "outputs": [{"id": "out-results", "name": "results", "type": "File", "format": "text/csv",
                                 "description": "Per field: count, median area (µm²), fraction large",
                                 "tolerances": {"median_area": _stated(0.10), "fraction_large": _stated(0.15)}}],
                    "example_data": "images",
                    "expected": "table_sizes.csv",
                },
            ],
        }
        crate_dir = build_crate(desc, out_dir / "paper_crate", base_dir=tmp)

        paper = out_dir / "paper.md"
        paper.write_text(PAPER.format(
            doi=DOI, count=_col_mean(tmp / "table_counts.csv", "count"),
            area=_col_mean(tmp / "table_counts.csv", "mean_area"),
            median=_col_mean(tmp / "table_sizes.csv", "median_area"),
            frac=_col_mean(tmp / "table_sizes.csv", "fraction_large")), encoding="utf-8")

        rel = {**desc, "methods": [
            {**m, "workflow": {**m["workflow"], "file": f"paper_crate/workflows/{Path(m['workflow']['file']).name}"},
             "example_data": f"paper_crate/data/{m['name'].replace('_', '-')}",
             "expected": f"paper_crate/expected/{m['name'].replace('_', '-')}.csv"} for m in desc["methods"]]}
        (out_dir / "paper_crate_description.json").write_text(json.dumps(rel, indent=2, ensure_ascii=False),
                                                              encoding="utf-8")

    mine = out_dir / "my_images"
    mine.mkdir()
    for i in range(3):
        half = downscale_local_mean(synth_image(rng).astype(float), (2, 2)).astype(np.uint16)
        save_calibrated(mine / f"my_field_{i + 1:02d}.tif", half, 1.3)
    return {"crate": crate_dir, "my_images": mine, "paper": paper}
