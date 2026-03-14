"""Terminal state workflows for NovaSell Dubizzle agent."""
from __future__ import annotations

from typing import Optional, override

from agentex.lib import adk
from agentex.lib.sdk.state_machine.state_machine import StateMachine
from agentex.lib.sdk.state_machine.state_workflow import StateWorkflow
from agentex.lib.utils.logging import make_logger
from agentex.types.text_content import TextContent

from project.state_machines.novasell_agent import NovaSellData, NovaSellState

logger = make_logger(__name__)


class SoldWorkflow(StateWorkflow):
    """Terminal state: item has been sold on Dubizzle."""

    @override
    async def execute(
        self,
        state_machine: StateMachine,
        state_machine_data: Optional[NovaSellData] = None,
    ) -> str:
        if state_machine_data and state_machine_data.task_id:
            title = state_machine_data.listing_content.title if state_machine_data.listing_content else ""
            price = f"{state_machine_data.price_estimate.recommended_price} AED" if state_machine_data.price_estimate else ""

            await adk.messages.create(
                task_id=state_machine_data.task_id,
                content=TextContent(
                    author="agent",
                    content=(
                        f"### 🎊 Item Sold on Dubizzle!\n\n"
                        f"**{title}** has been sold for **{price}**.\n\n"
                        f"Congratulations! The listing has been marked as sold."
                    ),
                ),
                trace_id=state_machine_data.task_id,
            )

        return NovaSellState.SOLD


class CompletedWorkflow(StateWorkflow):
    """Terminal state: workflow completed (listing removed or finished)."""

    @override
    async def execute(
        self,
        state_machine: StateMachine,
        state_machine_data: Optional[NovaSellData] = None,
    ) -> str:
        if state_machine_data and state_machine_data.task_id:
            await adk.messages.create(
                task_id=state_machine_data.task_id,
                content=TextContent(
                    author="agent",
                    content="### ✅ Workflow Complete\n\nThe NovaSell Dubizzle workflow has finished.",
                ),
                trace_id=state_machine_data.task_id,
            )

        return NovaSellState.COMPLETED


class FailedWorkflow(StateWorkflow):
    """Terminal state: workflow failed with an error."""

    @override
    async def execute(
        self,
        state_machine: StateMachine,
        state_machine_data: Optional[NovaSellData] = None,
    ) -> str:
        if state_machine_data:
            logger.error(f"NovaSell workflow failed: {state_machine_data.error_message}")

            if state_machine_data.task_id:
                await adk.messages.create(
                    task_id=state_machine_data.task_id,
                    content=TextContent(
                        author="agent",
                        content=(
                            f"### ❌ Workflow Failed\n\n"
                            f"**Error:** {state_machine_data.error_message}\n\n"
                            f"Please try again or contact support."
                        ),
                    ),
                    trace_id=state_machine_data.task_id,
                )

        return NovaSellState.FAILED


class CancelledWorkflow(StateWorkflow):
    """Terminal state: workflow cancelled by user."""

    @override
    async def execute(
        self,
        state_machine: StateMachine,
        state_machine_data: Optional[NovaSellData] = None,
    ) -> str:
        if state_machine_data and state_machine_data.task_id:
            await adk.messages.create(
                task_id=state_machine_data.task_id,
                content=TextContent(
                    author="agent",
                    content="### 🚫 Listing Cancelled\n\nThe Dubizzle listing has been cancelled.",
                ),
                trace_id=state_machine_data.task_id,
            )

        return NovaSellState.CANCELLED
