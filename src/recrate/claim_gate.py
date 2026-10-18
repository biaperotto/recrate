"""The non-model gate: nothing a language model asserted about a paper reaches a verdict unchecked.

A model drafts each claim with an ``anchor`` (a phrase meant to occur verbatim in the paper) and a
``paper_says`` value (meant to be printed there). ``gate`` re-checks both against the paper's text, and
the claim's data source against the data files, with no model involved, mirroring
``verify.check_description``. Checks run in order and the first failure wins:

1. the anchor is substantive (at least 3 words or 20 characters, so it proves something) and occurs in
   the paper's text as whole words;
2. the reported value is printed in the paper's text, not as part of a longer number;
3. the value is printed near an occurrence of the anchor, so the claim's "where in the paper" link points
   at a place that really holds the value;
4. when the claim names a data source and a data root is given, the file exists under that root and
   holds the named columns.

A claim that fails a check becomes ``Not checkable`` with a one-line reason. The gate only ever demotes:
it never sets a verdict, never promotes a claim, and never mutates the claims it is given.

    checked = gate(claims, paper_text, data_root=Path("data"))
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from .claims import Claim
from .verify import norm_text, number_positions

MIN_ANCHOR_WORDS = 3
MIN_ANCHOR_CHARS = 20
NEAR_CHARS = 1000  # generous: catches a value from an unrelated part of the paper, not one a sentence away

_PLAIN_NUMBER = re.compile(r"[-+]?\d+(?:\.(\d+))?")


def _is_number(value: str) -> bool:
    try:
        float(value.strip().replace("−", "-"))
    except ValueError:
        return False
    return True


def _spans(text: str, needle: str) -> list[tuple[int, int]]:
    """Where ``needle`` occurs in ``text``, never as part of a longer word or number."""
    if not needle:
        return []
    pattern = re.escape(needle)
    if needle[0].isalnum():
        pattern = r"(?<![\w.])" + pattern if needle[0].isdigit() else r"(?<!\w)" + pattern
    if needle[-1].isalnum():
        pattern += r"(?!\d|\.\d)" if needle[-1].isdigit() else r"(?!\w)"
    return [m.span() for m in re.finditer(pattern, text)]


def _value_spans(paper: str, value: str) -> list[tuple[int, int]]:
    """Where the reported value is printed. A number is matched with the sign and decimal tolerance of
    ``number_positions`` (the PDF-minus quirk is inherited from verify.py, not chosen here), but not when
    the paper prints more decimals than the claim gives (``68`` is not ``68.5``); anything else, such as
    a confidence interval like ``21% (8%, 48%)``, is matched as printed."""
    if not value.strip():
        return []
    if not _is_number(value):
        return _spans(paper, norm_text(value))
    plain = _PLAIN_NUMBER.fullmatch(value.strip().replace("−", "-"))
    given = len(plain.group(1) or "") if plain else None
    spans = []
    # Sign and PDF-minus tolerance ("-0.11" also matches "20.11") is inherited from verify.py, not chosen here.
    for start in number_positions(paper, value):
        printed = _PLAIN_NUMBER.match(paper, start)
        if printed and given is not None and len(printed.group(1) or "") > given:
            continue
        spans.append((start, printed.end() if printed else start))
    return spans


def _near(values: list[tuple[int, int]], anchors: list[tuple[int, int]]) -> bool:
    return any(vs <= ae + NEAR_CHARS and ve >= as_ - NEAR_CHARS
               for vs, ve in values for as_, ae in anchors)


def _data_problem(data_source: object, data_root: Path) -> str | None:
    """Why a claim's data source cannot be used, or None when the file and its columns are there."""
    if not isinstance(data_source, dict):
        return "data source is malformed"
    name = str(data_source.get("file") or "")
    columns, sheet = data_source.get("columns") or [], data_source.get("sheet")
    if not isinstance(columns, list) or not (sheet is None or isinstance(sheet, (str, int))):
        return "data source is malformed"
    root = data_root.resolve()
    try:
        path = (root / name).resolve()
        inside = path.is_relative_to(root)
        found = path.is_file()
    except (OSError, ValueError):
        return f"data file not found: {name}"
    if name and not inside:
        return f"data file path is outside the data root: {name}"
    if not name or not found:
        return f"data file not found: {name}"
    try:
        if path.suffix.lower() == ".xlsx":
            header = pd.read_excel(path, sheet_name=sheet or 0, nrows=0)
        elif path.suffix.lower() == ".csv":
            header = pd.read_csv(path, nrows=0)
        else:
            return f"data file type cannot be opened: {name}"
        present = {str(col) for col in header.columns}
    except Exception as exc:  # unreadable, corrupt or sheet missing: a reason, never a crash
        return f"data file could not be read: {name} ({type(exc).__name__})"
    for column in columns:
        if str(column) not in present:
            return f"column not in the data: {column}"
    return None


def _problem(claim: Claim, paper: str, data_root: Path | None) -> str | None:
    anchor = norm_text(claim.anchor)
    if len(anchor.split()) < MIN_ANCHOR_WORDS and len(anchor) < MIN_ANCHOR_CHARS:
        return "anchor too short to verify"
    anchors = _spans(paper, anchor)
    if not anchors:
        return "anchor not found in the paper's text"
    values = _value_spans(paper, claim.paper_says)
    if not values:
        return "the reported value is not printed in the paper"
    if not _near(values, anchors):
        return "the reported value is not printed near the anchor"
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
