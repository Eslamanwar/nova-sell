"""Memory store — manages persistent state for listings, conversations, and negotiations.

Provides an in-memory implementation with optional Redis/Postgres backends.
All agent state is tracked here for conversation continuity and audit trails.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from project.models.listing import Listing, ListingStatus
from project.models.conversation import (
    BuyerProfile,
    ChatMessage,
    NegotiationContext,
    NegotiationRound,
    ScheduleResult,
    VoiceSession,
)

logger = logging.getLogger(__name__)


class MemoryStore:
    """In-memory state store for the NovaSell agent.

    Tracks:
    - Active listings and their lifecycle
    - Buyer conversations per listing
    - Negotiation history
    - Call transcripts
    - Scheduled pickups
    - HITL request queue

    In production, this would be backed by Redis (hot data) + Postgres (persistent).
    """

    def __init__(self):
        # Listings indexed by listing_id
        self._listings: Dict[str, Listing] = {}

        # Buyer profiles indexed by buyer_id
        self._buyers: Dict[str, BuyerProfile] = {}

        # Conversations: listing_id -> buyer_id -> list of messages
        self._conversations: Dict[str, Dict[str, List[ChatMessage]]] = {}

        # Negotiations: listing_id -> buyer_id -> NegotiationContext
        self._negotiations: Dict[str, Dict[str, NegotiationContext]] = {}

        # Voice sessions indexed by session_id
        self._voice_sessions: Dict[str, VoiceSession] = {}

        # Scheduled pickups: listing_id -> list of ScheduleResult
        self._schedules: Dict[str, List[ScheduleResult]] = {}

        # Rate limiting counters
        self._rate_counters: Dict[str, List[float]] = {}

    # ─────────────────────────────────────────────────────────────────────
    # Listing Management
    # ─────────────────────────────────────────────────────────────────────

    def save_listing(self, listing: Listing) -> None:
        """Save or update a listing."""
        listing.updated_at = datetime.now(timezone.utc).isoformat()
        if not listing.created_at:
            listing.created_at = listing.updated_at
        self._listings[listing.listing_id] = listing
        logger.info(f"Listing saved: {listing.listing_id} (status={listing.status})")

    def get_listing(self, listing_id: str) -> Optional[Listing]:
        """Retrieve a listing by ID."""
        return self._listings.get(listing_id)

    def get_active_listings(self) -> List[Listing]:
        """Get all active listings."""
        return [
            l for l in self._listings.values()
            if l.status == ListingStatus.ACTIVE
        ]

    def get_all_listings(self) -> List[Listing]:
        """Get all listings."""
        return list(self._listings.values())

    def update_listing_status(self, listing_id: str, status: ListingStatus) -> None:
        """Update the status of a listing."""
        listing = self._listings.get(listing_id)
        if listing:
            listing.status = status
            listing.updated_at = datetime.now(timezone.utc).isoformat()
            logger.info(f"Listing {listing_id} status updated to {status}")

    # ─────────────────────────────────────────────────────────────────────
    # Buyer Management
    # ─────────────────────────────────────────────────────────────────────

    def save_buyer(self, buyer: BuyerProfile) -> None:
        """Save or update a buyer profile."""
        self._buyers[buyer.buyer_id] = buyer

    def get_buyer(self, buyer_id: str) -> Optional[BuyerProfile]:
        """Retrieve a buyer profile."""
        return self._buyers.get(buyer_id)

    def get_or_create_buyer(self, buyer_id: str, **kwargs) -> BuyerProfile:
        """Get existing buyer or create a new profile."""
        buyer = self._buyers.get(buyer_id)
        if not buyer:
            buyer = BuyerProfile(buyer_id=buyer_id, **kwargs)
            self._buyers[buyer_id] = buyer
        return buyer

    # ─────────────────────────────────────────────────────────────────────
    # Conversation Management
    # ─────────────────────────────────────────────────────────────────────

    def add_message(
        self,
        listing_id: str,
        buyer_id: str,
        message: ChatMessage,
    ) -> None:
        """Add a message to a conversation."""
        if listing_id not in self._conversations:
            self._conversations[listing_id] = {}
        if buyer_id not in self._conversations[listing_id]:
            self._conversations[listing_id][buyer_id] = []
        self._conversations[listing_id][buyer_id].append(message)

    def get_conversation(
        self,
        listing_id: str,
        buyer_id: str,
        limit: int = 20,
    ) -> List[ChatMessage]:
        """Get conversation history for a listing-buyer pair."""
        messages = self._conversations.get(listing_id, {}).get(buyer_id, [])
        return messages[-limit:]

    def get_all_conversations_for_listing(
        self, listing_id: str
    ) -> Dict[str, List[ChatMessage]]:
        """Get all conversations for a listing."""
        return self._conversations.get(listing_id, {})

    # ─────────────────────────────────────────────────────────────────────
    # Negotiation Management
    # ─────────────────────────────────────────────────────────────────────

    def get_or_create_negotiation(
        self,
        listing_id: str,
        buyer_id: str,
        listed_price: float = 0.0,
        min_price: float = 0.0,
        max_discount_pct: float = 15.0,
    ) -> NegotiationContext:
        """Get or create a negotiation context."""
        if listing_id not in self._negotiations:
            self._negotiations[listing_id] = {}
        if buyer_id not in self._negotiations[listing_id]:
            self._negotiations[listing_id][buyer_id] = NegotiationContext(
                listing_id=listing_id,
                buyer_id=buyer_id,
                listed_price=listed_price,
                min_acceptable_price=min_price,
                max_discount_percentage=max_discount_pct,
            )
        return self._negotiations[listing_id][buyer_id]

    def add_negotiation_round(
        self,
        listing_id: str,
        buyer_id: str,
        round_data: NegotiationRound,
    ) -> None:
        """Add a negotiation round."""
        ctx = self.get_or_create_negotiation(listing_id, buyer_id)
        round_data.round_number = len(ctx.rounds) + 1
        round_data.timestamp = datetime.now(timezone.utc).isoformat()
        ctx.rounds.append(round_data)
        ctx.current_status = round_data.status

    def get_negotiation(
        self, listing_id: str, buyer_id: str
    ) -> Optional[NegotiationContext]:
        """Get negotiation context."""
        return self._negotiations.get(listing_id, {}).get(buyer_id)

    # ─────────────────────────────────────────────────────────────────────
    # Voice Session Management
    # ─────────────────────────────────────────────────────────────────────

    def save_voice_session(self, session: VoiceSession) -> None:
        """Save a voice session."""
        self._voice_sessions[session.session_id] = session

    def get_voice_session(self, session_id: str) -> Optional[VoiceSession]:
        """Get a voice session by ID."""
        return self._voice_sessions.get(session_id)

    def get_voice_sessions_for_listing(
        self, listing_id: str
    ) -> List[VoiceSession]:
        """Get all voice sessions for a listing."""
        return [
            s for s in self._voice_sessions.values()
            if s.listing_id == listing_id
        ]

    # ─────────────────────────────────────────────────────────────────────
    # Schedule Management
    # ─────────────────────────────────────────────────────────────────────

    def add_schedule(self, listing_id: str, schedule: ScheduleResult) -> None:
        """Add a scheduled pickup/viewing."""
        if listing_id not in self._schedules:
            self._schedules[listing_id] = []
        self._schedules[listing_id].append(schedule)

    def get_schedules(self, listing_id: str) -> List[ScheduleResult]:
        """Get all schedules for a listing."""
        return self._schedules.get(listing_id, [])

    # ─────────────────────────────────────────────────────────────────────
    # Rate Limiting
    # ─────────────────────────────────────────────────────────────────────

    def record_action(self, action_type: str) -> None:
        """Record an action timestamp for rate limiting."""
        now = datetime.now(timezone.utc).timestamp()
        if action_type not in self._rate_counters:
            self._rate_counters[action_type] = []
        self._rate_counters[action_type].append(now)

    def get_action_count(
        self, action_type: str, window_seconds: int = 3600
    ) -> int:
        """Count actions within a time window."""
        now = datetime.now(timezone.utc).timestamp()
        cutoff = now - window_seconds
        timestamps = self._rate_counters.get(action_type, [])
        # Clean old entries
        self._rate_counters[action_type] = [
            t for t in timestamps if t > cutoff
        ]
        return len(self._rate_counters[action_type])

    def check_rate_limit(
        self, action_type: str, max_count: int, window_seconds: int = 3600
    ) -> bool:
        """Check if an action is within rate limits. Returns True if allowed."""
        count = self.get_action_count(action_type, window_seconds)
        return count < max_count

    # ─────────────────────────────────────────────────────────────────────
    # Export / Debug
    # ─────────────────────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """Get memory store statistics."""
        return {
            "total_listings": len(self._listings),
            "active_listings": len(self.get_active_listings()),
            "total_buyers": len(self._buyers),
            "total_conversations": sum(
                len(buyers)
                for buyers in self._conversations.values()
            ),
            "total_negotiations": sum(
                len(buyers)
                for buyers in self._negotiations.values()
            ),
            "total_voice_sessions": len(self._voice_sessions),
            "total_schedules": sum(
                len(s) for s in self._schedules.values()
            ),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────────────────────

_store: Optional[MemoryStore] = None


def get_memory_store() -> MemoryStore:
    """Get or create the global memory store instance."""
    global _store
    if _store is None:
        _store = MemoryStore()
    return _store