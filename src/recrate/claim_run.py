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
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Callable

from .claim_extract import extract_claims
from .claim_gate import gate
from .claims import Claim, save_claims
from .pinning import PinnedSource, data_links, file_record, pin_git

SECTION = "data availability section"
WHOLE_PAPER = "whole paper (no usable data availability section)"

_AVAILABILITY = re.compile(r"(data|code|materials?)\b.*\bavailab|availab\w*\s+of\s+(data|code|materials?)", re.I)
_MARKDOWN_HEADING = re.compile(r"\s*#{1,6}\s")
_LINK = re.compile(r"https?://|www\.|doi\.org|\b10\.\d{4,}/", re.I)
_TRAILING = frozenset("a an and are at by for from in is of on or the to under via was were with".split())
_PLAIN_HEADING = re.compile(
    r"(?:\d+\.?\s*)?(?:abstract|introduction|background|methods?|materials and methods|results(?: and discussion)?|"
    r"discussion|conclusions?|acknowledge?ments?|funding|references|bibliography|supplementary (?:material|information)|"
    r"author contributions?|competing interests?|conflicts? of interest|ethics(?: statement)?|reporting summary|"
    r"additional information|extended data|peer review information|keywords?)\s*:?", re.I)


def _is_availability_heading(line: str) -> bool:
    text = line.strip().lstrip("#").strip()
    return 0 < len(text) <= 80 and not text.endswith(".") and bool(_AVAILABILITY.search(text))


def _lone_title(lines: list[str], i: int) -> bool:
    """A short, unpunctuated line alone between blank lines that names no link and does not trail off
    ("The images are deposited at"): a plain-text heading the known names do not list."""
    line = lines[i].strip()
    alone = (i == 0 or not lines[i - 1].strip()) and (i + 1 >= len(lines) or not lines[i + 1].strip())
    words = line.split()
    return (bool(line) and alone and len(words) <= 6 and not line.endswith((".", ",", ";", ":"))
            and not _LINK.search(line) and words[-1].lower().strip("()") not in _TRAILING)


def _ends_section(lines: list[str], i: int) -> bool:
    """Whether ``lines[i]`` starts the next section: a markdown heading, a line that is just a well-known
    section name, or a lone short title. A link alone on its line is the data being named, not a heading."""
    return bool(_MARKDOWN_HEADING.match(lines[i]) or _PLAIN_HEADING.fullmatch(lines[i].strip())
                or _lone_title(lines, i))


def data_sections(paper_text: str) -> list[str]:
    """The text of every availability section (heading to the next heading), those naming data first.

    Papers often have separate "Data availability" and "Code availability" sections; the data one is
    listed first so its links are tried first.
    """
    lines = paper_text.splitlines()
    found: list[tuple[bool, str]] = []
    for i, line in enumerate(lines):
        if _is_availability_heading(line):
            end = next((j for j in range(i + 1, len(lines)) if _ends_section(lines, j)), len(lines))
            found.append(("data" not in line.lower(), "\n".join(lines[i + 1:end])))
    return [text for _, text in sorted(found, key=lambda f: f[0])]  # stable: data sections first


def _existing_checkout(url: str, dest: Path) -> PinnedSource | None:
    """``dest`` as a pin when it already is a git checkout of ``url`` (a re-run), else None."""
    if not (dest / ".git").exists():
        return None
    def git(*args: str) -> str | None:
        proc = subprocess.run(["git", "-C", str(dest), *args], capture_output=True, text=True)
        return proc.stdout.strip() if proc.returncode == 0 else None
    remote, sha = git("remote", "get-url", "origin"), git("rev-parse", "HEAD")
    if remote is None or sha is None or remote.rstrip("/").removesuffix(".git") != url.rstrip("/").removesuffix(".git"):
        return None
    return PinnedSource(url=url, kind="git", version=sha, path=dest)


def _pin(links: list[str], data_dir: Path) -> tuple[dict | None, str]:
    """Pin the first link that can be pinned: (its source record, "") or (None, why none could be)."""
    failures: list[str] = []
    for url in links:
        if "github.com" not in url and "gitlab.com" not in url and "codeberg.org" not in url:
            failures.append(f"{url} is not a git repository, which is all that can be pinned yet")
            continue
        dest = data_dir / url.rstrip("/").rsplit("/", 1)[-1]
        try:
            pinned = _existing_checkout(url, dest) or pin_git(url, dest)
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

    sections = data_sections(paper_text)
    links = data_links("\n\n".join(sections))
    found_in, note = SECTION, ""
    if not links:  # a section that names no link is no proof the data is not shared: look everywhere, and say so
        links = data_links(paper_text)
        found_in = WHOLE_PAPER
        note = (" (no link in the data availability section; whole paper searched)" if sections
                else " (no data availability section; whole paper searched)")

    source, reason = None, ""
    if not links:
        reason = "data not shared" + note
    elif not online:
        reason = f"data was not fetched (offline or --no-search): {links[0]}{note}"
    else:
        source, failure = _pin(links, agent_dir / "data")
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
