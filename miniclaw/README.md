# MiniClaw

A file-first memory system for AI agents, inspired by [OpenClaw's memory architecture](https://docs.openclaw.ai/concepts/memory).

Files are the source of truth. SQLite FTS5 is just the search index — it can be rebuilt at any time from the Markdown files.

## How it works

| Tier | File | Purpose |
|---|---|---|
| `fact` | `workspace/MEMORY.md` | Durable facts and user preferences — injected into every prompt |
| `session` | `workspace/sessions/YYYY-MM-DD.md` | Task summaries — what was done and learned |
| `log` | `workspace/memory/YYYY-MM-DD.md` | Ephemeral daily log — scratch space |

Search uses FTS5 BM25 ranking with `porter ascii` tokenizer, with automatic fallback to substring scan on query syntax errors.

## API

```python
from miniclaw import init_workspace, memory_write, memory_search, memory_get

init_workspace()                              # create dirs, DB, seed bootstrap facts
memory_write("user prefers JSON", "fact")     # write to a tier
memory_search("JSON output format")           # FTS5 BM25 search
memory_get("MEMORY.md")                       # read a workspace file
```

## Integration notes

- `memory_write("...", "fact")` entries are injected into every agent prompt via `MEMORY.md` — use sparingly for stable preferences and identity facts
- `memory_search()` excludes the `fact` tier (already in prompt) to avoid duplication
- Bootstrap knowledge (`_SYSTEM_KNOWLEDGE` in `memory.py`) is seeded once into FTS5 on first startup — make it agent-specific

## TODOs

- [ ] **Make standalone module** — currently lives inside the Warden project and imports path constants from `shared.py`. To extract as a reusable package: accept `workspace_dir: Path` as a constructor argument and derive all paths from it; make `seed_knowledge()` accept an injectable `facts: list[str]` parameter. Zero logic changes required.
- [ ] **Context window / memory flush** — OpenClaw uses a hard cutoff with a pre-flush agentic turn: at ~176K/200K tokens, a silent turn fires with _"Session nearing compaction. Store durable memories now."_ — the agent writes what it judges important to `memory/YYYY-MM-DD.md` (or replies `NO_REPLY`), then old messages are hard-discarded. It also enforces `bootstrapMaxChars` (20K chars/file, 150K total cap on injected workspace files). MiniClaw's `memory_context` (FTS5 results) currently has no char cap. Short-term: add a budget cap on `memory_context`. Long-term: implement the pre-flush pattern — detect token budget, execute a flush LLM call, agent-written facts persist to `MEMORY.md`. References: [OpenClaw context docs](https://docs.openclaw.ai/concepts/context), [OpenClaw memory deep-dive](https://snowan.gitbook.io/study-notes/ai-blogs/openclaw-memory-system-deep-dive)
- [ ] **Hybrid vector search** — currently FTS5 keyword search only. Add `sqlite-vec` + embedding model for semantic similarity alongside BM25 keyword ranking.
