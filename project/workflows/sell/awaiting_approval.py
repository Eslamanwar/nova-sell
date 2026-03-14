"""Awaiting Approval state — waits for user to approve, edit, or cancel the Dubizzle listing."""
from __future__ import annotations

import re
from typing import Optional, override

from temporalio import workflow

from agentex.lib.sdk.state_machine import StateMachine
from agentex.lib.sdk.state_machine.state_workflow import StateWorkflow
from agentex.lib.utils.logging import make_logger
from agentex.lib import adk
from agentex.types.text_content import TextContent

from project.state_machines.novasell_agent import NovaSellData, NovaSellState

logger = make_logger(__name__)


class AwaitingApprovalWorkflow(StateWorkflow):
    """Wait for user to approve the generated Dubizzle listing before publishing."""

    @override
    async def execute(
        self,
        state_machine: StateMachine,
        state_machine_data: Optional[NovaSellData] = None,
    ) -> str:
        if state_machine_data is None:
            return NovaSellState.FAILED

        logger.info("Awaiting user approval for Dubizzle listing")

        state_machine_data.waiting_for_user_input = True
        await workflow.wait_condition(
            lambda: not state_machine_data.waiting_for_user_input
        )

        # Get the latest user message
        latest_message = ""
        for msg in reversed(state_machine_data.conversation_history):
            if msg.get("role") == "user":
                latest_message = msg.get("content", "").strip().lower()
                break

        if not latest_message:
            return NovaSellState.AWAITING_APPROVAL

        # Process user decision
        if latest_message in ["approve", "yes", "post", "publish", "go", "ok", "confirm"]:
            state_machine_data.approved_by_user = True
            await adk.messages.create(
                task_id=state_machine_data.task_id,
                content=TextContent(
                    author="agent",
                    content=(
                        "### ✅ Listing Approved!\n\n"
                        f"Publishing to **Dubizzle Dubai**..."
                    ),
                ),
            )
            return NovaSellState.PUBLISHING

        elif latest_message in ["cancel", "no", "discard", "stop", "abort"]:
            await adk.messages.create(
                task_id=state_machine_data.task_id,
                content=TextContent(
                    author="agent",
                    content="### 🚫 Listing Cancelled\n\nYour listing has been discarded. Upload a new photo to start again.",
                ),
            )
            return NovaSellState.CANCELLED

        elif latest_message.startswith("edit"):
            edit_instructions = latest_message.replace("edit", "").strip()
            state_machine_data.user_edits = {"instructions": edit_instructions}
            await adk.messages.create(
                task_id=state_machine_data.task_id,
                content=TextContent(
                    author="agent",
                    content=(
                        f"**Noted!** Updating the listing based on your feedback.\n\n"
                        f"Changes: {edit_instructions or 'Please specify what to change.'}\n\n"
                        f"Regenerating listing..."
                    ),
                ),
            )
            if state_machine_data.seller_preferences is None:
                state_machine_data.seller_preferences = {}
            state_machine_data.seller_preferences["user_edits"] = edit_instructions
            return NovaSellState.LISTING_GENERATION

        elif latest_message.startswith("price"):
            price_match = re.search(r"[\d.]+", latest_message)
            if price_match:
                new_price = float(price_match.group())
                if state_machine_data.price_estimate:
                    state_machine_data.price_estimate.recommended_price = new_price
                await adk.messages.create(
                    task_id=state_machine_data.task_id,
                    content=TextContent(
                        author="agent",
                        content=f"**Price updated to {new_price} AED**\n\nReply **\"approve\"** to publish or make more changes.",
                    ),
                )
            else:
                await adk.messages.create(
                    task_id=state_machine_data.task_id,
                    content=TextContent(
                        author="agent",
                        content='Please specify the new price, e.g., **"price 299"**',
                    ),
                )
            return NovaSellState.AWAITING_APPROVAL

        else:
            await adk.messages.create(
                task_id=state_machine_data.task_id,
                content=TextContent(
                    author="agent",
                    content=(
                        "I didn't understand that. Please reply with:\n"
                        "- **\"approve\"** — publish to Dubizzle\n"
                        "- **\"edit [changes]\"** — modify the listing\n"
                        "- **\"price [amount]\"** — change the price\n"
                        "- **\"cancel\"** — discard the listing"
                    ),
                ),
            )
            return NovaSellState.AWAITING_APPROVAL
