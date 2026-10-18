"""The non-model gate: nothing a language model asserted about a paper reaches a verdict unchecked.

A model drafts each claim with an ``anchor`` (a phrase meant to occur verbatim in the paper) and a
``paper_says`` value (meant to be printed there). ``gate`` re-checks both against the paper's text, and
the claim's data source against the data files, with no model involved, mirroring
``verify.check_description``. Checks run in order and the first failure wins:

1. the anchor occurs in the paper's text;
2. the reported value is printed in the paper's text;
3. when the claim names a data source and a data root is given, the file exists under that root and
   holds the named columns.

A claim that fails a check becomes ``Not checkable`` with a one-line reason. The gate only ever demotes:
it never sets a verdict, never promotes a claim, and never mutates the claims it is given.

    checked = gate(claims, paper_text, data_root=Path("data"))
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .claims import Claim
from .verify import norm_text, number_in


def _is_number(value: str) -> bool:
    try:
        float(value.strip().replace("−", "-"))
    except ValueError:
        return False
    return True


def _value_printed(paper: str, value: str) -> bool:
    """A number is matched with the sign and decimal tolerance of ``number_in``; anything else
    (a confidence interval such as ``21% (8%, 48%)``) is matched as printed."""
    if not value.strip():
        return False
    if _is_number(value):
        return number_in(paper, value)
    return norm_text(value) in paper


def _data_problem(data_source: dict, data_root: Path) -> str | None:
    """Why a claim's data source cannot be used, or None when the file and its columns are there."""
    name = str(data_source.get("file") or "")
    root = data_root.resolve()
    path = (root / name).resolve()
    if not name or not path.is_relative_to(root) or not path.is_file():
        return f"data file not found: {name}"
    try:
        if path.suffix.lower() == ".xlsx":
            header = pd.read_excel(path, sheet_name=data_source.get("sheet") or 0, nrows=0)
        elif path.suffix.lower() == ".csv":
            header = pd.read_csv(path, nrows=0)
        else:
            return f"data file type cannot be opened: {name}"
    except Exception as exc:  # unreadable, corrupt or sheet missing: a reason, never a crash
        return f"data file could not be read: {name} ({type(exc).__name__})"
    present = {str(col) for col in header.columns}
    for column in data_source.get("columns") or []:
        if str(column) not in present:
            return f"column not in the data: {column}"
    return None


def _problem(claim: Claim, paper: str, data_root: Path | None) -> str | None:
    if not claim.anchor.strip() or norm_text(claim.anchor) not in paper:
        return "anchor not found in the paper's text"
    if not _value_printed(paper, claim.paper_says):
        return "the reported value is not printed in the paper"
    if claim.data_source and data_root is not None:
        return _data_problem(claim.data_source, data_root)
    return None


def gate(claims: list[Claim], paper_text: str, data_root: Path | None = None) -> list[Claim]:
    """A new list in which every claim that fails a check is ``Not checkable`` with its reason.

    A claim that passes comes back as it was given. With no ``data_root`` the data check is skipped:
    the caller is not asserting that the data is absent.
    """
    paper = norm_text(paper_text)
    out: list[Claim] = []
    for claim in claims:
        reason = None if claim.result == "Not checkable" else _problem(claim, paper, data_root)
        out.append(claim.not_checkable(reason) if reason else claim)
    return out
