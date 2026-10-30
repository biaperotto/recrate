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


class SlowEngine:
    """A chat engine that takes a moment to answer, as a model does."""

    def send(self, text: str):
        import time

        time.sleep(0.8)
        yield ("text", f"You asked: {text}")


def _answered(at) -> None:
    """Wait for the answer being written, then rerun the page: the fragment files it into the history."""
    import time

    for _ in range(100):
        if "live" not in at.session_state or at.session_state["live"]["done"]:
            break
        time.sleep(0.05)
    at.run()
    assert not at.exception, [str(e) for e in at.exception]


def test_an_answer_survives_reruns_of_the_page(agent, tmp_path):
    """The answer is computed in a thread, so a rerun of the page (any click in the workstation) does not stop it:
    the question stays, the input is disabled meanwhile, and once complete the answer is filed into the history with
    two questions that follow it; a later answer gets different ones."""
    session = _opened(agent, tmp_path, "validate")
    session["engine"] = SlowEngine()
    at = _run(session)
    at.chat_input[0].set_value("hello").run()
    assert not at.exception and "live" in at.session_state and at.chat_input[0].disabled
    assert at.session_state["history"][-1] == {"role": "user", "parts": [{"kind": "text", "text": "hello"}]}
    at.run()  # as a click in the workstation would: the page reruns, the answer is still coming
    assert "live" in at.session_state and at.session_state["history"][-1]["role"] == "user"
    _answered(at)
    history = at.session_state["history"]
    assert "live" not in at.session_state and not at.chat_input[0].disabled
    assert history[-1] == {"role": "assistant", "parts": [{"kind": "text", "text": "You asked: hello"}]}
    labels = [b.label for b in at.button if (b.key or "").startswith("suggest-next-")]
    assert len(labels) == 2
    at.chat_input[0].set_value("What is this about?").run()
    _answered(at)
    again = [b.label for b in at.button if (b.key or "").startswith("suggest-next-")]
    assert len(again) == 2 and again != labels  # what was offered under the first answer comes after new ones
