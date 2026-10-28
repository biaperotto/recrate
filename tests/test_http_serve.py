"""The agent served over streamable HTTP (``recrate serve --http``), as Paper2Agent's hosted servers are: an MCP
client connects by URL, lists the tools and calls one; ``install-mcp --transport http`` writes the client's entry and
checks it by connecting; ``verify-server --url`` checks a running server the same way. Serving beyond localhost warns."""

from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from recrate.agent import agentify  # noqa: E402

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "workflowhub-nuclei-segmentation"
pytestmark = pytest.mark.skipif(not (EXAMPLE / "crate").exists(), reason="WorkflowHub example not present")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for(port: int, proc: subprocess.Popen, timeout: float = 120) -> None:
    end = time.time() + timeout
    while time.time() < end:
        if proc.poll() is not None:
            raise RuntimeError(f"the server exited early:\n{proc.stdout.read() if proc.stdout else ''}")
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
            return
        except OSError:
            time.sleep(0.3)
    raise RuntimeError("the server did not open its port in time")


def _http_client(url: str):
    try:
        from mcp.client.streamable_http import streamable_http_client as http_client  # MCP SDK 2.x
    except ImportError:
        from mcp.client.streamable_http import streamablehttp_client as http_client  # MCP SDK 1.x
    return http_client(url)


