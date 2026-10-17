"""Serve a paper agent over MCP, so a chat agent becomes an expert on the paper.

    recrate serve paper_agents/my_paper          # stdio

Connect from Claude Code:
    claude mcp add my-paper -- recrate serve /abs/path/paper_agents/my_paper
or Claude Desktop (claude_desktop_config.json):
    {"mcpServers": {"my-paper": {"command": "recrate",
                                 "args": ["serve", "/abs/path/paper_agents/my_paper"]}}}

What the agent gets, mirroring Paper2Agent's tools / resources / prompts:

* **Resources**: the paper's text and metadata, the input crate, one card per method.
* **Tools**: ask the paper (``search_paper``), one tool per method of the paper
  (``run_<method>``), plus ``check_my_data``, ``reproduce``, ``run_on_my_data``,
  ``methods_text``, ``approve_run``, ``publish_run``, ``reproducibility_record``,
  ``draft_crate``.
* **Prompts**: reproduce the paper, apply a method to my data.

The agent never computes results: the methods run deterministically and every
run is written as a Workflow Run RO-Crate linked to the paper.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

try:  # MCP Python SDK 2.x
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:  # MCP Python SDK 1.x
    from mcp.server.fastmcp import FastMCP

from .agent import PaperAgent, revalidate
from .draft import build_crate
from .methods import methods_paragraph
from .pipeline import check_inputs, reproduce, run_on_data
from .record import find_runs, headline, publish_back, summarise_record
from .runcrate import approve

RUNNER = os.environ.get("RECRATE_RUNNER", "auto")


def _tool_name(method: str) -> str:
    return "run_" + (re.sub(r"[^a-z0-9]+", "_", method.lower()).strip("_") or "method")


def build_server(agent_dir: str | Path) -> FastMCP:
    agent = PaperAgent.load(agent_dir)
    title = agent.manifest.get("title", agent.name)
    method_lines = "\n".join(f"- {m.name} ({m.status}): {m.description}" for m in agent.methods) or \
        "- none yet (paper-only agent: answer from the paper; offer to draft a crate)"
    mcp = FastMCP(f"paper-{agent.name}"[:60], instructions=(
        f"You are an interactive expert on the paper '{title}'"
        f"{' (' + agent.manifest['doi'] + ')' if agent.manifest.get('doi') else ''}. "
        "Answer questions from the paper using search_paper and cite the passages. "
        "Its methods are available as tools:\n" + method_lines + "\n"
        "Before running a method on the user's data, call check_my_data and explain every warning "
        "(for example a pixel size difference and the parameter rescaling it implies). "
        "Never invent numbers: report what the tools return. Each run is recorded as a run crate."
    ))

    def _runs() -> Path:
        return agent.runs_dir

    # ---- resources ------------------------------------------------------------
    @mcp.resource("paper://text", name="paper-text", description="Full text of the paper", mime_type="text/markdown")
    def paper_text() -> str:
        return agent.paper.text if agent.paper else "No paper text in this agent."

    @mcp.resource("paper://info", name="paper-info", description="Title, DOI, code links, methods and validation",
                  mime_type="application/json")
    def paper_info() -> str:
        return json.dumps(agent.card(), indent=2, ensure_ascii=False)

    @mcp.resource("paper://crate", name="input-crate", description="The paper's input RO-Crate (JSON-LD)",
                  mime_type="application/ld+json")
    def crate_json() -> str:
        if agent.crate_dir is None:
            return "{}"
        return (agent.crate_dir / "ro-crate-metadata.json").read_text(encoding="utf-8")

    # ---- asking the paper -----------------------------------------------------
    @mcp.tool(description="Overview of the paper agent: title, DOI, crate source, methods and whether each "
                          "was validated against the paper's reported results.")
    def about_paper() -> dict[str, Any]:
        return agent.card()

    @mcp.tool(description="Search the paper's text and return the most relevant passages (for questions about "
                          "methods, parameters, data and reported results).")
    def search_paper(query: str, k: int = 5) -> list[dict[str, Any]]:
        return agent.search(query, k)

    @mcp.tool(description="Describe one method: what it expects as input (format, pixel size, channels), "
                          "its parameters and defaults, the software, and the tolerances used for checking.")
    def describe_method(method: str) -> dict[str, Any]:
        return agent.spec(method).summary()

    # ---- running the paper's methods ------------------------------------------------
    @mcp.tool(description="Check whether the user's data fits a method, using the crate's domain profile. "
                          "Returns ok / warn / fail per check and any parameter adjustments.")
    def check_my_data(method: str, data_path: str) -> dict[str, Any]:
        r = check_inputs(agent.spec(method), data_path)
        return {"status": r.status, "can_run": r.can_run, "checks": r.as_rows(),
                "adjustments": [a.__dict__ for a in r.adjustments]}

    @mcp.tool(name="reproduce", description="Run a method on the paper's own data and compare with the "
                                           "paper's reported results. Writes a run crate.")
    def reproduce_tool(method: str, who: str = "anonymous") -> dict[str, Any]:
        out = reproduce(agent.spec(method), _runs(), runner=RUNNER, agent=who)
        c = out.comparison
        return {"verdict": ("reproduced" if c.reproduced else "differs") if c and c.measures else "not compared",
                "headline": c.headline() if c else None, "run_crate": str(out.run_dir)}

    def _run(method: str, data_path: str, who: str, auto_adjust: bool, channel: int,
             parameters: dict[str, Any] | None) -> dict[str, Any]:
        out = run_on_data(agent.spec(method), data_path, _runs(), runner=RUNNER, agent=who,
                          auto_adjust=auto_adjust, channel=channel, params=parameters)
        return {"checks": out.check.as_rows() if out.check else [], "parameters_used": out.params,
                "results_csv": (out.run_dir / "outputs" / "results.csv").read_text(encoding="utf-8"),
                "run_crate": str(out.run_dir),
                "overlays": [str(p) for p in sorted((out.run_dir / "outputs" / "overlays").glob("*.png"))]}

    @mcp.tool(description="Run a method of the paper on the user's data after checking it. Applies and records "
                          "adjustments (e.g. rescaling size parameters for a different pixel size); stops if a "
                          "check fails. 'parameters' overrides defaults.")
    def run_on_my_data(method: str, data_path: str, who: str = "anonymous", auto_adjust: bool = True,
                       channel: int = 0, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
        return _run(method, data_path, who, auto_adjust, channel, parameters)

    # One tool per method, named after it, like Paper2Agent's paper tools
    for m in agent.methods:
        def make(method_id: str):
            def method_tool(data_path: str, who: str = "anonymous", auto_adjust: bool = True,
                            channel: int = 0, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
                return _run(method_id, data_path, who, auto_adjust, channel, parameters)
            return method_tool

        status = "validated against the paper" if m.status == "validated" else f"status: {m.status}"
        mcp.tool(name=_tool_name(m.name),
                 description=f"{m.name} from the paper ({status}). {m.description} "
                             "Runs on the user's data with input checks and a recorded run crate.")(make(m.id))

    # ---- records ----------------------------------------------------------------------
    @mcp.tool(description="Record that a person looked at a run's overlays and accepted or rejected the result.")
    def approve_run(run_crate: str, who: str, accepted: bool = True, note: str = "") -> str:
        approve(Path(run_crate), who, accepted, note)
        return f"Recorded {'approval' if accepted else 'rejection'} by {who}."

    @mcp.tool(description="Write a methods paragraph from a run crate (software, parameters, calibration, "
                          "adjustments), ready to paste into a paper.")
    def methods_text(run_crate: str) -> str:
        return methods_paragraph(Path(run_crate))

    @mcp.tool(description="Add a run to the paper's reproducibility record and zip it for deposit.")
    def publish_run(run_crate: str, method: str | None = None) -> dict[str, Any]:
        return publish_back(agent.spec(method), Path(run_crate))["summary"]

    @mcp.tool(description="The paper's reproducibility record: every run based on its crate, by whom, "
                          "on what data, with which adjustments and verdicts.")
    def reproducibility_record() -> dict[str, Any]:
        if agent.crate_dir is None:
            return {"headline": "No crate yet, so no runs.", "runs": []}
        runs = find_runs(agent.spec(), _runs())
        s = summarise_record(runs)
        return {"headline": headline(s), "summary": s, "runs": runs}

    @mcp.tool(description="For a paper without a crate: after reading the paper (search_paper) and its code, "
                          "pass a JSON description of its methods (format: recrate.draft) to write the crate "
                          "and validate it. Paths are relative to the agent folder (e.g. code/script.py). "
                          "The crate is marked as a draft; ask the user to confirm it. Restart the server to "
                          "get the new method tools.")
    def draft_crate(description_json: str) -> dict[str, Any]:
        desc = json.loads(description_json)
        desc.setdefault("draft", True)
        build_crate(desc, agent.path / "crate", base_dir=agent.path)
        new = revalidate(agent.path, runner=RUNNER)
        return new.card()

    # ---- prompts -------------------------------------------------------------------
    @mcp.prompt(description="Reproduce the paper's results with its own data and explain the verdicts.")
    def reproduce_paper() -> str:
        return (f"Reproduce the results of '{title}'. For each method, call reproduce, then summarise "
                "each verdict against the numbers in the paper (use search_paper to quote them).")

    @mcp.prompt(description="Apply one of the paper's methods to my data, with checks.")
    def apply_to_my_data(data_path: str, method: str = "") -> str:
        which = f"the method '{method}'" if method else "the most suitable method (ask me if unclear)"
        return (f"Apply {which} from '{title}' to my data at {data_path}. First describe_method, then "
                "check_my_data and explain every warning and adjustment. Ask before running if a check "
                "fails. Then run it, show the results and overlays, and offer the methods paragraph.")

    return mcp


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    target = argv[0] if argv else os.environ.get("RECRATE_AGENT")
    if not target:
        raise SystemExit("usage: recrate-mcp <paper agent folder>   (or set RECRATE_AGENT)")
    build_server(target).run()


if __name__ == "__main__":
    main()
