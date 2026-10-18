"""A paper's reproducibility record: every run that is based on its crate.

Each run crate says ``isBasedOn`` the paper's crate. Collecting those gives the
paper a growing, checkable history: reproduced N times, on which data, by whom,
with which adjustments.

``publish_back`` registers a run in a ``reproducibility-record.json`` next to the
paper's crate and zips the run crate for deposit. In a real deployment the zip
goes to Zenodo (or a WorkflowHub collection) with ``isBasedOn`` pointing at the
paper's crate DOI; see docs/architecture.md.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .crate import MethodSpec
from .runcrate import summarise_run

RECORD_FILE = "reproducibility-record.json"
VALIDATION_AGENT = "ReCRATE validation"


def find_runs(spec: MethodSpec, runs_dir: Path) -> list[dict[str, Any]]:
    runs_dir = Path(runs_dir)
    if not runs_dir.exists():
        return []
    out = []
    for meta in sorted(runs_dir.glob("*/ro-crate-metadata.json")):
        s = summarise_run(meta.parent)
        if s["based_on"] == spec.identifier:
            out.append(s)
    return out


def summarise_record(runs: list[dict[str, Any]]) -> dict[str, Any]:
    reproduce = [r for r in runs if r["mode"] == "reproduce"]
    reuse = [r for r in runs if r["mode"] == "reuse"]
    return {
        "runs": len(runs),
        "reproduced": sum(1 for r in reproduce if r["verdict"] == "reproduced"),
        "differed": sum(1 for r in reproduce if r["verdict"] == "differs"),
        "reused_on_new_data": len(reuse),
        "follow_up_checks": sum(1 for r in runs if r["mode"] == "follow-up"),
        "people": sorted({r["by"] for r in runs if r["by"] and r["by"] != VALIDATION_AGENT}),
        "methods": sorted({r["method"] for r in runs if r.get("method")}),
        "validation_runs": sum(1 for r in runs if r["by"] == VALIDATION_AGENT),
        "with_adjustments": sum(1 for r in runs if r["adjustments"]),
        "approved": sum(1 for r in runs if r["approved"] == "accepted"),
    }


def headline(summary: dict[str, Any]) -> str:
    return (f"Reproduced {summary['reproduced']} time(s), differed {summary['differed']}, "
            f"reused on new data {summary['reused_on_new_data']} time(s), "
            f"{summary.get('follow_up_checks', 0)} follow-up check(s), "
            f"by {len(summary['people'])} person(s).")


def publish_back(spec: MethodSpec, run_dir: Path, deposit_dir: Path | None = None) -> dict[str, Any]:
    """Register a run in the paper's record and zip the run crate for deposit."""
    run_dir = Path(run_dir)
    summary = summarise_run(run_dir)
    if summary["based_on"] != spec.identifier:
        raise ValueError("This run is based on a different crate.")
    deposit_dir = Path(deposit_dir or run_dir.parent / "deposits")
    deposit_dir.mkdir(parents=True, exist_ok=True)
    archive = shutil.make_archive(str(deposit_dir / run_dir.name), "zip", run_dir)

    record_path = spec.path / RECORD_FILE
    record = json.loads(record_path.read_text(encoding="utf-8")) if record_path.exists() else {
        "paper_crate": spec.identifier, "paper": spec.citation, "runs": []}
    record["runs"] = [r for r in record["runs"] if r["run"] != summary["run"]]
    record["runs"].append({**summary, "archive": archive})
    record["summary"] = summarise_record(record["runs"])
    record_path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    return record
