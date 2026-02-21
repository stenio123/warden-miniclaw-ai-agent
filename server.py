"""
Warden server — Flask UI for controlling and monitoring the agent.

Usage
-----
  uv run server.py
  open http://localhost:5001
"""
import asyncio
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

load_dotenv()

app = Flask(__name__)


# ---------------------------------------------------------------------------
# Temporal client helper
# ---------------------------------------------------------------------------

async def _temporal_client():
    from temporalio.client import Client, TLSConfig

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


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/state")
def api_state():
    async def _state():
        from shared import TOOLS_DIR, WORKFLOW_ID, WORKSPACE_DIR

        client = await _temporal_client()
        handle = client.get_workflow_handle(WORKFLOW_ID)

        try:
            status = await handle.query("get_status")
        except Exception as e:
            return {"error": str(e), "status": "unreachable"}

        # If a child validation workflow is running, fetch its pending details
        pending_approval = None
        child_id = status.get("pending_child_id")
        if child_id:
            try:
                child = client.get_workflow_handle(child_id)
                pending_approval = await child.query("get_pending_approval")
            except Exception:
                pass

        # Active tools = all tool files minus denied
        denied = status.get("denied_tools", [])
        active_tools = []
        if TOOLS_DIR.exists():
            active_tools = sorted(
                f.stem for f in TOOLS_DIR.glob("*.py") if f.stem not in denied
            )

        # Read MEMORY.md
        memory_path = WORKSPACE_DIR / "MEMORY.md"
        memory_md = memory_path.read_text() if memory_path.exists() else ""

        # Read today's session log
        today = datetime.now().strftime("%Y-%m-%d")
        session_path = WORKSPACE_DIR / "sessions" / f"{today}.md"
        session_log = session_path.read_text() if session_path.exists() else ""

        return {
            **status,
            "active_tools": active_tools,
            "pending_approval": pending_approval,
            "memory_md": memory_md,
            "session_log": session_log,
        }

    return jsonify(asyncio.run(_state()))


@app.route("/api/goal", methods=["POST"])
def api_goal():
    async def _goal(text):
        from shared import WORKFLOW_ID
        client = await _temporal_client()
        handle = client.get_workflow_handle(WORKFLOW_ID)
        await handle.signal("new_goal", text)

    data = request.get_json()
    asyncio.run(_goal(data["goal"].strip()))
    return jsonify({"ok": True})


@app.route("/api/teach", methods=["POST"])
def api_teach():
    from miniclaw import memory_write
    data = request.get_json()
    memory_write(f"[USER] {data['content'].strip()}", "fact")
    return jsonify({"ok": True})


@app.route("/api/tool-approval", methods=["POST"])
def api_tool_approval():
    async def _approve(child_id, approved, reason):
        client = await _temporal_client()
        handle = client.get_workflow_handle(child_id)
        await handle.signal("approve", args=[approved, reason])

    data = request.get_json()
    asyncio.run(_approve(data["child_id"], data["approved"], data.get("reason", "")))
    return jsonify({"ok": True})


@app.route("/api/deny-tool", methods=["POST"])
def api_deny_tool():
    async def _deny(tool_name):
        from shared import WORKFLOW_ID
        client = await _temporal_client()
        handle = client.get_workflow_handle(WORKFLOW_ID)
        await handle.signal("deny_tool", tool_name)

    data = request.get_json()
    asyncio.run(_deny(data["tool_name"]))
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(debug=True, port=5001)
