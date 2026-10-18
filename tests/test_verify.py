"""recrate check-description: a drafted crate is checked against the paper's text."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from recrate.agent import agentify
from recrate.verify import _positions, check_description

EX = Path(__file__).resolve().parent.parent / "examples" / "penguins-dimorphism"
pytestmark = pytest.mark.skipif(not (EX / "crate").exists(), reason="penguin example not present")


@pytest.fixture(scope="module")
def agent_dir(tmp_path_factory):
    return agentify(tmp_path_factory.mktemp("p") / "agent", paper=EX / "paper.pdf", crate=EX / "crate").path


def _desc(tmp_path: Path, change=None) -> Path:
    d = json.loads((EX / "crate_description.json").read_text())
    d["paper_file"] = str(EX / d["paper_file"])
    for m in d["methods"]:
        m["workflow"]["file"] = str(EX / m["workflow"]["file"])
        m["example_data"], m["expected"] = str(EX / m["example_data"]), str(EX / m["expected"])
    if change:
        change(d["methods"][0], tmp_path)
    p = tmp_path / "desc.json"
    p.write_text(json.dumps(d))
    return p


def test_the_real_description_passes(agent_dir, tmp_path):
    rep = check_description(agent_dir, _desc(tmp_path))
    assert rep.ok and rep.checked["published_values"] == 12 + 6  # Table 2, and the sample sizes of Figures 3-5


def test_an_invented_quote_is_caught(agent_dir, tmp_path):
    def change(m, _):
        m["inputs"][1]["quote"] = "Penguins were grouped by species and colour."
    rep = check_description(agent_dir, _desc(tmp_path, change))
    assert not rep.ok and any("quote not found" in f.what for f in rep.findings)


def test_a_value_the_paper_does_not_print_together_is_caught(agent_dir, tmp_path):
    def change(m, tmp):
        bad = tmp / "bad.csv"
        bad.write_text(Path(m["expected"]).read_text().replace("-0.20", "-0.27"))
        m["expected"] = str(bad)
    rep = check_description(agent_dir, _desc(tmp_path, change))
    assert not rep.ok and any("not printed together" in f.what for f in rep.findings)


def test_symbol_font_minus_in_pdf_text():
    assert _positions("Culmen length 0.816 20.68760.37 20.11", "-0.11")
