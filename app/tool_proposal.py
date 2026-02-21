"""
Helpers for parsing tool proposals from agent output.
"""
from __future__ import annotations

import json
import re


def parse_tool_proposal(output: str) -> dict | None:
    """Extract a tool proposal from a ```json ... ``` block in agent output.

    Returns the ``propose_tool`` dict if present and valid, otherwise None.
    """
    match = re.search(r"```json\s*(\{.*?\})\s*```", output, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    proposal = data.get("propose_tool")
    if not isinstance(proposal, dict):
        return None
    if not proposal.get("name") or not proposal.get("code"):
        return None
    return proposal
