"""Read an input RO-Crate (Workflow RO-Crate) into a MethodSpec.

The input crate follows Workflow RO-Crate 1.0. The parts ReCRATE needs are:

* root Dataset: name, description, citation (the paper), license, author
* mainEntity: the workflow file (ComputationalWorkflow) with programmingLanguage,
  input / output FormalParameters and softwareRequirements
* FormalParameters: encodingFormat, defaultValue, and ``additionalProperty``
  (schema.org PropertyValue) for things the standard has no term for yet:
  pixel size, channels, dimensions, how a parameter scales, tolerances
* data entities with ``exampleOfWork`` pointing at a parameter: the paper's own
  input data and its reported (expected) output

Domain-specific properties use plain PropertyValues so the crate stays valid
RO-Crate. Their names are a ReCRATE proposal, documented in docs/crate-spec.md.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

METADATA_FILE = "ro-crate-metadata.json"


class CrateError(ValueError):
    """The crate is missing something ReCRATE needs."""


@dataclass
class Prop:
    """A PropertyValue: a named value with an optional unit."""

    name: str
    value: Any
    unit: str | None = None
    description: str = ""


@dataclass
class Parameter:
    id: str
    name: str
    type: str | None = None
    format: str | None = None
    description: str | None = None
    default: Any = None
    properties: dict[str, Prop] = field(default_factory=dict)

    def prop(self, name: str, default: Any = None) -> Any:
        p = self.properties.get(name)
        return p.value if p is not None else default

    @property
    def is_file(self) -> bool:
        return (self.type or "").lower() in {"file", "dataset", "directory"}


@dataclass
class MethodSpec:
    """Everything ReCRATE knows about a published method."""

    path: Path
    identifier: str
    name: str
    description: str = ""
    citation: str | None = None
    license: str | None = None
    authors: list[str] = field(default_factory=list)
    profile: str = "generic"
    workflow_path: Path | None = None
    workflow_language: str | None = None
    software: list[tuple[str, str | None]] = field(default_factory=list)
    inputs: list[Parameter] = field(default_factory=list)
    outputs: list[Parameter] = field(default_factory=list)
    example_data: Path | None = None
    expected_results: Path | None = None
    method_id: str = ""
    method_name: str = ""
    method_description: str = ""
    draft: bool = False
    followups: list[dict[str, Any]] = field(default_factory=list)  # declared follow-up checks (recrate.followup)
    raw: dict[str, Any] = field(default_factory=dict)

    # ---- convenience -------------------------------------------------
    @property
    def data_input(self) -> Parameter:
        for p in self.inputs:
            if p.is_file:
                return p
        raise CrateError("No File input parameter declared in the crate.")

    @property
    def value_inputs(self) -> list[Parameter]:
        return [p for p in self.inputs if not p.is_file]

    def default_params(self) -> dict[str, Any]:
        return {p.name: _coerce(p.default, p.type) for p in self.value_inputs}

    def tolerances(self) -> dict[str, float]:
        tol: dict[str, float] = {}
        for out in self.outputs:
            for name, prop in out.properties.items():
                if name.startswith("tolerance:"):
                    tol[name.split(":", 1)[1]] = float(prop.value)
        return tol

    def abs_tolerances(self) -> dict[str, float]:
        """Absolute margins per column (``tolerance_abs:<column>``), checked for every item."""
        tol: dict[str, float] = {}
        for out in self.outputs:
            for name, prop in out.properties.items():
                if name.startswith("tolerance_abs:"):
                    tol[name.split(":", 1)[1]] = float(prop.value)
        return tol

    def tolerance_specs(self) -> list["Tolerance"]:
        """Every tolerance with its source (``tolerance_source:<column>``): see model.TOLERANCE_SOURCES."""
        from .model import Tolerance

        out = []
        for o in self.outputs:
            for name, prop in o.properties.items():
                kind, _, col = name.partition(":")
                if kind in {"tolerance", "tolerance_abs"} and col:
                    src = o.properties.get(f"tolerance_source:{col}")
                    out.append(Tolerance(col, float(prop.value), kind == "tolerance_abs",
                                         str(src.value) if src else None, src.description if src else ""))
        return out

    def summary(self) -> dict[str, Any]:
        """A plain dict for display (the 'method card')."""
        d = self.data_input
        return {
            "name": self.name,
            "description": self.description,
            "method": self.method_name,
            "method_description": self.method_description,
            "draft": self.draft,
            "paper": self.citation,
            "authors": self.authors,
            "license": self.license,
            "workflow": self.workflow_path.name if self.workflow_path else None,
            "language": self.workflow_language,
            "software": [f"{n} {v}" if v else n for n, v in self.software],
            "profile": self.profile,
            "expects": {
                "format": d.format,
                "description": d.description,
                **{k: (f"{p.value} {p.unit}" if p.unit else p.value) for k, p in d.properties.items()},
            },
            "parameters": {
                p.name: {"default": p.default, "description": p.description,
                         **({"scaling": p.prop("scaling")} if p.prop("scaling") else {})}
                for p in self.value_inputs
            },
            "tolerances": {t.column: {"value": t.value, "absolute": t.absolute, "source": t.source,
                                      "evidence": t.evidence} for t in self.tolerance_specs()},
            "has_example_data": bool(self.example_data and self.example_data.exists()),
            "has_reported_results": bool(self.expected_results and self.expected_results.exists()),
        }


# ---- parsing -----------------------------------------------------------

def _as_list(x: Any) -> list[Any]:
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def _ref(x: Any) -> str | None:
    if isinstance(x, dict):
        return x.get("@id")
    return x if isinstance(x, str) else None


def _types(entity: dict[str, Any]) -> set[str]:
    return set(_as_list(entity.get("@type")))


def _coerce(value: Any, type_name: str | None) -> Any:
    if value is None:
        return None
    t = (type_name or "").lower()
    try:
        if t in {"float", "number"}:
            return float(value)
        if t in {"integer", "int"}:
            return int(value)
        if t == "boolean":
            return str(value).lower() in {"true", "1", "yes"}
    except (TypeError, ValueError):
        return value
    return value


def _props(entity: dict[str, Any], graph: dict[str, dict[str, Any]]) -> dict[str, Prop]:
    out: dict[str, Prop] = {}
    for r in _as_list(entity.get("additionalProperty")):
        pv = graph.get(_ref(r) or "", r if isinstance(r, dict) else {})
        name = pv.get("name")
        if name:
            out[name] = Prop(name=name, value=pv.get("value"), unit=pv.get("unitText") or pv.get("unitCode"),
                             description=pv.get("description", ""))
    return out


def _param(ref: Any, graph: dict[str, dict[str, Any]]) -> Parameter:
    pid = _ref(ref) or ""
    e = graph.get(pid)
    if e is None:
        raise CrateError(f"Parameter {pid!r} is referenced but not described in the crate.")
    return Parameter(
        id=pid,
        name=e.get("name", pid.lstrip("#")),
        type=e.get("additionalType"),
        format=e.get("encodingFormat"),
        description=e.get("description"),
        default=e.get("defaultValue"),
        properties=_props(e, graph),
    )


def resolve_crate_dir(path: str | Path) -> Path:
    """Accept a crate folder, its metadata file, or a paper agent folder (with crate/)."""
    path = Path(path)
    if path.is_file():
        return path.parent
    if (path / METADATA_FILE).exists():
        return path
    if (path / "crate" / METADATA_FILE).exists():
        return path / "crate"
    raise CrateError(f"No {METADATA_FILE} found at {path} (or {path / 'crate'})")


def _read(path: str | Path) -> tuple[Path, dict[str, Any], dict[str, dict[str, Any]], dict[str, Any]]:
    root_dir = resolve_crate_dir(path)
    raw = json.loads((root_dir / METADATA_FILE).read_text(encoding="utf-8"))
    graph = {e["@id"]: e for e in raw.get("@graph", []) if "@id" in e}
    descriptor = graph.get(METADATA_FILE, {})
    root = graph.get(_ref(descriptor.get("about")) or "./")
    if root is None:
        raise CrateError("Crate has no root Dataset.")
    return root_dir, raw, graph, root


def _workflow_ids(graph: dict[str, dict[str, Any]], root: dict[str, Any]) -> list[str]:
    """mainEntity first, then any other ComputationalWorkflow in the crate."""
    ids = []
    main = _ref(root.get("mainEntity"))
    if main:
        ids.append(main)
    for eid, e in graph.items():
        if "ComputationalWorkflow" in _types(e) and eid not in ids:
            ids.append(eid)
    return ids


def list_methods(path: str | Path) -> list[dict[str, str]]:
    """The methods (workflows) a crate declares: id, name, description, language."""
    _, _, graph, root = _read(path)
    out = []
    for wid in _workflow_ids(graph, root):
        wf = graph.get(wid, {})
        lang = graph.get(_ref(wf.get("programmingLanguage")) or "", {})
        out.append({"id": wid, "name": wf.get("name", Path(wid).stem),
                    "description": wf.get("description", ""), "language": lang.get("name", "")})
    return out


def load_crate(path: str | Path, method: str | None = None) -> MethodSpec:
    """Load one method from an input crate.

    ``path`` is a crate folder or a paper agent folder. ``method`` selects a
    workflow by id or name; by default the crate's mainEntity.
    """
    root_dir, raw, graph, root = _read(path)
    wf_ids = _workflow_ids(graph, root)
    if not wf_ids:
        raise CrateError("Crate declares no workflow (mainEntity or ComputationalWorkflow).")
    if method is None:
        wf_id = wf_ids[0]
    else:
        matches = [w for w in wf_ids if w == method or graph.get(w, {}).get("name") == method
                   or Path(w).stem == method]
        if not matches:
            names = ", ".join(graph.get(w, {}).get("name", w) for w in wf_ids)
            raise CrateError(f"No method {method!r} in this crate. Methods: {names}")
        wf_id = matches[0]
    wf = graph[wf_id]

    lang = graph.get(_ref(wf.get("programmingLanguage")) or "", {})
    software = []
    for r in _as_list(wf.get("softwareRequirements")):
        s = graph.get(_ref(r) or "", {})
        if s:
            software.append((s.get("name", _ref(r)), s.get("softwareVersion") or s.get("version")))

    authors = []
    for r in _as_list(root.get("author")):
        a = graph.get(_ref(r) or "", r if isinstance(r, dict) else {})
        authors.append(a.get("name") or _ref(r) or "")

    root_props = _props(root, graph)
    profile = str(root_props["domain profile"].value) if "domain profile" in root_props else "generic"

    inputs = [_param(r, graph) for r in _as_list(wf.get("input"))]
    outputs = [_param(r, graph) for r in _as_list(wf.get("output"))]
    input_ids = {p.id for p in inputs}
    output_ids = {p.id for p in outputs}

    example_data = expected = None
    for eid, e in graph.items():
        target = _ref(e.get("exampleOfWork"))
        if not target or eid.startswith("#") or "://" in eid:
            continue
        if target in input_ids and example_data is None and {"Dataset", "File"} & _types(e):
            example_data = root_dir / eid
        elif target in output_ids and expected is None:
            expected = root_dir / eid

    return MethodSpec(
        path=root_dir,
        identifier=root.get("identifier") or root.get("name") or str(root_dir.resolve()),
        name=root.get("name", root_dir.name),
        description=root.get("description", ""),
        citation=_ref(root.get("citation")),
        license=_ref(root.get("license")),
        authors=[a for a in authors if a],
        profile=profile,
        workflow_path=root_dir / wf_id if wf_id and "://" not in wf_id else None,
        workflow_language=lang.get("name"),
        software=software,
        inputs=inputs,
        outputs=outputs,
        example_data=example_data,
        expected_results=expected,
        method_id=wf_id,
        method_name=wf.get("name", Path(wf_id).stem),
        method_description=wf.get("description", ""),
        draft=str(root_props["draft"].value).lower() == "true" if "draft" in root_props else False,
        followups=[json.loads(graph[_ref(r)]["value"]) for r in _as_list(wf.get("additionalProperty"))
                   if graph.get(_ref(r) or "", {}).get("name") == "followup"],
        raw=raw,
    )
