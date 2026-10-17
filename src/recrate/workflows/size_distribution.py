"""Nuclear size distribution (demo 'paper code', a standalone Python script).

Usage:
    python size_distribution.py --input_dir DIR --output_dir DIR
                                [--min_size_px 50] [--large_area_um2 150] [--channel 0]

For each calibrated TIFF: Otsu threshold, 8-connected objects of at least
min_size_px pixels, then per image the number of nuclei, the median nuclear area
(µm²) and the fraction of nuclei larger than large_area_um2. Writes results.csv
and masks/ to output_dir.
"""

import argparse
import csv
from pathlib import Path

import numpy as np
import tifffile
from skimage.filters import threshold_otsu
from skimage.measure import label, regionprops


def pixel_size(tif):
    tag = tif.pages[0].tags.get("XResolution")
    if tag is None:
        return None
    num, den = tag.value
    return float(den) / float(num) if num else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input_dir", required=True)
    ap.add_argument("--output_dir", required=True)
    ap.add_argument("--min_size_px", type=float, default=50)
    ap.add_argument("--large_area_um2", type=float, default=150)
    ap.add_argument("--channel", type=int, default=0)
    a = ap.parse_args()

    out = Path(a.output_dir)
    (out / "masks").mkdir(parents=True, exist_ok=True)
    with open(out / "results.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["image", "count", "median_area", "fraction_large"])
        for f in sorted(Path(a.input_dir).glob("*.tif*")):
            with tifffile.TiffFile(f) as tif:
                img = tif.series[0].asarray().astype(float)
                px = pixel_size(tif) or 1.0
            if img.ndim == 3:
                img = img[a.channel]
            lab = label(img > threshold_otsu(img), connectivity=2)
            kept = [r for r in regionprops(lab) if r.area >= a.min_size_px]
            areas = np.array([r.area * px * px for r in kept])
            tifffile.imwrite(out / "masks" / f"{f.stem}_mask.tif",
                             (np.isin(lab, [r.label for r in kept]) * 255).astype(np.uint8))
            median = float(np.median(areas)) if len(areas) else 0.0
            frac = float((areas > a.large_area_um2).mean()) if len(areas) else 0.0
            w.writerow([f.name, len(kept), f"{median:.4f}", f"{frac:.4f}"])


if __name__ == "__main__":
    main()
