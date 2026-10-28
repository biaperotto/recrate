"""Figures a notebook or a Quarto document drew during a run, brought into the run's ``figures/`` folder.

A notebook keeps its plots inside the executed ``.ipynb`` (base64 cell outputs) and Quarto writes them to a
``*_files/figure-html/`` folder next to the rendered report; neither lands in ``output_dir/figures/`` on its own,
so the run crate would record the report but not the figures. As Paper2Agent's ``extract_notebook_images`` does,
each image is written out with its provenance (the cell and output it came from, its hash) in ``image-manifest.json``,
so a figure in the run crate can be traced back to the cell that drew it.
"""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

MANIFEST = "image-manifest.json"
MIMES = (("image/png", "png"), ("image/jpeg", "jpg"), ("image/svg+xml", "svg"))


def extract_notebook_images(notebook: Path, dest: Path) -> list[dict[str, Any]]:
    """Write every image a notebook's code cells output to ``dest/figure_<n>.<ext>`` (one rendition per output:
    PNG, else JPEG, else SVG), with ``image-manifest.json`` listing the cell index, cell id, output index, mime
    type and sha256 of each. Files already in ``dest`` (figures the notebook saved itself) are left alone."""
    notebook, dest = Path(notebook), Path(dest)
    raw = notebook.read_bytes()
    nb = json.loads(raw)
    images: list[dict[str, Any]] = []
    for ci, cell in enumerate(nb.get("cells", [])):
        if cell.get("cell_type") != "code":
            continue
        for oi, output in enumerate(cell.get("outputs", [])):
            data = output.get("data") or {}
            for mime, ext in MIMES:
                if mime not in data:
                    continue
                value = data[mime]
                if isinstance(value, list):
                    value = "".join(value)
                payload = value.encode("utf-8") if ext == "svg" else base64.b64decode("".join(str(value).split()))
                images.append({"path": f"figure_{len(images) + 1}.{ext}", "cell_index": ci, "cell_id": cell.get("id"),
                               "output_index": oi, "mime_type": mime, "sha256": hashlib.sha256(payload).hexdigest(),
                               "_payload": payload})
                break
    if not images:
        return []
    dest.mkdir(parents=True, exist_ok=True)
    for entry in images:
        (dest / entry["path"]).write_bytes(entry.pop("_payload"))
    manifest = {"schema_version": 1, "generator": "recrate.notebook_figures", "notebook": notebook.name,
                "notebook_sha256": hashlib.sha256(raw).hexdigest(), "images": images}
    (dest / MANIFEST).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return images


def collect_quarto_figures(work: Path, dest: Path) -> list[dict[str, Any]]:
    """Copy the figures Quarto rendered (``*_files/figure-html/*`` and ``figure-pdf``) into ``dest``, with a manifest
    naming the file each came from."""
    work, dest = Path(work), Path(dest)
    images: list[dict[str, Any]] = []
    for folder in sorted(work.glob("*_files")):
        for sub in ("figure-html", "figure-pdf", "figure-docx"):
            for f in sorted((folder / sub).glob("*")):
                if f.suffix.lower() not in {".png", ".jpg", ".jpeg", ".svg", ".pdf"} or not f.is_file():
                    continue
                dest.mkdir(parents=True, exist_ok=True)
                name = f.name if not (dest / f.name).exists() else f"{folder.name}-{f.name}"
                shutil.copy2(f, dest / name)
                images.append({"path": name, "rendered_from": f"{folder.name}/{sub}/{f.name}",
                               "sha256": hashlib.sha256(f.read_bytes()).hexdigest()})
    if images:
        (dest / MANIFEST).write_text(json.dumps({"schema_version": 1, "generator": "recrate.notebook_figures",
                                                 "images": images}, indent=2), encoding="utf-8")
    return images
