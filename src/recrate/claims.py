"""Claims: the paper's checkable quantitative claims, each with a verdict.

A claim is one sentence or figure the paper asserts that a run of its code on its
data can confirm or contradict. This module holds only the data model and its
``claims.json`` read/write; extraction, checking and computation live elsewhere.

    save_claims(claims, Path("claims.json"))
    claims = load_claims(Path("claims.json"))
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path

RESULTS = ("Reproduced", "Mismatch", "Not checked", "Not checkable")


@dataclass
class Claim:
    id: str
    kind: str  # "number" | "figure"
    claim: str
    section: str
    anchor: str
    paper_says: str
    data_gives: str | None = None
    result: str = "Not checked"
    why_not: str = ""
    data_source: dict | None = None
    computation: dict | None = None
    tolerance: dict | None = None

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> Claim:
        return cls(**d)

    def not_checkable(self, reason: str) -> Claim:
        return replace(self, result="Not checkable", why_not=reason)


def save_claims(claims: list[Claim], path: Path) -> None:
    for c in claims:
        if c.result not in RESULTS:
            raise ValueError(f"claim {c.id!r} has unknown result {c.result!r}; expected one of {RESULTS}")
    text = json.dumps([c.to_json() for c in claims], indent=2, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")


def load_claims(path: Path) -> list[Claim]:
    return [Claim.from_json(d) for d in json.loads(path.read_text(encoding="utf-8"))]
