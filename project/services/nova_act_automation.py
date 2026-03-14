"""Nova Act UI Automation Abstraction Layer.

Uses the Nova Act AWS Service via @workflow decorator pattern.
Workflow definition: arn:aws:nova-act:us-east-1:741241483179:workflow-definition/novasell
"""

import os
import base64
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

NOVA_ACT_WORKFLOW_DEFINITION = "novasell"
NOVA_ACT_MODEL_ID = "nova-act-latest"


# ─────────────────────────────────────────────────────────────────────────────
# Data Models
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class AutomationStep:
    """A single automation step result."""
    instruction: str
    success: bool
    response: Optional[str] = None
    screenshot_b64: Optional[str] = None
    error: Optional[str] = None


@dataclass
class AutomationResult:
    """Result of a complete automation workflow."""
    marketplace: str
    success: bool
    listing_url: str = ""
    listing_id: str = ""
    steps: List[AutomationStep] = field(default_factory=list)
    final_screenshot_b64: str = ""
    error: str = ""


@dataclass
class ListingData:
    """Standardized listing data for automation."""
    title: str
    description: str
    price: float
    currency: str = "AED"
    category: str = ""
    subcategory: str = ""
    condition: str = "Used - Good"
    location: str = ""
    image_urls: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    shipping_available: bool = False
    contact_info: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Abstract Base Class
# ─────────────────────────────────────────────────────────────────────────────


class MarketplaceAutomator(ABC):
    """Abstract base class for marketplace automation."""

    def __init__(self, headless: bool = True):
        self.headless = headless

    @property
    @abstractmethod
    def marketplace_name(self) -> str:
        """Name of the marketplace."""
        ...

    @property
    @abstractmethod
    def base_url(self) -> str:
        """Base URL of the marketplace."""
        ...

    @abstractmethod
    def get_create_listing_url(self) -> str:
        """URL to create a new listing."""
        ...

    @abstractmethod
    def get_listing_steps(self, listing: ListingData) -> List[str]:
        """Get the automation steps for creating a listing."""
        ...

    @abstractmethod
    def get_chat_response_steps(self, response_text: str) -> List[str]:
        """Get the automation steps for responding to a chat message."""
        ...

    async def create_listing(self, listing: ListingData) -> AutomationResult:
        """Create a listing on the marketplace using Nova Act AWS Service."""
        try:
            from nova_act import NovaAct, workflow

            steps = self.get_listing_steps(listing)
            result = AutomationResult(
                marketplace=self.marketplace_name,
                success=False,
            )

            starting_url = self.get_create_listing_url()
            marketplace_name = self.marketplace_name

            @workflow(
                workflow_definition_name=NOVA_ACT_WORKFLOW_DEFINITION,
                model_id=NOVA_ACT_MODEL_ID,
            )
            def run_listing_automation():
                with NovaAct(starting_page=starting_url) as nova:
                    step_results = []
                    for instruction in steps:
                        logger.info(f"[{marketplace_name}] Executing: {instruction[:80]}...")
                        try:
                            act_result = nova.act(instruction)
                            step_results.append({
                                "instruction": instruction,
                                "success": act_result.response is not None,
                                "response": str(act_result.response)[:500] if act_result.response else None,
                            })
                        except Exception as e:
                            step_results.append({
                                "instruction": instruction,
                                "success": False,
                                "error": str(e),
                            })

                    # Capture final screenshot
                    screenshot_b64 = ""
                    try:
                        screenshot = nova.page.screenshot()
                        if screenshot:
                            screenshot_b64 = base64.b64encode(screenshot).decode("utf-8")
                    except Exception:
                        pass

                    # Try to extract listing URL
                    current_url = ""
                    try:
                        current_url = nova.page.url or ""
                    except Exception:
                        pass

                    return {
                        "steps": step_results,
                        "screenshot_b64": screenshot_b64,
                        "current_url": current_url,
                    }

            # Execute the Nova Act workflow on AWS
            workflow_result = run_listing_automation()

            # Map workflow results back to AutomationResult
            for step_data in workflow_result.get("steps", []):
                step = AutomationStep(
                    instruction=step_data["instruction"],
                    success=step_data.get("success", False),
                    response=step_data.get("response"),
                    error=step_data.get("error"),
                )
                result.steps.append(step)

            result.final_screenshot_b64 = workflow_result.get("screenshot_b64", "")
            current_url = workflow_result.get("current_url", "")
            if current_url and current_url != starting_url:
                result.listing_url = current_url

            result.success = all(s.success for s in result.steps)
            return result

        except ImportError:
            logger.warning(f"Nova Act SDK not available for {self.marketplace_name}")
            return AutomationResult(
                marketplace=self.marketplace_name,
                success=False,
                error="Nova Act SDK not installed",
            )

        except Exception as e:
            logger.error(f"Automation error for {self.marketplace_name}: {e}")
            return AutomationResult(
                marketplace=self.marketplace_name,
                success=False,
                error=str(e),
            )

    async def respond_to_chat(self, listing_url: str, response_text: str) -> AutomationResult:
        """Respond to a customer chat on the marketplace using Nova Act AWS Service."""
        try:
            from nova_act import NovaAct, workflow

            chat_steps = self.get_chat_response_steps(response_text)
            result = AutomationResult(
                marketplace=self.marketplace_name,
                success=False,
            )

            @workflow(
                workflow_definition_name=NOVA_ACT_WORKFLOW_DEFINITION,
                model_id=NOVA_ACT_MODEL_ID,
            )
            def run_chat_automation():
                with NovaAct(starting_page=listing_url) as nova:
                    step_results = []
                    for instruction in chat_steps:
                        try:
                            act_result = nova.act(instruction)
                            step_results.append({
                                "instruction": instruction,
                                "success": act_result.response is not None,
                                "response": str(act_result.response)[:500] if act_result.response else None,
                            })
                        except Exception as e:
                            step_results.append({
                                "instruction": instruction,
                                "success": False,
                                "error": str(e),
                            })
                    return {"steps": step_results}

            workflow_result = run_chat_automation()

            for step_data in workflow_result.get("steps", []):
                step = AutomationStep(
                    instruction=step_data["instruction"],
                    success=step_data.get("success", False),
                    response=step_data.get("response"),
                    error=step_data.get("error"),
                )
                result.steps.append(step)

            result.success = all(s.success for s in result.steps)
            return result

        except ImportError:
            return AutomationResult(
                marketplace=self.marketplace_name,
                success=False,
                error="Nova Act SDK not installed",
            )

        except Exception as e:
            return AutomationResult(
                marketplace=self.marketplace_name,
                success=False,
                error=str(e),
            )


