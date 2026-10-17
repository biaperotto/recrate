"""When a reproduction differs: follow-up checks, each a recorded run of its own.

A difference is a finding. Follow-up checks look for where it comes from, one change at a time:

* **declared in the crate** (by whoever drafted it, with the paper's sentence that suggests it), e.g. an inclusion
  rule the paper states in words but the method did not apply;
* **rounding convention**: the reproduced values rounded as the paper prints them, instead of within a margin;
* **software version** and **data version**: proposed with what they need; they run once the paper's environment
  or data version is available.

Each check is its own run with its own run crate, linked to the run it follows up (``recrate follow-up of``) and
kept whether or not it closes the gap. The original result is never replaced, and a follow-up never changes the
method's verdict: it is evidence for a person to weigh.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .crate import CrateError, MethodSpec
from .pipeline import RunOutcome, check_inputs, execute, latest_run
from .runcrate import summarise_run


@dataclass
class FollowUp:
    id: str
    kind: str  # inclusion rule | rounding convention | software version | data version | parameter
    title: str
    why: str
    changes: dict[str, Any] = field(default_factory=dict)  # parameter -> value for this check
    quote: str = ""  # the paper's sentence that suggests it
    runnable: bool = True
    needs: str = ""  # what it needs when it can't run here

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "check"


def propose(spec: MethodSpec) -> list[FollowUp]:
    """The follow-up checks for a method whose numbers differ, declared ones first."""
    out = [FollowUp(id=_slug(f["title"]), kind=f.get("kind", "parameter"), title=f["title"], why=f.get("why", ""),
                    changes=dict(f.get("changes") or {}), quote=f.get("quote", ""))
           for f in spec.followups]
    if spec.abs_tolerances():
        out.append(FollowUp("rounding", "rounding convention", "Round as the paper prints",
                            "Compare each reproduced value rounded half-up to the decimals the paper prints, "
                            "instead of within a margin."))
    out.append(FollowUp("software-version", "software version", "Run with the software as of publication",
                        "A later version of a dependency can change results.", runnable=False,
                        needs="the paper's software environment, built from environment.json (not automated yet)"))
    out.append(FollowUp("data-version", "data version", "Run on the data version the paper used",
                        "Published data are sometimes corrected after the paper.", runnable=False,
                        needs="the version of the data the paper used; the crate does not record one"))
    return out


def run_followup(spec: MethodSpec, check_id: str, runs_dir: str | Path, who: str = "anonymous",
                 runner: str = "auto", original: str | Path | None = None) -> RunOutcome:
    """Run one follow-up check on the paper's data and record it, linked to the reproduction it follows up
    (``original``: a run folder; by default the latest reproduction of this method)."""
    runs_dir = Path(runs_dir)
    f = next((x for x in propose(spec) if x.id == check_id), None)
    if f is None:
        raise CrateError(f"No follow-up check {check_id!r}. Checks: {', '.join(x.id for x in propose(spec))}")
    if not f.runnable:
        raise CrateError(f"'{f.title}' can't run here yet: it needs {f.needs}.")
    original = Path(original) if original else latest_run(runs_dir, spec)
    if original is None:
        raise CrateError("Reproduce the method first: a follow-up check follows up a reproduction.")
    defaults = spec.default_params()
    unknown = [k for k in f.changes if k not in defaults]
    if unknown:
        raise CrateError(f"'{f.title}' changes parameters the method doesn't have: {', '.join(unknown)}")
    why = f.why + (f' The paper: "{f.quote}"' if f.quote else "")
    notes = {k: f"Follow-up check '{f.title}': set to {v!r} (as published: {defaults.get(k)!r}). {why}"
             for k, v in f.changes.items()}
    return execute(spec, spec.example_data, "follow-up", runs_dir, runner, who, {**defaults, **f.changes},
                   check_inputs(spec, spec.example_data), compare=True, param_notes=notes,
                   rounded=f.id == "rounding", followup={"of": original.name, "title": f.title, "why": why})


def followups_of(runs_dir: str | Path, original: str | Path) -> list[dict[str, Any]]:
    """Every follow-up run recorded for a reproduction, oldest first, whether or not it closed the gap."""
    name = Path(original).name
    return [s for s in (summarise_run(m.parent) for m in sorted(Path(runs_dir).glob("*/ro-crate-metadata.json")))
            if s.get("followup_of") == name]


def followups_for(runs_dir: str | Path, spec: MethodSpec) -> list[dict[str, Any]]:
    """Every follow-up run of this method, whichever reproduction it followed, oldest first."""
    method = spec.method_name or (spec.workflow_path.stem if spec.workflow_path else "")
    return [s for s in (summarise_run(m.parent) for m in sorted(Path(runs_dir).glob("*/ro-crate-metadata.json")))
            if s.get("followup_of") and s["method"] == method and s["based_on"] == spec.identifier]
