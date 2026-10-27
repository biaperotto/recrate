"""What "reproduced" means, in one place: the verdict ladder and where each tolerance comes from.

The app, the CLI, the MCP server and the run crates all use these, so a verdict reads the same everywhere.

**The ladder.** Each method climbs four steps, each ``passed``, ``partial``, ``failed`` or ``not checked``:

1. *Materials found*: the workflow, the published data and the published results;
2. *Runs*: the workflow ran on the published data;
3. *Same numbers*: the outputs match the published values within tolerance (``9/12`` style counts);
4. *Same conclusion*: never set by the code. Only a person can mark it, and it is stored as a review.

A step only advances on evidence from a run. "Ready" never means "reproduced".

**Tolerance sources.** Every tolerance says where it came from. Without a source, there is no verdict on the numbers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PASSED, PARTIAL, FAILED, NOT_CHECKED = "passed", "partial", "failed", "not checked"

STEPS = [
    ("materials", "Materials found"),
    ("runs", "Runs"),
    ("numbers", "Same numbers"),
    ("conclusion", "Same conclusion"),
]

TOLERANCE_SOURCES = {
    "paper-rounding": "derived from the decimals printed in the paper",
    "paper-stated": "stated in the paper or its published test (quoted)",
    "method-noise": "measured by rerunning a stochastic method",
    "person": "set by a named person, with a reason",
    "code-stated": "stated by the authors' code or tutorial output (file named)",
    "test-stated": "stated by the workflow's own published test (file named)",
}


@dataclass
class Tolerance:
    column: str
    value: float
    absolute: bool  # an absolute margin per item, else a relative one on the mean
    source: str | None = None  # one of TOLERANCE_SOURCES
    evidence: str = ""  # the quoted sentence, the person and reason, or how it was derived

    def label(self) -> str:
        v = f"±{self.value:g}" if self.absolute else f"{self.value:.0%}"
        return f"{v} ({self.source})" if self.source else f"{v} (no source)"


def step(status: str, reason: str) -> dict[str, str]:
    return {"status": status, "reason": reason}


def build_ladder(spec, comparison=None, error: str | None = None, blocked: str | None = None,
                 review: dict[str, Any] | None = None, ran: bool = False) -> dict[str, dict[str, str]]:
    """The ladder for one method, from what is known: the crate (``spec``), a run's ``comparison`` or ``error``,
    a ``blocked`` reason (no runner here), and a person's ``review`` of the conclusion."""
    has_wf = bool(spec.workflow_path and spec.workflow_path.exists())
    has_data = bool(spec.example_data and spec.example_data.exists()) or not getattr(spec, "needs_data", True)
    has_results = bool(spec.expected_results and spec.expected_results.exists())
    missing = [w for w, ok in (("workflow", has_wf), ("published data", has_data),
                               ("published results", has_results)) if not ok]
    if not has_wf:
        materials = step(FAILED, "No workflow file: nothing can run.")
    elif missing:
        materials = step(PARTIAL, "Missing: " + ", ".join(missing) + ".")
    else:
        materials = step(PASSED, "Workflow, published data and published results found"
                                 + (" (draft crate: a person must confirm it)." if spec.draft else "."))

    if error:
        runs = step(FAILED, error)
    elif blocked:
        runs = step(NOT_CHECKED, blocked)
    elif ran or comparison is not None:
        runs = step(PASSED, "Ran on the published data.")
    else:
        runs = step(NOT_CHECKED, "Not run yet.")

    numbers = step(NOT_CHECKED, "No run compared with the published results yet.")
    if not has_results and ran:
        numbers = step(NOT_CHECKED, "The paper publishes no results for this method to compare with.")
    if comparison is not None and comparison.measures:
        n = len(comparison.measures)
        ok = sum(1 for m in comparison.measures if m.within)
        unsourced = sorted({m.measure.split(" · ")[0] for m in comparison.measures if not m.source})
        sources = sorted({m.source for m in comparison.measures if m.source})
        if unsourced:
            numbers = step(NOT_CHECKED, f"{ok}/{n} within tolerance, but no verdict: the tolerance for "
                                        f"{', '.join(unsourced)} has no stated source.")
        else:
            missing_items = f"; {len(comparison.missing_images)} published item(s) not produced" \
                if comparison.missing_images else ""
            status = PASSED if ok == n and not comparison.missing_images else PARTIAL if ok else FAILED
            numbers = step(status, f"{ok}/{n} values within tolerance ({', '.join(sources)}){missing_items}.")
    elif comparison is not None:
        numbers = step(NOT_CHECKED, "Nothing to compare: no published results.")
    kind = getattr(spec, "output_kind", "table")
    if kind not in COMPARED_KINDS:
        numbers = step(NOT_CHECKED, f"ReCRATE does not compare '{kind}' outputs yet "
                                    f"({OUTPUT_KINDS.get(kind, 'unknown kind')}).")

    if review:
        status = {"yes": PASSED, "partly": PARTIAL, "no": FAILED}.get(review.get("agrees", ""), NOT_CHECKED)
        note = f": {review['note']}" if review.get("note") else ""
        conclusion = step(status, f"Reviewed by {review.get('by', 'someone')}{note}")
    else:
        conclusion = step(NOT_CHECKED, "Needs a person: does this run support the paper's conclusion?")
    return {"materials": materials, "runs": runs, "numbers": numbers, "conclusion": conclusion}