# ─────────────────────────────────────────────────────────────────────────────
# Marketplace Implementations
# ─────────────────────────────────────────────────────────────────────────────


class FacebookMarketplaceAutomator(MarketplaceAutomator):
    """Automation for Facebook Marketplace with Playwright keyboard login.

    Per Nova Act docs: never pass passwords to act(). Use page.keyboard.type() instead.
    Uses user_data_dir for persistent sessions so login only happens once.
    """

    @property
    def marketplace_name(self) -> str:
        return "facebook"

    @property
    def base_url(self) -> str:
        return "https://www.facebook.com"

    def get_create_listing_url(self) -> str:
        return "https://www.facebook.com/login"

    def get_listing_steps(self, listing: ListingData) -> List[str]:
        """Listing steps only — login is handled separately via keyboard API."""
        return [
            f'Click on the "What are you selling?" or title input field and type: "{listing.title}"',
            f'Set the price to: {listing.price:.2f}',
            f'Select the category that best matches: "{listing.category}"',
            f'Select the condition: "{listing.condition}"',
            f'Fill in the description field with: "{listing.description[:1000]}"',
            f'Set the location to: "{listing.location}"' if listing.location else 'Skip the location field',
            'Click the "Next" button',
            'Click the "Publish" or "Post" button to submit the listing',
        ]

    def get_chat_response_steps(self, response_text: str) -> List[str]:
        return [
            'Click on the messages or inbox icon',
            'Click on the most recent unread conversation',
            f'Type in the message input field: "{response_text}"',
            'Click the send button',
        ]

    async def create_listing(self, listing: ListingData) -> AutomationResult:
        """Create a Facebook listing — handles login via keyboard API, then posts."""
        try:
            import time
            from nova_act import NovaAct, workflow

            fb_email = os.environ.get("FACEBOOK_EMAIL", "")
            fb_pass = os.environ.get("FACEBOOK_PASS", "")
            user_data_dir = os.environ.get("NOVA_ACT_USER_DATA_DIR", "/data/novasell/nova-act-profile")
            os.makedirs(user_data_dir, exist_ok=True)

            steps = self.get_listing_steps(listing)
            result = AutomationResult(marketplace=self.marketplace_name, success=False)

            @workflow(
                workflow_definition_name=NOVA_ACT_WORKFLOW_DEFINITION,
                model_id=NOVA_ACT_MODEL_ID,
            )
            def run_fb_listing():
                with NovaAct(
                    starting_page=self.get_create_listing_url(),
                    user_data_dir=user_data_dir,
                    clone_user_data_dir=False,
                ) as nova:
                    # Login via Playwright keyboard API if on login page
                    current_url = nova.page.url or ""
                    if ("login" in current_url or "checkpoint" in current_url) and fb_email and fb_pass:
                        nova.act("Click on the email or phone number input field")
                        nova.page.keyboard.type(fb_email)
                        nova.act("Click on the password input field")
                        nova.page.keyboard.type(fb_pass)
                        nova.act('Click the "Log In" button')
                        time.sleep(3)
                        nova.act('If you see any prompts like "Not now", dismiss them. Otherwise skip.')

                    # Navigate to create listing page
                    nova.act("Navigate to https://www.facebook.com/marketplace/create/item")
                    nova.act('If you see any popups or overlays, close them. Otherwise skip.')

                    # Execute listing steps
                    step_results = []
                    for instruction in steps:
                        try:
                            act_result = nova.act(instruction)
                            step_results.append({
                                "instruction": instruction,
                                "success": act_result.response is not None,
                                "response": str(act_result.response)[:500] if act_result.response else None,
                            })
                        except Exception as e:
                            step_results.append({
                                "instruction": instruction,
                                "success": False,
                                "error": str(e),
                            })

                    # Capture final state
                    screenshot_b64 = ""
                    try:
                        screenshot = nova.page.screenshot()
                        if screenshot:
                            screenshot_b64 = base64.b64encode(screenshot).decode("utf-8")
                    except Exception:
                        pass

                    listing_url = ""
                    try:
                        listing_url = nova.page.url or ""
                    except Exception:
                        pass

                    return {
                        "steps": step_results,
                        "screenshot_b64": screenshot_b64,
                        "current_url": listing_url,
                    }

            workflow_result = run_fb_listing()

            for step_data in workflow_result.get("steps", []):
                step = AutomationStep(
                    instruction=step_data["instruction"],
                    success=step_data.get("success", False),
                    response=step_data.get("response"),
                    error=step_data.get("error"),
                )
                result.steps.append(step)

            result.final_screenshot_b64 = workflow_result.get("screenshot_b64", "")
            current_url = workflow_result.get("current_url", "")
            if current_url:
                result.listing_url = current_url
            result.success = all(s.success for s in result.steps)
            return result

        except ImportError:
            return AutomationResult(marketplace=self.marketplace_name, success=False, error="Nova Act SDK not installed")
        except Exception as e:
            logger.error(f"Facebook automation error: {e}")
            return AutomationResult(marketplace=self.marketplace_name, success=False, error=str(e))


