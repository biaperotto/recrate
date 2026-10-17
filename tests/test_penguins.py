"""A real paper outside imaging: Gorman et al. 2014 (PLOS ONE), its Table 2 recomputed from open data."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from recrate.agent import agentify
from recrate.pipeline import check_inputs, run_on_data

EX = Path(__file__).resolve().parent.parent / "examples" / "penguins-dimorphism"
pytestmark = pytest.mark.skipif(not (EX / "crate").exists(), reason="penguin example not present")


@pytest.fixture()
def penguins(tmp_path):
    return agentify(tmp_path / "agent", paper=EX / "paper.pdf", crate=EX / "crate")


def test_validation_says_exactly_what_differs(penguins):
    m = penguins.methods[0]
    assert m.status == "differs" and "Found 3 of 12 published values" in m.detail and "other 9 reproduce" in m.detail
    assert m.detail.count("Chinstrap") == 3 and "Gentoo" not in m.detail.split("differs:")[-1]
    assert penguins.card()["published"] == "2014-03-05"


def test_reuse_with_a_recorded_change_of_grouping(penguins):
    out = run_on_data(penguins.spec(), EX / "research-data", penguins.runs_dir, agent="test",
                      overrides={"group_column": ("Island", "compare colonies")})
    rows = (out.run_dir / "outputs" / "results.csv").read_text().splitlines()
    assert [r.split(",")[0] for r in rows[1:]] == ["Biscoe", "Dream", "Torgersen"]


def test_tables_are_checked_for_the_columns_the_method_needs(penguins, tmp_path):
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "t.csv").write_text("Species,Sex,Mass\nA,MALE,1\n")
    item = next(i for i in check_inputs(penguins.spec(), bad).items if i.name == "Columns")
    assert item.status == "fail" and "Culmen Length (mm)" in item.observed
