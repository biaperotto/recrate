"""Galaxy workflows from WorkflowHub: read them into a ReCRATE crate, and run them.

A Galaxy Workflow RO-Crate (e.g. from WorkflowHub) describes the workflow, its author and its tests well,
but keeps the method's parameters inside the ``.ga`` file and its example data and expected outputs in
the Planemo test file. ``import_galaxy_crate`` reads them from there and writes a ReCRATE input crate
next to the original, which is left untouched:

* parameters: the ``tool_state`` of each supported tool (e.g. Gaussian size, threshold method);
* example data and expected results: the Planemo test's input image and expected object count;
* the input: a 2D single-channel image, as the workflow's tools require.

A Galaxy workflow outside imaging (climate, genomics, text) is imported too: its inputs from the workflow's own
input steps, its outputs from the declared workflow outputs (compared as files), the parameters from each step's
tool state, and the field from the WorkflowHub record (EDAM topics, tags) when ``workflowhub.json`` sits next to
the crate (sources.fetch_workflowhub writes it). Such a workflow is reported *blocked* until its tools are ported
or a Galaxy server runs it: readiness lists every unported tool.

``GalaxyRunner`` runs the workflow without a Galaxy server. Each supported tool is a port of the Galaxy
imaging tool's own code (BMCV/galaxy-image-analysis) at the version the workflow pins, and steps run in
the workflow's own wiring. Only what the scientific outputs need is computed; steps whose outputs are
only pictures (e.g. the overlay and its contrast-enhanced background) are skipped and listed in the log.
Unsupported tools stop the run with a clear message.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
import tifffile

from . import __version__
from .runners.base import Runner, RunResult, now_iso

GIA = "https://github.com/BMCV/galaxy-image-analysis"


# ---- ported tools ---------------------------------------------------------------------------
# Each takes the step's inputs (by input name) and its tool_state, and returns its outputs.

def _filter(inputs: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    import scipy.ndimage as ndi
    from skimage.morphology import disk

    im = inputs["input"]
    f = state.get("filter") or {}
    kind, size = f.get("filter_type", state.get("filter_type", "gaussian")), float(f.get("size", 1))
    ops: dict[str, Callable] = {
        "gaussian": lambda: ndi.gaussian_filter(im, size),
        "median": lambda: ndi.median_filter(im, footprint=disk(size)),
        "prewitt_h": lambda: ndi.prewitt(im, axis=1), "prewitt_v": lambda: ndi.prewitt(im, axis=0),
        "sobel_h": lambda: ndi.sobel(im, axis=1), "sobel_v": lambda: ndi.sobel(im, axis=0),
    }
    return {"output": ops[kind]()}


def _equalize(inputs: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    from skimage import exposure, util

    f = {"default": exposure.equalize_hist, "clahe": exposure.equalize_adapthist}[state.get("h_type", "default")]
    return {"output": util.img_as_uint(f(inputs["input"]))}


def _threshold(inputs: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    from skimage import filters, util

    img = np.squeeze(inputs["input"])
    th = state.get("th_method") or {}
    method, offset = th.get("method_id", "otsu"), float(th.get("offset", 0) or 0)
    bz = int(th.get("block_size", 5) or 5)
    auto = {
        "manual": lambda: float(th.get("threshold", 0) or 0),
        "otsu": lambda: filters.threshold_otsu(img), "li": lambda: filters.threshold_li(img),
        "yen": lambda: filters.threshold_yen(img), "isodata": lambda: filters.threshold_isodata(img),
        "loc_gaussian": lambda: filters.threshold_local(img, bz, method="gaussian"),
        "loc_median": lambda: filters.threshold_local(img, bz, method="median"),
        "loc_mean": lambda: filters.threshold_local(img, bz, method="mean"),
    }
    res = img > offset + auto[method]()
    if str(state.get("invert_output", False)).lower() == "true":
        res = np.logical_not(res)
    return {"output": util.img_as_ubyte(res)}


def _convert(inputs: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    return {"output": inputs["input_file"]}  # a format conversion: pixel values unchanged


def _binary_to_label(inputs: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    from skimage import measure, util

    mode = (state.get("mode") or {}).get("mode_selector", "cca")
    if mode != "cca":
        raise NotImplementedError(f"ip_binary_to_labelimage mode '{mode}' is not ported (only 'cca').")
    return {"output": util.img_as_uint(measure.label(inputs["input"] > 0))}


def _count(inputs: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    from skimage.measure import regionprops

    return {"output": len(regionprops(inputs["input"]))}


# tool short id -> (function, versions the port follows, source file in the Galaxy tools repository)
TOOLS: dict[str, tuple[Callable, set[str], str]] = {
    "ip_filter_standard": (_filter, {"1.12.0+galaxy1"}, "tools/2d_simple_filter/filter_image.py"),
    "ip_histogram_equalization": (_equalize, {"0.18.1+galaxy0"}, "tools/2d_histogram_equalization/histogram_equalization.py"),
    "ip_threshold": (_threshold, {"0.18.1+galaxy3"}, "tools/2d_auto_threshold/auto_threshold.py"),
    "ip_convertimage": (_convert, {"6.7.0+galaxy3"}, "tools/bfconvert"),
    "ip_binary_to_labelimage": (_binary_to_label, {"0.5+galaxy0"}, "tools/binary2labelimage/binary2label.py"),
    "ip_count_objects": (_count, {"0.0.5-2"}, "tools/count_objects/count_objects.py"),
}
VISUAL_ONLY = {"ip_overlay_images"}  # outputs are pictures for people, not results

# Parameters exposed to ReCRATE: tool -> [(name, path in tool_state, type, description, scaling)]
EXPOSED = {
    "ip_filter_standard": [
        ("filter_type", ("filter", "filter_type"), "Text", "Filter (gaussian, median, sobel, prewitt)", None),
        ("filter_size", ("filter", "size"), "Float", "Filter size in pixels (sigma for gaussian, radius for median)",
         "inverse_pixel_size"),
    ],
    "ip_histogram_equalization": [("equalization", ("h_type",), "Text", "Histogram equalization (default, clahe)", None)],
    "ip_threshold": [
        ("threshold_method", ("th_method", "method_id"), "Text", "Threshold method (otsu, li, yen, isodata, manual...)", None),
        ("threshold_offset", ("th_method", "offset"), "Float", "Offset added to the automatic threshold", None),
    ],
}


# What each exposed parameter means for reuse: its unit, and which check of your data makes it your decision
MEANING = {
    "filter_size": {"unit": "pixel"},
    "filter_type": {"role": "fixed"},
    "equalization": {"role": "fixed"},
    "threshold_method": {"role": "fixed", "sensitive_to": "Bit depth / intensity"},
    "threshold_offset": {"unit": "intensity", "sensitive_to": "Bit depth / intensity"},
}


def tool_key(tool_id: str | None) -> str | None:
    if not tool_id:
        return None
    parts = tool_id.rstrip("/").split("/")
    return parts[-2] if len(parts) >= 2 and "toolshed" in tool_id else parts[-1]


def _nested(v: Any) -> Any:
    """A tool-state value that is JSON in a string (a conditional, a repeat) is read; a text value that merely starts
    with a bracket, such as an annotation, stays as it is."""
    if isinstance(v, str) and v[:1] in "{[":
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _state(step: dict[str, Any]) -> dict[str, Any]:
    st = step.get("tool_state") or {}
    try:
        st = json.loads(st) if isinstance(st, str) else st
    except ValueError:  # not a Galaxy tool state: nothing to read from it
        return {}
    return {k: _nested(v) for k, v in st.items()} if isinstance(st, dict) else {}


def _get(d: dict[str, Any], path: tuple[str, ...]) -> Any:
    for k in path:
        d = d.get(k, {}) if isinstance(d, dict) else {}
    return d if d != {} else None


def _set(d: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    for k in path[:-1]:
        d = d.setdefault(k, {})
    d[path[-1]] = value


def load_workflow(ga: Path) -> dict[str, Any]:
    return json.loads(Path(ga).read_text(encoding="utf-8"))


def result_steps(wf: dict[str, Any]) -> set[str]:
    """Steps the scientific outputs depend on (not those that only make pictures)."""
    steps, need = wf["steps"], set()

    def visit(sid: str) -> None:
        if sid in need:
            return
        need.add(sid)
        for conn in (steps[sid].get("input_connections") or {}).values():
            conn = conn[0] if isinstance(conn, list) else conn
            visit(str(conn["id"]))

    outs = _outputs(wf)
    if outs:
        for sid, _ in outs.values():
            if tool_key(steps[sid].get("tool_id")) not in VISUAL_ONLY:
                visit(sid)
    else:  # nothing declared as an output: every step may feed a result, so every tool is needed
        for sid, s in steps.items():
            if s.get("type") == "tool" and tool_key(s.get("tool_id")) not in VISUAL_ONLY:
                visit(sid)
    return need


def unported_tools(wf: dict[str, Any]) -> list[str]:
    """Tools the scientific outputs need that ReCRATE has not ported: the reason a Galaxy workflow can't run here."""
    needed = result_steps(wf)
    return sorted({f"{tool_key(s.get('tool_id'))} {s.get('tool_version')}" for sid, s in wf["steps"].items()
                   if sid in needed and s["type"] == "tool" and tool_key(s.get("tool_id")) not in TOOLS})


