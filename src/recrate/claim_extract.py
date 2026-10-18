"""Extract a paper's checkable quantitative claims with Claude.

Optional: needs ``pip install "recrate[draft]"`` and ``ANTHROPIC_API_KEY``, unless a
``respond`` callable is supplied (tests replay a recorded response that way).
The model is ``RECRATE_MODEL`` (default ``claude-sonnet-5-5``).

Extraction only reads. Every claim comes back ``result="Not checked"``; whether the
anchor and value really occur in the paper is checked later by a non-model gate.

    claims = extract_claims(paper_text)
"""

from __future__ import annotations

import json
import os
from typing import Callable

from .claims import Claim
from .drafter import parse_json

KINDS = ("number", "figure")
REQUIRED = ("claim", "section", "anchor", "paper_says")

SYSTEM = """You list the checkable quantitative claims a scientific paper makes, so that each can later
be compared with a re-run of the paper's code on its data. Only report what the text states. Never
invent or recompute a number. Answer with one JSON object and nothing else."""

INSTRUCTIONS = """List the quantitative claims in the PAPER below: counts, percentages, means, effect
sizes, intervals and similar numbers that an analysis of the paper's data could confirm or contradict.

Answer with {{"claims": [ ... ]}} where each entry has exactly these keys:
- "claim": ONE short sentence in your own words saying what is claimed. Do not copy the paper's
  wording.
- "section": the heading of the section the claim is in (for example "Results: Effect of treatment").
- "anchor": a few words (about three to ten) copied VERBATIM, character for character, from the PAPER,
  that locate the claim. It must occur in the PAPER exactly as you write it: no paraphrase, no
  ellipsis, no changed spacing or punctuation.
- "paper_says": the value EXACTLY as printed in the PAPER, as a string, keeping its rounding, units
  and confidence-interval format (for example "21% (8%, 48%)" or "153.4"). Do not round, convert or
  reformat it.
- "kind": "number" (the only kind for now).

List the claims in the order they appear in the PAPER. Skip anything you cannot anchor verbatim.
Do not say whether a claim is true.

PAPER:
{paper}
"""


def _call_claude(prompt: str) -> str:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("Set ANTHROPIC_API_KEY to extract claims with Claude.")
    try:
        import anthropic  # type: ignore
    except ImportError as e:
        raise RuntimeError('Extracting claims with Claude needs: pip install "recrate[draft]"') from e
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=os.environ.get("RECRATE_MODEL", "claude-sonnet-5-5"),
        max_tokens=8000,
        system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(getattr(block, "text", "") for block in msg.content)


def _claim_from_entry(index: int, entry: object) -> Claim:
    where = f"claims[{index}]"
    if not isinstance(entry, dict):
        raise ValueError(f"{where} is not an object")
    for key in REQUIRED:
        value = entry.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{where} needs a non-empty string {key!r}")
    kind = entry.get("kind", "number")
    if kind not in KINDS:
        raise ValueError(f"{where} has unknown kind {kind!r}; expected one of {KINDS}")
    return Claim(
        id=f"c{index + 1}",
        kind=kind,
        claim=entry["claim"],
        section=entry["section"],
        anchor=entry["anchor"],
        paper_says=entry["paper_says"],
    )


def extract_claims(paper_text: str, *, respond: Callable[[str], str] | None = None) -> list[Claim]:
    """Ask the model for the paper's quantitative claims, in document order.

    ``respond`` takes the composed prompt and returns the model's raw output; when given,
    no API key is needed. Raises ``ValueError`` on output that is not the expected JSON.
    """
    prompt = INSTRUCTIONS.format(paper=paper_text)  # unchanged: anchors are later checked against this exact text
    raw = (respond or _call_claude)(prompt)
    try:
        payload = parse_json(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"model output is not valid JSON: {e}") from e
    entries = payload.get("claims")
    if not isinstance(entries, list):
        raise ValueError('model output has no "claims" list')
    return [_claim_from_entry(i, entry) for i, entry in enumerate(entries)]