@pytest.fixture()
def served(tmp_path):
    """The nuclei agent, served over HTTP by ``python -m recrate.mcp_server <agent> --http`` on a free port."""
    a = agentify(tmp_path / "agent", crate=EXAMPLE / "crate", validate="none")
    port = _free_port()
    proc = subprocess.Popen([sys.executable, "-m", "recrate.mcp_server", str(a.path), "--http", "--port", str(port)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=str(a.path))
    try:
        _wait_for(port, proc)
        yield a, f"http://127.0.0.1:{port}/mcp"
    finally:
        proc.terminate()
        try:
            proc.wait(15)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_an_mcp_client_connects_by_url_lists_the_tools_and_calls_one(served):
    a, url = served

    async def go():
        from mcp import ClientSession

        async with _http_client(url) as streams:  # (read, write) in 2.x; 1.x adds a session-id getter
            async with ClientSession(streams[0], streams[1]) as s:
                await s.initialize()
                tools = {t.name for t in (await s.list_tools()).tools}
                r = await asyncio.wait_for(s.call_tool("about_paper", {}), 120)
                return tools, "\n".join(c.text for c in r.content if getattr(c, "type", "") == "text")

    tools, text = asyncio.run(go())
    assert {"about_paper", "reproduce", "run_on_my_data", "make_figure"} <= tools
    assert any(t.startswith("run_") and t not in {"run_on_my_data", "run_followup_check"} for t in tools)  # a method
    assert "nuclei" in text.lower()  # the paper's title


def test_install_mcp_and_verify_server_over_http(served, tmp_path):
    from recrate.install import describe, install
    from recrate.server_check import check_server

    a, url = served
    config = tmp_path / "claude_desktop_config.json"
    config.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
    result = install(a.path, client="desktop", config_path=config, transport="http", url=url)
    written = json.loads(config.read_text())
    assert written["mcpServers"][result["name"]] == {"type": "http", "url": url} and "other" in written["mcpServers"]
    assert result["check"]["ok"], result["check"]  # checked by connecting, not by launching
    assert "answers at its URL" in describe(result) and "recrate serve" in result["next"]

    rep = check_server(a.path, url=url, write=False)
    assert rep["ok"] and rep["url"] == url and "streamable HTTP" in rep["checks"][0]["detail"]

    nobody = f"http://127.0.0.1:{_free_port()}/mcp"  # nothing listens there
    rep = check_server(a.path, url=nobody, write=False, timeout=30)
    assert not rep["ok"] and rep["checks"][0]["check"] == "launch"


class _Server2:
    """The MCP SDK 2.x shape: host and port are options of run()."""

    def __init__(self):
        self.calls = []

    async def run_streamable_http_async(self, *, host="127.0.0.1", port=8000, streamable_http_path="/mcp"):
        pass

    def run(self, transport="stdio", **kw):
        self.calls.append((transport, kw))


class _Server1:
    """The MCP SDK 1.x shape: host and port are settings on the server."""

    def __init__(self):
        self.calls = []
        self.settings = type("S", (), {"host": None, "port": None, "streamable_http_path": None})()

    async def run_streamable_http_async(self):
        pass

    def run(self, transport="stdio", **kw):
        self.calls.append((transport, kw, self.settings.host, self.settings.port, self.settings.streamable_http_path))


def test_serve_binds_to_localhost_by_default_and_warns_beyond_it(tmp_path, monkeypatch, capsys):
    import recrate.mcp_server as m
    from recrate.cli import main

    a = agentify(tmp_path / "agent", crate=EXAMPLE / "crate", validate="none")
    fake = _Server2()
    monkeypatch.setattr(m, "build_server", lambda target: fake)

    assert main(["serve", str(a.path)]) == 0
    assert fake.calls == [("stdio", {})]
    assert capsys.readouterr().out == ""  # stdio is the protocol: nothing printed

    assert main(["serve", str(a.path), "--http", "--port", "9"]) == 0
    assert fake.calls[-1] == ("streamable-http", {"host": "127.0.0.1", "port": 9, "streamable_http_path": "/mcp"})
    out, err = capsys.readouterr()
    assert "http://127.0.0.1:9/mcp" in out and "Warning" not in err

    assert main(["serve", str(a.path), "--http", "--host", "0.0.0.0", "--port", "9"]) == 0
    out, err = capsys.readouterr()
    assert "no authentication" in err and "runs the paper's code" in err and "http://0.0.0.0:9/mcp" in out

    fake1 = _Server1()  # the 1.x fallback: the same settings, set on the server
    monkeypatch.setattr(m, "build_server", lambda target: fake1)
    assert main(["serve", str(a.path), "--http", "--port", "9"]) == 0
    assert fake1.calls == [("streamable-http", {}, "127.0.0.1", 9, "/mcp")]


@pytest.fixture()
def served_with_token(tmp_path):
    """The agent served over HTTP behind a bearer token (``--token``)."""
    a = agentify(tmp_path / "agent", crate=EXAMPLE / "crate", validate="none")
    port = _free_port()
    proc = subprocess.Popen([sys.executable, "-m", "recrate.mcp_server", str(a.path), "--http", "--port", str(port),
                             "--token", "s3cret"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            cwd=str(a.path))
    try:
        _wait_for(port, proc)
        yield a, f"http://127.0.0.1:{port}/mcp"
    finally:
        proc.terminate()
        try:
            proc.wait(15)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_a_token_is_required_when_the_server_asks_for_one(served_with_token, tmp_path):
    """Without the token the server answers 401 and the check fails; with it, the check and the registration work,
    and the client's config carries it as the Authorization header."""
    import urllib.error
    import urllib.request

    from recrate.install import install
    from recrate.server_check import check_server

    a, url = served_with_token
    with pytest.raises(urllib.error.HTTPError) as err:  # no token: 401 before anything MCP happens
        urllib.request.urlopen(urllib.request.Request(url, data=b"{}", headers={"Content-Type": "application/json"}),
                               timeout=10)
    assert err.value.code == 401 and err.value.headers.get("WWW-Authenticate") == "Bearer"
    rep = check_server(a.path, url=url, write=False, timeout=30)
    assert not rep["ok"]
    rep = check_server(a.path, url=url, token="s3cret", write=False)
    assert rep["ok"]
    rep = check_server(a.path, url=url, token="wrong", write=False, timeout=30)
    assert not rep["ok"]
    cfg = tmp_path / "claude_desktop_config.json"
    result = install(a.path, client="desktop", config_path=cfg, transport="http", url=url, token="s3cret")
    assert result["check"]["ok"]
    entry = json.loads(cfg.read_text())["mcpServers"][result["name"]]
    assert entry == {"type": "http", "url": url, "headers": {"Authorization": "Bearer s3cret"}}