GENERIC_PARAMETER_CAP = 40  # parameters exposed from the tool states of unported tools, by step order
_SKIP_KEYS = {"chromInfo", "dbkey", "__page__", "__rerun_remap_job_id__"}
PARAMETER_TYPES = {"integer": "Integer", "float": "Float", "boolean": "Boolean", "text": "Text", "color": "Text"}


def _scalar(v: Any) -> Any:
    """A tool-state value as a scalar: JSON-encoded strings (``"\"no\""``) decoded; dicts, lists and empty
    strings are not scalars (None)."""
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            pass
    if isinstance(v, bool) or isinstance(v, (int, float)):
        return v
    if isinstance(v, str) and v.strip():
        return v
    return None


def _scalars(state: dict[str, Any], nested: bool = False) -> list[tuple[tuple[str, ...], Any]]:
    """The scalar values of a tool state, one level of conditional or section deep (``filter`` → ``filter_type``).
    Connected inputs, runtime values and Galaxy's own keys are left out."""
    out: list[tuple[tuple[str, ...], Any]] = []
    for k, v in state.items():
        if k.startswith("__") or k in _SKIP_KEYS:
            continue
        if isinstance(v, str) and v[:1] in "{[":
            try:
                v = json.loads(v)
            except ValueError:
                pass
        if isinstance(v, dict):
            if v.get("__class__") in {"ConnectedValue", "RuntimeValue"} or nested:
                continue
            out += [((k, *path), val) for path, val in _scalars(v, nested=True)]
            continue
        if isinstance(v, list):
            continue
        val = _scalar(v)
        if val is not None:
            out.append(((k,), val))
    return out


