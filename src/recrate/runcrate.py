"""Write every run as a Workflow Run RO-Crate.

Profiles followed (https://www.researchobject.org/workflow-run-crate/):
Process Run Crate 0.5, Workflow Run Crate 0.5 and Workflow RO-Crate 1.0, so the
run crates are readable by other tools that emit the same profiles.

A run crate records:
* the workflow that ran (copied) and the software versions used
* inputs (copied when small, otherwise referenced) with SHA-256 checksums
* parameter values actually used, including adjustments and why
* outputs (results table, masks, overlays)
* the input compatibility check (domain profile) as an AssessAction
* in reproduce mode, the comparison with the paper's reported results (each tolerance with its source)
  and the verdict ladder (model.py)
* figures, each made by code from the run's outputs, with the action that made it
* for a follow-up check, which run it follows up and what it changed
* optionally, a person's visual approval of the overlays, or review of the conclusion
* ``isBasedOn`` the paper's crate, so runs can be collected into the paper's
  reproducibility record
"""

from __future__ import annotations

import csv
import hashlib
import html
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .compare import Comparison
from .crate import MethodSpec
from .profiles.base import CheckReport
from .runners.base import RunResult

RO_CRATE = "https://w3id.org/ro/crate/1.1"
PROFILES = {
    "process": "https://w3id.org/ro/wfrun/process/0.5",
    "workflow": "https://w3id.org/ro/wfrun/workflow/0.5",
    "wroc": "https://w3id.org/workflowhub/workflow-ro-crate/1.0",
}
COPY_INPUTS_LIMIT = 20 * 1024 * 1024  # copy inputs into the run crate below 20 MB


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "x"


_FAVICON = __import__("base64").b64encode((Path(__file__).parent / "favicon.svg").read_bytes()).decode() \
    if (Path(__file__).parent / "favicon.svg").exists() else ""
_MEDIA = {".png": "image/png", ".svg": "image/svg+xml", ".pdf": "application/pdf", ".jpg": "image/jpeg"}


def reuse_summary(spec: MethodSpec, files: list[Path], results_csv: Path, adjust: dict[str, Any],
                  param_notes: dict[str, str] | None) -> str:
    """One line for a reuse run: inputs, the main result per input (or overall), and every parameter change."""
    from .compare import _num, read_results

    rows = read_results(results_csv) if results_csv.exists() else []
    main = ""
    if rows:
        key = next(iter(rows[0]))
        col = next((c for c in rows[0] if c != key and _num(rows[0][c]) is not None), None)
        if col:
            vals = [_num(r[col]) for r in rows if _num(r[col]) is not None]
            if len(rows) <= 4:
                main = "; ".join(f"{r[key]}: {col} {r[col]}" for r in rows)
            elif vals:
                main = f"{col} {min(vals):g} to {max(vals):g} (mean {sum(vals) / len(vals):.4g}) over {len(rows)} rows"
    changes = [f"{a.parameter} {a.old} → {a.new} ({a.reason})" for a in adjust.values()] + \
              [f"{k}: {v}" for k, v in (param_notes or {}).items()]
    what = (f"on {len(files)} input file(s)" if spec.needs_data else "with the parameter values below")
    return (f"Reuse of {spec.method_name or 'the method'} ({spec.name}) {what}."
            + (f" Results: {main}." if main else "")
            + (" Parameter changes: " + "; ".join(changes) + "." if changes else " Parameters as published."))


def _person(name: str) -> dict[str, Any]:
    return {"@id": f"#person-{_slug(name)}", "@type": "Person", "name": name}


