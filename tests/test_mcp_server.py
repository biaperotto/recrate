"""The MCP server, used for real: launched over stdio and every tool called once on the WorkflowHub nuclei example.

Checks the science, not only that the tools exist: the reproduction counts 20 nuclei like the published test,
a run on the paper's image counts 20, files come back as artifacts with absolute paths, and a figure is recorded
in its run crate. ``draft_crate`` is the one tool not called: it rewrites the agent's crate (tested in test_agent).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from recrate.agent import agentify  # noqa: E402

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "workflowhub-nuclei-segmentation"
pytestmark = pytest.mark.skipif(not (EXAMPLE / "crate").exists(), reason="WorkflowHub example not present")
NOT_CALLED = {"draft_crate"}


def _text(result) -> str:
    return "\n".join(getattr(c, "text", "") for c in result.content if getattr(c, "type", "") == "text")


def _error(result) -> bool:
    return bool(getattr(result, "is_error", None) or getattr(result, "isError", None))


async def _session(agent_dir: Path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=sys.executable, args=["-m", "recrate.mcp_server", str(agent_dir)],
                                   cwd=str(agent_dir))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()
            tools = {t.name for t in (await s.list_tools()).tools}
            called: dict[str, object] = {}

            async def call(name: str, args: dict | None = None):
                r = await asyncio.wait_for(s.call_tool(name, args or {}), 300)
                txt = _text(r)
                called[name] = r
                try:
                    return r, json.loads(txt)
                except json.JSONDecodeError:
                    return r, txt

            return await _exercise(call, agent_dir, tools, called)


async def _exercise(call, agent_dir: Path, tools: set[str], called: dict) -> dict:
    from recrate.agent import PaperAgent

    agent = PaperAgent.load(agent_dir)
    m = agent.methods[0]
    data = str(agent.spec().example_data)

    _, card = await call("about_paper")
    assert card["methods"][0]["ladder"]["numbers"]["status"] == "passed" and card["verdict"].startswith("Reproduced")
    _, hits = await call("search_paper", {"query": "Otsu threshold"})
    assert hits
    _, desc = await call("describe_method", {"method": m.id})
    assert desc["tolerances"]["count"]["source"] == "paper-stated"
    _, check = await call("check_my_data", {"method": m.id, "data_path": data})
    assert check["can_run"]

    _, rep = await call("reproduce", {"method": m.id, "who": "MCP test"})
    assert "count 20 vs 20" in rep["message"] and rep["same_numbers"] == "1/1"
    assert rep["ladder"]["conclusion"]["status"] == "not checked"
    assert all(Path(a["path"]).is_absolute() and Path(a["path"]).exists() for a in rep["artifacts"])

    _, run = await call("run_on_my_data", {"method": m.id, "data_path": data, "who": "MCP test"})
    assert run["results_csv"].splitlines()[1].split(",")[1] == "20"  # 20 nuclei on the paper's image
    _, run2 = await call(next(t for t in tools if t.startswith("run_") and t not in
                              {"run_on_my_data", "run_followup_check"}), {"data_path": data})
    assert run2["results_csv"].splitlines()[1].split(",")[1] == "20"

    _, fig = await call("make_figure", {"run_crate": rep["run_crate"]})
    assert Path(fig["artifacts"][0]["path"]).exists()
    meta = json.loads((Path(rep["run_crate"]) / "ro-crate-metadata.json").read_text())
    assert any(e["@id"] == "outputs/figures/published_vs_reproduced.png" for e in meta["@graph"])

    _, fu = await call("followup_checks", {"method": m.id})
    assert {f["id"] for f in fu["proposed"]} >= {"software-version", "data-version"}
    _, nope = await call("run_followup_check", {"method": m.id, "check": "software-version"})
    assert not nope["ran"] and "needs" in nope["message"]  # proposed, but can't run here yet: says what it needs

    _, rev = await call("review_conclusion", {"method": m.id, "who": "MCP test", "agrees": "yes"})
    assert rev["ladder"]["conclusion"]["status"] == "passed"
    _, ok = await call("approve_run", {"run_crate": run["run_crate"], "who": "MCP test"})
    assert "approval" in str(ok)
    _, methods = await call("methods_text", {"run_crate": run["run_crate"]})
    assert "Workflow Run RO-Crate" in str(methods)
    _, pub = await call("publish_run", {"run_crate": run["run_crate"]})
    assert pub["artifacts"][0]["path"].endswith(".zip")
    _, record = await call("reproducibility_record")
    assert record["summary"]["reproduced"] >= 1

    for name, args in (("list_tables", {}), ("get_table", {"table": "Table 1"}), ("list_figures", {}),
                       ("get_figure", {"figure": "Figure 1"})):
        r, _ = await call(name, args)
        assert not _error(r)
    return {"tools": tools, "called": set(called)}


def test_every_tool_over_stdio(tmp_path):
    a = agentify(tmp_path / "agent", crate=EXAMPLE / "crate")
    result = asyncio.run(_session(a.path.resolve()))
    assert result["tools"] - result["called"] == NOT_CALLED
