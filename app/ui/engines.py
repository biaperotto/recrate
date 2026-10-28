"""Which model answers in the chat: Claude Code, Claude, a local model, or simple commands."""

from __future__ import annotations

import streamlit as st

from recrate.agent import PaperAgent
from recrate.chat import (
    ChatTools,
    ClaudeChat,
    ClaudeCodeChat,
    LocalChat,
    OpenAICompatChat,
    claude_available,
    claude_code_binary,
    local_models,
)
from ui.common import ss


# ---- which model answers in the chat ----------------------------------------------------------
@st.cache_data(ttl=20, show_spinner=False)
def available_models(url: str) -> list[str]:
    return local_models(url)
def pick_model(models: list[str]) -> str | None:
    for pref in ("qwen2.5", "qwen3", "llama3.1", "llama3.2", "mistral", "gemma"):
        hit = next((m for m in models if pref in m.lower()), None)
        if hit:
            return hit
    return models[0] if models else None
AGENTS = {"claude": "Claude", "local": "Local model", "commands": "Simple commands"}
def default_agent() -> str:
    """Claude when it is available (Claude Code signed in, or an API key), else a local model if one runs."""
    if claude_code_binary() or claude_available(ss.get("api_key"))[0]:
        return "claude"
    return "local" if available_models(ss.local_url) else "commands"
def engine_plan() -> tuple[str, str]:
    """(kind, label) for the chosen agent: subscription | claude | local | commands, and the name shown on the chat."""
    choice = ss.engine_choice if ss.engine_choice in AGENTS else "claude"
    if choice == "claude":
        if claude_code_binary():
            return "subscription", "Claude"
        if claude_available(ss.get("api_key"))[0]:
            return "claude", "Claude"
    if choice == "local":
        models = available_models(ss.local_url)
        model = ss.get("local_model") if ss.get("local_model") in models else pick_model(models)
        if model:
            return "local", model
    return "commands", "Simple commands"
def make_engine(agent: PaperAgent, tools: ChatTools):
    kind, _ = engine_plan()
    if kind == "subscription":
        return ClaudeCodeChat(agent, tools)
    if kind == "claude":
        return ClaudeChat(agent, tools, api_key=ss.get("api_key"))
    if kind == "local":
        models = available_models(ss.local_url)
        model = ss.get("local_model") if ss.get("local_model") in models else pick_model(models)
        return OpenAICompatChat(agent, tools, model, ss.local_url)
    return LocalChat(agent, tools)
