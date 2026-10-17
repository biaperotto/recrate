from __future__ import annotations

import asyncio
import json

import pytest

from httpfake import FakeHTTP
from recrate.agent import agentify
from recrate.paper import Paper


def test_agentify_stores_diagnosis_and_readiness(agent):
    card = agent.card()
    assert card["crate_source"] == "supplied"
    assert card["readiness"] == "ready"
    diag = agent.diagnosis()
    assert diag and diag["readiness"] == "ready"
    assert {m["name"] for m in diag["methods"]} == {"count_nuclei", "size_distribution"}


def test_agentify_locate_wires_in_the_found_crate(demo, tmp_path, monkeypatch):
    # The paper is local; --locate finds the crate online via signposting -> Zenodo zip.
    import io
    import zipfile
    from pathlib import Path

    crate_dir = Path(demo["crate"])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for f in sorted(crate_dir.rglob("*")):
            if f.is_file():
                zf.writestr(f.relative_to(crate_dir).as_posix(), f.read_bytes())

    rec = "https://zenodo.org/api/records/42"
    dl = "https://zenodo.org/api/records/42/files/x.crate.zip/content"
    fake = FakeHTTP()
    fake.add(rec, text=json.dumps({"files": [{"key": "x.crate.zip", "links": {"self": dl}}]}))
    fake.add(dl, content=buf.getvalue())
    monkeypatch.setattr("recrate.http.get", fake.get)

    # A local-file paper but force the located signposting by patching fetch.
    import recrate.agent as agent_mod

    def fake_fetch(link, work_dir, pdf=None):
        from recrate.fetch import FetchResult
        p = Paper(title="Demo", text="x", source=link, dois=["10.0000/recrate-demo-paper"])
        return FetchResult(paper=p, doi=p.doi, landing_url="https://example.org",
                           full_text=True, kind="url",
                           signposting=[{"url": rec, "rel": "describedby"}])
    monkeypatch.setattr("recrate.fetch.fetch_paper", fake_fetch)

    a = agentify(tmp_path / "agent", paper="https://doi.org/10.0000/recrate-demo-paper", locate=True)
    assert a.card()["crate_source"] == "found"
    assert a.card()["crate_url"] == rec
    assert a.card()["readiness"] == "ready"


def test_mcp_exposes_diagnosis(agent):
    pytest.importorskip("mcp")
    from recrate.mcp_server import build_server

    server = build_server(agent.path)

    async def go():
        tools = {t.name for t in await server.list_tools()}
        assert "diagnosis" in tools
        resources = {str(r.uri) for r in await server.list_resources()}
        assert "paper://diagnosis" in resources
        res = await server.call_tool("diagnosis", {})
        text = res.content[0].text if hasattr(res, "content") else str(res)
        assert "ready" in text

    asyncio.run(go())
