"""Generic profile: for any workflow, check that the declared kind of input is there.

It knows nothing about a domain, so it proposes no adjustments: it checks that the data exists and that
its files match the format the crate declares for the method's input (by media type or extension).
Domain profiles (bioimaging, ...) add the checks that make results comparable.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

from ..crate import MethodSpec
from .base import FAIL, OK, WARN, CheckItem, CheckReport, DomainProfile


def _files(data_path: Path) -> list[Path]:
    if data_path.is_file():
        return [data_path]
    return sorted(p for p in data_path.rglob("*") if p.is_file() and not p.name.startswith("."))


def _matches(f: Path, fmt: str | None) -> bool:
    if not fmt:
        return True
    guessed = mimetypes.guess_type(f.name)[0] or ""
    fmt = fmt.lower()
    return guessed == fmt or fmt.endswith("/" + f.suffix.lower().lstrip(".")) or fmt.split("/")[-1] in f.name.lower()


class GenericProfile(DomainProfile):
    name = "generic"
    description = "Any workflow: the declared kind of input is present."

    def inspect(self, data_path: Path) -> dict[str, Any]:
        return {"files": [{"file": f.name, "bytes": f.stat().st_size} for f in _files(Path(data_path))]}

    def check(self, spec: MethodSpec, data_path: Path, params: dict[str, Any] | None = None) -> CheckReport:
        data_path = Path(data_path)
        report = CheckReport(profile=self.name, data_path=data_path)
        if not data_path.exists():
            report.items.append(CheckItem("Data found", "a file or folder", "missing", FAIL, f"{data_path} does not exist."))
            return report
        files = _files(data_path)
        report.observed = self.inspect(data_path)
        report.items.append(CheckItem("Data found", "≥1 file", len(files), OK if files else FAIL,
                                      "" if files else "The folder is empty."))
        fmt = spec.data_input.format if spec.data_input else None
        if files and fmt:
            ok = [f for f in files if _matches(f, fmt)]
            status = OK if len(ok) == len(files) else (WARN if ok else FAIL)
            note = "" if status == OK else (f"{len(files) - len(ok)} file(s) are not {fmt}; they may be ignored."
                                            if ok else f"No {fmt} files found.")
            report.items.append(CheckItem("Format", fmt, f"{len(ok)} of {len(files)} match", status, note))
        need = spec.data_input.prop("columns") if spec.data_input else None
        tables = [f for f in files if f.suffix.lower() in {".csv", ".tsv", ".txt"}]
        if need and tables:
            import csv

            want = [c.strip() for c in str(need).split(",") if c.strip()]
            have = set()
            for f in tables:
                with f.open(newline="", encoding="utf-8", errors="replace") as fh:
                    have |= set(next(csv.reader(fh, delimiter="\t" if f.suffix.lower() == ".tsv" else ","), []))
            missing = [c for c in want if c not in have]
            report.items.append(CheckItem("Columns", ", ".join(want), "all present" if not missing else
                                          f"missing: {', '.join(missing)}", FAIL if missing else OK,
                                          "" if not missing else "Rename your columns, or set the column parameters."))
        return report
