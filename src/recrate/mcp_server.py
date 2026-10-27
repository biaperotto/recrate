"""Serve a paper agent over MCP, so a chat agent becomes an expert on the paper.

    recrate serve paper_agents/my_paper                       # stdio: what clients launch themselves
    recrate serve paper_agents/my_paper --http --port 8765    # streamable HTTP: http://127.0.0.1:8765/mcp

Register it with a client (absolute paths, checked after installing; see install.py):
    recrate install-mcp paper_agents/my_paper --client desktop    # Claude Desktop
    recrate install-mcp paper_agents/my_paper --client project    # .mcp.json: Claude Code, VS Code extension
    recrate install-mcp paper_agents/my_paper                     # print the JSON for any other client

What the agent gets, mirroring Paper2Agent's tools / resources / prompts:

* **Resources**: the paper's text and metadata, the input crate, one card per method.
* **Tools**, in four groups:
  read the paper (``about_paper``, ``search_paper``, ``describe_method``, tables and figures);
  check and run (``check_my_data``, ``reproduce``, ``run_on_my_data``, one ``run_<method>`` per method,
  ``followup_checks``, ``run_followup_check``);
  figures (``make_figure``: the published-vs-reproduced chart, or a bar, scatter, line, box or histogram of any
  column of a run's table, the paper's tables or the user's data, drawn by code and recorded with its data);
  record (``review_conclusion``, ``approve_run``, ``methods_text``, ``publish_run``, ``reproducibility_record``,
  ``report_page``, ``export_crate``, ``draft_crate``).

Tools that produce files return ``{"message", "artifacts": [{"description", "path"}]}`` with absolute paths (the
Paper2Agent convention), so a client can show outputs and figures; a run's figures are also returned inline as
images (``RECRATE_MCP_IMAGES``, default 4), so a client without file access sees them. Each run writes a fresh run
crate folder.
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

from .agent import PaperAgent, revalidate, review_conclusion
from .draft import build_crate
from .methods import methods_paragraph
from .pipeline import check_inputs, reproduce, run_on_data
from .profiles import fields_of
from .record import find_runs, headline, publish_back, summarise_record
from .runcrate import approve

RUNNER = os.environ.get("RECRATE_RUNNER", "auto")


def _artifacts(*items: tuple[str, Path]) -> list[dict[str, str]]:
    return [{"description": d, "path": str(Path(p).resolve())} for d, p in items if Path(p).exists()]


def _stamp() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")


def _run_artifacts(run_dir: Path) -> list[dict[str, str]]:
    run_dir = Path(run_dir)
    items = [("Run crate (Workflow Run RO-Crate folder)", run_dir),
             ("Results table", run_dir / "outputs" / "results.csv"),
             ("Comparison with the published values", run_dir / "comparison.csv")]
    items += [(f"Figure: {f.stem}", f) for f in sorted((run_dir / "outputs" / "figures").glob("*"))]
    items += [(f"Overlay: {f.stem}", f) for f in sorted((run_dir / "outputs" / "overlays").glob("*.png"))[:12]]
    return _artifacts(*items)


MAX_IMAGES = int(os.environ.get("RECRATE_MCP_IMAGES", "4"))  # figures returned inline per run; 0 for paths only


def _run_images(run_dir: Path, limit: int = MAX_IMAGES) -> list:
    """The figures a run drew (and a few overlays), as image content a client shows inline. Paper2Agent returns
    paths and relies on Claude Code reading them; a client without file access (Claude Desktop, a remote one) needs
    the pixels in the reply."""
    run_dir = Path(run_dir)
    files = [f for f in sorted((run_dir / "outputs" / "figures").glob("*")) if f.suffix.lower() in {".png", ".jpg", ".jpeg"}]
    files += sorted((run_dir / "outputs" / "overlays").glob("*.png"))
    return [Image(path=str(f)) for f in files[:max(0, limit)]]


def _with_images(payload: dict[str, Any], run_dir: Path) -> list:
    """A tool's structured reply followed by its figures as images: one text block (the JSON) and one image block
    per figure."""
    return [payload, *_run_images(run_dir)]


def _tool_name(method: str) -> str:
    return "run_" + (re.sub(r"[^a-z0-9]+", "_", method.lower()).strip("_") or "method")


_PY_TYPES = {"float": float, "number": float, "integer": int, "int": int, "boolean": bool, "bool": bool}


def typed_method_tool(spec, method_id: str, run):
    """A function whose signature is the method's own parameters, typed, documented and defaulting to the published
    values, so an MCP client sees ``count_nuclei(data_path: str, min_size_px: float = 50, ...)``. Values the caller
    changes are passed on as recorded overrides, with the reason."""
    import inspect
    from typing import Annotated

    from pydantic import Field

    from .crate import _coerce

    kw = inspect.Parameter.KEYWORD_ONLY
    params, names = [], {}
    if spec.needs_data:
        d = spec.data_input
        expected = d.description or d.format or "see describe_method"
        named = (spec.invocation or {}).get("inputs") or {}
        if named:  # the workflow takes named files: say which, by the names its community uses
            expected = "a folder with: " + ", ".join(f"{f} ({n})" for n, f in named.items())
        params.append(inspect.Parameter("data_path", kw, annotation=Annotated[str, Field(
            description=f"File or folder with the user's data. Expected: {expected}")]))
    for p in spec.value_inputs:
        arg = re.sub(r"\W+", "_", p.name).strip("_") or "value"
        if arg in names or arg in {"data_path", "who", "reason", "scale_ratio", "auto_adjust", "channel"}:
            arg += "_param"
        names[arg] = p.name
        typ = _PY_TYPES.get((p.type or "").lower(), str)
        default = _coerce(p.default, p.type)
        bits = [p.description or "", f"unit: {p.prop('unit')}" if p.prop("unit") else "",
                f"the paper: \"{p.prop('quote')}\"" if p.prop("quote") else "", f"published value: {p.default}"]
        params.append(inspect.Parameter(arg, kw, default=default, annotation=Annotated[
            typ | None, Field(description="; ".join(b for b in bits if b))]))
    params += [inspect.Parameter("reason", kw, default="", annotation=Annotated[str, Field(
                   description="Why any value differs from the published one (required then; recorded)")]),
               inspect.Parameter("who", kw, default="anonymous", annotation=Annotated[str, Field(
                   description="The person running it, recorded in the run crate")])]
    from .profiles import get_profile

    fld = get_profile(spec.profile).field
    if spec.needs_data and fld.scale:  # only fields whose parameters depend on a scale (pixel size, sampling rate)
        params += [inspect.Parameter("scale_ratio", kw, default=None, annotation=Annotated[float | None, Field(
                       description=f"Your {fld.scale} / the research's, only when the research states none")])]
    if spec.needs_data:
        params += [inspect.Parameter("auto_adjust", kw, default=True, annotation=bool)]
    published = spec.default_params()

    def tool(**kwargs):
        data = kwargs.pop("data_path", None)
        reason, who = kwargs.pop("reason", ""), kwargs.pop("who", "anonymous")
        ratio, adjust = kwargs.pop("scale_ratio", None), kwargs.pop("auto_adjust", True)
        types = {q.name: q.type for q in spec.value_inputs}
        changes = {names[k]: v for k, v in kwargs.items() if v is not None and k in names
                   and _coerce(v, types[names[k]]) != published.get(names[k])}
        return run(method_id, data, who, adjust, 0, changes, reason, ratio)

    tool.__signature__ = inspect.Signature(params)  # no return annotation: the reply is JSON text plus images
    tool.__annotations__ = {p.name: p.annotation for p in params}
    tool.__name__ = _tool_name(spec.method_name or method_id)
    return tool


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
        + next((f"(for example a {f.scale} different from the research's, and the parameter rescaling it implies). "
                for f in fields_of(agent) if f.scale), "and every difference from the research's data. ") +
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

    @mcp.tool(description="Describe one method: what it expects as input (format, and the properties that matter "
                          "for its field), "
                          "its parameters and defaults, the software, and the tolerances used for checking.")
    def describe_method(method: str) -> dict[str, Any]:
        from .model import assumptions

        spec = agent.spec(method)
        return {**spec.summary(), "what_the_paper_does_not_say": assumptions(spec, agent.environment)}

    # ---- running the paper's methods ------------------------------------------------
    @mcp.tool(description="Check whether the user's data fits a method, using the crate's domain profile. "
                          "Returns ok / warn / fail per check, and the parameters in three groups: adjusted "
                          "automatically (with the computation), needing the user's decision (with why), and as "
                          "published. Explain the second group to the user before running.")
    def check_my_data(method: str, data_path: str, scale_ratio: float | None = None) -> dict[str, Any]:
        """scale_ratio: the user's scale (pixel size, sampling rate...) / the research's, only when the research
        states none."""
        from .model import parameter_plan

        spec = agent.spec(method)
        r = check_inputs(spec, data_path, scale_ratio=scale_ratio)
        return {"status": r.status, "can_run": r.can_run, "checks": r.as_rows(),
                "adjustments": [a.__dict__ for a in r.adjustments],
                "parameters": parameter_plan(spec, r)}

    @mcp.tool(name="reproduce", description="Run a method on the paper's own data and compare with the "
                                           "paper's reported results. Returns the verdict ladder (materials found, "
                                           "runs, same numbers, same conclusion) and the run crate's files.")
    def reproduce_tool(method: str, who: str = "anonymous"):
        from .agent import record_reproduction

        out = reproduce(agent.spec(method), _runs(), runner=RUNNER, agent=who)
        if record_reproduction(agent.path, method, out):
            agent.methods[:] = PaperAgent.load(agent.path).methods
        c = out.comparison
        headline = c.headline() if c else "No published results to compare with."
        return _with_images({"message": headline, "artifacts": _run_artifacts(out.run_dir),
                             "verdict": ("reproduced" if c.reproduced else "differs") if c and c.measures else "not compared",
                             "same_numbers": c.count if c and c.measures else None, "ladder": out.ladder,
                             "headline": headline, "run_crate": str(out.run_dir.resolve())}, out.run_dir)

    def _run(method: str, data_path: str | None, who: str, auto_adjust: bool, channel: int,
             changes: dict[str, Any] | None, reason: str, scale_ratio: float | None = None) -> list:
        from .runcrate import summarise_run

        if changes and not reason.strip():
            return [{"message": "Changing a parameter needs a reason (pass reason=...); it is recorded in the run crate.",
                     "artifacts": []}]
        out = run_on_data(agent.spec(method), data_path, _runs(), runner=RUNNER, agent=who, auto_adjust=auto_adjust,
                          channel=channel, scale_ratio=scale_ratio,
                          overrides={k: (v, reason) for k, v in (changes or {}).items()} or None)
        return _with_images({"message": summarise_run(out.run_dir)["headline"], "artifacts": _run_artifacts(out.run_dir),
                             "checks": out.check.as_rows() if out.check else [], "parameters_used": out.params,
                             "results_csv": (out.run_dir / "outputs" / "results.csv").read_text(encoding="utf-8"),
                             "run_crate": str(out.run_dir.resolve())}, out.run_dir)

    @mcp.tool(description="Run a method of the paper on the user's data after checking it. Applies and records "
                          "adjustments (e.g. rescaling a parameter for a different pixel size or sampling rate); stops if a "
                          "check fails. 'parameters' changes values (with a 'reason', recorded). data_path is not "
                          "needed for methods that take only values.")
    def run_on_my_data(method: str, data_path: str | None = None, who: str = "anonymous", auto_adjust: bool = True,
                       channel: int = 0, parameters: dict[str, Any] | None = None, reason: str = "",
                       scale_ratio: float | None = None):
        return _run(method, data_path, who, auto_adjust, channel, parameters, reason, scale_ratio)

    # One typed tool per method, like Paper2Agent's tools: each parameter is an argument with its type, unit, the
    # paper's sentence and its published value as the default.
    for m in agent.methods:
        try:
            spec_m = agent.spec(m.id)
        except Exception:  # noqa: BLE001 - a method the crate can't describe gets no tool
            continue
        status = {"validated": "reproduces the paper's published results", "differs": "differs from the paper's "
                  "published results: see about_paper", "failed": "failed to run on the paper's data"}.get(
                      m.status, "not checked against the paper yet")
        mcp.tool(name=_tool_name(m.name),
                 description=f"{m.name} from the paper ({status}). {m.description} "
                             + ("Runs on the user's data (data_path) with input checks; " if spec_m.needs_data else
                                "Takes parameter values only; ")
                             + "changed values need a reason. Every run is recorded as a run crate.")(
            typed_method_tool(spec_m, m.id, _run))

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
        from .followup import followups_for, propose

        done = followups_for(_runs(), agent.spec(method))
        return {"proposed": [f.as_dict() for f in propose(agent.spec(method))],
                "already_run": [{k: r[k] for k in ("run", "followup", "headline", "when", "by")} for r in done]}

    @mcp.tool(description="Run one follow-up check (by id, from followup_checks) on the paper's data. Writes its "
                          "own run crate, linked to the reproduction it follows up.")
    def run_followup_check(method: str, check: str, who: str = "anonymous"):
        from .crate import CrateError
        from .followup import run_followup

        try:
            out = run_followup(agent.spec(method), check, _runs(), who=who, runner=RUNNER)
        except CrateError as e:  # not runnable here, or no reproduction yet: say what it needs
            return [{"message": str(e), "ran": False, "artifacts": []}]
        from .runcrate import summarise_run

        c = out.comparison
        return _with_images({"message": summarise_run(out.run_dir)["headline"], "ran": True,
                             "same_numbers": c.count if c else None,
                             "artifacts": _run_artifacts(out.run_dir), "run_crate": str(out.run_dir.resolve())},
                            out.run_dir)

    # ---- figures ------------------------------------------------------------------------
    def _latest_run() -> Path:
        runs = sorted(p for p in _runs().glob("*") if p.is_dir() and (p / "ro-crate-metadata.json").exists())
        if not runs:
            raise ValueError("No run yet: reproduce a method or run it on data first.")
        return runs[-1]

    def _table_source(source: str) -> str:
        """``results`` / ``comparison`` as they are; ``table:<label>`` → one of the paper's tables; else a path."""
        if source.lower().startswith("table:"):
            label = source.split(":", 1)[1].strip().lower()
            t = next((x for x in agent.tables() if label in (x["label"].lower(), Path(x["file"]).stem.lower())), None)
            if t is None:
                raise ValueError(f"No table {label!r}. Tables: {', '.join(x['label'] for x in agent.tables()) or 'none'}")
            return str(agent.path / "paper" / t["file"])
        return source

    @mcp.tool(description="Make a figure, drawn by ReCRATE's code from a file (never by the model) and recorded in "
                          "the run crate with the data it came from and what was plotted. kind: "
                          "published_vs_reproduced (one row per compared value: published ring, reproduced dot, "
                          "values outside tolerance marked), or bar, scatter, line, box, hist of a table's columns: "
                          "give y (and x; group optional) as column names. source: results (the run's results "
                          "table), comparison, table:<label> (one of the paper's tables, e.g. 'table:Table 2'), or a "
                          "CSV path (the user's data). run_crate: a run crate folder, or empty for the latest run. "
                          "Returns the image inline and its path.")
    def make_figure(run_crate: str = "", kind: str = "published_vs_reproduced", source: str = "results",
                    x: str = "", y: str = "", group: str = "", title: str = ""):
        from .figures import PLOT_KINDS, make_run_figure

        run_dir = Path(run_crate) if run_crate else _latest_run()
        spec = {"source": _table_source(source), "x": x, "y": y, "group": group, "title": title} \
            if kind in PLOT_KINDS else None
        path = make_run_figure(run_dir, kind, spec)
        what = f"{kind} of {y}" + (f" by {x}" if x else "") + (f" grouped by {group}" if group else "") \
            if kind in PLOT_KINDS else kind.replace("_", " ")
        return [{"message": f"Figure plotted from {run_dir.name} ({what}) and recorded in its run crate.",
                 "artifacts": _artifacts((what, path)), "run_crate": str(run_dir.resolve())}, Image(path=str(path))]

    @mcp.tool(description="The paper's figures a method's latest reproduction redrew from the published data, each "
                          "with the original (from the paper's reading package) and its caption, as images. Ask the "
                          "user whether they show the same thing; record it with review_figure.")
    def recreated_figures(method: str) -> list:
        from .paper_figures import recreated

        m = next((x for x in agent.methods if method in (x.name, x.id)), None)
        if m is None or not m.run_crate:
            return [f"No reproduction of {method} yet."]
        out: list = []
        for f in recreated(Path(m.run_crate), agent.path / "paper"):
            out.append(f"{f['label']}: {f['caption']}" + (f" (reviewed: {f['review']['value']})" if f["review"] else ""))
            if f["original"]:
                out += ["The paper:", Image(path=str(f["original"]))]
            out += ["Recreated:", Image(path=str(f["recreated"]))]
        return out or [f"{method} redraws none of the paper's figures."]

    @mcp.tool(description="Record a person's judgement of a recreated figure against the paper's: verdict same, "
                          "differs or can't tell, with why. Only a person may judge: ask the user, never decide.")
    def review_figure(method: str, figure: str, who: str, verdict: str, note: str = "") -> str:
        from .paper_figures import recreated
        from .paper_figures import review_figure as record

        m = next((x for x in agent.methods if method in (x.name, x.id)), None)
        f = next((x for x in recreated(Path(m.run_crate), agent.path / "paper")
                  if x["label"].lower() == figure.lower().strip()), None) if m and m.run_crate else None
        if f is None:
            return f"{method} didn't recreate {figure}."
        record(Path(m.run_crate), who, f["recreated"].name, verdict, note)
        return f"Recorded: {f['label']} judged '{verdict}' by {who}."

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

    @mcp.tool(description="Export this research object's RO-Crate (found, supplied, recreated as of publication, or "
                          "drafted) as a zip, with a record of how it was obtained. Runs are separate crates.")
    def export_crate() -> dict[str, Any]:
        from .export import export_crate as export

        path = export(agent.path, agent.path / "exports" / f"{agent.name[:60]}-ro-crate-{_stamp()}.zip")
        return {"message": f"RO-Crate exported ({agent.manifest.get('crate_source')}).",
                "artifacts": _artifacts(("The research object's RO-Crate (zip)", path))}

    @mcp.tool(description="Write one static HTML report for this research object: verdict, ladder, every value "
                          "compared with its tolerance source, figures, follow-up checks, assumptions, every run. "
                          "A reviewer can open it without installing anything.")
    def report_page() -> dict[str, Any]:
        from .report import build_report

        path = build_report(agent.path, agent.path / "reports" / f"report-{_stamp()}.html")
        return {"message": "Report written from the run crates.", "artifacts": _artifacts(("Report page (HTML)", path))}

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


LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def serve(target: str | Path, transport: str = "stdio", host: str = "127.0.0.1", port: int = 8765,
          path: str = "/mcp") -> None:
    """Run the agent's server: over stdio (the default, what clients launch themselves) or over streamable HTTP, so
    an agent on one computer is usable from any MCP client without cloning anything (as Paper2Agent's hosted
    servers are). HTTP has no authentication, and the server runs the paper's code on this machine: keep it on
    localhost unless you mean to share it."""
    server = build_server(target)
    if transport == "stdio":
        server.run()
        return
    if transport not in {"http", "streamable-http"}:
        raise ValueError(f"Unknown transport {transport!r}: stdio or http.")
    url = f"http://{host}:{port}{path}"
    if host not in LOCAL_HOSTS:
        print(f"Warning: {url} is reachable from other computers, has no authentication, and runs the paper's code "
              "on this machine.", file=sys.stderr, flush=True)
    print(f"ReCRATE agent over streamable HTTP at {url} (stop with Ctrl-C)", flush=True)
    import inspect

    if "host" in inspect.signature(getattr(server, "run_streamable_http_async", lambda: None)).parameters:
        server.run(transport="streamable-http", host=host, port=port, streamable_http_path=path)  # SDK 2.x
    else:  # SDK 1.x: host, port and path are settings on the server
        server.settings.host, server.settings.port, server.settings.streamable_http_path = host, port, path
        server.run(transport="streamable-http")


def main(argv: list[str] | None = None) -> None:
    import argparse

    ap = argparse.ArgumentParser(prog="recrate-mcp", description="Serve a ReCRATE agent over MCP.")
    ap.add_argument("agent", nargs="?", default=os.environ.get("RECRATE_AGENT"), help="the agent folder")
    ap.add_argument("--http", action="store_true", help="streamable HTTP instead of stdio")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args(sys.argv[1:] if argv is None else argv)
    if not a.agent:
        raise SystemExit("usage: recrate-mcp <paper agent folder> [--http --host H --port P]   (or set RECRATE_AGENT)")
    serve(a.agent, "http" if a.http else "stdio", host=a.host, port=a.port)


if __name__ == "__main__":
    main()
