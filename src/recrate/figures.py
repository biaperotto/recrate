"""Publication figures from a run's images: panels, letters, calibrated scale bars, same physical scale.

Needs matplotlib (``pip install "recrate[app]"``). Pixel sizes come from the images' calibration, so a
scale bar always means what it says, and ``same_scale`` resamples panels to one µm per pixel so a
nucleus in the paper's image and in yours appear the same size.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import tifffile
from PIL import Image

from .profiles.bioimaging import read_image_metadata, to_um
from .runners.python_ref import _plane

# Reference categorical slots 1 and 2 (validated palette), neutral ink for text and the paper's band.
SERIES = ["#2a78d6", "#eb6834"]
INK, INK_MUTED, BAND, GRID = "#0b0b0b", "#52514e", "#e4e2dc", "#ecebe6"


@dataclass
class Panel:
    path: str
    label: str = ""
    pixel_size_um: float | None = None  # None: read from the file (TIFF), or unknown


def pixel_size_um(path: str | Path) -> float | None:
    p = Path(path)
    if p.suffix.lower() not in {".tif", ".tiff"}:
        return None
    m = read_image_metadata(p)
    return to_um(m["pixel_size"], m["unit"])


def load_display(path: str | Path, channel: int = 0, low: float = 1.0, high: float = 99.8) -> np.ndarray:
    """An image ready to show: TIFF planes auto-contrasted to grey RGB, PNG overlays as they are."""
    p = Path(path)
    if p.suffix.lower() in {".tif", ".tiff"}:
        meta = read_image_metadata(p)
        with tifffile.TiffFile(p) as tif:
            img = _plane(tif.series[0].asarray(), meta["axes"], channel).astype(float)
        lo, hi = np.percentile(img, (low, high))
        g = np.clip((img - lo) / max(hi - lo, 1e-9), 0, 1)
        return np.stack([g] * 3, axis=-1)
    return np.asarray(Image.open(p).convert("RGB"), dtype=float) / 255.0


def _resize(img: np.ndarray, factor: float) -> np.ndarray:
    if abs(factor - 1) < 1e-3:
        return img
    h, w = img.shape[:2]
    im = Image.fromarray((img * 255).astype(np.uint8))
    im = im.resize((max(1, round(w * factor)), max(1, round(h * factor))), Image.LANCZOS)
    return np.asarray(im, dtype=float) / 255.0


def _crop_center(img: np.ndarray, h: int, w: int) -> np.ndarray:
    H, W = img.shape[:2]
    y, x = max(0, (H - h) // 2), max(0, (W - w) // 2)
    return img[y:y + h, x:x + w]


def nice_scale_bar(field_um: float) -> float:
    """A round bar length about a fifth of the field of view."""
    target = field_um / 5
    for v in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000):
        if v >= target * 0.6:
            return float(v)
    return float(10 ** math.floor(math.log10(target)))


def panel_figure(panels: list[Panel], cols: int = 2, width_in: float = 7.0, title: str = "",
                 scale_bar_um: float | None = None, same_scale: bool = False, letters: bool = True,
                 channel: int = 0, fmt: str = "png", dpi: int = 300) -> tuple[bytes, list[str]]:
    """Compose panels into one figure. Returns the file bytes and notes (e.g. panels without a scale)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.patheffects as pe
    import matplotlib.pyplot as plt

    notes: list[str] = []
    imgs, sizes = [], []
    for p in panels:
        imgs.append(load_display(p.path, channel))
        sizes.append(p.pixel_size_um if p.pixel_size_um is not None else pixel_size_um(p.path))
    known = [s for s in sizes if s]
    if same_scale and known:
        target = max(known)  # coarsest sampling: never invent detail
        for i, s in enumerate(sizes):
            if s:
                imgs[i] = _resize(imgs[i], s / target)
                sizes[i] = target
            else:
                notes.append(f"Panel {i + 1} has no calibration, so it is not rescaled.")
        h = min(im.shape[0] for im in imgs)
        w = min(im.shape[1] for im in imgs)
        imgs = [_crop_center(im, h, w) for im in imgs]
    if scale_bar_um is None and known:
        scale_bar_um = nice_scale_bar(min(im.shape[1] * s for im, s in zip(imgs, sizes) if s))

    cols = max(1, min(cols, len(imgs)))
    rows = math.ceil(len(imgs) / cols)
    aspect = max(im.shape[0] / im.shape[1] for im in imgs)
    cap_h = 0.22 if any(p.label for p in panels) else 0.0
    cell_w = width_in / cols
    fig_h = rows * (cell_w * aspect + cap_h) + (0.35 if title else 0.05)
    fig, axes = plt.subplots(rows, cols, figsize=(width_in, fig_h), squeeze=False)
    fig.patch.set_facecolor("white")
    for k, ax in enumerate(axes.flat):
        ax.set_axis_off()
        if k >= len(imgs):
            continue
        im, s = imgs[k], sizes[k]
        ax.imshow(im, interpolation="nearest")
        if letters:
            ax.text(0.03, 0.97, chr(ord("a") + k), transform=ax.transAxes, ha="left", va="top",
                    fontsize=12, fontweight="bold", color="white",
                    path_effects=[pe.withStroke(linewidth=2.5, foreground="black")])
        if panels[k].label:
            ax.set_title(panels[k].label, fontsize=8, color=INK, pad=3)
        if s and scale_bar_um:
            bar_px = scale_bar_um / s
            H, W = im.shape[:2]
            x1, y = W * 0.95, H * 0.93
            ax.plot([x1 - bar_px, x1], [y, y], color="white", linewidth=3, solid_capstyle="butt",
                    path_effects=[pe.withStroke(linewidth=4.5, foreground="black")])
            ax.text(x1 - bar_px / 2, y - H * 0.025, f"{scale_bar_um:g} µm", ha="center", va="bottom", fontsize=7,
                    color="white", path_effects=[pe.withStroke(linewidth=2, foreground="black")])
        elif not s:
            notes.append(f"Panel {chr(ord('a') + k)} has no calibration, so it has no scale bar.")
    if title:
        fig.suptitle(title, fontsize=10, color=INK, y=0.995)
    fig.tight_layout(pad=0.3, h_pad=0.4, w_pad=0.3)
    buf = io.BytesIO()
    fig.savefig(buf, format=fmt, dpi=dpi, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    if scale_bar_um and known:
        notes.insert(0, f"Scale bars: {scale_bar_um:g} µm.")
    return buf.getvalue(), list(dict.fromkeys(notes))


def comparison_chart(images: list[str], adjusted: list[float], as_is: list[float] | None, metric: str,
                     adjusted_label: str, as_is_label: str = "", paper_range: tuple[float, float] | None = None,
                     fmt: str = "png", dpi: int = 300) -> bytes:
    """Per-image dot plot: your results (adjusted), the paper's parameters as-is, the paper's range."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(6.0, 3.0))
    x = np.arange(len(images))
    if paper_range:
        ax.axhspan(*paper_range, color=BAND, zorder=0, linewidth=0)
        ax.text(-0.4, paper_range[1], " paper's range", ha="left", va="top", fontsize=7, color=INK_MUTED)
    ax.scatter(x, adjusted, s=36, color=SERIES[0], edgecolor="white", linewidth=1.5, zorder=4, label=adjusted_label)
    if as_is is not None:
        ax.scatter(x, as_is, s=36, color=SERIES[1], edgecolor="white", linewidth=1.5, zorder=3, label=as_is_label)
    ax.set_xlim(-0.5, len(images) - 0.5)
    ax.set_xticks(x, [Path(i).stem for i in images], fontsize=7, color=INK_MUTED)
    ax.set_ylabel(metric.replace("_", " "), fontsize=8, color=INK_MUTED)
    ax.tick_params(axis="y", labelsize=7, colors=INK_MUTED, length=0)
    ax.tick_params(axis="x", length=0)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.legend(frameon=False, fontsize=7, loc="upper left", bbox_to_anchor=(0, 1.18), ncol=2)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format=fmt, dpi=dpi, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()