def _type_of(value: Any) -> str:
    if isinstance(value, bool):
        return "Boolean"
    if isinstance(value, int):
        return "Integer"
    if isinstance(value, float):
        return "Float"
    return "Text"


def _param_name(label: str | None, fallback: str) -> str:
    return re.sub(r"\W+", "_", (label or "").strip().lower()).strip("_") or fallback


def input_parameters(wf: dict[str, Any]) -> list[dict[str, Any]]:
    """The workflow's data inputs (one per input step), as crate parameters: name from the step's label,
    description from its annotation, the Galaxy datatype as the format when it has a media type."""
    out = []
    for sid, step in sorted(wf["steps"].items(), key=lambda kv: int(kv[0])):
        if step.get("type") not in {"data_input", "data_collection_input"}:
            continue
        first = (step.get("inputs") or [{}])[0]
        label = step.get("label") or first.get("name") or f"input_{sid}"
        name = _param_name(label, f"input_{sid}")
        fmt = _state(step).get("format")
        fmt = fmt[0] if isinstance(fmt, list) and fmt else fmt
        desc = step.get("annotation") or first.get("description") or f"Galaxy input '{label}'"
        if fmt and not isinstance(fmt, dict):
            desc += f" (Galaxy datatype: {fmt})"
        p = {"id": f"in-{name}", "name": name, "description": desc,
             "type": "Dataset" if step["type"] == "data_collection_input" else "File"}
        media = GALAXY_FORMATS.get(str(fmt).lower()) if fmt and not isinstance(fmt, dict) else None
        if media:
            p["format"] = media
        out.append(p)
    return out


