"""Anti-Ban Strategy — prevents Dubizzle bot detection.

Implements human-like browsing patterns:
- Random delays between actions
- Human-like typing speed
- Session reuse with persistent cookies
- Rate limiting for listings and messages
- Browser fingerprint management
- Page load waiting
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import datetime, timezone
from typing import Optional

from project.config import get_config
from project.services.memory_store import get_memory_store

logger = logging.getLogger(__name__)


class AntiBanService:
    """Service to prevent bot detection on Dubizzle.

    Strategies:
    1. Random delays between browser actions (1-3s)
    2. Human-like typing with variable speed
    3. Session/cookie reuse to avoid repeated logins
    4. Rate limiting: max N listings per hour/day
    5. Random mouse movements and scroll patterns
    6. Viewport and user-agent management
    """

    def __init__(self):
        self._config = get_config().anti_ban
        self._memory = get_memory_store()
        self._last_action_time: float = 0.0

    # ─────────────────────────────────────────────────────────────────────
    # Delay Management
    # ─────────────────────────────────────────────────────────────────────

    async def random_delay(
        self,
        min_seconds: Optional[float] = None,
        max_seconds: Optional[float] = None,
    ) -> float:
        """Wait a random duration to simulate human behavior.

        Args:
            min_seconds: Minimum delay (defaults to config)
            max_seconds: Maximum delay (defaults to config)

        Returns:
            Actual delay in seconds
        """
        min_s = min_seconds or self._config.min_action_delay
        max_s = max_seconds or self._config.max_action_delay
        delay = random.uniform(min_s, max_s)
        await asyncio.sleep(delay)
        self._last_action_time = time.time()
        return delay

    def random_delay_sync(
        self,
        min_seconds: Optional[float] = None,
        max_seconds: Optional[float] = None,
    ) -> float:
        """Synchronous version of random_delay for use in Nova Act threads.

        Args:
            min_seconds: Minimum delay
            max_seconds: Maximum delay

        Returns:
            Actual delay in seconds
        """
        min_s = min_seconds or self._config.min_action_delay
        max_s = max_seconds or self._config.max_action_delay
        delay = random.uniform(min_s, max_s)
        time.sleep(delay)
        self._last_action_time = time.time()
        return delay

    async def page_load_delay(self) -> float:
        """Wait for page to fully load with human-like patience."""
        delay = self._config.page_load_wait + random.uniform(0.5, 1.5)
        await asyncio.sleep(delay)
        return delay

    def page_load_delay_sync(self) -> float:
        """Synchronous page load delay."""
        delay = self._config.page_load_wait + random.uniform(0.5, 1.5)
        time.sleep(delay)
        return delay

    # ─────────────────────────────────────────────────────────────────────
    # Human-like Typing
    # ─────────────────────────────────────────────────────────────────────

    def get_typing_delays(self, text: str) -> list[float]:
        """Generate human-like typing delays for each character.

        Simulates natural typing patterns:
        - Variable speed per character
        - Slightly longer pauses after spaces and punctuation
        - Occasional brief pauses (thinking)

        Args:
            text: Text to type

        Returns:
            List of delays (one per character)
        """
        delays = []
        min_d = self._config.min_typing_delay
        max_d = self._config.max_typing_delay

        for i, char in enumerate(text):
            base_delay = random.uniform(min_d, max_d)

            # Longer pause after punctuation
            if char in ".!?,;:":
                base_delay *= random.uniform(2.0, 4.0)
            # Slight pause after spaces
            elif char == " ":
                base_delay *= random.uniform(1.2, 2.0)
            # Occasional thinking pause (5% chance)
            elif random.random() < 0.05:
                base_delay += random.uniform(0.3, 0.8)

            delays.append(base_delay)

        return delays

    def type_with_delays_sync(self, page, text: str) -> None:
        """Type text with human-like delays using Playwright page.keyboard.

        Args:
            page: Playwright page object
            text: Text to type
        """
        delays = self.get_typing_delays(text)
        for char, delay in zip(text, delays):
            page.keyboard.type(char)
            time.sleep(delay)

    # ─────────────────────────────────────────────────────────────────────
    # Rate Limiting
    # ─────────────────────────────────────────────────────────────────────

    def check_listing_rate_limit(self) -> bool:
        """Check if we can create a new listing within rate limits.

        Returns:
            True if allowed, False if rate limited
        """
        # Check hourly limit
        hourly_ok = self._memory.check_rate_limit(
            "listing_created",
            self._config.max_listings_per_hour,
            window_seconds=3600,
        )
        if not hourly_ok:
            logger.warning(
                f"Hourly listing rate limit reached "
                f"({self._config.max_listings_per_hour}/hour)"
            )
            return False

        # Check daily limit
        daily_ok = self._memory.check_rate_limit(
            "listing_created",
            self._config.max_listings_per_day,
            window_seconds=86400,
        )
        if not daily_ok:
            logger.warning(
                f"Daily listing rate limit reached "
                f"({self._config.max_listings_per_day}/day)"
            )
            return False

        return True

    def check_message_rate_limit(self) -> bool:
        """Check if we can send a message within rate limits.

        Returns:
            True if allowed, False if rate limited
        """
        return self._memory.check_rate_limit(
            "message_sent",
            self._config.max_messages_per_minute,
            window_seconds=60,
        )

    def record_listing_created(self) -> None:
        """Record that a listing was created (for rate limiting)."""
        self._memory.record_action("listing_created")

    def record_message_sent(self) -> None:
        """Record that a message was sent (for rate limiting)."""
        self._memory.record_action("message_sent")

    # ─────────────────────────────────────────────────────────────────────
    # Session Management
    # ─────────────────────────────────────────────────────────────────────

    def get_user_data_dir(self) -> str:
        """Get the persistent browser profile directory for session reuse."""
        import os
        user_data_dir = self._config.user_data_dir
        os.makedirs(user_data_dir, exist_ok=True)
        return user_data_dir

    def has_saved_session(self) -> bool:
        """Check if a saved browser session exists (cookies/profile)."""
        import os
        user_data_dir = self._config.user_data_dir
        if not os.path.isdir(user_data_dir):
            return False
        contents = [
            f for f in os.listdir(user_data_dir)
            if f not in (".", "..")
        ]
        return len(contents) > 0

    # ─────────────────────────────────────────────────────────────────────
    # Browser Fingerprint
    # ─────────────────────────────────────────────────────────────────────

    def get_viewport_size(self) -> dict[str, int]:
        """Get viewport size with slight randomization."""
        width = self._config.viewport_width + random.randint(-20, 20)
        height = self._config.viewport_height + random.randint(-10, 10)
        return {"width": width, "height": height}

    def get_user_agent(self) -> str:
        """Get user agent string (custom or default Chrome)."""
        if self._config.user_agent:
            return self._config.user_agent
        # Default: recent Chrome on macOS
        return (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )

    # ─────────────────────────────────────────────────────────────────────
    # Human-like Mouse Movements
    # ─────────────────────────────────────────────────────────────────────

    def simulate_mouse_movement_sync(self, page, target_x: float, target_y: float) -> None:
        """Simulate human-like mouse movement to a target position.

        Moves the mouse in small incremental steps with slight randomization
        to avoid detection of instant teleportation.

        Args:
            page: Playwright page object
            target_x: Target X coordinate
            target_y: Target Y coordinate
        """
        try:
            # Get current viewport for starting position estimation
            viewport = page.viewport_size or {"width": 1280, "height": 720}
            current_x = random.uniform(0, viewport["width"])
            current_y = random.uniform(0, viewport["height"])

            # Number of intermediate steps
            steps = random.randint(3, 7)
            for i in range(steps):
                progress = (i + 1) / steps
                # Add slight curve/randomness to path
                jitter_x = random.uniform(-10, 10) * (1 - progress)
                jitter_y = random.uniform(-10, 10) * (1 - progress)
                intermediate_x = current_x + (target_x - current_x) * progress + jitter_x
                intermediate_y = current_y + (target_y - current_y) * progress + jitter_y

                page.mouse.move(intermediate_x, intermediate_y)
                time.sleep(random.uniform(0.02, 0.08))

            # Final precise move to target
            page.mouse.move(target_x, target_y)
            time.sleep(random.uniform(0.1, 0.3))

        except Exception as e:
            logger.debug(f"Mouse movement simulation failed: {e}")

    def random_scroll_sync(self, page) -> None:
        """Perform a random scroll to simulate human browsing.

        Args:
            page: Playwright page object
        """
        try:
            scroll_amount = random.randint(100, 400)
            direction = random.choice(["up", "down"])
            if direction == "up":
                scroll_amount = -scroll_amount

            page.mouse.wheel(0, scroll_amount)
            time.sleep(random.uniform(0.5, 1.5))
        except Exception as e:
            logger.debug(f"Random scroll failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────────────────────

_service: Optional[AntiBanService] = None


def get_anti_ban() -> AntiBanService:
    """Get or create the global anti-ban service."""
    global _service
    if _service is None:
        _service = AntiBanService()
    return _service