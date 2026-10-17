from __future__ import annotations

import json
import zipfile
from pathlib import Path

from recrate.chat import TOOLS, ChatTools, LocalChat, _echo_content, copy_into, runner_choices


def _tools(agent, demo, tmp_path, with_files=True):
    uploads = tmp_path / "uploads"
    if with_files:
        copy_into(sorted(Path(demo["my_images"]).glob("*.tif")), uploads)
    return ChatTools(agent, uploads, who="Anna")


def test_every_tool_has_a_handler(agent, tmp_path):
    t = ChatTools(agent, tmp_path)
    assert all(hasattr(t, f"t_{d['name']}") for d in TOOLS)


def test_check_run_and_output_crate_on_uploads(agent, demo, tmp_path):
    t = _tools(agent, demo, tmp_path)
    check = json.loads(t.call("check_my_data", {"method": "count_nuclei"}).text)
    assert check["status"] == "warn" and check["adjustments"][0]["new"] == 12.5

    run = t.call("run_on_my_data", {"method": "count_nuclei"})
    assert not run.error and json.loads(run.text)["parameters_used"]["min_size_px"] == 12.5
    assert {a["type"] for a in run.attachments} >= {"results", "images", "run"}

    crate = t.call("create_output_crate", {})
    assert not crate.error
    z = Path(json.loads(crate.text)["crate_zip"])
    assert "ro-crate-metadata.json" in zipfile.ZipFile(z).namelist()


def test_reproduce_reports_the_verdict(agent, demo, tmp_path):
    r = json.loads(_tools(agent, demo, tmp_path).call("reproduce", {"method": "count_nuclei"}).text)
    assert r["verdict"] == "reproduced"


def test_errors_come_back_as_results(agent, demo, tmp_path):
    t = _tools(agent, demo, tmp_path, with_files=False)
    r = t.call("check_my_data", {"method": "count_nuclei"})
    assert r.error and "No data yet" in r.text and "ValueError" not in r.text
    assert t.call("create_output_crate", {}).error  # no run yet


def test_local_chat_routes_to_tools(agent, demo, tmp_path):
    chat = LocalChat(agent, _tools(agent, demo, tmp_path))
    called = lambda text: [e[1] for e in chat.send(text) if e[0] == "tool"]  # noqa: E731
    assert called("reproduce size_distribution") == ["reproduce"]
    assert called("run count_nuclei on my images") == ["run_on_my_data"]
    assert called("export the crate") == ["create_output_crate"]
    passages = [e for e in chat.send("what pixel size were the images?") if e[0] == "result"]
    assert any("0.65" in h["text"] for h in passages[0][2].attachments[0]["items"])


def test_local_chat_compare_and_pixel_size(agent, demo, tmp_path):
    t = _tools(agent, demo, tmp_path)
    chat = LocalChat(agent, t)
    list(chat.send("run count_nuclei on my images"))
    called = [e for e in chat.send("compare with the paper's parameters") if e[0] == "result"]
    assert called[0][1] == "compare_paper_parameters" and t.whatifs[t.last_run.name]["overlays"]
    px = [e for e in chat.send("my pixel size is 1.3 µm") if e[0] == "tool"]
    assert px[0][1] == "set_pixel_size" and px[0][2]["pixel_size"] == 1.3


def test_runner_choices_hide_fiji_without_fiji(monkeypatch):
    monkeypatch.delenv("FIJI_PATH", raising=False)
    assert "fiji" not in runner_choices()


def test_echo_drops_declined_calls_before_fallback():
    class B:
        def __init__(self, type):
            self.type = type
    blocks = [B("thinking"), B("text"), B("tool_use"), B("fallback"), B("thinking"), B("tool_use")]
    assert [b.type for b in _echo_content(blocks)] == ["text", "thinking", "tool_use"]


def test_local_chat_ignores_the_apps_notes_and_gives_an_overview(agent, demo, tmp_path):
    chat = LocalChat(agent, _tools(agent, demo, tmp_path))
    note = "[Done in the workstation since my last message: Running with the published values unchanged]\n\n"
    events = list(chat.send(note + "what is this research about?"))
    assert not [e for e in events if e[0] == "tool"]  # no command triggered by the note
    assert "count_nuclei" in events[0][1] and "Counting and sizing nuclei" in events[0][1]


def test_claude_code_engine_streams_tools_and_keeps_the_session(tmp_path):
    """The app's chat on a Claude subscription: Claude Code's stream is turned into text, tool chips and results."""
    import json
    import stat
    import sys

    from recrate.agent import PaperAgent
    from recrate.chat import ChatTools, ClaudeCodeChat
    from recrate.demo import make_demo
    from recrate.agent import agentify

    demo = make_demo(tmp_path / "demo")
    agent = agentify(tmp_path / "agent", paper=demo["paper"], crate=demo["crate"], validate="none")
    events = [
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "mcp__recrate__reproduce",
                                                       "input": {"method": "count_nuclei"}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": [
            {"type": "text", "text": json.dumps({"message": "Reproduced: 2 of 2", "same_numbers": "2/2",
                                                 "run_crate": str(tmp_path), "artifacts": []})}]}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "It reproduces."}]}},
        {"type": "result", "subtype": "success", "session_id": "abc"},
    ]
    fake = tmp_path / "claude"
    fake.write_text(f"#!{sys.executable}\nimport sys\nopen({str(tmp_path / 'args.txt')!r}, 'a').write(' '.join(sys.argv) + '\\n')\n"
                    + "".join(f"print({json.dumps(json.dumps(e))})\n" for e in events))
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    chat = ClaudeCodeChat(PaperAgent.load(agent.path), ChatTools(agent, tmp_path / "up"), binary=str(fake))
    out = list(chat.send("Does it reproduce?"))
    assert [e[0] for e in out] == ["tool", "result", "text"] and out[0][1] == "reproduce"
    assert out[1][2].attachments[0] == {"type": "comparison", "ok": True, "headline": "Reproduced: 2 of 2"}
    assert chat.session == "abc"
    list(chat.send("And then?"))
    args = (tmp_path / "args.txt").read_text().splitlines()
    assert "--strict-mcp-config" in args[0] and "--resume abc" in args[1]  # only ReCRATE's tools; one conversation


def test_suggested_questions_follow_the_content(tmp_path):
    """Suggestions come from what the research object holds: a differing method gets 'why', and its follow-ups."""
    from pathlib import Path

    from recrate.agent import agentify
    from recrate.chat import suggested_questions

    ex = Path(__file__).resolve().parent.parent / "examples" / "penguins-dimorphism"
    a = agentify(tmp_path / "p", paper=ex / "paper.pdf", crate=ex / "crate", search_crate=False, online=False)
    qs = suggested_questions(a)
    assert qs[0].startswith("What is this research about")
    assert "Why do the size dimorphism index results differ from the paper?" in qs
    assert any(q.startswith("Run the follow-up checks") for q in qs) and len(qs) <= 6
