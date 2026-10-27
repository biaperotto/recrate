"""Galaxy runner through a server's API (BioBlend): any Galaxy workflow runs, with the server's own tools.

``galaxy.GalaxyRunner`` runs a workflow here through ReCRATE's ports of its tools, so it runs only the workflows
whose tools are ported (the imaging ones). Most Galaxy workflows on WorkflowHub use tools ReCRATE has not ported.
This runner sends the workflow and the data to a Galaxy server instead, as a Galaxy user would, and brings the
outputs back:

1. a history named after the method, so the run is visible on the server (its URL is in the run's log);
2. the ``.ga`` file imported as it is in the crate (same tools, same versions);
3. every file of the data folder uploaded and mapped to the workflow's input steps, by the step's label (the
   file's name or stem, as the Planemo test names them), else in order; a workflow with one data input and several
   files is invoked once per file, so the results have one row per file as the ported runner's do;
4. the method's parameters set on the steps they belong to (``galaxy.exposed_parameters`` says which);
5. the invocation awaited, each output downloaded and named by its workflow output label, as the published test
   names its expected files, so ``file,sha1,size`` rows compare.

``results.csv`` is ``image,count,mean_area_px`` when the workflow counts objects (the count read from its tabular
output, as ``galaxy.import_galaxy_crate`` reads the published test's), else ``file,sha1,size`` of what it produced.

Configuration, from the environment: ``GALAXY_URL`` (default ``https://usegalaxy.eu``), ``GALAXY_API_KEY``
(required: on usegalaxy.eu, User → Preferences → Manage API Key) and ``RECRATE_GALAXY_TIMEOUT`` (seconds to wait
for a run, default 3600). Nothing of the workflow runs on this computer, so no sandbox applies; the run crate
records the server, its version and the history. Nothing is deleted on the server: the history is the record.
"""

from __future__ import annotations

import csv
import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .base import Runner, RunResult, now_iso

DEFAULT_URL = "https://usegalaxy.eu"
HOW_TO = ("set GALAXY_API_KEY (on usegalaxy.eu: User → Preferences → Manage API Key) and, for another server, "
          "GALAXY_URL")
COUNT_FORMATS = {"tabular", "tsv", "csv", "txt"}


def server_url() -> str:
    return os.environ.get("GALAXY_URL", DEFAULT_URL).rstrip("/")


def configured() -> bool:
    """Whether a server and key are set (the key is what a run needs)."""
    return bool(os.environ.get("GALAXY_API_KEY"))


def bioblend_missing() -> str | None:
    """None when BioBlend is installed, else how to install it."""
    try:
        import bioblend  # noqa: F401
    except ImportError:
        return "Install BioBlend (pip install 'recrate[galaxy]')."
    return None


def why_not() -> str | None:
    """None when this runner can run, else what is missing."""
    missing = bioblend_missing()
    if missing:
        return f"Running on a Galaxy server needs BioBlend. {missing}"
    if not configured():
        return f"No Galaxy server configured: {HOW_TO}."
    return None


def connect():
    """A BioBlend GalaxyInstance for the configured server (tests replace this)."""
    from bioblend.galaxy import GalaxyInstance

    return GalaxyInstance(server_url(), key=os.environ.get("GALAXY_API_KEY"))


def timeout() -> float:
    try:
        return float(os.environ.get("RECRATE_GALAXY_TIMEOUT", "3600"))
    except ValueError:
        return 3600.0


# ---- mapping the crate's data and parameters onto the workflow --------------------------------------------

def data_inputs(wf: dict[str, Any]) -> list[tuple[str, str]]:
    """The workflow's data input steps: (step index, label), in order."""
    out = []
    for sid, step in sorted(wf["steps"].items(), key=lambda kv: int(kv[0])):
        if step.get("type") in {"data_input", "data_collection_input"}:
            label = step.get("label") or next((i.get("name") for i in step.get("inputs") or [] if i.get("name")), None)
            out.append((str(sid), str(label or f"input_{sid}")))
    return out


