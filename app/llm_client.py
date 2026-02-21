"""
LLM client — agent factory using OpenAI Agents SDK + LiteLLM.

SOUL.md is loaded here and prepended to every agent's instructions,
keeping identity and guardrails decoupled from workflow code.
"""
import os
from pathlib import Path

from agents import Agent

from shared import MODEL_MAIN, WORKSPACE_DIR

_DEFAULT_MODEL = os.getenv("LLM_MODEL", MODEL_MAIN)


def _load_soul() -> str:
    soul_path = WORKSPACE_DIR / "SOUL.md"
    if soul_path.exists():
        return soul_path.read_text().strip()
    return ""


def create_agent(
    instructions: str,
    tools: list,
    name: str = "Warden",
    model: str | None = None,
) -> Agent:
    """Create an agent with SOUL.md prepended to its instructions.

    Args:
        instructions: Dynamic context (tools list, memory, goal). Appended after SOUL.md.
        tools: List of activity_as_tool() wrappers.
        name: Agent display name.
        model: LiteLLM model string. Defaults to LLM_MODEL env var or MODEL_MAIN.
    """
    soul = _load_soul()
    full_instructions = f"{soul}\n\n---\n\n{instructions}" if soul else instructions

    return Agent(
        name=name,
        model=model or _DEFAULT_MODEL,
        instructions=full_instructions,
        tools=tools,
    )
