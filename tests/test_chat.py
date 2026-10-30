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

    from recrate.agent import PaperAgent, agentify
    from recrate.chat import ChatTools, ClaudeCodeChat
    from recrate.demo import make_demo

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


def test_two_follow_up_questions_from_what_the_answer_did(tmp_path):
    from pathlib import Path

    from recrate.agent import agentify
    from recrate.chat import follow_up_questions

    ex = Path(__file__).resolve().parent.parent / "examples" / "penguins-dimorphism"
    a = agentify(tmp_path / "p", paper=ex / "paper.pdf", crate=ex / "crate", search_crate=False, online=False)
    reply = [{"kind": "tool", "name": "reproduce", "args": {"method": "size_dimorphism_index"}}]
    qs = follow_up_questions(a, reply, asked=set())
    assert qs == ["Why do the size dimorphism index results differ from the paper?",
                  "Run the follow-up checks for size dimorphism index: do they explain the difference?"]
    assert qs[0] not in follow_up_questions(a, reply, asked={qs[0]})  # never one already asked


def test_follow_ups_come_from_the_question_and_the_answer(tmp_path):
    """'Ask next' follows what was just asked and answered: the method, table, figure or parameter they name, or the
    section of the paper nearest to the question's words; not the generic list."""
    import json
    from pathlib import Path

    from recrate.agent import agentify
    from recrate.chat import follow_up_questions, questions_about
    from recrate.paper import Paper

    ex = Path(__file__).resolve().parent.parent / "examples" / "penguins-dimorphism"
    a = agentify(tmp_path / "p", paper=ex / "paper.pdf", crate=ex / "crate", search_crate=False, online=False)
    # a reading package with one table and one figure, as Europe PMC would give
    (a.path / "paper" / "tables").mkdir(parents=True, exist_ok=True)
    (a.path / "paper" / "tables" / "table-2.csv").write_text("Species,Culmen length,Body mass\nAdelie,-0.07,-0.14\n")
    (a.path / "paper" / "figures").mkdir(exist_ok=True)
    (a.path / "paper" / "figures" / "figure-3.jpg").write_bytes(b"\xff\xd8\xff")
    (a.path / "paper" / "package.json").write_text(json.dumps({
        "tables": [{"label": "Table 2", "caption": "SDI", "file": "tables/table-2.csv"}],
        "figures": [{"label": "Figure 3", "caption": "Isotopes", "file": "figures/figure-3.jpg"}]}))

    # a question about a method, answered in words only (no tool): its status decides the first follow-up
    reply = [{"kind": "text", "text": "The size dimorphism index is the mean of the larger sex over the smaller, minus one."}]
    qs = follow_up_questions(a, reply, asked=set(), question="What is the size dimorphism index?")
    assert qs[0] == "Why do the size dimorphism index results differ from the paper?"
    assert qs[1].startswith("Which parameters does size dimorphism index use")

    # a question about a table: show it, and plot it with its own columns
    qs = follow_up_questions(a, [{"kind": "text", "text": "Table 2 lists the index per species."}], asked=set(),
                             question="What does Table 2 report?")
    assert qs == ["Show Table 2 and explain what it reports.", "Plot Table 2: Culmen length by Species."]
    # once the table was shown, the first is not offered again
    reply = [{"kind": "tool", "name": "get_table", "args": {"table": "Table 2"}}, {"kind": "text", "text": "Table 2: ..."}]
    assert follow_up_questions(a, reply, asked=set(), question="Show Table 2")[0].startswith("Plot Table 2")

    # a figure named in the answer
    qs = questions_about(a, "What do the isotopes show?", "See Figure 3 of the paper.", set())
    assert "Show Figure 3." in qs

    # the section of the paper nearest to the question's words, when the text has sections
    a.paper = Paper(title="t", text="# Title\n\n## Abstract\n\nx\n\n## Stable isotope analysis\n\nCarbon and nitrogen "
                                    "signatures were measured in feathers of each penguin.\n\n## References\n\ncarbon nitrogen",
                    source="test")
    qs = questions_about(a, "What do the carbon and nitrogen signatures show?", "", set())
    assert qs == ["What does the paper say about stable isotope analysis?"]
    # never the question just asked, nor one asked before
    qs = follow_up_questions(a, [{"kind": "text", "text": "..."}], asked={"What does the paper say about stable isotope analysis?"},
                             question="What do the carbon and nitrogen signatures show?")
    assert "What does the paper say about stable isotope analysis?" not in qs


