"""Publishing state — posts the listing to Dubizzle using Nova Act browser automation."""
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
from project.models.listing import PostingResult

logger = make_logger(__name__)


class PublishingWorkflow(StateWorkflow):
    """Automate posting the listing to Dubizzle using Nova Act browser automation."""

    @override
    async def execute(
        self,
        state_machine: StateMachine,
        state_machine_data: Optional[NovaSellData] = None,
    ) -> str:
        if state_machine_data is None:
            return NovaSellState.FAILED

        if state_machine_data.listing_content is None or state_machine_data.price_estimate is None:
            state_machine_data.error_message = "Listing content and price required for publishing"
            return NovaSellState.FAILED

        logger.info(f"Publishing listing to {state_machine_data.target_marketplace}")

        _mp = state_machine_data.target_marketplace
        marketplace_label = (
            "Shozon" if _mp == "shozon"
            else "Facebook Marketplace" if _mp == "facebook"
            else "Dubizzle"
        )

        await adk.messages.create(
            task_id=state_machine_data.task_id,
            content=TextContent(
                author="agent",
                content=(
                    f"🚀 **Publishing to {marketplace_label}...**\n\n"
                    f"Using Nova Act AI browser automation to post your listing.\n"
                    f"Anti-ban measures active (human-like delays, session reuse)."
                ),
            ),
        )

        try:
            image_urls = []
            if state_machine_data.image_url:
                image_urls.append(state_machine_data.image_url)

            # Nova Act browser automation (streams screenshots to chat)
            result = await workflow.execute_activity(
                "post_listing_to_marketplace",
                args=[
                    state_machine_data.listing_content.model_dump(),
                    state_machine_data.price_estimate.recommended_price,
                    image_urls,
                    state_machine_data.target_marketplace,
                    state_machine_data.task_id,
                ],
                start_to_close_timeout=timedelta(minutes=60),
                heartbeat_timeout=timedelta(minutes=2),
                retry_policy=RetryPolicy(maximum_attempts=1),
            )

            posting_result = PostingResult(**result)
            state_machine_data.posting_results.append(posting_result)

            if posting_result.status in ["posted", "mock_posted"]:
                listing_url = posting_result.listing_url or "URL pending"

                await adk.messages.create(
                    task_id=state_machine_data.task_id,
                    content=TextContent(
                        author="agent",
                        content=(
                            f"🎉 **Listing Published on {marketplace_label}!**\n\n"
                            f"| Detail | Value |\n"
                            f"|--------|-------|\n"
                            f"| **Marketplace** | {marketplace_label} |\n"
                            f"| **Status** | ✅ {posting_result.status.replace('_', ' ').title()} |\n"
                            f"| **Listing URL** | {listing_url} |\n\n"
                            f"📬 I'm now monitoring for buyer inquiries.\n"
                            f"I'll handle messages, negotiate prices, and schedule pickups automatically.\n\n"
                            f"**Commands:**\n"
                            f"- **\"status\"** — check listing status\n"
                            f"- **\"sold\"** — mark as sold\n"
                            f"- **\"remove\"** — take down the listing"
                        ),
                    ),
                )

                state_machine_data.result = {
                    "status": "published",
                    "marketplace": posting_result.marketplace,
                    "listing_url": posting_result.listing_url,
                    "title": state_machine_data.listing_content.title,
                    "price": state_machine_data.price_estimate.recommended_price,
                }

                return NovaSellState.ACTIVE_LISTING

            else:
                await adk.messages.create(
                    task_id=state_machine_data.task_id,
                    content=TextContent(
                        author="agent",
                        content=(
                            f"⚠️ **Posting Issue**\n\n"
                            f"Status: {posting_result.status}\n"
                            f"Error: {posting_result.error_message or 'Unknown'}\n\n"
                            f"Reply **\"retry\"** or **\"cancel\"**"
                        ),
                    ),
                )

                state_machine_data.waiting_for_user_input = True
                await workflow.wait_condition(
                    lambda: not state_machine_data.waiting_for_user_input
                )

                last_input = ""
                if state_machine_data.conversation_history:
                    last_input = (state_machine_data.conversation_history[-1].get("content", "") or "").strip().lower()

                if last_input == "cancel":
                    return NovaSellState.CANCELLED
                return NovaSellState.PUBLISHING

        except Exception as e:
            logger.error(f"Publishing failed: {e}")
            state_machine_data.error_message = f"Publishing failed: {str(e)}"

            await adk.messages.create(
                task_id=state_machine_data.task_id,
                content=TextContent(
                    author="agent",
                    content=f"❌ **Publishing Failed**\n\n{str(e)}\n\nReply **\"retry\"** or **\"cancel\"**",
                ),
            )

            state_machine_data.waiting_for_user_input = True
            await workflow.wait_condition(
                lambda: not state_machine_data.waiting_for_user_input
            )

            last_input = ""
            if state_machine_data.conversation_history:
                last_input = (state_machine_data.conversation_history[-1].get("content", "") or "").strip().lower()

            if last_input == "cancel":
                return NovaSellState.CANCELLED
            return NovaSellState.PUBLISHING