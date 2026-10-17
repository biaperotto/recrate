"""Check a drafted crate description against the paper, before anyone trusts it.

``recrate check-description <agent folder> <crate_description.json>`` verifies, without a language model:

* every file the description points to exists (workflow, example data, published results, paper);
* every parameter that gives a ``quote`` quotes the paper word for word (whitespace, hyphenation and case aside),
  and a numeric default appears in that quote or elsewhere in the paper;
* every number in the published results file appears in the paper's text (rounded values as printed; a
  minus sign may be printed as − or -), unless the method says where else they come from (``expected_source``);
* the description builds into a crate ReCRATE can load.

A drafted description is only as good as what the paper states; this makes "what the paper states" checkable.
"""

from __future__ import annotations

import csv
import json
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Finding:
    level: str  # error | warning
    method: str
    what: str

    def __str__(self) -> str:
        return f"[{self.level}] {self.method}: {self.what}"


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    checked: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not any(f.level == "error" for f in self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "checked": self.checked, "findings": [f.__dict__ for f in self.findings]}


def _norm(text: str) -> str:
    t = text.replace("−", "-").replace("–", "-").replace("­", "")
    t = re.sub(r"-\s*\n\s*", "", t)          # hyphenation at line ends
    return re.sub(r"\s+", " ", t).lower().strip()


def _number_forms(value: str) -> list[str]:
    v = value.strip().replace("−", "-")
    out = {v, v.lstrip("+")}
    if v.startswith("-"):
        out.add(v[1:])  # tables often print the sign in a header or as "−"
    try:
        f = float(v)
        out |= {f"{f:g}", f"{abs(f):g}"}
        if "." in v:
            dec = len(v.split(".")[1])
            out |= {f"{f:.{dec}f}", f"{abs(f):.{dec}f}"}
    except ValueError:
        pass
    return [o for o in out if o]


def _positions(text: str, value: str) -> list[int]:
    """Where a value is printed. A minus set in a symbol font often comes out of PDFs as "2" ("−0.11" reads
    "20.11"), so a negative value also matches that form."""
    pats = [rf"(?<![\d.]){re.escape(f)}(?![\d])" for f in _number_forms(value)]
    v = value.strip().replace("\u2212", "-")
    if v.startswith("-"):
        pats.append(rf"(?<![\d.])2{re.escape(v[1:])}(?![\d])")
    return sorted({m.start() for p in pats for m in re.finditer(p, text)})


def _number_in(text: str, value: str) -> bool:
    return bool(_positions(text, value))


def _row_together(text: str, values: list[str], window: int = 1500) -> bool:
    """A row of a table is printed in one place: all its values within a short stretch of text."""
    spots = [_positions(text, v) for v in values]
    if not values or any(not s for s in spots):
        return False
    for start in spots[0]:
        if all(any(abs(p - start) <= window for p in s) for s in spots[1:]):
            return True
    return False


def check_description(agent_dir: Path, description: Path) -> Report:
    agent_dir, description = Path(agent_dir), Path(description)
    desc = json.loads(description.read_text(encoding="utf-8"))
    base = description.parent
    paper_md = agent_dir / "paper" / "paper.md"
    paper = _norm(paper_md.read_text(encoding="utf-8")) if paper_md.exists() else ""
    rep = Report(checked={"quotes": 0, "published_values": 0, "files": 0})

    def path(p: str) -> Path:
        q = Path(p)
        return q if q.is_absolute() else base / q

    if not paper:
        rep.findings.append(Finding("warning", "-", "no paper text in the agent folder: quotes and values unchecked"))
    if desc.get("paper_file") and not path(desc["paper_file"]).exists():
        rep.findings.append(Finding("error", "-", f"paper_file not found: {desc['paper_file']}"))
    methods = desc.get("methods") or [desc]
    for m in methods:
        name = m.get("name", "?")
        wf = (m.get("workflow") or {}).get("file")
        for label, p in (("workflow", wf), ("example_data", m.get("example_data")), ("expected", m.get("expected"))):
            if p:
                rep.checked["files"] += 1
                if not path(p).exists():
                    rep.findings.append(Finding("error", name, f"{label} not found: {p}"))
            elif label == "workflow":
                rep.findings.append(Finding("error", name, "no workflow file: the method can't run"))
        for p in m.get("inputs", []):
            q = p.get("quote")
            if q and paper:
                rep.checked["quotes"] += 1
                if _norm(q) not in paper:
                    rep.findings.append(Finding("error", name, f"parameter {p['name']}: quote not found in the paper: "
                                                               f"\"{q[:120]}\""))
                elif isinstance(p.get("default"), (int, float)) and not _number_in(_norm(q), str(p["default"])) \
                        and not _number_in(paper, str(p["default"])):
                    rep.findings.append(Finding("warning", name, f"parameter {p['name']}: default {p['default']} "
                                                                 "is not stated in the paper"))
            elif p.get("type", "Text").lower() not in {"file", "dataset", "directory"} and paper:
                rep.findings.append(Finding("warning", name, f"parameter {p['name']}: no quote from the paper"))
        exp = m.get("expected")
        if exp and path(exp).exists() and paper and not m.get("expected_source"):
            with path(exp).open(newline="", encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
            key = next(iter(rows[0])) if rows else None
            missing, scattered = [], []
            for r in rows:
                nums = []
                for col, val in r.items():
                    if col == key or val in (None, ""):
                        continue
                    try:
                        float(val)
                    except ValueError:
                        continue
                    rep.checked["published_values"] += 1
                    nums.append(val)
                    if not _number_in(paper, val):
                        missing.append(f"{r[key]}/{col}={val}")
                if len(nums) > 1 and all(_number_in(paper, v) for v in nums) and not _row_together(paper, nums):
                    scattered.append(str(r[key]))
            if scattered:
                rep.findings.append(Finding("error", name, "the values of these rows are not printed together anywhere "
                                                           f"in the paper (check the table and row): {', '.join(scattered)}"))
            if missing:
                rep.findings.append(Finding("error", name, f"{len(missing)} published value(s) not found in the paper "
                                                           f"text: {', '.join(missing[:8])}"
                                                           + (" …" if len(missing) > 8 else "")))
    try:
        from .crate import list_methods, load_crate
        from .draft import build_crate

        with tempfile.TemporaryDirectory() as tmp:
            build_crate(desc, Path(tmp) / "crate", base_dir=base)
            for mm in list_methods(Path(tmp) / "crate"):
                load_crate(Path(tmp) / "crate", mm["id"])
    except Exception as e:  # noqa: BLE001
        rep.findings.append(Finding("error", "-", f"the description doesn't build into a crate: {type(e).__name__}: {e}"))
    return rep