def test_follow_ups_change_from_answer_to_answer(tmp_path):
    """Under an answer that names nothing in particular, the research object's own suggestions fill in; the ones
    offered under an earlier answer and not taken come last, so the two are not the same two every time. What the
    answer ran (a reproduction) still decides first, offered before or not."""
    from pathlib import Path

    from recrate.agent import agentify
    from recrate.chat import follow_up_questions, questions_about

    ex = Path(__file__).resolve().parent.parent / "examples" / "penguins-dimorphism"
    a = agentify(tmp_path / "p", paper=ex / "paper.pdf", crate=ex / "crate", search_crate=False, online=False)
    reply = [{"kind": "text", "text": "Three species were studied near Palmer Station."}]  # no method, table or figure
    first = follow_up_questions(a, reply, asked=set(), question="Where was the study done?")
    second = follow_up_questions(a, reply, asked=set(), question="How many birds were measured?", offered=set(first))
    third = follow_up_questions(a, reply, asked=set(), question="Over which years?", offered=set(first) | set(second))
    assert len(first) == len(second) == len(third) == 2
    assert not set(first) & set(second) and not set(second) & set(third) and not set(first) & set(third)
    # when everything was offered once, the oldest offers come round again rather than nothing
    everything = set(first) | set(second) | set(third) | set(follow_up_questions(a, reply, asked=set(), n=40))
    assert len(follow_up_questions(a, reply, asked=set(), question="And?", offered=everything)) == 2
    # a reproduction's own follow-ups stay first even if they were offered before
    ran = [{"kind": "tool", "name": "reproduce", "args": {"method": "size_dimorphism_index"}}]
    qs = follow_up_questions(a, ran, asked=set(), offered=everything)
    assert qs[0] == "Why do the size dimorphism index results differ from the paper?"
    # a method is recognised by its distinctive words, in any form; a short word of its name alone is not it
    qs = questions_about(a, "How strong is the dimorphism in gentoos?", "Gentoos show the largest dimorphism.", set())
    assert qs[0] == "Why do the size dimorphism index results differ from the paper?"
    assert not [q for q in questions_about(a, "What does the index say?", "", set()) if "dimorphism" in q]


def test_simple_commands_plot_a_table_by_its_columns(tmp_path):
    """The 'Plot Table 2: y by x' suggestion works without a model: the chart is drawn from the table and recorded."""
    import json
    from pathlib import Path

    from recrate.agent import agentify
    from recrate.chat import ChatTools, LocalChat

    ex = Path(__file__).resolve().parent.parent / "examples" / "penguins-dimorphism"
    a = agentify(tmp_path / "p", paper=ex / "paper.pdf", crate=ex / "crate", search_crate=False, online=False)
    (a.path / "paper" / "tables").mkdir(parents=True, exist_ok=True)
    (a.path / "paper" / "tables" / "table-2.csv").write_text(
        "Species,Culmen length,Body mass\nAdelie,-0.07,-0.14\nChinstrap,-0.11,-0.10\nGentoo,-0.08,-0.15\n")
    (a.path / "paper" / "package.json").write_text(json.dumps({
        "tables": [{"label": "Table 2", "caption": "SDI", "file": "tables/table-2.csv"}], "figures": []}))
    chat = LocalChat(a, ChatTools(a, tmp_path / "uploads"))
    events = list(chat.send("Plot Table 2: Culmen length by Species."))
    result = next(e[2] for e in events if e[0] == "result" and e[1] == "make_figure")
    fig = next(x for x in result.attachments if x["type"] == "figure")
    assert Path(fig["path"]).name == "bar-culmen-length-by-species.png" and Path(fig["path"]).exists()
    assert "table-2.csv" in fig["caption"]
    run = Path(fig["path"]).parent.parent.parent  # outputs/figures/<file> -> the run crate
    assert (run / "outputs" / "figures" / "data" / "table-2.csv").exists()  # the table plotted, kept with the figure
