"""Main workflow for NovaSell — Autonomous AI Dubizzle Selling Agent.

Orchestrates the complete selling lifecycle:
Image → Detection → Pricing → Listing → Approval → Publishing → Active Management

Powered by AWS Nova AI models:
- Nova Lite: Object detection (multimodal)
- Nova Pro: Reasoning, conversation, negotiation
- Nova Sonic: Voice calls (speech-to-speech)
- Nova Act: Browser automation (Dubizzle)
"""
from __future__ import annotations

import asyncio
import os
import re
from typing import Any, override

from temporalio import workflow

from agentex.lib import adk
from agentex.lib.core.temporal.types.workflow import SignalName
from agentex.lib.core.temporal.workflows.workflow import BaseWorkflow
from agentex.lib.environment_variables import EnvironmentVariables
from agentex.lib.sdk.state_machine.state import State
from agentex.lib.types.acp import CreateTaskParams, SendEventParams
from agentex.lib.utils.logging import make_logger
from agentex.types.data_content import DataContent
from agentex.types.text_content import TextContent

from project.services.browser_automation import (
    signal_ui_takeover_complete,
    relay_ui_takeover_command,
)
from project.state_machines.novasell_agent import (
    NovaSellData,
    NovaSellState,
    NovaSellStateMachine,
)
from project.workflows.sell.waiting_for_image import WaitingForImageWorkflow
from project.workflows.sell.object_detection import ObjectDetectionWorkflow
from project.workflows.sell.pricing import PricingWorkflow
from project.workflows.sell.listing_generation import ListingGenerationWorkflow
from project.workflows.sell.awaiting_approval import AwaitingApprovalWorkflow
from project.workflows.sell.publishing import PublishingWorkflow
from project.workflows.sell.active_listing import ActiveListingWorkflow
from project.workflows.terminal_states import (
    SoldWorkflow,
    CompletedWorkflow,
    FailedWorkflow,
    CancelledWorkflow,
)

# Access control
ALLOWED_EMAILS = [
    email.strip()
    for email in os.getenv("ALLOWED_EMAILS", "").split(",")
    if email.strip()
]

environment_variables = EnvironmentVariables.refresh()

if environment_variables.WORKFLOW_NAME is None:
    raise ValueError("Environment variable WORKFLOW_NAME is not set")

if environment_variables.AGENT_NAME is None:
    raise ValueError("Environment variable AGENT_NAME is not set")

logger = make_logger(__name__)

# Regex patterns for image detection in messages
_DATA_URL_RE = re.compile(
    r"data:image/[a-zA-Z]+;base64,([A-Za-z0-9+/=\s]+)", re.DOTALL
)
_RAW_BASE64_RE = re.compile(
    r"^(/9j/|iVBOR|R0lGOD)[A-Za-z0-9+/=\s]{100,}$", re.DOTALL
)


def _extract_image_and_text(message: Any) -> tuple[str, str, str]:
    """Extract image_base64, image_url, and text content from event content.

    Supports:
    - DataContent with image_base64 / image_url fields
    - TextContent with embedded base64 data URL
    - TextContent with raw base64 image data

    Returns:
        (image_base64, image_url, text_content)
    """
    image_base64 = ""
    image_url = ""
    text_content = ""

    if message is None:
        return image_base64, image_url, text_content

    msg_type = getattr(message, "type", None)

    # DataContent: structured data with explicit fields
    if msg_type == "data":
        data = getattr(message, "data", {}) or {}
        image_base64 = str(data.get("image_base64", ""))
        image_url = str(data.get("image_url", ""))
        user_hints = str(data.get("user_hints", ""))
        text_content = user_hints or str(data.get("message", ""))
        return image_base64, image_url, text_content

    # TextContent: plain text, may contain embedded base64
    if hasattr(message, "content"):
        raw = getattr(message, "content", "")
        if isinstance(raw, str):
            raw = raw.strip()

            m = _DATA_URL_RE.search(raw)
            if m:
                image_base64 = m.group(1).replace("\n", "").replace(" ", "")
                text_content = _DATA_URL_RE.sub("", raw).strip()
                return image_base64, image_url, text_content

            if _RAW_BASE64_RE.match(raw):
                image_base64 = raw.replace("\n", "").replace(" ", "")
                return image_base64, image_url, ""

            text_content = raw

    return image_base64, image_url, text_content


