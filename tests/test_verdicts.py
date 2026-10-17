"""What "reproduced" means: readiness, the verdict ladder, tolerance sources, follow-up checks, content checks
and figures, on the two real examples (WorkflowHub nuclei, penguins)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from recrate.agent import agentify, diagnose, review_conclusion
from recrate.figures import make_run_figure
from recrate.followup import followups_of, propose, run_followup
from recrate.pipeline import run_on_data
from recrate.runcrate import load_run_crate, run_ladder
from recrate.verify import check_description

ROOT = Path(__file__).resolve().parent.parent / "examples"
NUCLEI, PENGUINS = ROOT / "workflowhub-nuclei-segmentation", ROOT / "penguins-dimorphism"
pytestmark = pytest.mark.skipif(not (NUCLEI / "crate").exists() or not (PENGUINS / "crate").exists(),
                                reason="examples not present")


@pytest.fixture(scope="module")
def nuclei(tmp_path_factory):
    return agentify(tmp_path_factory.mktemp("n") / "agent", crate=NUCLEI / "crate")


@pytest.fixture(scope="module")
def penguins(tmp_path_factory):
    return agentify(tmp_path_factory.mktemp("p") / "agent", paper=PENGUINS / "paper.pdf", crate=PENGUINS / "crate")


def test_galaxy_workflow_is_ready_and_reproduced(nuclei):
    m = nuclei.methods[0]
    assert m.readiness == "ready-with-gaps" and "pixel size" in m.gaps[0]  # ready: not blocked, and says the gap
    assert m.ladder["runs"]["status"] == "passed" and m.ladder["numbers"]["status"] == "passed"
    assert m.ladder["conclusion"]["status"] == "not checked" and m.status == "validated"
    assert run_ladder(Path(m.run_crate))["numbers"]["reason"].startswith("1/1")


def test_galaxy_workflow_with_an_unported_tool_is_blocked_and_names_it(nuclei, tmp_path):
    crate = tmp_path / "crate"
    shutil.copytree(nuclei.crate_dir, crate)
    ga = next(crate.rglob("*.ga"))
    wf = json.loads(ga.read_text())
    step = next(s for s in wf["steps"].values() if "ip_threshold" in (s.get("tool_id") or ""))
    step["tool_id"] = "toolshed.g2.bx.psu.edu/repos/imgteam/2d_auto_threshold/ip_threshold_deep/1.0"
    ga.write_text(json.dumps(wf))
    from recrate.crate import load_crate

    readiness, gaps = diagnose(load_crate(crate))
    assert readiness == "blocked" and "ip_threshold_deep" in gaps[0]


def test_tolerances_need_a_source(tmp_path):
    desc = json.loads((PENGUINS / "crate_description.json").read_text())
    desc["methods"][0]["outputs"][0]["abs_tolerances"]["body_mass"] = 0.006  # a bare number: no source
    (tmp_path / "d.json").write_text(json.dumps(desc))
    for f in ("workflows", "data", "expected"):
        shutil.copytree(PENGUINS / f, tmp_path / f)
    shutil.copy2(PENGUINS / "paper.pdf", tmp_path)
    rep = check_description(tmp_path, tmp_path / "d.json")
    assert not rep.ok and any("body_mass has no source" in f.what for f in rep.findings)
    assert any("wider than the paper's rounding alone" in f.what for f in rep.findings)  # 0.006 > 0.005, said


def test_penguins_partly_reproduced_and_a_recorded_follow_up(penguins):
    m = penguins.methods[0]
    assert m.ladder["numbers"]["status"] == "partial" and m.ladder["numbers"]["reason"].startswith("9/12")
    assert "paper-rounding" in m.ladder["numbers"]["reason"] and m.status == "differs"
    assert penguins.card()["verdict"].startswith("Partly reproduced")
    clutch = next(f for f in propose(penguins.spec()) if f.kind == "inclusion rule")
    out = run_followup(penguins.spec(), clutch.id, penguins.runs_dir, who="test")
    assert out.comparison.count == "12/12"
    rows = (out.run_dir / "outputs" / "results.csv").read_text().splitlines()
    chinstrap = next(r for r in rows if r.startswith("Chinstrap")).split(",")
    assert int(chinstrap[-2]) + int(chinstrap[-1]) == 54  # the paper: "chinstrap n = 54" for the SDI
    assert [r["run"] for r in followups_of(penguins.runs_dir, m.run_crate)] == [out.run_dir.name]
    reloaded = json.loads((penguins.path / "agent.json").read_text())["methods"][0]
    assert reloaded["status"] == "differs" and reloaded["run_crate"] == m.run_crate  # the original stays


def test_only_a_person_sets_the_conclusion(penguins):
    m = review_conclusion(penguins.path, "size_dimorphism_index", "A. Reviewer", "partly", "same direction")
    assert m.ladder["conclusion"] == {"status": "partial", "reason": "Reviewed by A. Reviewer: same direction"}
    assert run_ladder(Path(m.run_crate))["conclusion"]["status"] == "partial"
    with pytest.raises(ValueError):
        review_conclusion(penguins.path, "size_dimorphism_index", "", "yes")


def test_figure_is_plotted_from_the_run_and_recorded(penguins):
    run = Path(penguins.methods[0].run_crate)
    path = make_run_figure(run)
    graph = {e["@id"]: e for e in load_run_crate(run)["@graph"]}
    assert path.exists() and "outputs/figures/published_vs_reproduced.png" in graph
    action = graph["#figure-published-vs-reproduced-png"]
    assert action["object"] == [{"@id": "comparison.csv"}] and "comparison.csv" in graph


def test_reuse_says_what_ran_and_flags_a_missed_rescaling(nuclei):
    half = NUCLEI / "my-data-half-resolution"
    if not half.exists():
        pytest.skip("run examples/workflowhub-nuclei-segmentation/make_half_resolution.py")
    adjusted = run_on_data(nuclei.spec(), half, nuclei.runs_dir, agent="test", pixel_ratio=2)
    root = next(e for e in load_run_crate(adjusted.run_dir)["@graph"] if e["@id"] == "./")
    assert root["description"].startswith("Reuse of segmentation_and_counting") and "on 3 input file(s)" in root["description"]
    assert "filter_size 3.0 → 1.5" in root["description"]
    size = next(i for i in adjusted.check.items if i.name == "Object size")
    assert size.status == "ok"
    unadjusted = run_on_data(nuclei.spec(), half, nuclei.runs_dir, agent="test")  # no pixel ratio given
    size = next(i for i in unadjusted.check.items if i.name == "Object size")
    assert size.status == "warn" and "pixel sizes unknown" in size.observed
