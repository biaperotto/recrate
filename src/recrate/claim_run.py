"""Run the claims pipeline for a paper agent: extract, pin the data, gate, write ``claims.json``.

    claims = run_claims(Path("agents/my_paper"))

Only the paper's own data-availability section is searched for data links: a repository the paper merely
cites is not this paper's data. When the paper has no such section the whole text is searched and every
pinned claim says so (``data_source["found_in"]``). Every claim leaves ``run_claims`` either still
``Not checked`` with a pinned data source, or ``Not checkable`` with a one-line reason; no claim gets a
verdict that did not come through ``gate``.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from typing import Callable

from .claim_extract import extract_claims
from .claim_gate import gate
from .claims import Claim, save_claims
from .pinning import data_links, file_record, pin_git

SECTION = "data availability section"
WHOLE_PAPER = "whole paper (no data availability section found)"

_AVAILABILITY = re.compile(r"(data|code|materials?)\b.*\bavailab|availab\w*\s+of\s+(data|code|materials?)", re.I)
_MARKDOWN_HEADING = re.compile(r"\s*#{1,6}\s")


def _is_availability_heading(line: str) -> bool:
    text = line.strip().lstrip("#").strip()
    return 0 < len(text) <= 80 and not text.endswith(".") and bool(_AVAILABILITY.search(text))


def _ends_section(lines: list[str], i: int) -> bool:
    """Whether ``lines[i]`` starts the next section: a markdown heading, or (for plain text from a PDF) a
    short unpunctuated line standing alone between blank lines."""
    line = lines[i].strip()
    if _MARKDOWN_HEADING.match(lines[i]):
        return True
    alone = (i == 0 or not lines[i - 1].strip()) and (i + 1 >= len(lines) or not lines[i + 1].strip())
    return bool(line) and alone and len(line) <= 60 and not line.endswith((".", ",", ";", ":"))


def data_section(paper_text: str) -> str | None:
    """The text of the paper's data-availability section (heading to the next heading), or None."""
    lines = paper_text.splitlines()
    for i, line in enumerate(lines):
        if _is_availability_heading(line):
            end = next((j for j in range(i + 1, len(lines)) if _ends_section(lines, j)), len(lines))
            return "\n".join(lines[i + 1:end])
    return None


def _pin(links: list[str], data_dir: Path) -> tuple[dict | None, str]:
    """Pin the first link that can be pinned: (its source record, "") or (None, why none could be)."""
    failures: list[str] = []
    for url in links:
        if "github.com" not in url and "gitlab.com" not in url and "codeberg.org" not in url:
            failures.append(f"{url} is not a git repository, which is all that can be pinned yet")
            continue
        dest = data_dir / url.rstrip("/").rsplit("/", 1)[-1]
        try:
            pinned = pin_git(url, dest)
        except (RuntimeError, OSError) as e:
            failures.append(str(e))
            continue
        files = [file_record(p, url) | {"name": p.relative_to(data_dir).as_posix()}
                 for p in sorted(pinned.path.rglob("*"))
                 if p.is_file() and ".git" not in p.relative_to(pinned.path).parts]
        return {"url": pinned.url, "kind": pinned.kind, "version": pinned.version, "files": files}, ""
    return None, "; ".join(failures)


def run_claims(agent_dir: Path, *, respond: Callable[[str], str] | None = None,
               online: bool = True) -> list[Claim]:
    """Write ``<agent_dir>/claims.json`` and return its claims. See the module docstring."""
    agent_dir = Path(agent_dir)
    paper_md = agent_dir / "paper" / "paper.md"
    if not paper_md.is_file():
        raise FileNotFoundError(f"No paper text at {paper_md}: build the paper agent first (recrate agentify)")
    paper_text = paper_md.read_text(encoding="utf-8")

    claims = extract_claims(paper_text, respond=respond)

    section = data_section(paper_text)
    found_in, where = (SECTION, section) if section is not None else (WHOLE_PAPER, paper_text)
    note = "" if section is not None else " (no data availability section; whole paper searched)"
    links = data_links(where)

    source, reason = None, ""
    if not links:
        reason = "data not shared" + note
    elif not online:
        reason = f"data was not fetched because the run was offline: {links[0]}{note}"
    else:
        data_dir = agent_dir / "data"
        source, failure = _pin(links, data_dir)
        if source is None:
            reason = f"data could not be pinned: {failure}{note}"
        else:
            source["found_in"] = found_in

    checked = gate(claims, paper_text, data_root=agent_dir / "data" if source else None)
    out = []
    for c in checked:
        if c.result == "Not checked":  # the gate's own demotions keep their reason
            c = replace(c, data_source=source) if source else c.not_checkable(reason)
        out.append(c)
    save_claims(out, agent_dir / "claims.json")
    return out
