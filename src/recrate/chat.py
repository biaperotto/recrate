"""Chat with a paper agent: ask the paper, check and run its methods on your files, export run crates.

The engines share the same tools (``ChatTools``, in recrate.chat_tools):

* ``ClaudeChat``: Claude decides which tools to call (needs ``pip install "recrate[draft]"`` and
  ``ANTHROPIC_API_KEY``; model ``RECRATE_CHAT_MODEL``, default ``claude-opus-5-5``).
* ``LocalChat``: no API key; a keyword router over the same tools, so the demo works offline.

As in the MCP server, the model never computes results: the tools run the paper's methods and every
run is written as a Workflow Run RO-Crate. Each tool returns a short JSON text for the model and a list
of *attachments* (tables, images, run crates) for the user interface to render.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Iterator

from .agent import PaperAgent

MODEL = os.environ.get("RECRATE_CHAT_MODEL", "claude-opus-5-5")
MAX_PAPER_CHARS = 400_000  # papers longer than this are reached through search_paper only


from .chat_tools import (  # noqa: F401 - re-exported: the chat's tools live in chat_tools
    TOOLS,
    ChatTools,
    ToolResult,
    runner_choices,
    tools_for,
)


def system_prompt(agent: PaperAgent, max_paper_chars: int = MAX_PAPER_CHARS) -> str:
    title = agent.manifest.get("title", agent.name)
    doi = agent.manifest.get("doi")
    methods = "\n".join(f"- {m.name} ({m.status}): {m.description}" for m in agent.methods) or \
        "- none yet: answer from the paper only"
    paper = agent.paper.text if agent.paper else ""
    paper_block = (f"\n\n<paper>\n{paper}\n</paper>" if paper and len(paper) <= max_paper_chars
                   else "\n\nThe paper is long: use search_paper to read the relevant parts.")
    from .profiles import fields_of

    fields = fields_of(agent)  # how to talk about this research's data: images, recordings, tables...
    noun = " and ".join(dict.fromkeys(f.data_noun for f in fields)) or "data"
    example = next((f" (for example a {f.scale} different from the research's, and why a parameter that depends on "
                    "it is rescaled)" for f in fields if f.scale), "")
    return (
        f"You are the paper agent for '{title}'{f' ({doi})' if doi else ''}, inside the ReCRATE app. "
        f"The user talks to the paper: they ask about it, reproduce its results, bring their own {noun} "
        "and run its methods on them, and export run records as RO-Crates.\n\n"
        f"Methods (each is a tool-backed workflow, reproduced against the paper's reported results):\n{methods}\n\n"
        "How to work:\n"
        "- Answer questions from the paper and quote the relevant sentence. Say so when the paper does not say.\n"
        "- Never invent numbers. Every result comes from a tool; report what the tools return.\n"
        "- Before running a method on the user's files, call check_my_data and explain each warning and "
        f"parameter adjustment in plain terms{example}. If a check fails, explain what to fix and do not run.\n"
        "- Uploaded files are used by default when data_path is omitted.\n"
        "- After a run, summarise the results briefly, ask the user to check them, and offer to "
        "create the output RO-Crate (create_output_crate) and a methods paragraph.\n"
        "- After a run with adjustments, offer compare_paper_parameters to show what the paper's values "
        "unchanged would give.\n"
        "- A reproduction that differs is a finding about the published result, not a failure: name each "
        "value that differs (published vs recomputed, and the tolerance) and say how many reproduce.\n"
        + "".join(f"- If the research doesn't state the {f.scale} and a parameter depends on it, ask the user how "
                  f"theirs compares with the research's and pass scale_ratio; never guess it.\n"
                  for f in fields if f.scale)
        + "".join(f"- {h}\n" for f in fields for h in f.hints) +
        "- If the user wants a different parameter value, pass it in overrides with their reason.\n"
        "- Only call approve_run when the user says the result looks right or wrong.\n"
        "- The app also has a workspace (fit, parameters, results, review, figures, crate, export): "
        "point the user to it when useful.\n"
        "- The app shows tool outputs (tables, figures, download buttons) to the user, so don't repeat whole "
        "tables; point out what matters. Keep answers short."
        + paper_block
    )


def _echo_content(blocks: list[Any]) -> list[Any]:
    """Assistant content to send back. After a mid-output fallback, drop the declined model's
    thinking and tool calls before the last fallback marker (they were never run)."""
    idx = [i for i, b in enumerate(blocks) if getattr(b, "type", None) == "fallback"]
    if not idx:
        return list(blocks)
    cut = idx[-1]
    drop = {"thinking", "redacted_thinking", "tool_use"}
    return [b for i, b in enumerate(blocks) if i > cut or (i < cut and b.type not in drop)]


class ClaudeChat:
    """Claude with the paper agent's tools (manual tool-use loop, so the UI can show each step)."""

    def __init__(self, agent: PaperAgent, tools: ChatTools, model: str = MODEL, api_key: str | None = None):
        import anthropic  # optional dependency: recrate[draft]

        self.client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self.agent, self.tools, self.model = agent, tools, model
        self.system = [{"type": "text", "text": system_prompt(agent), "cache_control": {"type": "ephemeral"}}]
        self.messages: list[dict[str, Any]] = []

    def send(self, text: str, max_steps: int = 12) -> Iterator[tuple]:
        self.messages.append({"role": "user", "content": text})
        for _ in range(max_steps):
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=self.system,
                tools=tools_for(self.agent),
                messages=self.messages,
                output_config={"effort": "medium"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            if response.stop_reason == "refusal":
                self.messages.pop()  # leave the history as it was before the declined message
                yield ("text", "I can't help with that request.")
                return
            content = _echo_content(response.content)
            self.messages.append({"role": "assistant", "content": content})
            for b in content:
                if b.type == "text" and b.text.strip():
                    yield ("text", b.text)
            calls = [b for b in content if b.type == "tool_use"]
            if response.stop_reason == "pause_turn":
                continue
            if not calls:
                if response.stop_reason == "max_tokens":
                    yield ("text", "_(The answer was cut off; ask me to continue.)_")
                return
            results = []
            for c in calls:
                yield ("tool", c.name, c.input)
                r = self.tools.call(c.name, dict(c.input or {}))
                yield ("result", c.name, r)
                results.append({"type": "tool_result", "tool_use_id": c.id, "content": r.text,
                                **({"is_error": True} if r.error else {})})
            self.messages.append({"role": "user", "content": results})
        yield ("text", "_(Stopped after many steps; tell me how to continue.)_")


def suggested_questions(agent: PaperAgent, limit: int = 6) -> list[str]:
    """Questions worth asking this research object, chosen from what it contains: its methods and their verdicts, the
    follow-up checks for a difference, what data a method takes, its tables, what the paper leaves unsaid. Written
    from the content, never by a model, so every suggestion has something real behind it."""
    from .followup import propose
    from .model import assumptions

    out = ["What is this research about, and what did the authors find?"]
    if not agent.methods:
        out += ["Which code and data does the paper link to?",
                "Which software did it use, and in which versions?",
                "What would it take to make its methods runnable?"]
    for m in agent.methods[:3]:
        try:
            spec = agent.spec(m.id)
        except Exception:  # noqa: BLE001 - a method the crate can't describe gets no suggestions
            continue
        name = m.name.replace("_", " ")
        if m.status == "differs":
            out.append(f"Why do the {name} results differ from the paper?")
            if any(f.runnable for f in propose(spec)):
                out.append(f"Run the follow-up checks for {name}: do they explain the difference?")
        elif m.status == "validated":
            out.append(f"How was {name} checked against the paper's published results?")
        elif m.status == "failed":
            out.append(f"Why did {name} fail to run?")
        else:
            out.append(f"Reproduce {name} on the paper's own data.")
        if not spec.needs_data:
            out.append(f"Run {name} with my own values: which can I change?")
        elif spec.profile == "bioimaging":
            out.append(f"Will {name} work on my images? What should I check first?")
        elif spec.profile == "timeseries":
            out.append(f"Will {name} work on my recordings if the sampling rate differs?")
        else:
            out.append(f"Can I use {name} on my own data? What must it look like?")
        if any(p.prop("quote") for p in spec.value_inputs):
            out.append(f"Which parameters does {name} use, and where does the paper state them?")
    tables = agent.tables()
    if tables:  # the table a method reproduces, when its description names one
        said = " ".join(m.description for m in agent.methods).lower()
        t = next((t for t in tables if t["label"] and t["label"].lower() in said), tables[0])
        out.append(f"Show {t['label']} and explain what it reports.")
    if agent.methods:
        try:
            if assumptions(agent.spec(), agent.environment):
                out.append("What does the paper not say, that had to be assumed?")
        except Exception:  # noqa: BLE001
            pass
        if any(m.run_crate for m in agent.methods):
            out.append("Make the report page for this research.")
    seen, picked = set(), []
    for q in out:  # one per kind of question first, so a long list of methods doesn't crowd out the rest
        if q not in seen:
            seen.add(q)
            picked.append(q)
    return picked[:limit]


SECTION_SKIP = {"abstract", "references", "acknowledgements", "acknowledgments", "author contributions", "funding",
                "competing interests", "conflict of interest", "supporting information", "supplementary material"}


def _sections(text: str) -> list[tuple[str, str]]:
    """``(heading, body)`` for each ``## heading`` of the paper's text (empty for a PDF read as plain text)."""
    out: list[tuple[str, str]] = []
    head, buf = None, []  # type: str | None, list[str]
    for line in text.splitlines():
        m = re.match(r"^#{1,3}\s+(.+?)\s*$", line)
        if m:
            if head is not None:
                out.append((head, "\n".join(buf)))
            head, buf = m.group(1), []
        elif head is not None:
            buf.append(line)
    if head is not None:
        out.append((head, "\n".join(buf)))
    return out


def _terms(text: str) -> set[str]:
    from .paper import STOPWORDS

    return {w for w in re.findall(r"[a-z][a-z-]{3,}", text.lower()) if w not in STOPWORDS}


def _table_axes(path: Path) -> tuple[str, str] | None:
    """The first column and the first numeric column of a table, as a plot's x and y; None if it has none."""
    import csv

    try:
        with path.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
    except OSError:
        return None
    if len(rows) < 2 or len(rows[0]) < 2:
        return None
    header, first = rows[0], rows[1]
    for i, cell in enumerate(first[1:], 1):
        try:
            float(str(cell).replace(",", "").replace("−", "-"))
        except ValueError:
            continue
        if header[i].strip():
            return header[0].strip() or "row", header[i].strip()
    return None


def questions_about(agent: PaperAgent, question: str, answer: str, tools_used: set[str] | None = None) -> list[str]:
    """Questions that follow from what the last question and answer were about: the methods, parameters, follow-up
    checks, tables and figures they name, and the section of the paper nearest to the question's words. Each
    suggestion points at something the research object holds; nothing is invented."""
    from .followup import propose

    tools_used = tools_used or set()
    text = f"{question}\n{answer}".lower()
    out: list[str] = []
    for m in agent.methods:
        name = m.name.replace("_", " ")
        if not (name.lower() in text or m.name.lower() in text or m.id.lower() in text):
            continue
        try:
            spec = agent.spec(m.id)
        except Exception:  # noqa: BLE001 - a method the crate can't describe gets no suggestions
            continue
        if m.status == "differs":
            out.append(f"Why do the {name} results differ from the paper?")
        elif m.status == "validated":
            out.append(f"How was {name} checked against the paper's published results?")
        elif m.status == "failed":
            out.append(f"Why did {name} fail to run?")
        elif m.readiness != "blocked":
            out.append(f"Reproduce {name} on the paper's own data.")
        for p in spec.value_inputs:  # a parameter the question or answer names
            if (p.name.replace("_", " ").lower() in text or p.name.lower() in text) and p.prop("quote"):
                out.append(f"Where does the paper state {p.name.replace('_', ' ')}, and what happens if I change it?")
        try:
            for f in propose(spec):  # a follow-up check it names
                if f.runnable and f.title.lower() in text:
                    out.append(f"Run the check '{f.title}' for {name}.")
        except Exception:  # noqa: BLE001
            pass
        if any(p.prop("quote") for p in spec.value_inputs):
            out.append(f"Which parameters does {name} use, and where does the paper state them?")
        out.append(f"Will {name} work on my data? What should I check first?" if spec.needs_data
                   else f"Run {name} with my own values: which can I change?")
    for t in agent.tables():
        label = t.get("label") or ""
        if label and label.lower() in text:
            if "get_table" not in tools_used:
                out.append(f"Show {label} and explain what it reports.")
            axes = _table_axes(agent.path / "paper" / t["file"])
            if axes:
                out.append(f"Plot {label}: {axes[1]} by {axes[0]}.")
            if any(label.lower() in (m.description or "").lower() for m in agent.methods):
                out.append(f"Which method reproduces {label}, and does it match?")
    for f in agent.figures():
        label = f.get("label") or ""
        if label and label.lower() in text:
            if not {"get_figure", "show_figure"} & tools_used:
                out.append(f"Show {label}.")
            if any(m.run_crate for m in agent.methods):
                out.append(f"Was {label} redrawn by a reproduction? Show both.")
    if agent.paper and question:  # the section of the paper the question's words point at, not yet asked about
        terms = _terms(question)
        best, score = None, 0
        for head, body in _sections(agent.paper.text):
            if head.lower() in SECTION_SKIP or not _terms(head) or _terms(head) <= terms:
                continue
            n = sum(1 for w in terms if w in body.lower())
            if n > score:
                best, score = head, n
        if best and score >= min(2, len(terms)) and score > 0:
            out.append(f"What does the paper say about {best[0].lower() + best[1:]}?")
    return out


def follow_up_questions(agent: PaperAgent, reply_parts: list[dict[str, Any]], asked: set[str], n: int = 2,
                        question: str = "") -> list[str]:
    """Two questions that follow from the last question and answer: what the answer ran decides first (a
    difference leads to its follow-up checks, a check of your data to running on it...), then what the question
    and answer were about (a method, a table, a figure, a parameter, a section of the paper), then the research
    object's own suggestions. Never one already asked."""
    from .followup import propose

    tools = [p for p in reply_parts if p.get("kind") == "tool"]
    names = [t["name"] for t in tools]
    method_id = next((t.get("args", {}).get("method") for t in reversed(tools) if t.get("args", {}).get("method")),
                     None) or (agent.methods[0].name if agent.methods else None)
    m = next((x for x in agent.methods if method_id in (x.id, x.name)), None)
    name = (m.name if m else method_id or "this method").replace("_", " ")
    out: list[str] = []
    if "reproduce" in names and m is not None:
        if m.status == "differs":
            out += [f"Why do the {name} results differ from the paper?",
                    f"Run the follow-up checks for {name}: do they explain the difference?"]
        else:
            out += [f"Will {name} work on my data? What should I check first?", "Make the report page for this research."]
    if "followup_checks" in names and m is not None:
        f = next((x for x in propose(agent.spec(m.id)) if x.runnable), None)
        if f:
            out.append(f"Run the check '{f.title}' for {name}.")
        out.append(f"Plot the published and reproduced values for {name}.")
    if "run_followup_check" in names:
        out += [f"Plot the published and reproduced values for {name}.", "Make the report page for this research."]
    if "check_my_data" in names:
        out += [f"Run {name} on my data, with the adjustments.", f"Which parameters of {name} need my decision?"]
    if "run_on_my_data" in names:
        out += ["How do my results compare with what the paper reported?", "Write the methods paragraph for this run."]
    if {"make_figure", "report_page"} & set(names):
        out += ["Export the RO-Crate of this research.", "What does the paper not say, that had to be assumed?"]
    answer = "\n".join(p.get("text", "") for p in reply_parts if p.get("kind") == "text")
    answer += "\n" + "\n".join(i.get("text", "") for p in reply_parts if p.get("kind") == "attachment"
                               and (p.get("data") or {}).get("type") == "passages"
                               for i in (p.get("data") or {}).get("items") or [])
    out += questions_about(agent, question, answer, set(names))  # then what the question and answer were about
    out += suggested_questions(agent, limit=12)  # then the research object's own, in order
    seen, picked = set(asked) | {question.strip()}, []
    for q in out:
        if q not in seen:
            seen.add(q)
            picked.append(q)
        if len(picked) == n:
            break
    return picked


def claude_code_binary() -> str | None:
    """The Claude Code program: ``RECRATE_CLAUDE``, ``claude`` on the PATH, or the one bundled with the Claude Code
    extension for VS Code or Cursor. It signs in with the person's Claude subscription, so no API key is needed."""
    import shutil

    env = os.environ.get("RECRATE_CLAUDE")
    if env and Path(env).exists():
        return env
    found = shutil.which("claude")
    if found:
        return found
    for base in ("~/.vscode/extensions", "~/.vscode-insiders/extensions", "~/.cursor/extensions"):
        hits = sorted(Path(base).expanduser().glob("anthropic.claude-code-*/resources/native-binary/claude"))
        if hits:
            return str(hits[-1])
    return None


class ClaudeCodeChat:
    """Claude on the person's own Claude subscription, through Claude Code, with the research object's MCP server as
    its only tools. The app's chat becomes the same agent as in Claude Desktop, the way Paper2Agent's agents use
    their MCP servers: Claude reasons and talks; every number comes from a ReCRATE tool. Claude Code's own tools
    (files, shell, web) are switched off, and the conversation continues across messages (``--resume``)."""

    def __init__(self, agent: PaperAgent, tools: ChatTools, binary: str | None = None, model: str | None = None):
        self.agent, self.tools, self.model = agent, tools, model
        self.binary = binary or claude_code_binary()
        if not self.binary:
            raise RuntimeError("Claude Code is not installed (or its VS Code extension).")
        self.session: str | None = None

    def _command(self, text: str) -> list[str]:
        from .install import server_entry

        try:
            data = str(self.tools.current_data())
        except Exception:  # noqa: BLE001 - no data yet
            data = "none yet"
        context = (f"You are the chat of the ReCRATE app for this research object. The user's data folder is {data} "
                   f"(pass it as data_path). Record every run with who='{self.tools.who}'. Use only the recrate tools. "
                   "Never state a number that did not come from a tool result. Never call review_conclusion unless the "
                   "user gives their own verdict in this conversation. Keep answers short and plain.")
        cmd = [self.binary, "-p", text, "--output-format", "stream-json", "--verbose",
               "--mcp-config", json.dumps({"mcpServers": {"recrate": server_entry(self.agent.path)}}),
               "--strict-mcp-config", "--tools", "", "--allowedTools", "mcp__recrate", "--setting-sources", "",
               "--append-system-prompt", context, "--max-turns", "16"]
        if self.model:
            cmd += ["--model", self.model]
        if self.session:
            cmd += ["--resume", self.session]
        return cmd

    def _result(self, name: str, content: Any, error: bool) -> ToolResult:
        """A tool result as the app shows it: figures, the comparison headline, a note."""
        text = content if isinstance(content, str) else "\n".join(
            c.get("text", "") for c in (content or []) if isinstance(c, dict) and c.get("type") == "text")
        atts: list[dict[str, Any]] = []
        try:
            payload = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            payload = None
        if error:
            atts.append({"type": "error", "text": text[:600]})
        elif isinstance(payload, dict):
            if payload.get("run_crate"):
                self.tools.last_run = Path(payload["run_crate"])
            msg = payload.get("message") or payload.get("headline")
            if name in {"reproduce", "run_followup_check"} and msg:
                n = str(payload.get("same_numbers") or "")
                ok = bool(n) and n.split("/")[0] == n.split("/")[-1]
                atts.append({"type": "comparison", "ok": ok, "headline": msg})
            elif msg:
                atts.append({"type": "note", "text": msg})
            for a in payload.get("artifacts") or []:
                if str(a.get("path", "")).lower().endswith((".png", ".jpg", ".svg")):
                    atts.append({"type": "figure", "path": a["path"], "caption": a.get("description", "")})
        return ToolResult(text, atts, error)

    def send(self, text: str) -> Iterator[tuple]:
        import subprocess

        names: dict[str, str] = {}
        proc = subprocess.Popen(self._command(text), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, cwd=str(self.agent.path))
        answered = False
        for line in proc.stdout:
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = e.get("type")
            if kind == "assistant":
                for b in e["message"].get("content", []):
                    if b.get("type") == "text" and b.get("text", "").strip():
                        answered = True
                        yield ("text", b["text"])
                    elif b.get("type") == "tool_use":
                        name = b["name"].removeprefix("mcp__recrate__")
                        names[b["id"]] = name
                        yield ("tool", name, b.get("input") or {})
            elif kind == "user":
                for b in e["message"].get("content", []):
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        name = names.get(b.get("tool_use_id"), "tool")
                        yield ("result", name, self._result(name, b.get("content"), bool(b.get("is_error"))))
            elif kind == "result":
                self.session = e.get("session_id") or self.session
                if e.get("is_error") and not answered:
                    yield ("text", f"Claude Code stopped: {e.get('result') or e.get('subtype')}")
        proc.wait()
        if proc.returncode and not answered:
            yield ("text", "Claude Code could not answer: " + (proc.stderr.read()[-600:] or f"exit {proc.returncode}"))


LOCAL_URL = os.environ.get("RECRATE_LOCAL_URL", "http://localhost:11434/v1")  # Ollama's OpenAI-compatible API


def _http_json(url: str, payload: dict | None = None, api_key: str | None = None, timeout: int = 300) -> dict:
    import urllib.request

    req = urllib.request.Request(url, data=json.dumps(payload).encode() if payload is not None else None,
                                 headers={"Content-Type": "application/json",
                                          **({"Authorization": f"Bearer {api_key}"} if api_key else {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - a server the user chose
        return json.loads(r.read())


def local_models(base_url: str = LOCAL_URL, timeout: float = 1.5) -> list[str]:
    """Models served by a local OpenAI-compatible server (Ollama, LM Studio, llama.cpp); [] if none is running."""
    try:
        return sorted(m["id"] for m in _http_json(f"{base_url.rstrip('/')}/models", timeout=timeout).get("data", []))
    except Exception:  # noqa: BLE001 - no server: that's an answer, not an error
        return []


class OpenAICompatChat:
    """An open model on your own machine (or any OpenAI-compatible server) with the paper agent's tools.
    Free, no API key: e.g. `ollama pull qwen2.5:7b` and ReCRATE finds it. Models need tool calling
    (qwen2.5, llama3.1, mistral-nemo...)."""

    def __init__(self, agent: PaperAgent, tools: ChatTools, model: str, base_url: str = LOCAL_URL,
                 api_key: str | None = None, max_paper_chars: int = 12_000):
        self.agent, self.tools, self.model = agent, tools, model
        self.url, self.api_key = base_url.rstrip("/") + "/chat/completions", api_key
        self.tool_defs = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                            "parameters": t["input_schema"]}} for t in tools_for(agent)]
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt(agent, max_paper_chars)}]

    def send(self, text: str, max_steps: int = 8) -> Iterator[tuple]:
        self.messages.append({"role": "user", "content": text})
        for _ in range(max_steps):
            out = _http_json(self.url, {"model": self.model, "messages": self.messages, "tools": self.tool_defs,
                                        "tool_choice": "auto", "temperature": 0.2}, self.api_key)
            msg = (out.get("choices") or [{}])[0].get("message") or {}
            calls = msg.get("tool_calls") or []
            self.messages.append({"role": "assistant", "content": msg.get("content") or "",
                                  **({"tool_calls": calls} if calls else {})})
            if msg.get("content"):
                yield ("text", msg["content"])
            if not calls:
                return
            for c in calls:
                fn = c.get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                name = fn.get("name", "")
                yield ("tool", name, args)
                r = self.tools.call(name, args if isinstance(args, dict) else {})
                yield ("result", name, r)
                self.messages.append({"role": "tool", "tool_call_id": c.get("id", name), "content": r.text})
        yield ("text", "_(Stopped after many steps; tell me how to continue.)_")