def write_run_crate(
    out_dir: Path,
    spec: MethodSpec,
    mode: str,
    input_dir: Path,
    result: RunResult,
    check: CheckReport | None,
    comparison: Comparison | None,
    agent: str = "anonymous",
    overlays: list[Path] | None = None,
    param_notes: dict[str, str] | None = None,
    ladder: dict[str, dict[str, str]] | None = None,
    figures: list[Path] | None = None,
    followup: dict[str, Any] | None = None,
    purpose: str | None = None,
) -> Path:
    """Create a run crate folder from a finished run. Returns its path."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    graph: list[dict[str, Any]] = []
    has_part: list[dict[str, str]] = []

    def add(entity: dict[str, Any], part: bool = False) -> dict[str, str]:
        graph.append(entity)
        ref = {"@id": entity["@id"]}
        if part:
            has_part.append(ref)
        return ref

    # Workflow (copied) and the software it ran with
    wf_id = f"workflow/{spec.workflow_path.name}" if spec.workflow_path else "workflow/unknown"
    if spec.workflow_path and spec.workflow_path.exists():
        (out_dir / "workflow").mkdir(exist_ok=True)
        shutil.copy2(spec.workflow_path, out_dir / wf_id)
    software_refs = []
    for i, (name, version) in enumerate(result.software):
        software_refs.append(add({"@id": f"#software-{i}", "@type": "SoftwareApplication",
                                  "name": name, "softwareVersion": version}))
    recrate_ref = add({"@id": "#recrate", "@type": "SoftwareApplication", "name": "ReCRATE",
                       "softwareVersion": __version__, "url": "https://github.com/recrate/recrate"})
    lang_ref = add({"@id": "#language", "@type": "ComputerLanguage",
                    "name": spec.workflow_language or "unknown"})

    params_by_name = {p.name: p for p in spec.inputs}
    formal: list[dict[str, Any]] = []
    for p in spec.inputs + spec.outputs:
        e = {"@id": p.id, "@type": "FormalParameter", "name": p.name,
             "conformsTo": {"@id": "https://bioschemas.org/profiles/FormalParameter/1.0-RELEASE"}}
        if p.type:
            e["additionalType"] = p.type
        if p.format:
            e["encodingFormat"] = p.format
        if p.default is not None:
            e["defaultValue"] = str(p.default)
        if p.description:
            e["description"] = p.description
        formal.append(e)
    graph.extend(formal)
    add({
        "@id": wf_id,
        "@type": ["File", "SoftwareSourceCode", "ComputationalWorkflow"],
        "name": spec.workflow_path.stem if spec.workflow_path else "workflow",
        "programmingLanguage": lang_ref,
        "softwareRequirements": software_refs,
        "input": [{"@id": p.id} for p in spec.inputs],
        "output": [{"@id": p.id} for p in spec.outputs],
        "isBasedOn": {"@id": spec.identifier},
    }, part=True)

    # Inputs
    files = sorted(p for p in Path(input_dir).iterdir() if p.is_file())
    copy_inputs = sum(f.stat().st_size for f in files) <= COPY_INPUTS_LIMIT
    data_param = spec.data_input.id
    input_refs = []
    if copy_inputs:
        (out_dir / "inputs").mkdir(exist_ok=True)
    for f in files:
        if copy_inputs:
            shutil.copy2(f, out_dir / "inputs" / f.name)
            fid = f"inputs/{f.name}"
        else:
            fid = f.resolve().as_uri()
        input_refs.append(add({"@id": fid, "@type": "File", "name": f.name,
                               "contentSize": str(f.stat().st_size), "sha256": _sha256(f),
                               "exampleOfWork": {"@id": data_param}}, part=copy_inputs))

    # Parameter values used, with adjustments
    adjust = {a.parameter: a for a in (check.adjustments if check else [])}
    pv_refs = []
    for name, value in result.params.items():
        e = {"@id": f"#pv-{_slug(name)}", "@type": "PropertyValue", "name": name, "value": str(value)}
        if name in params_by_name:
            e["exampleOfWork"] = {"@id": params_by_name[name].id}
        if name in adjust:
            a = adjust[name]
            e["description"] = f"Adjusted from {a.old} to {a.new}: {a.reason}."
        if param_notes and name in param_notes:
            e["description"] = param_notes[name]
        if "description" not in e:
            default = params_by_name[name].default if name in params_by_name else None
            e["description"] = "As published." if default is None or str(default) == str(value) else \
                f"Set to {value} (published: {default})."
        pv_refs.append(add(e))

    # Outputs
    (out_dir / "outputs").mkdir(exist_ok=True)
    shutil.copy2(result.results_csv, out_dir / "outputs" / "results.csv")
    out_param = spec.outputs[0].id if spec.outputs else None
    output_refs = [add({"@id": "outputs/results.csv", "@type": "File", "name": "Results table",
                        "encodingFormat": "text/csv",
                        **({"exampleOfWork": {"@id": out_param}} if out_param else {})}, part=True)]
    if result.masks_dir and Path(result.masks_dir).exists():
        (out_dir / "outputs" / "masks").mkdir(exist_ok=True)
        for m in sorted(Path(result.masks_dir).glob("*.tif")):
            shutil.copy2(m, out_dir / "outputs" / "masks" / m.name)
            output_refs.append(add({"@id": f"outputs/masks/{m.name}", "@type": "File",
                                    "name": f"Mask {m.stem}", "encodingFormat": "image/tiff"}, part=True))
    for ov in overlays or []:
        (out_dir / "outputs" / "overlays").mkdir(exist_ok=True)
        shutil.copy2(ov, out_dir / "outputs" / "overlays" / ov.name)
        output_refs.append(add({"@id": f"outputs/overlays/{ov.name}", "@type": "File",
                                "name": f"Overlay {ov.stem}", "encodingFormat": "image/png"}, part=True))

    out_root = Path(result.results_csv).parent
    for f in result.extra_outputs or []:  # every other file the method wrote, and packages.txt
        if Path(f).exists():
            rel = Path(f).relative_to(out_root).as_posix() if Path(f).is_relative_to(out_root) else Path(f).name
            (out_dir / "outputs" / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, out_dir / "outputs" / rel)
            output_refs.append(add({"@id": f"outputs/{rel}", "@type": "File", "name": rel,
                                    "encodingFormat": "text/plain" if Path(f).suffix == ".txt" else
                                    _MEDIA.get(Path(f).suffix.lower(), "application/octet-stream")}, part=True))
    for i, fig in enumerate(figures or []):  # made by the paper's own plotting code, as part of the run
        (out_dir / "outputs" / "figures").mkdir(exist_ok=True)
        shutil.copy2(fig, out_dir / "outputs" / "figures" / fig.name)
        output_refs.append(add({"@id": f"outputs/figures/{fig.name}", "@type": ["File", "ImageObject"],
                                "name": f"Figure {fig.stem}", "encodingFormat": _MEDIA.get(fig.suffix.lower(), "image/png"),
                                "description": "Plotted by the workflow's own code during this run."}, part=True))

    person_ref = add(_person(agent))
    adjust_txt = "; ".join([f"{a.parameter} {a.old} → {a.new} ({a.reason})" for a in adjust.values()]
                           + [f"{k}: {v}" for k, v in (param_notes or {}).items()])
    run_ref = add({
        "@id": "#run",
        "@type": "CreateAction",
        "name": f"ReCRATE {mode} run of {spec.name}",
        "description": f"Mode: {mode}." + (f" Adjustments: {adjust_txt}." if adjust_txt else " No adjustments.")
                       + (f" Follow-up check of run {followup['of']}: {followup['title']}." if followup else ""),
        "instrument": {"@id": wf_id},
        "agent": person_ref,
        "object": input_refs + pv_refs,
        "result": output_refs,
        "startTime": result.started,
        "endTime": result.ended,
        "actionStatus": {"@id": "http://schema.org/CompletedActionStatus"},
    })
    mentions = [run_ref]

    # Input compatibility check
    if check is not None:
        check_refs = []
        for i, item in enumerate(check.items):
            check_refs.append(add({
                "@id": f"#check-{i}", "@type": "PropertyValue", "name": item.name, "value": item.status,
                "description": f"paper: {item.expected}; data: {item.observed}" + (f"; {item.note}" if item.note else ""),
            }))
        mentions.append(add({
            "@id": "#input-check", "@type": "AssessAction",
            "name": f"Input compatibility check ({check.profile} profile)",
            "instrument": recrate_ref, "object": input_refs, "result": check_refs,
            "actionStatus": {"@id": "http://schema.org/CompletedActionStatus"},
            "description": f"Overall: {check.status}",
        }))

    # Comparison with reported results
    verdict = "not compared"
    if comparison is not None and comparison.measures:
        verdict = "reproduced" if comparison.reproduced else "differs"
        if followup:  # evidence about a difference, not a reproduction
            verdict = f"follow-up: {comparison.count} within tolerance"
        reported_refs = []
        if spec.expected_results and spec.expected_results.exists():
            (out_dir / "reported").mkdir(exist_ok=True)
            shutil.copy2(spec.expected_results, out_dir / "reported" / "results.csv")
            reported_refs.append(add({"@id": "reported/results.csv", "@type": "File",
                                      "name": "Results reported in the paper", "encodingFormat": "text/csv"},
                                     part=True))
        measure_refs = [add({"@id": "#verdict", "@type": "PropertyValue", "name": "verdict", "value": verdict,
                             "description": comparison.headline()})]
        sources = {t.column: t for t in spec.tolerance_specs()}
        for m in comparison.measures:
            t = sources.get(m.measure.split(" · ")[0])
            evidence = f": {t.evidence}" if t and t.evidence else ""
            measure_refs.append(add({
                "@id": f"#measure-{_slug(m.measure)}", "@type": "PropertyValue", "name": m.measure,
                "value": m.reproduced, "description": (
                    f"reported {m.reported}; {'absolute' if m.absolute else 'relative'} difference "
                    f"{m.rel_diff:.4f}; tolerance {m.tolerance} ({m.source or 'no source'}{evidence}); "
                    f"{'within' if m.within else 'outside'} tolerance"),
            }))
        with (out_dir / "comparison.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["value", "published", "reproduced", "difference", "kind", "tolerance", "tolerance_source",
                        "within"])
            for m in comparison.measures:
                w.writerow([m.measure, m.reported, m.reproduced, round(m.rel_diff, 6),
                            "absolute" if m.absolute else "relative", m.tolerance, m.source or "", m.within])
        comparison_ref = add({"@id": "comparison.csv", "@type": "File", "encodingFormat": "text/csv",
                              "name": "Comparison with the published values",
                              "description": "Per value: published, reproduced, difference, tolerance and its source, "
                                             "within tolerance or not."}, part=True)
        mentions.append(add({
            "@id": "#comparison", "@type": "AssessAction", "name": "Comparison with reported results",
            "instrument": recrate_ref, "object": output_refs[:1] + reported_refs,
            "result": measure_refs + [comparison_ref],
            "actionStatus": {"@id": "http://schema.org/CompletedActionStatus"},
        }))

    if ladder:
        from .model import STEPS

        steps = [add({"@id": f"#ladder-{k}", "@type": "PropertyValue", "name": label,
                      "value": ladder[k]["status"], "description": ladder[k]["reason"]}) for k, label in STEPS]
        mentions.append(add({
            "@id": "#ladder", "@type": "AssessAction", "name": "Verdict ladder",
            "description": "Materials found, runs, same numbers, same conclusion (the last is set only by a person).",
            "instrument": recrate_ref, "object": [{"@id": "#run"}], "result": steps,
            "actionStatus": {"@id": "http://schema.org/CompletedActionStatus"},
        }))

    for key, url in PROFILES.items():
        add({"@id": url, "@type": "CreativeWork", "name": {
            "process": "Process Run Crate", "workflow": "Workflow Run Crate", "wroc": "Workflow RO-Crate"}[key],
            "version": url.rsplit("/", 1)[-1]})

    root = {
        "@id": "./",
        "@type": "Dataset",
        "name": f"ReCRATE run: {mode} of {spec.method_name or 'method'} ({spec.name})",
        "description": (
            f"Follow-up check of {followup['of']}: {followup['title']}. With this change, {comparison.count} values "
            "are within tolerance. The original result stands; this is evidence for a person to weigh."
            if followup and comparison and comparison.measures
            else comparison.headline() if comparison and comparison.measures
            else reuse_summary(spec, files, out_dir / "outputs" / "results.csv", adjust, param_notes)),
        "datePublished": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "conformsTo": [{"@id": u} for u in PROFILES.values()],
        "mainEntity": {"@id": wf_id},
        "isBasedOn": {"@id": spec.identifier},
        "hasPart": has_part,
        "mentions": mentions,
        "additionalProperty": [{"@id": "#prop-mode"}, {"@id": "#prop-verdict"}, {"@id": "#prop-method"}]
                              + ([{"@id": "#prop-followup-of"}, {"@id": "#prop-followup"}] if followup else [])
                              + ([{"@id": "#prop-purpose"}] if purpose else []),
    }
    if purpose:
        graph.append({"@id": "#prop-purpose", "@type": "PropertyValue", "name": "recrate purpose", "value": purpose})
        root["description"] = purpose + ". " + root["description"]
    if followup:
        graph.append({"@id": "#prop-followup-of", "@type": "PropertyValue", "name": "recrate follow-up of",
                      "value": followup["of"]})
        graph.append({"@id": "#prop-followup", "@type": "PropertyValue", "name": "recrate follow-up check",
                      "value": followup["title"], "description": followup.get("why", "")})
    if spec.citation:
        root["citation"] = {"@id": spec.citation}
    if spec.license:
        root["license"] = {"@id": spec.license}
        lic = next((e for e in spec.raw.get("@graph", []) if e.get("@id") == spec.license), None)
        graph.append({"@id": spec.license, "@type": "CreativeWork",  # described, as RO-Crate recommends
                      "name": (lic or {}).get("name") or spec.license.rstrip("/").rsplit("/", 1)[-1],
                      "description": (lic or {}).get("description") or "The licence of the research this run is "
                                                                         "based on."})
    graph.append({"@id": "#prop-mode", "@type": "PropertyValue", "name": "recrate mode", "value": mode})
    graph.append({"@id": "#prop-verdict", "@type": "PropertyValue", "name": "recrate verdict", "value": verdict})
    graph.append({"@id": "#prop-method", "@type": "PropertyValue", "name": "recrate method",
                  "value": spec.method_name or (spec.workflow_path.stem if spec.workflow_path else "")})

    metadata = {  # the workflow-run terms define sha256 (file checksums)
        "@context": ["https://w3id.org/ro/crate/1.1/context", "https://w3id.org/ro/terms/workflow-run/context"],
        "@graph": [
            {"@id": "ro-crate-metadata.json", "@type": "CreativeWork",
             "conformsTo": {"@id": RO_CRATE}, "about": {"@id": "./"}},
            root,
            *graph,
        ],
    }
    save_crate(out_dir, metadata)
    return out_dir


# ---- reading, updating, preview ----------------------------------------------

def load_run_crate(run_dir: Path) -> dict[str, Any]:
    return json.loads((Path(run_dir) / "ro-crate-metadata.json").read_text(encoding="utf-8"))


def save_crate(run_dir: Path, metadata: dict[str, Any]) -> None:
    run_dir = Path(run_dir)
    (run_dir / "ro-crate-metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    (run_dir / "ro-crate-preview.html").write_text(render_preview(metadata), encoding="utf-8")


def entity_index(metadata: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {e["@id"]: e for e in metadata["@graph"]}


def approve(run_dir: Path, who: str, accepted: bool = True, note: str = "") -> None:
    """Record a person's visual check of the overlays in the run crate."""
    meta = load_run_crate(run_dir)
    idx = entity_index(meta)
    root = idx["./"]
    person = _person(who)
    if person["@id"] not in idx:
        meta["@graph"].append(person)
    n = sum(1 for e in meta["@graph"] if str(e["@id"]).startswith("#approval"))
    overlays = [p for p in root.get("hasPart", []) if "overlays/" in p["@id"]]
    approval = {
        "@id": f"#approval-{n}",
        "@type": "AssessAction",
        "name": "Visual check of detected objects",
        "agent": {"@id": person["@id"]},
        "object": overlays or [{"@id": "outputs/results.csv"}],
        "endTime": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "actionStatus": {"@id": "http://schema.org/CompletedActionStatus"},
        "result": {"@id": f"#approval-{n}-result"},
    }
    result = {"@id": f"#approval-{n}-result", "@type": "PropertyValue", "name": "approval",
              "value": "accepted" if accepted else "rejected", **({"description": note} if note else {})}
    meta["@graph"].extend([approval, result])
    root.setdefault("mentions", []).append({"@id": approval["@id"]})
    save_crate(run_dir, meta)


