"""The app's pages render without an exception: the welcome page, the workspace with a research object open (its
Reproduce, Reuse and Record sections), and the chat card. Run headless with Streamlit's AppTest."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("streamlit.testing.v1")

APP = Path(__file__).resolve().parent.parent / "app" / "app.py"


def _run(session: dict | None = None, timeout: int = 120):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(APP), default_timeout=timeout)
    for k, v in (session or {}).items():
        at.session_state[k] = v
    at.run()
    assert not at.exception, [str(e) for e in at.exception]
    return at


def test_the_welcome_page_renders():
    at = _run({"landing": True})
    text = " ".join(m.value for m in at.markdown) + " ".join(b.label for b in at.button)
    assert "Reproduce" in text and "Reuse" in text and "Record" in text


def _opened(agent, tmp_path, section: str) -> dict:
    """The session as ui.building.load_agent leaves it, with the simple-commands chat engine (no model)."""
    from recrate.chat import ChatTools, LocalChat

    tools = ChatTools(agent, tmp_path / "uploads" / "data")
    return {"landing": False, "agent_dir": str(agent.path), "section": section, "tools": tools,
            "engine": LocalChat(agent, tools), "engine_kind": "commands", "engine_choice": "commands",
            "run_name": None, "highlight": None, "chat_mark": 0, "session_ros": [str(agent.path.resolve())]}


def test_the_workspace_renders_with_a_research_object(agent, tmp_path):
    at = _run(_opened(agent, tmp_path, "validate"))
    text = " ".join(m.value for m in at.markdown)
    assert agent.methods[0].name in text  # the Reproduce section lists the methods
    for section in ("reuse", "record"):
        at = _run(_opened(agent, tmp_path, section))
        assert not at.exception
