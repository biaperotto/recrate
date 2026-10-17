"""Overlay of detected objects on the input image, for a human to check."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import tifffile
from PIL import Image
from skimage.segmentation import find_boundaries

from .profiles.bioimaging import read_image_metadata
from .runners.python_ref import _plane


def make_overlay(image_path: Path, mask_path: Path, out_path: Path, channel: int = 0) -> Path:
    meta = read_image_metadata(image_path)
    with tifffile.TiffFile(image_path) as tif:
        img = _plane(tif.series[0].asarray(), meta["axes"], channel).astype(float)
    mask = tifffile.imread(mask_path) > 0
    lo, hi = np.percentile(img, (1, 99.5))
    grey = np.clip((img - lo) / max(hi - lo, 1e-9), 0, 1)
    rgb = np.stack([grey] * 3, axis=-1)
    edges = find_boundaries(mask, mode="outer")
    rgb[edges] = (1.0, 0.75, 0.0)
    Image.fromarray((rgb * 255).astype(np.uint8)).save(out_path)
    return out_path


def make_overlays(input_dir: Path, masks_dir: Path, out_dir: Path, channel: int = 0, limit: int = 6) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    made = []
    for mask in sorted(Path(masks_dir).glob("*_mask.tif"))[:limit]:
        stem = mask.name[: -len("_mask.tif")]
        for suffix in (".tif", ".tiff"):
            img = Path(input_dir) / f"{stem}{suffix}"
            if img.exists():
                made.append(make_overlay(img, mask, out_dir / f"{stem}_overlay.png", channel))
                break
    return made
