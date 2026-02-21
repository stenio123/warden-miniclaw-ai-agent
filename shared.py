"""
Shared constants for Warden.
"""
from pathlib import Path

# Temporal
TASK_QUEUE = "warden-queue"
WORKFLOW_ID = "warden-main"

# Models (LiteLLM format)
MODEL_MAIN = "anthropic/claude-sonnet-4-6"
MODEL_SAFETY = "anthropic/claude-haiku-4-5-20251001"

# Workspace
WORKSPACE_DIR = Path(__file__).parent / "workspace"
MEMORY_MD = WORKSPACE_DIR / "MEMORY.md"
MEMORY_DIR = WORKSPACE_DIR / "memory"
SESSIONS_DIR = WORKSPACE_DIR / "sessions"
TOOLS_DIR = WORKSPACE_DIR / "tools"
MEMORY_DB = WORKSPACE_DIR / "memory.db"
