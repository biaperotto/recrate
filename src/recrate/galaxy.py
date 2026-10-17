"""Galaxy workflows from WorkflowHub: read them into a ReCRATE crate, and run them.

A Galaxy Workflow RO-Crate (e.g. from WorkflowHub) describes the workflow, its author and its tests well,
but keeps the method's parameters inside the ``.ga`` file and its example data and expected outputs in
the Planemo test file. ``import_galaxy_crate`` reads them from there and writes a ReCRATE input crate
next to the original, which is left untouched:

* parameters: the ``tool_state`` of each supported tool (e.g. Gaussian size, threshold method);
* example data and expected results: the Planemo test's input image and expected object count;
* the input: a 2D single-channel image, as the workflow's tools require.

``GalaxyRunner`` runs the workflow without a Galaxy server. Each supported tool is a port of the Galaxy
imaging tool's own code (BMCV/galaxy-image-analysis) at the version the workflow pins, and steps run in
the workflow's own wiring. Only what the scientific outputs need is computed; steps whose outputs are
only pictures (e.g. the overlay and its contrast-enhanced background) are skipped and listed in the log.
Unsupported tools stop the run with a clear message.
"""

from __future__ import annotations

import csv
import json
import shutil
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
import tifffile

from . import __version__
from .runners.base import RunResult, Runner, now_iso

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


def tool_key(tool_id: str | None) -> str | None:
    if not tool_id:
        return None
    parts = tool_id.rstrip("/").split("/")
    return parts[-2] if len(parts) >= 2 and "toolshed" in tool_id else parts[-1]


def _state(step: dict[str, Any]) -> dict[str, Any]:
    st = step.get("tool_state") or {}
    st = json.loads(st) if isinstance(st, str) else st
    return {k: (json.loads(v) if isinstance(v, str) and v[:1] in "{[" else v) for k, v in st.items()}


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

    for sid, _ in _outputs(wf).values():
        if tool_key(steps[sid].get("tool_id")) not in VISUAL_ONLY:
            visit(sid)
    return need


def exposed_parameters(wf: dict[str, Any]) -> list[dict[str, Any]]:
    """The parameters that change the results, with their values in the workflow."""
    out, seen = [], set()
    needed = result_steps(wf)
    for sid, step in sorted(wf["steps"].items(), key=lambda kv: int(kv[0])):
        if sid not in needed:
            continue
        key = tool_key(step.get("tool_id"))
        for name, path, typ, desc, scaling in EXPOSED.get(key, []):
            n = name if name not in seen else f"{name}_step{sid}"
            seen.add(n)
            value = _get(_state(step), path)
            if typ == "Float" and value is not None:
                value = float(value)
            out.append({"name": n, "step": sid, "path": path, "type": typ, "default": value,
                        "description": f"{desc}. Galaxy step {sid}: {step.get('label') or step.get('name')}.",
                        **({"scaling": scaling} if scaling else {})})
    return out


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

        from .profiles.bioimaging import list_images, read_image_metadata
        from .runners.python_ref import _plane

        wf = load_workflow(workflow)
        output_dir = Path(output_dir)
        masks = output_dir / "masks"
        masks.mkdir(parents=True, exist_ok=True)
        started = now_iso()
        log: list[str] = []
        images = list_images(Path(input_dir))
        with (output_dir / "results.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["image", "count", "mean_area_px"])
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
                            f"{areas.mean():.2f}" if len(areas) else "0"])
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

def is_galaxy_crate(crate_dir: Path) -> bool:
    meta = Path(crate_dir) / "ro-crate-metadata.json"
    if not meta.exists():
        return False
    graph = json.loads(meta.read_text(encoding="utf-8"))["@graph"]
    root = next((e for e in graph if e.get("@id") == "./"), {})
    main = (root.get("mainEntity") or {}).get("@id", "")
    return main.endswith(".ga")


