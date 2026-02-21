"""
WardenWorkflow — long-running self-improving agent.

The workflow runs indefinitely, processing goals one at a time from a queue.
New capabilities are added via ValidateToolWorkflow child workflows (Stage 4c+).

Signals
-------
  new_goal(goal: str)          Queue a new task for the agent.
  refresh_tools()              Rebuild tool context after a new tool is approved.
  deny_tool(tool_name: str)    Immediately revoke a tool; added to deny-list.

Queries
-------
  get_status() -> dict         Current state snapshot.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Deque, List, Optional

from temporalio import workflow
from temporalio.contrib.openai_agents.workflow import activity_as_tool

with workflow.unsafe.imports_passed_through():
    from agents import Runner
    from app import llm_client

from app.activities import WardenActivities
from app.tool_proposal import parse_tool_proposal
from app.validate_workflow import ValidateToolInput, ValidateToolWorkflow
from shared import TASK_QUEUE


_SESSION_HISTORY_HEAD = 500        # chars of oldest entries to preserve (orientation)
_SESSION_HISTORY_TAIL = 3500       # chars of most-recent entries to preserve (recency)
_SESSION_HISTORY_MAX_CHARS = _SESSION_HISTORY_HEAD + _SESSION_HISTORY_TAIL  # total budget


@dataclass
class WardenInput:
    initial_goal: str = ""
    denied_tools: List[str] = field(default_factory=list)
    last_result: Optional[str] = None


@workflow.defn
class WardenWorkflow:
    """Long-running Warden agent workflow."""

    def __init__(self) -> None:
        self.goal_queue: Deque[str] = deque()
        self._refresh_tools: bool = False
        self.denied_tools: List[str] = []
        self.goals_processed: int = 0
        self.current_goal: Optional[str] = None
        self.status: str = "idle"
        self.pending_child_id: Optional[str] = None
        self.last_result: Optional[str] = None

    # -------------------------------------------------------------------------
    # Main run loop
    # -------------------------------------------------------------------------

    @workflow.run
    async def run(self, input: WardenInput) -> str:
        # Carry over state from continue_as_new
        self.denied_tools = list(input.denied_tools)
        self.last_result = input.last_result
        if input.initial_goal:
            self.goal_queue.append(input.initial_goal)

        _act_timeout = timedelta(seconds=60)
        _tool_timeout = timedelta(seconds=120)

        # Build static tool wrappers (memory + execute_tool). These never change
        # — only the *instructions* listing which tools are available is rebuilt.
        static_tools = [
            activity_as_tool(
                WardenActivities.memory_search_activity,
                start_to_close_timeout=_act_timeout,
            ),
            activity_as_tool(
                WardenActivities.memory_write_activity,
                start_to_close_timeout=_act_timeout,
            ),
            activity_as_tool(
                WardenActivities.memory_get_activity,
                start_to_close_timeout=_act_timeout,
            ),
            activity_as_tool(
                WardenActivities.execute_tool_activity,
                start_to_close_timeout=_tool_timeout,
            ),
            activity_as_tool(
                WardenActivities.spawn_workflow_activity,
                start_to_close_timeout=_act_timeout,
            ),
        ]

        while True:
            # Wait for a goal or a tool-refresh signal
            await workflow.wait_condition(
                lambda: bool(self.goal_queue) or self._refresh_tools
            )

            # Handle tool refresh (no goal needed — just update instructions next turn)
            if self._refresh_tools:
                self._refresh_tools = False
                workflow.logger.info("Toolbox refresh acknowledged")

            if not self.goal_queue:
                continue

            goal = self.goal_queue.popleft()
            self.current_goal = goal
            self.status = "working"
            workflow.logger.info(f"Processing goal: {goal[:80]!r}")

            memory_context = await workflow.execute_activity(
                WardenActivities.memory_search_activity,
                goal,
                start_to_close_timeout=_act_timeout,
            )
            tool_names = await workflow.execute_activity(
                WardenActivities.list_tools_activity,
                start_to_close_timeout=timedelta(seconds=10),
            )
            # Load MEMORY.md directly — always injected regardless of search match
            memory_md = await workflow.execute_activity(
                WardenActivities.memory_get_activity,
                "MEMORY.md",
                start_to_close_timeout=timedelta(seconds=10),
            )
            # Load today's session log for chronological conversation history
            today = workflow.now().strftime("%Y-%m-%d")
            recent_history = await workflow.execute_activity(
                WardenActivities.memory_get_activity,
                f"sessions/{today}.md",
                start_to_close_timeout=timedelta(seconds=10),
            )

            available = [t for t in tool_names if t not in self.denied_tools]

            instructions = _build_instructions(goal, memory_md, memory_context, recent_history, available)
            agent = llm_client.create_agent(instructions=instructions, tools=static_tools)

            result = await Runner.run(agent, input=goal)
            # final_output is None/empty when the agent's last action was a tool call
            # (memory_write as a trailing call is the common culprit)
            output = result.final_output or ""
            if not output:
                # Fallback: scan new_messages for the last assistant text
                for msg in reversed(list(result.new_messages or [])):
                    content = getattr(msg, "content", None)
                    if not isinstance(content, list):
                        continue
                    for item in content:
                        text = getattr(item, "text", None)
                        if text:
                            output = text
                            break
                    if output:
                        break

            # Check whether the agent is proposing a new tool
            proposal = parse_tool_proposal(output)

            if proposal:
                await workflow.execute_activity(
                    WardenActivities.memory_write_activity,
                    args=[
                        f"[AGENT] Goal: {goal[:80]}\n"
                        f"Proposing tool: {proposal['name']}\n{output}",
                        "session",
                    ],
                    start_to_close_timeout=_act_timeout,
                )
                workflow.logger.info(f"Tool proposal detected: {proposal['name']!r}")

                # Spawn ValidateToolWorkflow as a child — blocks until human acts
                self.status = "awaiting_tool_approval"
                ts = workflow.now().strftime("%Y%m%d%H%M%S")
                child_id = (
                    f"{workflow.info().workflow_id}"
                    f"-validate-{proposal['name']}-{ts}"
                )
                self.pending_child_id = child_id
                validate_result = await workflow.execute_child_workflow(
                    ValidateToolWorkflow.run,
                    ValidateToolInput(
                        tool_name=proposal["name"],
                        tool_code=proposal["code"],
                        proposal=proposal.get("description", ""),
                        requester=workflow.info().workflow_id,
                    ),
                    id=child_id,
                    task_queue=TASK_QUEUE,
                )

                self.pending_child_id = None

                if validate_result.approved:
                    workflow.logger.info(
                        f"Tool {proposal['name']!r} approved and activated"
                    )
                    await workflow.execute_activity(
                        WardenActivities.memory_write_activity,
                        args=[
                            f"[AGENT] Tool '{proposal['name']}' approved and activated.",
                            "session",
                        ],
                        start_to_close_timeout=_act_timeout,
                    )
                    # Re-queue the original goal so the agent can now use the new tool
                    resume_goal = (
                        f"{goal}\n\n"
                        f"[UPDATE] Your proposed tool '{proposal['name']}' was just approved "
                        f"and is now available in your toolbox. Please complete the original goal."
                    )
                    self.goal_queue.appendleft(resume_goal)
                else:
                    # Log rejection + re-queue original goal with feedback
                    rejection_note = (
                        f"[AGENT] Tool proposal '{proposal['name']}' rejected.\n"
                        f"Reason: {validate_result.reason}"
                    )
                    await workflow.execute_activity(
                        WardenActivities.memory_write_activity,
                        args=[rejection_note, "session"],
                        start_to_close_timeout=_act_timeout,
                    )
                    workflow.logger.warning(
                        f"Tool rejected: {validate_result.reason!r}"
                    )
                    retry_goal = (
                        f"Retry: {goal}\n\n"
                        f"Your tool proposal '{proposal['name']}' was rejected.\n"
                        f"Feedback: {validate_result.reason}\n"
                        f"Complete the goal using existing tools, or revise your "
                        f"proposal to address this feedback."
                    )
                    self.goal_queue.appendleft(retry_goal)

            else:
                # Normal completion
                self.last_result = output
                await workflow.execute_activity(
                    WardenActivities.memory_write_activity,
                    args=[f"[AGENT] Goal: {goal[:80]}\nResult: {output}", "session"],
                    start_to_close_timeout=_act_timeout,
                )
                workflow.logger.info(f"Goal complete: {output[:120]!r}")

            self.current_goal = None
            self.goals_processed += 1
            self.status = "idle"

            # Prune event history when Temporal recommends it
            if workflow.info().is_continue_as_new_suggested():
                workflow.logger.info("Continuing as new to prune workflow history")
                workflow.continue_as_new(
                    WardenInput(denied_tools=self.denied_tools, last_result=self.last_result)
                )

    # -------------------------------------------------------------------------
    # Signals
    # -------------------------------------------------------------------------

    @workflow.signal
    async def new_goal(self, goal: str) -> None:
        workflow.logger.info(f"Signal new_goal: {goal[:80]!r}")
        self.goal_queue.append(goal)

    @workflow.signal
    async def refresh_tools(self) -> None:
        workflow.logger.info("Signal refresh_tools received")
        self._refresh_tools = True

    @workflow.signal
    async def deny_tool(self, tool_name: str) -> None:
        workflow.logger.info(f"Signal deny_tool: {tool_name!r}")
        if tool_name not in self.denied_tools:
            self.denied_tools.append(tool_name)

    # -------------------------------------------------------------------------
    # Queries
    # -------------------------------------------------------------------------

    @workflow.query
    def get_status(self) -> dict:
        return {
            "status": self.status,
            "current_goal": self.current_goal,
            "goals_processed": self.goals_processed,
            "denied_tools": self.denied_tools,
            "queue_depth": len(self.goal_queue),
            "pending_child_id": self.pending_child_id,
            "last_result": self.last_result,
        }


# ---------------------------------------------------------------------------
# Helpers (pure functions — safe to call from workflow context)
# ---------------------------------------------------------------------------

_MEMORY_MD_MAX_CHARS = 4000   # cap on injected MEMORY.md size


def _build_instructions(
    goal: str,
    memory_md: str,
    memory_context: str,
    recent_history: str,
    available_tools: list[str],
) -> str:
    """Build the dynamic instructions string for the agent."""
    tools_section = (
        "\n".join(f"  - {t}" for t in available_tools)
        if available_tools
        else "  (no custom tools approved yet)"
    )
    if not recent_history or recent_history.startswith("File not found"):
        history_section = "(no activity yet today)"
    elif len(recent_history) > _SESSION_HISTORY_MAX_CHARS:
        # Head+tail: preserve oldest entries for orientation + newest for recency
        head = recent_history[:_SESSION_HISTORY_HEAD]
        tail = recent_history[-_SESSION_HISTORY_TAIL:]
        history_section = head + "\n...(middle entries omitted)...\n" + tail
    else:
        history_section = recent_history

    if not memory_md or memory_md.startswith("File not found"):
        memory_md_section = "(empty)"
    elif len(memory_md) > _MEMORY_MD_MAX_CHARS:
        memory_md_section = memory_md[-_MEMORY_MD_MAX_CHARS:]
    else:
        memory_md_section = memory_md

    return f"""# Current Goal

