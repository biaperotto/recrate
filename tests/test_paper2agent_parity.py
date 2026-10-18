"""What Paper2Agent's agents can do, ReCRATE's can too: tools that take values instead of a data folder, with typed
parameters, and each paper's code in its own environment. Plus what ReCRATE adds: the tool is checked against a
published value, and every change is recorded with its reason."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from recrate.agent import agentify
from recrate.draft import build_crate
from recrate.env import _sources
from recrate.pipeline import run_on_data
from recrate.runcrate import summarise_run

GC = '''
import argparse, csv
from pathlib import Path
ap = argparse.ArgumentParser()
ap.add_argument("--input_dir"); ap.add_argument("--output_dir"); ap.add_argument("--sequence", default="GATTACA")
a = ap.parse_args()
s = a.sequence.upper()
Path(a.output_dir).mkdir(parents=True, exist_ok=True)
with open(Path(a.output_dir) / "results.csv", "w", newline="") as fh:
    w = csv.writer(fh); w.writerow(["sequence", "gc"]); w.writerow(["query", round((s.count("G") + s.count("C")) / len(s), 4)])
'''


def _agent(tmp: Path):
    (tmp / "gc.py").write_text(GC)
    (tmp / "expected.csv").write_text("sequence,gc\nquery,0.2857\n")
    desc = {"name": "GC content", "identifier": "urn:recrate:test:gc", "methods": [{
        "name": "gc_content", "workflow": {"file": "gc.py", "language": "Python"},
        "inputs": [{"id": "seq", "name": "sequence", "type": "Text", "default": "GATTACA", "role": "free",
                    "description": "A DNA sequence"}],
        "outputs": [{"id": "out", "name": "results", "type": "File", "format": "text/csv", "kind": "scalar",
                     "abs_tolerances": {"gc": {"value": 0.00005, "source": "paper-rounding"}}}],
        "expected": "expected.csv"}]}
    build_crate(desc, tmp / "crate", base_dir=tmp)
    return agentify(tmp / "agent", crate=tmp / "crate")


def test_a_tool_that_takes_values_not_data(tmp_path):
    a = _agent(tmp_path)
    m = a.methods[0]
    assert m.readiness == "ready" and m.ladder["numbers"]["status"] == "passed"  # checked against the published value
    out = run_on_data(a.spec(), None, a.runs_dir, agent="test", overrides={"sequence": ("GGCC", "my primer")})
    assert (out.run_dir / "outputs" / "results.csv").read_text().splitlines()[1] == "query,1.0"
    assert "Set to GGCC by test" in summarise_run(out.run_dir)["adjustments"][0]  # the change, with its reason

    from recrate.mcp_server import build_server

    tools = {t.name: t for t in asyncio.run(build_server(a.path).list_tools())}
    schema = getattr(tools["run_gc_content"], "inputSchema", None) or tools["run_gc_content"].input_schema
    assert "data_path" not in schema["properties"] and schema["properties"]["sequence"]["default"] == "GATTACA"


def test_the_environment_comes_from_the_papers_code(tmp_path):
    code = tmp_path / "code"
    (code / "src").mkdir(parents=True)
    (code / "requirements.txt").write_text("numpy==1.26.4\n")
    (code / "pyproject.toml").write_text("[project]\nname='x'\n")
    found = [w for w, _ in _sources(tmp_path)]
    assert found == ["code/requirements.txt", "code/pyproject.toml (the code, installed as a package)"]
    (tmp_path / "environment.json").write_text(json.dumps({"published": "2020-01-01", "published_source": "x",
                                                           "software": []}))
    assert _sources(tmp_path)[0][0] == "code/requirements.txt"  # the code's own list wins over inferred versions


def test_build_runs_the_recrate_agent_and_reports_progress(tmp_path):
    """recrate build: the /recrate agent run by Claude Code, its steps readable while it runs (fake Claude Code here)."""
    import stat
    import sys

    from recrate.build import build, events, state

    a = _agent(tmp_path)
    fake = tmp_path / "claude"
    lines = [{"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash",
                                                            "input": {"command": "recrate env /x"}}]}},
             {"type": "assistant", "message": {"content": [{"type": "text", "text": "All methods reproduce."}]}},
             {"type": "result", "subtype": "success", "result": "Done: 1 method reproduces.", "session_id": "s1"}]
    record_stages = ("import json, pathlib\nagent = pathlib.Path(sys.argv[2].split(' ', 1)[1].removesuffix(' quick'))\n"
                     "(agent / 'work').mkdir(exist_ok=True)\n(agent / 'work' / 'state.json').write_text(json.dumps("
                     "{'events': [{'stage': s} for s in ['setup', 'read', 'survey', 'plan', 'implement', 'assemble',"
                     " 'check', 'validate', 'verify', 'deliver']]}))\n")
    fake.write_text(f"#!{sys.executable}\nimport sys\nassert sys.argv[2].startswith('/recrate ')\n" + record_stages
                    + "".join(f"print({json.dumps(json.dumps(x))})\n" for x in lines))
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    assert build(a.path, wait=True, binary=str(fake)) == 0
    s = state(a.path)
    assert s["state"] == "done" and s["result"] == "Done: 1 method reproduces." and s["session"] == "s1"
    assert s["stages"][-1] == "deliver"  # done means delivered, not just that Claude Code exited
    assert s["quick"] is True  # quick by default: at most 3 methods
    assert events(a.path) == ["↳ Bash: recrate env /x", "All methods reproduce."]


def test_a_build_that_stops_early_is_resumed_then_called_incomplete(tmp_path):
    """Claude Code exits when its turn ends: a pass that stops early is resumed, and is never called done."""
    import stat
    import sys

    from recrate.build import build, state

    a = _agent(tmp_path)
    fake = tmp_path / "claude"
    calls = tmp_path / "calls.txt"
    # each pass records one more stage, and stops before delivery
    fake.write_text(f"""#!{sys.executable}
import json, pathlib, sys
open({str(calls)!r}, 'a').write(' '.join(sys.argv) + '\\n')
n = len(open({str(calls)!r}).read().splitlines())
agent = pathlib.Path({str(a.path)!r})
(agent / 'work').mkdir(exist_ok=True)
stages = ['setup', 'read', 'survey', 'plan'][:n]
(agent / 'work' / 'state.json').write_text(json.dumps({{'events': [{{'stage': s}} for s in stages]}}))
print(json.dumps({{'type': 'result', 'subtype': 'success', 'result': 'waiting for subagents', 'session_id': 's1'}}))
""")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    assert build(a.path, wait=True, binary=str(fake)) == 1
    args = calls.read_text().splitlines()
    assert len(args) == 4 and "--resume s1" in args[1] and "unattended" in args[0]  # resumed, told to wait
    s = state(a.path)
    assert s["state"] == "incomplete" and s["stages"] == ["setup", "read", "survey", "plan"]