def add_figure(run_dir: Path, data: bytes, filename: str, name: str, description: str,
               made_from: list[str] | None = None, code: str = "recrate.figures") -> Path:
    """Record a figure made by code from this run's outputs: the file, and the action that made it from which files.
    A figure of the same file name is replaced (same inputs, same code)."""
    meta = load_run_crate(run_dir)
    idx = entity_index(meta)
    root = idx["./"]
    fid = f"outputs/figures/{filename}"
    (Path(run_dir) / "outputs" / "figures").mkdir(parents=True, exist_ok=True)
    (Path(run_dir) / fid).write_bytes(data)
    aid = f"#figure-{_slug(filename)}"
    meta["@graph"] = [e for e in meta["@graph"] if e["@id"] not in {fid, aid}]
    if "#figures-code" not in idx:
        meta["@graph"].append({"@id": "#figures-code", "@type": "SoftwareApplication",
                               "name": f"ReCRATE figures ({code})", "softwareVersion": __version__})
    meta["@graph"].extend([
        {"@id": fid, "@type": ["File", "ImageObject"], "name": name, "description": description,
         "encodingFormat": _MEDIA.get(Path(filename).suffix.lower(), "image/png")},
        {"@id": aid, "@type": "CreateAction", "name": f"Plot {name}", "instrument": {"@id": "#figures-code"},
         "object": [{"@id": f} for f in (made_from or ["outputs/results.csv"])], "result": {"@id": fid},
         "endTime": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
         "actionStatus": {"@id": "http://schema.org/CompletedActionStatus"}},
    ])
    if {"@id": fid} not in root.setdefault("hasPart", []):
        root["hasPart"].append({"@id": fid})
    if {"@id": aid} not in root.setdefault("mentions", []):
        root["mentions"].append({"@id": aid})
    save_crate(run_dir, meta)
    return Path(run_dir) / fid


