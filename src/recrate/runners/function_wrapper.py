"""Call a Python function as a ReCRATE method: the wrapper that runs in the paper's own interpreter.

A crate may name a function (``recrate entry``, ``module:function``) instead of a script that follows the
``--input_dir/--output_dir`` contract, the way Paper2Agent wraps a package's API. This file is what ReCRATE runs
then. It is standalone on purpose: the paper's interpreter need not have ReCRATE installed.

    python function_wrapper.py --entry module:function --path DIR ... --input_dir DIR --output_dir DIR --params JSON
    python function_wrapper.py --entry module:function --path DIR ... --check

The function gets the method's parameters as keyword arguments, plus ``input_dir``, ``output_dir`` and ``channel``
when its signature takes them (or ``**kwargs``). What it returns becomes ``output_dir/results.csv``:

* a table: a pandas DataFrame (anything with ``to_csv``), a list of dicts (one per row) or a dict of equal-length
  lists (one per column); the first column names the item (species, image, sample...);
* one row: a dict of scalars, whose first key names the item, e.g. ``{"sequence": "query", "gc": 0.2857}``;
* the path of a CSV it wrote;
* ``None``, when it wrote ``results.csv`` into ``output_dir`` itself;

optionally as a 2-tuple ``(table, figures)``: ``figures`` maps a name to a matplotlib Figure, a PIL Image, PNG bytes
or an image path, each saved as ``output_dir/figures/<name>.png`` (``figure-3`` recreates the paper's Figure 3, and
ReCRATE pairs it with the original). Figures the function writes into ``output_dir/figures/`` itself are kept too.

Errors are one line on stderr and exit code 1, so ReCRATE can say what went wrong.
"""

import argparse
import csv
import importlib
import inspect
import json
import os
import shutil
import sys
from pathlib import Path

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".svg", ".pdf"}


def load(entry):
    """The callable ``module:attr`` names (``attr`` may be dotted: ``Class.method``)."""
    mod_name, sep, attr = entry.partition(":")
    if not sep or not mod_name or not attr:
        raise SystemExit("recrate entry %r is not of the form module:function" % entry)
    try:
        obj = importlib.import_module(mod_name)
    except BaseException as e:  # ImportError, or an error raised by the module itself
        raise SystemExit("cannot import %r: %s: %s" % (mod_name, type(e).__name__, e))
    for part in attr.split("."):
        if not hasattr(obj, part):
            raise SystemExit("%r has no %r (recrate entry %r)" % (mod_name, attr, entry))
        obj = getattr(obj, part)
    if not callable(obj):
        raise SystemExit("%r is not callable" % entry)
    return obj


def _cell(v):
    if hasattr(v, "item") and not isinstance(v, (str, bytes)):  # numpy scalars
        try:
            return v.item()
        except (TypeError, ValueError):
            return v
    return v


def write_table(result, out):
    """``result`` as ``out/results.csv`` (see the module docstring for the accepted forms)."""
    results = out / "results.csv"
    if result is None:
        if not results.exists():
            raise SystemExit("the function returned nothing and wrote no results.csv in output_dir")
        return
    if isinstance(result, (str, os.PathLike)):
        src = Path(result)
        if not src.is_file():
            raise SystemExit("the function returned %r, which is not a file" % str(result))
        if src.resolve() != results.resolve():
            shutil.copyfile(str(src), str(results))
        return
    if hasattr(result, "to_csv"):  # a pandas DataFrame (or anything that writes itself as CSV)
        index = getattr(getattr(result, "index", None), "name", None) is not None  # a named index is a column
        result.to_csv(str(results), index=index)
        return
    rows = None
    if isinstance(result, dict):
        if result and all(isinstance(v, (list, tuple)) for v in result.values()):  # columns
            lengths = {len(v) for v in result.values()}
            if len(lengths) != 1:
                raise SystemExit("the function returned columns of different lengths")
            rows = [{k: v[i] for k, v in result.items()} for i in range(lengths.pop())]
        else:
            rows = [result]
    elif isinstance(result, (list, tuple)):
        if not all(isinstance(r, dict) for r in result):
            raise SystemExit("the function returned a list whose items are not dicts (one dict per row)")
        rows = list(result)
    if rows is None:
        raise SystemExit("cannot write a results table from a %s: return a DataFrame, a list of dicts, a dict, "
                         "or the path of a CSV" % type(result).__name__)
    if not rows:
        raise SystemExit("the function returned an empty table")
    cols = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    with open(str(results), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: _cell(v) for k, v in r.items()})


def save_figures(figures, out):
    if not figures:
        return
    if not isinstance(figures, dict):
        raise SystemExit("the second item of the returned tuple must be a dict of figures by name")
    fdir = out / "figures"
    fdir.mkdir(parents=True, exist_ok=True)
    for name, fig in figures.items():
        stem = str(name)
        dest = fdir / (stem if Path(stem).suffix.lower() in IMAGE_SUFFIXES else stem + ".png")
        if isinstance(fig, (bytes, bytearray)):
            dest.write_bytes(bytes(fig))
        elif isinstance(fig, (str, os.PathLike)):
            shutil.copyfile(str(fig), str(dest))
        elif hasattr(fig, "savefig"):  # matplotlib
            fig.savefig(str(dest), dpi=150, bbox_inches="tight")
        elif hasattr(fig, "save"):  # PIL
            fig.save(str(dest))
        else:
            raise SystemExit("figure %r is not a matplotlib Figure, a PIL Image, bytes or a path" % stem)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Call a Python function as a ReCRATE method.")
    ap.add_argument("--entry", required=True, help="module:function")
    ap.add_argument("--path", action="append", default=[], help="folder to import from (repeatable)")
    ap.add_argument("--input_dir")
    ap.add_argument("--output_dir")
    ap.add_argument("--channel", type=int, default=0)
    ap.add_argument("--params", default="{}", help="the method's parameters, as JSON")
    ap.add_argument("--check", action="store_true", help="only import the function and print its signature")
    a = ap.parse_args(argv)
    for p in reversed(a.path):
        sys.path.insert(0, p)
    func = load(a.entry)
    try:
        sig = inspect.signature(func)
    except (TypeError, ValueError):
        sig = None
    if a.check:
        print("%s%s" % (a.entry, sig if sig is not None else "(...)"))
        return 0
    if not a.output_dir:
        raise SystemExit("--output_dir is required")
    params = json.loads(a.params)
    names = set(sig.parameters) if sig is not None else set()
    takes_any = sig is None or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
    unknown = [k for k in params if k not in names and not takes_any]
    if unknown:
        raise SystemExit("%s does not take the parameter(s) %s: the crate declares them, so the function must "
                         "accept them (or **kwargs)" % (a.entry, ", ".join(unknown)))
    out = Path(a.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    kwargs = dict(params)
    for name, value in (("input_dir", a.input_dir), ("output_dir", str(out)), ("channel", a.channel)):
        if (name in names or takes_any) and name not in kwargs and value is not None:
            kwargs[name] = value
    result = func(**kwargs)
    figures = None
    if isinstance(result, tuple) and len(result) == 2:
        result, figures = result
    write_table(result, out)
    save_figures(figures, out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
