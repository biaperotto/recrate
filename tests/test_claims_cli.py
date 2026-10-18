"""`recrate claims`: extraction, the gate and pinning joined into one claims.json. Offline, no API key."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from recrate import claim_run
from recrate.claim_run import run_claims
from recrate.claims import RESULTS, load_claims
from recrate.cli import main
from recrate.pinning import PinnedSource

# Anchors and values below are printed verbatim in the demo paper the `agent` fixture is built from.
_RESPONSE = json.dumps({"claims": [
    {"claim": "Each field held about 22 nuclei.", "section": "Results",
     "anchor": "the mean count was 22.0 nuclei per field", "paper_says": "22.0"},
    {"claim": "Mean nuclear area was about 107.5 square micrometres.", "section": "Results",
     "anchor": "the mean nuclear area was 107.5 µm²", "paper_says": "107.5"},
    {"claim": "About a sixth of nuclei were large.", "section": "Results",
     "anchor": "17% of nuclei were larger than 150 µm²", "paper_says": "17%"},
    {"claim": "A number the paper never prints.", "section": "Results",
     "anchor": "the mean count was 22.0 nuclei per field", "paper_says": "99.9"},
]})
_REPO = "https://github.com/someone/their-data"
_CITED = "https://github.com/other/cited-tool"


def _replay(prompt: str) -> str:
    return _RESPONSE


def _replay_no_data(prompt: str) -> str:
    # guards the fixture: the demo paper names no repository, so there is no data to pin
    assert "github.com" not in prompt
    return _RESPONSE


def _set_paper(agent, text: str) -> None:
    (agent.path / "paper" / "paper.md").write_text(text, encoding="utf-8")


def _paper_with(agent, availability: str, trailer: str = "") -> str:
    base = (agent.path / "paper" / "paper.md").read_text(encoding="utf-8")
    head = base.split("## Data and code availability")[0]
    return f"{head}## Data and code availability\n\n{availability}\n{trailer}"


def _fake_pin(calls: list):
    def pin(url, dest, ref=None):
        calls.append(url)
        dest = Path(dest)
        dest.mkdir(parents=True)
        (dest / "table.csv").write_text("a,b\n1,2\n", encoding="utf-8")
        (dest / ".git").mkdir()
        (dest / ".git" / "HEAD").write_text("x", encoding="utf-8")
        return PinnedSource(url=url, kind="git", version="a" * 40, path=dest)
    return pin


def test_writes_claims_json_for_an_agent(tmp_path, agent):
    out = run_claims(agent.path, respond=_replay, online=False)
    assert (agent.path / "claims.json").exists()
    assert all(c.result in RESULTS for c in load_claims(agent.path / "claims.json"))
    assert [c.id for c in out] == [c.id for c in load_claims(agent.path / "claims.json")]


def test_no_data_statement_still_writes_every_row(tmp_path, agent):   # Review Focus 1
    claims = run_claims(agent.path, respond=_replay_no_data, online=False)
    assert claims and all(c.result == "Not checkable" for c in claims)
    assert all(c.why_not for c in claims)
    assert any(c.why_not.startswith("data not shared") for c in claims)


def test_cli_claims_command(tmp_path, agent, capsys, monkeypatch):
    monkeypatch.setattr("recrate.claim_extract._call_claude", _replay)
    assert main(["claims", str(agent.path)]) == 0
    out = capsys.readouterr().out
    assert "claims.json" in out
    assert "c1" in out


def test_gate_reasons_are_never_overwritten(agent):
    claims = run_claims(agent.path, respond=_replay, online=False)
    unprinted = [c for c in claims if c.paper_says == "99.9"][0]
    assert unprinted.why_not == "the reported value is not printed in the paper"


def test_missing_paper_fails_clearly(tmp_path):
    (tmp_path / "agent").mkdir()
    with pytest.raises(FileNotFoundError, match="paper.md"):
        run_claims(tmp_path / "agent", respond=_replay, online=False)
    assert not (tmp_path / "agent" / "claims.json").exists()


def test_offline_with_a_data_link_is_not_called_unshared(agent):
    _set_paper(agent, _paper_with(agent, f"The data are at {_REPO}."))
    claims = run_claims(agent.path, respond=_replay, online=False)
    reasons = {c.why_not for c in claims if c.paper_says != "99.9"}
    assert len(reasons) == 1
    (reason,) = reasons
    assert "not fetched" in reason and "offline" in reason and "not shared" not in reason
    assert not (agent.path / "data").exists()


def test_pins_the_data_section_link_and_not_a_cited_repository(agent, monkeypatch):
    _set_paper(agent, _paper_with(agent, f"The data are at {_REPO}.",
                                  f"\n## References\n\n1. A tool. {_CITED}\n"))
    calls: list[str] = []
    monkeypatch.setattr(claim_run, "pin_git", _fake_pin(calls))
    claims = run_claims(agent.path, respond=_replay, online=True)
    assert calls == [_REPO]
    good = [c for c in claims if c.result == "Not checked"]
    assert len(good) == 3
    for c in good:
        src = c.data_source
        assert src["url"] == _REPO and src["version"] == "a" * 40
        assert src["found_in"] == "data availability section"
        names = [f["name"] for f in src["files"]]
        assert names == ["their-data/table.csv"]           # relative to data/; the .git folder is not data
        assert len(src["files"][0]["sha256"]) == 64
    assert [c for c in claims if c.paper_says == "99.9"][0].result == "Not checkable"
    assert (agent.path / "data" / "their-data" / "table.csv").exists()


def test_pin_failure_names_the_failure(agent, monkeypatch):
    _set_paper(agent, _paper_with(agent, f"The data are at {_REPO}."))

    def boom(url, dest, ref=None):
        raise RuntimeError("Could not clone: repository not found")
    monkeypatch.setattr(claim_run, "pin_git", boom)
    claims = run_claims(agent.path, respond=_replay, online=True)
    for c in claims:
        assert c.result == "Not checkable"
    assert all("repository not found" in c.why_not for c in claims if c.paper_says != "99.9")


def test_without_a_data_section_the_whole_paper_is_searched_and_says_so(agent, monkeypatch):
    base = (agent.path / "paper" / "paper.md").read_text(encoding="utf-8").split("## Data and code availability")[0]
    _set_paper(agent, base + f"\nSee also {_REPO} for the images.\n")
    calls: list[str] = []
    monkeypatch.setattr(claim_run, "pin_git", _fake_pin(calls))
    claims = run_claims(agent.path, respond=_replay, online=True)
    assert calls == [_REPO]
    good = [c for c in claims if c.result == "Not checked"]
    assert good and all(c.data_source["found_in"].startswith("whole paper") for c in good)


def test_cli_no_search_skips_pinning(agent, capsys, monkeypatch):
    _set_paper(agent, _paper_with(agent, f"The data are at {_REPO}."))
    monkeypatch.setattr("recrate.claim_extract._call_claude", _replay)
    calls: list[str] = []
    monkeypatch.setattr(claim_run, "pin_git", _fake_pin(calls))
    assert main(["claims", str(agent.path), "--no-search"]) == 0
    assert calls == []
    out = capsys.readouterr().out
    assert "not fetched" in out and "not shared" not in out


def test_cli_reports_a_missing_paper(tmp_path, capsys):
    (tmp_path / "a").mkdir()
    assert main(["claims", str(tmp_path / "a")]) == 2
    assert "paper.md" in capsys.readouterr().err


_CODE_REPO = "https://github.com/someone/their-code"


def test_a_plain_text_section_with_a_link_on_its_own_line_keeps_the_link(agent, monkeypatch):
    base = (agent.path / "paper" / "paper.md").read_text(encoding="utf-8").split("## Data and code availability")[0]
    plain = base.replace("## ", "").replace("# ", "")
    _set_paper(agent, plain + f"\nData availability\n\nThe images are deposited at\n\n{_REPO}\n\nMethods\n\nText.\n")
    calls: list[str] = []
    monkeypatch.setattr(claim_run, "pin_git", _fake_pin(calls))
    claims = run_claims(agent.path, respond=_replay, online=True)
    assert calls == [_REPO]
    good = [c for c in claims if c.result == "Not checked"]
    assert good and all(c.data_source["found_in"] == "data availability section" for c in good)


def test_a_section_without_a_link_falls_back_to_the_whole_paper(agent, monkeypatch):
    base = (agent.path / "paper" / "paper.md").read_text(encoding="utf-8").split("## Data and code availability")[0]
    _set_paper(agent, base + f"See {_REPO} for the images.\n\n## Data availability\n\nOn request.\n")
    monkeypatch.setattr(claim_run, "pin_git", _fake_pin([]))
    claims = run_claims(agent.path, respond=_replay, online=True)
    good = [c for c in claims if c.result == "Not checked"]
    assert good and all(c.data_source["found_in"].startswith("whole paper") for c in good)
    # and with nothing anywhere it still says so, rather than inventing a link
    _set_paper(agent, base + "## Data availability\n\nOn request.\n")
    claims = run_claims(agent.path, respond=_replay, online=False)
    assert all(c.why_not.startswith("data not shared") for c in claims if c.paper_says != "99.9")


def test_separate_code_and_data_sections_pin_the_data_repository(agent, monkeypatch):
    _set_paper(agent, _paper_with(agent, f"The scripts are at {_CODE_REPO}.").replace(
        "## Data and code availability", "## Code availability") + f"\n## Data availability\n\nThe images: {_REPO}\n")
    calls: list[str] = []
    monkeypatch.setattr(claim_run, "pin_git", _fake_pin(calls))
    run_claims(agent.path, respond=_replay, online=True)
    assert calls == [_REPO]


def test_rerun_reuses_the_pinned_checkout_and_gives_the_same_rows(agent, monkeypatch):
    _set_paper(agent, _paper_with(agent, f"The data are at {_REPO}."))

    def pin(url, dest, ref=None):
        dest = Path(dest)
        dest.mkdir(parents=True)
        (dest / "table.csv").write_text("a,b\n1,2\n", encoding="utf-8")
        for cmd in (["init", "-q"], ["remote", "add", "origin", url], ["add", "."],
                    ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x"]):
            subprocess.run(["git", "-C", str(dest), *cmd], check=True, capture_output=True)
        sha = subprocess.run(["git", "-C", str(dest), "rev-parse", "HEAD"], check=True, capture_output=True,
                             text=True).stdout.strip()
        return PinnedSource(url=url, kind="git", version=sha, path=dest)

    monkeypatch.setattr(claim_run, "pin_git", pin)
    first = run_claims(agent.path, respond=_replay, online=True)

    def refuse(url, dest, ref=None):
        raise RuntimeError("fatal: destination path already exists and is not an empty directory")
    monkeypatch.setattr(claim_run, "pin_git", refuse)
    second = run_claims(agent.path, respond=_replay, online=True)
    assert [c.to_json() for c in second] == [c.to_json() for c in first]
    assert any(c.result == "Not checked" and c.data_source["version"] for c in second)
