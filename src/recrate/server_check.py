"""Check a paper agent's MCP server the way a client will use it: launched over stdio, from its own folder, or
already running over streamable HTTP (``url``), as ``recrate serve --http`` serves it.

Paper2Agent verifies each generated MCP server before delivery (tool inventory, schemas, a real call per tool).
``check_server`` does the same for a ReCRATE agent:

1. **launch**: start ``python -m recrate.mcp_server <agent>`` as a subprocess and initialise an MCP session;
2. **inventory**: the core tools are there, and every tool has a description and an object input schema;
3. **acceptance calls**: ``about_paper`` names the paper, ``search_paper`` answers, ``list_tables`` and
   ``list_figures`` answer, and (with ``reproduce=True``) each method's ``reproduce`` returns its verdict.

The result is written to ``<agent>/work/server-check.json``.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CORE = ["about_paper", "search_paper", "describe_method", "check_my_data", "reproduce", "run_on_my_data",
        "list_tables", "get_table", "list_figures", "get_figure", "methods_text", "reproducibility_record",
        "followup_checks", "run_followup_check", "make_figure", "review_conclusion"]


def _get(obj, snake: str, camel: str):  # MCP SDK 2.x uses snake_case fields, 1.x camelCase
    return getattr(obj, snake, None) if hasattr(obj, snake) else getattr(obj, camel, None)


def _err(result) -> bool:
    return bool(_get(result, "is_error", "isError"))


def _text(result) -> str:
    return "\n".join(getattr(c, "text", "") for c in result.content if getattr(c, "type", "") == "text")


def _transport(agent_dir: Path, url: str | None, token: str | None = None):
    """The client transport: the server launched over stdio from its own folder, or a connection to ``url`` (with
    ``Authorization: Bearer <token>`` when the server requires one)."""
    if url:
        headers = {"Authorization": f"Bearer {token}"} if token else None
        try:
            from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client  # MCP SDK 2.x

            return streamable_http_client(url, http_client=create_mcp_http_client(headers=headers) if headers else None)
        except ImportError:
            from mcp.client.streamable_http import streamablehttp_client  # MCP SDK 1.x

            return streamablehttp_client(url, headers=headers)
    from mcp import StdioServerParameters
    from mcp.client.stdio import stdio_client

    return stdio_client(StdioServerParameters(command=sys.executable, args=["-m", "recrate.mcp_server", str(agent_dir)],
                                              cwd=str(agent_dir)))


async def _check(agent_dir: Path, reproduce: bool, timeout: float, url: str | None = None,
                 token: str | None = None) -> dict[str, Any]:
    from mcp import ClientSession

    from .agent import PaperAgent

    agent = PaperAgent.load(agent_dir)
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    async with _transport(agent_dir, url, token) as streams:  # stdio: (read, write); HTTP 1.x adds a session-id getter
        async with ClientSession(streams[0], streams[1]) as session:
            info = await asyncio.wait_for(session.initialize(), timeout)
            check("launch", True, f"{_get(info, 'server_info', 'serverInfo').name} over "
                                  + (f"streamable HTTP at {url}" if url else "stdio"))
            tools = {t.name: t for t in (await session.list_tools()).tools}
            missing = [t for t in CORE if t not in tools]
            check("core tools", not missing, f"{len(tools)} tools" + (f"; missing {', '.join(missing)}" if missing else ""))
            bad = [n for n, t in tools.items() if not (t.description or "").strip()
                   or (_get(t, "input_schema", "inputSchema") or {}).get("type") != "object"]
            check("tool schemas", not bad, "every tool has a description and an object input schema" if not bad
                  else f"incomplete: {', '.join(bad)}")

            async def call(name: str, args: dict[str, Any]):
                r = await asyncio.wait_for(session.call_tool(name, args), timeout)
                return r, _text(r)

            r, txt = await call("about_paper", {})
            title = agent.manifest.get("title") or agent.name
            check("about_paper", not _err(r) and (title[:40] in txt or not title), title[:80])
            r, txt = await call("search_paper", {"query": "method results"})
            check("search_paper", not _err(r), f"{txt.count(chr(10)) + 1 if txt else 0} lines")
            for name, n in (("list_tables", len(agent.tables())), ("list_figures", len(agent.figures()))):
                r, _ = await call(name, {})
                check(name, not _err(r), f"{n} in the reading package")
            if agent.figures():
                r = await asyncio.wait_for(session.call_tool("get_figure", {"figure": agent.figures()[0]["label"]}), timeout)
                check("get_figure", not _err(r) and any(getattr(c, "type", "") == "image" for c in r.content),
                      "returns an image")
            if reproduce:
                for m in agent.methods:
                    try:
                        r, txt = await call("reproduce", {"method": m.id, "who": "ReCRATE server check"})
                        verdict = json.loads(txt).get("headline", txt[:120]) if txt.startswith("{") else txt[:120]
                        check(f"reproduce {m.id}", not _err(r), verdict)
                    except Exception as e:  # noqa: BLE001 - a failing method is a finding, not a crash
                        check(f"reproduce {m.id}", False, f"{type(e).__name__}: {e}")
    return {"agent": str(agent_dir), "checked": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            **({"url": url} if url else {}), "ok": all(c["ok"] for c in checks), "checks": checks}


def check_server(agent_dir: str | Path, reproduce: bool = False, timeout: float = 600, write: bool = True,
                 url: str | None = None, token: str | None = None) -> dict:
    """``url``: check a server already running over streamable HTTP there, instead of launching one over stdio;
    ``token``: the bearer token it requires, if any."""
    agent_dir = Path(agent_dir).resolve()
    try:
        report = asyncio.run(_check(agent_dir, reproduce, timeout, url, token))
    except Exception as e:  # noqa: BLE001 - the server failing to start (or answer) is the result
        cause = e
        while getattr(cause, "exceptions", None):  # task groups wrap the real error
            cause = cause.exceptions[0]
        report = {"agent": str(agent_dir), **({"url": url} if url else {}), "ok": False,
                  "checks": [{"check": "launch", "ok": False, "detail": f"{type(cause).__name__}: {cause}"}]}
    if write:
        (agent_dir / "work").mkdir(exist_ok=True)
        (agent_dir / "work" / "server-check.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def describe(report: dict) -> str:
    lines = [f"{'✓' if c['ok'] else '✗'} {c['check']}: {c['detail']}" for c in report["checks"]]
    return "\n".join(lines + ["", "Server check passed." if report["ok"] else "Server check FAILED."])
