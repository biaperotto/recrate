"""Build an input crate from a plain description of a method.

Two uses:

1. **Paper with no crate.** A chat agent (through the MCP ``draft_crate`` tool)
   reads the paper and repo, fills in the description below, and ReCRATE writes
   a valid Workflow RO-Crate. The user then confirms it. The draft is only as
   good as what the paper states, so it is marked ``draft: true`` until confirmed.
2. **Authors packaging their own method** at publication time.

Description format (JSON / dict)::

    {
      "name": "...", "description": "...", "identifier": "...",
      "citation": "https://doi.org/...", "license": "https://spdx.org/licenses/CC-BY-4.0",
      "authors": ["..."], "profile": "bioimaging", "draft": false,
      "workflow": {"file": "path/to/count_nuclei.ijm", "language": "ImageJ Macro Language",
                   "software": [{"name": "Fiji", "version": "2.16.0"}]},
      "inputs": [
        {"id": "in-images", "name": "images", "type": "File", "format": "image/tiff",
         "description": "...", "properties": {"pixel size": {"value": 0.65, "unit": "µm"},
                                              "channels": {"value": 1}, "dimensions": {"value": "2D"}}},
        {"id": "in-min-size", "name": "min_size_px", "type": "Float", "default": 50,
         "description": "...", "scaling": "inverse_pixel_area"}
      ],
      "outputs": [{"id": "out-results", "name": "results", "type": "File", "format": "text/csv",
                   "tolerances": {"count": 0.05, "mean_area": 0.10}}],
      "example_data": "path/to/images/",       # optional, copied to data/
      "expected": "path/to/results.csv"        # optional, copied to expected/results.csv
    }
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .crate import load_crate

REQUIRED = ["name", "workflow", "inputs", "outputs"]


def _pv(pid: str, name: str, value: Any, unit: str | None = None) -> dict[str, Any]:
    e = {"@id": pid, "@type": "PropertyValue", "name": name, "value": value}
    if unit:
        e["unitText"] = unit
    return e


def build_crate(desc: dict[str, Any], out_dir: str | Path, base_dir: str | Path | None = None) -> Path:
    """Write an input crate folder from a description. Returns the folder."""
    missing = [k for k in REQUIRED if k not in desc]
    if missing:
        raise ValueError(f"Description is missing: {', '.join(missing)}")
    out_dir = Path(out_dir)
    base = Path(base_dir) if base_dir else Path.cwd()
    out_dir.mkdir(parents=True, exist_ok=True)
    graph: list[dict[str, Any]] = []
    has_part: list[dict[str, str]] = []

    wf_desc = desc["workflow"]
    wf_src = (base / wf_desc["file"]) if not Path(wf_desc["file"]).is_absolute() else Path(wf_desc["file"])
    wf_name = wf_src.name
    if wf_src.exists():
        shutil.copy2(wf_src, out_dir / wf_name)
    has_part.append({"@id": wf_name})

    software_refs = []
    for i, s in enumerate(wf_desc.get("software", [])):
        sid = f"#software-{i}"
        software_refs.append({"@id": sid})
        graph.append({"@id": sid, "@type": "SoftwareApplication", "name": s["name"],
                      **({"softwareVersion": str(s["version"])} if s.get("version") else {})})
    graph.append({"@id": "#language", "@type": "ComputerLanguage",
                  "name": wf_desc.get("language", "ImageJ Macro Language")})

    def param(p: dict[str, Any], is_output: bool) -> dict[str, str]:
        pid = "#" + p.get("id", p["name"]).lstrip("#")
        e: dict[str, Any] = {
            "@id": pid, "@type": "FormalParameter", "name": p["name"],
            "conformsTo": {"@id": "https://bioschemas.org/profiles/FormalParameter/1.0-RELEASE"},
            "additionalType": p.get("type", "File" if is_output else "Text"),
        }
        for key, field in (("format", "encodingFormat"), ("description", "description")):
            if p.get(key):
                e[field] = p[key]
        if p.get("default") is not None:
            e["defaultValue"] = str(p["default"])
        props = []
        for name, v in (p.get("properties") or {}).items():
            v = v if isinstance(v, dict) else {"value": v}
            ppid = f"{pid}-{name.replace(' ', '-')}"
            props.append({"@id": ppid})
            graph.append(_pv(ppid, name, v.get("value"), v.get("unit")))
        if p.get("scaling"):
            ppid = f"{pid}-scaling"
            props.append({"@id": ppid})
            graph.append(_pv(ppid, "scaling", p["scaling"]))
        for col, tol in (p.get("tolerances") or {}).items():
            ppid = f"{pid}-tolerance-{col}"
            props.append({"@id": ppid})
            graph.append(_pv(ppid, f"tolerance:{col}", tol))
        if props:
            e["additionalProperty"] = props
        graph.append(e)
        return {"@id": pid}

    input_refs = [param(p, False) for p in desc["inputs"]]
    output_refs = [param(p, True) for p in desc["outputs"]]
    data_input = next((r for r, p in zip(input_refs, desc["inputs"])
                       if p.get("type", "Text").lower() in {"file", "dataset", "directory"}), None)

    graph.append({
        "@id": wf_name,
        "@type": ["File", "SoftwareSourceCode", "ComputationalWorkflow"],
        "name": wf_src.stem,
        "programmingLanguage": {"@id": "#language"},
        "softwareRequirements": software_refs,
        "input": input_refs,
        "output": output_refs,
    })

    if desc.get("example_data"):
        src = base / desc["example_data"]
        dst = out_dir / "data"
        if src.resolve() != dst.resolve():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
        parts = sorted(p for p in dst.iterdir() if p.is_file())
        graph.append({"@id": "data/", "@type": "Dataset", "name": "Example data from the paper",
                      "hasPart": [{"@id": f"data/{p.name}"} for p in parts],
                      **({"exampleOfWork": data_input} if data_input else {})})
        for p in parts:
            graph.append({"@id": f"data/{p.name}", "@type": "File", "name": p.name,
                          "encodingFormat": "image/tiff" if p.suffix.lower() in {".tif", ".tiff"} else None})
        has_part.append({"@id": "data/"})
    if desc.get("expected"):
        (out_dir / "expected").mkdir(exist_ok=True)
        src = base / desc["expected"]
        if src.resolve() != (out_dir / "expected" / "results.csv").resolve():
            shutil.copy2(src, out_dir / "expected" / "results.csv")
        graph.append({"@id": "expected/results.csv", "@type": "File", "name": "Results reported in the paper",
                      "encodingFormat": "text/csv", "exampleOfWork": output_refs[0]})
        has_part.append({"@id": "expected/results.csv"})

    root_props = [{"@id": "#domain-profile"}]
    graph.append(_pv("#domain-profile", "domain profile", desc.get("profile", "bioimaging")))
    if desc.get("draft"):
        root_props.append({"@id": "#draft"})
        graph.append(_pv("#draft", "draft", True))

    authors = []
    for i, a in enumerate(desc.get("authors", [])):
        aid = f"#author-{i}"
        authors.append({"@id": aid})
        graph.append({"@id": aid, "@type": "Person", "name": a})

    root = {
        "@id": "./", "@type": "Dataset",
        "name": desc["name"],
        "description": desc.get("description", ""),
        "mainEntity": {"@id": wf_name},
        "hasPart": has_part,
        "additionalProperty": root_props,
        "conformsTo": [{"@id": "https://w3id.org/workflowhub/workflow-ro-crate/1.0"}],
    }
    if desc.get("identifier"):
        root["identifier"] = desc["identifier"]
    if desc.get("citation"):
        root["citation"] = {"@id": desc["citation"]}
    if desc.get("license"):
        root["license"] = {"@id": desc["license"]}
    if authors:
        root["author"] = authors
    if desc.get("datePublished"):
        root["datePublished"] = desc["datePublished"]

    metadata = {
        "@context": "https://w3id.org/ro/crate/1.1/context",
        "@graph": [
            {"@id": "ro-crate-metadata.json", "@type": "CreativeWork",
             "conformsTo": {"@id": "https://w3id.org/ro/crate/1.1"}, "about": {"@id": "./"}},
            root,
            {"@id": "https://w3id.org/workflowhub/workflow-ro-crate/1.0", "@type": "CreativeWork",
             "name": "Workflow RO-Crate", "version": "1.0"},
            *[{k: v for k, v in e.items() if v is not None} for e in graph],
        ],
    }
    (out_dir / "ro-crate-metadata.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False),
                                                    encoding="utf-8")
    load_crate(out_dir)  # validate that ReCRATE can read what it wrote
    return out_dir


def build_crate_from_json(json_path: str | Path, out_dir: str | Path) -> Path:
    json_path = Path(json_path)
    return build_crate(json.loads(json_path.read_text(encoding="utf-8")), out_dir, base_dir=json_path.parent)
