"""
MiniClaw: file-first memory system inspired by OpenClaw.

Files are the source of truth. SQLite FTS5 is just the search index.

Tiers:
  "fact"    → workspace/MEMORY.md          (durable facts + user preferences)
  "session" → workspace/sessions/YYYY-MM-DD.md  (task summaries)
  "log"     → workspace/memory/YYYY-MM-DD.md    (ephemeral daily log)
  "system"  → MEMORY.md (indexed only; file already has the canonical text)
"""
import sqlite3
from datetime import datetime
from pathlib import Path

from shared import MEMORY_DB, MEMORY_DIR, MEMORY_MD, SESSIONS_DIR, TOOLS_DIR, WORKSPACE_DIR


# ---------------------------------------------------------------------------
# Bootstrap knowledge — institutional facts seeded at deployment
# ---------------------------------------------------------------------------

_SYSTEM_KNOWLEDGE = [
    (
        "[SYSTEM] For recurring or monitoring tasks (e.g. 'check X every N minutes'), "
        "use the built-in `spawn_workflow` tool rather than a polling loop. "
        "Spawned child workflows are durable — they survive worker restarts, appear in "
        "the Temporal Cloud UI as independent executions, and can be signalled or "
        "terminated from the UI at any time. "
        "`spawn_workflow` is a built-in tool (always available, no approval needed) — "
        "call it directly, not via execute_tool."
    ),
    (
        '[SYSTEM] All tools must return structured JSON: {"result": ..., "source": ...}. '
        "Never return free-text — unstructured output from external sources is a "
        "prompt injection vector."
    ),
    (
        "[SYSTEM] When proposing a tool that needs an API key or credential, state it "
        "explicitly in the proposal (name, why it's needed). This triggers the Resource "
        "Request signal so the human can provide it securely. Never guess or hardcode "
        "credentials."
    ),
    (
        "[SYSTEM] `spawn_workflow` is a built-in system tool, always available alongside "
        "the memory tools. It does not require LLM review or human approval, and is called "
        "directly (not via execute_tool). All agent-proposed tools do require approval."
    ),
]


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------

def init_workspace() -> None:
    """Ensure workspace directories, DB table, and bootstrap knowledge exist."""
    for d in [MEMORY_DIR, SESSIONS_DIR, TOOLS_DIR]:
        d.mkdir(parents=True, exist_ok=True)
    if not MEMORY_MD.exists():
        MEMORY_MD.write_text(
            "# Warden Memory\n\nDurable facts and user preferences.\n"
        )
    with sqlite3.connect(str(MEMORY_DB)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                content, tier, source_file, timestamp,
                tokenize='porter ascii'
            )
        """)
    seed_knowledge()


def seed_knowledge() -> None:
    """Index bootstrap [SYSTEM] knowledge into FTS5 (idempotent).

    MEMORY.md already has the canonical text. This function ensures the same
    facts are searchable via memory_search() by inserting them into the FTS5
    index. Safe to call on every startup — skips if already seeded.
    """
    with sqlite3.connect(str(MEMORY_DB)) as conn:
        already_seeded = conn.execute(
            "SELECT COUNT(*) FROM memory_fts WHERE tier = 'system'"
        ).fetchone()[0]
        if already_seeded:
            return
        timestamp = datetime.now().isoformat()
        conn.executemany(
            "INSERT INTO memory_fts(content, tier, source_file, timestamp) VALUES (?, ?, ?, ?)",
            [(fact, "system", "MEMORY.md", timestamp) for fact in _SYSTEM_KNOWLEDGE],
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _append(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(path, "a") as f:
        f.write(f"\n<!-- {timestamp} -->\n{content}\n")


def _index(content: str, tier: str, source_file: str) -> None:
    timestamp = datetime.now().isoformat()
    with sqlite3.connect(str(MEMORY_DB)) as conn:
        conn.execute(
            "INSERT INTO memory_fts(content, tier, source_file, timestamp) VALUES (?, ?, ?, ?)",
            (content, tier, source_file, timestamp),
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def memory_write(content: str, tier: str = "log") -> str:
    """Write content to memory.

    Args:
        content: Text to store.
        tier: "fact" | "session" | "log"

    Returns:
        Confirmation string.
    """
    today = datetime.now().strftime("%Y-%m-%d")

    if tier == "fact":
        path = MEMORY_MD
    elif tier == "session":
        path = SESSIONS_DIR / f"{today}.md"
    else:
        path = MEMORY_DIR / f"{today}.md"

    _append(path, content)
    _index(content, tier, str(path.relative_to(WORKSPACE_DIR)))
    return f"Written to {path.relative_to(WORKSPACE_DIR)}"


def memory_search(query: str, limit: int = 5) -> str:
    """Search memory using FTS5 BM25 ranking.

    Args:
        query: Search terms.
        limit: Max results.

    Returns:
        Formatted results string, or "No results found."
    """
    # Exclude 'fact' tier — those entries live in MEMORY.md which is injected
    # directly into every prompt, so including them here would duplicate content.
    try:
        with sqlite3.connect(str(MEMORY_DB)) as conn:
            rows = conn.execute(
                """
                SELECT content, tier, source_file, timestamp
                FROM memory_fts
                WHERE memory_fts MATCH ? AND tier != 'fact'
                ORDER BY rank
                LIMIT ?
                """,
                (query, limit),
            ).fetchall()
    except sqlite3.OperationalError:
        # FTS5 query syntax error — fall back to substring scan
        with sqlite3.connect(str(MEMORY_DB)) as conn:
            rows = conn.execute(
                """
                SELECT content, tier, source_file, timestamp
                FROM memory_fts
                WHERE content LIKE ? AND tier != 'fact'
                LIMIT ?
                """,
                (f"%{query}%", limit),
            ).fetchall()

    if not rows:
        return "No results found."

    parts = []
    for content, tier, source_file, timestamp in rows:
        parts.append(f"[{tier} | {source_file} | {timestamp[:10]}]\n{content}")
    return "\n---\n".join(parts)


def memory_get(file: str) -> str:
    """Read a specific file from the workspace.

    Args:
        file: Path relative to workspace/ (e.g. "MEMORY.md", "sessions/2025-01-01.md")

    Returns:
        File contents, or an error string.
    """
    path = (WORKSPACE_DIR / file).resolve()
    if not str(path).startswith(str(WORKSPACE_DIR.resolve())):
        return "Error: path outside workspace"
    if not path.exists():
        return f"File not found: {file}"
    return path.read_text()


# ---------------------------------------------------------------------------
# Validation entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    init_workspace()
    print("Writing facts...")
    print(memory_write("User prefers practical applications over theory", "fact"))
    print(memory_write("web_search tool validated and approved", "log"))
    print(memory_write("Task: summarized AI agent trends. Found 3 papers.", "session"))

    print("\nSearching for 'practical'...")
    print(memory_search("practical"))

    print("\nSearching for 'web search'...")
    print(memory_search("web search"))

    print("\nReading MEMORY.md...")
    print(memory_get("MEMORY.md"))
