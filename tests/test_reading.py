"""The reading package: tables and figures from JATS, and the tools that expose them."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from recrate.reading import figures, from_jats, tables, tables_text

PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da63f8ff"
                    "ff3f0005fe02fea7d6a4b30000000049454e44ae426082")

JATS = """<article xmlns:xlink="http://www.w3.org/1999/xlink"><front><journal-meta><journal-title-group>
<journal-title>J Test</journal-title></journal-title-group></journal-meta><article-meta>
<article-id pub-id-type="doi">10.1/x</article-id><title-group><article-title>A paper</article-title></title-group>
<contrib-group><contrib contrib-type="author"><name><surname>Doe</surname><given-names>Ann</given-names></name></contrib></contrib-group>
<contrib-group><contrib contrib-type="editor"><name><surname>Ed</surname></name><role>Editor</role></contrib></contrib-group>
<pub-date pub-type="epub"><day>5</day><month>3</month><year>2014</year></pub-date></article-meta></front>
<body><sec><title>Results</title><p>Body sizes differ.</p>
<table-wrap><label>Table 2</label><caption><p>Dimorphism index.</p></caption><table>
<thead><tr><th>Species</th><th>SDI</th></tr></thead><tbody><tr><td>Adelie</td><td>&#8722;0.06</td></tr></tbody></table></table-wrap>
<fig><label>Figure 1</label><caption><p>Map.</p></caption><graphic xlink:href="pone.g001"/></fig>
<supplementary-material><label>S1</label><media xlink:href="../evil.csv"/></supplementary-material>
</sec></body></article>"""


def test_from_jats(tmp_path):
    d = tmp_path / "paper"
    from_jats(JATS, d, {"pone.g001.png": PNG, "../evil.csv": b"a,b\n"})
    assert "Doe" in (d / "paper.md").read_text() and "Ed" not in (d / "paper.md").read_text().split("\n")[2]
    assert [t["label"] for t in tables(d)] == ["Table 2"]
    assert "Adelie | −0.06" in tables_text(d)
    assert figures(d)[0]["file"] == "figures/figure-1.png"
    assert (d / "supplement" / "evil.csv").exists() and not (tmp_path / "evil.csv").exists()


EX = Path(__file__).resolve().parent.parent / "examples" / "penguins-dimorphism"


@pytest.mark.skipif(not (EX / "crate").exists(), reason="penguin example not present")
def test_chat_tools_read_tables_and_figures(tmp_path):
    from recrate.agent import agentify
    from recrate.chat import ChatTools

    agent = agentify(tmp_path / "agent", paper=EX / "paper.pdf", crate=EX / "crate")
    from_jats(JATS, agent.path / "paper", {"pone.g001.png": PNG})
    tools = ChatTools(agent, tmp_path / "up")
    assert "Table 2" in tools.call("list_tables", {}).text
    res = tools.call("get_table", {"table": "table 2"})
    assert json.loads(res.text)["rows"][0]["SDI"] == "−0.06"
    fig = tools.call("show_figure", {"figure": "Figure 1"})
    assert fig.attachments[0]["type"] == "figure" and Path(fig.attachments[0]["path"]).exists()
    assert "No figure" in tools.call("show_figure", {"figure": "Figure 9"}).text
