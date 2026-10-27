"""Python reference runner for the demo nuclei-counting macro.

Mirrors ``count_nuclei.ijm`` with scikit-image (Otsu threshold, 8-connected
particles, minimum size in pixels, areas in calibrated units) so the whole
pipeline can be developed and tested without Fiji. It is only valid for that
demo workflow; real papers run through the Fiji runner.

Small numeric differences from Fiji are expected (threshold implementation);
per-measurement tolerances in the crate absorb them.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import numpy as np
import skimage
import tifffile
from skimage.filters import threshold_otsu
from skimage.measure import label, regionprops

from .. import __version__
from ..profiles.bioimaging import list_images, read_image_metadata
from .base import Runner, RunResult, now_iso

SUPPORTED_WORKFLOWS = {"count_nuclei.ijm"}


def _plane(arr: np.ndarray, axes: str, channel: int) -> np.ndarray:
    for ax in ("T", "Z"):
        if ax in axes:
            arr = np.take(arr, 0, axis=axes.index(ax))
            axes = axes.replace(ax, "")
    for ax in ("C", "S"):
        if ax in axes:
            arr = np.take(arr, channel, axis=axes.index(ax))
            axes = axes.replace(ax, "")
    return arr


class PythonReferenceRunner(Runner):
    name = "python-reference"

    def run(self, workflow: Path, input_dir: Path, output_dir: Path, params: dict[str, Any],
            channel: int = 0, progress=None) -> RunResult:
        if Path(workflow).name not in SUPPORTED_WORKFLOWS:
            raise ValueError(
                f"The Python reference runner only mirrors {sorted(SUPPORTED_WORKFLOWS)}; "
                f"use the Fiji runner for {Path(workflow).name}."
            )
        method = str(params.get("threshold_method", "Otsu"))
        if method.lower() != "otsu":
            raise ValueError(f"Python reference runner supports threshold_method='Otsu' only, got {method!r}.")
        min_size = float(params.get("min_size_px", 50))

        output_dir = Path(output_dir)
        masks_dir = output_dir / "masks"
        masks_dir.mkdir(parents=True, exist_ok=True)
        results = output_dir / "results.csv"
        started = now_iso()
        with results.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["image", "count", "mean_area", "area_unit", "pixel_size", "pixel_unit"])
            images = list_images(Path(input_dir))
            for i, img_path in enumerate(images):
                if progress:
                    progress(i, len(images), img_path.name)
                meta = read_image_metadata(img_path)
                with tifffile.TiffFile(img_path) as tif:
                    arr = tif.series[0].asarray()
                img = _plane(arr, meta["axes"], channel).astype(float)
                mask = img > threshold_otsu(img)
                lab = label(mask, connectivity=2)
                px = meta["pixel_size"] or 1.0
                unit = meta["unit"] or "pixel"
                kept = [r for r in regionprops(lab) if r.area >= min_size]
                keep_mask = np.isin(lab, [r.label for r in kept])
                areas = [r.area * px * px for r in kept]
                mean_area = float(np.mean(areas)) if areas else 0.0
                tifffile.imwrite(masks_dir / f"{img_path.stem}_mask.tif", (keep_mask * 255).astype(np.uint8))
                w.writerow([img_path.name, len(kept), f"{mean_area:.4f}", f"{unit}^2", f"{px:.6f}", unit])
        return RunResult(
            results_csv=results,
            masks_dir=masks_dir,
            software=[("ReCRATE Python reference runner", __version__), ("scikit-image", skimage.__version__)],
            started=started,
            ended=now_iso(),
            params={"threshold_method": method, "min_size_px": min_size},
        )
