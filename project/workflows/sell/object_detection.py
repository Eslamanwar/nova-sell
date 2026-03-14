"""Object Detection state — analyzes the uploaded image to identify the item."""
from __future__ import annotations

from datetime import timedelta
from typing import Optional, override

from temporalio import workflow
from temporalio.common import RetryPolicy

from agentex.lib.sdk.state_machine import StateMachine
from agentex.lib.sdk.state_machine.state_workflow import StateWorkflow
from agentex.lib.utils.logging import make_logger
from agentex.lib import adk
from agentex.types.text_content import TextContent

from project.state_machines.novasell_agent import NovaSellData, NovaSellState
from project.models.listing import ObjectAnalysis

logger = make_logger(__name__)


class ObjectDetectionWorkflow(StateWorkflow):
    """Analyze the uploaded image using Nova Lite to detect object type, brand, model, condition."""

    @override
    async def execute(
        self,
        state_machine: StateMachine,
        state_machine_data: Optional[NovaSellData] = None,
    ) -> str:
        if state_machine_data is None:
            return NovaSellState.FAILED

        logger.info("Starting object detection with Nova Lite")

        await adk.messages.create(
            task_id=state_machine_data.task_id,
            content=TextContent(
                author="agent",
                content="🔍 **Analyzing your image with Nova Lite...**\n\nIdentifying the item, brand, model, and assessing condition.",
            ),
        )

        try:
            result = await workflow.execute_activity(
                "detect_object",
                args=[
                    state_machine_data.image_base64,
                    state_machine_data.user_hints,
                    state_machine_data.image_file_path,
                ],
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )

            state_machine_data.object_analysis = ObjectAnalysis(**result)

            # Normalize confidence to 0-1
            confidence = float(result.get("confidence", 0))
            if confidence > 1:
                confidence = confidence / 100.0

            if confidence < 0.5:
                await adk.messages.create(
                    task_id=state_machine_data.task_id,
                    content=TextContent(
                        author="agent",
                        content=(
                            f"⚠️ **Low confidence detection** ({confidence:.0%})\n\n"
                            f"I detected: **{result.get('brand', 'Unknown')} {result.get('model', 'Unknown')}** "
                            f"({result.get('object_type', 'Unknown')})\n\n"
                            f"Could you provide more details about this item?"
                        ),
                    ),
                )
                state_machine_data.waiting_for_user_input = True
                await workflow.wait_condition(
                    lambda: not state_machine_data.waiting_for_user_input
                )
                return NovaSellState.OBJECT_DETECTION

            # High confidence — show results
            defects = result.get("visible_defects", [])
            defects_text = "\n".join([f"  - {d}" for d in defects]) if defects else "  None detected"
            accessories = result.get("accessories", [])
            accessories_text = ", ".join(accessories) if accessories else "None detected"

            await adk.messages.create(
                task_id=state_machine_data.task_id,
                content=TextContent(
                    author="agent",
                    content=(
                        f"✅ **Item Identified!** ({confidence:.0%} confidence)\n\n"
                        f"| Property | Value |\n"
                        f"|----------|-------|\n"
                        f"| **Type** | {result.get('object_type', 'Unknown')} |\n"
                        f"| **Brand** | {result.get('brand', 'Unknown')} |\n"
                        f"| **Model** | {result.get('model', 'Unknown')} |\n"
                        f"| **Color** | {result.get('color', 'Unknown')} |\n"
                        f"| **Condition** | {result.get('condition_score', 0)}/10 — {result.get('condition_description', 'Unknown')} |\n"
                        f"| **Accessories** | {accessories_text} |\n\n"
                        f"**Visible Defects:**\n{defects_text}\n\n"
                        f"💰 Now estimating market value for Dubai..."
                    ),
                ),
            )

            return NovaSellState.PRICING

        except Exception as e:
            logger.error(f"Object detection failed: {e}")
            state_machine_data.error_message = f"Object detection failed: {str(e)}"
            await adk.messages.create(
                task_id=state_machine_data.task_id,
                content=TextContent(
                    author="agent",
                    content=f"❌ **Detection Failed**\n\n{str(e)}\n\nPlease try uploading a clearer photo.",
                ),
            )
            return NovaSellState.FAILED