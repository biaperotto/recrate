"""Chat with a paper agent: ask the paper, check and run its methods on your files, export run crates.

Two engines share the same tools (``ChatTools``):

* ``ClaudeChat``: Claude decides which tools to call (needs ``pip install "recrate[draft]"`` and
  ``ANTHROPIC_API_KEY``; model ``RECRATE_CHAT_MODEL``, default ``claude-opus-5-5``).
* ``LocalChat``: no API key; a keyword router over the same tools, so the demo works offline.

As in the MCP server, the model never computes results: the tools run the paper's methods and every
run is written as a Workflow Run RO-Crate. Each tool returns a short JSON text for the model and a list
of *attachments* (tables, images, run crates) for the user interface to render.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from .agent import PaperAgent
from .methods import methods_paragraph
from .pipeline import check_inputs, reproduce, run_on_data
from .record import find_runs, headline, publish_back, summarise_record
from .runcrate import approve, summarise_run
from .runners import FijiRunner

MODEL = os.environ.get("RECRATE_CHAT_MODEL", "claude-opus-5-5")
MAX_PAPER_CHARS = 400_000  # papers longer than this are reached through search_paper only


def runner_choices() -> list[str]:
    """Runners that can work on this machine (Fiji only when FIJI_PATH and PyImageJ are set up)."""
    return ["auto", "fiji", "reference", "script"] if FijiRunner().available() else ["auto", "reference", "script"]


def _schema(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required or []}


_METHOD = {"type": "string", "description": "Method name, as listed by about_paper."}
_DATA = {"type": "string", "description": "Folder with the images. Omit to use the files the user uploaded."}
_RUN = {"type": "string", "description": "Run crate folder or run name, as returned by reproduce/run_on_my_data. "
                                         "Omit for the latest run in this conversation."}

_RATIO = {"type": "number", "description": "The user's pixel size divided by the research's, only when the "
          "research states no pixel size and the user knows how theirs compares (e.g. 2 for 2×2 binning)"}
TOOLS: list[dict[str, Any]] = [
    {"name": "about_paper", "description": "Title, DOI, crate source, and the paper's methods with their "
                                           "validation status against the paper's reported results.",
     "input_schema": _schema({})},
    {"name": "search_paper", "description": "Search the paper's text; returns the most relevant passages. Use it "
                                            "to quote the paper when answering.",
     "input_schema": _schema({"query": {"type": "string"}, "k": {"type": "integer", "default": 4}}, ["query"])},
    {"name": "describe_method", "description": "One method's card: expected input (format, pixel size, channels), "
                                               "parameters and defaults, software, tolerances.",
     "input_schema": _schema({"method": _METHOD}, ["method"])},
    {"name": "list_tables", "description": "The paper's tables (label, caption). Read one with get_table.",
     "input_schema": _schema({})},
    {"name": "get_table", "description": "One of the paper's tables, as printed, by label (e.g. 'Table 2').",
     "input_schema": _schema({"table": {"type": "string"}}, ["table"])},
    {"name": "list_figures", "description": "The paper's figures (label, caption). Show one with show_figure.",
     "input_schema": _schema({})},
    {"name": "show_figure", "description": "Show one of the paper's figures to the user, by label (e.g. 'Figure 3'); "
                                           "returns its caption.",
     "input_schema": _schema({"figure": {"type": "string"}}, ["figure"])},
    {"name": "list_my_files", "description": "The files the user uploaded in this conversation, with their "
                                             "image metadata (pixel size, channels, dimensions).",
     "input_schema": _schema({})},
    {"name": "check_my_data", "description": "Check whether the user's data fits a method (domain profile checks). "
                                             "Returns ok / warn / fail per check and parameter adjustments.",
     "input_schema": _schema({"method": _METHOD, "data_path": _DATA, "pixel_ratio": _RATIO}, ["method"])},
    {"name": "reproduce", "description": "Run a method on the paper's own data and compare with the paper's "
                                         "reported results. Writes a run crate.",
     "input_schema": _schema({"method": _METHOD}, ["method"])},
    {"name": "run_on_my_data", "description": "Run a method on the user's data after checking it. Applies and "
                                              "records adjustments (e.g. rescaling size parameters for a different "
                                              "pixel size); stops if a check fails. Writes a run crate.",
     "input_schema": _schema({"method": _METHOD, "data_path": _DATA, "pixel_ratio": _RATIO,
                              "auto_adjust": {"type": "boolean", "default": True},
                              "channel": {"type": "integer", "default": 0, "description": "0-based channel"},
                              "overrides": {"type": "object", "description": (
                                  "Parameter values the user chose, each {\"value\": ..., \"reason\": ...}. They win "
                                  "over automatic adjustments and are recorded with the reason. Only use values "
                                  "the user asked for.")}},
                             ["method"])},
    {"name": "compare_paper_parameters", "description": "Run a method on the user's data with the paper's "
                                                        "parameters unchanged (not recorded), to show what the "
                                                        "adjustments change. Use after a run with adjustments.",
     "input_schema": _schema({"method": _METHOD, "data_path": _DATA}, ["method"])},
    {"name": "set_pixel_size", "description": "Give the user's images a pixel size when they have no calibration, "
                                              "using a value the user states (e.g. from the microscope). Recorded "
                                              "as user-provided. Never guess the value.",
     "input_schema": _schema({"pixel_size": {"type": "number"}, "unit": {"type": "string", "default": "µm"},
                              "source": {"type": "string", "description": "Where the value comes from."}},
                             ["pixel_size"])},
    {"name": "methods_text", "description": "A methods paragraph written from a run crate, ready to paste.",
     "input_schema": _schema({"run": _RUN})},
    {"name": "approve_run", "description": "Record that the user looked at a run's overlays and accepted or "
                                           "rejected the result. Only call this when the user says so.",
     "input_schema": _schema({"run": _RUN, "accepted": {"type": "boolean"}, "note": {"type": "string"}},
                             ["accepted"])},
    {"name": "create_output_crate", "description": "Package a run as an output RO-Crate (zip) the user can "
                                                   "download and deposit, and add it to the paper's "
                                                   "reproducibility record.",
     "input_schema": _schema({"run": _RUN})},
    {"name": "reproducibility_record", "description": "Every run based on the paper's crate: by whom, on what "
                                                      "data, with which adjustments and verdicts.",
     "input_schema": _schema({})},
    {"name": "followup_checks", "description": "When a method's numbers differ from the paper's: the follow-up "
                                               "checks proposed (inclusion rules, rounding, versions), with what each "
                                               "changes and why, and those already run.",
     "input_schema": _schema({"method": {"type": "string"}}, ["method"])},
    {"name": "run_followup_check", "description": "Run one follow-up check by id. It is its own recorded run, "
                                                  "linked to the reproduction; the original result is never replaced.",
     "input_schema": _schema({"method": {"type": "string"}, "check": {"type": "string"}}, ["method", "check"])},
    {"name": "make_figure", "description": "Plot the published vs reproduced values of a reproduction from its "
                                           "run's outputs, and record the figure in its run crate.",
     "input_schema": _schema({"run": _RUN})},
]


@dataclass
class ToolResult:
    text: str  # JSON for the model
    attachments: list[dict[str, Any]] = field(default_factory=list)
    error: bool = False


class ChatTools:
    """The paper agent's tools for one conversation: uploads folder, runner, user name, latest run."""

    def __init__(self, agent: PaperAgent, uploads: Path, runner: str = "auto", who: str = "anonymous"):
        self.agent, self.uploads, self.runner, self.who = agent, Path(uploads), runner, who
        self.last_run: Path | None = None
        self.data_dir: Path | None = None  # a folder on this computer, used instead of the uploads
        self.progress = None  # progress(done, total, name), set by the UI
        self.whatifs: dict[str, dict[str, Any]] = {}  # run name -> what-if with the paper's parameters

    # ---- helpers -------------------------------------------------------------------
    def current_data(self) -> Path:
        return self.data_dir or self.uploads

    def _data(self, data_path: str | None) -> Path:
        if data_path:
            return Path(data_path).expanduser()
        d = self.current_data()
        if not d.exists() or not any(p.is_file() and not p.name.startswith(".") for p in d.rglob("*")):
            raise ValueError("No data yet. Attach files to a message, or choose a folder.")
        return d

    def _run_dir(self, run: str | None) -> Path:
        if not run:
            if self.last_run is None:
                raise ValueError("No run yet in this conversation. Reproduce or run a method first.")
            return self.last_run
        p = Path(run)
        return p if p.is_absolute() or p.exists() else self.agent.runs_dir / run

    def _method_of(self, run_dir: Path) -> str | None:
        return summarise_run(run_dir).get("method")

    def _run_attachments(self, run_dir: Path) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = [{"type": "results", "csv": str(run_dir / "outputs" / "results.csv")}]
        overlays = sorted((run_dir / "outputs" / "overlays").glob("*.png"))
        if overlays:
            out.append({"type": "images", "paths": [str(o) for o in overlays]})
        out.append({"type": "run", "run_dir": str(run_dir)})
        return out

    # ---- dispatch --------------------------------------------------------------------
    def call(self, name: str, args: dict[str, Any]) -> ToolResult:
        fn = getattr(self, f"t_{name}", None)
        if fn is None:
            return ToolResult(json.dumps({"error": f"Unknown tool {name}"}), error=True)
        try:
            return fn(**args)
        except Exception as e:  # noqa: BLE001 - every failure goes back to the chat, not a crash
            from .crate import CrateError

            expected = isinstance(e, (ValueError, FileNotFoundError, CrateError))  # a situation, not a bug
            msg = str(e) if expected else f"{type(e).__name__}: {e}"
            return ToolResult(json.dumps({"error": msg}), [{"type": "error", "text": msg}], error=True)

    # ---- tools -----------------------------------------------------------------------
    def t_about_paper(self) -> ToolResult:
        card = self.agent.card()
        return ToolResult(json.dumps(card, ensure_ascii=False), [{"type": "methods_table", "methods": card["methods"]}])

    def t_search_paper(self, query: str, k: int = 4) -> ToolResult:
        hits = self.agent.search(query, k)
        return ToolResult(json.dumps(hits, ensure_ascii=False), [{"type": "passages", "items": hits}] if hits else [])

    def t_describe_method(self, method: str) -> ToolResult:
        card = self.agent.spec(method).summary()
        return ToolResult(json.dumps(card, ensure_ascii=False, default=str), [{"type": "method_card", "card": card}])

    def _find(self, items: list[dict], name: str) -> dict | None:
        n = name.lower().strip()
        return next((x for x in items if n in (x["label"].lower(), x["file"].lower(), Path(x["file"]).stem.lower())
                     or x["label"].lower().replace(" ", "") == n.replace(" ", "")), None)

    def t_list_tables(self) -> ToolResult:
        t = self.agent.tables()
        return ToolResult(json.dumps(t, ensure_ascii=False), [{"type": "table", "rows": [
            {"table": x["label"], "caption": x["caption"]} for x in t]}] if t else [])

    def t_get_table(self, table: str) -> ToolResult:
        t = self._find(self.agent.tables(), table)
        if t is None:
            raise ValueError(f"No table {table!r} in this paper.")
        import csv as _csv
        with (self.agent.path / "paper" / t["file"]).open(newline="", encoding="utf-8") as fh:
            rows = list(_csv.reader(fh))
        head, body = rows[0], rows[1:]
        recs = [dict(zip(head, r)) for r in body]
        return ToolResult(json.dumps({"label": t["label"], "caption": t["caption"], "rows": recs}, ensure_ascii=False),
                          [{"type": "note", "text": f"{t['label']}: {t['caption']}"}, {"type": "table", "rows": recs}])

    def t_list_figures(self) -> ToolResult:
        f = self.agent.figures()
        return ToolResult(json.dumps(f, ensure_ascii=False), [{"type": "table", "rows": [
            {"figure": x["label"], "caption": x["caption"][:160]} for x in f]}] if f else [])

    def t_show_figure(self, figure: str) -> ToolResult:
        f = self._find(self.agent.figures(), figure)
        if f is None:
            raise ValueError(f"No figure {figure!r} in this paper.")
        return ToolResult(json.dumps({"label": f["label"], "caption": f["caption"]}, ensure_ascii=False),
                          [{"type": "figure", "path": str(self.agent.path / "paper" / f["file"]),
                            "caption": f"{f['label']}: {f['caption']}"}])

    def t_list_my_files(self) -> ToolResult:
        from .profiles.bioimaging import list_images, read_image_metadata

        d = self.current_data()
        files = [read_image_metadata(f) for f in list_images(d)] if d.exists() else []
        rows = [{k: m.get(k) for k in ("file", "format", "dimensions", "channels", "pixel_size", "unit")}
                for m in files]
        return ToolResult(json.dumps({"folder": str(d), "files": rows}, default=str),
                          [{"type": "table", "rows": rows}] if rows else [])

    def t_check_my_data(self, method: str, data_path: str | None = None, pixel_ratio: float | None = None) -> ToolResult:
        r = check_inputs(self.agent.spec(method), self._data(data_path), pixel_ratio=pixel_ratio)
        adj = [a.__dict__ for a in r.adjustments]
        payload = {"status": r.status, "can_run": r.can_run, "checks": r.as_rows(), "adjustments": adj}
        return ToolResult(json.dumps(payload, default=str),
                          [{"type": "checks", "status": r.status, "rows": r.as_rows(), "adjustments": adj}])

    def t_reproduce(self, method: str) -> ToolResult:
        out = reproduce(self.agent.spec(method), self.agent.runs_dir, runner=self.runner, agent=self.who,
                        progress=self.progress)
        self.last_run = out.run_dir
        from .agent import PaperAgent, record_reproduction

        if record_reproduction(self.agent.path, method, out):
            self.agent.methods[:] = PaperAgent.load(self.agent.path).methods
        c = out.comparison
        verdict = ("reproduced" if c.reproduced else "differs") if c and c.measures else "not compared"
        rows = [{"measure": m.measure, "reported": m.reported, "reproduced": m.reproduced,
                 "difference": f"±{m.rel_diff:.3g}" if m.absolute else f"{m.rel_diff:.1%}",
                 "tolerance": m.tolerance_label, "within": "yes" if m.within else "no"}
                for m in (c.measures if c else [])]
        payload = {"verdict": verdict, "headline": c.headline() if c else None, "same_numbers": c.count if c else None,
                   "ladder": out.ladder, "measures": rows, "run": out.run_dir.name}
        return ToolResult(json.dumps(payload, default=str),
                          [{"type": "comparison", "ok": verdict == "reproduced",
                            "headline": c.headline() if c else verdict, "rows": rows},
                           *self._run_attachments(out.run_dir)])

    def t_run_on_my_data(self, method: str, data_path: str | None = None, auto_adjust: bool = True,
                         channel: int = 0, overrides: dict[str, Any] | None = None,
                         pixel_ratio: float | None = None) -> ToolResult:
        ov = {k: (v["value"], v.get("reason") or "chosen by the user") if isinstance(v, dict) else
              (v, "chosen by the user") for k, v in (overrides or {}).items()}
        out = run_on_data(self.agent.spec(method), self._data(data_path), self.agent.runs_dir, runner=self.runner,
                          agent=self.who, auto_adjust=auto_adjust, channel=channel, overrides=ov,
                          progress=self.progress, pixel_ratio=pixel_ratio)
        self.last_run = out.run_dir
        csv_text = (out.run_dir / "outputs" / "results.csv").read_text(encoding="utf-8")
        lines = csv_text.splitlines()
        payload = {"parameters_used": out.params, "checks": out.check.as_rows() if out.check else [],
                   "adjustments": [a.__dict__ for a in out.check.adjustments] if out.check else [],
                   "results_csv": "\n".join(lines[:50]) + ("\n..." if len(lines) > 50 else ""),
                   "run": out.run_dir.name}
        return ToolResult(json.dumps(payload, default=str),
                          [{"type": "params", "params": out.params}, *self._run_attachments(out.run_dir)])

    def t_compare_paper_parameters(self, method: str, data_path: str | None = None) -> ToolResult:
        from .workspace import whatif

        spec = self.agent.spec(method)
        w = whatif(spec, self._data(data_path), spec.default_params(), runner=self.runner)
        if self.last_run is not None:
            self.whatifs[self.last_run.name] = w
        rows = w["rows"]
        return ToolResult(json.dumps({"parameters": w["params"], "results": rows[:50],
                                      "note": "Not recorded: a comparison only."}, default=str),
                          [{"type": "whatif", "params": w["params"], "rows": rows}])

    def t_set_pixel_size(self, pixel_size: float, unit: str = "µm", source: str = "") -> ToolResult:
        from .profiles.bioimaging import set_calibration

        src = self._data(None)
        dest = src if src == self.uploads else self.uploads / "calibrated"
        changed = set_calibration(src, dest, float(pixel_size), unit, who=self.who, source=source)
        if dest != src:
            self.data_dir = dest
        msg = (f"Pixel size {pixel_size:g} {unit} set for {len(changed)} uncalibrated file(s), recorded as "
               f"provided by {self.who}." if changed else "All files already had a calibration; nothing changed.")
        return ToolResult(json.dumps({"calibrated": changed, "data": str(dest)}), [{"type": "note", "text": msg}])

    def t_methods_text(self, run: str | None = None) -> ToolResult:
        text = methods_paragraph(self._run_dir(run))
        return ToolResult(json.dumps({"methods_paragraph": text}), [{"type": "methods_text", "text": text}])

    def t_approve_run(self, accepted: bool, run: str | None = None, note: str = "") -> ToolResult:
        d = self._run_dir(run)
        approve(d, self.who, accepted, note)
        word = "approval" if accepted else "rejection"
        return ToolResult(json.dumps({"recorded": word, "by": self.who, "run": d.name}),
                          [{"type": "note", "text": f"Recorded {word} by {self.who} in {d.name}."}])

    def t_create_output_crate(self, run: str | None = None) -> ToolResult:
        d = self._run_dir(run)
        method = self._method_of(d)
        spec = self.agent.spec(method)
        record = publish_back(spec, d)
        archive = next(r["archive"] for r in record["runs"] if r["run"] == d.name)
        h = "The paper's record: " + headline(summarise_record(find_runs(spec, self.agent.runs_dir)))
        return ToolResult(json.dumps({"crate_zip": archive, "run": d.name, "record": h}),
                          [{"type": "crate", "zip": archive, "run_dir": str(d), "record": h}])

    def t_followup_checks(self, method: str) -> ToolResult:
        from .followup import followups_of, propose

        m = next((x for x in self.agent.methods if method in (x.id, x.name)), None)
        done = followups_of(self.agent.runs_dir, m.run_crate) if m and m.run_crate else []
        payload = {"proposed": [f.as_dict() for f in propose(self.agent.spec(method))],
                   "already_run": [{k: r[k] for k in ("run", "followup", "headline")} for r in done]}
        return ToolResult(json.dumps(payload, default=str))

    def t_run_followup_check(self, method: str, check: str) -> ToolResult:
        from .followup import run_followup
        from .runcrate import summarise_run

        out = run_followup(self.agent.spec(method), check, self.agent.runs_dir, who=self.who, runner=self.runner)
        self.last_run = out.run_dir
        h = summarise_run(out.run_dir)["headline"]
        return ToolResult(json.dumps({"headline": h, "same_numbers": out.comparison.count if out.comparison else None,
                                      "run": out.run_dir.name}),
                          [{"type": "note", "text": h}, *self._run_attachments(out.run_dir)])

    def t_make_figure(self, run: str | None = None) -> ToolResult:
        from .figures import make_run_figure

        path = make_run_figure(self._run_dir(run))
        return ToolResult(json.dumps({"figure": str(path)}),
                          [{"type": "figure", "path": str(path), "caption": "Published vs reproduced, plotted from "
                                                                            "this run's outputs"}])

    def t_reproducibility_record(self) -> ToolResult:
        if not self.agent.has_methods:
            return ToolResult(json.dumps({"headline": "No runnable method yet, so no runs.", "runs": []}))
        runs = find_runs(self.agent.spec(), self.agent.runs_dir)
        h = headline(summarise_record(runs))
        rows = [{k: r[k] for k in ("when", "method", "mode", "verdict", "by", "images", "approved")} for r in runs]
        return ToolResult(json.dumps({"headline": h, "runs": rows}, default=str),
                          [{"type": "record", "headline": h, "rows": rows}])


