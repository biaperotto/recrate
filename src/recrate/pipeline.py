"""The two ReCRATE modes, end to end.

* ``reproduce``: run the method on the paper's own data, compare with the
  reported results, write a run crate.
* ``run_on_data`` (reuse): check your data against the paper's declared inputs,
  adjust or stop, run, check the results against what the paper reported, write a run crate linked to the paper.

Follow-up checks of a reproduction that differs are in followup.py; they run through ``execute`` too.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .compare import Comparison, compare_results
from .crate import CrateError, MethodSpec, _coerce, load_crate
from .model import build_ladder
from .overlay import make_overlays
from .profiles import CheckReport, get_profile
from .runcrate import write_run_crate
from .runners import get_runner


OVERLAY_LIMIT = 48  # overlays written per run, for the per-image review


@dataclass
class RunOutcome:
    run_dir: Path
    check: CheckReport | None
    comparison: Comparison | None
    params: dict[str, Any]
    ladder: dict[str, dict[str, str]] | None = None


def _spec(crate: MethodSpec | str | Path, method: str | None = None) -> MethodSpec:
    return crate if isinstance(crate, MethodSpec) else load_crate(crate, method)


def _new_run_dir(runs_dir: Path, mode: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")[:-3]
    d = Path(runs_dir) / f"{stamp}-{mode}"
    d.mkdir(parents=True, exist_ok=False)
    return d


def no_data_dir(runs_dir: Path) -> Path:
    """An empty input folder for methods that take only values (the script contract still passes --input_dir)."""
    d = Path(runs_dir) / ".no-input"
    d.mkdir(parents=True, exist_ok=True)
    return d


def check_inputs(crate: MethodSpec | str | Path, data: str | Path | None,
                 params: dict[str, Any] | None = None, method: str | None = None,
                 pixel_ratio: float | None = None) -> CheckReport:
    from .profiles import OK, CheckItem

    spec = _spec(crate, method)
    if not spec.needs_data:
        report = CheckReport(profile=spec.profile, data_path=Path(data or "."))
        report.items.append(CheckItem("Input data", "none: this method takes parameter values only", "-", OK))
        return report
    if data is None:
        raise CrateError("This method needs input data: give a file or folder.")
    return get_profile(spec.profile).check(spec, Path(data), params, pixel_ratio=pixel_ratio)


def latest_run(runs_dir: Path, spec: MethodSpec, mode: str = "reproduce") -> Path | None:
    """The newest run of this method in ``runs_dir`` with this mode (e.g. the reproduction to compare a reuse to)."""
    from .runcrate import summarise_run

    method = spec.method_name or (spec.workflow_path.stem if spec.workflow_path else "")
    for meta in sorted(Path(runs_dir).glob("*/ro-crate-metadata.json"), reverse=True):
        s = summarise_run(meta.parent)
        if s["mode"] == mode and s["method"] == method and s["based_on"] == spec.identifier:
            return meta.parent
    return None


def execute(spec: MethodSpec, data: Path, mode: str, runs_dir: Path, runner: str, agent: str,
            params: dict[str, Any], check: CheckReport | None, compare: bool, channel: int = 0,
            progress=None, param_notes: dict[str, str] | None = None, rounded: bool = False,
            followup: dict[str, Any] | None = None, pixel_ratio: float | None = None,
            purpose: str | None = None) -> RunOutcome:
    """Run, compare or check the results, and write the run crate. ``followup``: the follow-up check this run is."""
    if spec.workflow_path is None or not spec.workflow_path.exists():
        raise CrateError("The crate's workflow file is missing.")
    run = get_runner(runner, spec.workflow_language, spec.workflow_path)
    if run.name in {"script", "notebook"}:
        from .env import env_python

        run.python = env_python(spec.path.parent)  # the research object's own environment, when built
    with tempfile.TemporaryDirectory(prefix="recrate-") as tmp:
        work = Path(tmp)
        result = run.run(spec.workflow_path, data, work / "out", params, channel=channel, progress=progress)
        overlays = (make_overlays(data, result.masks_dir, work / "overlays", channel, limit=OVERLAY_LIMIT)
                    if result.masks_dir else [])
        figures = sorted(f for f in (work / "out" / "figures").glob("*")  # made by the paper's own plotting code
                         if f.suffix.lower() in {".png", ".svg", ".pdf", ".jpg"}) if (work / "out" / "figures").is_dir() else []
        comparison = None
        from .model import COMPARED_KINDS

        if compare and spec.expected_results and spec.expected_results.exists() and spec.output_kind in COMPARED_KINDS:
            comparison = compare_results(result.results_csv, spec.expected_results, spec.tolerances(),
                                         spec.abs_tolerances(), {t.column: t.source for t in spec.tolerance_specs()},
                                         rounded=rounded)
        if mode == "reuse" and check is not None:  # are the results in the range the paper reported?
            ref = latest_run(runs_dir, spec)
            get_profile(spec.profile).check_results(spec, result.results_csv, check,
                                                    ref / "outputs" / "results.csv" if ref else None, pixel_ratio)
        ladder = build_ladder(spec, comparison, ran=True) if compare else None
        run_dir = _new_run_dir(runs_dir, mode)
        write_run_crate(run_dir, spec, mode, data, result, check, comparison, agent=agent, overlays=overlays,
                        param_notes=param_notes, ladder=ladder, figures=figures, followup=followup,
                        purpose=purpose)
    return RunOutcome(run_dir=run_dir, check=check, comparison=comparison, params=result.params, ladder=ladder)


def reproduce(crate: MethodSpec | str | Path, runs_dir: str | Path = "runs", runner: str = "auto",
              agent: str = "anonymous", method: str | None = None, progress=None) -> RunOutcome:
    """Run on the paper's own data and compare with its reported results."""
    spec = _spec(crate, method)
    data = spec.example_data if spec.needs_data else no_data_dir(Path(runs_dir))
    if data is None or not data.exists():
        raise CrateError("The crate has no example data to reproduce on.")
    check = check_inputs(spec, data)
    return execute(spec, data, "reproduce", Path(runs_dir), runner, agent,
                   spec.default_params(), check, compare=True, channel=0, progress=progress)