{goal}

# Operator Memory (MEMORY.md — always current)
{memory_md_section}

# Today's Session History (chronological)
{history_section}

# Relevant Past Context (search results: session logs, system knowledge)
{memory_context}

# Custom Toolbox
Use `execute_tool` to call any tool by name, passing args as a JSON string.
Available custom tools:
{tools_section}

# Built-in Tools (always available, no approval needed)
- memory_search      — search past knowledge (args: query)
- memory_write       — save a fact or log entry (args: content, tier)
- memory_get         — read a workspace file (args: file, e.g. "MEMORY.md")
- spawn_workflow     — start a durable child workflow or recurring Temporal Schedule
                       (args: workflow_type, workflow_id, schedule [cron, optional], params_json [optional])

# Tool Proposal Protocol
If your current toolbox cannot complete this goal, propose a new tool by including
EXACTLY ONE JSON block in your response:

```json
{{
  "propose_tool": {{
    "name": "snake_case_name",
    "description": "One sentence: what the tool does",
    "code": "import os\\n\\ndef run(args: dict) -> str:\\n    ...",
    "needs_env_vars": ["ENV_VAR_NAME"]
  }}
}}
```

Rules:
- Only propose when genuinely blocked — not as a first instinct
- `code` must be a complete Python module with a single `run(args: dict) -> str`
- `run()` must return a JSON string: {{"result": ..., "source": ...}}
- Read secrets via `os.environ.get("KEY")` — never hardcode credentials
- `needs_env_vars` lists env var names the operator must set before activating
- After the JSON block, explain what the tool does and why it's needed

When finished (no proposal):
1. Call memory_write to log the outcome (if worth recording)
2. End with a plain-text summary of what you did (1–3 sentences)

IMPORTANT: your final output MUST be plain text. Do not call any tools after your summary.
"""