# ---- events streamed to the UI ------------------------------------------------------------
# ("text", str) | ("tool", name, input) | ("result", name, ToolResult)


def system_prompt(agent: PaperAgent, max_paper_chars: int = MAX_PAPER_CHARS) -> str:
    title = agent.manifest.get("title", agent.name)
    doi = agent.manifest.get("doi")
    methods = "\n".join(f"- {m.name} ({m.status}): {m.description}" for m in agent.methods) or \
        "- none yet: answer from the paper only"
    paper = agent.paper.text if agent.paper else ""
    paper_block = (f"\n\n<paper>\n{paper}\n</paper>" if paper and len(paper) <= max_paper_chars
                   else "\n\nThe paper is long: use search_paper to read the relevant parts.")
    return (
        f"You are the paper agent for '{title}'{f' ({doi})' if doi else ''}, inside the ReCRATE app. "
        "The user talks to the paper: they ask about it, reproduce its results, upload their own images "
        "and run its methods on them, and export run records as RO-Crates.\n\n"
        f"Methods (each is a tool-backed workflow, validated against the paper's reported results):\n{methods}\n\n"
        "How to work:\n"
        "- Answer questions from the paper and quote the relevant sentence. Say so when the paper does not say.\n"
        "- Never invent numbers. Every result comes from a tool; report what the tools return.\n"
        "- Before running a method on the user's files, call check_my_data and explain each warning and "
        "parameter adjustment in plain terms (for example a pixel size difference and why a size threshold is "
        "rescaled). If a check fails, explain what to fix and do not run.\n"
        "- Uploaded files are used by default when data_path is omitted.\n"
        "- After a run, summarise the results briefly, ask the user to look at the overlays, and offer to "
        "create the output RO-Crate (create_output_crate) and a methods paragraph.\n"
        "- After a run with adjustments, offer compare_paper_parameters to show what the paper's values "
        "unchanged would give.\n"
        "- A validation that differs is a finding about the published result, not a failure: name each "
        "value that differs (published vs recomputed, and the tolerance) and say how many reproduce.\n"
        "- If the research states no pixel size and a parameter is in pixels, ask the user how their pixel size "
        "compares with the research's and pass pixel_ratio; never guess it.\n"
        "- If images have no pixel size, ask the user for it (from the microscope or acquisition software) and "
        "call set_pixel_size with what they say; never guess it.\n"
        "- If the user wants a different parameter value, pass it in overrides with their reason.\n"
        "- Only call approve_run when the user says the result looks right or wrong.\n"
        "- The app also has a workspace (fit, parameters, results, per-image review, figures, crate, export): "
        "point the user to it when useful.\n"
        "- The app shows tool outputs (tables, overlays, download buttons) to the user, so don't repeat whole "
        "tables; point out what matters. Keep answers short."
        + paper_block
    )


