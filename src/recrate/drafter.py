"""Draft a crate description from a paper (and its code) with Claude.

Optional: needs ``pip install "recrate[draft]"`` and ``ANTHROPIC_API_KEY``.
The model is ``RECRATE_MODEL`` (default ``claude-sonnet-5-5``).

The output is only a draft: the paper may not state everything (calibration,
exact parameters), so the crate is marked ``draft`` and the user confirms it.
Without an API key, use the ``/agentify`` Claude Code command in this repo,
or the MCP ``draft_crate`` tool, which do the same reading interactively.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from . import draft

SYSTEM = """You turn a scientific paper and its code into a machine-readable description of its
computational methods, so each method can be run on new data and checked against the paper.
Only state what the paper or code supports. Never invent numbers. If a value is unknown, leave it out.
Answer with one JSON object and nothing else."""

INSTRUCTIONS = """Describe this paper's methods in the JSON format documented below.

Rules:
- One entry in "methods" per method a reader would want to apply to their own data.
- "workflow.file" must be a path from the CODE FILES list (they start with code/). Prefer a script or
  macro that takes an input folder and writes a results table.
- Declare every parameter the method needs, with the paper's default values.
- For image methods, declare the expected input properties the paper states: "pixel size" (value and unit),
  "channels", "dimensions". Mark size parameters given in pixels with "scaling": "inverse_pixel_area"
  (areas) or "inverse_pixel_size" (lengths).
- If the paper reports per-image or summary results for its own data, point "example_data" and "expected"
  at those files when they exist in the code or data, and set "tolerances" per reported measurement, each
  with its "source": "paper-rounding" (abs_tolerances, half a unit of the last printed decimal), or
  "paper-stated" with the "quote". Never invent a tolerance without a source.
- The reported results are the ground truth the verdict is judged against, so cite them: set
  "expected_source" to where in the paper the reported results are (e.g. "Table 1 (p. 4)", "Figure 3b").
  If a number is your estimate rather than the paper's, say so in that source string; never leave it blank
  to hide that.
- Set "draft": true, "citation" to the paper's DOI URL if known, and "profile" to "bioimaging" for image data.

FORMAT:
{format_doc}

CODE FILES:
{files}

README (truncated):
{readme}

PAPER (truncated):
{paper}
"""


def _repo_context(code_dir: Path | None, base: Path, max_files: int = 300) -> tuple[str, str]:
    if code_dir is None or not code_dir.exists():
        return "(no code available)", "(none)"
    files = [p for p in sorted(code_dir.rglob("*")) if p.is_file() and ".git" not in p.parts][:max_files]
    listing = "\n".join(str(p.relative_to(base)) for p in files)
    readme = next((p for p in files if p.name.lower().startswith("readme")), None)
    return listing, readme.read_text(errors="replace")[:6000] if readme else "(none)"


def parse_json(text: str) -> dict[str, Any]:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("Claude did not return a JSON object.")
    return json.loads(text[start:end + 1])


def draft_description(paper_text: str, base: Path, code_dir: Path | None = None) -> dict[str, Any]:
    try:
        import anthropic  # type: ignore
    except ImportError as e:
        raise RuntimeError('Drafting with Claude needs: pip install "recrate[draft]"') from e
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("Set ANTHROPIC_API_KEY to draft a crate with Claude.")
    files, readme = _repo_context(code_dir, base)
    prompt = INSTRUCTIONS.format(format_doc=draft.__doc__, files=files, readme=readme,
                                 paper=re.sub(r"\s+\n", "\n", paper_text)[:60000])
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=os.environ.get("RECRATE_MODEL", "claude-sonnet-5-5"),
        max_tokens=8000,
        system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(getattr(block, "text", "") for block in msg.content)
    desc = parse_json(text)
    desc["draft"] = True
    return desc
