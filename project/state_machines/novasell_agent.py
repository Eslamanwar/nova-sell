"""State machine for NovaSell autonomous Dubizzle selling agent workflow.

Defines:
- NovaSellState: All possible workflow states
- NovaSellData: Complete workflow state data model
- NovaSellStateMachine: State machine with terminal condition
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional, override

from pydantic import BaseModel, Field

from agentex.lib.sdk.state_machine import StateMachine
from agentex.types.span import Span

from project.models.listing import (
    ListingContent,
    ObjectAnalysis,
    PostingResult,
    PriceEstimate,
)
from project.models.conversation import (
    ChatMessage,
    ChatResponse,
    NegotiationContext,
    ScheduleResult,
    VoiceSession,
    HITLRequest,
    BuyerProfile,
)


# ─────────────────────────────────────────────────────────────────────────────
# States
# ─────────────────────────────────────────────────────────────────────────────


class NovaSellState(str, Enum):
    """States for the NovaSell Dubizzle selling agent workflow."""

    # Initial state — waiting for user to upload an image
    WAITING_FOR_IMAGE = "waiting_for_image"

    # Analysis pipeline
    OBJECT_DETECTION = "object_detection"
    PRICING = "pricing"
    LISTING_GENERATION = "listing_generation"

    # User review
    AWAITING_APPROVAL = "awaiting_approval"

    # Publishing to Dubizzle
    PUBLISHING = "publishing"

    # Post-publish — active listing management
    ACTIVE_LISTING = "active_listing"

    # Sub-states for active listing
    HANDLING_CHAT = "handling_chat"
    HANDLING_VOICE = "handling_voice"
    NEGOTIATING = "negotiating"
    SCHEDULING = "scheduling"

    # Terminal states
    SOLD = "sold"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ─────────────────────────────────────────────────────────────────────────────
# Workflow Data
# ─────────────────────────────────────────────────────────────────────────────


class NovaSellData(BaseModel):
    """Complete state data for the NovaSell Dubizzle selling workflow.

    Tracks the entire lifecycle: image upload → detection → pricing →
    listing generation → approval → publishing → active management →
    buyer conversations → negotiation → scheduling → sold.
    """

    # ── Input ────────────────────────────────────────────────────────────
    image_base64: str = ""  # Temporary — cleared after saving to disk
    image_url: str = ""
    image_file_path: str = ""  # Local disk path to saved image
    image_s3_key: str = ""
    user_hints: str = ""  # Optional hints from user about the item
    target_marketplace: str = "shozon"
    seller_preferences: Dict[str, Any] = Field(default_factory=dict)

    # ── Analysis Results ─────────────────────────────────────────────────
    object_analysis: Optional[ObjectAnalysis] = None
    price_estimate: Optional[PriceEstimate] = None
    listing_content: Optional[ListingContent] = None

    # ── Publishing ───────────────────────────────────────────────────────
    posting_results: List[PostingResult] = Field(default_factory=list)
    approved_by_user: bool = False
    user_edits: Dict[str, Any] = Field(default_factory=dict)

    # ── Active Listing Management ────────────────────────────────────────
    chat_history: List[ChatMessage] = Field(default_factory=list)
    chat_responses: List[ChatResponse] = Field(default_factory=list)
    voice_sessions: List[VoiceSession] = Field(default_factory=list)
    schedule_results: List[ScheduleResult] = Field(default_factory=list)
    negotiation_contexts: List[NegotiationContext] = Field(default_factory=list)
    buyer_profiles: List[BuyerProfile] = Field(default_factory=list)

    # ── HITL ─────────────────────────────────────────────────────────────
    hitl_requests: List[HITLRequest] = Field(default_factory=list)
    hitl_pending: bool = False

    # ── Pricing Boundaries ───────────────────────────────────────────────
    min_acceptable_price: float = 0.0
    max_discount_percentage: float = 15.0

    # ── Result ───────────────────────────────────────────────────────────
    result: Dict[str, Any] = Field(default_factory=dict)
    error_message: str = ""

    # ── Workflow Metadata ────────────────────────────────────────────────
    task_id: Optional[str] = None
    current_span: Optional[Span] = None
    waiting_for_user_input: bool = False
    conversation_history: List[Dict[str, Any]] = Field(default_factory=list)

    # ── Incoming Event Data ──────────────────────────────────────────────
    incoming_chat_message: Optional[str] = None
    incoming_voice_session_id: Optional[str] = None
    incoming_schedule_request: Optional[str] = None
    incoming_buyer_offer: Optional[float] = None


# ─────────────────────────────────────────────────────────────────────────────
# State Machine
# ─────────────────────────────────────────────────────────────────────────────


class NovaSellStateMachine(StateMachine[NovaSellData]):
    """State machine for orchestrating the NovaSell Dubizzle selling workflow.

    Terminal states: SOLD, COMPLETED, FAILED, CANCELLED
    """

    @override
    async def terminal_condition(self) -> bool:
        """Check if the state machine has reached a terminal state."""
        return self.get_current_state() in [
            NovaSellState.SOLD,
            NovaSellState.COMPLETED,
            NovaSellState.FAILED,
            NovaSellState.CANCELLED,
        ]