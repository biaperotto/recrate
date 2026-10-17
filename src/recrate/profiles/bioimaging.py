"""Bioimaging profile: calibration-aware checks for image analysis methods.

Reads calibration from the images themselves (OME-TIFF metadata, or ImageJ
TIFF tags) and compares it with what the method's crate declares:

* file format
* dimensions (2D / 3D)
* number of channels
* pixel size and unit, consistent across files

When pixel size differs, parameters that declare a ``scaling`` property are
rescaled so they mean the same physical size:

* ``inverse_pixel_area``: a size in pixels^2 (e.g. minimum particle area)
* ``inverse_pixel_size``: a length in pixels (e.g. a filter radius)
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import tifffile

from ..crate import MethodSpec
from .base import FAIL, OK, WARN, Adjustment, CheckItem, CheckReport, DomainProfile

IMAGE_SUFFIXES = {".tif", ".tiff"}
PIXEL_SIZE_REL_TOL = 0.05

_UNIT_ALIASES = {
    "um": "µm", "µm": "µm", "μm": "µm", "micron": "µm", "microns": "µm", "micrometer": "µm",
    "\\u00b5m": "µm", "nm": "nm", "nanometer": "nm", "mm": "mm", "pixel": "pixel", "pixels": "pixel",
}
_TO_UM = {"µm": 1.0, "nm": 1e-3, "mm": 1e3}


def normalise_unit(unit: str | None) -> str | None:
    if unit is None:
        return None
    return _UNIT_ALIASES.get(str(unit).strip().lower(), str(unit).strip())


def to_um(value: float | None, unit: str | None) -> float | None:
    if value is None:
        return None
    factor = _TO_UM.get(normalise_unit(unit) or "")
    return value * factor if factor is not None else None


def list_images(data_path: Path) -> list[Path]:
    data_path = Path(data_path)
    if data_path.is_file():
        return [data_path] if data_path.suffix.lower() in IMAGE_SUFFIXES else []
    return sorted(p for p in data_path.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)


def read_image_metadata(path: Path) -> dict[str, Any]:
    """Pixel size, unit, shape, axes, channels and format of one TIFF."""
    info: dict[str, Any] = {"file": path.name, "format": "TIFF", "pixel_size": None, "unit": None}
    with tifffile.TiffFile(path) as tif:
        series = tif.series[0]
        info["shape"] = tuple(int(s) for s in series.shape)
        info["axes"] = series.axes
        if tif.is_ome and tif.ome_metadata:
            info["format"] = "OME-TIFF"
            px, unit = _ome_pixel_size(tif.ome_metadata)
            info["pixel_size"], info["unit"] = px, unit
        if info["pixel_size"] is None:
            page = tif.pages[0]
            xres = page.tags.get("XResolution")
            ij = tif.imagej_metadata or {}
            if xres is not None:
                num, den = xres.value if isinstance(xres.value, tuple) else (xres.value, 1)
                if num:
                    info["pixel_size"] = float(den) / float(num)
            unit = ij.get("unit")
            if unit is None:
                res_unit = page.tags.get("ResolutionUnit")
                unit = {2: "inch", 3: "cm"}.get(int(res_unit.value) if res_unit else 0)
                if unit in {"inch", "cm"}:
                    info["pixel_size"] = None  # DPI-style resolution, not a microscope calibration
                    unit = None
            info["unit"] = unit
    axes = info["axes"]
    shape = info["shape"]
    info["channels"] = shape[axes.index("C")] if "C" in axes else (shape[axes.index("S")] if "S" in axes else 1)
    z = shape[axes.index("Z")] if "Z" in axes else 1
    info["dimensions"] = "3D" if z > 1 else "2D"
    info["unit"] = normalise_unit(info["unit"])
    return info


def _ome_pixel_size(xml: str) -> tuple[float | None, str | None]:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None, None
    for el in root.iter():
        if re.sub(r"\{.*\}", "", el.tag) == "Pixels":
            px = el.get("PhysicalSizeX")
            unit = el.get("PhysicalSizeXUnit", "µm")
            return (float(px) if px else None), unit
    return None, None


class BioimagingProfile(DomainProfile):
    name = "bioimaging"
    description = "Calibration-aware checks for 2D/3D microscopy image analysis (OME / ImageJ metadata)."

    def inspect(self, data_path: Path) -> dict[str, Any]:
        files = list_images(Path(data_path))
        return {"files": [read_image_metadata(f) for f in files]}

    def check(self, spec: MethodSpec, data_path: Path, params: dict[str, Any] | None = None) -> CheckReport:
        data_path = Path(data_path)
        params = dict(params or spec.default_params())
        report = CheckReport(profile=self.name, data_path=data_path)
        expected = spec.data_input
        files = list_images(data_path)

        if not files:
            report.items.append(CheckItem("Images found", "≥1 TIFF", 0, FAIL, "No .tif/.tiff files in this folder."))
            return report
        metas = [read_image_metadata(f) for f in files]
        report.observed = {"files": metas}
        report.items.append(CheckItem("Images found", "≥1 TIFF", len(files), OK))

        # Format
        exp_fmt = expected.format or "image/tiff"
        formats = sorted({m["format"] for m in metas})
        report.items.append(CheckItem("Format", exp_fmt, ", ".join(formats), OK,
                                      "" if "OME-TIFF" in formats else "OME-TIFF preferred; ImageJ calibration used."))

        # Dimensions
        exp_dims = expected.prop("dimensions")
        dims = sorted({m["dimensions"] for m in metas})
        if exp_dims:
            ok = dims == [exp_dims]
            report.items.append(CheckItem("Dimensions", exp_dims, ", ".join(dims), OK if ok else FAIL,
                                          "" if ok else f"Method expects {exp_dims} images."))

        # Channels
        exp_ch = expected.prop("channels")
        chans = sorted({m["channels"] for m in metas})
        if exp_ch is not None:
            exp_ch = int(exp_ch)
            if chans == [exp_ch]:
                report.items.append(CheckItem("Channels", exp_ch, chans[0], OK))
            elif all(c > exp_ch for c in chans):
                report.items.append(CheckItem("Channels", exp_ch, ", ".join(map(str, chans)), WARN,
                                              "More channels than the method uses: choose which one to analyse."))
            else:
                report.items.append(CheckItem("Channels", exp_ch, ", ".join(map(str, chans)), FAIL,
                                              "Fewer channels than the method needs."))

        # Pixel size
        exp_px_prop = expected.properties.get("pixel size")
        observed_um = [to_um(m["pixel_size"], m["unit"]) for m in metas]
        if exp_px_prop is not None:
            exp_um = to_um(float(exp_px_prop.value), exp_px_prop.unit or "µm")
            exp_txt = f"{exp_px_prop.value} {normalise_unit(exp_px_prop.unit) or 'µm'}"
            if any(v is None for v in observed_um):
                missing = [m["file"] for m, v in zip(metas, observed_um) if v is None]
                report.items.append(CheckItem("Pixel size", exp_txt, "missing", FAIL,
                                              f"No physical calibration in: {', '.join(missing[:3])}. "
                                              "Size-based results cannot be compared; add calibration first."))
            else:
                lo, hi = min(observed_um), max(observed_um)
                if hi - lo > PIXEL_SIZE_REL_TOL * lo:
                    report.items.append(CheckItem("Pixel size", exp_txt, f"{lo:g}–{hi:g} µm", FAIL,
                                                  "Pixel size differs between your files; split them into consistent sets."))
                else:
                    obs = sum(observed_um) / len(observed_um)
                    rel = abs(obs - exp_um) / exp_um
                    if rel <= PIXEL_SIZE_REL_TOL:
                        report.items.append(CheckItem("Pixel size", exp_txt, f"{obs:g} µm", OK))
                    else:
                        adj = self._rescale(spec, params, exp_um, obs)
                        report.adjustments.extend(adj)
                        note = "; ".join(f"{a.parameter} {a.old:g} → {a.new:g}" for a in adj) or \
                               "No scalable parameters declared; results may not be comparable."
                        report.items.append(CheckItem("Pixel size", exp_txt, f"{obs:g} µm", WARN, note))
        return report

    @staticmethod
    def _rescale(spec: MethodSpec, params: dict[str, Any], exp_um: float, obs_um: float) -> list[Adjustment]:
        out = []
        ratio = exp_um / obs_um
        for p in spec.value_inputs:
            scaling = p.prop("scaling")
            old = params.get(p.name)
            if scaling is None or old is None:
                continue
            if scaling == "inverse_pixel_area":
                new = float(old) * ratio ** 2
            elif scaling == "inverse_pixel_size":
                new = float(old) * ratio
            else:
                continue
            new = round(new, 4)
            out.append(Adjustment(
                parameter=p.name, old=old, new=new,
                reason=(f"pixel size {obs_um:g} µm vs {exp_um:g} µm in the paper; "
                        f"{p.name} is in pixels and scales as {scaling.replace('_', ' ')}"),
            ))
        return out