def import_galaxy_crate(src: Path, out_dir: Path) -> dict[str, Any]:
    """Write a ReCRATE input crate from a Galaxy Workflow RO-Crate. Returns the description used."""
    try:
        import yaml
    except ImportError as e:  # a dependency since Galaxy support; older installs lack it
        raise RuntimeError('Reading Galaxy tests needs PyYAML: run `pip install -e .` (or `pip install pyyaml`).') from e

    from .draft import build_crate

    src, out_dir = Path(src), Path(out_dir)
    meta = json.loads((src / "ro-crate-metadata.json").read_text(encoding="utf-8"))
    idx = {e["@id"]: e for e in meta["@graph"]}
    root = idx["./"]
    ga = src / root["mainEntity"]["@id"]
    wf_entity = idx.get(root["mainEntity"]["@id"], {})
    wf = load_workflow(ga)
    authors = [idx.get(c["@id"], {}).get("name") or c["@id"] for c in wf_entity.get("creator", [])]
    authors = [a for a in authors if a] or [c.get("name") for c in wf.get("creator", []) if c.get("name")]

    # Example data and expected results, from the Planemo test
    tests = next((src / e["@id"] for e in meta["@graph"]
                  if "TestDefinition" in (e.get("@type") if isinstance(e.get("@type"), list) else [e.get("@type")])), None)
    work = out_dir.parent / f".{out_dir.name}-import"
    shutil.rmtree(work, ignore_errors=True)
    (work / "example").mkdir(parents=True)
    expected = None
    if tests and tests.exists():
        case = (yaml.safe_load(tests.read_text(encoding="utf-8")) or [{}])[0]
        job_files = [v["path"] for v in (case.get("job") or {}).values() if isinstance(v, dict) and v.get("path")]
        for p in job_files:
            shutil.copy2(tests.parent / p, work / "example" / Path(p).name)
        count_out = next((v for v in (case.get("outputs") or {}).values()
                          if isinstance(v, dict) and str(v.get("path", "")).endswith((".tabular", ".tsv", ".csv"))), None)
        if count_out and job_files:
            rows = (tests.parent / count_out["path"]).read_text(encoding="utf-8").split()
            if len(rows) >= 2 and rows[-1].isdigit():
                expected = work / "expected.csv"
                expected.write_text(f"image,count\n{Path(job_files[0]).name},{rows[-1]}\n", encoding="utf-8")

    params = exposed_parameters(wf)
    tools = [f"{tool_key(s.get('tool_id'))} {s.get('tool_version')}" for _, s in sorted(wf["steps"].items(),
             key=lambda kv: int(kv[0])) if s["type"] == "tool"]
    desc = {
        "name": wf.get("name") or wf_entity.get("name") or src.name,
        "description": (wf.get("annotation") or "") + f" Derived by ReCRATE from the Galaxy Workflow RO-Crate "
                       f"{root.get('isBasedOn', '')} (original left unchanged).",
        "identifier": f"{root.get('isBasedOn') or src.resolve().as_uri()}#recrate",
        "citation": root.get("isBasedOn"),
        "license": f"https://spdx.org/licenses/{root['license']}" if isinstance(root.get("license"), str) else None,
        "authors": authors, "profile": "bioimaging", "draft": False,
        "datePublished": str(root.get("datePublished") or "")[:10] or None,
        "methods": [{
            "name": "segmentation_and_counting" if "count" in (wf.get("name") or "").lower() else Path(ga).stem,
            "description": wf.get("annotation") or "",
            "workflow": {"file": str(ga), "language": "Galaxy",
                         "software": [{"name": "Galaxy tool " + t.split()[0], "version": t.split()[1]} for t in tools]},
            "inputs": [{"id": "in-image", "name": "input_image", "type": "File", "format": "image/tiff",
                        "description": "Fluorescence image of cell nuclei, a single 2D channel (as the workflow's tools require)",
                        "properties": {"channels": {"value": 1}, "dimensions": {"value": "2D"}}}]
                      + [{"id": f"in-{p['name']}", "name": p["name"], "type": p["type"], "default": p["default"],
                          "description": p["description"], **({"scaling": p["scaling"]} if p.get("scaling") else {})}
                         for p in params],
            "outputs": [{"id": "out-results", "name": "objects_count", "type": "File", "format": "text/csv",
                         "tolerances": {"count": 0.0}}],
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
    r["isBasedOn"] = {"@id": root.get("isBasedOn") or str(src)}
    r["dateModified"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    mpath.write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")
    shutil.rmtree(work, ignore_errors=True)
    return desc