GALAXY_FORMATS = {"tabular": "text/tab-separated-values", "tsv": "text/tab-separated-values", "csv": "text/csv",
                  "txt": "text/plain", "tiff": "image/tiff", "tif": "image/tiff", "png": "image/png",
                  "jpg": "image/jpeg", "netcdf": "application/x-netcdf", "json": "application/json",
                  "pdf": "application/pdf", "fasta": "fasta", "fastq": "fastq", "fastqsanger": "fastq",
                  "fastqsanger.gz": "fastq", "vcf": "vcf", "bam": "bam", "gff3": "gff3", "bed": "bed"}


def exposed_parameters(wf: dict[str, Any]) -> list[dict[str, Any]]:
    """The parameters that change the results, with their values in the workflow: for ported tools the ones
    EXPOSED lists; for the workflow's own parameter inputs their declared type and default; for any other tool the
    scalar values of its tool state (named ``<tool>_<key>``, as published), up to GENERIC_PARAMETER_CAP."""
    out, seen = [], set()
    needed = result_steps(wf)
    generic = 0
    for sid, step in sorted(wf["steps"].items(), key=lambda kv: int(kv[0])):
        if step.get("type") == "parameter_input":  # a parameter the workflow's authors meant to be set
            st = _state(step)
            name = _param_name(step.get("label"), f"parameter_{sid}")
            n = name if name not in seen else f"{name}_step{sid}"
            seen.add(n)
            out.append({"name": n, "step": sid, "path": ("default",), "default": _scalar(st.get("default")),
                        "type": PARAMETER_TYPES.get(str(st.get("parameter_type", "text")), "Text"), "role": "free",
                        "description": (step.get("annotation") or f"Workflow parameter '{step.get('label') or n}'")
                                       + f". Galaxy input step {sid}."})
            continue
        if sid not in needed or step.get("type") != "tool":
            continue
        key = tool_key(step.get("tool_id"))
        if key in EXPOSED:
            for name, path, typ, desc, scaling in EXPOSED[key]:
                n = name if name not in seen else f"{name}_step{sid}"
                seen.add(n)
                value = _get(_state(step), path)
                if typ == "Float" and value is not None:
                    value = float(value)
                out.append({"name": n, "step": sid, "path": path, "type": typ, "default": value,
                            "description": f"{desc}. Galaxy step {sid}: {step.get('label') or step.get('name')}.",
                            **({"scaling": scaling} if scaling else {})})
            continue
        if key in TOOLS:  # ported, with nothing worth exposing (a format conversion, a count)
            continue
        for path, value in _scalars(_state(step)):
            if generic >= GENERIC_PARAMETER_CAP:
                break
            name = _param_name(f"{key}_{'_'.join(path)}", f"step{sid}_{'_'.join(path)}")
            n = name if name not in seen else f"{name}_step{sid}"
            seen.add(n)
            generic += 1
            out.append({"name": n, "step": sid, "path": path, "type": _type_of(value), "default": value, "role": "fixed",
                        "description": f"'{'.'.join(path)}' of {key} {step.get('tool_version') or ''}".rstrip()
                                       + f". Galaxy step {sid}: {step.get('label') or step.get('name')} (as published)."})
    return out


def imaging_workflow(wf: dict[str, Any]) -> bool:
    """Whether every tool is an imaging tool ReCRATE knows (ported, or another ``ip_*`` imgteam tool): then the
    input is an image and the result an object count, as the ported tools compute."""
    keys = [tool_key(s.get("tool_id")) for s in wf["steps"].values() if s.get("type") == "tool"]
    return bool(keys) and all(k in TOOLS or k in VISUAL_ONLY or str(k).startswith("ip_") for k in keys)


def _hub_metadata(src: Path) -> dict[str, Any]:
    """The WorkflowHub record saved by sources.fetch_workflowhub (``workflowhub.json`` next to, or in, the crate)."""
    for p in (src / "workflowhub.json", src.parent / "workflowhub.json"):
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except ValueError:
                pass
    return {}