class EbayAutomator(MarketplaceAutomator):
    """Automation for eBay."""

    @property
    def marketplace_name(self) -> str:
        return "ebay"

    @property
    def base_url(self) -> str:
        return "https://www.ebay.com"

    def get_create_listing_url(self) -> str:
        return "https://www.ebay.com/sl/sell"

    def get_listing_steps(self, listing: ListingData) -> List[str]:
        return [
            f'In the "Tell us what you\'re selling" search box, type: "{listing.title}" and press Enter',
            'Select the most relevant category from the suggestions',
            'Click "Continue without match" or select the closest match',
            f'Fill in the title field with: "{listing.title}"',
            f'Select the condition: "{listing.condition}"',
            f'Fill in the item description with: "{listing.description[:2000]}"',
            f'Set the price to: {listing.price:.2f}',
            'Select "Fixed price" as the format',
            f'Set shipping to: {"Free shipping" if listing.shipping_available else "Local pickup only"}',
            'Click "List item" or "List it" to publish the listing',
        ]

    def get_chat_response_steps(self, response_text: str) -> List[str]:
        return [
            'Click on "My eBay" and then "Messages"',
            'Click on the most recent unread message',
            f'Type the reply: "{response_text}"',
            'Click "Send"',
        ]


class CraigslistAutomator(MarketplaceAutomator):
    """Automation for Craigslist."""

    @property
    def marketplace_name(self) -> str:
        return "craigslist"

    @property
    def base_url(self) -> str:
        return "https://www.craigslist.org"

    def get_create_listing_url(self) -> str:
        return "https://www.craigslist.org/"

    def get_listing_steps(self, listing: ListingData) -> List[str]:
        return [
            'Click on "post to classifieds" link',
            'Select "for sale by owner"',
            f'Select the category closest to: "{listing.category}"',
            f'Fill in the posting title: "{listing.title}"',
            f'Set the price: {listing.price:.2f}',
            f'Fill in the posting body/description: "{listing.description[:2000]}"',
            f'Set the location/area: "{listing.location}"' if listing.location else 'Skip location',
            'Click "continue" to proceed',
            'Review the listing and click "publish" or "continue"',
        ]

    def get_chat_response_steps(self, response_text: str) -> List[str]:
        return [
            'This marketplace uses email for communication. Response sent via email.',
        ]


