"""Generate the demo: a 'paper' crate and a folder of 'your images'.

Synthetic fluorescence nuclei, so the demo needs no downloads:

* paper data: 6 images, 256×256, pixel size 0.65 µm, with nuclei and small debris
* your images: 3 new images of the same kind, imaged at half the resolution
  (128×128, pixel size 1.3 µm). Running the paper's ``min_size_px = 50`` on them
  silently drops real nuclei; ReCRATE catches the mismatch and rescales it.

The paper's "reported results" are produced by the Python reference runner.
For a real demo, swap in a public dataset (e.g. a BBBC nuclei set) and a
reported number, and regenerate expected results with Fiji.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import numpy as np
import tifffile
from skimage.filters import gaussian
from skimage.transform import downscale_local_mean

from .draft import build_crate
from .runners.python_ref import PythonReferenceRunner

WORKFLOW = Path(__file__).parent / "workflows" / "count_nuclei.ijm"


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


def make_demo(out_dir: str | Path, seed: int = 7) -> dict[str, Path]:
    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    rng = np.random.default_rng(seed)

    with tempfile.TemporaryDirectory(prefix="recrate-demo-") as tmp:
        tmp = Path(tmp)
        paper_imgs = tmp / "paper_images"
        paper_imgs.mkdir()
        for i in range(6):
            save_calibrated(paper_imgs / f"field_{i + 1:02d}.tif", synth_image(rng), 0.65)

        # The paper's reported results (here from the reference implementation)
        res = PythonReferenceRunner().run(WORKFLOW, paper_imgs, tmp / "paper_out",
                                          {"threshold_method": "Otsu", "min_size_px": 50})
        shutil.copy2(res.results_csv, tmp / "reported.csv")
        shutil.copy2(WORKFLOW, tmp / WORKFLOW.name)

        desc = {
            "name": "Nuclei counting in 2D fluorescence images (Fiji)",
            "description": ("Counts nuclei per field and their mean area. Otsu threshold, particles of at "
                            "least 50 pixels kept to exclude debris. Synthetic demo standing in for a paper."),
            "identifier": "https://example.org/recrate-demo/nuclei-counting/v1",
            "citation": "https://doi.org/10.0000/recrate-demo-paper",
            "license": "https://spdx.org/licenses/CC-BY-4.0",
            "authors": ["ReCRATE demo"],
            "profile": "bioimaging",
            "workflow": {"file": WORKFLOW.name, "language": "ImageJ Macro Language",
                         "software": [{"name": "Fiji", "version": "2.16.0"}]},
            "inputs": [
                {"id": "in-images", "name": "input_dir", "type": "File", "format": "image/tiff",
                 "description": "2D single-channel fluorescence images of nuclei, calibrated",
                 "properties": {"pixel size": {"value": 0.65, "unit": "µm"},
                                "channels": {"value": 1}, "dimensions": {"value": "2D"}}},
                {"id": "in-threshold", "name": "threshold_method", "type": "Text", "default": "Otsu",
                 "description": "ImageJ auto-threshold method"},
                {"id": "in-min-size", "name": "min_size_px", "type": "Float", "default": 50,
                 "description": "Minimum particle area in pixels; removes debris",
                 "scaling": "inverse_pixel_area"},
            ],
            "outputs": [{"id": "out-results", "name": "results", "type": "File", "format": "text/csv",
                         "description": "One row per image: count and mean area (µm²)",
                         "tolerances": {"count": 0.05, "mean_area": 0.10}}],
            "example_data": "paper_images",
            "expected": "reported.csv",
        }
        crate_dir = build_crate(desc, out_dir / "paper_crate", base_dir=tmp)
        (out_dir / "paper_crate_description.json").write_text(
            json.dumps({**desc, "workflow": {**desc["workflow"], "file": "paper_crate/" + WORKFLOW.name},
                        "example_data": "paper_crate/data", "expected": "paper_crate/expected/results.csv"},
                       indent=2, ensure_ascii=False), encoding="utf-8")

    mine = out_dir / "my_images"
    mine.mkdir()
    for i in range(3):
        full = synth_image(rng).astype(float)
        half = downscale_local_mean(full, (2, 2)).astype(np.uint16)
        save_calibrated(mine / f"my_field_{i + 1:02d}.tif", half, 1.3)
    return {"crate": crate_dir, "my_images": mine}