def review_conclusion(run_dir: Path, who: str, agrees: str, note: str = "") -> None:
    """Record a person's judgement of the ladder's last step: does this run support the paper's conclusion?
    ``agrees``: yes | partly | no. Updates the ladder in the run crate."""
    if agrees not in {"yes", "partly", "no"}:
        raise ValueError("agrees must be yes, partly or no")
    if not who.strip():
        raise ValueError("A review needs the reviewer's name.")
    meta = load_run_crate(run_dir)
    idx = entity_index(meta)
    root = idx["./"]
    person = _person(who)
    if person["@id"] not in idx:
        meta["@graph"].append(person)
    n = sum(1 for e in meta["@graph"] if str(e["@id"]).startswith("#conclusion-review-")
            and e.get("@type") == "AssessAction")
    rid = f"#conclusion-review-{n}"
    meta["@graph"].extend([
        {"@id": rid, "@type": "AssessAction", "name": "Same conclusion? (review by a person)",
         "agent": {"@id": person["@id"]}, "object": [{"@id": "#run"}],
         "endTime": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
         "actionStatus": {"@id": "http://schema.org/CompletedActionStatus"}, "result": {"@id": f"{rid}-result"}},
        {"@id": f"{rid}-result", "@type": "PropertyValue", "name": "same conclusion", "value": agrees,
         **({"description": note} if note else {})},
    ])
    root.setdefault("mentions", []).append({"@id": rid})
    step = idx.get("#ladder-conclusion")
    if step is not None:
        step["value"] = {"yes": "passed", "partly": "partial", "no": "failed"}[agrees]
        step["description"] = f"Reviewed by {who}" + (f": {note}" if note else "")
    save_crate(run_dir, meta)