def profile_for(wf: dict[str, Any], hub: dict[str, Any]) -> str:
    """The domain profile: bioimaging for an imaging workflow; else read from the record's EDAM topics and tags."""
    if imaging_workflow(wf):
        return "bioimaging"
    words = " ".join([*(t.get("label", "") if isinstance(t, dict) else str(t) for t in hub.get("topic_annotations") or []),
                      *(str(t) for t in hub.get("tags") or [])]).lower()
    if any(w in words for w in ("imaging", "microscopy", "image analysis")):
        return "bioimaging"
    if any(w in words for w in ("time series", "electrophysiology", "eeg", "ecg")):
        return "timeseries"
    return "generic"


def _outputs(wf: dict[str, Any]) -> dict[str, tuple[str, str]]:
    """Workflow output label -> (step id, output name)."""
    out = {}
    for sid, step in wf["steps"].items():
        for o in step.get("workflow_outputs") or []:
            out[o.get("label") or o["output_name"]] = (sid, o["output_name"])
    return out


def run_workflow(wf: dict[str, Any], image: np.ndarray, params: dict[str, Any] | None = None) \
        -> tuple[dict[str, Any], list[str]]:
    """Run the workflow on one image. Returns {output label: value} for the scientific outputs, and a log."""
    steps = wf["steps"]
    states = {sid: _state(s) for sid, s in steps.items()}
    for p in exposed_parameters(wf):
        if params and p["name"] in params:
            _set(states[p["step"]], p["path"], params[p["name"]])
    cache: dict[str, dict[str, Any]] = {}
    log: list[str] = []

    def compute(sid: str) -> dict[str, Any]:
        if sid in cache:
            return cache[sid]
        step = steps[sid]
        if step["type"] in {"data_input", "data_collection_input"}:
            cache[sid] = {"output": image}
            return cache[sid]
        key = tool_key(step.get("tool_id"))
        if key not in TOOLS:
            raise NotImplementedError(f"Galaxy tool '{step.get('tool_id')}' (step {sid}) is not ported to ReCRATE yet.")
        fn, versions, _ = TOOLS[key]
        if step.get("tool_version") not in versions:
            log.append(f"step {sid} {key}: workflow pins {step.get('tool_version')}, port follows {sorted(versions)}")
        ins = {}
        for name, conn in (step.get("input_connections") or {}).items():
            conn = conn[0] if isinstance(conn, list) else conn
            ins[name.split("|")[-1]] = compute(str(conn["id"]))[conn["output_name"]]
        cache[sid] = fn(ins, states[sid])
        log.append(f"step {sid} {step.get('label') or step.get('name')}: {key} {step.get('tool_version')}")
        return cache[sid]

    results = {}
    for label, (sid, oname) in _outputs(wf).items():
        if tool_key(steps[sid].get("tool_id")) in VISUAL_ONLY:
            continue
        results[label] = compute(sid)[oname]
    skipped = [f"{s.get('label') or s.get('name')}" for sid, s in steps.items()
               if sid not in cache and s["type"] == "tool"]
    if skipped:
        log.append("skipped (only feed pictures, not results): " + ", ".join(skipped))
    return results, log


