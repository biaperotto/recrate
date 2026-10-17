"""Make a paper's methods runnable from the app or one command: the /recrate agent, run by Claude Code.

    recrate build agents/my_paper           # after: recrate agentify --paper paper.pdf --out agents/my_paper
    recrate build agents/my_paper --status

This is Paper2Agent's pipeline (the paper's own environment, tools from its code and tutorials, each tool tested,
an MCP server) with ReCRATE's checks on top (every parameter quoted from the paper, every tool compared with the
published results, an independent verifier, a run crate per run). It runs headless on the person's Claude
subscription through Claude Code, in the background: progress goes to ``<agent>/work/build.jsonl`` and the state to
``<agent>/work/build.json``, so the app can show it and a page reload doesn't stop it.

It runs the paper's code on this computer, as Paper2Agent does: start it only for code you are willing to run.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# What the agent may do: read and write files, run commands (to install and run the paper's code), use subagents,
# fetch the paper's code and data. Nothing else asks for permission, because nobody is there to answer.
TOOLS = ["Bash", "Read", "Write", "Edit", "MultiEdit", "Glob", "Grep", "Task", "Agent", "TodoWrite", "WebFetch",
         "WebSearch", "Skill", "NotebookEdit"]


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _paths(agent_dir: Path) -> tuple[Path, Path]:
    work = Path(agent_dir) / "work"
    work.mkdir(parents=True, exist_ok=True)
    return work / "build.json", work / "build.jsonl"


def _skill_home() -> Path:
    """Where Claude Code finds the /recrate skill: this repository, or the user's skills after installing it."""
    repo = Path(__file__).resolve().parents[2]
    if (repo / ".claude" / "skills" / "recrate").exists() or (repo / ".claude" / "commands" / "agentify.md").exists():
        return repo
    from .cli import main as cli

    cli(["install-skill", "--user"])
    return Path.home()


def state(agent_dir: str | Path) -> dict[str, Any]:
    f, _ = _paths(Path(agent_dir))
    s = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {"state": "not started"}
    if s.get("state") == "running" and s.get("pid"):
        try:
            os.kill(int(s["pid"]), 0)
        except OSError:  # the process is gone without finishing: say so
            s["state"] = "stopped"
    return s


def events(agent_dir: str | Path, last: int = 20) -> list[str]:
    """The latest steps, readable: what the agent said, and each tool it used."""
    _, log = _paths(Path(agent_dir))
    if not log.exists():
        return []
    out = []
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("type") != "assistant":
            continue
        for b in e["message"].get("content", []):
            if b.get("type") == "text" and b.get("text", "").strip():
                out.append(" ".join(b["text"].split())[:300])
            elif b.get("type") == "tool_use":
                i = b.get("input") or {}
                what = i.get("description") or i.get("command") or i.get("file_path") or i.get("prompt") or ""
                out.append(f"↳ {b['name']}: {' '.join(str(what).split())[:160]}")
    return out[-last:]


def describe_build(agent_dir: str | Path) -> str:
    s = state(agent_dir)
    lines = [f"Build: {s.get('state')}" + (f" (started {s['started']})" if s.get("started") else "")]
    lines += ["  " + e for e in events(agent_dir)]
    if s.get("result"):
        lines.append(s["result"][:1500])
    return "\n".join(lines)


def build(agent_dir: str | Path, wait: bool = True, binary: str | None = None) -> int:
    """Run the /recrate agent on ``agent_dir``. ``wait=False`` starts it in the background and returns at once."""
    from .chat import claude_code_binary

    agent_dir = Path(agent_dir).resolve()
    if not (agent_dir / "agent.json").exists():
        raise ValueError(f"{agent_dir} is not a research object: run recrate agentify first.")
    exe = binary or claude_code_binary()
    if not exe:
        raise RuntimeError("Claude Code is not installed (or its VS Code extension): it runs the /recrate agent.")
    status, log = _paths(agent_dir)
    if state(agent_dir).get("state") == "running" and not os.environ.get("RECRATE_BUILD_CHILD"):
        raise RuntimeError("A build is already running for this research object.")
    if not wait:  # detach: the same command, in its own session, so the app can carry on
        proc = subprocess.Popen([sys.executable, "-m", "recrate.build", str(agent_dir)], start_new_session=True,
                                env={**os.environ, "RECRATE_BUILD_CHILD": "1"},
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        status.write_text(json.dumps({"state": "running", "pid": proc.pid, "started": _now()}), encoding="utf-8")
        return 0
    env = {**os.environ, "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ.get('PATH', '')}"}
    cmd = [exe, "-p", f"/recrate {agent_dir}", "--output-format", "stream-json", "--verbose",
           "--permission-mode", "acceptEdits", "--allowedTools", *TOOLS, "--add-dir", str(agent_dir),
           "--max-turns", "500"]
    started = _now()
    proc = subprocess.Popen(cmd, cwd=str(_skill_home()), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    status.write_text(json.dumps({"state": "running", "pid": os.getpid(), "started": started}), encoding="utf-8")
    result, session = "", None
    with log.open("w", encoding="utf-8") as fh:
        for line in proc.stdout:
            fh.write(line)
            fh.flush()
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("type") == "result":
                result, session = e.get("result") or e.get("subtype") or "", e.get("session_id")
            if wait and e.get("type") == "assistant":
                for b in e["message"].get("content", []):
                    if b.get("type") == "tool_use":
                        print(f"↳ {b['name']}", flush=True)
    proc.wait()
    ok = proc.returncode == 0  # the agent validated its methods itself (recrate validate), updating agent.json
    status.write_text(json.dumps({"state": "done" if ok else "failed", "started": started, "ended": _now(),
                                  "session": session, "result": result or proc.stderr.read()[-2000:]}, indent=2),
                      encoding="utf-8")
    print(describe_build(agent_dir))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(build(sys.argv[1], wait=True))