def run_ladder(run_dir: Path) -> dict[str, dict[str, str]] | None:
    """The verdict ladder recorded in a run crate, if any."""
    from .model import STEPS

    idx = entity_index(load_run_crate(run_dir))
    if "#ladder" not in idx:
        return None
    return {k: {"status": idx[f"#ladder-{k}"]["value"], "reason": idx[f"#ladder-{k}"].get("description", "")}
            for k, _ in STEPS if f"#ladder-{k}" in idx}


def review_image(run_dir: Path, who: str, image: str, accepted: bool, note: str = "") -> None:
    """Record a person's check of one image's result (its overlay), as an AssessAction on that input."""
    meta = load_run_crate(run_dir)
    idx = entity_index(meta)
    root = idx["./"]
    stem = Path(image).stem
    target = next((e["@id"] for e in meta["@graph"] if e.get("@type") == "File"
                   and str(e["@id"]).split("/")[-1].startswith(stem) and "overlays/" in str(e["@id"])),
                  next((e["@id"] for e in meta["@graph"] if e.get("name") == image), image))
    person = _person(who)
    if person["@id"] not in idx:
        meta["@graph"].append(person)
    n = sum(1 for e in meta["@graph"] if str(e["@id"]).startswith("#review-") and e.get("@type") == "AssessAction")
    rid = f"#review-{n}"
    meta["@graph"].extend([
        {"@id": rid, "@type": "AssessAction", "name": f"Visual check of {image}",
         "agent": {"@id": person["@id"]}, "object": [{"@id": target}],
         "endTime": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
         "actionStatus": {"@id": "http://schema.org/CompletedActionStatus"}, "result": {"@id": f"{rid}-result"}},
        {"@id": f"{rid}-result", "@type": "PropertyValue", "name": f"review of {image}",
         "value": "accepted" if accepted else "rejected", **({"description": note} if note else {})},
    ])
    root.setdefault("mentions", []).append({"@id": rid})
    save_crate(run_dir, meta)


