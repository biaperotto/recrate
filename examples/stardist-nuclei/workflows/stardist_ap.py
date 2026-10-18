"""Table 1 of StarDist (Schmidt et al. 2018, arXiv:1806.03535), DSB2018 row: average precision at IoU thresholds 0.5 to
0.9, with the model trained in the paper, on the paper's test images and their ground truth.

ReCRATE's script contract:
    python stardist_ap.py --input_dir DIR --output_dir DIR [--prob_thresh 0.417819] [--nms_thresh 0.5]
Reads the images in DIR/images/ and their ground truth in DIR/masks/ (same names; label TIFF or PNG, or BBBC039's RGB
encoding), so the same check runs on another annotated dataset. Writes output_dir/results.csv (tau, ap), and the
predicted label images in output_dir/masks/ for the overlays.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import numpy as np

TAUS = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--input_dir", required=True)
    ap.add_argument("--output_dir", required=True)
    ap.add_argument("--prob_thresh", type=float, default=0.417819)
    ap.add_argument("--nms_thresh", type=float, default=0.5)
    ap.add_argument("--model", default="2D_paper_dsb2018")
    a = ap.parse_args()

    from csbdeep.utils import normalize
    from stardist.matching import matching_dataset
    from stardist.models import StarDist2D
    from tifffile import imwrite

    from segment_nuclei import ground_truth, images_in, read  # the same readers as the segmentation method

    d, out = Path(a.input_dir), Path(a.output_dir)
    (out / "masks").mkdir(parents=True, exist_ok=True)
    model = StarDist2D.from_pretrained(a.model)
    truth, pred = [], []
    for f in images_in(d):  # the folder itself, or its images/
        gt = ground_truth(d, f.stem)
        if gt is None:
            continue  # no ground truth for this image: nothing to score
        labels, _ = model.predict_instances(normalize(read(f), 1, 99.8), prob_thresh=a.prob_thresh,
                                            nms_thresh=a.nms_thresh, show_tile_progress=False)
        imwrite(out / "masks" / f"{f.stem}_mask.tif", labels.astype(np.uint16))
        pred.append(labels)
        truth.append(gt)
    if not truth:
        raise SystemExit("No image has ground truth: put label images named like the images in ground-truth/ "
                         "(or masks/) next to them.")
    stats = matching_dataset(truth, pred, thresh=TAUS, show_progress=False)
    with (out / "results.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["tau", "ap"])
        for t, s in zip(TAUS, stats):
            w.writerow([f"{t:.2f}", f"{s.accuracy:.4f}"])


if __name__ == "__main__":
    main()
