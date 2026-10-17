"""MCP server: lets a chat agent (Claude Desktop, Claude Code, ...) use ReCRATE.

The agent never computes results. It chooses, checks and explains; the tools
run the declared workflow and write the records.

Run:   recrate-mcp            (stdio transport)

Claude Desktop config (claude_desktop_config.json):
    {"mcpServers": {"recrate": {"command": "recrate-mcp",
                                "env": {"RECRATE_RUNS_DIR": "/path/to/runs"}}}}
Claude Code:
    claude mcp add recrate -- recrate-mcp

Typical conversation:
    "Reproduce the result from the crate in examples/demo/paper_crate."
    "Now check my images in examples/demo/my_images and run it on them."
    "Write the methods paragraph for that run."
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

try:  # MCP Python SDK 2.x
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:  # MCP Python SDK 1.x
    from mcp.server.fastmcp import FastMCP

from .crate import load_crate
from .draft import build_crate
from .methods import methods_paragraph
from .pipeline import check_inputs as _check
from .pipeline import reproduce as _reproduce
from .pipeline import run_on_data as _run
from .record import find_runs, headline, publish_back, summarise_record
from .runcrate import approve as _approve

RUNS_DIR = Path(os.environ.get("RECRATE_RUNS_DIR", "runs"))
RUNNER = os.environ.get("RECRATE_RUNNER", "auto")

mcp = FastMCP("recrate", instructions=(
    "ReCRATE reproduces published methods and reuses them on new data, with checks and provenance. "
    "Use inspect_crate first to see what the method expects. Before running on a user's data, call "
    "check_inputs and explain any warning (for example a pixel size difference and the parameter "
    "rescaling it implies) before calling run_on_my_data. Never invent numbers: report what the tools return."
))


@mcp.tool()
def inspect_crate(crate_path: str) -> dict[str, Any]:
    """Describe a published method from its input RO-Crate: paper, workflow, software,
    expected input data (format, pixel size, channels), parameters and tolerances."""
    return load_crate(crate_path).summary()


@mcp.tool()
def check_inputs(crate_path: str, data_path: str) -> dict[str, Any]:
    """Check whether the user's data fits the method, using the crate's domain profile.
    Returns each check (ok / warn / fail) and any parameter adjustments it implies."""
    r = _check(crate_path, data_path)
    return {"status": r.status, "can_run": r.can_run, "checks": r.as_rows(),
            "adjustments": [a.__dict__ for a in r.adjustments]}


@mcp.tool()
def reproduce(crate_path: str, who: str = "anonymous") -> dict[str, Any]:
    """Run the method on the paper's own data and compare with the paper's reported results.
    Writes a Workflow Run RO-Crate and returns the verdict."""
    out = _reproduce(crate_path, RUNS_DIR, runner=RUNNER, agent=who)
    c = out.comparison
    return {"verdict": ("reproduced" if c and c.reproduced else "differs") if c and c.measures else "not compared",
            "headline": c.headline() if c else None, "run_crate": str(out.run_dir)}


@mcp.tool()
def run_on_my_data(crate_path: str, data_path: str, who: str = "anonymous", auto_adjust: bool = True,
                   channel: int = 0) -> dict[str, Any]:
    """Run the method on the user's data after checking it. Applies and records adjustments
    (for example rescaling size parameters for a different pixel size). Stops if a check fails."""
    out = _run(crate_path, data_path, RUNS_DIR, runner=RUNNER, agent=who, auto_adjust=auto_adjust,
               channel=channel)
    results = (out.run_dir / "outputs" / "results.csv").read_text(encoding="utf-8")
    return {"checks": out.check.as_rows() if out.check else [], "parameters_used": out.params,
            "results_csv": results, "run_crate": str(out.run_dir),
            "overlays": [str(p) for p in sorted((out.run_dir / "outputs" / "overlays").glob("*.png"))]}


@mcp.tool()
def approve_run(run_crate: str, who: str, accepted: bool = True, note: str = "") -> str:
    """Record that a person looked at the overlays and accepted (or rejected) the result."""
    _approve(Path(run_crate), who, accepted, note)
    return f"Recorded {'approval' if accepted else 'rejection'} by {who}."


@mcp.tool()
def methods_text(run_crate: str) -> str:
    """Write a methods paragraph from a run crate (software, parameters, calibration, adjustments)."""
    return methods_paragraph(Path(run_crate))


@mcp.tool()
def publish_run(crate_path: str, run_crate: str) -> dict[str, Any]:
    """Add a run to the paper's reproducibility record and zip it for deposit."""
    return publish_back(load_crate(crate_path), Path(run_crate))["summary"]


@mcp.tool()
def reproducibility_record(crate_path: str) -> dict[str, Any]:
    """Every recorded run based on this paper's crate, with a one-line summary."""
    runs = find_runs(load_crate(crate_path), RUNS_DIR)
    s = summarise_record(runs)
    return {"headline": headline(s), "summary": s, "runs": runs}


@mcp.tool()
def draft_crate(description_json: str, out_dir: str) -> dict[str, Any]:
    """Write an input crate for a paper that has none. First read the paper and its code, then
    pass a JSON description (see recrate.draft for the format: name, workflow, inputs with
    pixel size / channels / dimensions, parameters with defaults and scaling, outputs with
    tolerances). The crate is marked as a draft; ask the user to confirm it."""
    desc = json.loads(description_json)
    desc.setdefault("draft", True)
    path = build_crate(desc, out_dir)
    return {"crate": str(path), "summary": load_crate(path).summary()}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