@workflow.defn(name=environment_variables.WORKFLOW_NAME)
class NovaSellWorkflow(BaseWorkflow):
    """NovaSell — Autonomous AI Dubizzle Selling Agent Workflow.

    State machine flow:
        WAITING_FOR_IMAGE → OBJECT_DETECTION → PRICING → LISTING_GENERATION
        → AWAITING_APPROVAL → PUBLISHING → ACTIVE_LISTING → SOLD/COMPLETED

    Active listing sub-flows:
        HANDLING_CHAT, NEGOTIATING, HANDLING_VOICE, SCHEDULING
    """

    def __init__(self):
        super().__init__(display_name=environment_variables.AGENT_NAME)

        self.state_machine = NovaSellStateMachine(
            initial_state=NovaSellState.WAITING_FOR_IMAGE,
            states=[
                State(name=NovaSellState.WAITING_FOR_IMAGE, workflow=WaitingForImageWorkflow()),
                State(name=NovaSellState.OBJECT_DETECTION, workflow=ObjectDetectionWorkflow()),
                State(name=NovaSellState.PRICING, workflow=PricingWorkflow()),
                State(name=NovaSellState.LISTING_GENERATION, workflow=ListingGenerationWorkflow()),
                State(name=NovaSellState.AWAITING_APPROVAL, workflow=AwaitingApprovalWorkflow()),
                State(name=NovaSellState.PUBLISHING, workflow=PublishingWorkflow()),
                State(name=NovaSellState.ACTIVE_LISTING, workflow=ActiveListingWorkflow()),
                State(name=NovaSellState.SOLD, workflow=SoldWorkflow()),
                State(name=NovaSellState.COMPLETED, workflow=CompletedWorkflow()),
                State(name=NovaSellState.FAILED, workflow=FailedWorkflow()),
                State(name=NovaSellState.CANCELLED, workflow=CancelledWorkflow()),
            ],
            state_machine_data=NovaSellData(),
            trace_transitions=True,
        )

    @override
    @workflow.signal(name=SignalName.RECEIVE_EVENT)
    async def on_task_event_send(self, params: SendEventParams) -> None:
        """Handle incoming user messages from the chat UI.

        Supports:
        - Text commands (approve, cancel, sold, status, etc.)
        - Image uploads (base64 or URL)
        - Browser HITL signals (click, type, key, done/cancel)
        """
        state_data = self.state_machine.get_state_machine_data()
        task = params.task
        message = params.event.content

        image_base64, image_url, text_content = _extract_image_and_text(message)

        # ── HITL Browser Control Signals ──
        if text_content in ("ui_takeover_done", "ui_takeover_cancel"):
            result = "done" if text_content == "ui_takeover_done" else "cancel"
            logger.info(f"UI takeover signal: {result} for task {task.id}")
            signal_ui_takeover_complete(task.id, result)
            return

        if text_content.startswith("browser_click:"):
            try:
                coords = text_content[len("browser_click:"):].split(",")
                relay_ui_takeover_command(task.id, {
                    "action": "click",
                    "x": float(coords[0]),
                    "y": float(coords[1]),
                })
            except (ValueError, IndexError) as e:
                logger.warning(f"Invalid browser_click: {e}")
            return

        if text_content.startswith("browser_type:"):
            relay_ui_takeover_command(task.id, {
                "action": "type", "text": text_content[len("browser_type:"):]
            })
            return

        if text_content.startswith("browser_key:"):
            relay_ui_takeover_command(task.id, {
                "action": "key", "key": text_content[len("browser_key:"):]
            })
            return

        logger.info(
            f"Event received: text={text_content[:80]!r}, "
            f"has_image={bool(image_base64)}, has_url={bool(image_url)}"
        )

        # Store image data
        if image_base64:
            state_data.image_base64 = image_base64
        if image_url:
            state_data.image_url = image_url

        # Extract extra fields from DataContent
        if getattr(message, "type", None) == "data":
            data = getattr(message, "data", {}) or {}
            if "target_marketplace" in data:
                state_data.target_marketplace = str(data["target_marketplace"])
            if "user_hints" in data:
                state_data.user_hints = str(data["user_hints"])

        # Create tracing span
        if not state_data.current_span:
            state_data.current_span = await adk.tracing.start_span(
                trace_id=task.id,
                name="User Input",
                input={
                    "task_id": task.id,
                    "message": text_content[:200],
                    "has_image": bool(image_base64 or image_url),
                },
            )

        # Echo user message
        if image_base64:
            await adk.messages.create(
                task_id=task.id,
                content=DataContent(
                    author="user",
                    data={
                        "image_base64": image_base64,
                        "image_name": "Uploaded image",
                        "message": text_content or None,
                    },
                ),
                trace_id=task.id,
                parent_span_id=state_data.current_span.id if state_data.current_span else None,
            )
        elif text_content:
            await adk.messages.create(
                task_id=task.id,
                content=TextContent(author="user", content=text_content),
                trace_id=task.id,
                parent_span_id=state_data.current_span.id if state_data.current_span else None,
            )

        # Store in conversation history
        if text_content:
            state_data.conversation_history.append({
                "role": "user",
                "content": text_content,
            })

        # Unblock the waiting workflow
        state_data.waiting_for_user_input = False

    @override
    @workflow.run
    async def on_task_create(self, params: CreateTaskParams) -> None:
        """Initialize and run the NovaSell Dubizzle selling workflow."""
        task = params.task

        # Access control
        user_email = None
        if task.task_metadata:
            user_email = task.task_metadata.get("user_email")

        if ALLOWED_EMAILS and user_email not in ALLOWED_EMAILS:
            logger.warning(f"Access denied for: {user_email}")
            await adk.messages.create(
                task_id=task.id,
                content=TextContent(
                    author="agent",
                    content=(
                        "# Access Denied\n\n"
                        "You do not have permission to use the NovaSell agent.\n"
                        "Contact the administrator for access."
                    ),
                ),
                trace_id=task.id,
            )
            return

        # Initialize state
        self.state_machine.set_task_id(task.id)
        state_data = self.state_machine.get_state_machine_data()
        state_data.task_id = task.id

        # Check for initial params
        if params.params:
            for key in ("image_base64", "image_url", "user_hints", "target_marketplace"):
                if key in params.params:
                    setattr(state_data, key, str(params.params[key]))

        logger.info(
            f"Starting NovaSell workflow: task={task.id}, "
            f"has_image={bool(state_data.image_base64 or state_data.image_url)}"
        )

        # Welcome message
        instructions = (
            "### 🛒 Welcome to NovaSell — Your Dubizzle AI Sales Agent\n\n"
            "I'm your autonomous AI selling assistant powered by **AWS Nova**. "
            "Send me a photo and I'll handle the entire selling process on **Dubizzle**:\n\n"
            "1. 🔍 **Detect** what you're selling (Nova Lite)\n"
            "2. 💰 **Price** it based on Dubai market data (Nova Pro)\n"
            "3. 📝 **Generate** a compelling Dubizzle listing (Nova Pro)\n"
            "4. 🚀 **Publish** to Dubizzle automatically (Nova Act)\n"
            "5. 💬 **Manage** buyer inquiries & negotiations (Nova Pro)\n"
            "6. 📞 **Handle** phone calls from buyers (Nova Sonic)\n"
            "7. 🗓️ **Schedule** pickups and viewings\n\n"
        )

        if state_data.image_base64 or state_data.image_url:
            instructions += "📸 Image received! Starting analysis..."
        else:
            instructions += (
                "**To get started**, click the 📎 attachment button "
                "to upload a photo of the item you'd like to sell on Dubizzle."
            )

        await adk.messages.create(
            task_id=task.id,
            content=TextContent(author="agent", content=instructions),
            trace_id=task.id,
        )

        try:
            await self.state_machine.run()
        except asyncio.CancelledError as error:
            logger.warning(f"Task canceled: {task.id}")
            raise error
        except Exception as error:
            logger.error(f"Workflow error for task {task.id}: {str(error)}")
            try:
                await adk.messages.create(
                    task_id=task.id,
                    content=TextContent(
                        author="agent",
                        content=f"### ❌ Error\n\nNovaSell encountered an error: {str(error)}",
                    ),
                    trace_id=task.id,
                )
            except Exception as msg_error:
                logger.error(f"Failed to send error message: {str(msg_error)}")

            state_data.error_message = str(error)
            await self.state_machine.transition(NovaSellState.FAILED)
            raise error
