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
from typing import Any

import numpy as np
import tifffile
from PIL import Image

from .profiles.bioimaging import read_image_metadata, to_um
from .runners.python_ref import _plane

# Categorical slots 1 to 8 of the validated palette (assigned in this fixed order, never cycled), neutral ink for
# text and the paper's band.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
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
    im = im.resize((max(1, round(w * factor)), max(1, round(h * factor))), Image.Resampling.LANCZOS)
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


def published_vs_reproduced(comparison, fmt: str = "png", dpi: int = 200) -> bytes:
    """One row per compared value: the published value as a ring, the reproduced one as a dot. Values outside
    tolerance sit on a shaded band and are labelled "outside" (never colour alone). One panel per measure, so
    measures of different scales never share an axis. Plotted only from the comparison (the run's outputs)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    groups: dict[str, list] = {}
    for m in comparison.measures:
        col, _, item = m.measure.partition(" · ")
        groups.setdefault(col, []).append((item or col, m))
    rows = sum(len(v) for v in groups.values())
    fig, axes = plt.subplots(len(groups), 1, figsize=(6.0, 0.9 + 0.32 * rows + 0.45 * len(groups)), squeeze=False,
                             gridspec_kw={"height_ratios": [len(v) for v in groups.values()]})
    for ax, (col, items) in zip(axes[:, 0], groups.items()):
        y = np.arange(len(items))[::-1]
        for yi, (label, m) in zip(y, items):
            if not m.within:
                ax.axhspan(yi - 0.45, yi + 0.45, color=BAND, zorder=0, linewidth=0)
            lo, hi = sorted((m.reported, m.reproduced))
            ax.plot([lo, hi], [yi, yi], color=GRID, linewidth=2, zorder=1)
            ax.scatter([m.reported], [yi], s=70, facecolor="white", edgecolor=SERIES[0], linewidth=2, zorder=3,
                       label="published" if yi == y[0] else None)
            ax.scatter([m.reproduced], [yi], s=36, color=SERIES[1], edgecolor="white", linewidth=1.5, zorder=4,
                       label="reproduced" if yi == y[0] else None)
        ax.set_yticks(y, [f"{lbl}{'  (outside)' if not m.within else ''}" for lbl, m in items], fontsize=7)
        for t, (_, m) in zip(ax.get_yticklabels(), items):
            t.set_color(INK)
            t.set_fontweight("bold" if not m.within else "normal")
        ax.set_ylim(-0.6, len(items) - 0.4)
        ax.set_title(col.replace("_", " ") + f"  ·  tolerance {items[0][1].tolerance_label}", fontsize=8,
                     color=INK_MUTED, loc="left")
        ax.tick_params(axis="x", labelsize=7, colors=INK_MUTED, length=0)
        ax.tick_params(axis="y", length=0)
        ax.grid(axis="x", color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(GRID)
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.suptitle(f"Published vs reproduced: {comparison.count} within tolerance", fontsize=9, color=INK, x=0.02,
                 ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    fig.legend(handles, labels, frameon=False, fontsize=7, loc="upper right", ncol=2, bbox_to_anchor=(0.99, 1.0))
    buf = io.BytesIO()
    fig.savefig(buf, format=fmt, dpi=dpi, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def comparison_from_run(run_dir: str | Path):
    """A run's comparison, read back from the table it recorded (``comparison.csv``), so a figure plots exactly
    what the run crate says."""
    import csv

    from .compare import Comparison, MeasureComparison

    path = Path(run_dir) / "comparison.csv"
    if not path.exists():
        return None
    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    return Comparison(measures=[MeasureComparison(r["value"], float(r["published"]), float(r["reproduced"]),
                                                  float(r["difference"]), float(r["tolerance"]),
                                                  r["kind"] == "absolute", r["tolerance_source"] or None)
                                for r in rows])


PLOT_KINDS = {
    "bar": "one bar per category (x) with the value (y); grouped bars with group; repeated categories are averaged",
    "scatter": "y against x, both numeric; coloured by group",
    "line": "y along x (sorted), one line per group",
    "box": "the distribution of y per category (x)",
    "hist": "the distribution of y; one histogram per group, overlaid",
}
MAX_GROUPS = len(SERIES)


def read_table(path: str | Path) -> list[dict[str, Any]]:
    """A CSV as rows; cells that read as numbers become floats."""
    import csv

    with Path(path).open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        for k, v in r.items():
            if isinstance(v, str):
                try:
                    r[k] = float(v.replace(",", "")) if v.strip() not in {"", "NA", "nan", "None"} else None
                except ValueError:
                    pass
    return rows


def _style(ax, xlabel: str, ylabel: str) -> None:
    ax.set_xlabel(xlabel.replace("_", " "), fontsize=8, color=INK_MUTED)
    ax.set_ylabel(ylabel.replace("_", " "), fontsize=8, color=INK_MUTED)
    ax.tick_params(axis="both", labelsize=7, colors=INK_MUTED, length=0)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)


def _numbers(rows: list[dict[str, Any]], col: str) -> list[float]:
    vals = [r.get(col) for r in rows]
    bad = [v for v in vals if v is not None and not isinstance(v, (int, float))]
    if bad:
        raise ValueError(f"Column {col!r} is not numeric (e.g. {bad[0]!r}); choose a numeric column for y.")
    return [float(v) if v is not None else math.nan for v in vals]


def plot_table(rows: list[dict[str, Any]], kind: str, x: str | None = None, y: str | None = None,
               group: str | None = None, title: str = "", fmt: str = "png", dpi: int = 200) -> tuple[bytes, str]:
    """A plot of a table's columns, chosen by name: the model (or a person) picks what to plot, the code draws it
    from the file, so every number on the figure is in the table. Returns the image and a note of what was drawn
    (rows, averaging)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if kind not in PLOT_KINDS:
        raise ValueError(f"Unknown plot kind {kind!r}; available: {', '.join(PLOT_KINDS)}.")
    if not rows:
        raise ValueError("The table has no rows to plot.")
    cols = list(rows[0])
    for name, c in (("x", x), ("y", y), ("group", group)):
        if c and c not in cols:
            raise ValueError(f"No column {c!r} for {name}; the table has: {', '.join(cols)}.")
    if not y:
        raise ValueError("Say which column to plot (y).")
    if kind != "hist" and not x:
        raise ValueError(f"A {kind} plot needs an x column too.")
    xcol, gcol = x or "", group or ""  # checked above; plain strings from here on
    groups = list(dict.fromkeys(str(r.get(gcol)) for r in rows)) if group else [None]
    if len(groups) > MAX_GROUPS:
        raise ValueError(f"{len(groups)} groups in {group!r}: more than {MAX_GROUPS} can't be told apart by colour; "
                         "filter the table or plot the groups separately.")
    colour = {g: SERIES[i] for i, g in enumerate(groups)}
    note = f"{len(rows)} rows"
    fig, ax = plt.subplots(figsize=(6.0, 3.4))
    yv = _numbers(rows, y)
    if kind == "bar":
        cats = list(dict.fromkeys(str(r.get(xcol)) for r in rows))
        width = 0.8 / len(groups)
        averaged = False
        for gi, g in enumerate(groups):
            heights = []
            for c in cats:
                vals = [v for r, v in zip(rows, yv) if str(r.get(xcol)) == c and (g is None or str(r.get(gcol)) == g)
                        and not math.isnan(v)]
                averaged |= len(vals) > 1
                heights.append(sum(vals) / len(vals) if vals else math.nan)
            pos = [i + (gi - (len(groups) - 1) / 2) * width for i in range(len(cats))]
            ax.bar(pos, heights, width=width * 0.92, color=colour[g], label=g, linewidth=0)
        ax.set_xticks(range(len(cats)), cats, fontsize=7, color=INK_MUTED)
        if averaged:
            note += f"; bars are means over repeated {x} values"
    elif kind == "box":
        cats = list(dict.fromkeys(str(r.get(xcol)) for r in rows))
        data = [[v for r, v in zip(rows, yv) if str(r.get(xcol)) == c and not math.isnan(v)] for c in cats]
        bp = ax.boxplot(data, patch_artist=True, widths=0.5, medianprops={"color": INK, "linewidth": 1.5},
                        whiskerprops={"color": INK_MUTED}, capprops={"color": INK_MUTED},
                        flierprops={"marker": "o", "markersize": 3, "markerfacecolor": INK_MUTED, "markeredgecolor": "none"})
        for patch in bp["boxes"]:
            patch.set(facecolor=SERIES[0], alpha=0.35, edgecolor=SERIES[0], linewidth=1)
        ax.set_xticks(range(1, len(cats) + 1), cats, fontsize=7, color=INK_MUTED)
        note += "; boxes: median and quartiles"
    elif kind == "hist":
        for g in groups:
            vals = [v for r, v in zip(rows, yv) if (g is None or str(r.get(gcol)) == g) and not math.isnan(v)]
            ax.hist(vals, bins="auto", color=colour[g], alpha=0.7 if len(groups) > 1 else 1.0, label=g, linewidth=0)
        ax.set_ylabel("count")
    else:  # scatter, line: x numeric
        xv = _numbers(rows, xcol)
        for g in groups:
            pts = sorted((xi, yi) for r, xi, yi in zip(rows, xv, yv) if (g is None or str(r.get(gcol)) == g)
                         and not (math.isnan(xi) or math.isnan(yi)))
            if not pts:
                continue
            if kind == "line":
                ax.plot([p[0] for p in pts], [p[1] for p in pts], color=colour[g], linewidth=2, marker="o",
                        markersize=4, markeredgecolor="white", markeredgewidth=1, label=g)
            else:
                ax.scatter([p[0] for p in pts], [p[1] for p in pts], s=36, color=colour[g], edgecolor="white",
                           linewidth=1.5, label=g, zorder=3)
    _style(ax, x or "", "count" if kind == "hist" else y)
    if len(groups) > 1 and kind != "box":  # a box plot shows one distribution per category; group is not drawn
        ax.legend(frameon=False, fontsize=7, title=gcol.replace("_", " "), title_fontsize=7, loc="best")
    elif kind == "box" and group:
        note += f"; group {group!r} is not drawn by a box plot (one box per {x})"
    if title:
        ax.set_title(title, fontsize=9, color=INK, loc="left")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format=fmt, dpi=dpi, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue(), note