def image_reviews(run_dir: Path) -> dict[str, dict[str, Any]]:
    """Latest review per image: {image: {"value": accepted|rejected, "note": ..., "by": ...}}."""
    meta = load_run_crate(run_dir)
    idx = entity_index(meta)
    out: dict[str, dict[str, Any]] = {}
    for e in meta["@graph"]:
        if str(e["@id"]).startswith("#review-") and e.get("@type") == "AssessAction":
            res = idx.get(e["result"]["@id"], {})
            image = res.get("name", "").removeprefix("review of ")
            out[image] = {"value": res.get("value"), "note": res.get("description", ""),
                          "by": idx.get(e["agent"]["@id"], {}).get("name")}
    return out


def run_story(run_dir: Path) -> dict[str, Any]:
    """The run crate as a readable account: what ran, on what, with which settings, checked by whom."""
    meta = load_run_crate(run_dir)
    idx = entity_index(meta)
    root, run = idx["./"], idx.get("#run", {})
    wf = idx.get(run.get("instrument", {}).get("@id", ""), {})
    software = [idx[s["@id"]] for s in wf.get("softwareRequirements", []) if s["@id"] in idx]
    objs = [idx.get(o["@id"], {}) for o in run.get("object", [])]
    formal = {e["@id"]: e for e in meta["@graph"] if e.get("@type") == "FormalParameter"}

    def assess(aid: str) -> list[dict[str, Any]]:
        a = idx.get(aid)
        if not a:
            return []
        res = a.get("result", [])
        res = res if isinstance(res, list) else [res]
        return [idx[r["@id"]] for r in res if r["@id"] in idx]

    approvals = []
    for e in meta["@graph"]:
        if e.get("@type") == "AssessAction" and str(e["@id"]).startswith(("#approval-", "#review-")):
            r = assess(e["@id"])
            approvals.append({"what": e.get("name"), "by": idx.get(e["agent"]["@id"], {}).get("name"),
                              "when": e.get("endTime"), "value": r[0].get("value") if r else None,
                              "note": r[0].get("description", "") if r else ""})
    return {
        "title": root.get("name"),
        "summary": root.get("description"),
        "paper_crate": root.get("isBasedOn", {}).get("@id"),
        "paper": root.get("citation", {}).get("@id"),
        "workflow": {"file": wf.get("@id"), "language": idx.get(wf.get("programmingLanguage", {}).get("@id", ""),
                                                                {}).get("name")},
        "software": [f"{s['name']} {s.get('softwareVersion', '')}".strip() for s in software],
        "who": idx.get(run.get("agent", {}).get("@id", ""), {}).get("name"),
        "started": run.get("startTime"), "ended": run.get("endTime"),
        "inputs": [{"file": o.get("name"), "size_bytes": int(o.get("contentSize", 0)),
                    "sha256": o.get("sha256", "")} for o in objs if o.get("@type") == "File"],
        "parameters": [{"name": o.get("name"), "value": o.get("value"),
                        "paper_default": formal.get(o.get("exampleOfWork", {}).get("@id", ""), {}).get("defaultValue"),
                        "note": o.get("description", "")} for o in objs if o.get("@type") == "PropertyValue"],
        "checks": [{"check": c.get("name"), "status": c.get("value"), "detail": c.get("description", "")}
                   for c in assess("#input-check")],
        "comparison": [{"measure": c.get("name"), "value": c.get("value"), "detail": c.get("description", "")}
                       for c in assess("#comparison") if c.get("@type") == "PropertyValue"],
        "reviews": approvals,
        "ladder": run_ladder(run_dir),
        "figures": [{"file": f["@id"], "name": f.get("name"), "description": f.get("description", "")}
                    for f in meta["@graph"] if str(f["@id"]).startswith("outputs/figures/")],
        "outputs": [p["@id"] for p in root.get("hasPart", []) if str(p["@id"]).startswith("outputs/")],
    }


