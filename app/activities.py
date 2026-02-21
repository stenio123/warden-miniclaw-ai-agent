"""
Warden Activities — all Temporal activity implementations.

Activity categories:
  Memory   : search, write, get — thin wrappers over app.memory
  Tool     : execute a validated tool from workspace/tools/
  LLM      : plain LLM call (no Runner) for planning and safety review phases
"""
import importlib.util
import json

from temporalio import activity

from miniclaw import init_workspace, memory_get, memory_search, memory_write
from shared import MODEL_MAIN, MODEL_SAFETY, TOOLS_DIR


class WardenActivities:

    # -------------------------------------------------------------------------
    # Memory activities
    # -------------------------------------------------------------------------

    @activity.defn(name="memory_search")
    async def memory_search_activity(self, query: str) -> str:
        activity.logger.info(f"memory_search: {query!r}")
        init_workspace()
        return memory_search(query)

    @activity.defn(name="memory_write")
    async def memory_write_activity(self, content: str, tier: str = "log") -> str:
        activity.logger.info(f"memory_write [{tier}]: {content[:80]!r}")
        init_workspace()
        return memory_write(content, tier)

    @activity.defn(name="memory_get")
    async def memory_get_activity(self, file: str) -> str:
        activity.logger.info(f"memory_get: {file!r}")
        return memory_get(file)

    # -------------------------------------------------------------------------
    # Tool execution activity
    # -------------------------------------------------------------------------

    @activity.defn(name="execute_tool")
    async def execute_tool_activity(self, tool_name: str, args_json: str) -> str:
        """Dynamically load and run a validated tool from workspace/tools/.

        Each tool file must expose: run(args: dict) -> str
        """
        activity.logger.info(f"execute_tool: {tool_name} args={args_json[:120]}")

        tool_path = TOOLS_DIR / f"{tool_name}.py"
        if not tool_path.exists():
            return f"Error: tool '{tool_name}' not found in toolbox"

        try:
            args = json.loads(args_json)
        except json.JSONDecodeError as e:
            return f"Error: invalid args JSON — {e}"

        try:
            spec = importlib.util.spec_from_file_location(tool_name, tool_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            if not hasattr(module, "run"):
                return f"Error: tool '{tool_name}' has no run() function"

            result = module.run(args)
            return str(result)
        except Exception as e:
            activity.logger.error(f"execute_tool error: {e}")
            return f"Error executing '{tool_name}': {e}"

    # -------------------------------------------------------------------------
    # Tool file write (called by ValidateToolWorkflow on human approval)
    # -------------------------------------------------------------------------

    @activity.defn(name="write_tool_file")
    async def write_tool_file_activity(self, tool_name: str, tool_code: str) -> str:
        """Write validated tool code to workspace/tools/<tool_name>.py."""
        activity.logger.info(f"write_tool_file: {tool_name!r}")
        TOOLS_DIR.mkdir(parents=True, exist_ok=True)
        tool_path = TOOLS_DIR / f"{tool_name}.py"
        tool_path.write_text(tool_code)
        return f"Tool '{tool_name}' written to {tool_path}"

    # -------------------------------------------------------------------------
    # Toolbox listing
    # -------------------------------------------------------------------------

    @activity.defn(name="list_tools")
    async def list_tools_activity(self) -> list[str]:
        """Return names of all validated tools currently in the toolbox."""
        from shared import TOOLS_DIR
        return [p.stem for p in sorted(TOOLS_DIR.glob("*.py"))]

    # -------------------------------------------------------------------------
    # Spawn workflow activity — starts a child workflow or Temporal Schedule
    # -------------------------------------------------------------------------

    @activity.defn(name="spawn_workflow")
    async def spawn_workflow_activity(
        self,
        workflow_type: str,
        workflow_id: str,
        schedule: str = "",
        params_json: str = "{}",
    ) -> str:
        """Start a child workflow or a recurring Temporal Schedule.

        Args:
            workflow_type: Temporal workflow class name to start.
            workflow_id:   Stable ID for the workflow (must be unique).
            schedule:      Optional cron expression (e.g. "0 5 * * *").
                           If omitted, starts a single workflow execution.
            params_json:   JSON string of input parameters for the workflow.

        Returns:
            JSON string with result, source, and workflow_id.
        """
        import json
        import os
        from pathlib import Path

        from dotenv import load_dotenv
        from temporalio.client import Client, TLSConfig

        activity.logger.info(f"spawn_workflow: {workflow_type!r} id={workflow_id!r} schedule={schedule!r}")

        load_dotenv()
        address = os.getenv("TEMPORAL_ADDRESS", "localhost:7233")
        namespace = os.getenv("TEMPORAL_NAMESPACE", "default")
        task_queue = os.getenv("TASK_QUEUE", "warden-queue")

        tls = None
        cert = os.getenv("TEMPORAL_TLS_CERT")
        key = os.getenv("TEMPORAL_TLS_KEY")
        if cert and key:
            tls = TLSConfig(
                client_cert=Path(cert).read_bytes(),
                client_private_key=Path(key).read_bytes(),
            )

        client = await Client.connect(address, namespace=namespace, tls=tls)
        params = json.loads(params_json)

        if schedule:
            from temporalio.client import (
                Schedule,
                ScheduleActionStartWorkflow,
                ScheduleSpec,
            )
            await client.create_schedule(
                f"schedule-{workflow_id}",
                Schedule(
                    action=ScheduleActionStartWorkflow(
                        workflow_type,
                        params,
                        id=workflow_id,
                        task_queue=task_queue,
                    ),
                    spec=ScheduleSpec(cron_expressions=[schedule]),
                ),
            )
            return json.dumps({
                "result": f"Created schedule '{schedule}' for workflow '{workflow_id}'",
                "source": "spawn_workflow",
                "workflow_id": workflow_id,
            })
        else:
            handle = await client.start_workflow(
                workflow_type,
                params,
                id=workflow_id,
                task_queue=task_queue,
            )
            return json.dumps({
                "result": f"Started workflow '{workflow_id}'",
                "source": "spawn_workflow",
                "workflow_id": handle.id,
            })

    # -------------------------------------------------------------------------
    # Plain LLM call (no Runner) — used for planning and safety review
    # -------------------------------------------------------------------------

    @activity.defn(name="call_llm")
    async def call_llm_activity(self, prompt: str, model: str = MODEL_MAIN) -> str:
        """Single LLM call without the agent loop.

        Used for:
          - Planning phase: "what tools do you need for this goal?"
          - Safety review: "is this proposed tool code safe?"

        Args:
            prompt: Full prompt text.
            model: LiteLLM model string. Defaults to MODEL_MAIN.
        """
        import litellm  # lazy: keeps this module out of the workflow sandbox
        activity.logger.info(f"call_llm [{model}]: {prompt[:120]!r}")
        response = await litellm.acompletion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.choices[0].message.content
        activity.logger.info(f"call_llm response: {content[:120]!r}")
        return content
