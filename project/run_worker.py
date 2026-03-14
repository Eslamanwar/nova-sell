"""Temporal worker for the NovaSell Dubizzle selling agent.

Registers all workflow and activity definitions with the Temporal server.
"""
import asyncio

from agentex.lib.core.temporal.activities import get_all_activities
from agentex.lib.core.temporal.workers.worker import AgentexWorker
from agentex.lib.environment_variables import EnvironmentVariables
from agentex.lib.utils.debug import setup_debug_if_enabled
from agentex.lib.utils.logging import make_logger

from project.workflow import NovaSellWorkflow
from project.activities import (
    detect_object,
    estimate_price,
    generate_listing,
    handle_chat_message,
    negotiate_price,
    handle_voice_session,
    handle_scheduling,
    post_listing_to_marketplace,
    upload_image_to_disk,
    respond_to_marketplace_chat,
)

environment_variables = EnvironmentVariables.refresh()

logger = make_logger(__name__)


async def main():
    """Run the Temporal worker for NovaSell."""
    setup_debug_if_enabled()

    task_queue_name = environment_variables.WORKFLOW_TASK_QUEUE
    if task_queue_name is None:
        raise ValueError("WORKFLOW_TASK_QUEUE is not set")

    logger.info(f"Starting NovaSell Dubizzle agent worker on queue: {task_queue_name}")

    worker = AgentexWorker(task_queue=task_queue_name)

    all_activities = get_all_activities()
    custom_activities = [
        detect_object,
        estimate_price,
        generate_listing,
        handle_chat_message,
        negotiate_price,
        handle_voice_session,
        handle_scheduling,
        post_listing_to_marketplace,
        upload_image_to_disk,
        respond_to_marketplace_chat,
    ]
    all_activities.extend(custom_activities)

    logger.info(
        f"Registered {len(all_activities)} activities "
        f"({len(custom_activities)} custom NovaSell agents)"
    )

    await worker.run(
        activities=all_activities,
        workflow=NovaSellWorkflow,
    )


if __name__ == "__main__":
    asyncio.run(main())
