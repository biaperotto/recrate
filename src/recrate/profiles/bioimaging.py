"""Bioimaging profile: calibration-aware checks for image analysis methods.

Reads calibration from the images themselves (OME-TIFF metadata, or ImageJ
TIFF tags) and compares it with what the method's crate declares:

* file format
* dimensions (2D / 3D)
* number of channels
* pixel size and unit, consistent across files
* bit depth and intensity range, against the paper's own example images
* after the run, object size against the paper's run, converted to the paper's pixels with the calibrations or your
  stated pixel ratio

When pixel size differs, parameters that declare a ``scaling`` property are
rescaled so they mean the same physical size (when the research states no pixel size, the user can state
how theirs compares, ``pixel_ratio``):

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
FAR_DENSITY = 3.0  # objects per area this many times the paper's (or less) is flagged
CALIBRATION_FILE = "recrate-calibration.json"  # written when a person supplies a missing calibration


def set_calibration(src: Path, dest: Path, pixel_size: float, unit: str = "µm", who: str = "anonymous",
                    source: str = "") -> list[str]:
    """Give uncalibrated TIFFs a pixel size a person supplied, in ``dest`` (may equal ``src``).

    Images that already carry a calibration are copied unchanged. The calibration is written into
    each file (ImageJ resolution tags, read by Fiji and Python alike) and recorded in
    ``recrate-calibration.json`` next to them, which run crates keep as an input. Returns the files
    that were calibrated.
    """
    import json
    import shutil
    from datetime import datetime, timezone

    src, dest = Path(src), Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    unit = normalise_unit(unit) or "µm"
    changed = []
    for f in list_images(src):
        out = dest / f.name
        meta = read_image_metadata(f)
        if meta["pixel_size"] is not None and to_um(meta["pixel_size"], meta["unit"]) is not None:
            if out.resolve() != f.resolve():
                shutil.copy2(f, out)
            continue
        with tifffile.TiffFile(f) as tif:
            arr = tif.series[0].asarray()
            axes = tif.series[0].axes
        ij_axes = {"YX": "YX", "CYX": "CYX", "ZYX": "ZYX", "ZCYX": "ZCYX", "TZCYX": "TZCYX"}.get(axes)
        tifffile.imwrite(out, arr, imagej=True, resolution=(1 / pixel_size, 1 / pixel_size),
                         metadata={"unit": "micron" if unit == "µm" else unit, **({"axes": ij_axes} if ij_axes else {})})
        changed.append(f.name)
    record = {"pixel_size": pixel_size, "unit": unit, "provided_by": who, "source": source,
              "files": changed, "when": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
              "note": "Calibration supplied by a person because the files had none."}
    if changed:
        (dest / CALIBRATION_FILE).write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    return changed


def user_calibration(data_path: Path) -> dict[str, Any] | None:
    import json

    p = Path(data_path) / CALIBRATION_FILE
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

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

    def check(self, spec: MethodSpec, data_path: Path, params: dict[str, Any] | None = None,
              pixel_ratio: float | None = None) -> CheckReport:
        """``pixel_ratio``: your pixel size divided by the research's, stated by the user, for research that
        states no pixel size (so the calibrations can't be compared)."""
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
        elif pixel_ratio is not None or data_path.resolve() != (spec.example_data or Path("/-")).resolve():
            scaled = [p.name for p in spec.value_inputs if p.prop("scaling")]
            if scaled and pixel_ratio is not None and abs(float(pixel_ratio) - 1) <= PIXEL_SIZE_REL_TOL:
                report.items.append(CheckItem("Pixel size", "not stated", "same as the research's (stated by you)", OK))
            elif scaled and pixel_ratio:
                adj = self._rescale(spec, params, 1.0, float(pixel_ratio))
                for a in adj:
                    a.reason = (f"your pixels are {float(pixel_ratio):g}× the research's (stated by you; the research "
                                f"states no pixel size); {a.parameter} is in pixels")
                report.adjustments.extend(adj)
                report.items.append(CheckItem("Pixel size", "not stated", f"{float(pixel_ratio):g}× the research's",
                                              WARN, "; ".join(f"{a.parameter} {a.old:g} → {a.new:g}" for a in adj)))
            elif scaled:
                report.items.append(CheckItem(
                    "Pixel size", "not stated", "not compared", WARN,
                    f"{', '.join(scaled)} {'is' if len(scaled) == 1 else 'are'} in pixels and the research states no "
                    "pixel size: if your pixels differ in size from the research's, give the ratio (pixel ratio) "
                    "so it is rescaled; otherwise results may not be comparable."))
        self._intensity(spec, files, data_path, report)
        cal = user_calibration(data_path) if data_path.is_dir() else None
        if cal:
            item = next((i for i in report.items if i.name == "Pixel size"), None)
            if item is not None:
                src = f" ({cal['source']})" if cal.get("source") else ""
                item.note = (item.note + "; " if item.note else "") + \
                    f"user-provided by {cal['provided_by']}{src} for {len(cal['files'])} file(s)"
        return report

    def diagnose_inputs(self, spec: MethodSpec) -> list[str]:
        d = spec.data_input
        scaled = [p.name for p in spec.value_inputs if p.prop("scaling")]
        if scaled and "pixel size" not in d.properties:
            return [f"The research states no pixel size, so {', '.join(scaled)} (in pixels) can only be rescaled for "
                    "other data when you state how your pixel size compares."]
        return []

    @staticmethod
    def _intensity(spec: MethodSpec, files: list[Path], data_path: Path, report: CheckReport) -> None:
        """Bit depth and intensity range against the paper's example images (first few files of each)."""
        import numpy as np

        if not spec.example_data or not spec.example_data.exists() or \
                data_path.resolve() == spec.example_data.resolve():
            return

        def stats(fs: list[Path]) -> tuple[set[str], float]:
            dtypes, spans = set(), []
            for f in fs[:3]:
                a = tifffile.imread(f)
                dtypes.add(str(a.dtype))
                lo, hi = np.percentile(a, (1, 99.8))
                spans.append(float(hi - lo))
            return dtypes, float(np.median(spans)) if spans else 0.0

        paper = list_images(spec.example_data)
        if not paper:
            return
        (pd, ps), (md, ms) = stats(paper), stats(files)
        exp, obs = f"{', '.join(sorted(pd))}, range {ps:.4g}", f"{', '.join(sorted(md))}, range {ms:.4g}"
        if pd != md:
            report.items.append(CheckItem("Bit depth / intensity", exp, obs, WARN,
                                          "Different bit depth from the paper's images: intensity-based settings "
                                          "(thresholds, offsets) may not mean the same."))
        elif ps and ms and not (0.25 <= ms / ps <= 4):
            report.items.append(CheckItem("Bit depth / intensity", exp, obs, WARN,
                                          f"Intensity range {ms / ps:.2g}× the paper's images: check exposure and "
                                          "any fixed thresholds."))
        else:
            report.items.append(CheckItem("Bit depth / intensity", exp, obs, OK))

    def check_results(self, spec: MethodSpec, results_csv: Path, report: CheckReport,
                      reference_csv: Path | None = None, pixel_ratio: float | None = None) -> None:
        from .base import _means

        mine, ref = _means(results_csv), _means(reference_csv)
        paper = _means(spec.expected_results)
        counts = {c for c in mine if "count" in c and c in paper and paper[c]}
        super().check_results(spec, results_csv, report, reference_csv, pixel_ratio, skip=counts)
        ratio = self._pixel_ratio(spec, report, pixel_ratio)
        paper_imgs = list_images(spec.example_data) if spec.example_data and spec.example_data.exists() else []
        mine_px = [m["shape"][-1] * m["shape"][-2] for m in report.observed.get("files", [])]
        if counts and paper_imgs and mine_px:  # a field's size changes its count: compare objects per area
            paper_px = [read_image_metadata(f)["shape"][-1] * read_image_metadata(f)["shape"][-2] for f in paper_imgs]
            area = (sum(mine_px) / len(mine_px)) * ((ratio or 1) ** 2)  # in the paper's pixels
            for c in sorted(counts):
                d_paper = paper[c] / (sum(paper_px) / len(paper_px)) * 1e4
                d_mine = mine[c] / area * 1e4
                r = d_mine / d_paper if d_paper else 0
                unit = "per 10⁴ paper px²" + ("" if ratio else " (pixel sizes unknown)")
                status = OK if 1 / FAR_DENSITY <= r <= FAR_DENSITY else WARN
                report.items.append(CheckItem(f"Objects per area ({c})", f"{d_paper:.3g} {unit}", f"{d_mine:.3g} {unit}",
                                              status, "" if status == OK else
                                              f"{r:.2g}× the paper's density: a real difference in your samples, or a "
                                              "sign the segmentation does not fit them; look at the overlays."))
        col = next((c for c in mine if "area" in c and c in ref and ref[c]), None)
        if col is None:
            return
        in_px = col.endswith("_px")
        ratio = ratio if in_px else None
        size = mine[col] * (ratio ** 2 if ratio else 1)
        unit = "paper's px²" if in_px else "as recorded"
        r = size / ref[col]
        obs = f"{size:.4g} {unit}" + ("" if ratio or not in_px else " (pixel sizes unknown: compared in pixels)")
        status = OK if 0.5 <= r <= 2 else WARN
        report.items.append(CheckItem("Object size", f"{ref[col]:.4g} {unit} (the paper's run)", obs, status,
                                      "" if status == OK else f"Objects are {r:.2g}× the size found in the paper's "
                                      "data: check the segmentation, or whether a size parameter needs rescaling."))

    @staticmethod
    def _pixel_ratio(spec: MethodSpec, report: CheckReport, pixel_ratio: float | None) -> float | None:
        """Your pixel size / the paper's: from both calibrations, else as you stated it, else unknown."""
        exp_px = spec.data_input.properties.get("pixel size")
        observed = [to_um(m["pixel_size"], m["unit"]) for m in report.observed.get("files", [])]
        if exp_px is not None and observed and None not in observed:
            return (sum(observed) / len(observed)) / to_um(float(exp_px.value), exp_px.unit or "µm")
        return float(pixel_ratio) if pixel_ratio else None

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
