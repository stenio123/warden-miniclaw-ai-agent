# Warden

A self-improving AI agent with enterprise-grade safety controls, built on Temporal for durable execution.

Warden runs as a long-lived Temporal Workflow that survives crashes and restarts, builds its own tools, remembers what it learns, and requires explicit human approval before any new capability is activated. It starts with limited tools, and can gradually create them as needed with user input and approval.

| Capability | How |
|---|---|
| **Durable execution** | Temporal Workflow — survives crashes, replays from history, never loses state |
| **Self-improving** | Agent proposes and writes its own Python tools; validated tools persist across sessions |
| **Memory** | [MiniClaw](miniclaw/README.md) — file-first (Markdown) + SQLite FTS5 search; facts, session logs, and daily activity separated by tier |
| **Three-layer safety** | LLM safety review → explicit human approval gate → revocable allow-list at any time |
| **Human-in-the-loop** | Signals pause the workflow for credentials, stuck-agent decisions, and tool approvals — all surfaced in the UI |
| **Auditable** | Temporal Cloud UI shows full workflow history, child workflows, and every signal — live on a second monitor |

---

## Installation

**Prerequisites:** Python 3.11+, [uv](https://docs.astral.sh/uv/), [Temporal CLI](https://docs.temporal.io/cli)

```bash
cp .env.example .env   # add ANTHROPIC_API_KEY (and optionally NEWS_API_KEY)
uv sync
```

Run each of the following in a separate terminal:

```bash
# Terminal 1 — Temporal dev server
temporal server start-dev

# Terminal 2 — Warden worker
uv run python worker.py

# Terminal 3 — Flask UI  (http://localhost:5001)
uv run python server.py

# Terminal 4 — Start the workflow
uv run python client.py start
```

Open **http://localhost:5001** to access the UI. The Temporal Cloud UI at **http://localhost:8233** shows the full workflow timeline.

---

## Demo

A suggested sequence that exercises each major capability:

| Goal | Capability demonstrated |
|---|---|
| `"What is your name and what can you do?"` | Agent identity (SOUL.md), memory search, no tools needed |
| `"What is the current price of AAPL?"` | Tool proposal → LLM safety review → human approval gate → agent retries with new tool |
| `"Find recent news about Temporal Technologies"` | Approved tool execution, structured JSON output, MiniClaw session log |
| `"Remember that I prefer summaries in bullet points"` | MiniClaw `fact` tier write — persists across sessions in MEMORY.md |
| `"Send me a Temporal news summary every day at 9am"` | `spawn_workflow` built-in, Temporal Schedule creation, durable recurring task visible in UI |
| `"What have you done today?"` | MiniClaw session log retrieval, memory continuity across goals |

To test **durability**: kill the worker mid-task (`Ctrl+C` on Terminal 2), restart it, and observe the workflow resume from exactly where it stopped.

---

## Warden vs OpenClaw

[OpenClaw](https://github.com/openclaw/openclaw) is the benchmark for long living and self improving AI agents. Warden is narrower in scope but focuses on security and durability.

| Area | OpenClaw | Warden |
|---|---|---|
| **Durability** | JSONL files — loses at most one line on crash | Temporal event history — zero data loss, deterministic replay |
| **Scheduled/long-running tasks** | Platform cron, external to agent | Temporal Schedules via `spawn_workflow` built-in — durable, visible in UI, survives crashes |
| **Tool governance** | Skills are community-authored; no explicit human approval gate | Three layers: LLM review → human approval → revocable allow-list. Nothing runs without human sign-off |
| **Prompt injection defense** | Skills can be overridden by injection; `after_tool_result` hook is [still a feature request](https://github.com/openclaw/openclaw/discussions/5178) | `ExecuteToolActivity` is the external intercept point by default — outside agent context, cannot be bypassed |
| **Sub-agent nesting** | Sub-agents [cannot spawn sub-agents](https://github.com/openclaw/openclaw/issues/17511) — main agent is the orchestrator bottleneck | Child workflows can spawn their own children arbitrarily deep |
| **Infinite loop protection** | Sequential queue prevents concurrent chaos | Temporal retry policies handle transient failures |
| **Audit trail** | JSONL files, grep-able | Temporal Cloud UI — full visual timeline, child workflows, signals, queryable state |
| **Secrets** | Credentials stored in local files | Credentials in `.env`, never written to Temporal event history or hardcoded in tool code; agent requests by name, operator provides via file update |

**Where OpenClaw wins:** multi-platform (Telegram, Discord, Slack, WhatsApp natively), a large community skill ecosystem, and years of real-world usage. Warden is a focused prototype, not a replacement.

**MiniClaw** (`miniclaw/`) is Warden's memory subsystem, inspired by OpenClaw's file-first memory architecture. It is designed to be extractable as a standalone component — see [miniclaw/README.md](miniclaw/README.md).

---

## TODOs

- [ ] **Update `workspace/SOUL.md` tone** — make it more concise and personality-driven, closer to OpenClaw's style (less manual, more voice). Operational instructions (memory usage, tool proposal format) should move to the per-goal dynamic `instructions` built in the workflow, leaving SOUL.md for identity and guardrails only.
  - Reference: [seedprod/openclaw-prompts-and-skills SOUL.md](https://github.com/seedprod/openclaw-prompts-and-skills/blob/main/SOUL.md)
  - Reference: [openclaw/openclaw system-prompt.ts](https://github.com/openclaw/openclaw/blob/main/src/agents/system-prompt.ts)
- [ ] **Degraded mode while tool awaits approval** — currently the workflow blocks until a human approves a proposed tool. Production variant: agent continues with existing tools and completes what it can; new tool activates asynchronously once approved. Requires decoupling `ValidateToolWorkflow` from the main execution path.
- [ ] **Context window / memory flush** — implement the pre-flush agentic turn (see [miniclaw/README.md](miniclaw/README.md) for full details). Warden-side change: detect token budget in `_build_instructions`, execute a `call_llm` activity with the flush prompt before truncating history.
- [ ] **Enforce `deny_tool` at the file level** — currently `deny_tool` removes a tool from the instructions listing but leaves the file on disk. Production fix: move denied tool files to `workspace/tools/denied/` and block `execute_tool_activity` from loading any file in that folder, providing both logical and filesystem-level isolation without permanently deleting tools (they remain auditable and can be re-evaluated).
- [ ] **Activity input dataclasses** — Temporal recommends a single `@dataclass` per activity instead of multiple positional parameters, so adding optional fields stays backward-compatible with in-flight workflow history. Currently `memory_write`, `execute_tool`, `write_tool_file`, `call_llm`, and `spawn_workflow` activities all use flat params. The refactor is mechanical; the main uncertainty is whether `activity_as_tool` derives the LLM tool schema correctly from a dataclass input vs flat params — test that before committing.
- [ ] **Parallel activity dispatch in `WardenWorkflow`** — memory search and tool listing currently run sequentially. Temporal's custom asyncio event loop supports native parallelism: `asyncio.gather(workflow.execute_activity(...), workflow.execute_activity(...))` or `asyncio.TaskGroup` (Python 3.11+) work without any Temporal-specific wrappers. Use `workflow.as_completed(tasks)` instead of `asyncio.as_completed` to ensure deterministic replay order. Reference: [Durable distributed asyncio event loop](https://temporal.io/blog/durable-distributed-asyncio-event-loop)
- [x] **Recurring tasks as child workflows** — currently a monitoring tool (e.g. "check HN every 5 min") runs as a long-lived activity via `ExecuteToolActivity`. The right production architecture: the agent expresses intent for a recurring task, the workflow spawns a dedicated child workflow with `workflow.sleep()` in a loop. The child appears in Temporal Cloud UI as an independent, durable, signal-able execution — not just a running activity. Requires distinguishing "run once" vs "run recurring" in the agent's tool proposal format.

---

## Security Improvements

### Prompt Injection from Tool Output

Tools that fetch external content (web pages, APIs) can return adversarial text designed to hijack the agent ("ignore previous instructions…"). The OpenClaw ecosystem has converged on three approaches to this problem:

| Approach | How | Cost |
|---|---|---|
| **Pattern-based skill** | Regex matching across 5 categories: instruction overrides, goal manipulation, exfiltration, encoding/obfuscation, social engineering | Negligible |
| **ML classifier via hook** | Local HuggingFace model intercepts tool output before it reaches the agent | 100–300ms/call, 1.2GB RAM — too heavy for demo |
| **Code-level plugin (SecureClaw)** | Enforces controls outside the agent context, so injection can't override it | Requires gateway integration |

OpenClaw's skill-only approach is limited because **skills themselves can be overridden by injection**. The fix requires interception *outside* the agent's context — which is what [SecureClaw by Adversa AI](https://www.prnewswire.com/news-releases/secureclaw-by-adversa-ai-launches-as-the-first-owasp-aligned-open-source-security-plugin-and-skill-for-openclaw-ai-agents-302688674.html) does at the gateway level, and what OpenClaw is working toward with the proposed [`after_tool_result` plugin hook](https://github.com/openclaw/openclaw/discussions/5178).

**Warden's architectural approach:** `ExecuteToolActivity` is already that external intercept point — it runs outside the agent's context entirely and cannot be bypassed by anything in the agent's prompt. This is equivalent to the `after_tool_result` hook, built in by default.

**TODO security improvements:**

- [ ] **Pattern-based scanner in `ExecuteToolActivity`** — adapt the [indirect-prompt-injection skill](https://github.com/openclaw/skills/blob/main/skills/aviv4339/indirect-prompt-injection/SKILL.md) (5-category regex). Suspicious content is replaced with `{"result": "[BLOCKED]", "warning": "..."}` before reaching the agent. Zero performance cost.
- [ ] **SOUL.md integrity check on worker startup** — SHA-256 checksum; warn if the file was modified unexpectedly. Detects drift/tampering cheaply.
- [ ] **Safety Agent reviews tool output** — run tool results through a second LLM call before feeding back to the main agent. Full defense against sophisticated attacks; adds ~1 LLM call per tool use. Best suited for production, not the demo.

**Current mitigations already in place:** structured `{"result": ..., "source": ...}` output format (limits free-text injection surface) + SOUL.md instruction to treat external tool output as untrusted data.
