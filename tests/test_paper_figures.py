"""The paper's figures, recreated by a method, paired with the originals and reviewed by a person."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from recrate.paper_figures import figure_label, figure_reviews, recreated, review_figure

PENGUINS = Path(__file__).resolve().parent.parent / "examples" / "penguins-dimorphism"


def test_figure_names():
    assert figure_label("figure-3.png") == "Figure 3"
    assert figure_label("fig2b.png") == "Figure 2" and figure_label("Figure_10.svg") == "Figure 10"
    assert figure_label("published_vs_reproduced.png") is None


@pytest.mark.skipif(not (PENGUINS / "crate").exists(), reason="penguin example not present")
def test_isotope_figures_recreated_and_reviewed(tmp_path):
    from recrate.agent import agentify

    agent = agentify(tmp_path / "agent", paper=PENGUINS / "paper.pdf", crate=PENGUINS / "crate")
    m = next(x for x in agent.methods if x.name == "isotope_signatures")
    # the captions' sample sizes don't come back from the published data: a finding, value by value
    assert m.status == "differs" and "Adelie 139 vs 127" in m.detail
    run = Path(m.run_crate)
    assert sorted(p.name for p in (run / "outputs" / "figures").glob("figure-*.png")) == \
        ["figure-3.png", "figure-4.png", "figure-5.png"]

    paper = tmp_path / "paper"  # a reading package with the original Figure 3
    (paper / "figures").mkdir(parents=True)
    shutil.copy(run / "outputs" / "figures" / "figure-3.png", paper / "figures" / "figure-3.jpg")
    (paper / "package.json").write_text(json.dumps({"figures": [
        {"label": "Figure 3", "caption": "Adélie isotopes", "file": "figures/figure-3.jpg"}]}))
    pairs = {f["label"]: f for f in recreated(run, paper)}
    assert pairs["Figure 3"]["original"].name == "figure-3.jpg" and pairs["Figure 4"]["original"] is None

    with pytest.raises(ValueError):
        review_figure(run, "anonymous", "figure-3.png", "same")
    review_figure(run, "A. Reviewer", "figure-3.png", "same", "same pattern each year")
    assert figure_reviews(run)["figure-3.png"]["value"] == "same"
    g = json.loads((run / "ro-crate-metadata.json").read_text())["@graph"]
    assert next(e for e in g if e["@id"] == "outputs/figures/figure-3.png")["description"].startswith("Recreates Figure 3")