def summarise_run(run_dir: Path) -> dict[str, Any]:
    meta = load_run_crate(run_dir)
    idx = entity_index(meta)
    root = idx["./"]
    props = {idx[p["@id"]]["name"]: idx[p["@id"]]["value"]
             for p in root.get("additionalProperty", []) if p["@id"] in idx}
    run = idx.get("#run", {})
    approvals = [idx[e["result"]["@id"]]["value"] for e in meta["@graph"]
                 if str(e["@id"]).startswith("#approval-") and e.get("@type") == "AssessAction"]
    agent = idx.get(run.get("agent", {}).get("@id", ""), {}).get("name")
    n_inputs = sum(1 for o in run.get("object", []) if idx.get(o["@id"], {}).get("@type") == "File")
    adjustments = [idx[o["@id"]]["description"] for o in run.get("object", [])
                   if idx.get(o["@id"], {}).get("description", "As published.") != "As published."]
    return {
        "run": Path(run_dir).name,
        "path": str(run_dir),
        "based_on": root.get("isBasedOn", {}).get("@id"),
        "mode": props.get("recrate mode"),
        "method": props.get("recrate method"),
        "verdict": props.get("recrate verdict"),
        "headline": root.get("description"),
        "when": run.get("endTime"),
        "by": agent,
        "images": n_inputs,
        "adjustments": adjustments,
        "approved": approvals[-1] if approvals else None,
        "followup_of": props.get("recrate follow-up of"),
        "purpose": props.get("recrate purpose"),
        "followup": props.get("recrate follow-up check"),
        "conclusion": next((idx[e["result"]["@id"]]["value"] for e in reversed(meta["@graph"])
                            if str(e["@id"]).startswith("#conclusion-review-") and e.get("@type") == "AssessAction"),
                           None),
    }


