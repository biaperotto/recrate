"""Pinning: a paper's data is located and fixed to an exact version, offline (tests clone a repo they build)."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from recrate.pinning import data_links, file_record, pin_git

# No dependence on the machine's git identity or default branch name, and no hint text on stderr.
_GIT = ["git", "-c", "init.defaultBranch=main", "-c", "user.email=test@example.com", "-c", "user.name=Test",
        "-c", "commit.gpgsign=false"]


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run([*_GIT, *args], cwd=repo, capture_output=True, text=True, check=True)
    return proc.stdout.strip()


def _commit(repo: Path, files: dict[str, str], message: str) -> str:
    for name, content in files.items():
        f = repo / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _make_repo(path: Path, files: dict[str, str]) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    _commit(path, files, "initial")
    return path


def _make_repo_two_commits(path: Path) -> tuple[Path, str]:
    _make_repo(path, {"data/x.csv": "a,b\n1,2\n"})
    first = _git(path, "rev-parse", "HEAD")
    _commit(path, {"data/x.csv": "a,b\n3,4\n"}, "second")
    return path, first


def test_pin_git_records_the_resolved_commit(tmp_path):
    origin = _make_repo(tmp_path / "origin", {"data/x.csv": "a,b\n1,2\n"})
    p = pin_git(str(origin), tmp_path / "work")
    assert len(p.version) == 40 and (p.path / "data" / "x.csv").exists()
    assert p.version == _git(origin, "rev-parse", "HEAD")
    assert p.kind == "git" and p.url == str(origin)


def test_pin_git_checks_out_a_named_commit(tmp_path):
    origin, first = _make_repo_two_commits(tmp_path / "origin")
    p = pin_git(str(origin), tmp_path / "work", ref=first)
    assert p.version == first
    assert (p.path / "data" / "x.csv").read_text() == "a,b\n1,2\n"


def test_pin_git_resolves_a_branch_name_to_a_sha(tmp_path):
    origin, first = _make_repo_two_commits(tmp_path / "origin")
    p = pin_git(str(origin), tmp_path / "work", ref="main")
    assert len(p.version) == 40 and p.version == _git(origin, "rev-parse", "HEAD")


def test_pin_git_unknown_ref_raises_and_leaves_nothing_behind(tmp_path):
    origin = _make_repo(tmp_path / "origin", {"a.txt": "a"})
    with pytest.raises(RuntimeError, match="no-such-ref"):
        pin_git(str(origin), tmp_path / "work", ref="no-such-ref")
    assert not (tmp_path / "work").exists()


def test_pin_git_failed_clone_raises_and_leaves_nothing_behind(tmp_path):
    with pytest.raises(RuntimeError, match="Could not clone"):
        pin_git(str(tmp_path / "not-a-repo"), tmp_path / "work")
    assert not (tmp_path / "work").exists()


def test_file_record_hashes_the_file(tmp_path):
    f = tmp_path / "x.csv"; f.write_text("a,b\n1,2\n")
    r = file_record(f, upstream="https://example.org/x.csv", license="CC-BY-NC-SA-4.0")
    assert r["sha256"] == hashlib.sha256(f.read_bytes()).hexdigest()
    assert r["size"] == f.stat().st_size and r["license"] == "CC-BY-NC-SA-4.0"
    assert r["name"] == "x.csv" and r["url"] == "https://example.org/x.csv"


def test_file_record_license_is_optional(tmp_path):
    f = tmp_path / "x.csv"; f.write_text("a")
    assert file_record(f, upstream="https://example.org/x.csv")["license"] is None


def test_data_links_finds_a_github_repo():
    assert data_links("Data and code: https://github.com/o/r") == ["https://github.com/o/r"]


def test_data_links_are_deduplicated_and_skip_papers():
    text = ("Code: https://github.com/o/r. See also https://github.com/o/r and the paper "
            "doi:10.1038/s41586-020-2649-2. Data: https://zenodo.org/records/3362976.")
    assert data_links(text) == ["https://zenodo.org/records/3362976", "https://github.com/o/r"]


def test_no_data_statement_yields_no_links():              # Review Focus 1
    assert data_links("The authors declare no competing interests.") == []
