"""Notification Service — alerts for HITL, escalations, and status updates.

Supports:
- Slack webhook notifications
- Email notifications (SMTP)
- In-app chat notifications (via AgentEx ADK)
- HITL request notifications
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx

from project.config import get_config
from project.models.conversation import HITLAction, HITLRequest

logger = logging.getLogger(__name__)


class NotificationService:
    """Service for sending notifications across channels."""

    def __init__(self):
        self._config = get_config().notifications

    async def notify_captcha_required(
        self,
        task_id: str,
        listing_title: str = "",
    ) -> None:
        """Notify that a CAPTCHA needs human solving.

        Args:
            task_id: Task ID for the workflow
            listing_title: Title of the listing being posted
        """
        message = (
            f"🔒 *CAPTCHA Required*\n"
            f"A CAPTCHA was detected while posting: *{listing_title}*\n"
            f"Task: `{task_id}`\n"
            f"Please solve it in the browser view."
        )
        await self._send_slack(message)
        await self._send_email(
            subject=f"NovaSell: CAPTCHA Required - {listing_title}",
            body=message,
        )

    async def notify_hitl_required(
        self,
        request: HITLRequest,
        listing_title: str = "",
    ) -> None:
        """Notify that human intervention is needed.

        Args:
            request: The HITL request details
            listing_title: Title of the related listing
        """
        action_labels = {
            HITLAction.SOLVE_CAPTCHA: "🔒 Solve CAPTCHA",
            HITLAction.APPROVE_NEGOTIATION: "💰 Approve Negotiation",
            HITLAction.REVIEW_BUYER: "👤 Review Suspicious Buyer",
            HITLAction.CONFIRM_PAYMENT: "💳 Confirm Payment",
            HITLAction.TAKE_OVER_CALL: "📞 Take Over Call",
            HITLAction.APPROVE_LISTING: "📋 Approve Listing",
            HITLAction.MANUAL_OVERRIDE: "🔧 Manual Override",
        }

        action_label = action_labels.get(request.action, "⚠️ Action Required")

        message = (
            f"{action_label}\n"
            f"Listing: *{listing_title}*\n"
            f"Reason: {request.reason}\n"
            f"Request ID: `{request.request_id}`\n"
            f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
        )
        await self._send_slack(message)

    async def notify_listing_published(
        self,
        listing_title: str,
        listing_url: str,
        price: float,
        marketplace: str = "Dubizzle",
    ) -> None:
        """Notify that a listing was successfully published.

        Args:
            listing_title: Title of the listing
            listing_url: URL of the published listing
            price: Listing price
            marketplace: Marketplace name
        """
        message = (
            f"🎉 *Listing Published on {marketplace}*\n"
            f"Title: *{listing_title}*\n"
            f"Price: {price:.2f} AED\n"
            f"URL: {listing_url}"
        )
        await self._send_slack(message)

    async def notify_negotiation_escalation(
        self,
        listing_title: str,
        buyer_offer: float,
        min_price: float,
        buyer_id: str = "",
    ) -> None:
        """Notify about a negotiation that needs human review.

        Args:
            listing_title: Title of the listing
            buyer_offer: The buyer's offer
            min_price: Minimum acceptable price
            buyer_id: Buyer identifier
        """
        message = (
            f"💰 *Negotiation Escalation*\n"
            f"Listing: *{listing_title}*\n"
            f"Buyer offer: {buyer_offer:.2f} AED\n"
            f"Min acceptable: {min_price:.2f} AED\n"
            f"Buyer: `{buyer_id}`\n"
            f"Action needed: Review and decide."
        )
        await self._send_slack(message)

    async def notify_suspicious_buyer(
        self,
        listing_title: str,
        buyer_id: str,
        reason: str,
    ) -> None:
        """Notify about a suspicious buyer interaction.

        Args:
            listing_title: Title of the listing
            buyer_id: Buyer identifier
            reason: Why the buyer is flagged
        """
        message = (
            f"⚠️ *Suspicious Buyer Detected*\n"
            f"Listing: *{listing_title}*\n"
            f"Buyer: `{buyer_id}`\n"
            f"Reason: {reason}\n"
            f"Please review the conversation."
        )
        await self._send_slack(message)

    async def notify_item_sold(
        self,
        listing_title: str,
        final_price: float,
        buyer_id: str = "",
    ) -> None:
        """Notify that an item was sold.

        Args:
            listing_title: Title of the listing
            final_price: Final sale price
            buyer_id: Buyer identifier
        """
        message = (
            f"🎊 *Item Sold!*\n"
            f"Title: *{listing_title}*\n"
            f"Final Price: {final_price:.2f} AED\n"
            f"Buyer: `{buyer_id or 'Unknown'}`"
        )
        await self._send_slack(message)

    async def notify_call_received(
        self,
        listing_title: str,
        caller_phone: str,
        session_id: str,
    ) -> None:
        """Notify about an incoming call.

        Args:
            listing_title: Title of the listing
            caller_phone: Caller's phone number
            session_id: Voice session ID
        """
        message = (
            f"📞 *Incoming Call*\n"
            f"Listing: *{listing_title}*\n"
            f"Caller: {caller_phone}\n"
            f"Session: `{session_id}`\n"
            f"AI agent is handling the call."
        )
        await self._send_slack(message)

    async def notify_error(
        self,
        error_type: str,
        error_message: str,
        task_id: str = "",
    ) -> None:
        """Notify about a system error.

        Args:
            error_type: Type of error
            error_message: Error details
            task_id: Related task ID
        """
        message = (
            f"❌ *Error: {error_type}*\n"
            f"Message: {error_message}\n"
            f"Task: `{task_id}`\n"
            f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
        )
        await self._send_slack(message)

    # ─────────────────────────────────────────────────────────────────────
    # Internal: Channel Implementations
    # ─────────────────────────────────────────────────────────────────────

    async def _send_slack(self, message: str) -> bool:
        """Send a notification to Slack via webhook.

        Args:
            message: Slack-formatted message

        Returns:
            True if sent successfully
        """
        webhook_url = self._config.slack_webhook_url
        if not webhook_url:
            logger.debug(f"Slack notification (no webhook configured): {message[:100]}")
            return False

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    webhook_url,
                    json={
                        "channel": self._config.slack_channel,
                        "text": message,
                        "username": "NovaSell Bot",
                        "icon_emoji": ":robot_face:",
                    },
                    timeout=10.0,
                )
                if response.status_code == 200:
                    logger.info("Slack notification sent")
                    return True
                else:
                    logger.warning(
                        f"Slack notification failed: {response.status_code}"
                    )
                    return False
        except Exception as e:
            logger.error(f"Slack notification error: {e}")
            return False

    async def _send_email(
        self,
        subject: str,
        body: str,
    ) -> bool:
        """Send an email notification.

        Args:
            subject: Email subject
            body: Email body

        Returns:
            True if sent successfully
        """
        if not self._config.smtp_host or not self._config.notification_email:
            logger.debug(f"Email notification (not configured): {subject}")
            return False

        try:
            import smtplib
            from email.mime.text import MIMEText

            msg = MIMEText(body)
            msg["Subject"] = subject
            msg["From"] = self._config.smtp_user
            msg["To"] = self._config.notification_email

            with smtplib.SMTP(
                self._config.smtp_host, self._config.smtp_port
            ) as server:
                server.starttls()
                server.login(
                    self._config.smtp_user, self._config.smtp_password
                )
                server.send_message(msg)

            logger.info(f"Email notification sent: {subject}")
            return True
        except Exception as e:
            logger.error(f"Email notification error: {e}")
            return False


# ─────────────────────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────────────────────

_service: Optional[NotificationService] = None


def get_notification_service() -> NotificationService:
    """Get or create the global notification service."""
    global _service
    if _service is None:
        _service = NotificationService()
    return _service