"""
Warden Worker — registers workflows and activities with Temporal.

Connects to Temporal Cloud (TLS) or local server depending on env vars.
"""
import asyncio
import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from agents.extensions.models.litellm_provider import LitellmProvider
from temporalio.client import Client, TLSConfig
from temporalio.contrib.openai_agents import ModelActivityParameters, OpenAIAgentsPlugin
from temporalio.worker import Worker

from app.activities import WardenActivities
from app.workflow import WardenWorkflow
from app.validate_workflow import ValidateToolWorkflow
from shared import TASK_QUEUE


def _tls_config() -> TLSConfig | None:
    cert = os.getenv("TEMPORAL_TLS_CERT")
    key = os.getenv("TEMPORAL_TLS_KEY")
    if cert and key:
        return TLSConfig(
            client_cert=Path(cert).read_bytes(),
            client_private_key=Path(key).read_bytes(),
        )
    return None


async def main() -> None:
    address = os.getenv("TEMPORAL_ADDRESS", "localhost:7233")
    namespace = os.getenv("TEMPORAL_NAMESPACE", "default")
    tls = _tls_config()

    client = await Client.connect(
        address,
        namespace=namespace,
        tls=tls,
        plugins=[
            OpenAIAgentsPlugin(
                model_params=ModelActivityParameters(
                    start_to_close_timeout=timedelta(seconds=60),
                ),
                model_provider=LitellmProvider(),
            ),
        ],
    )

    activities = WardenActivities()

    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[WardenWorkflow, ValidateToolWorkflow],
        activities=[
            activities.memory_search_activity,
            activities.memory_write_activity,
            activities.memory_get_activity,
            activities.list_tools_activity,
            activities.execute_tool_activity,
            activities.call_llm_activity,
            activities.write_tool_file_activity,
        ],
    )

    print("=" * 60)
    print("Warden worker started")
    print(f"  Address   : {address}")
    print(f"  Namespace : {namespace}")
    print(f"  TLS       : {'yes' if tls else 'no (local)'}")
    print(f"  Queue     : {TASK_QUEUE}")
    print("=" * 60)
    print("Press Ctrl+C to stop")

    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