def render_preview(metadata: dict[str, Any]) -> str:
    """A small self-contained HTML page so anyone can read the crate."""
    idx = entity_index(metadata)
    root = idx.get("./", {})
    esc = html.escape

    def fmt(v: Any) -> str:
        if isinstance(v, dict) and "@id" in v:
            target = v["@id"]
            label = idx.get(target, {}).get("name", target)
            href = f"#{esc(target)}" if target in idx else esc(target)
            return f'<a href="{href}">{esc(str(label))}</a>'
        if isinstance(v, list):
            return "<br>".join(fmt(x) for x in v)
        return esc(str(v))

    rows = []
    for e in metadata["@graph"]:
        if e["@id"] == "ro-crate-metadata.json":
            continue
        props = "".join(f"<tr><th>{esc(k)}</th><td>{fmt(v)}</td></tr>"
                        for k, v in e.items() if k not in {"@id"})
        rows.append(f'<section id="{esc(e["@id"])}"><h3>{esc(str(e.get("name", e["@id"])))}</h3>'
                    f'<p class="id">{esc(e["@id"])}</p><table>{props}</table></section>')
    verdict = next((e.get("value") for e in metadata["@graph"] if e.get("name") == "recrate verdict"), "")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(str(root.get('name', 'RO-Crate')))}</title>
<link rel="icon" href="data:image/svg+xml;base64,{_FAVICON}">
<style>
:root{{--bg:#F4F4F0;--fg:#14181F;--muted:#6b6a65;--line:#e4e1d8;--accent:#2A78D6}}
@media (prefers-color-scheme:dark){{:root{{--bg:#171715;--fg:#F4F4F0;--muted:#a3a29b;--line:#34332f;--accent:#7FB0F0}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;max-width:900px;margin:0 auto;padding:24px 16px}}
h1{{font-size:1.5rem;margin:0 0 .25rem}} h3{{margin:0;font-size:1rem}}
.verdict{{display:inline-block;padding:2px 10px;border:1px solid var(--accent);color:var(--accent);border-radius:99px}}
section{{border-top:1px solid var(--line);padding:12px 0}} .id{{color:var(--muted);font-size:.8rem;margin:0 0 6px}}
table{{border-collapse:collapse;width:100%}} th{{text-align:left;color:var(--muted);font-weight:500;width:28%;vertical-align:top;padding:2px 8px 2px 0}}
td{{padding:2px 0;word-break:break-word}} a{{color:var(--accent)}}
</style></head><body>
<h1>{esc(str(root.get('name', '')))}</h1>
<p>{esc(str(root.get('description', '')))}</p>
<p><span class="verdict">{esc(str(verdict))}</span></p>
{''.join(rows)}
</body></html>"""