def _slug(s: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "value"


def make_run_figure(run_dir: str | Path, kind: str = "published_vs_reproduced",
                    spec: dict[str, Any] | None = None) -> Path:
    """Make a figure from a run's outputs and record it in the run crate. Returns the image path.

    ``kind`` is ``published_vs_reproduced`` (from the run's recorded comparison) or one of PLOT_KINDS, drawn from a
    table named by ``spec``: ``{"source": "results" | "comparison" | <CSV path>, "x": ..., "y": ..., "group": ...,
    "title": ...}``. A table outside the run crate (the user's data, one of the paper's tables) is copied into it,
    so the record holds exactly what was plotted; the spec is recorded with the figure.
    """
    from .runcrate import add_figure

    run_dir = Path(run_dir)
    if kind == "published_vs_reproduced":
        comp = comparison_from_run(run_dir)
        if comp is None or not comp.measures:
            raise ValueError("This run has no comparison with published results to plot.")
        return add_figure(run_dir, published_vs_reproduced(comp), "published_vs_reproduced.png",
                          "Published vs reproduced",
                          f"Each published value (ring) and the reproduced one (dot); {comp.count} within tolerance. "
                          "Plotted by ReCRATE from this run's outputs.",
                          made_from=["comparison.csv"])
    if kind not in PLOT_KINDS:
        raise ValueError(f"Unknown figure kind {kind!r}; available: published_vs_reproduced, {', '.join(PLOT_KINDS)}.")
    spec = dict(spec or {})
    source = str(spec.get("source") or "results")
    if source == "results":
        src, made_from = run_dir / "outputs" / "results.csv", "outputs/results.csv"
    elif source == "comparison":
        src, made_from = run_dir / "comparison.csv", "comparison.csv"
    else:
        src = Path(source).expanduser()
        if not src.is_absolute():
            src = run_dir / src
        if not src.exists():
            raise ValueError(f"No table at {source}.")
        if src.resolve().is_relative_to(run_dir.resolve()):
            made_from = src.resolve().relative_to(run_dir.resolve()).as_posix()
        else:  # a table from outside the run: kept with the figure, so the record holds what was plotted
            import shutil

            made_from = f"outputs/figures/data/{src.name}"
            (run_dir / "outputs" / "figures" / "data").mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, run_dir / made_from)
    if not src.exists():
        raise ValueError(f"This run has no {made_from} to plot.")
    x, y, group, title = spec.get("x") or None, spec.get("y") or None, spec.get("group") or None, spec.get("title") or ""
    data, note = plot_table(read_table(src), kind, x, y, group, title)
    filename = f"{kind}-{_slug(y or '')}" + (f"-by-{_slug(x)}" if x else "") + (f"-{_slug(group)}" if group else "") + ".png"
    what = f"{kind} of {y}" + (f" by {x}" if x else "") + (f", grouped by {group}" if group else "")
    spec = {"kind": kind, "source": made_from, "x": x, "y": y, "group": group, "title": title}
    return add_figure(run_dir, data, filename, title or what,
                      f"Plotted by ReCRATE from {made_from}: {what} ({note}). The model chose what to plot; the "
                      "code drew it from the file.", made_from=[made_from], spec=spec)
