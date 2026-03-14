"""Browser Automation Service — Dubizzle-focused Nova Act automation.

Handles all browser interactions with Dubizzle:
- Login and session management
- Listing creation (form filling, image upload, submission)
- Reading buyer messages
- Responding to chats
- Detecting page states (CAPTCHA, errors, success)

Uses anti-ban strategies for human-like behavior.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import queue
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from project.config import get_config
from project.services.anti_ban import get_anti_ban

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# HITL Signaling (bridges workflow signal handler ↔ activity thread)
# ─────────────────────────────────────────────────────────────────────────────

_ui_takeover_events: Dict[str, threading.Event] = {}
_ui_takeover_results: Dict[str, str] = {}
_ui_takeover_commands: Dict[str, queue.Queue] = {}


def signal_ui_takeover_complete(task_id: str, result: str) -> None:
    """Called from the workflow signal handler when user clicks Done/Cancel."""
    _ui_takeover_results[task_id] = result
    event = _ui_takeover_events.get(task_id)
    if event:
        event.set()


def relay_ui_takeover_command(task_id: str, cmd: Dict[str, Any]) -> None:
    """Called from the workflow signal handler to relay click/key commands."""
    cmd_queue = _ui_takeover_commands.get(task_id)
    if cmd_queue:
        cmd_queue.put(cmd)


class DubizzleBrowserAutomation:
    """Dubizzle-specific browser automation using Nova Act.

    Responsibilities:
    - Navigate Dubizzle pages
    - Fill listing forms with anti-ban delays
    - Upload images
    - Submit listings
    - Read and respond to buyer messages
    - Detect CAPTCHA and trigger HITL
    """

    def __init__(self):
        self._config = get_config()
        self._anti_ban = get_anti_ban()

    def get_dubizzle_listing_steps(self, listing_data: Dict[str, Any], price: float) -> List[str]:
        """Generate Nova Act instruction steps for creating a Dubizzle listing.

        Args:
            listing_data: Listing content (title, description, category, etc.)
            price: Listing price in AED

        Returns:
            List of natural language instructions for Nova Act
        """
        steps = [
            f'Select the category that best matches: "{listing_data.get("category", "Electronics")}"',
            f'Fill in the title field with: "{listing_data.get("title", "")}"',
            f'Fill in the description with: "{listing_data.get("description", "")}"',
            f'Set the price to: {price:.2f} AED',
        ]

        condition = listing_data.get("condition", "")
        if condition:
            steps.append(f'Set the condition to match: "{condition}"')

        location = listing_data.get("location", self._config.dubizzle.default_location)
        if location:
            steps.append(f'Set the location to {location} if there is a location field')

        steps.append(
            'Click "Submit" or "Post Ad" or "Place Your Ad" to publish the listing'
        )

        return steps

    def get_chat_response_steps(self, response_text: str) -> List[str]:
        """Generate Nova Act steps for responding to a Dubizzle chat.

        Args:
            response_text: The response message to send

        Returns:
            List of instructions for Nova Act
        """
        return [
            "Click on the chat or messages icon",
            "Click on the most recent unread conversation",
            f'Type in the message input field: "{response_text}"',
            "Click the send button",
        ]

    def get_read_messages_steps(self) -> List[str]:
        """Generate Nova Act steps for reading unread Dubizzle messages.

        Returns:
            List of instructions for Nova Act
        """
        return [
            "Navigate to the messages or chat section",
            "Look for any unread messages indicated by badges or bold text",
            "Click on the first unread conversation",
            "Read the latest message from the buyer",
            "Extract the buyer's message text and any relevant details",
        ]

    @staticmethod
    def _cleanup_singleton_lock(user_data_dir: str) -> None:
        """Remove stale Chromium singleton lock files left by a crashed session.

        Chrome writes SingletonLock/SingletonSocket/SingletonCookie to the
        profile directory to prevent two instances from sharing it. If Chrome
        crashes these files are never removed and the next launch fails with
        exit code 21. Safe to delete when no other Chrome process is running
        against this directory.
        """
        import os as _os
        for fname in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
            path = _os.path.join(user_data_dir, fname)
            try:
                _os.remove(path)
                logger.info(f"Removed stale lock file: {path}")
            except FileNotFoundError:
                pass
            except Exception as e:
                logger.warning(f"Could not remove {path}: {e}")

    async def create_listing(
        self,
        listing_data: Dict[str, Any],
        price: float,
        image_urls: List[str],
        task_id: str = "",
        send_frame_callback: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        """Create a listing on Dubizzle using Nova Act browser automation.

        Args:
            listing_data: Listing content
            price: Price in AED
            image_urls: Product image URLs
            task_id: Task ID for HITL signaling
            send_frame_callback: Callback to stream browser screenshots

        Returns:
            Dict with posting result
        """
        # Check rate limits
        if not self._anti_ban.check_listing_rate_limit():
            return {
                "marketplace": "dubizzle",
                "listing_url": "",
                "listing_id": "",
                "status": "rate_limited",
                "automation_steps": [],
                "error_message": "Rate limit reached. Try again later.",
            }

        try:
            from nova_act import NovaAct, workflow as nova_workflow

            config = self._config
            anti_ban = self._anti_ban
            dubizzle_config = config.dubizzle

            user_data_dir = anti_ban.get_user_data_dir()
            has_session = anti_ban.has_saved_session()
            self._cleanup_singleton_lock(user_data_dir)

            starting_url = (
                dubizzle_config.dubizzle_place_ad_url
                if has_session
                else dubizzle_config.dubizzle_login_url
            )

            listing_steps = self.get_dubizzle_listing_steps(listing_data, price)
            total_steps = len(listing_steps)

            # Event loop for async callbacks from sync thread
            loop = asyncio.get_event_loop()
            frame_count = [0]

            def _send_frame_sync(screenshot_bytes: bytes, step_num: int, step_label: str):
                """Stream a browser screenshot from the sync Nova Act thread."""
                if not screenshot_bytes or not send_frame_callback:
                    return
                try:
                    future = asyncio.run_coroutine_threadsafe(
                        send_frame_callback(screenshot_bytes, step_num, step_label, total_steps),
                        loop,
                    )
                    future.result(timeout=10)
                    frame_count[0] += 1
                except Exception as e:
                    logger.warning(f"Failed to stream browser frame: {e}")

            # Mutable ref so the HITL callback can access the NovaAct instance
            nova_ref: List[Any] = [None]

            @nova_workflow(
                workflow_definition_name=config.nova.nova_act_workflow_definition,
                model_id=config.nova.nova_act_model_id,
            )
            def run_dubizzle_listing():
                # Build HITL callbacks inside the workflow function (same pattern as backup)
                hitl_callbacks = self._create_hitl_callbacks(task_id, loop, nova_ref)

                nova_kwargs = {
                    "starting_page": starting_url,
                    "tty": False,
                    "user_data_dir": user_data_dir,
                    "clone_user_data_dir": False,
                }
                if hitl_callbacks:
                    nova_kwargs["human_input_callbacks"] = hitl_callbacks

                with NovaAct(**nova_kwargs) as nova:
                    nova_ref[0] = nova  # allow HITL callback to access the page
                    results = []

                    # ── Login if needed ──
                    if not has_session:
                        self._handle_dubizzle_login_sync(
                            nova, dubizzle_config, anti_ban, _send_frame_sync
                        )

                    # ── Dismiss popups ──
                    anti_ban.random_delay_sync(1.0, 2.0)
                    try:
                        nova.act(
                            'If you see any popups, overlays, or notification prompts, '
                            'dismiss them by clicking X or "Not now". Otherwise skip.'
                        )
                    except Exception as e:
                        logger.warning(f"Popup dismissal failed (non-fatal): {e}")

                    # ── Initial screenshot ──
                    try:
                        _send_frame_sync(
                            nova.page.screenshot(), 0,
                            "Ready to create listing on Dubizzle"
                        )
                    except Exception:
                        pass

                    # ── Execute listing steps with anti-ban delays ──
                    for i, step_instruction in enumerate(listing_steps):
                        # Anti-ban delay between steps
                        anti_ban.random_delay_sync()

                        try:
                            result = nova.act(step_instruction)
                            results.append({
                                "step": step_instruction,
                                "success": result.response is not None,
                                "response": str(result.response)[:200] if result.response else None,
                            })
                        except Exception as e:
                            results.append({
                                "step": step_instruction,
                                "success": False,
                                "error": str(e),
                            })

                        # Screenshot after each step
                        try:
                            _send_frame_sync(
                                nova.page.screenshot(), i + 1,
                                step_instruction[:80]
                            )
                        except Exception:
                            pass

                    # Extract listing URL
                    listing_url = ""
                    try:
                        current_url = nova.page.url
                        if current_url and current_url != starting_url:
                            listing_url = current_url
                    except Exception:
                        pass

                    return {
                        "results": results,
                        "listing_url": listing_url,
                    }

            # Execute in thread pool
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                workflow_result = await loop.run_in_executor(pool, run_dubizzle_listing)

            # Record for rate limiting
            self._anti_ban.record_listing_created()

            logger.info(f"Listing posted to Dubizzle, streamed {frame_count[0]} frames")
            return {
                "marketplace": "dubizzle",
                "listing_url": workflow_result.get("listing_url", ""),
                "listing_id": "",
                "status": "posted",
                "screenshots": [],
                "automation_steps": workflow_result.get("results", []),
                "error_message": "",
            }

        except ImportError:
            logger.warning("Nova Act SDK not available. Using mock automation.")
            return await self._mock_listing(listing_data, price, send_frame_callback)

        except Exception as e:
            logger.error(f"Dubizzle automation error: {e}")
            return {
                "marketplace": "dubizzle",
                "listing_url": "",
                "listing_id": "",
                "status": "failed",
                "automation_steps": [],
                "error_message": str(e),
            }

    async def respond_to_chat(
        self,
        listing_url: str,
        response_text: str,
    ) -> Dict[str, Any]:
        """Respond to a buyer chat on Dubizzle using Nova Act.

        Args:
            listing_url: URL of the listing
            response_text: Response message to send

        Returns:
            Dict with automation result
        """
        if not self._anti_ban.check_message_rate_limit():
            return {
                "status": "rate_limited",
                "marketplace": "dubizzle",
                "error": "Message rate limit reached",
            }

        try:
            from nova_act import NovaAct, workflow as nova_workflow

            config = self._config
            anti_ban = self._anti_ban
            user_data_dir = anti_ban.get_user_data_dir()
            chat_steps = self.get_chat_response_steps(response_text)

            @nova_workflow(
                workflow_definition_name=config.nova.nova_act_workflow_definition,
                model_id=config.nova.nova_act_model_id,
            )
            def run_chat_response():
                with NovaAct(
                    starting_page=listing_url,
                    tty=False,
                    user_data_dir=user_data_dir,
                    clone_user_data_dir=False,
                ) as nova:
                    for step in chat_steps:
                        anti_ban.random_delay_sync(0.5, 1.5)
                        nova.act(step)

                    screenshot_b64 = ""
                    try:
                        screenshot = nova.page.screenshot()
                        if screenshot:
                            screenshot_b64 = base64.b64encode(screenshot).decode("utf-8")
                    except Exception:
                        pass

                    return {"screenshot_b64": screenshot_b64}

            result = run_chat_response()
            self._anti_ban.record_message_sent()

            return {
                "status": "sent",
                "marketplace": "dubizzle",
                "screenshot": result.get("screenshot_b64", ""),
            }

        except ImportError:
            return {
                "status": "mock_sent",
                "marketplace": "dubizzle",
                "note": "Nova Act SDK not installed",
            }

        except Exception as e:
            logger.error(f"Chat automation error: {e}")
            return {
                "status": "failed",
                "marketplace": "dubizzle",
                "error": str(e),
            }

    # ─────────────────────────────────────────────────────────────────────
    # Internal: Login
    # ─────────────────────────────────────────────────────────────────────

    def _handle_dubizzle_login_sync(
        self,
        nova,
        dubizzle_config,
        anti_ban,
        send_frame_fn,
    ) -> None:
        """Handle Dubizzle login via Playwright keyboard API (not Nova Act).

        Uses direct Playwright selectors to avoid leaking credentials
        into Nova Act's LLM context.
        """
        email = dubizzle_config.dubizzle_email
        password = dubizzle_config.dubizzle_password

        if not email or not password:
            logger.warning("Dubizzle credentials not configured, skipping login")
            return

        try:
            send_frame_fn(nova.page.screenshot(), 0, "Checking Dubizzle login status...")
        except Exception:
            pass

        current_url = nova.page.url or ""
        if "login" not in current_url and "signin" not in current_url:
            logger.info("Already logged in to Dubizzle")
            return

        logger.info("Dubizzle login required, entering credentials via Playwright API")

        try:
            nova.page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass

        # Dismiss cookie consent
        try:
            cookie_btn = nova.page.locator(
                'button:has-text("Accept"), button:has-text("Got it"), button:has-text("OK")'
            )
            if cookie_btn.count() > 0:
                cookie_btn.first.click(timeout=5000)
                anti_ban.random_delay_sync(0.5, 1.0)
        except Exception:
            pass

        try:
            send_frame_fn(nova.page.screenshot(), 0, "Logging in to Dubizzle...")
        except Exception:
            pass

        # Check if email field is visible — if not, there may be a CAPTCHA/challenge.
        # Fall back to nova.act() which will call human_UiTakeover automatically.
        email_field = nova.page.locator(
            'input[name="email"], input[type="email"], '
            'input[placeholder*="mail"], #email'
        ).first
        email_visible = False
        try:
            email_field.wait_for(state="visible", timeout=8000)
            email_visible = True
        except Exception:
            logger.warning(
                "Email field not visible — possible CAPTCHA/Cloudflare challenge. "
                "Handing off to Nova Act to resolve."
            )

        if not email_visible:
            # Nova Act will see the CAPTCHA and call human_UiTakeover
            try:
                nova.act(
                    "If you see a CAPTCHA, security challenge, or Cloudflare verification, "
                    "use the human_UiTakeover tool so the user can solve it. "
                    "Otherwise complete any visible step on the page."
                )
            except Exception as e:
                logger.warning(f"CAPTCHA/challenge act failed: {e}")
            # After HITL resolves, check if login form is now visible
            try:
                email_field.wait_for(state="visible", timeout=10000)
                email_visible = True
            except Exception:
                logger.warning("Email field still not visible after HITL — attempting to proceed")

        if email_visible:
            try:
                email_field.click()
            except Exception as e:
                logger.warning(f"Email field click failed: {e}")
            anti_ban.type_with_delays_sync(nova.page, email)

            anti_ban.random_delay_sync(0.5, 1.0)

            # Password field
            pass_field = nova.page.locator(
                'input[name="password"], input[type="password"], #password'
            ).first
            try:
                pass_field.wait_for(state="visible", timeout=10000)
                pass_field.click()
                anti_ban.type_with_delays_sync(nova.page, password)
                anti_ban.random_delay_sync(0.3, 0.8)
                nova.page.keyboard.press("Enter")
            except Exception as e:
                logger.warning(f"Password field error: {e}")

        try:
            nova.page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        anti_ban.page_load_delay_sync()

        try:
            send_frame_fn(nova.page.screenshot(), 0, "Logged in to Dubizzle")
        except Exception:
            pass

        # Dismiss post-login prompts
        try:
            nova.act(
                'If you see any popups, overlays, or notification prompts, '
                'dismiss them by clicking X or "Not now". Otherwise skip.'
            )
        except Exception as e:
            logger.warning(f"Post-login popup dismissal failed (non-fatal): {e}")

        # Navigate to place-ad page
        try:
            nova.page.goto(dubizzle_config.dubizzle_place_ad_url)
        except Exception as e:
            logger.warning(f"Navigation to place-ad page failed: {e}")
        try:
            nova.page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        anti_ban.page_load_delay_sync()

    # ─────────────────────────────────────────────────────────────────────
    # Shozon Marketplace
    # ─────────────────────────────────────────────────────────────────────

    def get_shozon_listing_steps(self, listing_data: Dict[str, Any], price: float, image_paths: List[str] = None) -> List[str]:
        """Generate Nova Act steps that match the exact Shozon ad-creation wizard.

        Shozon wizard flow (verified from screenshots):
          Screen 1 — "What's Your Ad About?" modal → Classified
          Screen 2 — "How would you like to continue?" → Continue Manually
          Screen 3-6 — "Select the Sub Category" multi-level drill-down
          Screen 7/9 — "Complete Ad Details" Page 1: Title, Description, Phone, Location → Next
          Screen 10  — "Complete Ad Details" Page 2: Media upload + Required fields → Next
          Screen 11  — Price → "Create Ad"
        """
        category = listing_data.get("category", "Electronics")
        subcategory = listing_data.get("subcategory", "")
        title = listing_data.get("title", "")
        description = listing_data.get("description", "")
        phone = self._config.shozon.shozon_phone or ""
        location = listing_data.get("location", "Dubai") or "Dubai"
        condition = listing_data.get("condition", "")

        # Use the deepest available category term for the search box
        category_search = subcategory if subcategory else category

        steps = [
            # ── Screen 1: Ad type ─────────────────────────────────────────
            (
                'You should see a modal titled "What\'s Your Ad About?" showing options like '
                'Motors, Classified, Jobs, Property. Click "Classified".'
            ),

            # ── Screen 2: Manual vs AI ────────────────────────────────────
            (
                'You should see "How would you like to continue?" with two cards. '
                'Click "Continue Manually" (the left card with the pencil icon).'
            ),

            # ── Screen 3-6: Category drill-down ──────────────────────────
            # The dialog has a search box "Searching your ad category ..." at every
            # level. Type the search term and click the best match. If it opens
            # another sub-level, keep clicking until no arrow (>) appears next to
            # the items (meaning we've reached a leaf category).
            (
                f'You are in the "Select the Sub Category" dialog. '
                f'Click the search box labelled "Searching your ad category ..." '
                f'and type "{category_search}". '
                f'If matching results appear, click the most relevant one. '
                f'If it opens another sub-level with more items (shown with a ">" arrow), '
                f'keep clicking the most relevant sub-item until you reach a leaf category '
                f'(no ">" arrow). '
                f'If the search returns no results, clear the search box and navigate '
                f'manually by clicking the most relevant top-level category, '
                f'then its sub-categories, until you reach a leaf.'
            ),

            # ── Screen 7/9: "Complete Ad Details" Page 1 ─────────────────
            # Information section: Title, Description, Phone, Location
            f'You are now on the "Complete Ad Details" form (Page 1 — "Ad Detail" step). '
            f'Under the "Information" section, click the "Title" field and type exactly: "{title}"',

            f'Click the "Description" field (textarea) and type exactly: "{description}"',
        ]

        # Phone field — pre-filled with +971 placeholder; clear and retype
        if phone:
            steps.append(
                f'Click the "Phone number" field (which shows a +971 placeholder), '
                f'select all text in it, and type: "{phone}"'
            )

        steps += [
            # Location field — has a map and a search input
            f'Under the "Location" section, click the "Location" search field '
            f'(which shows a placeholder like "Search...") and type "{location}". '
            f'Wait for autocomplete suggestions and click the first suggestion.',

            # Click Next to advance to Page 2
            'Click the green "Next" button at the bottom of the page to proceed to Page 2.',

            # ── Screen 10: "Complete Ad Details" Page 2 ──────────────────
            # Media upload (if we have images) + Required Information dropdowns
        ]

        if image_paths:
            # Only attempt upload for local file paths
            local_paths = [p for p in image_paths if p and not p.startswith("http")]
            if local_paths:
                steps.append(
                    f'You are on Page 2 (Media + Required Information). '
                    f'Under "Media", click the "Upload Video / Photo" area to open a file picker, '
                    f'then select the file: "{local_paths[0]}"'
                )

        # Required Information dropdowns — use sensible defaults if condition is known
        if condition:
            steps.append(
                f'Under "Required Information", find the "Condition" dropdown and select '
                f'the option closest to "{condition}" (e.g. "Good", "Very Good", "Scrap").'
            )

        steps += [
            # For other required dropdowns (Age, Color, etc.) just pick any default
            'Review the "Required Information" section. For any dropdown still showing '
            'a default or placeholder, leave it as-is. '
            'Click the green "Next" button to go to the Pricing / Planing step.',

            # ── Screen 11: Price ──────────────────────────────────────────
            f'You are on the Pricing step. Click the "Price" field (shows AED placeholder) '
            f'and type: "{price:.0f}"',

            # Final submit
            'Click the green "Create Ad" button to publish the listing.',
        ]

        return steps

    async def create_shozon_listing(
        self,
        listing_data: Dict[str, Any],
        price: float,
        image_urls: List[str],
        task_id: str = "",
        send_frame_callback: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        """Create a listing on Shozon (SPA) using Nova Act browser automation."""
        if not self._anti_ban.check_listing_rate_limit():
            return {
                "marketplace": "shozon",
                "listing_url": "",
                "listing_id": "",
                "status": "rate_limited",
                "automation_steps": [],
                "error_message": "Rate limit reached. Try again later.",
            }

        try:
            from nova_act import NovaAct, workflow as nova_workflow

            config = self._config
            anti_ban = self._anti_ban
            shozon_config = config.shozon

            user_data_dir = anti_ban.get_user_data_dir()
            self._cleanup_singleton_lock(user_data_dir)

            shozon_url = shozon_config.shozon_base_url
            listing_steps = self.get_shozon_listing_steps(listing_data, price, image_paths=image_urls)
            total_steps = len(listing_steps)

            loop = asyncio.get_event_loop()
            frame_count = [0]

            def _send_frame_sync(screenshot_bytes: bytes, step_num: int, step_label: str):
                if not screenshot_bytes or not send_frame_callback:
                    return
                try:
                    future = asyncio.run_coroutine_threadsafe(
                        send_frame_callback(screenshot_bytes, step_num, step_label, total_steps),
                        loop,
                    )
                    future.result(timeout=10)
                    frame_count[0] += 1
                except Exception as e:
                    logger.warning(f"Failed to stream browser frame: {e}")

            nova_ref: List[Any] = [None]

            @nova_workflow(
                workflow_definition_name=config.nova.nova_act_workflow_definition,
                model_id=config.nova.nova_act_model_id,
            )
            def run_shozon_listing():
                hitl_callbacks = self._create_hitl_callbacks(task_id, loop, nova_ref)

                nova_kwargs = {
                    # Start on about:blank — avoids NovaAct's internal 30s load timeout
                    # on Shozon's heavy SPA. We navigate manually below.
                    "starting_page": "about:blank",
                    "tty": False,
                    "user_data_dir": user_data_dir,
                    "clone_user_data_dir": False,
                }
                if hitl_callbacks:
                    nova_kwargs["human_input_callbacks"] = hitl_callbacks

                with NovaAct(**nova_kwargs) as nova:
                    nova_ref[0] = nova
                    results = []

                    # ── Navigate to Shozon with relaxed wait ──
                    # domcontentloaded fires as soon as the HTML is parsed —
                    # much faster than "load" which waits for all resources.
                    try:
                        nova.page.goto(shozon_url, wait_until="domcontentloaded", timeout=60000)
                    except Exception as e:
                        logger.warning(f"Shozon navigation warning (continuing): {e}")
                    # Then wait for the SPA JS to hydrate
                    try:
                        nova.page.wait_for_load_state("networkidle", timeout=30000)
                    except Exception:
                        pass

                    # ── Login: Nova Act clicks login button, then HITL for user ──
                    self._handle_shozon_login_sync(
                        nova, shozon_config, anti_ban, _send_frame_sync, hitl_callbacks
                    )

                    # ── Dismiss welcome/promo popup if present ──
                    anti_ban.random_delay_sync(0.5, 1.0)
                    try:
                        nova.act(
                            'If you see a welcome popup, discount modal, or any overlay '
                            '(e.g. "Welcome! Get 30% off", cookie consent, newsletter sign-up), '
                            'close it by clicking the X button or "Close" / "No thanks". '
                            'If no popup is visible, do nothing.',
                            max_steps=3,
                        )
                    except Exception:
                        pass

                    # ── Click "Post Ad" in the Shozon nav to start the wizard ──
                    anti_ban.random_delay_sync(1.0, 2.0)
                    try:
                        nova.act(
                            'Find and click the "Post Ad" button in the navigation '
                            'or header to start creating a new ad.',
                            max_steps=5,
                        )
                        _send_frame_sync(
                            nova.page.screenshot(), 0,
                            "Clicked Post Ad — wizard starting"
                        )
                    except Exception as e:
                        logger.warning(f"Click Post Ad failed: {e}")

                    # ── Execute listing steps ──
                    for i, step_instruction in enumerate(listing_steps):
                        anti_ban.random_delay_sync()

                        try:
                            result = nova.act(step_instruction, max_steps=25)
                            results.append({
                                "step": step_instruction,
                                "success": result.response is not None,
                                "response": str(result.response)[:200] if result.response else None,
                            })
                        except Exception as e:
                            results.append({
                                "step": step_instruction,
                                "success": False,
                                "error": str(e),
                            })

                        try:
                            _send_frame_sync(
                                nova.page.screenshot(), i + 1,
                                step_instruction[:80]
                            )
                        except Exception:
                            pass

                    listing_url = ""
                    try:
                        listing_url = nova.page.url
                    except Exception:
                        pass

                    return {
                        "results": results,
                        "listing_url": listing_url,
                    }

            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                workflow_result = await loop.run_in_executor(pool, run_shozon_listing)

            self._anti_ban.record_listing_created()

            logger.info(f"Listing posted to Shozon, streamed {frame_count[0]} frames")
            return {
                "marketplace": "shozon",
                "listing_url": workflow_result.get("listing_url", ""),
                "listing_id": "",
                "status": "posted",
                "screenshots": [],
                "automation_steps": workflow_result.get("results", []),
                "error_message": "",
            }

        except ImportError:
            logger.warning("Nova Act SDK not available. Using mock automation.")
            return await self._mock_shozon_listing(listing_data, price, send_frame_callback)

        except Exception as e:
            logger.error(f"Shozon automation error: {e}")
            return {
                "marketplace": "shozon",
                "listing_url": "",
                "listing_id": "",
                "status": "failed",
                "automation_steps": [],
                "error_message": str(e),
            }

    def _handle_shozon_login_sync(
        self,
        nova,
        shozon_config,
        anti_ban,
        send_frame_fn,
        hitl_callbacks=None,
    ) -> None:
        """Handle Shozon login.

        Flow:
        1. Nova Act clicks the "Login / Sign Up" button to open the login modal.
        2. Playwright fills email and password automatically.
        3. HITL: user reads the image CAPTCHA, types the security code, clicks Login.
        4. After Done, wait for SPA to settle.
        """
        import time as _time

        page = nova.page
        email = shozon_config.shozon_email
        password = shozon_config.shozon_password

        # ── Open login form ──────────────────────────────────────────────
        logger.info("Shozon: opening login form")
        try:
            send_frame_fn(page.screenshot(), 0, "Opening Shozon login...")
        except Exception:
            pass

        try:
            nova.act('Click the "Login / Sign Up" or "Login" or "Sign in" button to open the login form.')
        except Exception as e:
            logger.warning(f"Nova Act open-login failed: {e}")

        # Wait for login modal / form to render
        _time.sleep(1.5)
        try:
            page.wait_for_selector('input[type="password"]', state="visible", timeout=10000)
        except Exception:
            logger.warning("Shozon: password field not visible after clicking login")

        # ── Fill email ───────────────────────────────────────────────────
        if email:
            for sel in (
                'input[type="email"]',
                'input[name="email"]',
                'input[name="username"]',
                'input[placeholder*="email" i]',
                'input[placeholder*="Email" i]',
                # Shozon uses an icon+input layout — first text input before password
                'input[type="text"]:first-of-type',
            ):
                try:
                    page.wait_for_selector(sel, state="visible", timeout=3000)
                    page.fill(sel, email)
                    logger.info(f"Shozon email filled via: {sel}")
                    break
                except Exception:
                    pass
            _time.sleep(0.3)

        # ── Fill password ────────────────────────────────────────────────
        if password:
            try:
                page.wait_for_selector('input[type="password"]', state="visible", timeout=5000)
                page.fill('input[type="password"]', password)
                logger.info("Shozon password filled")
            except Exception as e:
                logger.warning(f"Shozon password fill failed: {e}")
            _time.sleep(0.3)

        try:
            send_frame_fn(page.screenshot(), 0, "Credentials filled — waiting for CAPTCHA solve...")
        except Exception:
            pass

        # ── HITL: user enters CAPTCHA security code and clicks Login ─────
        if hitl_callbacks is not None:
            logger.info("Shozon: triggering HITL for CAPTCHA")
            try:
                hitl_callbacks.ui_takeover(
                    "Email and password are filled. "
                    "Please enter the security code (CAPTCHA) and click Login."
                )
                logger.info("Shozon HITL completed")
            except Exception as e:
                logger.warning(f"Shozon HITL failed: {repr(e)}")
        else:
            logger.warning("Shozon: no HITL callbacks — CAPTCHA cannot be solved automatically")

        # ── Wait for SPA to settle after login ───────────────────────────
        try:
            page.wait_for_load_state("networkidle", timeout=30000)
        except Exception:
            pass
        anti_ban.page_load_delay_sync()

        logger.info(f"Shozon post-login URL: {page.url}")
        try:
            send_frame_fn(page.screenshot(), 0, "Logged in to Shozon")
        except Exception:
            pass

    async def _mock_shozon_listing(
        self,
        listing_data: Dict[str, Any],
        price: float,
        send_frame_callback: Optional[Callable],
    ) -> Dict[str, Any]:
        """Mock Shozon listing creation when Nova Act is not available."""
        from datetime import datetime

        mock_steps = [
            "Opening Shozon...",
            "Filling in listing title...",
            f"Setting price to {price:.2f}...",
            "Adding description...",
            "Submitting listing...",
        ]

        for i, label in enumerate(mock_steps):
            if send_frame_callback:
                await send_frame_callback(b"", i, label, len(mock_steps))
            await asyncio.sleep(0.5)

        return {
            "marketplace": "shozon",
            "listing_url": f"https://shozon.com/listing/mock-{datetime.utcnow().timestamp():.0f}",
            "listing_id": f"mock-{datetime.utcnow().timestamp():.0f}",
            "status": "mock_posted",
            "automation_steps": [],
            "error_message": "Nova Act SDK not installed. Mock posting used.",
        }

    # ─────────────────────────────────────────────────────────────────────
    # Facebook Marketplace
    # ─────────────────────────────────────────────────────────────────────

    def get_facebook_listing_steps(self, listing_data: Dict[str, Any], price: float) -> List[str]:
        """Generate Nova Act instruction steps for creating a Facebook Marketplace listing."""
        steps = [
            f'Click on the "What are you selling?" or title input field and type: "{listing_data.get("title", "")}"',
            f'Set the price to: {price:.2f}',
            f'Select the category: "{listing_data.get("category", "Electronics")}"',
            f'Fill in the description with: "{listing_data.get("description", "")}"',
        ]

        condition = listing_data.get("condition", "")
        if condition:
            steps.append(f'Set the condition to match: "{condition}"')

        location = listing_data.get("location", self._config.dubizzle.default_location)
        if location:
            steps.append(f'Set the location to {location} if there is a location field')

        steps.append('Click the "Next" or "Publish" button to post the listing')

        return steps

    async def create_facebook_listing(
        self,
        listing_data: Dict[str, Any],
        price: float,
        image_urls: List[str],
        task_id: str = "",
        send_frame_callback: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        """Create a listing on Facebook Marketplace using Nova Act browser automation."""
        if not self._anti_ban.check_listing_rate_limit():
            return {
                "marketplace": "facebook",
                "listing_url": "",
                "listing_id": "",
                "status": "rate_limited",
                "automation_steps": [],
                "error_message": "Rate limit reached. Try again later.",
            }

        try:
            from nova_act import NovaAct, workflow as nova_workflow

            config = self._config
            anti_ban = self._anti_ban
            fb_config = config.facebook

            user_data_dir = anti_ban.get_user_data_dir()
            has_session = anti_ban.has_saved_session()
            self._cleanup_singleton_lock(user_data_dir)

            starting_url = (
                fb_config.facebook_marketplace_create_url
                if has_session
                else fb_config.facebook_login_url
            )

            listing_steps = self.get_facebook_listing_steps(listing_data, price)
            total_steps = len(listing_steps)

            loop = asyncio.get_event_loop()
            frame_count = [0]

            def _send_frame_sync(screenshot_bytes: bytes, step_num: int, step_label: str):
                if not screenshot_bytes or not send_frame_callback:
                    return
                try:
                    future = asyncio.run_coroutine_threadsafe(
                        send_frame_callback(screenshot_bytes, step_num, step_label, total_steps),
                        loop,
                    )
                    future.result(timeout=10)
                    frame_count[0] += 1
                except Exception as e:
                    logger.warning(f"Failed to stream browser frame: {e}")

            nova_ref: List[Any] = [None]

            @nova_workflow(
                workflow_definition_name=config.nova.nova_act_workflow_definition,
                model_id=config.nova.nova_act_model_id,
            )
            def run_facebook_listing():
                hitl_callbacks = self._create_hitl_callbacks(task_id, loop, nova_ref)

                nova_kwargs = {
                    "starting_page": starting_url,
                    "tty": False,
                    "user_data_dir": user_data_dir,
                    "clone_user_data_dir": False,
                }
                if hitl_callbacks:
                    nova_kwargs["human_input_callbacks"] = hitl_callbacks

                with NovaAct(**nova_kwargs) as nova:
                    nova_ref[0] = nova
                    results = []

                    # ── Login if needed (always check — session may have expired) ──
                    self._handle_facebook_login_sync(
                        nova, fb_config, anti_ban, _send_frame_sync
                    )

                    # ── Dismiss popups ──
                    anti_ban.random_delay_sync(1.0, 2.0)
                    try:
                        nova.act(
                            'If you see any popups, overlays, or notification prompts, '
                            'dismiss them by clicking X or "Not now". Otherwise skip.'
                        )
                    except Exception as e:
                        logger.warning(f"Popup dismissal failed (non-fatal): {e}")

                    # ── Initial screenshot ──
                    try:
                        _send_frame_sync(
                            nova.page.screenshot(), 0,
                            "Ready to create listing on Facebook Marketplace"
                        )
                    except Exception:
                        pass

                    # ── Execute listing steps ──
                    for i, step_instruction in enumerate(listing_steps):
                        anti_ban.random_delay_sync()

                        try:
                            result = nova.act(step_instruction)
                            results.append({
                                "step": step_instruction,
                                "success": result.response is not None,
                                "response": str(result.response)[:200] if result.response else None,
                            })
                        except Exception as e:
                            results.append({
                                "step": step_instruction,
                                "success": False,
                                "error": str(e),
                            })

                        try:
                            _send_frame_sync(
                                nova.page.screenshot(), i + 1,
                                step_instruction[:80]
                            )
                        except Exception:
                            pass

                    listing_url = ""
                    try:
                        current_url = nova.page.url
                        if current_url and "marketplace" in current_url and current_url != starting_url:
                            listing_url = current_url
                    except Exception:
                        pass

                    return {
                        "results": results,
                        "listing_url": listing_url,
                    }

            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                workflow_result = await loop.run_in_executor(pool, run_facebook_listing)

            self._anti_ban.record_listing_created()

            logger.info(f"Listing posted to Facebook Marketplace, streamed {frame_count[0]} frames")
            return {
                "marketplace": "facebook",
                "listing_url": workflow_result.get("listing_url", ""),
                "listing_id": "",
                "status": "posted",
                "screenshots": [],
                "automation_steps": workflow_result.get("results", []),
                "error_message": "",
            }

        except ImportError:
            logger.warning("Nova Act SDK not available. Using mock automation.")
            return await self._mock_facebook_listing(listing_data, price, send_frame_callback)

        except Exception as e:
            logger.error(f"Facebook Marketplace automation error: {e}")
            return {
                "marketplace": "facebook",
                "listing_url": "",
                "listing_id": "",
                "status": "failed",
                "automation_steps": [],
                "error_message": str(e),
            }

    def _handle_facebook_login_sync(
        self,
        nova,
        fb_config,
        anti_ban,
        send_frame_fn,
    ) -> None:
        """Handle Facebook login via Playwright (not Nova Act LLM).

        Credentials are entered directly via Playwright so they never
        appear in Nova Act's LLM context. Supports TOTP 2FA via pyotp.
        """
        import time as _time

        email = fb_config.facebook_email
        password = fb_config.facebook_password

        if not email or not password:
            logger.warning("Facebook credentials not configured, skipping login")
            return

        page = nova.page

        # Wait for the page to finish loading
        try:
            page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
        _time.sleep(1.5)

        current_url = page.url or ""
        logger.info(f"Facebook login check — current URL: {current_url}")

        # Already logged in — just navigate to the create listing page
        if (
            "facebook.com" in current_url
            and "login" not in current_url
            and "checkpoint" not in current_url
            and "recover" not in current_url
            and "www.facebook.com" in current_url
        ):
            logger.info("Facebook: already authenticated, navigating to Marketplace create page")
            if "marketplace/create" not in current_url:
                try:
                    page.goto(fb_config.facebook_marketplace_create_url)
                    page.wait_for_load_state("networkidle", timeout=15000)
                except Exception as e:
                    logger.warning(f"Navigation to Marketplace create page failed: {e}")
            return

        logger.info("Facebook: login page detected, filling credentials automatically")
        send_frame_fn(page.screenshot(), 0, "Logging in to Facebook...")

        # ── Dismiss cookie/GDPR consent overlay ──────────────────────────
        for cookie_sel in (
            '[data-testid="cookie-policy-manage-dialog-accept-button"]',
            'button:has-text("Allow all cookies")',
            'button:has-text("Accept all")',
            'button:has-text("Accept")',
        ):
            try:
                btn = page.locator(cookie_sel).first
                if btn.is_visible(timeout=2000):
                    btn.click()
                    _time.sleep(0.8)
                    logger.info(f"Dismissed cookie consent: {cookie_sel}")
                    break
            except Exception:
                pass

        # ── Fill email ────────────────────────────────────────────────────
        email_filled = False
        for sel in ('#email', 'input[name="email"]', 'input[type="email"]'):
            try:
                page.wait_for_selector(sel, state="visible", timeout=8000)
                page.fill(sel, email)
                logger.info(f"Facebook email filled via selector: {sel}")
                email_filled = True
                break
            except Exception as e:
                logger.debug(f"Email selector {sel!r} failed: {e}")

        if not email_filled:
            logger.warning("Could not find Facebook email field — triggering HITL for user to log in manually")
            try:
                nova.act(
                    "The Facebook login page is open. "
                    "Use the human_UiTakeover tool so the user can log in manually."
                )
            except Exception as e:
                logger.warning(f"HITL fallback for FB login failed: {e}")
            return

        _time.sleep(0.4)

        # ── Fill password ─────────────────────────────────────────────────
        pass_filled = False
        for sel in ('#pass', 'input[name="pass"]', 'input[type="password"]'):
            try:
                page.wait_for_selector(sel, state="visible", timeout=8000)
                page.fill(sel, password)
                logger.info(f"Facebook password filled via selector: {sel}")
                pass_filled = True
                break
            except Exception as e:
                logger.debug(f"Password selector {sel!r} failed: {e}")

        if not pass_filled:
            logger.warning("Could not find Facebook password field")
            return

        _time.sleep(0.4)
        send_frame_fn(page.screenshot(), 0, "Submitting Facebook login...")

        # ── Submit ────────────────────────────────────────────────────────
        submitted = False
        for sel in ('[name="login"]', 'button[type="submit"]', '#loginbutton'):
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=2000):
                    btn.click()
                    submitted = True
                    logger.info(f"Facebook login submitted via: {sel}")
                    break
            except Exception:
                pass
        if not submitted:
            page.keyboard.press("Enter")
            logger.info("Facebook login submitted via Enter key")

        # ── Wait for post-login navigation ────────────────────────────────
        try:
            page.wait_for_load_state("networkidle", timeout=25000)
        except Exception:
            pass
        anti_ban.page_load_delay_sync()

        send_frame_fn(page.screenshot(), 0, "Post-login page...")

        # ── Handle 2FA if prompted ────────────────────────────────────────
        two_fa_secret = fb_config.facebook_2fa_secret
        if two_fa_secret:
            for sel in (
                'input[name="approvals_code"]',
                'input[aria-label*="code" i]',
                'input[placeholder*="code" i]',
                'input[placeholder*="Code"]',
            ):
                try:
                    page.wait_for_selector(sel, state="visible", timeout=8000)
                    import pyotp
                    otp_code = pyotp.TOTP(two_fa_secret).now()
                    page.fill(sel, otp_code)
                    logger.info(f"Facebook 2FA code entered via: {sel}")
                    _time.sleep(0.4)
                    page.keyboard.press("Enter")
                    try:
                        page.wait_for_load_state("networkidle", timeout=20000)
                    except Exception:
                        pass
                    anti_ban.page_load_delay_sync()
                    send_frame_fn(page.screenshot(), 0, "Post-2FA page...")
                    break
                except Exception as e:
                    logger.debug(f"2FA selector {sel!r} not found: {e}")

        logger.info(f"Facebook post-login URL: {page.url}")
        send_frame_fn(page.screenshot(), 0, "Logged in to Facebook")

        # ── Navigate to Marketplace create page ──────────────────────────
        try:
            page.goto(fb_config.facebook_marketplace_create_url)
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception as e:
            logger.warning(f"Navigation to Marketplace create page failed: {e}")
        anti_ban.page_load_delay_sync()

    async def _mock_facebook_listing(
        self,
        listing_data: Dict[str, Any],
        price: float,
        send_frame_callback: Optional[Callable],
    ) -> Dict[str, Any]:
        """Mock Facebook listing creation when Nova Act is not available."""
        from datetime import datetime

        mock_steps = [
            "Opening Facebook Marketplace...",
            "Filling in listing title...",
            f"Setting price to {price:.2f}...",
            "Adding description...",
            "Submitting listing...",
        ]

        for i, label in enumerate(mock_steps):
            if send_frame_callback:
                await send_frame_callback(b"", i, label, len(mock_steps))
            await asyncio.sleep(0.5)

        return {
            "marketplace": "facebook",
            "listing_url": f"https://www.facebook.com/marketplace/item/mock-{datetime.utcnow().timestamp():.0f}",
            "listing_id": f"mock-{datetime.utcnow().timestamp():.0f}",
            "status": "mock_posted",
            "automation_steps": [],
            "error_message": "Nova Act SDK not installed. Mock posting used.",
        }

    # ─────────────────────────────────────────────────────────────────────
    # Internal: HITL Callbacks
    # ─────────────────────────────────────────────────────────────────────

    def _create_hitl_callbacks(self, task_id: str, loop, nova_ref: List[Any]):
        """Create HITL callbacks for CAPTCHA handling.

        Returns None if Nova Act is not available.
        nova_ref is a mutable list [None] that will be populated with the
        NovaAct instance once the context manager starts, giving the HITL
        callback access to the live page for screenshots and CDP dispatch.
        """
        if not task_id:
            logger.warning("HITL callbacks NOT created: task_id is empty")
            return None

        try:
            from nova_act.tools.human.interface.human_input_callback import (
                ApprovalResponse,
                HumanInputCallbacksBase,
                UiTakeoverResponse,
            )
        except ImportError as e:
            logger.warning(f"HITL callbacks NOT created: ImportError — {e}")
            return None

        logger.info(f"HITL callbacks created for task_id={task_id!r}")

        class NovaSellHITLCallbacks(HumanInputCallbacksBase):
            """Hands browser control to the user via CDP for CAPTCHA solving."""

            def __init__(self, nova_ref_ref, task_id_ref, loop_ref):
                super().__init__()
                self._nova_ref = nova_ref_ref
                self._task_id = task_id_ref
                self._loop = loop_ref

            def _send_chat_message(self, text: str):
                if not self._task_id:
                    return
                try:
                    from agentex.lib import adk
                    from agentex.types.text_content import TextContent
                    future = asyncio.run_coroutine_threadsafe(
                        adk.messages.create(
                            task_id=self._task_id,
                            content=TextContent(author="agent", content=text),
                        ),
                        self._loop,
                    )
                    future.result(timeout=15)
                except Exception as e:
                    logger.warning(f"HITL chat message failed: {repr(e)}")

            def _send_data_message(self, data: dict):
                """Fire-and-forget — never blocks the screenshot loop.

                We intentionally do NOT call future.result() here.
                Waiting for each HTTP ack was causing concurrent.futures.TimeoutError
                (which has empty str()) and blocking the 1-fps screenshot stream.
                """
                if not self._task_id:
                    return
                try:
                    from agentex.lib import adk
                    from agentex.types.data_content import DataContent
                    asyncio.run_coroutine_threadsafe(
                        adk.messages.create(
                            task_id=self._task_id,
                            content=DataContent(author="agent", data=data),
                        ),
                        self._loop,
                    )
                    # Do not call .result() — fire and forget
                except Exception as e:
                    logger.warning(f"HITL data message schedule failed: {repr(e)}")

            def approve(self, message: str) -> ApprovalResponse:
                logger.info(f"HITL approve: {message}")
                self._send_chat_message(
                    f"**Approval Required:** {message}\n\nAuto-approving to continue."
                )
                return ApprovalResponse.YES

            def ui_takeover(self, message: str) -> UiTakeoverResponse:
                """Stream live screenshots to chat and relay user clicks/keys to the browser."""
                import time as _time
                logger.warning(f"HITL ui_takeover triggered: {message}")

                nova_instance = self._nova_ref[0]
                if nova_instance is None:
                    logger.error("HITL ui_takeover: nova_ref[0] is None — cannot stream browser")
                    self._send_chat_message(
                        f"**Browser Control Required:** {message}\n\n"
                        f"Please wait for the browser to initialize, then click Done."
                    )
                    # Fall back to waiting without live stream
                    event = threading.Event()
                    cmd_queue = queue.Queue()
                    _ui_takeover_events[self._task_id] = event
                    _ui_takeover_commands[self._task_id] = cmd_queue
                    try:
                        event.wait(timeout=300)
                        result = _ui_takeover_results.pop(self._task_id, "cancel")
                        return UiTakeoverResponse.COMPLETE if result == "done" else UiTakeoverResponse.CANCEL
                    finally:
                        _ui_takeover_events.pop(self._task_id, None)
                        _ui_takeover_results.pop(self._task_id, None)
                        _ui_takeover_commands.pop(self._task_id, None)

                page = nova_instance.page
                viewport = page.viewport_size or {"width": 1280, "height": 720}

                # Set up signaling and command queue
                event = threading.Event()
                cmd_queue = queue.Queue()
                _ui_takeover_events[self._task_id] = event
                _ui_takeover_commands[self._task_id] = cmd_queue

                # Create a persistent CDP session for input dispatch
                cdp_session = None
                try:
                    cdp_session = page.context.new_cdp_session(page)
                    logger.info("CDP session created for UI takeover input dispatch")
                except Exception as e:
                    logger.warning(f"Failed to create CDP session: {e}")

                self._send_chat_message(
                    f"**Browser Control Required:** {message}\n\n"
                    f"Click directly on the browser view below to interact. "
                    f"Type using your keyboard. Click **Done** when finished."
                )

                # Query actual page layout dimensions (may differ from Playwright viewport
                # if the page uses a viewport meta tag or CSS that sets a wider layout)
                try:
                    page_dims = page.evaluate("() => ({ w: window.innerWidth, h: window.innerHeight })")
                    layout_width = page_dims["w"]
                    layout_height = page_dims["h"]
                except Exception:
                    layout_width = viewport["width"]
                    layout_height = viewport["height"]

                logger.info(
                    f"UI takeover started: playwright_viewport={viewport['width']}x{viewport['height']}, "
                    f"page_layout={layout_width}x{layout_height}"
                )

                needs_immediate_screenshot = False

                try:
                    while not event.is_set():
                        # Process queued commands from user clicks/keys
                        while not cmd_queue.empty():
                            try:
                                cmd = cmd_queue.get_nowait()
                                action = cmd.get("action")
                                if action == "click":
                                    norm_x = cmd["x"]
                                    norm_y = cmd["y"]
                                    abs_x = norm_x * layout_width
                                    abs_y = norm_y * layout_height

                                    logger.info(
                                        f"UI takeover click: norm=({norm_x:.4f},{norm_y:.4f}) "
                                        f"abs=({abs_x:.1f},{abs_y:.1f}) "
                                        f"layout={layout_width}x{layout_height}"
                                    )

                                    if cdp_session:
                                        try:
                                            cdp_session.send("Input.dispatchMouseEvent", {
                                                "type": "mouseMoved",
                                                "x": abs_x,
                                                "y": abs_y,
                                            })
                                            _time.sleep(0.05)
                                            cdp_session.send("Input.dispatchMouseEvent", {
                                                "type": "mousePressed",
                                                "x": abs_x,
                                                "y": abs_y,
                                                "button": "left",
                                                "clickCount": 1,
                                            })
                                            _time.sleep(0.1)
                                            cdp_session.send("Input.dispatchMouseEvent", {
                                                "type": "mouseReleased",
                                                "x": abs_x,
                                                "y": abs_y,
                                                "button": "left",
                                                "clickCount": 1,
                                            })
                                            logger.info(f"UI takeover CDP click dispatched: ({abs_x:.0f}, {abs_y:.0f})")
                                        except Exception as cdp_err:
                                            logger.warning(f"CDP click failed ({cdp_err}), falling back to page.mouse")
                                            page.mouse.click(abs_x, abs_y)
                                    else:
                                        page.mouse.click(abs_x, abs_y)
                                        logger.info(f"UI takeover mouse click: ({abs_x:.0f}, {abs_y:.0f})")

                                    needs_immediate_screenshot = True
                                elif action == "type":
                                    page.keyboard.type(cmd["text"])
                                    needs_immediate_screenshot = True
                                elif action == "key":
                                    page.keyboard.press(cmd["key"])
                                    needs_immediate_screenshot = True
                            except queue.Empty:
                                break
                            except Exception as e:
                                logger.warning(f"UI takeover command error: {e}")

                        # Brief delay after commands to let the page render the effect
                        if needs_immediate_screenshot:
                            _time.sleep(0.3)
                            needs_immediate_screenshot = False

                        # Take screenshot and stream to chat (~1 FPS).
                        # Use CDP Page.captureScreenshot (via existing cdp_session)
                        # which does NOT wait for fonts to load — avoiding the
                        # 5-30s Playwright timeout that fires on CAPTCHA pages.
                        # Fall back to page.screenshot() if CDP is unavailable.
                        screenshot_bytes = None
                        if cdp_session:
                            try:
                                result = cdp_session.send("Page.captureScreenshot", {
                                    "format": "jpeg",
                                    "quality": 60,
                                })
                                screenshot_bytes = base64.b64decode(result["data"])
                            except Exception as e:
                                logger.debug(f"CDP screenshot failed, trying Playwright: {repr(e)}")
                        if not screenshot_bytes:
                            try:
                                screenshot_bytes = page.screenshot(
                                    type="jpeg", quality=60, timeout=3000
                                )
                            except Exception as e:
                                logger.debug(f"UI takeover screenshot skipped: {repr(e)}")
                        if screenshot_bytes:
                            self._send_data_message({
                                "type": "browser_takeover",
                                "image_base64": base64.b64encode(screenshot_bytes).decode("utf-8"),
                                "viewport_width": viewport["width"],
                                "viewport_height": viewport["height"],
                                "active": True,
                            })

                        event.wait(timeout=1.0)

                    # User signaled done or cancel
                    result = _ui_takeover_results.pop(self._task_id, "cancel")
                    logger.info(f"UI takeover user signal: {result}")

                    # Send final inactive frame
                    final_bytes = None
                    if cdp_session:
                        try:
                            r = cdp_session.send("Page.captureScreenshot", {"format": "jpeg", "quality": 60})
                            final_bytes = base64.b64decode(r["data"])
                        except Exception:
                            pass
                    if not final_bytes:
                        try:
                            final_bytes = page.screenshot(type="jpeg", quality=60, timeout=3000)
                        except Exception:
                            pass
                    if final_bytes:
                        self._send_data_message({
                            "type": "browser_takeover",
                            "image_base64": base64.b64encode(final_bytes).decode("utf-8"),
                            "viewport_width": viewport["width"],
                            "viewport_height": viewport["height"],
                            "active": False,
                        })

                    if result == "done":
                        self._send_chat_message("Browser control returned. Resuming automation...")
                        return UiTakeoverResponse.COMPLETE
                    else:
                        self._send_chat_message("UI takeover cancelled. Skipping...")
                        return UiTakeoverResponse.CANCEL
                finally:
                    _ui_takeover_events.pop(self._task_id, None)
                    _ui_takeover_results.pop(self._task_id, None)
                    _ui_takeover_commands.pop(self._task_id, None)
                    if cdp_session:
                        try:
                            cdp_session.detach()
                        except Exception:
                            pass

        return NovaSellHITLCallbacks(nova_ref, task_id, loop)

    # ─────────────────────────────────────────────────────────────────────
    # Internal: Mock automation
    # ─────────────────────────────────────────────────────────────────────

    async def _mock_listing(
        self,
        listing_data: Dict[str, Any],
        price: float,
        send_frame_callback: Optional[Callable],
    ) -> Dict[str, Any]:
        """Mock listing creation when Nova Act is not available."""
        from datetime import datetime

        mock_steps = [
            "Opening Dubizzle...",
            "Filling in listing title...",
            f"Setting price to {price:.2f} AED...",
            "Adding description...",
            "Submitting listing...",
        ]

        for i, label in enumerate(mock_steps):
            if send_frame_callback:
                await send_frame_callback(b"", i, label, len(mock_steps))
            await asyncio.sleep(0.5)

        return {
            "marketplace": "dubizzle",
            "listing_url": f"https://dubai.dubizzle.com/listing/mock-{datetime.utcnow().timestamp():.0f}",
            "listing_id": f"mock-{datetime.utcnow().timestamp():.0f}",
            "status": "mock_posted",
            "automation_steps": [],
            "error_message": "Nova Act SDK not installed. Mock posting used.",
        }


# ─────────────────────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────────────────────

_service: Optional[DubizzleBrowserAutomation] = None


def get_browser_automation() -> DubizzleBrowserAutomation:
    """Get or create the global browser automation service."""
    global _service
    if _service is None:
        _service = DubizzleBrowserAutomation()
    return _service