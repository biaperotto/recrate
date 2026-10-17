"""Build an input crate from a plain description of a paper and its methods.

Used in three ways:

1. **Paper with no crate.** ``recrate agentify`` (or Claude through the MCP
   ``draft_crate`` tool / the ``/agentify`` Claude Code command) reads the paper
   and its code, fills in the description below, and ReCRATE writes a valid
   Workflow RO-Crate marked ``draft`` until a person confirms it.
2. **Authors packaging their own paper** at publication time.
3. Tests and the demo.

Description format (JSON / dict). Each method becomes one tool of the paper agent::

    {
      "name": "...", "description": "...", "identifier": "...",
      "citation": "https://doi.org/...", "license": "https://spdx.org/licenses/CC-BY-4.0",
      "authors": ["..."], "profile": "bioimaging", "draft": false,
      "methods": [
        {
          "name": "count_nuclei", "description": "What the method does, in the paper's words",
          "workflow": {"file": "code/count_nuclei.ijm", "language": "ImageJ Macro Language",
                       "software": [{"name": "Fiji", "version": "2.16.0"}]},
          "inputs": [
            {"id": "in-images", "name": "input_dir", "type": "File", "format": "image/tiff",
             "description": "...", "properties": {"pixel size": {"value": 0.65, "unit": "µm"},
                                                  "channels": {"value": 1}, "dimensions": {"value": "2D"}}},
            {"id": "in-min-size", "name": "min_size_px", "type": "Float", "default": 50, "unit": "pixel^2",
             "description": "...", "scaling": "inverse_pixel_area",   # or "scales_with:<data property>^<exponent>"
             "quote": "the paper's sentence that states it"},
            {"id": "in-group", "name": "group_column", "type": "Text", "default": "Species",
             "role": "free", "changes_question": true},  # free: the user may change it; recorded as a new question
            {"id": "in-offset", "name": "threshold_offset", "type": "Float", "default": 0,
             "sensitive_to": "Bit depth / intensity"}   # needs your decision when that check warns
          ],
          "outputs": [{"id": "out-results", "name": "results", "type": "File", "format": "text/csv",
                       "kind": "table",                       # recrate.model.OUTPUT_KINDS
                       "tolerances": {"count": {"value": 0.05, "source": "paper-stated",
                                                "quote": "counts agreed within 5% between observers"}},
                       "abs_tolerances": {}}],   # optional: per-item absolute margins, for rounded values
          "example_data": "data/images/",      # optional: the paper's own data
          "expected": "results/table1.csv"     # optional: the paper's reported results
        }
      ]
    }

Every tolerance says where it came from (see recrate.model): ``paper-rounding`` (half a unit of the last decimal
printed), ``paper-stated`` (with the ``quote``), ``method-noise`` (with ``runs``) or ``person`` (with ``by`` and
``reason``). ``recrate check-description`` rejects a tolerance with no source.

A method may also list ``followups``: checks to run when its numbers differ, each a recorded run of its own
(see recrate.followup)::

    "followups": [{"title": "Exclude incomplete clutches", "kind": "inclusion rule",
                   "changes": {"keep_rows": "Clutch Completion=Yes"},
                   "why": "...", "quote": "the paper's sentence that suggests it"}]

A description with a single top-level ``workflow`` / ``inputs`` / ``outputs``
(no ``methods`` list) is accepted as one method.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from .crate import load_crate

METHOD_KEYS = ("workflow", "inputs", "outputs", "example_data", "expected")
FILE_TYPES = {"file", "dataset", "directory"}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "method"


def _pv(pid: str, name: str, value: Any, unit: str | None = None) -> dict[str, Any]:
    e = {"@id": pid, "@type": "PropertyValue", "name": name, "value": value}
    if unit:
        e["unitText"] = unit
    return e


def tolerance_entry(tol: Any) -> dict[str, Any]:
    """A tolerance as ``{"value", "source", "evidence"}``, from a number (no source) or a dict with ``source`` and
    its evidence: ``quote`` (paper-stated), ``by`` and ``reason`` (person), ``runs`` (method-noise), ``note``."""
    if not isinstance(tol, dict):
        return {"value": float(tol), "source": None, "evidence": ""}
    bits = [f'"{tol["quote"]}"' if tol.get("quote") else "",
            f"set by {tol['by']}" if tol.get("by") else "", tol.get("reason", ""),
            f"spread over {tol['runs']} runs" if tol.get("runs") else "", f"in {tol['file']}" if tol.get("file") else "",
            tol.get("note", "")]
    return {"value": float(tol["value"]), "source": tol.get("source"), "evidence": "; ".join(b for b in bits if b)}


def normalise(desc: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    """Return (methods, multi). ``multi`` is False for the single-method shorthand."""
    if desc.get("methods"):
        return list(desc["methods"]), True
    if all(k in desc for k in ("workflow", "inputs", "outputs")):
        m = {k: desc[k] for k in METHOD_KEYS if k in desc}
        m.setdefault("name", Path(desc["workflow"]["file"]).stem)
        return [m], False
    raise ValueError("Description needs a 'methods' list (or a single workflow/inputs/outputs).")


def build_crate(desc: dict[str, Any], out_dir: str | Path, base_dir: str | Path | None = None) -> Path:
    """Write an input crate folder from a description. Returns the folder."""
    if "name" not in desc:
        raise ValueError("Description is missing 'name' (the paper's title).")
    methods, multi = normalise(desc)
    out_dir = Path(out_dir)
    base = Path(base_dir) if base_dir else Path.cwd()
    out_dir.mkdir(parents=True, exist_ok=True)

    def src(p: str) -> Path:
        q = Path(p)
        return q if q.is_absolute() else base / q

    graph: list[dict[str, Any]] = []
    has_part: list[dict[str, str]] = []
    workflow_ids: list[str] = []
    seen_software: dict[tuple[str, str], str] = {}
    seen_lang: dict[str, str] = {}

    for m in methods:
        mslug = _slug(m.get("name") or Path(m["workflow"]["file"]).stem)
        prefix = f"{mslug}-" if multi else ""

        # Workflow file
        wf_src = src(m["workflow"]["file"])
        wf_rel = f"workflows/{wf_src.name}" if multi else wf_src.name
        (out_dir / wf_rel).parent.mkdir(parents=True, exist_ok=True)
        if wf_src.exists() and wf_src.resolve() != (out_dir / wf_rel).resolve():
            shutil.copy2(wf_src, out_dir / wf_rel)
        has_part.append({"@id": wf_rel})
        workflow_ids.append(wf_rel)

        software_refs = []
        for s in m["workflow"].get("software", []):
            key = (s["name"], str(s.get("version") or ""))
            if key not in seen_software:
                sid = f"#software-{len(seen_software)}"
                seen_software[key] = sid
                graph.append({"@id": sid, "@type": "SoftwareApplication", "name": s["name"],
                              **({"softwareVersion": str(s["version"])} if s.get("version") else {})})
            software_refs.append({"@id": seen_software[key]})
        lang_name = m["workflow"].get("language", "ImageJ Macro Language")
        if lang_name not in seen_lang:
            lid = f"#language-{_slug(lang_name)}"
            seen_lang[lang_name] = lid
            graph.append({"@id": lid, "@type": "ComputerLanguage", "name": lang_name})

        def param(p: dict[str, Any], is_output: bool) -> dict[str, str]:
            pid = "#" + prefix + p.get("id", p["name"]).lstrip("#")
            e: dict[str, Any] = {
                "@id": pid, "@type": "FormalParameter", "name": p["name"],
                "conformsTo": {"@id": "https://bioschemas.org/profiles/FormalParameter/1.0-RELEASE"},
                "additionalType": p.get("type", "File" if is_output else "Text"),
            }
            if p.get("format"):
                e["encodingFormat"] = p["format"]
            if p.get("description"):
                e["description"] = p["description"]
            if p.get("default") is not None:
                e["defaultValue"] = str(p["default"])
            props = []
            for name, v in (p.get("properties") or {}).items():
                v = v if isinstance(v, dict) else {"value": v}
                ppid = f"{pid}-{_slug(name)}"
                props.append({"@id": ppid})
                graph.append(_pv(ppid, name, v.get("value"), v.get("unit")))
            for key in ("scaling", "role", "unit", "quote", "sensitive_to", "changes_question", "kind"):
                if p.get(key) not in (None, ""):  # meaning: how it scales, who may change it, the paper's sentence
                    props.append({"@id": f"{pid}-{_slug(key)}"})
                    graph.append(_pv(f"{pid}-{_slug(key)}", key, p[key]))
            for key, kind in (("tolerances", "tolerance"), ("abs_tolerances", "tolerance_abs")):
                for col, tol in (p.get(key) or {}).items():
                    t = tolerance_entry(tol)
                    ppid = f"{pid}-{_slug(kind)}-{_slug(col)}"
                    props.append({"@id": ppid})
                    graph.append(_pv(ppid, f"{kind}:{col}", t["value"]))
                    if t.get("source"):
                        props.append({"@id": f"{ppid}-source"})
                        graph.append({**_pv(f"{ppid}-source", f"tolerance_source:{col}", t["source"]),
                                      **({"description": t["evidence"]} if t["evidence"] else {})})
            if props:
                e["additionalProperty"] = props
            graph.append(e)
            return {"@id": pid}

        input_refs = [param(p, False) for p in m["inputs"]]
        output_refs = [param(p, True) for p in m["outputs"]]
        data_input = next((r for r, p in zip(input_refs, m["inputs"])
                           if p.get("type", "Text").lower() in FILE_TYPES), None)

        wf_entity = {
            "@id": wf_rel,
            "@type": ["File", "SoftwareSourceCode", "ComputationalWorkflow"],
            "name": m.get("name", wf_src.stem),
            "programmingLanguage": {"@id": seen_lang[lang_name]},
            "softwareRequirements": software_refs,
            "input": input_refs,
            "output": output_refs,
        }
        if m.get("description"):
            wf_entity["description"] = m["description"]
        followups = []
        for i, f in enumerate(m.get("followups") or []):  # proposed checks for when the numbers differ
            fid = f"#{prefix}followup-{i}"
            graph.append({"@id": fid, "@type": "PropertyValue", "name": "followup",
                          "value": json.dumps(f, ensure_ascii=False), "description": f.get("title", "")})
            followups.append({"@id": fid})
        if followups:
            wf_entity["additionalProperty"] = followups
        graph.append(wf_entity)

        if m.get("example_data"):
            d_src = src(m["example_data"])
            d_rel = f"data/{mslug}/" if multi else "data/"
            dst = out_dir / d_rel
            if d_src.resolve() != dst.resolve():
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(d_src, dst)
            parts = sorted(p for p in dst.iterdir() if p.is_file())
            graph.append({"@id": d_rel, "@type": "Dataset", "name": f"Example data from the paper ({m.get('name')})",
                          "hasPart": [{"@id": f"{d_rel}{p.name}"} for p in parts],
                          **({"exampleOfWork": data_input} if data_input else {})})
            for p in parts:
                e = {"@id": f"{d_rel}{p.name}", "@type": "File", "name": p.name}
                if p.suffix.lower() in {".tif", ".tiff"}:
                    e["encodingFormat"] = "image/tiff"
                graph.append(e)
            has_part.append({"@id": d_rel})
        if m.get("expected"):
            e_rel = f"expected/{mslug}.csv" if multi else "expected/results.csv"
            (out_dir / e_rel).parent.mkdir(parents=True, exist_ok=True)
            e_src = src(m["expected"])
            if e_src.resolve() != (out_dir / e_rel).resolve():
                shutil.copy2(e_src, out_dir / e_rel)
            graph.append({"@id": e_rel, "@type": "File",
                          "name": f"Results reported in the paper ({m.get('name')})",
                          "encodingFormat": "text/csv", "exampleOfWork": output_refs[0]})
            has_part.append({"@id": e_rel})

    root_props = [{"@id": "#domain-profile"}]
    graph.append(_pv("#domain-profile", "domain profile", desc.get("profile", "generic")))
    if desc.get("draft"):
        root_props.append({"@id": "#draft"})
        graph.append(_pv("#draft", "draft", True))
    if desc.get("paper_file"):
        p_src = src(desc["paper_file"])
        if p_src.exists():
            shutil.copy2(p_src, out_dir / p_src.name)
            graph.append({"@id": p_src.name, "@type": ["File", "ScholarlyArticle"], "name": desc["name"],
                          **({"identifier": desc["citation"]} if desc.get("citation") else {})})
            has_part.append({"@id": p_src.name})

    authors = []
    for i, a in enumerate(desc.get("authors", [])):
        authors.append({"@id": f"#author-{i}"})
        graph.append({"@id": f"#author-{i}", "@type": "Person", "name": a})

    root: dict[str, Any] = {
        "@id": "./", "@type": "Dataset",
        "name": desc["name"],
        "description": desc.get("description", ""),
        "mainEntity": {"@id": workflow_ids[0]},
        "hasPart": has_part,
        "additionalProperty": root_props,
        "conformsTo": [{"@id": "https://w3id.org/workflowhub/workflow-ro-crate/1.0"}],
    }
    for key in ("identifier", "datePublished"):
        if desc.get(key):
            root[key] = desc[key]
    if desc.get("citation"):
        root["citation"] = {"@id": desc["citation"]}
    if desc.get("license"):
        root["license"] = {"@id": desc["license"]}
    if authors:
        root["author"] = authors

    metadata = {
        "@context": "https://w3id.org/ro/crate/1.1/context",
        "@graph": [
            {"@id": "ro-crate-metadata.json", "@type": "CreativeWork",
             "conformsTo": {"@id": "https://w3id.org/ro/crate/1.1"}, "about": {"@id": "./"}},
            root,
            {"@id": "https://w3id.org/workflowhub/workflow-ro-crate/1.0", "@type": "CreativeWork",
             "name": "Workflow RO-Crate", "version": "1.0"},
            *graph,
        ],
    }
    (out_dir / "ro-crate-metadata.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False),
                                                    encoding="utf-8")
    for wid in workflow_ids:  # check ReCRATE can read every method it wrote
        load_crate(out_dir, method=wid)
    return out_dir


def build_crate_from_json(json_path: str | Path, out_dir: str | Path) -> Path:
    json_path = Path(json_path)
    return build_crate(json.loads(json_path.read_text(encoding="utf-8")), out_dir, base_dir=json_path.parent)