class GalaxyRunner(Runner):
    """Runs a Galaxy imaging workflow (.ga) with the ported tools, one image at a time."""

    name = "galaxy"

    def run(self, workflow: Path, input_dir: Path, output_dir: Path, params: dict[str, Any],
            channel: int = 0, progress=None) -> RunResult:
        import scipy
        import skimage

        from .profiles.bioimaging import ground_truth_count, list_images, read_image_metadata
        from .runners.python_ref import _plane

        wf = load_workflow(workflow)
        output_dir = Path(output_dir)
        masks = output_dir / "masks"
        masks.mkdir(parents=True, exist_ok=True)
        started = now_iso()
        log: list[str] = []
        images = list_images(Path(input_dir))
        truth = {f.name: ground_truth_count(Path(input_dir), f.stem) for f in images}  # when your data brings one
        has_truth = any(v is not None for v in truth.values())
        with (output_dir / "results.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["image", "count", "mean_area_px"] + (["count_true"] if has_truth else []))
            for i, f in enumerate(images):
                if progress:
                    progress(i, len(images), f.name)
                meta = read_image_metadata(f)
                with tifffile.TiffFile(f) as tif:
                    img = _plane(tif.series[0].asarray(), meta["axes"], channel)
                with warnings.catch_warnings():  # img_as_uint precision notes, as in the Galaxy tools
                    warnings.simplefilter("ignore", UserWarning)
                    res, step_log = run_workflow(wf, img, params)
                log = log or step_log
                labels = next((v for v in res.values() if isinstance(v, np.ndarray)), None)
                count = next((v for v in res.values() if isinstance(v, (int, np.integer))), None)
                areas = np.bincount(labels.ravel())[1:] if labels is not None else np.array([])
                areas = areas[areas > 0]
                if labels is not None:
                    tifffile.imwrite(masks / f"{f.stem}_mask.tif", ((labels > 0) * 255).astype(np.uint8))
                w.writerow([f.name, int(count) if count is not None else len(areas),
                            f"{areas.mean():.2f}" if len(areas) else "0"]
                           + ([truth[f.name] if truth[f.name] is not None else ""] if has_truth else []))
        tools = sorted({f"{tool_key(s.get('tool_id'))} {s.get('tool_version')}" for s in wf["steps"].values()
                        if tool_key(s.get("tool_id")) in TOOLS})
        return RunResult(
            results_csv=output_dir / "results.csv",
            masks_dir=masks,
            software=[(f"Galaxy workflow '{wf.get('name')}' (ported by ReCRATE)", f"{wf.get('release', '')} / ReCRATE {__version__}"),
                      ("Galaxy imaging tools, ported", "; ".join(tools)),
                      ("scikit-image", skimage.__version__), ("scipy", scipy.__version__)],
            started=started, ended=now_iso(), params=dict(params), log="\n".join(log),
        )


# ---- importing a WorkflowHub crate --------------------------------------------------------------

def _main_entity(root: dict[str, Any]) -> str:
    m = root.get("mainEntity")
    return str(m.get("@id", "") if isinstance(m, dict) else m or "")


def _root(idx: dict[Any, dict[str, Any]]) -> dict[str, Any]:
    """The root dataset: what the metadata descriptor is ``about``, which is ``./`` in most crates but a URL in
    some (a Galaxy Training Network workflow's crate names its tutorial page)."""
    desc = idx.get("ro-crate-metadata.json", {})
    about = desc.get("about")
    rid = about.get("@id", "") if isinstance(about, dict) else str(about or "")
    return idx.get(rid) or idx.get("./", {})


def is_galaxy_crate(crate_dir: Path) -> bool:
    """A crate whose main workflow is Galaxy: a ``.ga`` file, or one whose programming language is Galaxy."""
    meta = Path(crate_dir) / "ro-crate-metadata.json"
    if not meta.exists():
        return False
    try:
        graph = json.loads(meta.read_text(encoding="utf-8")).get("@graph", [])
    except ValueError:
        return False
    idx = {e.get("@id"): e for e in graph if isinstance(e, dict)}
    main = _main_entity(_root(idx))
    if not main:
        return False
    if main.endswith(".ga"):
        return True
    lang = idx.get(main, {}).get("programmingLanguage")
    lid = str(lang.get("@id", "") if isinstance(lang, dict) else lang or "")
    return "galaxy" in (lid + " " + str(idx.get(lid, {}).get("name", ""))).lower()


def import_galaxy_crate(src: Path, out_dir: Path) -> dict[str, Any]:
    """Write a ReCRATE input crate from a Galaxy Workflow RO-Crate. Returns the description used."""
    try:
        import yaml
    except ImportError as e:  # a dependency since Galaxy support; older installs lack it
        raise RuntimeError('Reading Galaxy tests needs PyYAML: run `pip install -e .` (or `pip install pyyaml`).') from e

    from .draft import build_crate

    src, out_dir = Path(src), Path(out_dir)
    meta = json.loads((src / "ro-crate-metadata.json").read_text(encoding="utf-8"))
    idx = {e["@id"]: e for e in meta["@graph"] if "@id" in e}
    root = _root(idx)
    ga = src / _main_entity(root)
    wf_entity = idx.get(_main_entity(root), {})
    wf = load_workflow(ga)
    hub = _hub_metadata(src)
    imaging = imaging_workflow(wf)
    authors = [idx.get(c["@id"], {}).get("name") or c["@id"] for c in wf_entity.get("creator", [])
               if isinstance(c, dict)]
    authors = [a for a in authors if a] or [c.get("name") for c in wf.get("creator", []) if c.get("name")]
    origin = root.get("isBasedOn") or root.get("identifier") or hub.get("url") or root.get("name") or src.name
    origin = origin.get("@id", "") if isinstance(origin, dict) else str(origin)
    article = next((e["@id"] for e in meta["@graph"] if "ScholarlyArticle" in (e.get("@type") if isinstance(
        e.get("@type"), list) else [e.get("@type")]) and "@id" in e), None)
    citation = root.get("citation")
    citation = citation.get("@id") if isinstance(citation, dict) else citation
    citation = citation or article or (f"https://doi.org/{hub['doi']}" if hub.get("doi") else None)

    # Example data and expected results, from the Planemo test
    tests = next((src / e["@id"] for e in meta["@graph"]
                  if "TestDefinition" in (e.get("@type") if isinstance(e.get("@type"), list) else [e.get("@type")])), None)
    work = out_dir.parent / f".{out_dir.name}-import"
    shutil.rmtree(work, ignore_errors=True)
    (work / "example").mkdir(parents=True)
    expected, tolerance = None, 0.0  # no source unless the published test states how it compares (no verdict)
    tolerances: dict[str, Any] = {}
    case: dict[str, Any] = {}
    if tests and tests.exists():
        case = (yaml.safe_load(tests.read_text(encoding="utf-8")) or [{}])[0]
        job_files = [v["path"] for v in (case.get("job") or {}).values() if isinstance(v, dict) and v.get("path")]
        for p in job_files:
            shutil.copy2(tests.parent / p, work / "example" / Path(p).name)
        if not imaging:  # the test's expected files, compared file by file (sha1), as the test itself does
            rows = []
            for name, o in (case.get("outputs") or {}).items():
                f = tests.parent / str(o.get("path", "")) if isinstance(o, dict) and o.get("path") else None
                if f is not None and f.is_file():
                    rows.append((name, hashlib.sha1(f.read_bytes()).hexdigest(), f.stat().st_size))
            if rows:
                expected = work / "expected.csv"
                expected.write_text("file,sha1,size\n" + "".join(f"{n},{h},{z}\n" for n, h, z in rows), encoding="utf-8")
                tolerances = {"sha1": {"value": 0.0, "source": "test-stated", "file": tests.name,
                                       "note": "the workflow's published test compares these outputs"}}
        count_name, count_out = next(((k, v) for k, v in (case.get("outputs") or {}).items()
                                      if isinstance(v, dict) and str(v.get("path", "")).endswith((".tabular", ".tsv", ".csv"))),
                                     (None, None))
        if imaging and count_out and job_files:
            rows = (tests.parent / count_out["path"]).read_text(encoding="utf-8").split()
            if len(rows) >= 2 and rows[-1].isdigit():
                expected = work / "expected.csv"
                expected.write_text(f"image,count\n{Path(job_files[0]).name},{rows[-1]}\n", encoding="utf-8")
                if count_out.get("compare") == "diff":  # the published test compares the count exactly
                    rule = "compare: diff" + (f", lines_diff: {count_out['lines_diff']}" if "lines_diff" in count_out else "")
                    tolerance = {"value": 0.0, "source": "paper-stated",
                                 "quote": f"{count_name}: {rule}", "note": f"the workflow's published test, {tests.name}"}

    params = exposed_parameters(wf)
    tools = [f"{tool_key(s.get('tool_id'))} {s.get('tool_version')}" for _, s in sorted(wf["steps"].items(),
             key=lambda kv: int(kv[0])) if s["type"] == "tool"]
    wf_name = wf.get("name") or wf_entity.get("name") or src.name
    license_ = root.get("license")
    license_ = license_.get("@id") if isinstance(license_, dict) else license_
    if isinstance(license_, str) and "://" not in license_:
        license_ = f"https://spdx.org/licenses/{license_}"
    counts = any(tool_key(s.get("tool_id")) == "ip_count_objects" for s in wf["steps"].values())
    if imaging:
        data_inputs: list[dict[str, Any]] = [
            {"id": "in-image", "name": "input_image", "type": "File", "format": "image/tiff",
             "description": "Fluorescence image of cell nuclei, a single 2D channel (as the workflow's tools require)",
             "properties": {"channels": {"value": 1}, "dimensions": {"value": "2D"}}}]
        outputs: list[dict[str, Any]] = [{"id": "out-results", "name": "objects_count", "type": "File",
                                          "format": "text/csv", "tolerances": {"count": tolerance}}]
        method_name = "segmentation_and_counting" if counts else _param_name(wf_name, Path(ga).stem)
    else:
        data_inputs = input_parameters(wf)
        outputs = []
        for label, (sid, oname) in _outputs(wf).items():
            step = wf["steps"][sid]
            outputs.append({"id": f"out-{_param_name(label, oname)}", "name": _param_name(label, oname), "type": "File",
                            "description": f"Workflow output '{label}' (step {sid}: {step.get('label') or step.get('name')})"})
        if not outputs:
            outputs = [{"id": "out-results", "name": "results", "type": "File",
                        "description": "What the workflow produces (it declares no named outputs)."}]
        outputs[0]["kind"] = "files"  # compared file by file, as the workflow's test does (model.OUTPUT_KINDS)
        if tolerances:
            outputs[0]["tolerances"] = tolerances
        method_name = _param_name(wf_name, Path(ga).stem)
    desc = {
        "name": wf_name,
        "description": ((wf.get("annotation") or "") + f" Derived by ReCRATE from the Galaxy Workflow RO-Crate "
                        f"{origin} (original left unchanged).").strip(),
        "identifier": f"{origin if '://' in origin else src.resolve().as_uri()}#recrate",
        "citation": citation,
        "license": license_ if isinstance(license_, str) else None,
        "authors": authors, "profile": profile_for(wf, hub), "draft": False,
        "datePublished": str(root.get("datePublished") or "")[:10] or None,
        "methods": [{
            "name": method_name,
            "description": wf.get("annotation") or "",
            "workflow": {"file": str(ga), "language": "Galaxy",
                         "software": [{"name": "Galaxy tool " + t.split()[0], "version": t.split()[1]} for t in tools]},
            "inputs": data_inputs
                      + [{"id": f"in-{p['name']}", "name": p["name"], "type": p["type"], "default": p["default"],
                          "description": p["description"], **({"scaling": p["scaling"]} if p.get("scaling") else {}),
                          **({"role": p["role"]} if p.get("role") else {}), **MEANING.get(p["name"], {})}
                         for p in params],
            "outputs": outputs,
            **({"example_data": str(work / "example")} if any((work / "example").iterdir()) else {}),
            **({"expected": str(expected)} if expected else {}),
        }],
    }
    desc = {k: v for k, v in desc.items() if v is not None}
    build_crate(desc, out_dir)
    # Link the derived crate to the original
    mpath = out_dir / "ro-crate-metadata.json"
    m = json.loads(mpath.read_text(encoding="utf-8"))
    r = next(e for e in m["@graph"] if e.get("@id") == "./")
    r["isBasedOn"] = {"@id": origin if "://" in origin else str(src)}
    r["dateModified"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    # The test's published pictures (e.g. the overlay), kept so a reproduction can be shown next to them
    for name, out in (case.get("outputs") or {}).items():
        p = tests.parent / str(out.get("path", "")) if isinstance(out, dict) else None
        if p is not None and p.suffix.lower() in {".png", ".jpg", ".jpeg"} and p.exists():
            (out_dir / "expected" / "figures").mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, out_dir / "expected" / "figures" / p.name)
            fid = f"expected/figures/{p.name}"
            m["@graph"].append({"@id": fid, "@type": ["File", "ImageObject"], "name": f"Published {name}",
                                "encodingFormat": "image/png" if p.suffix.lower() == ".png" else "image/jpeg",
                                "description": f"Output '{name}' of the workflow's published test ({tests.name})."})
            r.setdefault("hasPart", []).append({"@id": fid})
    mpath.write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")
    shutil.rmtree(work, ignore_errors=True)
    return desc