def derived_status(ladder: dict[str, dict[str, str]]) -> str:
    """The older single status (validated | differs | failed | not validated), kept for compatibility."""
    if ladder["runs"]["status"] == FAILED:
        return "failed"
    n = ladder["numbers"]["status"]
    if n == PASSED:
        return "validated"
    if n in (PARTIAL, FAILED):
        return "differs"
    return "not validated"


def verdict_sentence(ladders: dict[str, dict[str, dict[str, str]]]) -> str:
    """One sentence for a whole paper, written from the ladders only (never by a model)."""
    if not ladders:
        return "Nothing to reproduce yet: no runnable method."
    names = list(ladders)
    # a method that runs but has no published numbers is not a failed reproduction: it is said apart
    nothing = [m for m in names if ladders[m]["runs"]["status"] == PASSED
               and ladders[m]["numbers"]["status"] == NOT_CHECKED and "no results" in ladders[m]["numbers"]["reason"]]
    note = (f" {', '.join(nothing)} runs; the paper publishes no numbers to compare it with." if nothing else "")
    names = [m for m in names if m not in nothing]
    if not names:
        return f"Runs, nothing to compare: {', '.join(nothing)} runs, and the paper publishes no numbers for it."
    passed = [m for m in names if ladders[m]["numbers"]["status"] == PASSED]
    differs = [m for m in names if ladders[m]["numbers"]["status"] in (PARTIAL, FAILED)]
    failed = [m for m in names if ladders[m]["runs"]["status"] == FAILED]
    if len(passed) == len(names):
        return f"Reproduced: {len(names)} of {len(names)} method(s) give the published numbers within tolerance.{note}"
    if failed and not passed and not differs:
        m = failed[0]
        return f"Not reproduced: {m} did not run ({ladders[m]['runs']['reason']}).{note}"
    if differs and len(names) == 1:
        m = differs[0]
        return f"Partly reproduced: {m} has {ladders[m]['numbers']['reason']}{note}"
    if differs:
        counts = "; ".join(f"{m} {ladders[m]['numbers']['reason'].split(' ')[0]}" for m in differs[:4])
        more = f" and {len(differs) - 4} more" if len(differs) > 4 else ""
        return (f"Partly reproduced: {len(passed)} of {len(names)} methods match in full. Values within tolerance: "
                f"{counts}{more}.{note}")
    return f"Not checked yet: {len(passed)} of {len(names)} method(s) reproduced so far.{note}"