class OfferUpAutomator(MarketplaceAutomator):
    """Automation for OfferUp."""

    @property
    def marketplace_name(self) -> str:
        return "offerup"

    @property
    def base_url(self) -> str:
        return "https://offerup.com"

    def get_create_listing_url(self) -> str:
        return "https://offerup.com/post"

    def get_listing_steps(self, listing: ListingData) -> List[str]:
        return [
            f'Fill in the title: "{listing.title}"',
            f'Set the price to: {listing.price:.2f}',
            f'Select the condition: "{listing.condition}"',
            f'Fill in the description: "{listing.description[:1000]}"',
            f'Select the category: "{listing.category}"',
            'Click "Post" to publish the listing',
        ]

    def get_chat_response_steps(self, response_text: str) -> List[str]:
        return [
            'Click on the inbox/messages icon',
            'Click on the latest conversation',
            f'Type the message: "{response_text}"',
            'Click send',
        ]


class MercariAutomator(MarketplaceAutomator):
    """Automation for Mercari."""

    @property
    def marketplace_name(self) -> str:
        return "mercari"

    @property
    def base_url(self) -> str:
        return "https://www.mercari.com"

    def get_create_listing_url(self) -> str:
        return "https://www.mercari.com/sell/"

    def get_listing_steps(self, listing: ListingData) -> List[str]:
        return [
            f'Fill in the listing name/title: "{listing.title}"',
            f'Fill in the description: "{listing.description[:1000]}"',
            f'Select the category: "{listing.category}"',
            f'Select the condition: "{listing.condition}"',
            f'Set the price to: {listing.price:.2f}',
            'Select shipping method',
            'Click "List" to publish',
        ]

    def get_chat_response_steps(self, response_text: str) -> List[str]:
        return [
            'Click on the inbox icon',
            'Click on the latest conversation',
            f'Type: "{response_text}"',
            'Click send',
        ]


class DubizzleAutomator(MarketplaceAutomator):
    """Automation for Dubizzle."""

    @property
    def marketplace_name(self) -> str:
        return "dubizzle"

    @property
    def base_url(self) -> str:
        return "https://www.dubizzle.com"

    def get_create_listing_url(self) -> str:
        return "https://www.dubizzle.com/place-ad/"

    def get_listing_steps(self, listing: ListingData) -> List[str]:
        return [
            f'Select the category that best matches: "{listing.category}"',
            f'Fill in the title field with: "{listing.title}"',
            f'Fill in the description with: "{listing.description[:2000]}"',
            f'Set the price to: {listing.price:.2f}',
            f'Select the condition: "{listing.condition}"',
            f'Set the location to: "{listing.location}"' if listing.location else 'Skip the location field',
            'Click "Submit" or "Post Ad" to publish the listing',
        ]

    def get_chat_response_steps(self, response_text: str) -> List[str]:
        return [
            'Click on the chat or messages icon',
            'Click on the most recent unread conversation',
            f'Type in the message input field: "{response_text}"',
            'Click the send button',
        ]


# ─────────────────────────────────────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────────────────────────────────────


class MarketplaceAutomatorFactory:
    """Factory for creating marketplace automators."""

    _automators = {
        "facebook": FacebookMarketplaceAutomator,
        "ebay": EbayAutomator,
        "craigslist": CraigslistAutomator,
        "offerup": OfferUpAutomator,
        "mercari": MercariAutomator,
        "dubizzle": DubizzleAutomator,
    }

    @classmethod
    def create(
        cls,
        marketplace: str,
        headless: bool = True,
    ) -> MarketplaceAutomator:
        """Create a marketplace automator instance."""
        automator_class = cls._automators.get(marketplace.lower())
        if automator_class is None:
            raise ValueError(
                f"Unsupported marketplace: {marketplace}. "
                f"Supported: {', '.join(cls._automators.keys())}"
            )

        return automator_class(headless=headless)

    @classmethod
    def supported_marketplaces(cls) -> List[str]:
        """Get list of supported marketplace names."""
        return list(cls._automators.keys())
