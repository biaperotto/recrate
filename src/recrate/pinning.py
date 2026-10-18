"""Find a paper's data and pin it to an exact version, so a claim can say what it was checked against.

"The data in that GitHub repo" is not reproducible: the repo moves. ``pin_git`` therefore resolves whatever
was asked for (the default branch, a branch, a tag, a commit) to a full 40-character commit SHA and records
that, never a name that can later point somewhere else. ``file_record`` records one file as the crate will
need it: its name, sha256, size, upstream URL and licence. ``data_links`` finds the repository and archive
links a paper's text names, reusing ``sources.identify`` so there is one definition of what a link looks like.

Nothing here talks to a network service other than the ``git`` remote it is told to clone.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .sources import identify


@dataclass
class PinnedSource:
    url: str
    kind: str  # "git" | "zenodo"
    version: str  # a 40-character commit SHA for git
    path: Path


def _run(cmd: list[str], what: str) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"{what}: {proc.stderr.strip()[-500:]}")
    return proc.stdout.strip()


def pin_git(url: str, dest: Path, ref: str | None = None) -> PinnedSource:
    """Clone ``url`` into ``dest`` and pin it to the commit it is at: ``ref`` (branch, tag or commit) when
    given, else the remote's default branch. ``PinnedSource.version`` is always the resolved SHA.

    Raises ``RuntimeError`` on any failure, and never leaves a ``dest`` it created behind. A ``dest`` that
    already exists is not removed: git refuses a non-empty one, which surfaces as the same error.
    """
    dest = Path(dest)
    if ref is not None and ref.startswith("-"):
        raise RuntimeError(f"Could not pin {url}: {ref!r} is not a valid git ref")
    created = not dest.exists()
    try:
        # --depth 1 cannot check out an arbitrary commit, so only take the shortcut when none is asked for.
        clone = ["git", "clone"] + (["--depth", "1"] if ref is None else []) + ["--", url, str(dest)]
        _run(clone, f"Could not clone {url}")
        if ref is not None:
            sha = _resolve(dest, ref, url)
            _run(["git", "-C", str(dest), "checkout", "-q", "--detach", sha], f"Could not check out {ref} of {url}")
        version = _run(["git", "-C", str(dest), "rev-parse", "HEAD"], f"Could not resolve the commit of {url}")
    except Exception:
        if created and dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        raise
    return PinnedSource(url=url, kind="git", version=version, path=dest)


def _resolve(repo: Path, ref: str, url: str) -> str:
    """The commit SHA ``ref`` names in ``repo``; a branch that exists only on the remote counts."""
    last = ""
    for candidate in (ref, f"origin/{ref}"):
        proc = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}"],
                              capture_output=True, text=True)
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
        last = proc.stderr.strip()
    raise RuntimeError(f"Could not find {ref} in {url}" + (f": {last[-500:]}" if last else ""))


def file_record(path: Path, upstream: str, license: str | None = None) -> dict:
    """What the crate needs to know about one data file: its bytes' sha256 and size, where it came from."""
    path = Path(path)
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
            size += len(chunk)
    return {"name": path.name, "sha256": digest.hexdigest(), "size": size, "url": upstream, "license": license}


def data_links(paper_text: str) -> list[str]:
    """Repository and archive URLs the text names, most specific first, without duplicates; ``[]`` if none.

    Only links the text contains: a Zenodo DOI becomes its record URL, ordinary paper DOIs are not data.
    """
    links: list[str] = []
    for ref in identify(paper_text):
        if ref.kind == "repo":
            link = ref.id
        elif ref.kind == "zenodo":
            link = f"https://zenodo.org/records/{ref.id}"
        else:
            continue
        if link not in links:
            links.append(link)
    return links
