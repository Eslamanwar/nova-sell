"""Listing domain models — object analysis, pricing, listing content, and posting results."""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────────────────────


class ListingStatus(str, Enum):
    """Status of a marketplace listing."""
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    PUBLISHING = "publishing"
    ACTIVE = "active"
    SOLD = "sold"
    EXPIRED = "expired"
    REMOVED = "removed"
    FAILED = "failed"


class ItemCondition(str, Enum):
    """Condition rating for items."""
    NEW_SEALED = "new_sealed"
    LIKE_NEW = "like_new"
    EXCELLENT = "excellent"
    VERY_GOOD = "very_good"
    GOOD = "good"
    FAIR = "fair"
    POOR = "poor"
    FOR_PARTS = "for_parts"


class PriceTrend(str, Enum):
    """Market price trend direction."""
    RISING = "rising"
    STABLE = "stable"
    DECLINING = "declining"


class SellSpeed(str, Enum):
    """Expected speed of sale."""
    FAST = "fast"
    MODERATE = "moderate"
    SLOW = "slow"


# ─────────────────────────────────────────────────────────────────────────────
# Object Analysis
# ─────────────────────────────────────────────────────────────────────────────


class ObjectAnalysis(BaseModel):
    """Result of AI object detection and analysis from an uploaded image."""
    object_type: str = ""
    brand: str = ""
    model: str = ""
    condition_score: float = 0.0
    condition_description: str = ""
    visible_defects: List[str] = Field(default_factory=list)
    detected_text: List[str] = Field(default_factory=list)
    color: str = ""
    accessories: List[str] = Field(default_factory=list)
    confidence: float = 0.0
    additional_notes: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Pricing
# ─────────────────────────────────────────────────────────────────────────────


class ComparableItem(BaseModel):
    """A comparable item found during market research."""
    title: str = ""
    price: float = 0.0
    platform: str = ""
    condition: str = ""
    url: str = ""


class PriceEstimate(BaseModel):
    """Result of AI pricing analysis."""
    min_price: float = 0.0
    max_price: float = 0.0
    recommended_price: float = 0.0
    currency: str = "AED"
    original_retail_price: float = 0.0
    depreciation_percentage: float = 0.0
    pricing_reasoning: str = ""
    comparable_items: List[ComparableItem] = Field(default_factory=list)
    price_trend: PriceTrend = PriceTrend.STABLE
    sell_speed_estimate: SellSpeed = SellSpeed.MODERATE
    confidence: float = 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Listing Content
# ─────────────────────────────────────────────────────────────────────────────


class ListingContent(BaseModel):
    """AI-generated listing content for marketplace posting."""
    title: str = ""
    description: str = ""
    short_description: str = ""
    tags: List[str] = Field(default_factory=list)
    category: str = ""
    subcategory: str = ""
    highlights: List[str] = Field(default_factory=list)
    specifications: Dict[str, str] = Field(default_factory=dict)
    seo_keywords: List[str] = Field(default_factory=list)
    suggested_images_order: List[str] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Posting Result
# ─────────────────────────────────────────────────────────────────────────────


class PostingResult(BaseModel):
    """Result of posting a listing to a marketplace via browser automation."""
    marketplace: str = ""
    listing_url: str = ""
    listing_id: str = ""
    status: str = ""  # posted | pending | failed | mock_posted
    screenshots: List[str] = Field(default_factory=list)
    automation_steps: List[Dict[str, Any]] = Field(default_factory=list)
    error_message: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Listing Aggregate
# ─────────────────────────────────────────────────────────────────────────────


class Listing(BaseModel):
    """Complete listing aggregate combining all listing-related data."""
    listing_id: str = ""
    status: ListingStatus = ListingStatus.DRAFT

    # Image data
    image_file_path: str = ""
    image_url: str = ""
    image_s3_key: str = ""

    # AI analysis results
    object_analysis: Optional[ObjectAnalysis] = None
    price_estimate: Optional[PriceEstimate] = None
    listing_content: Optional[ListingContent] = None

    # Publishing
    posting_result: Optional[PostingResult] = None
    marketplace_url: str = ""

    # Pricing boundaries for negotiation
    min_acceptable_price: float = 0.0
    max_discount_percentage: float = 15.0

    # Metadata
    seller_preferences: Dict[str, Any] = Field(default_factory=dict)
    user_hints: str = ""
    target_marketplace: str = "shozon"
    location: str = "Dubai"
    created_at: str = ""
    updated_at: str = ""