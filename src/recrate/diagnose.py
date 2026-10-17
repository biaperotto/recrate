"""M2 — diagnose a crate.

Diagnose answers one question: is everything needed to **validate and reuse** this
paper's methods present? It never runs a method. "ready" means ready to validate,
not "reproduced" or "works". Every check is plain code against the crate and
docs/crate-spec.md; no LLM decides a finding or the readiness.

Each finding has a ``level`` (``crate`` or ``method``), a ``severity``
(``blocking`` | ``warning`` | ``info``), a plain message and, where possible, the
fix. Per method, three flags are reported:

* **runnable** — workflow file, a runner for its language, and parameters present;
* **validatable** — runnable, plus example data and reported results;
* **reusable** — validatable, plus a known profile and its input properties.

Overall readiness: **ready** (every method validatable and reusable),
**ready-with-gaps** (at least one method validatable), or **blocked** (none).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .compare import DEFAULT_TOLERANCE, _num, read_results
from .crate import METADATA_FILE, CrateError, list_methods, load_crate, resolve_crate_dir
from .locate import norm_doi
from .profiles import PROFILES, get_profile

BLOCKING, WARNING, INFO = "blocking", "warning", "info"

#: Languages ReCRATE has a runner for. Others (Galaxy, CWL, Nextflow, R, ...) are
#: valid workflows but cannot be run yet, so a method in them is not runnable.
SUPPORTED_LANGUAGE = ("python", "imagej")


@dataclass
class Finding:
    level: str          # crate | method
    severity: str       # blocking | warning | info
    message: str
    fix: str = ""
    method: str | None = None

    def _order(self) -> int:
        return {BLOCKING: 0, WARNING: 1, INFO: 2}.get(self.severity, 3)


@dataclass
class MethodDiagnosis:
    id: str
    name: str
    language: str
    runnable: bool = False
    validatable: bool = False
    reusable: bool = False
    findings: list[Finding] = field(default_factory=list)


@dataclass
class Diagnosis:
    readiness: str                       # ready | ready-with-gaps | blocked
    crate_source: str                    # found | uploaded | drafted | supplied
    source_url: str | None = None
    crate_findings: list[Finding] = field(default_factory=list)
    methods: list[MethodDiagnosis] = field(default_factory=list)

    def all_findings(self) -> list[Finding]:
        out = list(self.crate_findings)
        for m in self.methods:
            out.extend(m.findings)
        return sorted(out, key=lambda f: (f._order(), f.level != "crate"))

    def top_fixes(self, n: int = 3) -> list[Finding]:
        return [f for f in self.all_findings() if f.severity in (BLOCKING, WARNING)][:n]

    def to_json(self) -> dict[str, Any]:
        return {
            "readiness": self.readiness,
            "crate_source": self.crate_source,
            "source_url": self.source_url,
            "crate_findings": [asdict(f) for f in self.crate_findings],
            "methods": [{**{k: v for k, v in asdict(m).items() if k != "findings"},
                         "findings": [asdict(f) for f in m.findings]} for m in self.methods],
        }

    def write(self, out_dir: str | Path) -> tuple[Path, Path]:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        jp = out_dir / "diagnosis.json"
        mp = out_dir / "diagnosis.md"
        jp.write_text(json.dumps(self.to_json(), indent=2, ensure_ascii=False), encoding="utf-8")
        mp.write_text(self.to_markdown(), encoding="utf-8")
        return jp, mp

    def to_markdown(self) -> str:
        sentence = {
            "ready": "Ready to validate: every method has what it needs to be validated and reused.",
            "ready-with-gaps": "Ready with gaps: at least one method can be validated, but some cannot.",
            "blocked": "Blocked: no method can be validated yet.",
        }[self.readiness]
        lines = [f"# Diagnosis: {self.readiness}", "", sentence, "",
                 f"Crate source: **{self.crate_source}**"
                 + (f" — {self.source_url}" if self.source_url else ""), ""]
        if self.methods:
            lines += ["| method | runnable | validatable | reusable |", "| --- | --- | --- | --- |"]
            for m in self.methods:
                lines.append(f"| {m.name} ({m.language or '?'}) | {_y(m.runnable)} | "
                             f"{_y(m.validatable)} | {_y(m.reusable)} |")
            lines.append("")
        lines += ["| severity | level | finding | fix |", "| --- | --- | --- | --- |"]
        for f in self.all_findings():
            where = f.level if f.method is None else f"{f.level}: {f.method}"
            lines.append(f"| {f.severity} | {where} | {_md(f.message)} | {_md(f.fix) or '—'} |")
        return "\n".join(lines) + "\n"


def _y(flag: bool) -> str:
    return "yes" if flag else "no"


def _md(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ")


# ---- crate-level checks ---------------------------------------------------

def _haspart_files_exist(crate_dir: Path) -> list[str]:
    """Local File/Dataset entities whose files are missing on disk."""
    raw = json.loads((crate_dir / METADATA_FILE).read_text(encoding="utf-8"))
    missing = []
    for e in raw.get("@graph", []):
        eid = e.get("@id", "")
        types = e.get("@type", [])
        types = types if isinstance(types, list) else [types]
        if eid in (METADATA_FILE, "./") or "://" in eid or eid.startswith("#"):
            continue
        if {"File", "Dataset"} & set(types) and not (crate_dir / eid).exists():
            missing.append(eid)
    return missing


def _crate_checks(crate_dir: Path, paper_doi: str | None, findings: list[Finding]) -> None:
    missing = _haspart_files_exist(crate_dir)
    if missing:
        findings.append(Finding("crate", BLOCKING,
                                f"{len(missing)} declared file(s) are missing from the crate: "
                                f"{', '.join(missing[:5])}.",
                                "Add the missing files or remove them from the crate."))
    spec0 = load_crate(crate_dir)
    root_doi = norm_doi(spec0.citation)
    want = norm_doi(paper_doi)
    if not root_doi:
        findings.append(Finding("crate", WARNING, "The crate declares no citation (paper DOI).",
                                "Add `citation` → the paper's DOI to the root Dataset."))
    elif want and root_doi != want:
        findings.append(Finding("crate", BLOCKING,
                                f"The crate cites a different work ({root_doi}) than the paper ({want}): "
                                f"this is probably the wrong crate.",
                                "Check the crate belongs to this paper."))
    if not spec0.license:
        findings.append(Finding("crate", WARNING, "No licence declared on the crate.",
                                "Add `license` to the root Dataset."))
    if spec0.profile not in PROFILES:
        findings.append(Finding("crate", WARNING,
                                f"Domain profile '{spec0.profile}' is not known to ReCRATE, so your data "
                                f"cannot be checked against the methods.",
                                f"Use one of: {', '.join(sorted(PROFILES))}."))
    if spec0.draft:
        findings.append(Finding("crate", INFO, "This crate was drafted from the paper and is marked draft.",
                                "A person should confirm it before relying on it."))


# ---- method-level checks --------------------------------------------------

def _supported_language(language: str | None) -> bool:
    low = (language or "").lower()
    return any(tok in low for tok in SUPPORTED_LANGUAGE)


def _measurement_columns(path: Path) -> list[str]:
    """Numeric result columns, exactly as compare.summarise would pick them.

    These are the "reported measurements"; text columns (units, image ids) are
    not compared, so they do not need a tolerance.
    """
    try:
        rows = read_results(path)
    except OSError:
        return []
    if not rows:
        return []
    return [c for c in rows[0] if c != "image" and _num(rows[0][c]) is not None]


def _method_checks(crate_dir: Path, meta: dict[str, str], profile_known: bool) -> MethodDiagnosis:
    md = MethodDiagnosis(id=meta["id"], name=meta["name"], language=meta.get("language", ""))
    f = md.findings
    try:
        spec = load_crate(crate_dir, meta["id"])
    except CrateError as e:
        f.append(Finding("method", BLOCKING, f"Could not load this method: {e}", method=meta["name"]))
        return md

    wf_ok = bool(spec.workflow_path and spec.workflow_path.exists())
    if not wf_ok:
        f.append(Finding("method", BLOCKING, "The workflow file is missing from the crate.",
                         "Add the workflow file referenced by the ComputationalWorkflow.", meta["name"]))

    lang_ok = _supported_language(spec.workflow_language)
    if not lang_ok:
        f.append(Finding("method", BLOCKING,
                         f"No runner for '{spec.workflow_language or 'unknown'}' workflows "
                         f"(Galaxy, CWL, Nextflow, R are not supported yet).",
                         "Use a Python or ImageJ-macro workflow, or add a runner.", meta["name"]))

    try:
        _ = spec.data_input
        has_input = True
    except CrateError:
        has_input = False
        f.append(Finding("method", BLOCKING, "No File input FormalParameter is declared.",
                         "Declare an `input` FormalParameter of type File.", meta["name"]))
    has_output = bool(spec.outputs)
    if not has_output:
        f.append(Finding("method", BLOCKING, "No output FormalParameter is declared.",
                         "Declare an `output` FormalParameter.", meta["name"]))

    if not spec.software:
        f.append(Finding("method", WARNING, "No softwareRequirements declared.",
                         "List software with versions in `softwareRequirements`.", meta["name"]))
    elif any(v is None for _n, v in spec.software):
        f.append(Finding("method", WARNING, "Some softwareRequirements have no version.",
                         "Add `softwareVersion` to each SoftwareApplication.", meta["name"]))

    for p in spec.value_inputs:
        if p.default is None:
            f.append(Finding("method", WARNING, f"Parameter '{p.name}' has no default value.",
                             f"Add `defaultValue` to '{p.name}'.", meta["name"]))

    has_example = bool(spec.example_data and spec.example_data.exists())
    has_results = bool(spec.expected_results and spec.expected_results.exists())
    if not has_example:
        f.append(Finding("method", WARNING, "No example data in the crate, so the method cannot be validated "
                         "(its status would be 'not validated').",
                         "Add the paper's own input data (exampleOfWork → the File input).", meta["name"]))
    if not has_results:
        f.append(Finding("method", WARNING, "No reported results in the crate, so the method cannot be "
                         "validated.", "Add the paper's reported results (exampleOfWork → the output).",
                         meta["name"]))
    elif has_results and not spec.tolerances():
        # With no declared tolerances, compare falls back to the default for every
        # numeric measurement column (see compare.compare_results).
        cols = _measurement_columns(spec.expected_results)
        if cols:
            f.append(Finding("method", WARNING,
                             f"No tolerance declared for the reported measurement(s) "
                             f"{', '.join(cols)}; the {DEFAULT_TOLERANCE:.0%} default would be used.",
                             "Add `tolerance:<column>` PropertyValues to the output parameter.", meta["name"]))

    # Profile input-property declarations (declare-only, via the profile hook).
    props_ok = True
    if profile_known:
        for item in get_profile(spec.profile).diagnose_inputs(spec):
            f.append(Finding("method", item.get("severity", WARNING), item["message"],
                             item.get("fix", ""), meta["name"]))
            if item.get("severity") == WARNING:
                props_ok = False

    md.runnable = wf_ok and lang_ok and has_input and has_output
    md.validatable = md.runnable and has_example and has_results
    md.reusable = md.validatable and profile_known and props_ok
    return md


# ---- entry point ----------------------------------------------------------

def diagnose_crate(crate: str | Path, paper_doi: str | None = None, crate_source: str = "supplied",
                   source_url: str | None = None) -> Diagnosis:
    """Diagnose a crate against docs/crate-spec.md. See the module docstring."""
    crate_findings: list[Finding] = []
    try:
        crate_dir = resolve_crate_dir(crate)
        methods_meta = list_methods(crate_dir)
    except (CrateError, ValueError, json.JSONDecodeError) as e:
        crate_findings.append(Finding("crate", BLOCKING,
                                      f"The crate does not parse as an RO-Crate: {e}",
                                      "Fix ro-crate-metadata.json."))
        return Diagnosis(readiness="blocked", crate_source=crate_source, source_url=source_url,
                         crate_findings=crate_findings)

    _crate_checks(crate_dir, paper_doi, crate_findings)

    if not methods_meta:
        crate_findings.append(Finding("crate", BLOCKING,
                                      "The crate declares no ComputationalWorkflow, so there is no method.",
                                      "Add a ComputationalWorkflow (mainEntity)."))
        return Diagnosis(readiness="blocked", crate_source=crate_source, source_url=source_url,
                         crate_findings=crate_findings)

    profile_known = load_crate(crate_dir).profile in PROFILES
    methods = [_method_checks(crate_dir, m, profile_known) for m in methods_meta]

    blocked_crate = any(f.severity == BLOCKING for f in crate_findings)
    if blocked_crate or not any(m.validatable for m in methods):
        readiness = "blocked"
    elif all(m.validatable and m.reusable for m in methods):
        readiness = "ready"
    else:
        readiness = "ready-with-gaps"

    return Diagnosis(readiness=readiness, crate_source=crate_source, source_url=source_url,
                     crate_findings=crate_findings, methods=methods)
