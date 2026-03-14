"""Temporal Activities for NovaSell autonomous Dubizzle selling agent.

Each activity corresponds to a specialized AI agent capability:
- Object Detection (Amazon Nova Lite — multimodal)
- Pricing (Amazon Nova Pro — reasoning)
- Listing Generation (Amazon Nova Pro — reasoning)
- Chat Handling / Conversation Agent (Amazon Nova Pro — reasoning)
- Negotiation Agent (Amazon Nova Pro — reasoning)
- Voice / Call Agent (Amazon Nova Sonic — speech-to-speech)
- Scheduling Agent (Amazon Nova Pro — reasoning)
- Browser Automation / Listing Agent (Nova Act — browser control)
- Image Storage
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from temporalio import activity

from agentex.lib.utils.logging import make_logger

from project.config import get_config
from project.constants import (
    OBJECT_DETECTION_SYSTEM_PROMPT,
    PRICING_SYSTEM_PROMPT,
    LISTING_GENERATION_SYSTEM_PROMPT,
    CHAT_AGENT_SYSTEM_PROMPT,
    NEGOTIATION_SYSTEM_PROMPT,
    VOICE_AGENT_SYSTEM_PROMPT,
    SCHEDULING_AGENT_SYSTEM_PROMPT,
)
from project.services.nova_llm import get_nova_llm, parse_json_response
from project.services.browser_automation import (
    get_browser_automation,
    signal_ui_takeover_complete,
    relay_ui_takeover_command,
)

logger = make_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Activity 1: Object Detection Agent (Nova Lite — Multimodal)
# ─────────────────────────────────────────────────────────────────────────────


@activity.defn(name="detect_object")
async def detect_object(
    image_base64: str,
    user_hints: str = "",
    image_file_path: str = "",
) -> Dict[str, Any]:
    """Analyze an uploaded image to detect object type, brand, model, and condition.

    Uses Amazon Nova Lite (multimodal) for image understanding.

    Args:
        image_base64: Base64-encoded image data
        user_hints: Optional hints from the user about the item
        image_file_path: Path to image on disk (preferred over base64)

    Returns:
        Dict with object analysis results
    """
    logger.info("Starting object detection analysis")
    activity.heartbeat("Analyzing image with Nova Lite...")

    llm = get_nova_llm()

    # Read image from disk if path provided and base64 is empty
    if not image_base64 and image_file_path:
        logger.info(f"Reading image from disk: {image_file_path}")
        with open(image_file_path, "rb") as f:
            image_base64 = base64.b64encode(f.read()).decode("utf-8")

    if not image_base64:
        raise ValueError("No image data provided (neither base64 nor file path)")

    prompt = (
        "Analyze this image of an item for sale on Dubizzle UAE. "
        "Identify the object type, brand, model, condition, and any visible text or defects."
    )
    if user_hints:
        prompt += f"\n\nAdditional context from the seller: {user_hints}"

    raw_response = await llm.call_with_image(
        system_prompt=OBJECT_DETECTION_SYSTEM_PROMPT,
        text_prompt=prompt,
        image_base64=image_base64,
        temperature=0.2,
    )

    try:
        result = parse_json_response(raw_response)
        logger.info(
            f"Object detected: {result.get('brand', 'Unknown')} "
            f"{result.get('model', 'Unknown')} "
            f"(confidence: {result.get('confidence', 0)})"
        )
        return result
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse object detection response: {e}")
        return {
            "object_type": "unknown",
            "brand": "unknown",
            "model": "unknown",
            "condition_score": 5,
            "condition_description": "Unable to assess from image",
            "visible_defects": [],
            "detected_text": [],
            "color": "unknown",
            "accessories": [],
            "confidence": 0.0,
            "additional_notes": f"Detection parsing failed: {str(e)}",
        }


# ─────────────────────────────────────────────────────────────────────────────
# Activity 2: Pricing Agent (Nova Pro — Reasoning)
# ─────────────────────────────────────────────────────────────────────────────


@activity.defn(name="estimate_price")
async def estimate_price(
    object_analysis: Dict[str, Any],
    market_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Estimate the market value of a detected item for the Dubai/UAE market.

    Uses Amazon Nova Pro (reasoning) for price estimation with market analysis.

    Args:
        object_analysis: Results from object detection
        market_context: Optional additional market data

    Returns:
        Dict with price estimate details
    """
    logger.info(
        f"Estimating price for {object_analysis.get('brand', 'Unknown')} "
        f"{object_analysis.get('model', 'Unknown')}"
    )
    activity.heartbeat("Researching market prices with Nova Pro...")

    llm = get_nova_llm()

    item_description = (
        f"Item: {object_analysis.get('object_type', 'Unknown')}\n"
        f"Brand: {object_analysis.get('brand', 'Unknown')}\n"
        f"Model: {object_analysis.get('model', 'Unknown')}\n"
        f"Condition Score: {object_analysis.get('condition_score', 5)}/10\n"
        f"Condition: {object_analysis.get('condition_description', 'Unknown')}\n"
        f"Color: {object_analysis.get('color', 'Unknown')}\n"
        f"Visible Defects: {', '.join(object_analysis.get('visible_defects', [])) or 'None'}\n"
        f"Accessories: {', '.join(object_analysis.get('accessories', [])) or 'None'}\n"
    )

    if market_context:
        item_description += f"\nAdditional Market Context:\n{json.dumps(market_context, indent=2)}"

    messages = [
        {"role": "system", "content": PRICING_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Estimate the market value for this item on Dubizzle Dubai:\n\n"
                f"{item_description}\n\n"
                f"Current date: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n"
                f"Market: Dubai, UAE (prices in AED)"
            ),
        },
    ]

    raw_response = await llm.call_nova_pro(messages=messages, temperature=0.3, max_tokens=3072)

    try:
        result = parse_json_response(raw_response)
        logger.info(
            f"Price estimate: {result.get('recommended_price', 0)} AED "
            f"(range: {result.get('min_price', 0)} - {result.get('max_price', 0)} AED)"
        )
        return result
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse pricing response: {e}")
        return {
            "min_price": 0, "max_price": 0, "recommended_price": 0,
            "currency": "AED", "original_retail_price": 0,
            "depreciation_percentage": 0,
            "pricing_reasoning": f"Pricing analysis failed: {str(e)}",
            "comparable_items": [], "price_trend": "stable",
            "sell_speed_estimate": "moderate", "confidence": 0.0,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Activity 3: Listing Generation Agent (Nova Pro — Reasoning)
# ─────────────────────────────────────────────────────────────────────────────


@activity.defn(name="generate_listing")
async def generate_listing(
    object_analysis: Dict[str, Any],
    price_estimate: Dict[str, Any],
    seller_preferences: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Generate a compelling Dubizzle marketplace listing.

    Uses Amazon Nova Pro (reasoning) for copywriting and SEO optimization.

    Args:
        object_analysis: Results from object detection
        price_estimate: Results from pricing analysis
        seller_preferences: Optional seller preferences

    Returns:
        Dict with listing content
    """
    logger.info("Generating Dubizzle listing")
    activity.heartbeat("Crafting listing with Nova Pro...")

    llm = get_nova_llm()

    item_context = (
        f"Product Details:\n"
        f"- Type: {object_analysis.get('object_type', 'Unknown')}\n"
        f"- Brand: {object_analysis.get('brand', 'Unknown')}\n"
        f"- Model: {object_analysis.get('model', 'Unknown')}\n"
        f"- Condition: {object_analysis.get('condition_score', 5)}/10 — "
        f"{object_analysis.get('condition_description', 'Unknown')}\n"
        f"- Color: {object_analysis.get('color', 'Unknown')}\n"
        f"- Defects: {', '.join(object_analysis.get('visible_defects', [])) or 'None'}\n"
        f"- Accessories: {', '.join(object_analysis.get('accessories', [])) or 'None'}\n"
        f"\nPricing:\n"
        f"- Recommended Price: {price_estimate.get('recommended_price', 0)} AED\n"
        f"- Price Range: {price_estimate.get('min_price', 0)} — {price_estimate.get('max_price', 0)} AED\n"
        f"- Market Trend: {price_estimate.get('price_trend', 'stable')}\n"
        f"\nMarketplace: Dubizzle Dubai, UAE"
    )

    if seller_preferences:
        item_context += f"\nSeller Preferences:\n{json.dumps(seller_preferences, indent=2)}"

    messages = [
        {"role": "system", "content": LISTING_GENERATION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Create a Dubizzle listing for this item:\n\n{item_context}\n\n"
                f"Generate a compelling, honest listing optimized for Dubizzle UAE."
            ),
        },
    ]

    raw_response = await llm.call_nova_pro(messages=messages, temperature=0.5, max_tokens=4096)

    try:
        result = parse_json_response(raw_response)
        logger.info(f"Listing generated: {result.get('title', 'Untitled')}")
        return result
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse listing response: {e}")
        brand = object_analysis.get("brand", "Unknown")
        model_name = object_analysis.get("model", "Unknown")
        obj_type = object_analysis.get("object_type", "Item")
        price = price_estimate.get("recommended_price", 0)
        return {
            "title": f"{brand} {model_name} — {obj_type} For Sale",
            "description": f"{brand} {model_name} for sale on Dubizzle. Condition: {object_analysis.get('condition_description', 'Good')}. Asking {price} AED.",
            "short_description": f"{brand} {model_name} in good condition",
            "tags": [brand.lower(), model_name.lower(), obj_type.lower(), "dubizzle", "dubai"],
            "category": "Electronics", "subcategory": obj_type,
            "highlights": [f"{brand} {model_name}"],
            "specifications": {}, "seo_keywords": [brand.lower(), model_name.lower()],
            "suggested_images_order": [],
        }


# ─────────────────────────────────────────────────────────────────────────────
# Activity 4: Conversation Agent (Nova Pro — Reasoning)
# ─────────────────────────────────────────────────────────────────────────────


@activity.defn(name="handle_chat_message")
async def handle_chat_message(
    customer_message: str,
    listing_context: Dict[str, Any],
    chat_history: List[Dict[str, str]],
    pricing_boundaries: Dict[str, float],
) -> Dict[str, Any]:
    """Handle a buyer chat message autonomously on Dubizzle.

    Uses Amazon Nova Pro (reasoning) for natural conversation and negotiation.

    Args:
        customer_message: The incoming buyer message
        listing_context: Details about the listing
        chat_history: Previous messages in the conversation
        pricing_boundaries: Min/max acceptable prices

    Returns:
        Dict with chat response and actions
    """
    logger.info(f"Handling chat message: {customer_message[:100]}...")
    activity.heartbeat("Processing buyer message with Nova Pro...")

    llm = get_nova_llm()

    listing_summary = (
        f"Listing: {listing_context.get('title', 'Unknown')}\n"
        f"Price: {listing_context.get('price', 0)} AED\n"
        f"Description: {listing_context.get('description', 'No description')[:500]}\n"
        f"Condition: {listing_context.get('condition', 'Unknown')}\n"
        f"Location: {listing_context.get('location', 'Dubai')}"
    )

    pricing_info = (
        f"Pricing Boundaries (CONFIDENTIAL — do not share with buyer):\n"
        f"- Listed Price: {pricing_boundaries.get('listed_price', 0)} AED\n"
        f"- Minimum Acceptable: {pricing_boundaries.get('min_price', 0)} AED\n"
        f"- Max Discount: {pricing_boundaries.get('max_discount_pct', 15)}%"
    )

    history_messages = []
    for msg in chat_history[-10:]:
        history_messages.append({
            "role": "assistant" if msg.get("role") == "agent" else "user",
            "content": msg.get("content", ""),
        })

    messages = [
        {
            "role": "system",
            "content": (
                f"{CHAT_AGENT_SYSTEM_PROMPT}\n\n"
                f"LISTING CONTEXT:\n{listing_summary}\n\n"
                f"PRICING RULES:\n{pricing_info}"
            ),
        },
        *history_messages,
        {"role": "user", "content": customer_message},
    ]

    raw_response = await llm.call_nova_pro(messages=messages, temperature=0.4, max_tokens=2048)

    try:
        result = parse_json_response(raw_response)
        logger.info(
            f"Chat response: negotiation={result.get('negotiation_status', 'none')}, "
            f"escalate={result.get('escalate_to_seller', False)}"
        )
        return result
    except json.JSONDecodeError:
        return {
            "reply": raw_response or "Thank you for your interest! Let me get back to you shortly.",
            "suggested_actions": [], "negotiation_status": "none",
            "agreed_price": None, "counter_offer": None,
            "escalate_to_seller": True,
            "escalation_reason": "Failed to parse structured response",
            "schedule_meeting": False, "meeting_details": {},
            "sentiment": "neutral", "buyer_intent": "inquiry",
        }


# ─────────────────────────────────────────────────────────────────────────────
# Activity 5: Negotiation Agent (Nova Pro — Reasoning)
# ─────────────────────────────────────────────────────────────────────────────


@activity.defn(name="negotiate_price")
async def negotiate_price(
    buyer_offer: float,
    listing_context: Dict[str, Any],
    pricing_boundaries: Dict[str, float],
    negotiation_history: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Handle a price negotiation round with a buyer.

    Uses Amazon Nova Pro for strategic negotiation reasoning.

    Args:
        buyer_offer: The buyer's current offer in AED
        listing_context: Listing details
        pricing_boundaries: Min/max prices and discount limits
        negotiation_history: Previous negotiation rounds

    Returns:
        Dict with negotiation decision
    """
    logger.info(f"Negotiating: buyer offers {buyer_offer} AED")
    activity.heartbeat("Negotiating price with Nova Pro...")

    llm = get_nova_llm()

    history_text = ""
    for round_data in negotiation_history:
        history_text += (
            f"Round {round_data.get('round_number', '?')}: "
            f"Buyer offered {round_data.get('buyer_offer', '?')} AED, "
            f"Agent countered {round_data.get('agent_counter', '?')} AED\n"
        )

    messages = [
        {"role": "system", "content": NEGOTIATION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Negotiation Context:\n"
                f"- Item: {listing_context.get('title', 'Unknown')}\n"
                f"- Listed Price: {pricing_boundaries.get('listed_price', 0)} AED\n"
                f"- Minimum Acceptable: {pricing_boundaries.get('min_price', 0)} AED\n"
                f"- Max Discount: {pricing_boundaries.get('max_discount_pct', 15)}%\n\n"
                f"Negotiation History:\n{history_text or 'No previous rounds'}\n\n"
                f"Current Buyer Offer: {buyer_offer} AED\n\n"
                f"What is your decision?"
            ),
        },
    ]

    raw_response = await llm.call_nova_pro(messages=messages, temperature=0.3, max_tokens=1024)

    try:
        result = parse_json_response(raw_response)
        logger.info(f"Negotiation decision: {result.get('decision', 'unknown')}")
        return result
    except json.JSONDecodeError:
        # Default: decline if below minimum
        min_price = pricing_boundaries.get("min_price", 0)
        if buyer_offer >= min_price:
            return {
                "decision": "accept", "counter_offer": None,
                "reasoning": "Offer meets minimum price",
                "response_to_buyer": f"I accept your offer of {buyer_offer} AED.",
                "escalation_needed": False, "escalation_reason": None,
                "confidence": 0.5,
            }
        return {
            "decision": "decline", "counter_offer": min_price,
            "reasoning": "Offer below minimum acceptable price",
            "response_to_buyer": f"I appreciate your offer, but the lowest I can go is {min_price} AED.",
            "escalation_needed": False, "escalation_reason": None,
            "confidence": 0.5,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Activity 6: Voice / Call Agent (Nova Sonic)
# ─────────────────────────────────────────────────────────────────────────────


@activity.defn(name="handle_voice_session")
async def handle_voice_session(
    session_id: str,
    audio_input_base64: str,
    listing_context: Dict[str, Any],
    conversation_transcript: List[Dict[str, str]],
) -> Dict[str, Any]:
    """Handle a voice conversation turn using Amazon Nova Sonic.

    Args:
        session_id: Unique session identifier
        audio_input_base64: Base64-encoded audio input from caller
        listing_context: Details about the listing
        conversation_transcript: Previous turns

    Returns:
        Dict with audio response and transcript
    """
    logger.info(f"Processing voice session: {session_id}")
    activity.heartbeat("Processing voice with Nova Sonic...")

    from project.services.nova_sonic import get_nova_sonic

    sonic = get_nova_sonic()

    # Start or continue session
    pricing_boundaries = {
        "listed_price": listing_context.get("price", 0),
        "min_price": listing_context.get("min_price", 0),
        "max_discount_pct": listing_context.get("max_discount_pct", 15),
    }

    try:
        result = await sonic.process_audio_turn(
            session_id=session_id,
            audio_base64=audio_input_base64,
            listing_context=listing_context,
            pricing_boundaries=pricing_boundaries,
        )
        return result
    except Exception as e:
        logger.error(f"Voice session error: {e}")
        return {
            "session_id": session_id,
            "status": "error",
            "response_text": "I'm sorry, I'm having trouble processing that. Could you repeat?",
            "response_audio_base64": "",
            "error": str(e),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Activity 7: Scheduling Agent (Nova Pro — Reasoning)
# ─────────────────────────────────────────────────────────────────────────────


@activity.defn(name="handle_scheduling")
async def handle_scheduling(
    request: str,
    seller_availability: List[Dict[str, str]],
    listing_context: Dict[str, Any],
) -> Dict[str, Any]:
    """Handle pickup/viewing scheduling between seller and buyer.

    Args:
        request: Natural language scheduling request
        seller_availability: Available time slots
        listing_context: Listing details

    Returns:
        Dict with scheduling result
    """
    logger.info(f"Processing scheduling request: {request[:100]}...")
    activity.heartbeat("Scheduling with Nova Pro...")

    llm = get_nova_llm()

    availability_text = "\n".join(
        f"- {slot.get('date', 'Unknown')}: {slot.get('start', '?')} — {slot.get('end', '?')}"
        for slot in seller_availability
    ) if seller_availability else "No specific availability provided. Suggest reasonable times in Dubai."

    messages = [
        {"role": "system", "content": SCHEDULING_AGENT_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Scheduling Request: {request}\n\n"
                f"Item: {listing_context.get('title', 'Unknown item')}\n"
                f"Location: {listing_context.get('location', 'Dubai')}\n\n"
                f"Seller Availability:\n{availability_text}\n\n"
                f"Current Date/Time: {datetime.now(timezone.utc).isoformat()}\n"
                f"Timezone: GST (UTC+4)"
            ),
        },
    ]

    raw_response = await llm.call_nova_pro(messages=messages, temperature=0.3, max_tokens=2048)

    try:
        return parse_json_response(raw_response)
    except json.JSONDecodeError:
        return {
            "action": "schedule",
            "proposed_times": [],
            "confirmation_message": "I'll coordinate a meeting time. The seller will confirm shortly.",
            "calendar_event": {},
        }


# ─────────────────────────────────────────────────────────────────────────────
# Activity 8: Browser Automation — Post Listing to Dubizzle (Nova Act)
# ─────────────────────────────────────────────────────────────────────────────


@activity.defn(name="post_listing_to_marketplace")
async def post_listing_to_marketplace(
    listing_content: Dict[str, Any],
    price: float,
    image_urls: List[str],
    marketplace: str,
    task_id: str = "",
) -> Dict[str, Any]:
    """Post a listing to Dubizzle using Nova Act browser automation.

    Streams browser screenshots to the chat UI after each step.
    Includes anti-ban delays and CAPTCHA HITL support.

    Args:
        listing_content: Generated listing content
        price: Listing price in AED
        image_urls: Product image URLs
        marketplace: Target marketplace (dubizzle)
        task_id: Task ID for streaming screenshots

    Returns:
        Dict with posting result
    """
    from agentex.lib import adk
    from agentex.types.data_content import DataContent

    logger.info(f"Posting listing to {marketplace}")
    activity.heartbeat(f"Automating {marketplace} listing with Nova Act...")

    # Keep Temporal from retrying during long HITL sessions (login, CAPTCHA, etc.)
    # by sending a heartbeat every 30 s for the lifetime of this activity.
    heartbeat_task: Optional[asyncio.Task] = None
    async def _heartbeat_loop():
        while True:
            await asyncio.sleep(30)
            try:
                activity.heartbeat(f"{marketplace} browser automation in progress...")
            except Exception:
                break
    heartbeat_task = asyncio.create_task(_heartbeat_loop())

    async def _send_browser_frame(
        screenshot_bytes: bytes, step_num: int, step_label: str, total_steps: int
    ):
        """Send a browser screenshot frame to the chat UI."""
        if not task_id:
            return
        try:
            frame_b64 = base64.b64encode(screenshot_bytes).decode("utf-8") if screenshot_bytes else ""
            await adk.messages.create(
                task_id=task_id,
                content=DataContent(
                    author="agent",
                    data={
                        "type": "browser_frame",
                        "image_base64": frame_b64,
                        "step": step_num,
                        "total_steps": total_steps,
                        "step_label": step_label,
                        "marketplace": marketplace,
                    },
                ),
            )
        except Exception as e:
            logger.warning(f"Failed to send browser frame: {e}")

    automation = get_browser_automation()

    try:
        if marketplace == "shozon":
            result = await automation.create_shozon_listing(
                listing_data=listing_content,
                price=price,
                image_urls=image_urls,
                task_id=task_id,
                send_frame_callback=_send_browser_frame,
            )
        elif marketplace == "facebook":
            result = await automation.create_facebook_listing(
                listing_data=listing_content,
                price=price,
                image_urls=image_urls,
                task_id=task_id,
                send_frame_callback=_send_browser_frame,
            )
        else:
            result = await automation.create_listing(
                listing_data=listing_content,
                price=price,
                image_urls=image_urls,
                task_id=task_id,
                send_frame_callback=_send_browser_frame,
            )
    finally:
        if heartbeat_task:
            heartbeat_task.cancel()

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Activity 9: Respond to Chat on Dubizzle (Nova Act)
# ─────────────────────────────────────────────────────────────────────────────


@activity.defn(name="respond_to_marketplace_chat")
async def respond_to_marketplace_chat(
    marketplace: str,
    listing_url: str,
    response_text: str,
) -> Dict[str, Any]:
    """Respond to a buyer chat on Dubizzle using Nova Act browser automation.

    Args:
        marketplace: The marketplace platform
        listing_url: URL of the listing
        response_text: The response to send

    Returns:
        Dict with automation result
    """
    logger.info(f"Responding to chat on {marketplace}")
    activity.heartbeat(f"Automating chat response on {marketplace}...")

    automation = get_browser_automation()
    return await automation.respond_to_chat(listing_url, response_text)


# ─────────────────────────────────────────────────────────────────────────────
# Activity 10: Image Storage
# ─────────────────────────────────────────────────────────────────────────────


@activity.defn(name="upload_image_to_disk")
async def upload_image_to_disk(
    image_base64: str,
    filename: str,
) -> Dict[str, str]:
    """Save an image to local disk storage.

    Args:
        image_base64: Base64-encoded image data
        filename: Desired filename

    Returns:
        Dict with file path and local URL
    """
    logger.info(f"Saving image to disk: {filename}")

    try:
        config = get_config()
        storage_dir = config.storage.image_storage_dir
        date_dir = datetime.now(timezone.utc).strftime("%Y/%m/%d")
        full_dir = os.path.join(storage_dir, "listings", date_dir)
        os.makedirs(full_dir, exist_ok=True)

        file_path = os.path.join(full_dir, filename)
        image_data = base64.b64decode(image_base64)
        with open(file_path, "wb") as f:
            f.write(image_data)

        relative_path = f"listings/{date_dir}/{filename}"
        logger.info(f"Image saved: {file_path}")

        return {
            "file_path": file_path,
            "relative_path": relative_path,
            "image_url": file_path,
        }
    except Exception as e:
        logger.error(f"Disk save error: {e}")
        return {"file_path": "", "relative_path": "", "image_url": "", "error": str(e)}