"""Serve a paper agent over MCP, so a chat agent becomes an expert on the paper.

    recrate serve paper_agents/my_paper          # stdio

Connect from Claude Code:
    claude mcp add my-paper -- recrate serve /abs/path/paper_agents/my_paper
or Claude Desktop (claude_desktop_config.json):
    {"mcpServers": {"my-paper": {"command": "recrate",
                                 "args": ["serve", "/abs/path/paper_agents/my_paper"]}}}

What the agent gets, mirroring Paper2Agent's tools / resources / prompts:

* **Resources**: the paper's text and metadata, the input crate, one card per method.
* **Tools**, in four groups:
  read the paper (``about_paper``, ``search_paper``, ``describe_method``, tables and figures);
  check and run (``check_my_data``, ``reproduce``, ``run_on_my_data``, one ``run_<method>`` per method,
  ``followup_checks``, ``run_followup_check``);
  figures (``make_figure``);
  record (``review_conclusion``, ``approve_run``, ``methods_text``, ``publish_run``, ``reproducibility_record``,
  ``draft_crate``).

Tools that produce files return ``{"message", "artifacts": [{"description", "path"}]}`` with absolute paths (the
Paper2Agent convention), so a client can show outputs and figures. Each run writes a fresh run crate folder.
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
    from mcp.server.mcpserver import Image
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:  # MCP Python SDK 1.x
    from mcp.server.fastmcp import FastMCP, Image

from .agent import PaperAgent, review_conclusion, revalidate
from .draft import build_crate
from .methods import methods_paragraph
from .pipeline import check_inputs, reproduce, run_on_data
from .record import find_runs, headline, publish_back, summarise_record
from .runcrate import approve

RUNNER = os.environ.get("RECRATE_RUNNER", "auto")


def _artifacts(*items: tuple[str, Path]) -> list[dict[str, str]]:
    return [{"description": d, "path": str(Path(p).resolve())} for d, p in items if Path(p).exists()]


def _run_artifacts(run_dir: Path) -> list[dict[str, str]]:
    run_dir = Path(run_dir)
    items = [("Run crate (Workflow Run RO-Crate folder)", run_dir),
             ("Results table", run_dir / "outputs" / "results.csv"),
             ("Comparison with the published values", run_dir / "comparison.csv")]
    items += [(f"Figure: {f.stem}", f) for f in sorted((run_dir / "outputs" / "figures").glob("*"))]
    items += [(f"Overlay: {f.stem}", f) for f in sorted((run_dir / "outputs" / "overlays").glob("*.png"))[:12]]
    return _artifacts(*items)


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
        "Never invent numbers: report what the tools return. Each run is recorded as a run crate. "
        "Never mark the 'same conclusion' step yourself: ask the user and record their answer with review_conclusion."
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
    def check_my_data(method: str, data_path: str, pixel_ratio: float | None = None) -> dict[str, Any]:
        """pixel_ratio: the user's pixel size / the research's, only when the research states none."""
        r = check_inputs(agent.spec(method), data_path, pixel_ratio=pixel_ratio)
        return {"status": r.status, "can_run": r.can_run, "checks": r.as_rows(),
                "adjustments": [a.__dict__ for a in r.adjustments]}

    @mcp.tool(name="reproduce", description="Run a method on the paper's own data and compare with the "
                                           "paper's reported results. Returns the verdict ladder (materials found, "
                                           "runs, same numbers, same conclusion) and the run crate's files.")
    def reproduce_tool(method: str, who: str = "anonymous") -> dict[str, Any]:
        from .agent import record_reproduction

        out = reproduce(agent.spec(method), _runs(), runner=RUNNER, agent=who)
        if record_reproduction(agent.path, method, out):
            agent.methods[:] = PaperAgent.load(agent.path).methods
        c = out.comparison
        headline = c.headline() if c else "No published results to compare with."
        return {"message": headline, "artifacts": _run_artifacts(out.run_dir),
                "verdict": ("reproduced" if c.reproduced else "differs") if c and c.measures else "not compared",
                "same_numbers": c.count if c and c.measures else None, "ladder": out.ladder,
                "headline": headline, "run_crate": str(out.run_dir.resolve())}

    def _run(method: str, data_path: str, who: str, auto_adjust: bool, channel: int,
             parameters: dict[str, Any] | None, pixel_ratio: float | None = None) -> dict[str, Any]:
        from .runcrate import summarise_run

        out = run_on_data(agent.spec(method), data_path, _runs(), runner=RUNNER, agent=who,
                          auto_adjust=auto_adjust, channel=channel, params=parameters, pixel_ratio=pixel_ratio)
        return {"message": summarise_run(out.run_dir)["headline"], "artifacts": _run_artifacts(out.run_dir),
                "checks": out.check.as_rows() if out.check else [], "parameters_used": out.params,
                "results_csv": (out.run_dir / "outputs" / "results.csv").read_text(encoding="utf-8"),
                "run_crate": str(out.run_dir.resolve())}

    @mcp.tool(description="Run a method of the paper on the user's data after checking it. Applies and records "
                          "adjustments (e.g. rescaling size parameters for a different pixel size); stops if a "
                          "check fails. 'parameters' overrides defaults.")
    def run_on_my_data(method: str, data_path: str, who: str = "anonymous", auto_adjust: bool = True,
                       channel: int = 0, parameters: dict[str, Any] | None = None,
                       pixel_ratio: float | None = None) -> dict[str, Any]:
        return _run(method, data_path, who, auto_adjust, channel, parameters, pixel_ratio)

    # One tool per method, named after it, like Paper2Agent's paper tools
    for m in agent.methods:
        def make(method_id: str):
            def method_tool(data_path: str, who: str = "anonymous", auto_adjust: bool = True,
                            channel: int = 0, parameters: dict[str, Any] | None = None,
                            pixel_ratio: float | None = None) -> dict[str, Any]:
                return _run(method_id, data_path, who, auto_adjust, channel, parameters, pixel_ratio)
            return method_tool

        status = "validated against the paper" if m.status == "validated" else f"status: {m.status}"
        mcp.tool(name=_tool_name(m.name),
                 description=f"{m.name} from the paper ({status}). {m.description} "
                             "Runs on the user's data with input checks and a recorded run crate.")(make(m.id))

    # ---- the paper's tables and figures (reading package) ---------------------------------
    @mcp.tool(description="The paper's tables: label, caption and file. Read one with get_table.")
    def list_tables() -> list[dict[str, Any]]:
        return agent.tables()

    @mcp.tool(description="One of the paper's tables as CSV (cells as printed), by label (e.g. 'Table 2') or file.")
    def get_table(table: str) -> str:
        t = next((x for x in agent.tables() if table.lower() in (x["label"].lower(), x["file"].lower(),
                                                                Path(x["file"]).stem.lower())), None)
        if t is None:
            return f"No table {table!r}. Tables: {', '.join(x['label'] for x in agent.tables()) or 'none'}"
        return f"{t['label']}: {t['caption']}\n\n" + (agent.path / "paper" / t["file"]).read_text(encoding="utf-8")

    @mcp.tool(description="The paper's figures: label, caption and file. Look at one with get_figure.")
    def list_figures() -> list[dict[str, Any]]:
        return agent.figures()

    @mcp.tool(description="One of the paper's figures, as an image, by label (e.g. 'Figure 3') or file.")
    def get_figure(figure: str):
        f = next((x for x in agent.figures() if figure.lower() in (x["label"].lower(), x["file"].lower(),
                                                                  Path(x["file"]).stem.lower())), None)
        if f is None:
            return f"No figure {figure!r}. Figures: {', '.join(x['label'] for x in agent.figures()) or 'none'}"
        return [f"{f['label']}: {f['caption']}", Image(path=str(agent.path / "paper" / f["file"]))]

    # ---- when results differ: follow-up checks ------------------------------------------
    @mcp.tool(description="Follow-up checks for a method whose numbers differ from the paper's (inclusion rules, "
                          "rounding convention, software and data versions), and those already run. Each check "
                          "is its own recorded run; the original result is never replaced.")
    def followup_checks(method: str) -> dict[str, Any]:
        from .followup import followups_of, propose

        m = next((x for x in agent.methods if method in (x.id, x.name)), None)
        done = followups_of(_runs(), m.run_crate) if m and m.run_crate else []
        return {"proposed": [f.as_dict() for f in propose(agent.spec(method))],
                "already_run": [{k: r[k] for k in ("run", "followup", "headline", "when", "by")} for r in done]}

    @mcp.tool(description="Run one follow-up check (by id, from followup_checks) on the paper's data. Writes its "
                          "own run crate, linked to the reproduction it follows up.")
    def run_followup_check(method: str, check: str, who: str = "anonymous") -> dict[str, Any]:
        from .followup import run_followup

        from .crate import CrateError

        try:
            out = run_followup(agent.spec(method), check, _runs(), who=who, runner=RUNNER)
        except CrateError as e:  # not runnable here, or no reproduction yet: say what it needs
            return {"message": str(e), "ran": False, "artifacts": []}
        from .runcrate import summarise_run

        c = out.comparison
        return {"message": summarise_run(out.run_dir)["headline"], "ran": True,
                "same_numbers": c.count if c else None,
                "artifacts": _run_artifacts(out.run_dir), "run_crate": str(out.run_dir.resolve())}

    # ---- figures ------------------------------------------------------------------------
    @mcp.tool(description="Make a figure from a run's outputs (never drawn by the model) and record it in the run "
                          "crate. kind: published_vs_reproduced (one row per value: published ring, reproduced "
                          "dot, values outside tolerance marked).")
    def make_figure(run_crate: str, kind: str = "published_vs_reproduced") -> dict[str, Any]:
        from .figures import make_run_figure

        path = make_run_figure(Path(run_crate), kind)
        return {"message": f"Figure '{kind}' plotted from {Path(run_crate).name} and recorded in its run crate.",
                "artifacts": _artifacts((kind.replace("_", " "), path))}

    # ---- records ----------------------------------------------------------------------
    @mcp.tool(name="review_conclusion", description="Record a person's answer to the last step of the verdict ladder for a method: does its "
                          "reproduction support the paper's conclusion? agrees: yes, partly or no. Only a person "
                          "may set this: ask the user, never answer it yourself.")
    def review_conclusion_tool(method: str, who: str, agrees: str, note: str = "") -> dict[str, Any]:
        m = review_conclusion(agent.path, method, who, agrees, note)
        agent.methods[:] = PaperAgent.load(agent.path).methods
        return {"message": f"Recorded: {m.ladder['conclusion']['reason']}", "ladder": m.ladder}

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
        record = publish_back(agent.spec(method), Path(run_crate))
        archive = next(r["archive"] for r in record["runs"] if r["run"] == Path(run_crate).name)
        return {"message": headline(record["summary"]), "summary": record["summary"],
                "artifacts": _artifacts(("Run crate, zipped for deposit", Path(archive)))}

    @mcp.tool(description="The paper's reproducibility record: every run based on its crate, by whom, "
                          "on what data, with which adjustments and verdicts.")
    def reproducibility_record() -> dict[str, Any]:
        if not agent.has_methods:
            return {"headline": "No runnable method yet, so no runs.", "runs": []}
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
