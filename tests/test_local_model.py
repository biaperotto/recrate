"""The chat with an open model on an OpenAI-compatible server (Ollama, LM Studio...): no API key."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from recrate.chat import ChatTools, OpenAICompatChat, local_models


class FakeOllama(BaseHTTPRequestHandler):
    calls: list[dict] = []

    def log_message(self, *a):
        pass

    def _send(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send({"data": [{"id": "qwen2.5:7b"}, {"id": "llama3.1:8b"}]})

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeOllama.calls.append(req)
        if not any(m["role"] == "tool" for m in req["messages"]):  # first: ask for a tool
            self._send({"choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "reproduce",
                                                              "arguments": json.dumps({"method": "count_nuclei"})}}]}}]})
        else:
            self._send({"choices": [{"message": {"role": "assistant", "content": "It reproduces: 22 vs 22."}}]})


def test_open_model_calls_the_tools(agent, demo, tmp_path):
    srv = HTTPServer(("127.0.0.1", 0), FakeOllama)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}/v1"
    try:
        assert local_models(base) == ["llama3.1:8b", "qwen2.5:7b"]
        chat = OpenAICompatChat(agent, ChatTools(agent, tmp_path), "qwen2.5:7b", base)
        events = list(chat.send("reproduce the paper"))
        kinds = [e[0] for e in events]
        assert kinds == ["tool", "result", "text"] and events[0][1] == "reproduce"
        assert json.loads(events[1][2].text)["verdict"] == "reproduced"
        assert FakeOllama.calls[0]["tools"][0]["type"] == "function"
        assert FakeOllama.calls[-1]["messages"][-1]["role"] == "tool"
    finally:
        srv.shutdown()


def test_no_server_means_no_models():
    assert local_models("http://127.0.0.1:9/v1", timeout=0.5) == []