def _echo_content(blocks: list[Any]) -> list[Any]:
    """Assistant content to send back. After a mid-output fallback, drop the declined model's
    thinking and tool calls before the last fallback marker (they were never run)."""
    idx = [i for i, b in enumerate(blocks) if getattr(b, "type", None) == "fallback"]
    if not idx:
        return list(blocks)
    cut = idx[-1]
    drop = {"thinking", "redacted_thinking", "tool_use"}
    return [b for i, b in enumerate(blocks) if i > cut or (i < cut and b.type not in drop)]


class ClaudeChat:
    """Claude with the paper agent's tools (manual tool-use loop, so the UI can show each step)."""

    def __init__(self, agent: PaperAgent, tools: ChatTools, model: str = MODEL, api_key: str | None = None):
        import anthropic  # optional dependency: recrate[draft]

        self.client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self.agent, self.tools, self.model = agent, tools, model
        self.system = [{"type": "text", "text": system_prompt(agent), "cache_control": {"type": "ephemeral"}}]
        self.messages: list[dict[str, Any]] = []

    def send(self, text: str, max_steps: int = 12) -> Iterator[tuple]:
        self.messages.append({"role": "user", "content": text})
        for _ in range(max_steps):
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=self.system,
                tools=TOOLS,
                messages=self.messages,
                output_config={"effort": "medium"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            if response.stop_reason == "refusal":
                self.messages.pop()  # leave the history as it was before the declined message
                yield ("text", "I can't help with that request.")
                return
            content = _echo_content(response.content)
            self.messages.append({"role": "assistant", "content": content})
            for b in content:
                if b.type == "text" and b.text.strip():
                    yield ("text", b.text)
            calls = [b for b in content if b.type == "tool_use"]
            if response.stop_reason == "pause_turn":
                continue
            if not calls:
                if response.stop_reason == "max_tokens":
                    yield ("text", "_(The answer was cut off; ask me to continue.)_")
                return
            results = []
            for c in calls:
                yield ("tool", c.name, c.input)
                r = self.tools.call(c.name, dict(c.input or {}))
                yield ("result", c.name, r)
                results.append({"type": "tool_result", "tool_use_id": c.id, "content": r.text,
                                **({"is_error": True} if r.error else {})})
            self.messages.append({"role": "user", "content": results})
        yield ("text", "_(Stopped after many steps; tell me how to continue.)_")


LOCAL_URL = os.environ.get("RECRATE_LOCAL_URL", "http://localhost:11434/v1")  # Ollama's OpenAI-compatible API


def _http_json(url: str, payload: dict | None = None, api_key: str | None = None, timeout: int = 300) -> dict:
    import urllib.request

    req = urllib.request.Request(url, data=json.dumps(payload).encode() if payload is not None else None,
                                 headers={"Content-Type": "application/json",
                                          **({"Authorization": f"Bearer {api_key}"} if api_key else {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - a server the user chose
        return json.loads(r.read())


def local_models(base_url: str = LOCAL_URL, timeout: float = 1.5) -> list[str]:
    """Models served by a local OpenAI-compatible server (Ollama, LM Studio, llama.cpp); [] if none is running."""
    try:
        return sorted(m["id"] for m in _http_json(f"{base_url.rstrip('/')}/models", timeout=timeout).get("data", []))
    except Exception:  # noqa: BLE001 - no server: that's an answer, not an error
        return []


class OpenAICompatChat:
    """An open model on your own machine (or any OpenAI-compatible server) with the paper agent's tools.
    Free, no API key: e.g. `ollama pull qwen2.5:7b` and ReCRATE finds it. Models need tool calling
    (qwen2.5, llama3.1, mistral-nemo...)."""

    def __init__(self, agent: PaperAgent, tools: ChatTools, model: str, base_url: str = LOCAL_URL,
                 api_key: str | None = None, max_paper_chars: int = 12_000):
        self.agent, self.tools, self.model = agent, tools, model
        self.url, self.api_key = base_url.rstrip("/") + "/chat/completions", api_key
        self.tool_defs = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                            "parameters": t["input_schema"]}} for t in TOOLS]
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt(agent, max_paper_chars)}]

    def send(self, text: str, max_steps: int = 8) -> Iterator[tuple]:
        self.messages.append({"role": "user", "content": text})
        for _ in range(max_steps):
            out = _http_json(self.url, {"model": self.model, "messages": self.messages, "tools": self.tool_defs,
                                        "tool_choice": "auto", "temperature": 0.2}, self.api_key)
            msg = (out.get("choices") or [{}])[0].get("message") or {}
            calls = msg.get("tool_calls") or []
            self.messages.append({"role": "assistant", "content": msg.get("content") or "",
                                  **({"tool_calls": calls} if calls else {})})
            if msg.get("content"):
                yield ("text", msg["content"])
            if not calls:
                return
            for c in calls:
                fn = c.get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                name = fn.get("name", "")
                yield ("tool", name, args)
                r = self.tools.call(name, args if isinstance(args, dict) else {})
                yield ("result", name, r)
                self.messages.append({"role": "tool", "tool_call_id": c.get("id", name), "content": r.text})
        yield ("text", "_(Stopped after many steps; tell me how to continue.)_")


