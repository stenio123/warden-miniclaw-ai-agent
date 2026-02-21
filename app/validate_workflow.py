"""
ValidateToolWorkflow — two-phase tool validation gate.

Phase 1 (LLM review): safety agent reads the proposed code and flags risks.
  - UNSAFE verdict → return immediately with reason (no human gate)
  - SAFE verdict   → proceed to Phase 2

Phase 2 (Human gate): workflow pauses; human sends approve/reject signal.
  - On approval → writes tool to workspace/tools/<name>.py,
                  signals requester WardenWorkflow.refresh_tools
  - On rejection → returns reason so the agent can revise and retry

Signals
-------
  approve(approved: bool, reason: str)   Human decision.

Queries
-------
  get_pending_approval() -> dict   Full tool info + LLM verdict for the UI.

Input
-----
  tool_name   (str)  Snake-case identifier, becomes the .py filename.
  tool_code   (str)  Full Python source (must expose run(args: dict) -> str).
  proposal    (str)  Agent's plain-English description of the tool.
  requester   (str)  Workflow ID of the requesting WardenWorkflow.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from temporalio import workflow

from app.activities import WardenActivities
from shared import MODEL_SAFETY, TOOLS_DIR


@dataclass
class ValidateToolInput:
    tool_name: str = ""
    tool_code: str = ""
    proposal: str = ""
    requester: str = ""   # workflow_id of the calling WardenWorkflow


@dataclass
class ValidateToolResult:
    approved: bool = False
    reason: str = ""


@workflow.defn
class ValidateToolWorkflow:
    """Two-phase tool validation: LLM safety review → human approval gate."""

    def __init__(self) -> None:
        self._human_decision: Optional[tuple[bool, str]] = None  # (approved, reason)
        self._pending_tool: dict = {}   # populated before human gate; cleared after

    @workflow.run
    async def run(self, input: ValidateToolInput) -> ValidateToolResult:
        if not input.tool_name or not input.tool_code:
            return ValidateToolResult(approved=False, reason="Missing tool_name or tool_code")

        _act_timeout = timedelta(seconds=60)

        # ── Phase 1: LLM safety review ────────────────────────────────────────
        safety_prompt = _build_safety_prompt(input.tool_name, input.tool_code, input.proposal)
        llm_verdict = await workflow.execute_activity(
            WardenActivities.call_llm_activity,
            args=[safety_prompt, MODEL_SAFETY],
            start_to_close_timeout=_act_timeout,
        )
        workflow.logger.info(f"LLM safety verdict for {input.tool_name!r}: {llm_verdict[:120]!r}")

        # UNSAFE → return immediately; no human gate needed
        if llm_verdict.strip().upper().startswith("UNSAFE"):
            return ValidateToolResult(
                approved=False,
                reason=f"LLM safety review rejected: {llm_verdict.strip()}",
            )

        # ── Phase 2: Human approval gate ─────────────────────────────────────
        # Store full tool details so get_pending_approval() can surface them to the UI.
        self._pending_tool = {
            "tool_name": input.tool_name,
            "proposal": input.proposal,
            "tool_code": input.tool_code,
            "llm_verdict": llm_verdict.strip(),
        }

        await workflow.wait_condition(lambda: self._human_decision is not None)

        approved, reason = self._human_decision
        self._pending_tool = {}  # no longer pending
        workflow.logger.info(
            f"Human decision for {input.tool_name!r}: approved={approved} reason={reason!r}"
        )

        if approved:
            # Write the tool file — side-effect must happen in an activity
            await workflow.execute_activity(
                WardenActivities.write_tool_file_activity,
                args=[input.tool_name, input.tool_code],
                start_to_close_timeout=_act_timeout,
            )
            # Log the approval to memory
            await workflow.execute_activity(
                WardenActivities.memory_write_activity,
                args=[
                    f"[AGENT] Tool approved and activated: {input.tool_name}\n"
                    f"Proposal: {input.proposal}",
                    "log",
                ],
                start_to_close_timeout=_act_timeout,
            )
            # Signal the parent WardenWorkflow to refresh its tool context
            if input.requester:
                parent = workflow.get_external_workflow_handle(input.requester)
                await parent.signal("refresh_tools")
                workflow.logger.info(f"Signalled refresh_tools to {input.requester!r}")

        return ValidateToolResult(approved=approved, reason=reason)

    # -------------------------------------------------------------------------
    # Signals
    # -------------------------------------------------------------------------

    @workflow.signal
    async def approve(self, approved: bool, reason: str = "") -> None:
        workflow.logger.info(f"Signal approve: approved={approved} reason={reason!r}")
        self._human_decision = (approved, reason)

    # -------------------------------------------------------------------------
    # Queries
    # -------------------------------------------------------------------------

    @workflow.query
    def get_pending_approval(self) -> dict:
        """Returns the full tool info + LLM verdict the UI needs to render the approval panel."""
        return {
            "waiting": bool(self._pending_tool),
            **self._pending_tool,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_safety_prompt(tool_name: str, tool_code: str, proposal: str) -> str:
    return f"""You are a security reviewer for an AI agent's self-built tools.
Review the proposed tool and assess whether it is SAFE to activate.

Tool name: {tool_name}
Agent's proposal: {proposal}

--- CODE START ---
{tool_code}
--- CODE END ---

Check for:
1. Destructive operations (rm -rf, DROP TABLE, mass deletion)
2. Credential exfiltration (sending secrets to external hosts)
3. Prompt injection amplifiers (executing arbitrary strings as code)
4. Network calls to unexpected endpoints
5. Infinite loops or resource exhaustion

Respond with:
SAFE: <one-sentence justification>
  OR
UNSAFE: <specific concern>

Be concise. One line only.
"""