def map_files(inputs: list[tuple[str, str]], files: list[Path]) -> dict[str, Path]:
    """Step index -> file: by the step's label matching the file's name or stem (as a Planemo test names its job
    files), else by order."""
    by_name = {f.name: f for f in files}
    by_stem = {f.stem: f for f in files}
    mapped: dict[str, Path] = {}
    used: set[Path] = set()
    for sid, label in inputs:
        f = by_name.get(label) or by_stem.get(label)
        if f is not None and f not in used:
            mapped[sid] = f
            used.add(f)
    rest = [f for f in files if f not in used]
    for sid, _ in inputs:
        if sid not in mapped and rest:
            mapped[sid] = rest.pop(0)
    return mapped


def parameters_by_step(wf: dict[str, Any], params: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The method's parameter values on the steps they belong to, keyed as Galaxy's API takes them
    (``conditional|name`` for a parameter inside a conditional)."""
    from ..galaxy import exposed_parameters

    out: dict[str, dict[str, Any]] = {}
    for p in exposed_parameters(wf):
        if p["name"] in params and params[p["name"]] is not None:
            out.setdefault(str(p["step"]), {})["|".join(p["path"])] = params[p["name"]]
    return out


def count_from_table(path: Path) -> int | None:
    """The object count a counting tool writes (a header, then the number), as the published test states it."""
    try:
        tokens = path.read_text(encoding="utf-8", errors="replace").split()
    except OSError:
        return None
    return int(tokens[-1]) if len(tokens) >= 2 and tokens[-1].isdigit() else None


def _sha1(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()


class GalaxyApiRunner(Runner):
    """Runs a Galaxy workflow on a Galaxy server through its API and brings the outputs back."""

    name = "galaxy-api"

    def available(self) -> bool:
        return why_not() is None

    def readiness(self) -> str | None:
        return why_not()

    def run(self, workflow: Path, input_dir: Path, output_dir: Path, params: dict[str, Any],
            channel: int = 0, progress=None) -> RunResult:
        from ..galaxy import imaging_workflow, load_workflow, tool_key

        why = why_not()
        if why:
            raise RuntimeError(why)
        wf = load_workflow(Path(workflow))
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        url, wait = server_url(), timeout()
        gi = connect()
        version = str((gi.config.get_version() or {}).get("version_major") or "unknown")
        method = getattr(getattr(self, "spec", None), "name", None) or Path(workflow).stem
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        history = gi.histories.create_history(f"ReCRATE: {method} ({stamp})")
        hid = history["id"]
        history_url = f"{url}/histories/view?id={hid}"
        log = [f"Galaxy server {url} (version {version}), history {history_url}"]
        imported = gi.workflows.import_workflow_dict(wf)
        wid = imported["id"]
        log.append(f"workflow imported as {wid}: {wf.get('name')}")
        inputs = data_inputs(wf)
        files = sorted(f for f in Path(input_dir).iterdir() if f.is_file() and not f.name.startswith("."))
        if inputs and not files:
            raise RuntimeError(f"No input files in {input_dir} for the workflow's inputs "
                               f"{', '.join(label for _, label in inputs)}.")
        batches: list[dict[str, Path]] = ([{inputs[0][0]: f} for f in files] if len(inputs) == 1 and len(files) > 1
                                          else [map_files(inputs, files)])
        by_step = parameters_by_step(wf, params)
        started = now_iso()
        imaging = imaging_workflow(wf)
        rows: list[list[Any]] = []
        file_rows: list[tuple[str, str, int]] = []
        masks = output_dir / "masks"
        for i, batch in enumerate(batches):
            sub = output_dir / files[i].stem if len(batches) > 1 else output_dir
            sub.mkdir(parents=True, exist_ok=True)
            if progress:
                progress(i, len(batches), ", ".join(f.name for f in batch.values()))
            invoke_inputs: dict[str, Any] = {}
            for sid, f in batch.items():
                up = gi.tools.upload_file(str(f), hid, file_name=f.name)
                hda = up["outputs"][0]["id"]
                gi.datasets.wait_for_dataset(hda, maxwait=wait)
                invoke_inputs[sid] = {"src": "hda", "id": hda}
                log.append(f"uploaded {f.name} as {hda} for step {sid}")
            inv = gi.workflows.invoke_workflow(wid, inputs=invoke_inputs, params=by_step or None, history_id=hid,
                                               inputs_by="step_index", allow_tool_state_corrections=True)
            inv = gi.invocations.wait_for_invocation(inv["id"], maxwait=wait)
            inv = gi.invocations.show_invocation(inv["id"])
            log.append(f"invocation {inv['id']}: {inv.get('state', '?')}")
            if by_step:
                log.append("parameters: " + "; ".join(f"step {s}: {v}" for s, v in by_step.items()))
            downloaded: dict[str, Path] = {}
            for label, ref in sorted((inv.get("outputs") or {}).items()):
                did = ref["id"]
                info = gi.datasets.wait_for_dataset(did, maxwait=wait) or {}
                ext = str(info.get("file_ext") or info.get("extension") or "data")
                dest = sub / f"{label}.{ext}"
                gi.datasets.download_dataset(did, file_path=str(dest), use_default_filename=False)
                downloaded[label] = dest
                log.append(f"output {label}: dataset {did} ({ext}, {dest.stat().st_size} bytes)")
            for label, p in downloaded.items():
                name = label if len(batches) == 1 else f"{files[i].stem}/{label}"
                file_rows.append((name, _sha1(p), p.stat().st_size))
            if imaging:
                image = batch[inputs[0][0]].name if inputs else files[i].name
                count = next((c for lbl, p in downloaded.items() if p.suffix.lstrip(".") in COUNT_FORMATS
                              and (c := count_from_table(p)) is not None), None)
                areas = self._areas(downloaded, masks, files[i].stem)
                rows.append([image, count if count is not None else len(areas),
                             f"{sum(areas) / len(areas):.2f}" if areas else "0"])
        results = output_dir / "results.csv"
        with results.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            if imaging and rows:
                w.writerow(["image", "count", "mean_area_px"])
                w.writerows(rows)
            else:
                w.writerow(["file", "sha1", "size"])
                w.writerows(file_rows)
        tools = sorted({(f"Galaxy tool {tool_key(s.get('tool_id'))}", str(s.get("tool_version") or ""))
                        for s in wf["steps"].values() if s.get("type") == "tool"})
        extra = [f for f in sorted(output_dir.rglob("*")) if f.is_file() and f != results
                 and not f.is_relative_to(masks)]
        return RunResult(results_csv=results, masks_dir=masks if masks.exists() else None,
                         software=[(f"Galaxy server {url}", version), *tools], started=started, ended=now_iso(),
                         params=dict(params), log="\n".join(log), extra_outputs=extra,
                         sandbox=f"on the Galaxy server {url} (history {history_url}), not on this computer")

    @staticmethod
    def _areas(downloaded: dict[str, Path], masks: Path, stem: str) -> list[int]:
        """Object areas from a label image the workflow produced, and a mask for the overlays (as the ported
        runner writes them); empty when there is no label image."""
        label = next((p for lbl, p in downloaded.items() if "label" in lbl.lower()
                      and p.suffix.lower() in {".tiff", ".tif"}), None)
        if label is None:
            return []
        try:
            import numpy as np
            import tifffile

            labels = tifffile.imread(label)
        except Exception:  # noqa: BLE001 - not a label image after all: no areas, no mask
            return []
        masks.mkdir(parents=True, exist_ok=True)
        tifffile.imwrite(masks / f"{stem}_mask.tif", ((labels > 0) * 255).astype(np.uint8))
        areas = np.bincount(labels.ravel())[1:]
        return [int(a) for a in areas if a > 0]
