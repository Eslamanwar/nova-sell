"""Active Listing state — manages a live Dubizzle listing.

Handles:
- Buyer chat messages (conversation agent)
- Price negotiations (negotiation agent)
- Voice calls (Nova Sonic)
- Pickup scheduling
- Seller commands (status, sold, remove)
- HITL escalations
"""
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

from project.state_machines.novasell_agent import (
    NovaSellData,
    NovaSellState,
)
from project.models.conversation import (
    ChatMessage,
    ChatResponse,
    VoiceSession,
    ScheduleResult,
    NegotiationRound,
    NegotiationStatus,
    ConversationChannel,
)

logger = make_logger(__name__)


class ActiveListingWorkflow(StateWorkflow):
    """Manage an active Dubizzle listing — handle buyer chats, negotiations, calls, scheduling."""

    @override
    async def execute(
        self,
        state_machine: StateMachine,
        state_machine_data: Optional[NovaSellData] = None,
    ) -> str:
        if state_machine_data is None:
            return NovaSellState.FAILED

        logger.info("Active listing management loop")

        # Check for incoming events
        if state_machine_data.incoming_chat_message:
            return await self._handle_chat(state_machine_data)

        if state_machine_data.incoming_buyer_offer is not None:
            return await self._handle_negotiation(state_machine_data)

        if state_machine_data.incoming_voice_session_id:
            return await self._handle_voice(state_machine_data)

        if state_machine_data.incoming_schedule_request:
            return await self._handle_scheduling(state_machine_data)

        # Check for seller commands
        if state_machine_data.conversation_history:
            latest_message = ""
            for msg in reversed(state_machine_data.conversation_history):
                if msg.get("role") == "user":
                    latest_message = msg.get("content", "").strip().lower()
                    break

            if latest_message:
                if latest_message in ["sold", "mark sold", "item sold"]:
                    await adk.messages.create(
                        task_id=state_machine_data.task_id,
                        content=TextContent(
                            author="agent",
                            content=(
                                "🎉 **Congratulations on the sale!**\n\n"
                                "I've marked this listing as sold on Dubizzle."
                            ),
                        ),
                    )
                    state_machine_data.result["final_status"] = "sold"
                    return NovaSellState.SOLD

                elif latest_message in ["remove", "delete", "take down", "delist"]:
                    await adk.messages.create(
                        task_id=state_machine_data.task_id,
                        content=TextContent(
                            author="agent",
                            content="🗑️ **Listing removed.** The Dubizzle listing has been taken down.",
                        ),
                    )
                    return NovaSellState.COMPLETED

                elif latest_message in ["status", "check", "update"]:
                    await self._show_status(state_machine_data)

        # Stay in active listing state, block until user/event input arrives
        state_machine_data.waiting_for_user_input = True
        await workflow.wait_condition(
            lambda: not state_machine_data.waiting_for_user_input
        )

        return NovaSellState.ACTIVE_LISTING

    async def _show_status(self, data: NovaSellData) -> None:
        """Show current listing status to the seller."""
        posting = data.posting_results[-1] if data.posting_results else None
        chat_count = len(data.chat_history)
        voice_count = len(data.voice_sessions)
        negotiation_count = len(data.negotiation_contexts)

        await adk.messages.create(
            task_id=data.task_id,
            content=TextContent(
                author="agent",
                content=(
                    f"📊 **Dubizzle Listing Status**\n\n"
                    f"| Metric | Value |\n"
                    f"|--------|-------|\n"
                    f"| **Title** | {data.listing_content.title if data.listing_content else 'N/A'} |\n"
                    f"| **Price** | {data.price_estimate.recommended_price if data.price_estimate else 0} AED |\n"
                    f"| **Marketplace** | {posting.marketplace.title() if posting else 'Dubizzle'} |\n"
                    f"| **Status** | {posting.status if posting else 'N/A'} |\n"
                    f"| **Buyer Chats** | {chat_count} messages |\n"
                    f"| **Voice Calls** | {voice_count} sessions |\n"
                    f"| **Negotiations** | {negotiation_count} |\n"
                    f"| **Pickups Scheduled** | {len(data.schedule_results)} |\n"
                ),
            ),
        )

    async def _handle_chat(self, data: NovaSellData) -> str:
        """Handle an incoming buyer chat message via the conversation agent."""
        customer_message = data.incoming_chat_message
        data.incoming_chat_message = None

        logger.info(f"Handling buyer chat: {customer_message[:100]}...")

        # Add to chat history
        data.chat_history.append(ChatMessage(
            role="buyer",
            content=customer_message,
            timestamp=str(workflow.now()),
            channel=ConversationChannel.DUBIZZLE_CHAT,
        ))

        # Build listing context
        listing_context = {}
        if data.listing_content:
            listing_context = {
                "title": data.listing_content.title,
                "description": data.listing_content.description,
                "condition": data.object_analysis.condition_description if data.object_analysis else "Unknown",
                "location": data.seller_preferences.get("location", "Dubai"),
            }
        if data.price_estimate:
            listing_context["price"] = data.price_estimate.recommended_price

        pricing_boundaries = {
            "listed_price": data.price_estimate.recommended_price if data.price_estimate else 0,
            "min_price": data.min_acceptable_price,
            "max_discount_pct": data.max_discount_percentage,
        }

        try:
            result = await workflow.execute_activity(
                "handle_chat_message",
                args=[
                    customer_message,
                    listing_context,
                    [msg.model_dump() for msg in data.chat_history[-10:]],
                    pricing_boundaries,
                ],
                start_to_close_timeout=timedelta(minutes=3),
                retry_policy=RetryPolicy(maximum_attempts=2),
            )

            chat_response = ChatResponse(**result)
            data.chat_responses.append(chat_response)

            # Add agent reply to history
            data.chat_history.append(ChatMessage(
                role="agent",
                content=chat_response.reply,
                timestamp=str(workflow.now()),
                channel=ConversationChannel.DUBIZZLE_CHAT,
            ))

            # Build notification for seller
            escalation_note = ""
            if chat_response.escalate_to_seller:
                escalation_note = f"\n\n⚠️ **Escalation:** {chat_response.escalation_reason}"

            negotiation_note = ""
            if chat_response.negotiation_status == "agreed" and chat_response.agreed_price:
                negotiation_note = f"\n\n💰 **Price agreed: {chat_response.agreed_price} AED**"

            counter_note = ""
            if chat_response.counter_offer:
                counter_note = f"\n\n💬 **Counter offer: {chat_response.counter_offer} AED**"

            await adk.messages.create(
                task_id=data.task_id,
                content=TextContent(
                    author="agent",
                    content=(
                        f"💬 **Buyer Chat Update**\n\n"
                        f"**Buyer:** {customer_message}\n\n"
                        f"**My Reply:** {chat_response.reply}"
                        f"{negotiation_note}{counter_note}{escalation_note}"
                    ),
                ),
            )

            # Schedule meeting if requested
            if chat_response.schedule_meeting:
                data.incoming_schedule_request = str(chat_response.meeting_details)
                return NovaSellState.ACTIVE_LISTING

            # Automate response on Dubizzle
            if data.posting_results:
                posting = data.posting_results[-1]
                if posting.listing_url:
                    try:
                        await workflow.execute_activity(
                            "respond_to_marketplace_chat",
                            args=[posting.marketplace, posting.listing_url, chat_response.reply],
                            start_to_close_timeout=timedelta(minutes=5),
                            retry_policy=RetryPolicy(maximum_attempts=1),
                        )
                    except Exception as e:
                        logger.warning(f"Failed to automate chat response on Dubizzle: {e}")

        except Exception as e:
            logger.error(f"Chat handling failed: {e}")
            await adk.messages.create(
                task_id=data.task_id,
                content=TextContent(
                    author="agent",
                    content=f"⚠️ Failed to process buyer message: {str(e)}",
                ),
            )

        data.waiting_for_user_input = True
        return NovaSellState.ACTIVE_LISTING

    async def _handle_negotiation(self, data: NovaSellData) -> str:
        """Handle a price negotiation round using the negotiation agent."""
        buyer_offer = data.incoming_buyer_offer
        data.incoming_buyer_offer = None

        if buyer_offer is None:
            return NovaSellState.ACTIVE_LISTING

        logger.info(f"Handling negotiation: buyer offers {buyer_offer} AED")

        listing_context = {
            "title": data.listing_content.title if data.listing_content else "Unknown",
        }

        pricing_boundaries = {
            "listed_price": data.price_estimate.recommended_price if data.price_estimate else 0,
            "min_price": data.min_acceptable_price,
            "max_discount_pct": data.max_discount_percentage,
        }

        # Build negotiation history
        neg_history = []
        for ctx in data.negotiation_contexts:
            for r in ctx.rounds:
                neg_history.append(r.model_dump())

        try:
            result = await workflow.execute_activity(
                "negotiate_price",
                args=[buyer_offer, listing_context, pricing_boundaries, neg_history],
                start_to_close_timeout=timedelta(minutes=3),
                retry_policy=RetryPolicy(maximum_attempts=2),
            )

            decision = result.get("decision", "decline")
            counter = result.get("counter_offer")
            response = result.get("response_to_buyer", "")

            # Record negotiation round
            round_data = NegotiationRound(
                buyer_offer=buyer_offer,
                agent_counter=counter,
                status=(
                    NegotiationStatus.AGREED if decision == "accept"
                    else NegotiationStatus.DECLINED if decision == "decline"
                    else NegotiationStatus.ESCALATED if decision == "escalate"
                    else NegotiationStatus.IN_PROGRESS
                ),
                reasoning=result.get("reasoning", ""),
            )

            # Notify seller
            decision_emoji = {
                "accept": "✅", "counter": "💬",
                "decline": "❌", "escalate": "⚠️",
            }.get(decision, "❓")

            await adk.messages.create(
                task_id=data.task_id,
                content=TextContent(
                    author="agent",
                    content=(
                        f"{decision_emoji} **Negotiation Update**\n\n"
                        f"Buyer offered: **{buyer_offer} AED**\n"
                        f"Decision: **{decision.title()}**\n"
                        f"{'Counter: **' + str(counter) + ' AED**' if counter else ''}\n\n"
                        f"Response: {response}\n\n"
                        f"Reasoning: {result.get('reasoning', 'N/A')}"
                    ),
                ),
            )

            if result.get("escalation_needed"):
                await adk.messages.create(
                    task_id=data.task_id,
                    content=TextContent(
                        author="agent",
                        content=(
                            f"⚠️ **Human Review Needed**\n\n"
                            f"Reason: {result.get('escalation_reason', 'Negotiation requires approval')}\n"
                            f"Reply **\"approve\"** to accept or **\"decline\"** to reject."
                        ),
                    ),
                )

        except Exception as e:
            logger.error(f"Negotiation failed: {e}")

        data.waiting_for_user_input = True
        return NovaSellState.ACTIVE_LISTING

    async def _handle_voice(self, data: NovaSellData) -> str:
        """Handle an incoming voice call using Nova Sonic."""
        session_id = data.incoming_voice_session_id
        data.incoming_voice_session_id = None

        logger.info(f"Handling voice session: {session_id}")

        listing_context = {}
        if data.listing_content:
            listing_context = {
                "title": data.listing_content.title,
                "price": data.price_estimate.recommended_price if data.price_estimate else 0,
                "condition": data.object_analysis.condition_description if data.object_analysis else "Unknown",
                "location": data.seller_preferences.get("location", "Dubai"),
                "min_price": data.min_acceptable_price,
                "max_discount_pct": data.max_discount_percentage,
            }

        try:
            result = await workflow.execute_activity(
                "handle_voice_session",
                args=[session_id, "", listing_context, []],
                start_to_close_timeout=timedelta(minutes=10),
                retry_policy=RetryPolicy(maximum_attempts=1),
            )

            voice_session = VoiceSession(
                session_id=session_id,
                status=result.get("status", "completed"),
                summary=result.get("response_text", ""),
            )
            data.voice_sessions.append(voice_session)

            await adk.messages.create(
                task_id=data.task_id,
                content=TextContent(
                    author="agent",
                    content=(
                        f"📞 **Voice Call Summary**\n\n"
                        f"Session: `{session_id}`\n"
                        f"Status: {voice_session.status}\n"
                        f"Summary: {voice_session.summary}"
                    ),
                ),
            )

        except Exception as e:
            logger.error(f"Voice session failed: {e}")

        data.waiting_for_user_input = True
        return NovaSellState.ACTIVE_LISTING

    async def _handle_scheduling(self, data: NovaSellData) -> str:
        """Handle a pickup/viewing scheduling request."""
        request = data.incoming_schedule_request
        data.incoming_schedule_request = None

        logger.info(f"Handling scheduling request: {request[:100]}...")

        listing_context = {}
        if data.listing_content:
            listing_context = {
                "title": data.listing_content.title,
                "location": data.seller_preferences.get("location", "Dubai"),
            }

        seller_availability = data.seller_preferences.get("availability", [])

        try:
            result = await workflow.execute_activity(
                "handle_scheduling",
                args=[request, seller_availability, listing_context],
                start_to_close_timeout=timedelta(minutes=3),
                retry_policy=RetryPolicy(maximum_attempts=2),
            )

            schedule_result = ScheduleResult(**result)
            data.schedule_results.append(schedule_result)

            await adk.messages.create(
                task_id=data.task_id,
                content=TextContent(
                    author="agent",
                    content=(
                        f"🗓️ **Pickup/Viewing Scheduled**\n\n"
                        f"{schedule_result.confirmation_message}"
                    ),
                ),
            )

        except Exception as e:
            logger.error(f"Scheduling failed: {e}")

        data.waiting_for_user_input = True
        return NovaSellState.ACTIVE_LISTING