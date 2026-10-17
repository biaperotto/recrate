"""Registering an agent with an MCP client: absolute paths, the client's other settings kept, and a server that starts."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from recrate.agent import agentify  # noqa: E402
from recrate.install import install  # noqa: E402

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "workflowhub-nuclei-segmentation"
pytestmark = pytest.mark.skipif(not (EXAMPLE / "crate").exists(), reason="WorkflowHub example not present")


def test_install_into_a_client_config(tmp_path):
    a = agentify(tmp_path / "agent", crate=EXAMPLE / "crate", validate="none")
    config = tmp_path / "claude_desktop_config.json"
    config.write_text(json.dumps({"preferences": {"keep": True}, "mcpServers": {"other": {"command": "x"}}}))
    result = install(a.path, client="desktop", config_path=config)
    written = json.loads(config.read_text())
    entry = written["mcpServers"][result["name"]]
    assert written["preferences"] == {"keep": True} and "other" in written["mcpServers"]  # nothing lost
    assert Path(result["backup"]).exists()
    assert entry == {"command": sys.executable, "args": ["-m", "recrate.mcp_server", str(a.path.resolve())]}
    assert Path(entry["command"]).is_absolute() and result["name"].startswith("recrate-segmentation")
    assert result["check"]["ok"], result["check"]  # started over stdio, as the client will


def test_install_refuses_a_folder_that_is_not_an_agent(tmp_path):
    with pytest.raises(ValueError, match="agentify"):
        install(tmp_path, client="print", check=False)
