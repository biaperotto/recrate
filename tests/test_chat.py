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
    assert r.error and "No data yet" in r.text
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
