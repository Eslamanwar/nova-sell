"""Waiting for Image state — initial state where the agent waits for user to upload a photo."""
from __future__ import annotations

from datetime import timedelta
from typing import Optional, override

from temporalio import workflow
from temporalio.common import RetryPolicy

from agentex.lib.sdk.state_machine import StateMachine
from agentex.lib.sdk.state_machine.state_workflow import StateWorkflow
from agentex.lib.utils.logging import make_logger

from project.state_machines.novasell_agent import NovaSellData, NovaSellState

logger = make_logger(__name__)


class WaitingForImageWorkflow(StateWorkflow):
    """Initial state: waiting for the user to upload an image of the item to sell."""

    @override
    async def execute(
        self,
        state_machine: StateMachine,
        state_machine_data: Optional[NovaSellData] = None,
    ) -> str:
        if state_machine_data is None:
            return NovaSellState.FAILED

        logger.info("Waiting for image upload")

        # Check if image is already provided (e.g., from API call)
        has_image = state_machine_data.image_base64 or state_machine_data.image_url
        if not has_image:
            # Mark as waiting for user input and block until signal arrives
            state_machine_data.waiting_for_user_input = True
            await workflow.wait_condition(
                lambda: not state_machine_data.waiting_for_user_input
            )

        # After user input, check if image was provided
        if state_machine_data.image_base64:
            # Save to disk immediately to avoid large Temporal payloads
            logger.info("Saving image to disk to reduce payload size")
            try:
                disk_result = await workflow.execute_activity(
                    "upload_image_to_disk",
                    args=[
                        state_machine_data.image_base64,
                        f"upload_{state_machine_data.task_id}.jpg",
                    ],
                    start_to_close_timeout=timedelta(minutes=1),
                    retry_policy=RetryPolicy(maximum_attempts=2),
                )
                state_machine_data.image_file_path = disk_result.get("file_path", "")
                logger.info(f"Image saved to {state_machine_data.image_file_path}")
                # Clear base64 from state to keep Temporal payloads small
                state_machine_data.image_base64 = ""
            except Exception as e:
                logger.warning(f"Failed to save image to disk: {e}, keeping in memory")

            return NovaSellState.OBJECT_DETECTION

        if state_machine_data.image_url:
            logger.info("Image URL provided, proceeding to detection")
            return NovaSellState.OBJECT_DETECTION

        # User sent a message but no image — stay in this state
        return NovaSellState.WAITING_FOR_IMAGE
