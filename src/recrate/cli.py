"""Command line interface.

    recrate demo-data examples/demo           # make a paper crate + 'your images'
    recrate inspect   CRATE                   # the method card
    recrate reproduce CRATE --who "Anna"      # run on the paper's data, compare
    recrate check     CRATE DATA              # does your data fit the method?
    recrate run       CRATE DATA --who "Anna" # run on your data, recorded
    recrate approve   RUN --who "Anna"        # record a visual check
    recrate methods   RUN                     # methods paragraph from a run
    recrate publish   CRATE RUN               # add a run to the paper's record
    recrate record    CRATE                   # the paper's reproducibility record
    recrate draft     DESCRIPTION.json OUT    # build an input crate
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .crate import CrateError, load_crate

STATUS_MARK = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL"}


def _print_check(report) -> None:
    print(f"Input check ({report.profile} profile): {report.status.upper()}")
    for i in report.items:
        line = f"  [{STATUS_MARK[i.status]}] {i.name}: paper {i.expected} | your data {i.observed}"
        print(line + (f"  ({i.note})" if i.note else ""))
    for a in report.adjustments:
        print(f"  adjust {a.parameter}: {a.old} -> {a.new}  ({a.reason})")


def cmd_demo(a) -> int:
    from .demo import make_demo

    paths = make_demo(a.out)
    print(f"Paper crate:  {paths['crate']}\nYour images:  {paths['my_images']}")
    return 0


def cmd_inspect(a) -> int:
    print(json.dumps(load_crate(a.crate).summary(), indent=2, ensure_ascii=False))
    return 0


def cmd_reproduce(a) -> int:
    from .pipeline import reproduce

    out = reproduce(a.crate, a.runs_dir, runner=a.runner, agent=a.who)
    print(out.comparison.headline() if out.comparison else "No reported results to compare with.")
    print(f"Run crate: {out.run_dir}")
    return 0 if (out.comparison is None or out.comparison.reproduced) else 2


def cmd_check(a) -> int:
    from .pipeline import check_inputs

    report = check_inputs(a.crate, a.data)
    _print_check(report)
    return {"ok": 0, "warn": 0, "fail": 1}[report.status]


def cmd_run(a) -> int:
    from .pipeline import run_on_data

    params = dict(kv.split("=", 1) for kv in a.param or [])
    out = run_on_data(a.crate, a.data, a.runs_dir, runner=a.runner, agent=a.who,
                      auto_adjust=not a.no_adjust, channel=a.channel, params=params or None)
    _print_check(out.check)
    print(f"Parameters used: {out.params}")
    print(f"Run crate: {out.run_dir}")
    return 0


def cmd_approve(a) -> int:
    from .runcrate import approve

    approve(Path(a.run), a.who, accepted=not a.reject, note=a.note or "")
    print(f"Recorded {'rejection' if a.reject else 'approval'} by {a.who} in {a.run}")
    return 0


def cmd_methods(a) -> int:
    from .methods import methods_paragraph

    print(methods_paragraph(Path(a.run)))
    return 0


def cmd_publish(a) -> int:
    from .record import headline, publish_back

    record = publish_back(load_crate(a.crate), Path(a.run))
    print(headline(record["summary"]))
    return 0


def cmd_record(a) -> int:
    from .record import find_runs, headline, summarise_record

    spec = load_crate(a.crate)
    runs = find_runs(spec, Path(a.runs_dir))
    print(headline(summarise_record(runs)))
    for r in runs:
        print(f"  {r['when']}  {r['mode']:<9} {r['verdict']:<13} by {r['by']}  ({r['images']} images)"
              + (f"  approved: {r['approved']}" if r["approved"] else ""))
    return 0


def cmd_draft(a) -> int:
    from .draft import build_crate_from_json

    out = build_crate_from_json(a.description, a.out)
    print(f"Wrote input crate to {out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="recrate", description="Reuse published methods on your data, checked and recorded.")
    p.add_argument("--version", action="version", version=f"recrate {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    def runner_opts(sp):
        sp.add_argument("--runner", default="auto", choices=["auto", "fiji", "python"])
        sp.add_argument("--runs-dir", default="runs")
        sp.add_argument("--who", default="anonymous", help="Name recorded as the person who ran it")

    s = sub.add_parser("demo-data", help="Generate a demo paper crate and 'your images'")
    s.add_argument("out")
    s.set_defaults(func=cmd_demo)

    s = sub.add_parser("inspect", help="Show the method card from a crate")
    s.add_argument("crate")
    s.set_defaults(func=cmd_inspect)

    s = sub.add_parser("reproduce", help="Run on the paper's data and compare with its results")
    s.add_argument("crate")
    runner_opts(s)
    s.set_defaults(func=cmd_reproduce)

    s = sub.add_parser("check", help="Check whether your data fits the method")
    s.add_argument("crate")
    s.add_argument("data")
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("run", help="Run the method on your data, with checks and a run crate")
    s.add_argument("crate")
    s.add_argument("data")
    runner_opts(s)
    s.add_argument("--no-adjust", action="store_true", help="Do not rescale parameters")
    s.add_argument("--channel", type=int, default=0, help="Channel index to analyse (0-based)")
    s.add_argument("--param", action="append", metavar="NAME=VALUE", help="Override a parameter")
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("approve", help="Record a visual check of a run's overlays")
    s.add_argument("run")
    s.add_argument("--who", required=True)
    s.add_argument("--reject", action="store_true")
    s.add_argument("--note")
    s.set_defaults(func=cmd_approve)

    s = sub.add_parser("methods", help="Write a methods paragraph from a run crate")
    s.add_argument("run")
    s.set_defaults(func=cmd_methods)

    s = sub.add_parser("publish", help="Add a run to the paper's reproducibility record")
    s.add_argument("crate")
    s.add_argument("run")
    s.set_defaults(func=cmd_publish)

    s = sub.add_parser("record", help="Show the paper's reproducibility record")
    s.add_argument("crate")
    s.add_argument("--runs-dir", default="runs")
    s.set_defaults(func=cmd_record)

    s = sub.add_parser("draft", help="Build an input crate from a JSON description")
    s.add_argument("description")
    s.add_argument("out")
    s.set_defaults(func=cmd_draft)
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