def run_on_data(crate: MethodSpec | str | Path, data: str | Path | None, runs_dir: str | Path = "runs",
                runner: str = "auto", agent: str = "anonymous", auto_adjust: bool = True,
                channel: int = 0, params: dict[str, Any] | None = None,
                allow_warnings: bool = True, method: str | None = None,
                overrides: dict[str, tuple[Any, str]] | None = None, progress=None,
                pixel_ratio: float | None = None) -> RunOutcome:
    """Check your data against the paper's inputs, then run and record.

    ``overrides`` maps a parameter to ``(value, reason)``: a person's choice. It wins over the
    automatic adjustment and is recorded with its reason in the run crate. ``pixel_ratio``: your pixel size
    divided by the research's, when the research states none.
    """
    spec = _spec(crate, method)
    if not spec.needs_data:
        data = no_data_dir(Path(runs_dir))
    base = {**spec.default_params(), **(params or {})}
    check = check_inputs(spec, data, base, pixel_ratio=pixel_ratio)
    if not check.can_run:
        failed = [f"{i.name}: {i.note}" for i in check.items if i.status == "fail"]
        raise CrateError("Your data does not fit this method:\n- " + "\n- ".join(failed))
    if check.status == "warn" and not allow_warnings:
        raise CrateError("Checks raised warnings; review them or pass allow_warnings=True.")
    used = check.adjusted_params(base) if auto_adjust else base
    if not auto_adjust and check.adjustments:
        names = {a.parameter for a in check.adjustments}
        for item in check.items:
            if item.status == "warn" and any(n in item.note for n in names):
                item.note += " (proposed, not applied: results may not be comparable)"
        check.adjustments = []
    notes: dict[str, str] = {}
    types = {p.name: p.type for p in spec.value_inputs}
    for name, (value, reason) in (overrides or {}).items():
        if name not in types:
            raise CrateError(f"{spec.method_name} has no parameter {name!r}. Parameters: {', '.join(types)}")
        value = _coerce(value, types[name])
        auto = next((a for a in check.adjustments if a.parameter == name), None)
        if auto is not None:
            check.adjustments.remove(auto)
        used[name] = value
        notes[name] = (f"Set to {value} by {agent} (paper: {spec.default_params().get(name)}"
                       + (f"; automatic adjustment: {auto.new}" if auto else "") + f"). Reason: {reason}")
    question = [f"{n} {spec.default_params().get(n)} → {used[n]} ({r})" for n, (v, r) in (overrides or {}).items()
                if any(p.name == n and str(p.prop("changes_question")).lower() == "true" for p in spec.value_inputs)]
    purpose = ("Method reused for a different question: " + "; ".join(question)) if question else None
    return execute(spec, Path(data), "reuse", Path(runs_dir), runner, agent, used, check,
                   compare=False, channel=channel, progress=progress, param_notes=notes, pixel_ratio=pixel_ratio,
                   purpose=purpose)
