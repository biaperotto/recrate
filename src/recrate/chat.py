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
_DATA = {"type": "string", "description": "Folder with the user's data. Omit to use the files the user uploaded."}
_RUN = {"type": "string", "description": "Run crate folder or run name, as returned by reproduce/run_on_my_data. "
                                         "Omit for the latest run in this conversation."}

_RATIO = {"type": "number", "description": "Only when the research doesn't state the scale its parameters depend on "
          "(e.g. pixel size, sampling rate) and the user says how theirs compares: theirs ÷ the research's"}
TOOLS: list[dict[str, Any]] = [
    {"name": "about_paper", "description": "Title, DOI, crate source, and the paper's methods with their "
                                           "reproduction status (the verdict ladder) against the paper's reported results.",
     "input_schema": _schema({})},
    {"name": "search_paper", "description": "Search the paper's text; returns the most relevant passages. Use it "
                                            "to quote the paper when answering.",
     "input_schema": _schema({"query": {"type": "string"}, "k": {"type": "integer", "default": 4}}, ["query"])},
    {"name": "describe_method", "description": "One method's card: what its input must look like (format, and the "
                                               "properties that matter for its field), parameters and defaults, "
                                               "software, tolerances.",
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
    {"name": "list_my_files", "description": "The user's data files, with what ReCRATE reads from them for "
                                             "the methods' field (e.g. pixel size for images, columns for tables).",
     "input_schema": _schema({})},
    {"name": "check_my_data", "description": "Check whether the user's data fits a method (domain profile checks). "
                                             "Returns ok / warn / fail per check and parameter adjustments.",
     "input_schema": _schema({"method": _METHOD, "data_path": _DATA, "scale_ratio": _RATIO}, ["method"])},
    {"name": "reproduce", "description": "Run a method on the paper's own data and compare with the paper's "
                                         "reported results. Writes a run crate.",
     "input_schema": _schema({"method": _METHOD}, ["method"])},
    {"name": "run_on_my_data", "description": "Run a method on the user's data after checking it. Applies and "
                                              "records adjustments (e.g. rescaling size parameters for a different "
                                              "pixel size or sampling rate); stops if a check fails. Writes a run crate.",
     "input_schema": _schema({"method": _METHOD, "data_path": _DATA, "scale_ratio": _RATIO,
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
    {"name": "approve_run", "description": "Record that the user looked at a run's results (and overlays, for "
                                           "images) and accepted or rejected them. Only call this when the user says "
                                           "so.",
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
    {"name": "export_crate", "description": "Export this research object's RO-Crate as a zip the user can download, "
                                            "with a record of how it was obtained.",
     "input_schema": _schema({})},
    {"name": "report_page", "description": "Write one HTML page for this research object (verdict, every compared "
                                           "value, figures, follow-ups, every run) that opens anywhere.",
     "input_schema": _schema({})},
    {"name": "make_figure", "description": "Plot the published vs reproduced values of a reproduction from its "
                                           "run's outputs, and record the figure in its run crate.",
     "input_schema": _schema({"run": _RUN})},
    {"name": "recreated_figures", "description": "The paper's figures a method's latest reproduction redrew, each "
                                                 "shown next to the original with its caption and review. Whether "
                                                 "they show the same thing is for the user to say (review_figure).",
     "input_schema": _schema({"method": _METHOD}, ["method"])},
    {"name": "review_figure", "description": "Record the user's judgement of a recreated figure against the paper's: "
                                             "same, differs or can't tell, and why. Only when the user says so.",
     "input_schema": _schema({"method": _METHOD, "figure": {"type": "string", "description": "e.g. Figure 3"},
                              "verdict": {"type": "string", "enum": ["same", "differs", "can't tell"]},
                              "note": {"type": "string"}}, ["method", "figure", "verdict"])},
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
        from .profiles import get_profile

        d = self.current_data()
        names = list(dict.fromkeys(self.agent.spec(m.id).profile for m in self.agent.methods)) or ["generic"]
        rows: list[dict[str, Any]] = []
        for name in names:  # read as each method's field reads it: image metadata, table columns, sampling rate...
            try:
                files = get_profile(name).inspect(d).get("files", []) if d.exists() else []
            except Exception:  # noqa: BLE001 - files this field can't read are listed by the others
                files = []
            for f in files:
                row = {k: (", ".join(map(str, v)) if isinstance(v, list) else v) for k, v in f.items()
                       if k not in ("shape",)}
                if not any(r.get("file") == row.get("file") for r in rows):
                    rows.append(row)
        return ToolResult(json.dumps({"folder": str(d), "files": rows}, default=str),
                          [{"type": "table", "rows": rows}] if rows else [])

    def t_check_my_data(self, method: str, data_path: str | None = None, scale_ratio: float | None = None) -> ToolResult:
        r = check_inputs(self.agent.spec(method), self._data(data_path), scale_ratio=scale_ratio)
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
                         scale_ratio: float | None = None) -> ToolResult:
        ov = {k: (v["value"], v.get("reason") or "chosen by the user") if isinstance(v, dict) else
              (v, "chosen by the user") for k, v in (overrides or {}).items()}
        out = run_on_data(self.agent.spec(method), self._data(data_path), self.agent.runs_dir, runner=self.runner,
                          agent=self.who, auto_adjust=auto_adjust, channel=channel, overrides=ov,
                          progress=self.progress, scale_ratio=scale_ratio)
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
        from .followup import followups_for, propose

        done = followups_for(self.agent.runs_dir, self.agent.spec(method))
        payload = {"proposed": [f.as_dict() for f in propose(self.agent.spec(method))],
                   "already_run": [{k: r[k] for k in ("run", "followup", "headline")} for r in done]}
        rows = [{"id": f["id"], "kind": f["kind"], "check": f["title"], "why": f["why"],
                 "can run here": "yes" if f["runnable"] else f"needs {f['needs']}"} for f in payload["proposed"]]
        done_rows = [{"ran": r["followup"], "result": r["headline"]} for r in done]
        return ToolResult(json.dumps(payload, default=str),
                          [{"type": "table", "rows": rows}] + ([{"type": "table", "rows": done_rows}] if done else []))

    def t_run_followup_check(self, method: str, check: str) -> ToolResult:
        from .followup import run_followup
        from .runcrate import summarise_run

        out = run_followup(self.agent.spec(method), check, self.agent.runs_dir, who=self.who, runner=self.runner)
        self.last_run = out.run_dir
        h = summarise_run(out.run_dir)["headline"]
        return ToolResult(json.dumps({"headline": h, "same_numbers": out.comparison.count if out.comparison else None,
                                      "run": out.run_dir.name}),
                          [{"type": "note", "text": h}, *self._run_attachments(out.run_dir)])

    def t_export_crate(self) -> ToolResult:
        from .export import export_crate

        path = export_crate(self.agent.path)
        return ToolResult(json.dumps({"crate": str(path)}),
                          [{"type": "crate", "zip": str(path), "record": "The research object's RO-Crate."}])

    def t_report_page(self) -> ToolResult:
        from .report import build_report

        path = build_report(self.agent.path)
        return ToolResult(json.dumps({"report": str(path)}), [{"type": "note", "text": f"Report page written: {path}"}])

    def t_make_figure(self, run: str | None = None) -> ToolResult:
        from .figures import make_run_figure

        current = next((m.run_crate for m in self.agent.methods if m.run_crate), None)
        path = make_run_figure(self._run_dir(run) if run else Path(current) if current else self._run_dir(None))
        return ToolResult(json.dumps({"figure": str(path)}),
                          [{"type": "figure", "path": str(path), "caption": "Published vs reproduced, plotted from "
                                                                            "this run's outputs"}])

    def _recreated(self, method: str) -> tuple[Path, list[dict[str, Any]]]:
        from .paper_figures import recreated

        m = next((x for x in self.agent.methods if method in (x.name, x.id)), None)
        if m is None or not m.run_crate or not Path(m.run_crate).exists():
            raise ValueError(f"No reproduction of {method} yet: reproduce it first.")
        return Path(m.run_crate), recreated(Path(m.run_crate), self.agent.path / "paper")

    def t_recreated_figures(self, method: str) -> ToolResult:
        _, figs = self._recreated(method)
        if not figs:
            raise ValueError(f"{method} redraws none of the paper's figures.")
        att: list[dict[str, Any]] = []
        for f in figs:
            if f["original"]:
                att.append({"type": "figure", "path": str(f["original"]), "caption": f"{f['label']}, the paper"})
            att.append({"type": "figure", "path": str(f["recreated"]), "caption": f"{f['label']}, recreated"})
        info = [{"figure": f["label"], "caption": f["caption"], "original_available": bool(f["original"]),
                 "review": f["review"]} for f in figs]
        return ToolResult(json.dumps(info, ensure_ascii=False, default=str), att)

    def t_review_figure(self, method: str, figure: str, verdict: str, note: str = "") -> ToolResult:
        from .paper_figures import review_figure

        run, figs = self._recreated(method)
        f = next((x for x in figs if x["label"].lower() == figure.lower().strip() or x["recreated"].name == figure), None)
        if f is None:
            raise ValueError(f"{method} didn't recreate {figure}.")
        review_figure(run, self.who, f["recreated"].name, verdict, note)
        msg = f"Recorded: {f['label']} judged '{verdict}' by {self.who}."
        return ToolResult(json.dumps({"message": msg}), [{"type": "note", "text": msg}])

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


def tools_for(agent: PaperAgent) -> list[dict[str, Any]]:
    """The tools for this research object's fields: no pixel sizes or channels for a paper analysing tables."""
    import copy

    from .profiles import fields_of

    fields = fields_of(agent)
    scale = any(f.scale for f in fields)
    images = any(f.scale == "pixel size" for f in fields)
    out = []
    for t in copy.deepcopy(TOOLS):
        if t["name"] == "set_pixel_size" and not images:
            continue
        props = t["input_schema"].get("properties", {})
        if not scale:
            props.pop("scale_ratio", None)
        if not any(f.visual for f in fields):
            props.pop("channel", None)
        out.append(t)
    return out


def system_prompt(agent: PaperAgent, max_paper_chars: int = MAX_PAPER_CHARS) -> str:
    title = agent.manifest.get("title", agent.name)
    doi = agent.manifest.get("doi")
    methods = "\n".join(f"- {m.name} ({m.status}): {m.description}" for m in agent.methods) or \
        "- none yet: answer from the paper only"
    paper = agent.paper.text if agent.paper else ""
    paper_block = (f"\n\n<paper>\n{paper}\n</paper>" if paper and len(paper) <= max_paper_chars
                   else "\n\nThe paper is long: use search_paper to read the relevant parts.")
    from .profiles import fields_of

    fields = fields_of(agent)  # how to talk about this research's data: images, recordings, tables...
    noun = " and ".join(dict.fromkeys(f.data_noun for f in fields)) or "data"
    example = next((f" (for example a {f.scale} different from the research's, and why a parameter that depends on "
                    "it is rescaled)" for f in fields if f.scale), "")
    return (
        f"You are the paper agent for '{title}'{f' ({doi})' if doi else ''}, inside the ReCRATE app. "
        f"The user talks to the paper: they ask about it, reproduce its results, bring their own {noun} "
        "and run its methods on them, and export run records as RO-Crates.\n\n"
        f"Methods (each is a tool-backed workflow, reproduced against the paper's reported results):\n{methods}\n\n"
        "How to work:\n"
        "- Answer questions from the paper and quote the relevant sentence. Say so when the paper does not say.\n"
        "- Never invent numbers. Every result comes from a tool; report what the tools return.\n"
        "- Before running a method on the user's files, call check_my_data and explain each warning and "
        f"parameter adjustment in plain terms{example}. If a check fails, explain what to fix and do not run.\n"
        "- Uploaded files are used by default when data_path is omitted.\n"
        "- After a run, summarise the results briefly, ask the user to check them, and offer to "
        "create the output RO-Crate (create_output_crate) and a methods paragraph.\n"
        "- After a run with adjustments, offer compare_paper_parameters to show what the paper's values "
        "unchanged would give.\n"
        "- A reproduction that differs is a finding about the published result, not a failure: name each "
        "value that differs (published vs recomputed, and the tolerance) and say how many reproduce.\n"
        + "".join(f"- If the research doesn't state the {f.scale} and a parameter depends on it, ask the user how "
                  f"theirs compares with the research's and pass scale_ratio; never guess it.\n"
                  for f in fields if f.scale)
        + "".join(f"- {h}\n" for f in fields for h in f.hints) +
        "- If the user wants a different parameter value, pass it in overrides with their reason.\n"
        "- Only call approve_run when the user says the result looks right or wrong.\n"
        "- The app also has a workspace (fit, parameters, results, review, figures, crate, export): "
        "point the user to it when useful.\n"
        "- The app shows tool outputs (tables, figures, download buttons) to the user, so don't repeat whole "
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
                tools=tools_for(self.agent),
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


def suggested_questions(agent: PaperAgent, limit: int = 6) -> list[str]:
    """Questions worth asking this research object, chosen from what it contains: its methods and their verdicts, the
    follow-up checks for a difference, what data a method takes, its tables, what the paper leaves unsaid. Written
    from the content, never by a model, so every suggestion has something real behind it."""
    from .followup import propose
    from .model import assumptions

    out = ["What is this research about, and what did the authors find?"]
    if not agent.methods:
        out += ["Which code and data does the paper link to?",
                "Which software did it use, and in which versions?",
                "What would it take to make its methods runnable?"]
    for m in agent.methods[:3]:
        try:
            spec = agent.spec(m.id)
        except Exception:  # noqa: BLE001 - a method the crate can't describe gets no suggestions
            continue
        name = m.name.replace("_", " ")
        if m.status == "differs":
            out.append(f"Why do the {name} results differ from the paper?")
            if any(f.runnable for f in propose(spec)):
                out.append(f"Run the follow-up checks for {name}: do they explain the difference?")
        elif m.status == "validated":
            out.append(f"How was {name} checked against the paper's published results?")
        elif m.status == "failed":
            out.append(f"Why did {name} fail to run?")
        else:
            out.append(f"Reproduce {name} on the paper's own data.")
        if not spec.needs_data:
            out.append(f"Run {name} with my own values: which can I change?")
        elif spec.profile == "bioimaging":
            out.append(f"Will {name} work on my images? What should I check first?")
        elif spec.profile == "timeseries":
            out.append(f"Will {name} work on my recordings if the sampling rate differs?")
        else:
            out.append(f"Can I use {name} on my own data? What must it look like?")
        if any(p.prop("quote") for p in spec.value_inputs):
            out.append(f"Which parameters does {name} use, and where does the paper state them?")
    tables = agent.tables()
    if tables:  # the table a method reproduces, when its description names one
        said = " ".join(m.description for m in agent.methods).lower()
        t = next((t for t in tables if t["label"] and t["label"].lower() in said), tables[0])
        out.append(f"Show {t['label']} and explain what it reports.")
    if agent.methods:
        try:
            if assumptions(agent.spec(), agent.environment):
                out.append("What does the paper not say, that had to be assumed?")
        except Exception:  # noqa: BLE001
            pass
        if any(m.run_crate for m in agent.methods):
            out.append("Make the report page for this research.")
    seen, picked = set(), []
    for q in out:  # one per kind of question first, so a long list of methods doesn't crowd out the rest
        if q not in seen:
            seen.add(q)
            picked.append(q)
    return picked[:limit]


def follow_up_questions(agent: PaperAgent, reply_parts: list[dict[str, Any]], asked: set[str], n: int = 2) -> list[str]:
    """Two questions that follow from the last answer: what it ran decides what is worth asking next (a difference
    leads to its follow-up checks, a check of your data to running on it...). Never one already asked."""
    from .followup import propose

    tools = [p for p in reply_parts if p.get("kind") == "tool"]
    names = [t["name"] for t in tools]
    method_id = next((t.get("args", {}).get("method") for t in reversed(tools) if t.get("args", {}).get("method")),
                     None) or (agent.methods[0].name if agent.methods else None)
    m = next((x for x in agent.methods if method_id in (x.id, x.name)), None)
    name = (m.name if m else method_id or "this method").replace("_", " ")
    out: list[str] = []
    if "reproduce" in names and m is not None:
        if m.status == "differs":
            out += [f"Why do the {name} results differ from the paper?",
                    f"Run the follow-up checks for {name}: do they explain the difference?"]
        else:
            out += [f"Will {name} work on my data? What should I check first?", "Make the report page for this research."]
    if "followup_checks" in names and m is not None:
        f = next((x for x in propose(agent.spec(m.id)) if x.runnable), None)
        if f:
            out.append(f"Run the check '{f.title}' for {name}.")
        out.append(f"Plot the published and reproduced values for {name}.")
    if "run_followup_check" in names:
        out += [f"Plot the published and reproduced values for {name}.", "Make the report page for this research."]
    if "check_my_data" in names:
        out += [f"Run {name} on my data, with the adjustments.", f"Which parameters of {name} need my decision?"]
    if "run_on_my_data" in names:
        out += ["How do my results compare with what the paper reported?", "Write the methods paragraph for this run."]
    if {"make_figure", "report_page"} & set(names):
        out += ["Export the RO-Crate of this research.", "What does the paper not say, that had to be assumed?"]
    out += suggested_questions(agent, limit=12)  # then the content-based ones, in order
    seen, picked = set(asked), []
    for q in out:
        if q not in seen:
            seen.add(q)
            picked.append(q)
        if len(picked) == n:
            break
    return picked


def claude_code_binary() -> str | None:
    """The Claude Code program: ``RECRATE_CLAUDE``, ``claude`` on the PATH, or the one bundled with the Claude Code
    extension for VS Code or Cursor. It signs in with the person's Claude subscription, so no API key is needed."""
    import shutil

    env = os.environ.get("RECRATE_CLAUDE")
    if env and Path(env).exists():
        return env
    found = shutil.which("claude")
    if found:
        return found
    for base in ("~/.vscode/extensions", "~/.vscode-insiders/extensions", "~/.cursor/extensions"):
        hits = sorted(Path(base).expanduser().glob("anthropic.claude-code-*/resources/native-binary/claude"))
        if hits:
            return str(hits[-1])
    return None


class ClaudeCodeChat:
    """Claude on the person's own Claude subscription, through Claude Code, with the research object's MCP server as
    its only tools. The app's chat becomes the same agent as in Claude Desktop, the way Paper2Agent's agents use
    their MCP servers: Claude reasons and talks; every number comes from a ReCRATE tool. Claude Code's own tools
    (files, shell, web) are switched off, and the conversation continues across messages (``--resume``)."""

    def __init__(self, agent: PaperAgent, tools: ChatTools, binary: str | None = None, model: str | None = None):
        self.agent, self.tools, self.model = agent, tools, model
        self.binary = binary or claude_code_binary()
        if not self.binary:
            raise RuntimeError("Claude Code is not installed (or its VS Code extension).")
        self.session: str | None = None

    def _command(self, text: str) -> list[str]:
        from .install import server_entry

        try:
            data = str(self.tools.current_data())
        except Exception:  # noqa: BLE001 - no data yet
            data = "none yet"
        context = (f"You are the chat of the ReCRATE app for this research object. The user's data folder is {data} "
                   f"(pass it as data_path). Record every run with who='{self.tools.who}'. Use only the recrate tools. "
                   "Never state a number that did not come from a tool result. Never call review_conclusion unless the "
                   "user gives their own verdict in this conversation. Keep answers short and plain.")
        cmd = [self.binary, "-p", text, "--output-format", "stream-json", "--verbose",
               "--mcp-config", json.dumps({"mcpServers": {"recrate": server_entry(self.agent.path)}}),
               "--strict-mcp-config", "--tools", "", "--allowedTools", "mcp__recrate", "--setting-sources", "",
               "--append-system-prompt", context, "--max-turns", "16"]
        if self.model:
            cmd += ["--model", self.model]
        if self.session:
            cmd += ["--resume", self.session]
        return cmd

    def _result(self, name: str, content: Any, error: bool) -> ToolResult:
        """A tool result as the app shows it: figures, the comparison headline, a note."""
        text = content if isinstance(content, str) else "\n".join(
            c.get("text", "") for c in (content or []) if isinstance(c, dict) and c.get("type") == "text")
        atts: list[dict[str, Any]] = []
        try:
            payload = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            payload = None
        if error:
            atts.append({"type": "error", "text": text[:600]})
        elif isinstance(payload, dict):
            if payload.get("run_crate"):
                self.tools.last_run = Path(payload["run_crate"])
            msg = payload.get("message") or payload.get("headline")
            if name in {"reproduce", "run_followup_check"} and msg:
                n = str(payload.get("same_numbers") or "")
                ok = bool(n) and n.split("/")[0] == n.split("/")[-1]
                atts.append({"type": "comparison", "ok": ok, "headline": msg})
            elif msg:
                atts.append({"type": "note", "text": msg})
            for a in payload.get("artifacts") or []:
                if str(a.get("path", "")).lower().endswith((".png", ".jpg", ".svg")):
                    atts.append({"type": "figure", "path": a["path"], "caption": a.get("description", "")})
        return ToolResult(text, atts, error)

    def send(self, text: str) -> Iterator[tuple]:
        import subprocess

        names: dict[str, str] = {}
        proc = subprocess.Popen(self._command(text), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, cwd=str(self.agent.path))
        answered = False
        for line in proc.stdout:
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = e.get("type")
            if kind == "assistant":
                for b in e["message"].get("content", []):
                    if b.get("type") == "text" and b.get("text", "").strip():
                        answered = True
                        yield ("text", b["text"])
                    elif b.get("type") == "tool_use":
                        name = b["name"].removeprefix("mcp__recrate__")
                        names[b["id"]] = name
                        yield ("tool", name, b.get("input") or {})
            elif kind == "user":
                for b in e["message"].get("content", []):
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        name = names.get(b.get("tool_use_id"), "tool")
                        yield ("result", name, self._result(name, b.get("content"), bool(b.get("is_error"))))
            elif kind == "result":
                self.session = e.get("session_id") or self.session
                if e.get("is_error") and not answered:
                    yield ("text", f"Claude Code stopped: {e.get('result') or e.get('subtype')}")
        proc.wait()
        if proc.returncode and not answered:
            yield ("text", "Claude Code could not answer: " + (proc.stderr.read()[-600:] or f"exit {proc.returncode}"))


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
                                                            "parameters": t["input_schema"]}} for t in tools_for(agent)]
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
            "- **why does it differ?** follow-up checks, and **run check** <id>\n"
            "- **plot** published vs reproduced, **report** page\n"
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
            lines.append("No runnable method yet: see **Reproduce** for how to add one.")
        return "\n\n".join(lines)

    def send(self, text: str) -> Iterator[tuple]:
        # Only the person's words: notes the app adds for language models ("[Done in the workstation: ...]",
        # "[The user attached: ...]") must not trigger commands.
        text = re.sub(r"\[(?:Done in the|The user attached)[^\]]*\]", " ", text).strip() or text
        low = text.lower().strip()
        has = lambda *w: any(re.search(rf"\b{x}", low) for x in w)  # noqa: E731
        method = self._method(text)
        # A question about the paper ("what was excluded from the analysis?") is answered from the paper, not
        # taken as a command because it contains a command word.
        question = low.endswith("?") and not re.match(r"(can|could|would|will) you\b", low)
        cmd = lambda *w: not question and has(*w)  # noqa: E731
        needs_method = cmd("reproduce", "check", "run", "describe")
        if needs_method and method is None:
            yield ("text", "This paper agent has no runnable methods yet, so I can only answer from the paper.")
            return
        px = re.search(r"pixel size\D{0,12}?([\d]*\.?\d+)\s*(µm|um|nm|micron)?", low)
        if low in {"help", "?"}:
            yield ("text", self.HELP)
        elif px and has("is", "set", "=", "use", "my"):
            yield from self._do("set_pixel_size", pixel_size=float(px.group(1)), unit=px.group(2) or "µm",
                                source="stated in the chat")
        elif cmd("run check", "run the check", "follow-up check"):
            from .followup import propose

            ids = [f.id for f in propose(self.agent.spec(method))] if method else []
            cid = next((i for i in ids if i in low.replace(" ", "-")), None) or next(
                (f.id for f in propose(self.agent.spec(method)) if f.runnable), None)
            if cid is None:
                yield ("text", "No follow-up check can run here for this method.")
            else:
                yield ("text", f"Running the follow-up check **{cid}** (its own recorded run; the original stays):")
                yield from self._do("run_followup_check", method=method, check=cid)
        elif has("differ", "follow", "why") and method:
            yield from self._do("followup_checks", method=method)
            yield ("text", "Say **run check** and the check's id to run one.")
        elif cmd("plot", "figure", "chart"):
            yield from self._do("make_figure")
        elif cmd("export ro-crate", "export the ro-crate", "paper's crate", "research object's crate", "ro-crate zip"):
            yield from self._do("export_crate")
        elif cmd("report"):
            yield from self._do("report_page")
        elif cmd("compare", "as-is", "unchanged", "without adjust"):
            yield ("text", f"Running **{method}** with the paper's parameters unchanged (a comparison, not recorded):")
            yield from self._do("compare_paper_parameters", method=method)
        elif cmd("reproduce"):
            yield ("text", f"Reproducing **{method}** on the paper's own data:")
            yield from self._do("reproduce", method=method)
        elif cmd("crate", "export", "package", "deposit", "publish"):
            yield ("text", "Packaging the latest run as an output RO-Crate:")
            yield from self._do("create_output_crate")
        elif cmd("check", "fit", "compatible"):
            yield ("text", f"Checking your files against what **{method}** expects:")
            yield from self._do("check_my_data", method=method)
        elif cmd("run", "apply", "analy"):
            yield ("text", f"Running **{method}** on your files (checks first, adjustments applied and recorded):")
            yield from self._do("run_on_my_data", method=method)
            yield ("text", "Look at the overlays. Say **approve** or **reject**, or **crate** to export the run.")
        elif cmd("approve", "looks right", "accept"):
            yield from self._do("approve_run", accepted=True)
        elif cmd("reject", "wrong"):
            yield from self._do("approve_run", accepted=False)
        elif has("methods paragraph", "methods text", "paragraph"):
            yield from self._do("methods_text")
        elif has("record", "history"):
            yield from self._do("reproducibility_record")
        elif has("files", "uploads", "my images"):
            yield from self._do("list_my_files")
        elif has("about", "what is this", "overview", "summar", "methods", "what can you", "what does"):
            yield ("text", self.overview())
        elif cmd("describe"):
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
