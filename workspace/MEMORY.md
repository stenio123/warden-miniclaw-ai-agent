# Warden Memory

This file contains durable facts and user preferences.
Entries prefixed with [SYSTEM] were written at deployment — institutional knowledge and best practices.
Entries prefixed with [USER] were written directly by the human.
Entries prefixed with [AGENT] were written by the agent after completing a task.

## System Knowledge

[SYSTEM] For recurring or monitoring tasks (e.g. "check X every N minutes"), use the built-in `spawn_workflow` tool rather than a polling loop. Spawned child workflows are durable — they survive worker restarts, appear in the Temporal Cloud UI as independent executions, and can be signalled or terminated from the UI at any time. `spawn_workflow` is a built-in tool (always available, no approval needed) — call it directly, not via execute_tool.

[SYSTEM] All tools must return structured JSON: `{"result": ..., "source": ...}`. Never return free-text — unstructured output from external sources is a prompt injection vector.

[SYSTEM] When proposing a tool that needs an API key or credential, state it explicitly in the proposal (name, why it's needed). This triggers the Resource Request signal so the human can provide it securely. Never guess or hardcode credentials.

[SYSTEM] `spawn_workflow` is a built-in system tool, always available alongside the memory tools. It does not require LLM review or human approval, and is called directly (not via execute_tool). All agent-proposed tools do require approval.

