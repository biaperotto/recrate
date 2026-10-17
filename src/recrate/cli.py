"""Command line interface.

Make a paper agent:
    recrate agentify --paper paper.pdf --crate crate/ --out agents/my_paper   # paper + RO-Crate
    recrate agentify --paper paper.pdf --fetch-code --draft claude --out ...    # paper only
    recrate validate agents/my_paper                                            # re-run validation

Use it:
    recrate serve   agents/my_paper                      # MCP server for Claude
    recrate inspect agents/my_paper                      # paper, methods, validation
    recrate ask     agents/my_paper "what pixel size?"   # search the paper
    recrate check   agents/my_paper my_images/ --method count_nuclei
    recrate run     agents/my_paper my_images/ --method count_nuclei --who "Anna"
    recrate reproduce agents/my_paper --method count_nuclei [--figure]
    recrate followup agents/my_paper --method count_nuclei [--run CHECK --who "Anna"]
    recrate review  agents/my_paper --method count_nuclei --who "Anna" --agrees partly --note "..."
    recrate approve RUN --who "Anna"
    recrate write-methods RUN
    recrate publish agents/my_paper RUN
    recrate record  agents/my_paper
    recrate export  agents/my_paper dist/   # package crate/ as a WorkflowHub .crate.zip
    recrate report  agents/my_paper                      # one HTML page a reviewer can open

TARGET can be a paper agent folder or a bare crate folder.
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from pathlib import Path

from . import __version__
from .crate import CrateError

STATUS_MARK = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL"}


def _agent(target: str):
    from .agent import PaperAgent

    return PaperAgent.load(target)


def _runs(agent, runs_dir: str | None) -> Path:
    return Path(runs_dir) if runs_dir else agent.runs_dir


def _print_check(report) -> None:
    print(f"Input check ({report.profile} profile): {report.status.upper()}")
    for i in report.items:
        line = f"  [{STATUS_MARK[i.status]}] {i.name}: paper {i.expected} | your data {i.observed}"
        print(line + (f"  ({i.note})" if i.note else ""))
    for a in report.adjustments:
        print(f"  adjust {a.parameter}: {a.old} -> {a.new}  ({a.reason})")


def _print_agent(agent) -> None:
    from .model import STEPS

    card = agent.card()
    print(f"{card['title']}")
    if card.get("doi"):
        print(f"  {card['doi']}")
    print(f"  crate: {card['crate_source']}{' ' + card['crate_url'] if card.get('crate_url') else ''}"
          f"{' (draft, needs confirming)' if card.get('draft') else ''}")
    if card.get("readiness"):
        print(f"  readiness: {card['readiness']}")
    if card.get("code"):
        print(f"  code: {card['code']}")
    if not card["methods"]:
        print("  methods: none yet. The agent can answer questions about the paper; see DRAFT_ME.md to add methods.")
    if card["methods"]:
        print(f"  verdict: {card['verdict']}")
    for m in card["methods"]:
        print(f"  - {m['name']} [{m['language']}] readiness: {m.get('readiness') or 'unknown'}")
        for g in m.get("gaps") or []:
            if m.get("readiness") != "blocked":
                print(f"      gap: {g}")
        ladder = m.get("ladder") or {}
        for key, label in STEPS:
            if key in ladder:
                print(f"      {label:<16} {ladder[key]['status']:<12} {ladder[key]['reason']}")
        if not ladder:
            print(f"      {m['status']}: {m['detail']}")


def cmd_demo(a) -> int:
    from .demo import make_demo

    p = make_demo(a.out)
    print(f"Paper:        {p['paper']}\nPaper crate:  {p['crate']}\nYour images:  {p['my_images']}")
    print(f"\nNext: recrate agentify --paper {p['paper']} --crate {p['crate']} --out {Path(a.out) / 'paper_agent'}")
    return 0


def _print_diagnosis(outcome) -> None:
    fr = outcome.fetch
    where = fr.landing_url or fr.paper.source if fr.paper else outcome.out_dir
    print(f"Paper: {fr.paper.title if fr.paper else '(unknown)'}")
    print(f"  DOI: {fr.doi or 'not found'}   source: {where}")
    if not fr.full_text:
        print(f"  note: {fr.message}")
    if outcome.exit_code == 3:
        print("\n" + outcome.message)
        return
    d = outcome.diagnosis
    src = outcome.crate_source + (f" ({outcome.source_url})" if outcome.source_url else "")
    print(f"  crate: {src}")
    print(f"\nReadiness: {d.readiness.upper()}")
    for m in d.methods:
        flags = ", ".join(k for k, v in (("runnable", m.runnable), ("validatable", m.validatable),
                                         ("reusable", m.reusable)) if v) or "nothing yet"
        print(f"  - {m.name} [{m.language or '?'}]: {flags}")
    fixes = d.top_fixes(3)
    if fixes:
        print("\nTop fixes:")
        for f in fixes:
            print(f"  [{f.severity}] {f.message}" + (f"  → {f.fix}" if f.fix else ""))
    print(f"\nWrote {outcome.out_dir / 'diagnosis.json'} and {outcome.out_dir / 'diagnosis.md'}")


def cmd_diagnose(a) -> int:
    from .diagnose import run_diagnose

    try:
        outcome = run_diagnose(a.link, a.out, crate=a.crate, pdf=a.pdf, draft=a.draft)
    except (CrateError, ValueError, RuntimeError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    _print_diagnosis(outcome)
    return outcome.exit_code


def cmd_agentify(a) -> int:
    from .agent import agentify

    agent = agentify(a.out, paper=a.paper, crate=a.crate, repo=a.repo, fetch_code=a.fetch_code,
                     draft=a.draft, runner=a.runner, strict=a.strict, locate=a.locate, pdf=a.pdf,
                     search_crate=not a.no_search, paper_doi=a.doi)
    found = agent.manifest.get("crate_search")
    if found:
        print(found["summary"])
    _print_agent(agent)
    print(f"\nPaper agent: {agent.path}\nServe it:    recrate serve {agent.path}")
    return 0


def cmd_validate(a) -> int:
    from .agent import revalidate

    _print_agent(revalidate(a.agent, runner=a.runner))
    return 0


def cmd_serve(a) -> int:
    from .mcp_server import build_server

    build_server(a.agent).run()
    return 0


def cmd_inspect(a) -> int:
    agent = _agent(a.target)
    if a.method:
        print(json.dumps(agent.spec(a.method).summary(), indent=2, ensure_ascii=False))
    else:
        _print_agent(agent)
    return 0


def cmd_ask(a) -> int:
    agent = _agent(a.agent)
    hits = agent.search(a.question, a.k)
    if not hits:
        print("No matching passages (or no paper text in this agent).")
    for h in hits:
        print(f"--- passage {h['passage']} (score {h['score']})")
        print(textwrap.shorten(" ".join(h["text"].split()), 700))
    return 0


def cmd_reproduce(a) -> int:
    from .pipeline import reproduce

    agent = _agent(a.target)
    from .agent import record_reproduction

    out = reproduce(agent.spec(a.method), _runs(agent, a.runs_dir), runner=a.runner, agent=a.who)
    if not a.runs_dir:
        record_reproduction(agent.path, a.method or agent.methods[0].id, out)
    print(out.comparison.headline() if out.comparison else "No reported results to compare with.")
    if a.figure and out.comparison and out.comparison.measures:
        from .figures import make_run_figure

        print(f"Figure:    {make_run_figure(out.run_dir)}")
    print(f"Run crate: {out.run_dir}")
    return 0 if (out.comparison is None or out.comparison.reproduced) else 2


def cmd_followup(a) -> int:
    from .followup import followups_for, propose, run_followup

    agent = _agent(a.target)
    spec = agent.spec(a.method)
    if a.run:
        from .runcrate import summarise_run

        out = run_followup(spec, a.run, _runs(agent, a.runs_dir), who=a.who, runner=a.runner)
        print(summarise_run(out.run_dir)["headline"])
        print(f"Run crate: {out.run_dir}")
        return 0
    for f in propose(spec):
        print(f"  {f.id:<28} [{f.kind}] {f.title}" + ("" if f.runnable else f"  (needs {f.needs})"))
        if f.why:
            print(f"      {f.why}")
    for r in followups_for(_runs(agent, a.runs_dir), spec):
        print(f"  ran {r['when']}: {r['followup']}: {r['headline']}")
    return 0


def cmd_review(a) -> int:
    from .agent import review_conclusion

    agent = _agent(a.target)
    method = a.method or agent.methods[0].name
    m = review_conclusion(agent.path, method, a.who, a.agrees, a.note or "")
    print(f"Same conclusion: {m.ladder['conclusion']['status']} ({m.ladder['conclusion']['reason']})")
    return 0


def cmd_report(a) -> int:
    from .report import build_report

    print(f"Report: {build_report(a.target, a.out)}")
    return 0


def cmd_check(a) -> int:
    from .pipeline import check_inputs

    from .model import parameter_plan

    spec = _agent(a.target).spec(a.method)
    report = check_inputs(spec, a.data, pixel_ratio=a.pixel_ratio)
    _print_check(report)
    plan = parameter_plan(spec, report)
    for group, title in (("adjusted", "Adjusted automatically"), ("decide", "Needs your decision"),
                         ("as_published", "As published")):
        if plan[group]:
            print(f"{title}:")
            for p in plan[group]:
                value = f"{p['published']} → {p['value']}" if "value" in p else f"{p['published']}"
                print(f"  {p['name']} = {value}" + (f"  ({p['why']})" if p.get("why") else ""))
                if p.get("quote") and group != "as_published":
                    print(f"      the paper: \"{p['quote']}\"")
    return {"ok": 0, "warn": 0, "fail": 1}[report.status]


def cmd_run(a) -> int:
    from .pipeline import run_on_data

    agent = _agent(a.target)
    changes = dict(kv.split("=", 1) for kv in a.param or [])
    if changes and not a.reason:
        raise ValueError("Changing a parameter needs a reason: add --reason \"...\" (it is recorded in the run crate).")
    out = run_on_data(agent.spec(a.method), a.data, _runs(agent, a.runs_dir), runner=a.runner, agent=a.who,
                      auto_adjust=not a.no_adjust, channel=a.channel, pixel_ratio=a.pixel_ratio,
                      overrides={k: (v, a.reason) for k, v in changes.items()} or None)
    _print_check(out.check)
    print(f"Parameters used: {out.params}")
    print(f"Run crate: {out.run_dir}")
    return 0


def cmd_approve(a) -> int:
    from .runcrate import approve

    approve(Path(a.run), a.who, accepted=not a.reject, note=a.note or "")
    print(f"Recorded {'rejection' if a.reject else 'approval'} by {a.who} in {a.run}")
    return 0


def cmd_write_methods(a) -> int:
    from .methods import methods_paragraph

    print(methods_paragraph(Path(a.run)))
    return 0


def cmd_publish(a) -> int:
    from .record import headline, publish_back

    record = publish_back(_agent(a.target).spec(), Path(a.run))
    print(headline(record["summary"]))
    return 0


def cmd_record(a) -> int:
    from .record import find_runs, headline, summarise_record

    agent = _agent(a.target)
    runs = find_runs(agent.spec(), _runs(agent, a.runs_dir))
    print(headline(summarise_record(runs)))
    for r in runs:
        print(f"  {r['when']}  {r['method'] or '':<18} {r['mode']:<9} {r['verdict']:<13} by {r['by']}"
              f"  ({r['images']} images)" + (f"  approved: {r['approved']}" if r["approved"] else ""))
    return 0


def cmd_draft(a) -> int:
    from .draft import build_crate_from_json

    out = build_crate_from_json(a.description, a.out)
    print(f"Wrote input crate to {out}")
    return 0


def cmd_export(a) -> int:
    from .crate import resolve_crate_dir
    from .export import export_crate_zip

    crate = resolve_crate_dir(a.target)
    out = export_crate_zip(crate, a.out)
    print(f"Wrote WorkflowHub crate to {out}")
    return 0


def cmd_check_description(a) -> int:
    from .verify import check_description

    rep = check_description(Path(a.agent), Path(a.description))
    if a.json:
        print(json.dumps(rep.to_dict(), indent=2, ensure_ascii=False))
    else:
        c = rep.checked
        print(f"Checked {c['quotes']} quote(s), {c['published_values']} published value(s), {c['files']} file(s), {c.get('tolerances', 0)} tolerance(s).")
        for f in rep.findings:
            print(f"  {f}")
        print("OK: every quote and published value is in the paper." if rep.ok else "Not OK: fix the errors above.")
    return 0 if rep.ok else 1


def cmd_install_skill(a) -> int:
    """Install the /recrate Claude Code skill: for this project (.claude/skills) or for you (~/.claude/skills)."""
    import shutil

    src = Path(__file__).resolve().parent / "skill"
    dest = (Path.home() / ".claude" / "skills" / "recrate") if a.user else (Path.cwd() / ".claude" / "skills" / "recrate")
    if dest.exists() or dest.is_symlink():
        if dest.is_symlink() or dest.is_file():
            dest.unlink()
        else:
            shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest)
    print(f"Installed the recrate skill in {dest}\nIn Claude Code: /recrate <paper PDF, DOI, arXiv link or RO-Crate>")
    return 0


def cmd_hardware(a) -> int:
    from .hardware import describe, probe

    h = probe()
    print(json.dumps(h, indent=2) if a.json else describe(h))
    return 0


def cmd_verify_server(a) -> int:
    from .server_check import check_server, describe

    rep = check_server(a.agent, reproduce=a.reproduce)
    print(json.dumps(rep, indent=2, ensure_ascii=False) if a.json else describe(rep))
    return 0 if rep["ok"] else 1


def cmd_mark(a) -> int:
    from .workflow_state import mark

    e = mark(a.agent, a.stage, a.role, a.id or "", a.method, a.output, a.note or "")
    print(f"Recorded {e['stage']} by {e['role']}" + (f" ({e['agent_id']})" if e["agent_id"] else "")
          + (f": {', '.join(e['outputs'])}" if e["outputs"] else ""))
    return 0


def cmd_workflow_check(a) -> int:
    from .workflow_state import check_state, describe

    rep = check_state(a.agent)
    print(json.dumps(rep, indent=2) if a.json else describe(rep))
    return 0 if rep["ok"] else 1


def cmd_package(a) -> int:
    from .package import package_agent
    from .server_check import describe

    rep = package_agent(a.agent, a.out, verify=not a.no_verify)
    if rep.get("check"):
        print(describe(rep["check"]))
    for f in rep["left_out"]:
        print(f"  not packed (too large): {f}")
    print(f"Packaged {rep['zip']} ({rep['size_mb']} MB), checked in a fresh folder." if rep["ok"] and rep["zip"]
          and not a.no_verify else f"Packaged {rep['zip']}." if rep["zip"] else "Not packaged: the check failed.")
    return 0 if rep["ok"] else 1


def build_parser() -> argparse.ArgumentParser:
    from .runners import CHOICES

    p = argparse.ArgumentParser(prog="recrate", description="Turn a paper into an agent that runs its methods "
                                                            "on your data, checked and recorded.")
    p.add_argument("--version", action="version", version=f"recrate {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    def runner_opts(sp, who: bool = True):
        sp.add_argument("--runner", default="auto", choices=CHOICES)
        sp.add_argument("--runs-dir", default=None, help="Default: the agent's recrate/ folder")
        if who:
            sp.add_argument("--who", default="anonymous", help="Name recorded as the person who ran it")

    s = sub.add_parser("demo-data", help="Generate a demo paper, its crate, and 'your images'")
    s.add_argument("out")
    s.set_defaults(func=cmd_demo)

    s = sub.add_parser("diagnose", help="From a paper link: fetch it, locate its RO-Crate, and report "
                                        "whether its methods are ready to validate and reuse")
    s.add_argument("link", help="A DOI, URL, arXiv/bioRxiv link, or local paper file")
    s.add_argument("--crate", help="Skip locate: use this crate (folder, .zip, or URL)")
    s.add_argument("--pdf", help="Full-text PDF to use when the paper is paywalled")
    s.add_argument("--draft", default="none", choices=["none", "claude"],
                   help="If no crate is found, draft one with Claude (needs ANTHROPIC_API_KEY)")
    s.add_argument("--out", required=True, help="Folder to write the paper, crate, locate.json and diagnosis")
    s.set_defaults(func=cmd_diagnose)

    s = sub.add_parser("agentify", help="Turn a paper (and its RO-Crate, if any) into a paper agent")
    s.add_argument("--paper", help="Paper as a PDF/Markdown/text file, or a DOI/URL/arXiv link to fetch")
    s.add_argument("--crate", help="The paper's input RO-Crate, if available")
    s.add_argument("--locate", action="store_true",
                   help="Without --crate: find the paper's RO-Crate online (Diagnose)")
    s.add_argument("--pdf", help="Full-text PDF to use when a fetched paper is paywalled")
    s.add_argument("--repo", help="Code repository URL to clone")
    s.add_argument("--fetch-code", action="store_true", help="Clone the code repository linked in the paper")
    s.add_argument("--draft", default="none", choices=["none", "claude"],
                   help="Without a crate: draft one with Claude (needs ANTHROPIC_API_KEY)")
    s.add_argument("--runner", default="auto", choices=CHOICES)
    s.add_argument("--strict", action="store_true", help="Only expose methods that validated")
    s.add_argument("--doi", help="The paper's own DOI (used to look for a published RO-Crate)")
    s.add_argument("--no-search", action="store_true", help="Don't look for a published RO-Crate; recreate one")
    s.add_argument("--out", required=True, help="Paper agent folder to create")
    s.set_defaults(func=cmd_agentify)

    s = sub.add_parser("validate", help="Re-run each method on the paper's data and compare with its results")
    s.add_argument("agent")
    s.add_argument("--runner", default="auto", choices=CHOICES)
    s.set_defaults(func=cmd_validate)

    s = sub.add_parser("serve", help="Serve a paper agent over MCP (stdio)")
    s.add_argument("agent")
    s.set_defaults(func=cmd_serve)

    s = sub.add_parser("inspect", help="Show the paper agent, or one method's card with --method")
    s.add_argument("target")
    s.add_argument("--method")
    s.set_defaults(func=cmd_inspect)

    s = sub.add_parser("ask", help="Search the paper's text")
    s.add_argument("agent")
    s.add_argument("question")
    s.add_argument("-k", type=int, default=3)
    s.set_defaults(func=cmd_ask)

    s = sub.add_parser("reproduce", help="Run a method on the paper's data and compare with its results")
    s.add_argument("target")
    s.add_argument("--method")
    s.add_argument("--figure", action="store_true", help="Also plot published vs reproduced into the run crate")
    runner_opts(s)
    s.set_defaults(func=cmd_reproduce)

    s = sub.add_parser("followup", help="When numbers differ: list follow-up checks, or run one (--run ID)")
    s.add_argument("target")
    s.add_argument("--method")
    s.add_argument("--run", metavar="CHECK", help="The id of the check to run")
    runner_opts(s)
    s.set_defaults(func=cmd_followup)

    s = sub.add_parser("report", help="Write one static HTML page for the research object (no install needed)")
    s.add_argument("target")
    s.add_argument("--out", help="Default: <agent>/report.html")
    s.set_defaults(func=cmd_report)

    s = sub.add_parser("review", help="Record a person's review: does the reproduction support the conclusion?")
    s.add_argument("target")
    s.add_argument("--method")
    s.add_argument("--who", required=True)
    s.add_argument("--agrees", required=True, choices=["yes", "partly", "no"])
    s.add_argument("--note")
    s.set_defaults(func=cmd_review)

    s = sub.add_parser("check", help="Check whether your data fits a method, and what that means for its parameters")
    s.add_argument("target")
    s.add_argument("data")
    s.add_argument("--method")
    s.add_argument("--pixel-ratio", type=float, help="Your pixel size / the research's, when the research states none")
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("run", help="Run a method on your data, with checks and a run crate")
    s.add_argument("target")
    s.add_argument("data")
    s.add_argument("--method")
    runner_opts(s)
    s.add_argument("--no-adjust", action="store_true", help="Do not rescale parameters")
    s.add_argument("--channel", type=int, default=0, help="Channel index to analyse (0-based)")
    s.add_argument("--param", action="append", metavar="NAME=VALUE", help="Change a parameter (needs --reason)")
    s.add_argument("--reason", help="Why you changed the parameters (recorded in the run crate)")
    s.add_argument("--pixel-ratio", type=float, help="Your pixel size / the research's, when the research states "
                                                     "none (e.g. 2 for 2×2-binned images)")
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("approve", help="Record a visual check of a run's overlays")
    s.add_argument("run")
    s.add_argument("--who", required=True)
    s.add_argument("--reject", action="store_true")
    s.add_argument("--note")
    s.set_defaults(func=cmd_approve)

    s = sub.add_parser("write-methods", help="Write a methods paragraph from a run crate")
    s.add_argument("run")
    s.set_defaults(func=cmd_write_methods)

    s = sub.add_parser("publish", help="Add a run to the paper's reproducibility record")
    s.add_argument("target")
    s.add_argument("run")
    s.set_defaults(func=cmd_publish)

    s = sub.add_parser("record", help="Show the paper's reproducibility record")
    s.add_argument("target")
    s.add_argument("--runs-dir", default=None)
    s.set_defaults(func=cmd_record)

    s = sub.add_parser("export", help="Package a crate as a WorkflowHub-ingestible .crate.zip")
    s.add_argument("target", help="A paper agent folder or a bare crate folder")
    s.add_argument("out", help="Output .crate.zip path, or a directory to write it into")
    s.set_defaults(func=cmd_export)

    s = sub.add_parser("draft", help="Build an input crate from a JSON description")
    s.add_argument("description")
    s.add_argument("out")
    s.set_defaults(func=cmd_draft)

    s = sub.add_parser("check-description", help="Check a drafted crate description against the paper's text")
    s.add_argument("agent", help="The agent folder (with paper/paper.md)")
    s.add_argument("description", help="crate_description.json")
    s.add_argument("--json", action="store_true", help="Machine-readable report")
    s.set_defaults(func=cmd_check_description)

    s = sub.add_parser("install-skill", help="Install the /recrate Claude Code skill")
    s.add_argument("--user", action="store_true", help="For all your projects (~/.claude/skills), not just this one")
    s.set_defaults(func=cmd_install_skill)

    s = sub.add_parser("hardware", help="What this computer can run: CPUs, memory, GPU, interpreters")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_hardware)

    s = sub.add_parser("verify-server", help="Launch the agent's MCP server over stdio and check its tools")
    s.add_argument("agent")
    s.add_argument("--reproduce", action="store_true", help="Also call reproduce for every method")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_verify_server)

    from .workflow_state import STAGES

    s = sub.add_parser("mark", help="Record a finished stage of the /recrate workflow (work/state.json)")
    s.add_argument("agent")
    s.add_argument("stage", choices=STAGES)
    s.add_argument("--role", required=True, help="e.g. coordinator, paper-reader, method-implementer, verifier")
    s.add_argument("--id", help="The subagent's id (needed to check the verifier's independence)")
    s.add_argument("--method", help="The method, for implement")
    s.add_argument("--output", action="append", help="A file this stage produced (relative to the agent)")
    s.add_argument("--note")
    s.set_defaults(func=cmd_mark)

    s = sub.add_parser("workflow-check", help="Check the /recrate workflow's order, outputs and independence")
    s.add_argument("agent")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_workflow_check)

    s = sub.add_parser("package", help="Zip an agent with a USAGE.md, and check it from a fresh folder")
    s.add_argument("agent")
    s.add_argument("--out", help="Default: <agent>.zip next to the agent")
    s.add_argument("--no-verify", action="store_true")
    s.set_defaults(func=cmd_package)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (CrateError, ValueError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
