"""The StarDist example: fetching a second dataset to reuse the method on, and reading its ground truth."""

from __future__ import annotations

import io
import subprocess
import sys
import zipfile
from pathlib import Path

import numpy as np
import pytest

from recrate import sources

EX = Path(__file__).resolve().parent.parent / "examples" / "stardist-nuclei"


def _zip(folder: str, names: list[str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n in names:
            z.writestr(f"{folder}/{n}", b"x")
    return buf.getvalue()


def test_fetch_dataset_unpacks_side_by_side_and_only_once(tmp_path, monkeypatch):
    calls = []

    def fake_download(url, dest, limit=0, progress=None):
        calls.append(url)
        folder = Path(url).stem  # images.zip -> images/
        dest.write_bytes(_zip(folder, ["a.tif", "b.tif"] if folder == "images" else ["a.png", "b.png"]))
        return dest

    monkeypatch.setattr(sources, "_download", fake_download)
    refs = ["https://example.org/data/images.zip", "https://example.org/data/masks.zip"]
    root = sources.fetch_dataset(refs, tmp_path / "d")
    assert root == tmp_path / "d" and (root / "images" / "a.tif").exists() and (root / "masks" / "b.png").exists()
    assert sources.fetch_dataset(refs, tmp_path / "d") == root and len(calls) == 2  # the second time: nothing fetched


@pytest.mark.skipif(not (EX / "workflows" / "segment_nuclei.py").exists(), reason="StarDist example not present")
def test_colouring_masks_give_one_label_per_nucleus(tmp_path):
    pytest.importorskip("skimage")
    pytest.importorskip("PIL")
    from PIL import Image

    m = np.zeros((20, 20, 4), np.uint8)
    m[..., 3] = 255
    m[2:6, 2:6, 0] = 1    # two separate nuclei with the same colour
    m[10:14, 2:6, 0] = 1
    m[2:6, 6:10, 0] = 2   # touching the first one, so another colour
    (tmp_path / "masks").mkdir()
    Image.fromarray(m).save(tmp_path / "masks" / "img.png")
    code = ("import sys, numpy as np; from pathlib import Path; from segment_nuclei import ground_truth; "
            "g = ground_truth(Path(sys.argv[1]), 'img'); print(len(np.unique(g)) - 1)")
    out = subprocess.run([sys.executable, "-c", code, str(tmp_path)], cwd=EX / "workflows", capture_output=True,
                         text=True, check=True)
    assert out.stdout.strip() == "3"
