"""Conversation domain models — chat messages, negotiation, voice sessions, scheduling."""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────────────────────


class NegotiationStatus(str, Enum):
    """Status of a price negotiation."""
    NONE = "none"
    IN_PROGRESS = "in_progress"
    AGREED = "agreed"
    DECLINED = "declined"
    ESCALATED = "escalated"


class ConversationChannel(str, Enum):
    """Communication channel for buyer conversations."""
    DUBIZZLE_CHAT = "dubizzle_chat"
    SMS = "sms"
    WHATSAPP = "whatsapp"
    PHONE_CALL = "phone_call"


class EscalationReason(str, Enum):
    """Reasons for escalating to a human."""
    HIGH_VALUE_NEGOTIATION = "high_value_negotiation"
    SUSPICIOUS_BUYER = "suspicious_buyer"
    COMPLEX_QUESTION = "complex_question"
    PAYMENT_CONFIRMATION = "payment_confirmation"
    CALL_ESCALATION = "call_escalation"
    CAPTCHA_REQUIRED = "captcha_required"
    BUYER_REQUEST = "buyer_request"


class HITLAction(str, Enum):
    """Human-in-the-loop action types."""
    SOLVE_CAPTCHA = "solve_captcha"
    APPROVE_NEGOTIATION = "approve_negotiation"
    REVIEW_BUYER = "review_buyer"
    CONFIRM_PAYMENT = "confirm_payment"
    TAKE_OVER_CALL = "take_over_call"
    APPROVE_LISTING = "approve_listing"
    MANUAL_OVERRIDE = "manual_override"


# ─────────────────────────────────────────────────────────────────────────────
# Chat Models
# ─────────────────────────────────────────────────────────────────────────────


class ChatMessage(BaseModel):
    """A single chat message in a buyer conversation."""
    role: str = ""  # buyer | agent | seller
    content: str = ""
    timestamp: str = ""
    channel: ConversationChannel = ConversationChannel.DUBIZZLE_CHAT
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ChatResponse(BaseModel):
    """AI-generated response to a buyer message."""
    reply: str = ""
    suggested_actions: List[str] = Field(default_factory=list)
    negotiation_status: NegotiationStatus = NegotiationStatus.NONE
    agreed_price: Optional[float] = None
    counter_offer: Optional[float] = None
    escalate_to_seller: bool = False
    escalation_reason: Optional[str] = None
    schedule_meeting: bool = False
    meeting_details: Dict[str, Any] = Field(default_factory=dict)
    sentiment: str = ""  # positive | neutral | negative
    buyer_intent: str = ""  # inquiry | negotiation | scheduling | complaint


# ─────────────────────────────────────────────────────────────────────────────
# Negotiation Models
# ─────────────────────────────────────────────────────────────────────────────


class NegotiationRound(BaseModel):
    """A single round in a price negotiation."""
    round_number: int = 0
    buyer_offer: Optional[float] = None
    agent_counter: Optional[float] = None
    status: NegotiationStatus = NegotiationStatus.IN_PROGRESS
    reasoning: str = ""
    timestamp: str = ""


class NegotiationContext(BaseModel):
    """Full context for an ongoing negotiation."""
    listing_id: str = ""
    buyer_id: str = ""
    listed_price: float = 0.0
    min_acceptable_price: float = 0.0
    max_discount_percentage: float = 15.0
    rounds: List[NegotiationRound] = Field(default_factory=list)
    current_status: NegotiationStatus = NegotiationStatus.NONE
    final_agreed_price: Optional[float] = None
    escalated: bool = False


# ─────────────────────────────────────────────────────────────────────────────
# Voice / Call Models
# ─────────────────────────────────────────────────────────────────────────────


class VoiceSession(BaseModel):
    """Voice conversation session data powered by Nova Sonic."""
    session_id: str = ""
    status: str = ""  # active | completed | failed | escalated
    transcript: List[Dict[str, str]] = Field(default_factory=list)
    summary: str = ""
    actions_taken: List[str] = Field(default_factory=list)
    duration_seconds: float = 0.0
    caller_phone: str = ""
    listing_id: str = ""
    negotiation_result: Optional[NegotiationRound] = None
    scheduled_pickup: Optional[Dict[str, Any]] = None


# ─────────────────────────────────────────────────────────────────────────────
# Scheduling Models
# ─────────────────────────────────────────────────────────────────────────────


class ScheduleResult(BaseModel):
    """Result of scheduling a pickup or viewing."""
    action: str = ""  # schedule | reschedule | cancel | check_availability
    proposed_times: List[Dict[str, Any]] = Field(default_factory=list)
    confirmed_time: Optional[str] = None
    location: str = ""
    confirmation_message: str = ""
    calendar_event: Dict[str, Any] = Field(default_factory=dict)
    buyer_name: str = ""
    buyer_contact: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# HITL Models
# ─────────────────────────────────────────────────────────────────────────────


class HITLRequest(BaseModel):
    """A request for human intervention."""
    request_id: str = ""
    action: HITLAction = HITLAction.MANUAL_OVERRIDE
    reason: str = ""
    context: Dict[str, Any] = Field(default_factory=dict)
    status: str = "pending"  # pending | in_progress | completed | cancelled
    created_at: str = ""
    resolved_at: Optional[str] = None
    resolution: Optional[str] = None
    assigned_to: Optional[str] = None


class BuyerProfile(BaseModel):
    """Profile of a buyer interacting with listings."""
    buyer_id: str = ""
    name: str = ""
    phone: str = ""
    email: str = ""
    channel: ConversationChannel = ConversationChannel.DUBIZZLE_CHAT
    conversation_history: List[ChatMessage] = Field(default_factory=list)
    negotiation_history: List[NegotiationRound] = Field(default_factory=list)
    trust_score: float = 1.0  # 0.0 = suspicious, 1.0 = trusted
    total_interactions: int = 0
    metadata: Dict[str, Any] = Field(default_factory=dict)