class LocalChat:
    """No API key: route a message to tools by keywords. Enough to drive the demo end to end."""

    HELP = ("I'm answering with simple commands (no language model is connected: see **Connect**). I understand:\n"
            "- **what is this about?** and other questions about the paper (I'll show the most relevant passages)\n"
            "- **reproduce** [method]\n"
            "- **check** my images [for method]\n"
            "- **run** [method] on my images\n"
            "- **crate** / **export**: package the latest run as an output RO-Crate\n"
            "- **compare** with the paper's parameters unchanged\n"
            "- **my pixel size is 0.5 µm** (when your images have no calibration)\n"
            "- **methods** paragraph, **approve** / **reject** the latest run, **record**, **files**, **about**")

    def __init__(self, agent: PaperAgent, tools: ChatTools):
        self.agent, self.tools = agent, tools

    def _method(self, text: str) -> str | None:
        names = [m.name for m in self.agent.methods]
        low = text.lower().replace(" ", "_")
        hit = next((n for n in names if n.lower() in low), None)
        return hit or (names[0] if names else None)

    def _do(self, name: str, **args: Any) -> Iterator[tuple]:
        yield ("tool", name, args)
        yield ("result", name, self.tools.call(name, args))

    def overview(self) -> str:
        """What the research is, from its crate and paper: title, what it does, its methods and their status."""
        c = self.agent.card()
        lines = [f"**{c['title']}**"]
        meta = [x for x in [c.get("published") and f"published {c['published']}", c.get("doi")] if x]
        if meta:
            lines.append(" · ".join(meta))
        desc = next((m.description for m in self.agent.methods if m.description), "")
        if not desc and self.agent.paper:  # no method described: the paper's abstract, or its opening
            text = self.agent.paper.text
            m = re.search(r"(?is)abstract\W{0,5}(.{80,900}?)(?:\n\s*\n|introduction)", text)
            desc = " ".join((m.group(1) if m else text[:700]).split())
        if desc:
            lines.append(desc[:900])
        if self.agent.methods:
            lines.append("**Methods**\n" + "\n".join(
                f"- **{m.name}** ({m.language}): {m.status}" + (f". {m.detail}" if m.detail else "")
                for m in self.agent.methods))
        else:
            lines.append("No runnable method yet: see **Validate** for how to add one.")
        return "\n\n".join(lines)

    def send(self, text: str) -> Iterator[tuple]:
        # Only the person's words: notes the app adds for language models ("[Done in the workstation: ...]",
        # "[The user attached: ...]") must not trigger commands.
        text = re.sub(r"\[(?:Done in the|The user attached)[^\]]*\]", " ", text).strip() or text
        low = text.lower().strip()
        has = lambda *w: any(re.search(rf"\b{x}", low) for x in w)  # noqa: E731
        method = self._method(text)
        needs_method = has("reproduce", "check", "run", "describe")
        if needs_method and method is None:
            yield ("text", "This paper agent has no runnable methods yet, so I can only answer from the paper.")
            return
        px = re.search(r"pixel size\D{0,12}?([\d]*\.?\d+)\s*(µm|um|nm|micron)?", low)
        if low in {"help", "?"}:
            yield ("text", self.HELP)
        elif px and has("is", "set", "=", "use", "my"):
            yield from self._do("set_pixel_size", pixel_size=float(px.group(1)), unit=px.group(2) or "µm",
                                source="stated in the chat")
        elif has("compare", "as-is", "unchanged", "without adjust"):
            yield ("text", f"Running **{method}** with the paper's parameters unchanged (a comparison, not recorded):")
            yield from self._do("compare_paper_parameters", method=method)
        elif has("reproduce"):
            yield ("text", f"Reproducing **{method}** on the paper's own data:")
            yield from self._do("reproduce", method=method)
        elif has("crate", "export", "package", "deposit", "publish"):
            yield ("text", "Packaging the latest run as an output RO-Crate:")
            yield from self._do("create_output_crate")
        elif has("check", "fit", "compatible"):
            yield ("text", f"Checking your files against what **{method}** expects:")
            yield from self._do("check_my_data", method=method)
        elif has("run", "apply", "analy"):
            yield ("text", f"Running **{method}** on your files (checks first, adjustments applied and recorded):")
            yield from self._do("run_on_my_data", method=method)
            yield ("text", "Look at the overlays. Say **approve** or **reject**, or **crate** to export the run.")
        elif has("approve", "looks right", "accept"):
            yield from self._do("approve_run", accepted=True)
        elif has("reject", "wrong"):
            yield from self._do("approve_run", accepted=False)
        elif has("methods paragraph", "methods text", "paragraph"):
            yield from self._do("methods_text")
        elif has("record", "history"):
            yield from self._do("reproducibility_record")
        elif has("files", "uploads", "my images"):
            yield from self._do("list_my_files")
        elif has("about", "what is this", "overview", "summar", "methods", "what can you", "what does"):
            yield ("text", self.overview())
        elif has("describe"):
            yield from self._do("describe_method", method=method)
        else:
            hits = self.agent.search(text, 3)
            if hits:
                yield ("text", "From the paper:")
                yield ("result", "search_paper", ToolResult("", [{"type": "passages", "items": hits}]))
            else:
                yield ("text", "I found nothing about that in the paper.\n\n" + self.HELP)


def claude_installed() -> bool:
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def claude_available(api_key: str | None = None) -> tuple[bool, str]:
    """Whether the app's own chat can use Claude: the SDK, and a key (given here or in the environment)."""
    if not claude_installed():
        return False, 'Install `pip install -e ".[draft]"` to chat with Claude in the app.'
    if not (api_key or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        return False, "No API key: the chat understands simple commands."
    return True, f"Claude ({MODEL})"


def copy_into(files: list[Path], uploads: Path) -> list[str]:
    uploads.mkdir(parents=True, exist_ok=True)
    for f in files:
        shutil.copy2(f, uploads / f.name)
    return [f.name for f in files]