# ---- output kinds ---------------------------------------------------------------------------------------------
# Every method output declares a kind (``kind`` on the output parameter; ``table`` by default). Each kind has its
# own comparison. ReCRATE compares the kinds in COMPARED_KINDS today; for any other kind the "same numbers" step
# says so ("not checked"), rather than comparing it the wrong way.
OUTPUT_KINDS = {
    "scalar": "a count, a p-value: relative or absolute tolerance",
    "table": "per cell, keyed by row id; a tolerance per column",
    "array": "labels exact (or IoU / Dice above a threshold); intensities by RMSE or max abs difference",
    "image": "as array",
    "distribution": "summary statistics within tolerance, or a two-sample test with its threshold stated",
    "categorical": "agreement (adjusted Rand, accuracy) above a threshold",
    "metric": "within the paper's reported CI, or a stated tolerance",
    "figure": "never compared as pixels: compare the data behind it",
    "text": "exact match or set overlap",
    "files": "one row per produced file: sha1 exact, else size",
}
COMPARED_KINDS = {"scalar", "table", "files"}

# ---- parameters with meaning ------------------------------------------------------------------------------------
PARAMETER_ROLES = {
    "scales_with": "has a scaling rule: adjusted automatically when the data differs, with the computation shown",
    "free": "a choice the user may change, with a reason",
    "fixed": "as published unless the user changes it, with a reason",
}


def parameter_plan(spec, check=None, used: dict[str, Any] | None = None) -> dict[str, list[dict[str, Any]]]:
    """The parameters in three groups, for any field:

    * ``adjusted``: changed automatically by a scaling rule (with the computation);
    * ``decide``: no rule, but your data differs in a way that may matter (a check it is sensitive to warned or
      failed, or a scaling rule that couldn't be applied because a property is unknown), or a free choice;
    * ``as_published``: the rest.
    """
    adjusted = {a.parameter: a for a in (check.adjustments if check else [])}
    status = {i.name: i for i in (check.items if check else [])}
    out: dict[str, list[dict[str, Any]]] = {"adjusted": [], "decide": [], "as_published": []}
    for p in spec.value_inputs:
        row = {"name": p.name, "published": p.default, "unit": p.prop("unit"), "role": p.role,
               "quote": p.prop("quote") or "", "description": p.description or ""}
        if used is not None and p.name in used:
            row["value"] = used[p.name]
        rule = p.scaling_rule
        sensitive = status.get(str(p.prop("sensitive_to") or ""))
        rule_check = status.get(rule[0].capitalize()) if rule else None
        if p.name in adjusted:
            a = adjusted[p.name]
            out["adjusted"].append({**row, "value": a.new, "why": a.reason})
        elif rule and rule_check is not None and rule_check.status != "ok":
            out["decide"].append({**row, "why": f"Scales with {rule[0]}, which could not be compared: "
                                               f"{rule_check.note or rule_check.observed}"})
        elif sensitive is not None and sensitive.status != "ok":
            out["decide"].append({**row, "why": f"Depends on {sensitive.name.lower()}, which differs from the "
                                               f"paper's data ({sensitive.observed} vs {sensitive.expected})."})
        elif p.role == "free":
            out["decide"].append({**row, "why": "A choice you may change"
                                               + (": changing it asks a different question than the paper's."
                                                  if str(p.prop("changes_question")).lower() == "true" else ".")})
        else:
            out["as_published"].append(row)
    return out


def assumptions(spec, environment=None) -> list[str]:
    """What the paper doesn't say, so ReCRATE (or whoever drafted the crate) had to assume it."""
    out = []
    if spec.draft:
        out.append("The crate is a draft, made from the paper: a named person must confirm it.")
    for p in spec.value_inputs:
        if spec.draft and not p.prop("quote"):
            out.append(f"Parameter {p.name} = {p.default}: no sentence of the paper states it.")
    for t in spec.tolerance_specs():
        if not t.source:
            out.append(f"Tolerance for {t.column}: no stated source.")
        elif "wider" in t.evidence:
            out.append(f"Tolerance for {t.column} (±{t.value:g}): {t.evidence}.")
    if "pixel size" not in spec.data_input.properties and any(
            (p.scaling_rule or ("",))[0] == "pixel size" for p in spec.value_inputs):
        out.append("The pixel size of the paper's images is not stated.")
    for name, version in spec.software:
        if not version:
            out.append(f"{name}: version not stated.")
    for s in (environment.software if environment else []):
        if s.how in {"as of publication", "later", "history", "unknown"}:
            out.append(f"{s.name}{' ' + s.version if s.version else ''}: {s.note}.")
    return out
