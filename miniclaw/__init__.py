"""
MiniClaw — file-first memory system inspired by OpenClaw.

Public API:
    init_workspace()          — create dirs, DB table, seed bootstrap knowledge
    memory_write(content, tier) — write to fact / session / log tier
    memory_search(query, limit) — FTS5 BM25 search
    memory_get(file)            — read a workspace file by relative path
"""
from miniclaw.memory import init_workspace, memory_get, memory_search, memory_write

__all__ = ["init_workspace", "memory_write", "memory_search", "memory_get"]
