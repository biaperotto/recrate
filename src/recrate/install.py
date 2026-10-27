"""Register a research object's agent with an MCP client, the way Paper2Agent installs its servers.

    recrate install-mcp agents/my_paper --client desktop   # Claude Desktop
    recrate install-mcp agents/my_paper --client project   # .mcp.json here (Claude Code, VS Code extension)
    recrate install-mcp agents/my_paper --client claude-code   # Claude Code, all projects (needs the claude CLI)
    recrate install-mcp agents/my_paper                    # print the JSON for any other client
    recrate install-mcp agents/my_paper --client desktop --transport http --url http://127.0.0.1:8765/mcp
                                                           # a server you run with `recrate serve --http`

What makes it work without fiddling:

* **Absolute paths, no PATH.** The server is launched as ``<this environment's python> -m recrate.mcp_server
  <absolute agent folder>``. Clients such as Claude Desktop start servers with an almost empty environment from ``/``;
  nothing here depends on the shell, the working directory or ``recrate`` being on the PATH.
* **Nothing lost.** The client's config file is merged, not replaced, and backed up first.
* **Checked.** After installing, the server is started over stdio as the client will start it, and its tools are
  listed and called (server_check.py). The client still needs restarting to pick it up.
* **Or over HTTP.** With ``--transport http`` the entry is ``{"type": "http", "url": ...}``: the client connects to
  a server already running (``recrate serve <agent> --http``, here or on another computer), and the check connects
  to that URL instead of launching anything.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

CLIENTS = ["print", "desktop", "project", "claude-code"]
TRANSPORTS = ["stdio", "http"]
DEFAULT_URL = "http://127.0.0.1:8765/mcp"


def server_name(agent_dir: Path) -> str:
    from .agent import PaperAgent

    words = re.sub(r"[^a-z0-9]+", "-", PaperAgent.load(agent_dir).name.lower()).strip("-").split("-")
    slug = ""
    for w in words:  # whole words only, up to 32 characters
        if len(slug) + len(w) + 1 > 32:
            break
        slug = f"{slug}-{w}" if slug else w
    return f"recrate-{slug or 'paper'}"


def server_entry(agent_dir: Path, transport: str = "stdio", url: str | None = None,
                 token: str | None = None) -> dict[str, Any]:
    """The launch command, with absolute paths only. ``sys.executable`` is this environment's interpreter (the
    virtualenv's, not the system's), so the server gets the same packages as this command. For ``http``, the URL
    of a server already running (``recrate serve --http``) and, when it requires one, the bearer token it was
    started with, sent as the Authorization header."""
    if transport == "http":
        entry: dict[str, Any] = {"type": "http", "url": url or DEFAULT_URL}
        if token:
            entry["headers"] = {"Authorization": f"Bearer {token}"}
        return entry
    return {"command": sys.executable, "args": ["-m", "recrate.mcp_server", str(Path(agent_dir).resolve())]}


def desktop_config_path() -> Path:
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if system == "Windows":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def write_config(path: Path, name: str, entry: dict[str, Any]) -> Path | None:
    """Add or replace one server in an ``mcpServers`` config file, keeping everything else. Returns the backup."""
    path = Path(path)
    config: dict[str, Any] = {}
    backup = None
    if path.exists():
        text = path.read_text(encoding="utf-8").strip()
        config = json.loads(text) if text else {}
        backup = path.with_name(f"{path.name}.bak-{datetime.now().strftime('%Y%m%dT%H%M%S')}")
        shutil.copy2(path, backup)
    config.setdefault("mcpServers", {})[name] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return backup


def install(agent_dir: str | Path, client: str = "print", name: str | None = None, project_dir: str | Path = ".",
            config_path: str | Path | None = None, check: bool = True, transport: str = "stdio",
            url: str | None = None, token: str | None = None) -> dict[str, Any]:
    """Register the agent with ``client`` and check it starts (``stdio``) or answers at ``url`` (``http``, with the
    server's bearer ``token`` when it requires one). ``config_path`` overrides where the config is written."""
    agent_dir = Path(agent_dir).resolve()
    if not (agent_dir / "agent.json").exists():
        raise ValueError(f"{agent_dir} is not a research object folder (no agent.json): run recrate agentify first.")
    if client not in CLIENTS:
        raise ValueError(f"Unknown client {client!r}; use one of {', '.join(CLIENTS)}.")
    if transport not in TRANSPORTS:
        raise ValueError(f"Unknown transport {transport!r}; use one of {', '.join(TRANSPORTS)}.")
    name = name or server_name(agent_dir)
    entry = server_entry(agent_dir, transport, url, token)
    out: dict[str, Any] = {"name": name, "entry": entry, "client": client, "config": None, "backup": None,
                           "transport": transport}
    if client == "desktop":
        path = Path(config_path) if config_path else desktop_config_path()
        out["config"], out["backup"] = str(path), write_config(path, name, entry)
        out["next"] = "Quit and reopen Claude Desktop; the server appears under the tools (hammer) menu."
    elif client == "project":
        path = Path(config_path) if config_path else Path(project_dir).resolve() / ".mcp.json"
        out["config"], out["backup"] = str(path), write_config(path, name, entry)
        out["next"] = ("Reopen Claude Code (or the VS Code extension) in this folder and approve the project's MCP "
                       "server when asked; /mcp shows its status.")
    elif client == "claude-code":
        exe = shutil.which("claude")
        if exe is None:
            raise RuntimeError("The claude command is not installed here. Use --client project (Claude Code and its VS "
                               "Code extension read .mcp.json) or --client desktop.")
        add = ([exe, "mcp", "add", "-s", "user", "--transport", "http",
                *(["--header", f"Authorization: Bearer {token}"] if token else []), name, entry["url"]]
               if transport == "http" else [exe, "mcp", "add", "-s", "user", name, "--", entry["command"], *entry["args"]])
        proc = subprocess.run(add, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"claude mcp add failed: {proc.stderr.strip() or proc.stdout.strip()}")
        out["next"] = "Start a new Claude Code session; /mcp shows its status."
    else:
        out["next"] = "Add this to your MCP client's configuration (the mcpServers section)."
    if transport == "http":
        out["next"] = (f"Keep the server running: recrate serve {agent_dir} --http (its URL must be {entry['url']}). "
                       + out["next"])
    if check:
        from .server_check import check_server

        report = check_server(agent_dir, write=False, timeout=120, url=entry["url"] if transport == "http" else None,
                              token=token)
        out["check"] = report
    return out


def describe(result: dict[str, Any]) -> str:
    lines = []
    if result["client"] == "print":
        lines.append(json.dumps({"mcpServers": {result["name"]: result["entry"]}}, indent=2))
    else:
        where = result["config"] or "Claude Code (user scope)"
        lines.append(f"Registered '{result['name']}' in {where}")
        if result.get("backup"):
            lines.append(f"Previous config backed up to {result['backup']}")
    rep = result.get("check")
    if rep:
        ok = rep["ok"]
        n = next((c["detail"] for c in rep["checks"] if c["check"] == "core tools"), "")
        http = result.get("transport") == "http"
        lines.append((("Checked: the server answers at its URL" if http else "Checked: the server starts and answers")
                      if ok else ("Check FAILED: no server answered at the URL (start it with recrate serve --http)"
                                  if http else "Check FAILED: the server did not start cleanly"))
                     + (f" ({n})." if n else "."))
        if not ok:
            lines += [f"  {'✓' if c['ok'] else '✗'} {c['check']}: {c['detail']}" for c in rep["checks"]]
    lines.append(f"Next: {result['next']}")
    return "\n".join(lines)
