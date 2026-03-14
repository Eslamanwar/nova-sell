"""Pricing state — estimates market value for the detected item in the Dubai/UAE market."""
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
from project.models.listing import PriceEstimate

logger = make_logger(__name__)


class PricingWorkflow(StateWorkflow):
    """Estimate the market value of the detected item using Nova Pro reasoning."""

    @override
    async def execute(
        self,
        state_machine: StateMachine,
        state_machine_data: Optional[NovaSellData] = None,
    ) -> str:
        if state_machine_data is None:
            return NovaSellState.FAILED

        if state_machine_data.object_analysis is None:
            state_machine_data.error_message = "Object analysis required before pricing"
            return NovaSellState.FAILED

        logger.info("Starting price estimation with Nova Pro")

        await adk.messages.create(
            task_id=state_machine_data.task_id,
            content=TextContent(
                author="agent",
                content="💰 **Researching Dubai market prices...**\n\nAnalyzing comparable Dubizzle listings and market trends.",
            ),
        )

        try:
            result = await workflow.execute_activity(
                "estimate_price",
                args=[state_machine_data.object_analysis.model_dump(), None],
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )

            state_machine_data.price_estimate = PriceEstimate(**result)

            # Set pricing boundaries for negotiation
            recommended = result.get("recommended_price", 0)
            state_machine_data.min_acceptable_price = result.get("min_price", recommended * 0.8)

            # Build comparables table
            comparables = result.get("comparable_items", [])
            comparables_text = ""
            if comparables:
                comparables_text = "\n\n**Comparable Dubizzle Listings:**\n"
                comparables_text += "| Item | Price | Platform | Condition |\n"
                comparables_text += "|------|-------|----------|-----------|\n"
                for comp in comparables[:5]:
                    comparables_text += (
                        f"| {comp.get('title', 'N/A')[:40]} "
                        f"| {comp.get('price', 0)} AED "
                        f"| {comp.get('platform', 'N/A')} "
                        f"| {comp.get('condition', 'N/A')} |\n"
                    )

            trend_emoji = {"rising": "📈", "stable": "➡️", "declining": "📉"}.get(
                result.get("price_trend", "stable"), "➡️"
            )
            speed_emoji = {"fast": "🚀", "moderate": "⏱️", "slow": "🐢"}.get(
                result.get("sell_speed_estimate", "moderate"), "⏱️"
            )
            confidence = float(result.get("confidence", 0))
            if confidence > 1:
                confidence = confidence / 100.0

            await adk.messages.create(
                task_id=state_machine_data.task_id,
                content=TextContent(
                    author="agent",
                    content=(
                        f"💰 **Price Estimate Ready!**\n\n"
                        f"| Metric | Value |\n"
                        f"|--------|-------|\n"
                        f"| **Recommended Price** | **{result.get('recommended_price', 0)} AED** |\n"
                        f"| Price Range | {result.get('min_price', 0)} — {result.get('max_price', 0)} AED |\n"
                        f"| Original Retail | {result.get('original_retail_price', 0)} AED |\n"
                        f"| Depreciation | {result.get('depreciation_percentage', 0):.0f}% |\n"
                        f"| Market Trend | {trend_emoji} {result.get('price_trend', 'stable').title()} |\n"
                        f"| Expected Sell Speed | {speed_emoji} {result.get('sell_speed_estimate', 'moderate').title()} |\n"
                        f"| Confidence | {confidence:.0%} |\n\n"
                        f"**Reasoning:** {result.get('pricing_reasoning', 'N/A')}"
                        f"{comparables_text}\n\n"
                        f"📝 Now generating your Dubizzle listing..."
                    ),
                ),
            )

            return NovaSellState.LISTING_GENERATION

        except Exception as e:
            logger.error(f"Pricing failed: {e}")
            state_machine_data.error_message = f"Pricing failed: {str(e)}"
            await adk.messages.create(
                task_id=state_machine_data.task_id,
                content=TextContent(
                    author="agent",
                    content=f"❌ **Pricing Failed**\n\n{str(e)}",
                ),
            )
            return NovaSellState.FAILED