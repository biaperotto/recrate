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
    has_data = bool(spec.example_data and spec.example_data.exists())
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
    passed = [m for m in names if ladders[m]["numbers"]["status"] == PASSED]
    differs = [m for m in names if ladders[m]["numbers"]["status"] in (PARTIAL, FAILED)]
    failed = [m for m in names if ladders[m]["runs"]["status"] == FAILED]
    if len(passed) == len(names):
        return f"Reproduced: {len(names)} of {len(names)} method(s) give the published numbers within tolerance."
    if failed and not passed and not differs:
        m = failed[0]
        return f"Not reproduced: {m} did not run ({ladders[m]['runs']['reason']})."
    if differs:
        m = differs[0]
        others = f" {len(passed)} of {len(names)} method(s) match." if len(names) > 1 else ""
        return f"Partly reproduced: {m} has {ladders[m]['numbers']['reason']}{others}"
    return f"Not checked yet: {len(passed)} of {len(names)} method(s) reproduced so far."
