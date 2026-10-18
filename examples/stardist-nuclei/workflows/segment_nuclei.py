"""Segment nuclei with StarDist (Schmidt et al. 2018, arXiv:1806.03535), using the model trained in the paper.

ReCRATE's script contract:
    python segment_nuclei.py --input_dir DIR --output_dir DIR [--prob_thresh 0.417819] [--nms_thresh 0.5] [--scale 1]
Reads the images in DIR (or DIR/images/): TIFF or PNG, one nucleus channel. Writes:
* output_dir/results.csv: per image, the number of nuclei and their mean area in pixels; when ground truth is found
  (DIR/masks/<image name>.tif or .png), also its number of nuclei and the average precision at IoU 0.5;
* output_dir/masks/<image>_mask.tif: the label image (one value per nucleus), for the overlays.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import numpy as np

IMAGES = {".tif", ".tiff", ".png"}


def images_in(d: Path) -> list[Path]:
    found = sorted(p for p in d.iterdir() if p.suffix.lower() in IMAGES)
    return found or (sorted(p for p in (d / "images").iterdir() if p.suffix.lower() in IMAGES) if (d / "images").is_dir() else [])


def read(path: Path) -> np.ndarray:
    if path.suffix.lower() in {".tif", ".tiff"}:
        from tifffile import imread

        return imread(path)
    from PIL import Image

    return np.array(Image.open(path))


def ground_truth(d: Path, stem: str) -> np.ndarray | None:
    """Labels for an image, from a label image (TIFF or PNG) or a colouring where touching nuclei get different values
    (BBBC039: values 1 to 3 in the red channel). Either way, each nucleus is one connected region of one value."""
    from skimage.measure import label

    for p in (d / "masks" / f"{stem}{s}" for s in (".tif", ".tiff", ".png")):
        if p.exists():
            m = read(p)
            if m.ndim == 3:
                m = m[..., 0].astype(np.int64) + 256 * m[..., 1].astype(np.int64)
            out = np.zeros(m.shape, np.int32)
            for v in np.unique(m)[1:]:
                regions = label(m == v)
                out[regions > 0] = regions[regions > 0] + out.max()
            return out
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--input_dir", required=True)
    ap.add_argument("--output_dir", required=True)
    ap.add_argument("--prob_thresh", type=float, default=0.417819)
    ap.add_argument("--nms_thresh", type=float, default=0.5)
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--model", default="2D_paper_dsb2018")
    a = ap.parse_args()

    from csbdeep.utils import normalize
    from stardist.matching import matching
    from stardist.models import StarDist2D
    from tifffile import imwrite

    d, out = Path(a.input_dir), Path(a.output_dir)
    (out / "masks").mkdir(parents=True, exist_ok=True)
    model = StarDist2D.from_pretrained(a.model)
    rows = []
    for f in images_in(d):
        x = read(f)
        if x.ndim == 3:  # one nucleus channel: the first, or the mean of an RGB rendering
            x = x[..., 0] if x.shape[-1] in (3, 4) and np.allclose(x[..., 0], x[..., 1]) else x.mean(axis=-1)
        labels, _ = model.predict_instances(normalize(x, 1, 99.8), prob_thresh=a.prob_thresh, nms_thresh=a.nms_thresh,
                                            scale=None if a.scale == 1 else a.scale, show_tile_progress=False)
        imwrite(out / "masks" / f"{f.stem}_mask.tif", labels.astype(np.uint16))
        areas = np.bincount(labels.ravel())[1:]
        areas = areas[areas > 0]
        row = {"image": f.name, "count": int(labels.max()), "mean_area_px": round(float(areas.mean()), 2) if len(areas) else 0}
        gt = ground_truth(d, f.stem)
        if gt is not None:
            row["count_true"] = int(len(np.unique(gt)) - 1)
            row["ap_0.5"] = round(float(matching(gt, labels, thresh=0.5).accuracy), 4)
        rows.append(row)
    cols = list(dict.fromkeys(k for r in rows for k in r))
    with (out / "results.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, cols)
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    main()
