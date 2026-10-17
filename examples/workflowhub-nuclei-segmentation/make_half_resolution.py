"""Make the reuse example: the research images at half resolution, as from a 2x2-binned camera or a lower
magnification. Each 2x2 block of pixels is averaged, so every pixel is twice as wide as in the research's images.

    python examples/workflowhub-nuclei-segmentation/make_half_resolution.py

Writes my-data-half-resolution/ next to this file. The full-resolution counts (425, 413, 307) are the reference.
"""

from pathlib import Path

import tifffile
from skimage.transform import downscale_local_mean

here = Path(__file__).resolve().parent
out = here / "my-data-half-resolution"
out.mkdir(exist_ok=True)
for p in sorted((here / "research-data" / "B2").glob("*.tif")):
    x = tifffile.imread(p)
    tifffile.imwrite(out / p.name, downscale_local_mean(x.astype(float), (2, 2)).astype(x.dtype))
    print(out / p.name)
