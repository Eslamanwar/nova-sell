"""Listing Generation state — creates a compelling Dubizzle marketplace listing."""
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
from project.models.listing import ListingContent

logger = make_logger(__name__)


class ListingGenerationWorkflow(StateWorkflow):
    """Generate a compelling Dubizzle listing using Nova Pro AI copywriting."""

    @override
    async def execute(
        self,
        state_machine: StateMachine,
        state_machine_data: Optional[NovaSellData] = None,
    ) -> str:
        if state_machine_data is None:
            return NovaSellState.FAILED

        if state_machine_data.object_analysis is None or state_machine_data.price_estimate is None:
            state_machine_data.error_message = "Object analysis and price estimate required"
            return NovaSellState.FAILED

        logger.info("Starting Dubizzle listing generation with Nova Pro")

        await adk.messages.create(
            task_id=state_machine_data.task_id,
            content=TextContent(
                author="agent",
                content="📝 **Crafting your Dubizzle listing...**\n\nGenerating an optimized title, description, and tags for the Dubai market.",
            ),
        )

        try:
            result = await workflow.execute_activity(
                "generate_listing",
                args=[
                    state_machine_data.object_analysis.model_dump(),
                    state_machine_data.price_estimate.model_dump(),
                    state_machine_data.seller_preferences or None,
                ],
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )

            state_machine_data.listing_content = ListingContent(**result)

            highlights = result.get("highlights", [])
            highlights_text = "\n".join([f"  ✨ {h}" for h in highlights]) if highlights else "  None"

            tags = result.get("tags", [])
            tags_text = " ".join([f"`{t}`" for t in tags]) if tags else "None"

            specs = result.get("specifications", {})
            specs_text = ""
            if specs:
                specs_text = "\n\n**Specifications:**\n"
                specs_text += "| Spec | Value |\n|------|-------|\n"
                for key, value in specs.items():
                    specs_text += f"| {key} | {value} |\n"

            price = state_machine_data.price_estimate.recommended_price

            await adk.messages.create(
                task_id=state_machine_data.task_id,
                content=TextContent(
                    author="agent",
                    content=(
                        f"📋 **Dubizzle Listing Preview**\n\n"
                        f"---\n\n"
                        f"### {result.get('title', 'Untitled')}\n\n"
                        f"**Price: {price} AED**\n\n"
                        f"**Category:** {result.get('category', 'General')} > {result.get('subcategory', '')}\n\n"
                        f"**Description:**\n{result.get('description', 'No description')}\n\n"
                        f"**Key Highlights:**\n{highlights_text}\n\n"
                        f"**Tags:** {tags_text}"
                        f"{specs_text}\n\n"
                        f"---\n\n"
                        f"✅ **Ready to publish on Dubizzle!** Reply with:\n"
                        f"- **\"approve\"** to post this listing\n"
                        f"- **\"edit\"** followed by your changes\n"
                        f"- **\"price [amount]\"** to change the price\n"
                        f"- **\"cancel\"** to discard"
                    ),
                ),
            )

            state_machine_data.waiting_for_user_input = True
            return NovaSellState.AWAITING_APPROVAL

        except Exception as e:
            logger.error(f"Listing generation failed: {e}")
            state_machine_data.error_message = f"Listing generation failed: {str(e)}"
            await adk.messages.create(
                task_id=state_machine_data.task_id,
                content=TextContent(
                    author="agent",
                    content=f"❌ **Listing Generation Failed**\n\n{str(e)}",
                ),
            )
            return NovaSellState.FAILED