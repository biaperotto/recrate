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
    profile: str = "bioimaging"
    workflow_path: Path | None = None
    workflow_language: str | None = None
    software: list[tuple[str, str | None]] = field(default_factory=list)
    inputs: list[Parameter] = field(default_factory=list)
    outputs: list[Parameter] = field(default_factory=list)
    example_data: Path | None = None
    expected_results: Path | None = None
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

    def summary(self) -> dict[str, Any]:
        """A plain dict for display (the 'method card')."""
        d = self.data_input
        return {
            "name": self.name,
            "description": self.description,
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
            "tolerances": self.tolerances(),
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
            out[name] = Prop(name=name, value=pv.get("value"), unit=pv.get("unitText") or pv.get("unitCode"))
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


def load_crate(path: str | Path) -> MethodSpec:
    """Load an input crate from a folder containing ro-crate-metadata.json."""
    path = Path(path)
    meta_file = path / METADATA_FILE if path.is_dir() else path
    root_dir = meta_file.parent
    if not meta_file.exists():
        raise CrateError(f"No {METADATA_FILE} found at {path}")
    raw = json.loads(meta_file.read_text(encoding="utf-8"))
    graph = {e["@id"]: e for e in raw.get("@graph", []) if "@id" in e}

    descriptor = graph.get(METADATA_FILE, {})
    root_id = _ref(descriptor.get("about")) or "./"
    root = graph.get(root_id)
    if root is None:
        raise CrateError("Crate has no root Dataset.")

    wf_id = _ref(root.get("mainEntity"))
    wf = graph.get(wf_id or "")
    if wf is None:
        raise CrateError("Crate root has no mainEntity (the workflow).")

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
    profile = str(root_props["domain profile"].value) if "domain profile" in root_props else "bioimaging"

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
        raw=raw,
    )
