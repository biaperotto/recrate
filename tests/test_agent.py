from __future__ import annotations

import asyncio
import json

import pytest

from recrate.agent import PaperAgent, agentify
from recrate.crate import CrateError, list_methods, load_crate
from recrate.draft import build_crate_from_json
from recrate.paper import read_paper, search


def test_agentify_with_crate_validates_every_method(agent):
    card = agent.card()
    assert card["crate_source"] == "supplied"
    assert card["doi"] == "https://doi.org/10.0000/recrate-demo-paper"
    assert {m["name"]: m["status"] for m in card["methods"]} == {
        "count_nuclei": "validated", "size_distribution": "validated"}


def test_paper_only_agent_answers_from_the_paper(demo, tmp_path):
    a = agentify(tmp_path / "paper_only", paper=demo["paper"])
    card = a.card()
    assert card["crate_source"] == "recreated from the paper" and card["draft"] and a.methods == []
    assert (a.path / "DRAFT_ME.md").exists() and not a.has_methods
    names = {s.name for s in a.environment.software}
    assert {"Fiji", "scikit-image"} <= names  # software the paper names, recorded in the recreated crate
    assert (a.crate_dir / "environment" / "requirements.txt").exists()
    hits = a.search("pixel size")
    assert hits and "0.65" in hits[0]["text"]
    with pytest.raises(CrateError):
        a.spec()


def test_paper_only_then_drafted_crate(demo, tmp_path):
    a = agentify(tmp_path / "later", paper=demo["paper"])
    desc = json.loads((demo["crate"].parent / "paper_crate_description.json").read_text(encoding="utf-8"))
    for m in desc["methods"]:  # point at the demo files
        m["workflow"]["file"] = str(demo["crate"].parent / m["workflow"]["file"])
        m["example_data"] = str(demo["crate"].parent / m["example_data"])
        m["expected"] = str(demo["crate"].parent / m["expected"])
    desc["draft"] = True
    (tmp_path / "desc.json").write_text(json.dumps(desc), encoding="utf-8")
    build_crate_from_json(tmp_path / "desc.json", a.path / "crate")
    from recrate.agent import revalidate

    b = revalidate(a.path, runner="auto")
    assert b.card()["draft"] is True
    assert all(m.status == "validated" for m in b.methods)


def test_bare_crate_works_as_agent(demo):
    a = PaperAgent.load(demo["crate"])
    assert [m.name for m in a.methods] == ["count_nuclei", "size_distribution"]
    assert a.spec("size_distribution").workflow_language == "Python"


def test_crate_lists_methods(demo):
    names = [m["name"] for m in list_methods(demo["crate"])]
    assert names == ["count_nuclei", "size_distribution"]
    spec = load_crate(demo["crate"], "size_distribution")
    assert spec.tolerances() == {"median_area": 0.10, "fraction_large": 0.15}
    assert spec.example_data.exists() and spec.expected_results.exists()


def test_read_markdown_paper(demo):
    p = read_paper(demo["paper"])
    assert p.title.startswith("Counting and sizing nuclei")
    assert p.doi == "10.0000/recrate-demo-paper"
    assert search(p.text, "fraction larger than 150")[0]["text"].count("150")


def test_mcp_server_exposes_paper_methods_as_tools(agent, demo):
    pytest.importorskip("mcp")
    from recrate.mcp_server import build_server

    server = build_server(agent.path)

    async def go():
        tools = {t.name for t in await server.list_tools()}
        assert {"search_paper", "check_my_data", "run_count_nuclei", "run_size_distribution",
                "reproducibility_record", "draft_crate"} <= tools
        resources = {str(r.uri) for r in await server.list_resources()}
        assert {"paper://text", "paper://info", "paper://crate"} <= resources
        res = await server.call_tool("run_count_nuclei", {"data_path": str(demo["my_images"]), "who": "Anna"})
        text = res.content[0].text if hasattr(res, "content") else str(res)
        assert '"min_size_px": 12.5' in text

    asyncio.run(go())