class LocalChat:
    """No API key: route a message to tools by keywords. Enough to drive the demo end to end."""

    HELP = ("I'm answering with simple commands (no language model is connected: see **Connect**). I understand:\n"
            "- **what is this about?** and other questions about the paper (I'll show the most relevant passages)\n"
            "- **reproduce** [method]\n"
            "- **check** my images [for method]\n"
            "- **run** [method] on my images\n"
            "- **crate** / **export**: package the latest run as an output RO-Crate\n"
            "- **compare** with the paper's parameters unchanged\n"
            "- **my pixel size is 0.5 µm** (when your images have no calibration)\n"
            "- **why does it differ?** follow-up checks, and **run check** <id>\n"
            "- **plot** published vs reproduced, or **plot Table 2: <y> by <x>** (a bar chart; say scatter, line, box or "
            "hist for another kind), **report** page\n"
            "- **methods** paragraph, **approve** / **reject** the latest run, **record**, **files**, **about**")

    def __init__(self, agent: PaperAgent, tools: ChatTools):
        self.agent, self.tools = agent, tools

    def _method(self, text: str) -> str | None:
        names = [m.name for m in self.agent.methods]
        low = text.lower().replace(" ", "_")
        hit = next((n for n in names if n.lower() in low), None)
        return hit or (names[0] if names else None)

    def _do(self, name: str, **args: Any) -> Iterator[tuple]:
        yield ("tool", name, args)
        yield ("result", name, self.tools.call(name, args))

    def overview(self) -> str:
        """What the research is, from its crate and paper: title, what it does, its methods and their status."""
        c = self.agent.card()
        lines = [f"**{c['title']}**"]
        meta = [x for x in [c.get("published") and f"published {c['published']}", c.get("doi")] if x]
        if meta:
            lines.append(" · ".join(meta))
        desc = next((m.description for m in self.agent.methods if m.description), "")
        if not desc and self.agent.paper:  # no method described: the paper's abstract, or its opening
            text = self.agent.paper.text
            m = re.search(r"(?is)abstract\W{0,5}(.{80,900}?)(?:\n\s*\n|introduction)", text)
            desc = " ".join((m.group(1) if m else text[:700]).split())
        if desc:
            lines.append(desc[:900])
        if self.agent.methods:
            lines.append("**Methods**\n" + "\n".join(
                f"- **{m.name}** ({m.language}): {m.status}" + (f". {m.detail}" if m.detail else "")
                for m in self.agent.methods))
        else:
            lines.append("No runnable method yet: see **Reproduce** for how to add one.")
        return "\n\n".join(lines)

    def send(self, text: str) -> Iterator[tuple]:
        # Only the person's words: notes the app adds for language models ("[Done in the workstation: ...]",
        # "[The user attached: ...]") must not trigger commands.
        text = re.sub(r"\[(?:Done in the|The user attached)[^\]]*\]", " ", text).strip() or text
        low = text.lower().strip()
        has = lambda *w: any(re.search(rf"\b{x}", low) for x in w)  # noqa: E731
        method = self._method(text)
        # A question about the paper ("what was excluded from the analysis?") is answered from the paper, not
        # taken as a command because it contains a command word.
        question = low.endswith("?") and not re.match(r"(can|could|would|will) you\b", low)
        cmd = lambda *w: not question and has(*w)  # noqa: E731
        needs_method = cmd("reproduce", "check", "run", "describe")
        if needs_method and method is None:
            yield ("text", "This paper agent has no runnable methods yet, so I can only answer from the paper.")
            return
        px = re.search(r"pixel size\D{0,12}?([\d]*\.?\d+)\s*(µm|um|nm|micron)?", low)
        if low in {"help", "?"}:
            yield ("text", self.HELP)
        elif px and has("is", "set", "=", "use", "my"):
            yield from self._do("set_pixel_size", pixel_size=float(px.group(1)), unit=px.group(2) or "µm",
                                source="stated in the chat")
        elif cmd("run check", "run the check", "follow-up check"):
            from .followup import propose

            ids = [f.id for f in propose(self.agent.spec(method))] if method else []
            cid = next((i for i in ids if i in low.replace(" ", "-")), None) or next(
                (f.id for f in propose(self.agent.spec(method)) if f.runnable), None)
            if cid is None:
                yield ("text", "No follow-up check can run here for this method.")
            else:
                yield ("text", f"Running the follow-up check **{cid}** (its own recorded run; the original stays):")
                yield from self._do("run_followup_check", method=method, check=cid)
        elif has("differ", "follow", "why") and method:
            yield from self._do("followup_checks", method=method)
            yield ("text", "Say **run check** and the check's id to run one.")
        elif cmd("plot", "figure", "chart"):
            # "Plot Table 2: Culmen length by Species", "plot count by image grouped by sex": a chart of a table's
            # columns, drawn by code from the file; otherwise the published-vs-reproduced chart of the latest run
            spec = re.search(r"plot\s+(?:(table\s+\S+?)\s*:\s*)?(.+?)\s+by\s+(.+?)(?:\s+grouped\s+by\s+(.+?))?[.?!]?\s*$",
                             text, re.I)
            if spec:
                kind = next((k for k in ("scatter", "line", "box", "hist") if has(k)), "bar")
                yield from self._do("make_figure", kind=kind, source=f"table:{spec.group(1)}" if spec.group(1) else "results",
                                    y=spec.group(2).strip(), x=spec.group(3).strip(), group=(spec.group(4) or "").strip())
            else:
                yield from self._do("make_figure")
        elif cmd("export ro-crate", "export the ro-crate", "paper's crate", "research object's crate", "ro-crate zip"):
            yield from self._do("export_crate")
        elif cmd("report"):
            yield from self._do("report_page")
        elif cmd("compare", "as-is", "unchanged", "without adjust"):
            yield ("text", f"Running **{method}** with the paper's parameters unchanged (a comparison, not recorded):")
            yield from self._do("compare_paper_parameters", method=method)
        elif cmd("reproduce"):
            yield ("text", f"Reproducing **{method}** on the paper's own data:")
            yield from self._do("reproduce", method=method)
        elif cmd("crate", "export", "package", "deposit", "publish"):
            yield ("text", "Packaging the latest run as an output RO-Crate:")
            yield from self._do("create_output_crate")
        elif cmd("check", "fit", "compatible"):
            yield ("text", f"Checking your files against what **{method}** expects:")
            yield from self._do("check_my_data", method=method)
        elif cmd("run", "apply", "analy"):
            yield ("text", f"Running **{method}** on your files (checks first, adjustments applied and recorded):")
            yield from self._do("run_on_my_data", method=method)
            yield ("text", "Look at the overlays. Say **approve** or **reject**, or **crate** to export the run.")
        elif cmd("approve", "looks right", "accept"):
            yield from self._do("approve_run", accepted=True)
        elif cmd("reject", "wrong"):
            yield from self._do("approve_run", accepted=False)
        elif has("methods paragraph", "methods text", "paragraph"):
            yield from self._do("methods_text")
        elif has("record", "history"):
            yield from self._do("reproducibility_record")
        elif has("files", "uploads", "my images"):
            yield from self._do("list_my_files")
        elif has("about", "what is this", "overview", "summar", "methods", "what can you", "what does"):
            yield ("text", self.overview())
        elif cmd("describe"):
            yield from self._do("describe_method", method=method)
        else:
            hits = self.agent.search(text, 3)
            if hits:
                yield ("text", "From the paper:")
                yield ("result", "search_paper", ToolResult("", [{"type": "passages", "items": hits}]))
            else:
                yield ("text", "I found nothing about that in the paper.\n\n" + self.HELP)


def claude_installed() -> bool:
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def claude_available(api_key: str | None = None) -> tuple[bool, str]:
    """Whether the app's own chat can use Claude: the SDK, and a key (given here or in the environment)."""
    if not claude_installed():
        return False, 'Install `pip install -e ".[draft]"` to chat with Claude in the app.'
    if not (api_key or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        return False, "No API key: the chat understands simple commands."
    return True, f"Claude ({MODEL})"


def copy_into(files: list[Path], uploads: Path) -> list[str]:
    uploads.mkdir(parents=True, exist_ok=True)
    for f in files:
        shutil.copy2(f, uploads / f.name)
    return [f.name for f in files]
