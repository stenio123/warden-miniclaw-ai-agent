"""
Warden client — starts the workflow and sends signals from the CLI.

Usage
-----
  # Start the workflow (idempotent — safe to run multiple times)
  python client.py start

  # Send a goal to a running workflow
  python client.py goal "Research the latest Temporal Python SDK release notes"

  # Check status
  python client.py status

  # Start a ValidateToolWorkflow directly (for testing Stage 4b in isolation)
  python client.py validate-start <workflow-id> <tool-name>

  # Approve / reject a tool proposal
  python client.py approve <validate-workflow-id>
  python client.py reject <validate-workflow-id> "Reason text"

  # Revoke a tool at any time
  python client.py deny <tool_name>
"""
import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


async def _client():
    from temporalio.client import Client, TLSConfig
    from app.workflow import WardenInput, WardenWorkflow
    from shared import TASK_QUEUE, WORKFLOW_ID

    address = os.getenv("TEMPORAL_ADDRESS", "localhost:7233")
    namespace = os.getenv("TEMPORAL_NAMESPACE", "default")

    tls = None
    cert = os.getenv("TEMPORAL_TLS_CERT")
    key = os.getenv("TEMPORAL_TLS_KEY")
    if cert and key:
        tls = TLSConfig(
            client_cert=Path(cert).read_bytes(),
            client_private_key=Path(key).read_bytes(),
        )

    return await Client.connect(address, namespace=namespace, tls=tls)


async def cmd_start(initial_goal: str = "") -> None:
    from temporalio.exceptions import WorkflowAlreadyStartedError
    from app.workflow import WardenInput, WardenWorkflow
    from shared import TASK_QUEUE, WORKFLOW_ID

    client = await _client()
    try:
        handle = await client.start_workflow(
            WardenWorkflow.run,
            WardenInput(initial_goal=initial_goal),
            id=WORKFLOW_ID,
            task_queue=TASK_QUEUE,
        )
        print(f"Started WardenWorkflow  id={handle.id}")
        if initial_goal:
            print(f"  Initial goal queued: {initial_goal!r}")
    except WorkflowAlreadyStartedError:
        print(f"Workflow already running: {WORKFLOW_ID} (sending goal if provided)")
        if initial_goal:
            await cmd_goal(initial_goal)
        else:
            raise


async def cmd_goal(goal: str) -> None:
    from shared import WORKFLOW_ID
    client = await _client()
    handle = client.get_workflow_handle(WORKFLOW_ID)
    await handle.signal("new_goal", goal)
    print(f"Sent new_goal signal: {goal!r}")


async def cmd_status() -> None:
    from shared import WORKFLOW_ID
    client = await _client()
    handle = client.get_workflow_handle(WORKFLOW_ID)
    status = await handle.query("get_status")
    print(json.dumps(status, indent=2))


async def cmd_deny(tool_name: str) -> None:
    from shared import WORKFLOW_ID
    client = await _client()
    handle = client.get_workflow_handle(WORKFLOW_ID)
    await handle.signal("deny_tool", tool_name)
    print(f"Sent deny_tool signal: {tool_name!r}")



async def cmd_validate_start(wf_id: str, tool_name: str) -> None:
    """Start a ValidateToolWorkflow directly with a trivial test tool (for Stage 4b testing)."""
    from app.validate_workflow import ValidateToolInput, ValidateToolWorkflow
    from shared import TASK_QUEUE

    test_code = (
        "def run(args: dict) -> str:\n"
        "    return '{\"result\": \"hello from test tool\", \"source\": \"test\"}'\n"
    )
    client = await _client()
    handle = await client.start_workflow(
        ValidateToolWorkflow.run,
        ValidateToolInput(
            tool_name=tool_name,
            tool_code=test_code,
            proposal=f"Test tool '{tool_name}' — prints a greeting. Used for Stage 4b validation.",
            requester="warden-main",
        ),
        id=wf_id,
        task_queue=TASK_QUEUE,
    )
    print(f"Started ValidateToolWorkflow  id={handle.id}")
    print(f"  Tool name: {tool_name!r}")
    print(f"  Waiting for LLM review, then human approval...")
    print(f"  Approve: python client.py approve {wf_id}")
    print(f"  Reject:  python client.py reject {wf_id} \"reason\"")


async def cmd_approve(validate_wf_id: str) -> None:
    client = await _client()
    handle = client.get_workflow_handle(validate_wf_id)
    await handle.signal("approve", args=[True, "Approved by human"])
    print(f"Approved tool in workflow: {validate_wf_id}")


async def cmd_reject(validate_wf_id: str, reason: str) -> None:
    client = await _client()
    handle = client.get_workflow_handle(validate_wf_id)
    await handle.signal("approve", args=[False, reason])
    print(f"Rejected tool in workflow: {validate_wf_id}  reason={reason!r}")


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    cmd = args[0]

    if cmd == "validate-start":
        if len(args) < 3:
            print("Usage: client.py validate-start <workflow-id> <tool-name>")
            sys.exit(1)
        asyncio.run(cmd_validate_start(args[1], args[2]))
    elif cmd == "start":
        goal = " ".join(args[1:])
        asyncio.run(cmd_start(goal))
    elif cmd == "goal":
        if len(args) < 2:
            print("Usage: client.py goal <goal text>")
            sys.exit(1)
        asyncio.run(cmd_goal(" ".join(args[1:])))
    elif cmd == "status":
        asyncio.run(cmd_status())
    elif cmd == "deny":
        if len(args) < 2:
            print("Usage: client.py deny <tool_name>")
            sys.exit(1)
        asyncio.run(cmd_deny(args[1]))
    elif cmd == "approve":
        if len(args) < 2:
            print("Usage: client.py approve <validate-workflow-id>")
            sys.exit(1)
        asyncio.run(cmd_approve(args[1]))
    elif cmd == "reject":
        if len(args) < 3:
            print("Usage: client.py reject <validate-workflow-id> <reason>")
            sys.exit(1)
        asyncio.run(cmd_reject(args[1], " ".join(args[2:])))
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
