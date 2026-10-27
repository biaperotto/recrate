"""The paper agent's chat tools: what the chat can do, and what each tool returns.

Shared by every chat engine (recrate.chat): ``TOOLS`` describes them for a model, ``ChatTools`` runs them. As in the
MCP server, the model never computes results: the tools run the paper's methods and every run is written as a
Workflow Run RO-Crate. Each tool returns a short JSON text for the model and a list of *attachments* (tables,
figures, run crates) for the user interface to render.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .agent import PaperAgent
from .methods import methods_paragraph
from .pipeline import check_inputs, reproduce, run_on_data
from .record import find_runs, headline, publish_back, summarise_record
from .runcrate import approve, summarise_run
from .runners import FijiRunner


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
    {"name": "make_figure", "description": "Make a figure drawn by ReCRATE's code from a file (never by you) and "
                                           "recorded in the run crate with its data. kind published_vs_reproduced "
                                           "plots a reproduction's compared values; bar, scatter, line, box or hist "
                                           "plot columns of a table (y, and x; group optional): the run's results "
                                           "(source results), its comparison, one of the paper's tables "
                                           "(source 'table:Table 2'), or a CSV path. Use it when the user asks for a "
                                           "plot or a chart.",
     "input_schema": _schema({"run": _RUN, "kind": {"type": "string", "enum": ["published_vs_reproduced", "bar",
                                                                                 "scatter", "line", "box", "hist"]},
                              "source": {"type": "string", "description": "results | comparison | table:<label> | "
                                                                           "a CSV path"},
                              "x": {"type": "string"}, "y": {"type": "string"}, "group": {"type": "string"},
                              "title": {"type": "string"}})},
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
        from .paper_figures import figure_label

        out: list[dict[str, Any]] = [{"type": "results", "csv": str(run_dir / "outputs" / "results.csv")}]
        for f in sorted((run_dir / "outputs" / "figures").glob("*")):  # the figures the method drew in this run
            if f.suffix.lower() in {".png", ".jpg", ".jpeg", ".svg"}:
                label = figure_label(f.name)
                out.append({"type": "figure", "path": str(f),
                            "caption": f"The paper's {label}, drawn by the method in this run" if label
                            else f"{f.stem.replace('_', ' ')}, drawn in this run"})
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

    def t_make_figure(self, run: str | None = None, kind: str = "published_vs_reproduced", source: str = "results",
                      x: str = "", y: str = "", group: str = "", title: str = "") -> ToolResult:
        from .figures import PLOT_KINDS, make_run_figure

        current = next((m.run_crate for m in self.agent.methods if m.run_crate), None)
        run_dir = self._run_dir(run) if run else self.last_run or (Path(current) if current else self._run_dir(None))
        if source.lower().startswith("table:"):  # one of the paper's tables, by label
            label = source.split(":", 1)[1].strip().lower()
            t = next((x for x in self.agent.tables() if label in (x["label"].lower(), Path(x["file"]).stem.lower())), None)
            if t is None:
                raise ValueError(f"No table {label!r}. Tables: {', '.join(x['label'] for x in self.agent.tables()) or 'none'}")
            source = str(self.agent.path / "paper" / t["file"])
        spec = {"source": source, "x": x, "y": y, "group": group, "title": title} if kind in PLOT_KINDS else None
        path = make_run_figure(run_dir, kind, spec)
        what = (f"{kind} of {y}" + (f" by {x}" if x else "") + (f", grouped by {group}" if group else "")
                if kind in PLOT_KINDS else "Published vs reproduced")
        return ToolResult(json.dumps({"figure": str(path), "plotted": what, "run": run_dir.name}),
                          [{"type": "figure", "path": str(path),
                            "caption": f"{title or what}: plotted by ReCRATE from {Path(spec['source']).name if spec else 'this run'}"
                                       f"{'' if spec else chr(39) + 's outputs'}, recorded in {run_dir.name}"}])

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
