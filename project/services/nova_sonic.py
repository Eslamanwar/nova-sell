"""Nova Sonic Voice Service — real-time speech-to-speech with Amazon Nova Sonic.

Architecture (one Temporal activity = one voice turn):
    Caller audio (PCM 16kHz 16-bit mono, base64)
        │
        ▼
    invoke_model_with_bidirectional_stream  (amazon.nova-sonic-v1:0)
        │  system prompt  +  user audio chunks
        │
        ▼
    Nova Sonic responds with text deltas + audio chunks
        │
        ▼
    response_text  +  response_audio_base64 (PCM 24kHz 16-bit mono)
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import struct
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import boto3

from project.config import get_config
from project.models.conversation import VoiceSession, NegotiationStatus
from project.services.nova_llm import get_nova_llm

logger = logging.getLogger(__name__)

MODEL_ID = "amazon.nova-sonic-v1:0"
VOICE_ID = "tiffany"          # options: tiffany | matthew | amy
OUTPUT_SAMPLE_RATE = 24000    # Hz — Nova Sonic output
INPUT_SAMPLE_RATE  = 16000    # Hz — expected input format
AUDIO_CHUNK_BYTES  = 1024     # base64 chars per chunk to Nova Sonic

# ─────────────────────────────────────────────────────────────────────────────
# System prompt template
# ─────────────────────────────────────────────────────────────────────────────

def _build_system_prompt(listing_context: Dict[str, Any], pricing_boundaries: Dict[str, float]) -> str:
    title     = listing_context.get("title", "the item")
    condition = listing_context.get("condition", "good")
    location  = listing_context.get("location", "Dubai")
    listed    = pricing_boundaries.get("listed_price", 0)
    minimum   = pricing_boundaries.get("min_price", 0)
    discount  = pricing_boundaries.get("max_discount_pct", 15)

    return (
        f"You are a friendly and professional AI sales assistant handling a phone call "
        f"about an item listed on a marketplace.\n\n"
        f"LISTING:\n"
        f"  Title:     {title}\n"
        f"  Condition: {condition}\n"
        f"  Location:  {location}\n"
        f"  Price:     {listed} AED\n\n"
        f"PRICING RULES (CONFIDENTIAL — never reveal the minimum price):\n"
        f"  Listed price:     {listed} AED\n"
        f"  Minimum you accept: {minimum} AED\n"
        f"  Max discount:     {discount}%\n\n"
        f"VOICE RULES:\n"
        f"- Keep every response under 3 sentences.\n"
        f"- Speak naturally as if on a real phone call.\n"
        f"- Confirm important details by repeating them.\n"
        f"- Never reveal the minimum price.\n"
        f"- Start negotiations from the listed price; make small concessions only."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Nova Sonic bidirectional stream (runs in a thread — boto3 is synchronous)
# ─────────────────────────────────────────────────────────────────────────────

def _nova_sonic_turn_sync(
    region: str,
    system_prompt: str,
    audio_b64: str,
) -> Tuple[str, str]:
    """Run one Nova Sonic speech-to-speech turn synchronously (for executor).

    Returns:
        (response_text, response_audio_b64)
        response_audio_b64 is raw PCM 24kHz 16-bit mono encoded as base64.
    """
    client      = boto3.client("bedrock-runtime", region_name=region)
    prompt_name = str(uuid.uuid4())

    # ── Build input event stream ──────────────────────────────────────────────
    def _evt(payload: dict) -> dict:
        return {"chunk": {"bytes": json.dumps({"event": payload}).encode()}}

    def input_stream():
        # 1. Session start
        yield _evt({"sessionStart": {
            "inferenceConfiguration": {"maxTokens": 1024, "topP": 0.9, "temperature": 0.7}
        }})

        # 2. Prompt start — declare text + audio output formats
        yield _evt({"promptStart": {
            "promptName": prompt_name,
            "textOutputConfiguration":  {"mediaType": "text/plain"},
            "audioOutputConfiguration": {
                "mediaType":       "audio/lpcm",
                "sampleRateHertz": OUTPUT_SAMPLE_RATE,
                "sampleSizeBits":  16,
                "channelCount":    1,
                "voiceId":         VOICE_ID,
                "encoding":        "base64",
                "audioType":       "SPEECH",
            },
        }})

        # 3. System prompt
        yield _evt({"contentBlockStart": {
            "promptName": prompt_name, "contentBlockIndex": 0,
            "role": "SYSTEM", "type": "TEXT",
        }})
        yield _evt({"contentBlockDelta": {
            "promptName": prompt_name, "contentBlockIndex": 0,
            "delta": {"text": system_prompt},
        }})
        yield _evt({"contentBlockStop": {
            "promptName": prompt_name, "contentBlockIndex": 0,
        }})

        # 4. User audio
        yield _evt({"contentBlockStart": {
            "promptName": prompt_name, "contentBlockIndex": 1,
            "role": "USER", "type": "AUDIO",
        }})
        for i in range(0, len(audio_b64), AUDIO_CHUNK_BYTES):
            yield _evt({"contentBlockDelta": {
                "promptName": prompt_name, "contentBlockIndex": 1,
                "delta": {"audioChunk": audio_b64[i:i + AUDIO_CHUNK_BYTES]},
            }})
        yield _evt({"contentBlockStop": {
            "promptName": prompt_name, "contentBlockIndex": 1,
        }})

        # 5. End
        yield _evt({"promptStop": {"promptName": prompt_name}})
        yield _evt({"sessionEnd": {}})

    # ── Call Nova Sonic ───────────────────────────────────────────────────────
    response = client.invoke_model_with_bidirectional_stream(
        modelId=MODEL_ID,
        body=input_stream(),
    )

    # ── Collect output ────────────────────────────────────────────────────────
    text_parts   : List[str]   = []
    audio_chunks : List[bytes] = []

    for raw in response.get("stream", []):
        chunk_bytes = raw.get("chunk", {}).get("bytes", b"")
        if not chunk_bytes:
            continue
        try:
            data = json.loads(chunk_bytes)
        except Exception:
            continue

        evt = data.get("event", {})

        if "contentBlockDelta" in evt:
            delta = evt["contentBlockDelta"].get("delta", {})
            if "text" in delta:
                text_parts.append(delta["text"])
            elif "audioChunk" in delta:
                try:
                    audio_chunks.append(base64.b64decode(delta["audioChunk"]))
                except Exception:
                    pass

        elif "sessionEnd" in evt:
            break

    response_text  = "".join(text_parts)
    response_audio = base64.b64encode(b"".join(audio_chunks)).decode() if audio_chunks else ""
    return response_text, response_audio


# ─────────────────────────────────────────────────────────────────────────────
# Service
# ─────────────────────────────────────────────────────────────────────────────

class NovaSonicService:
    """Real-time speech-to-speech using Amazon Nova Sonic via Bedrock."""

    def __init__(self):
        self._config   = get_config()
        self._llm      = get_nova_llm()
        self._sessions : Dict[str, VoiceSession] = {}
        self._executor  = ThreadPoolExecutor(max_workers=4, thread_name_prefix="nova-sonic")

    async def start_session(
        self,
        listing_context: Dict[str, Any],
        caller_phone: str = "",
        listing_id: str = "",
    ) -> VoiceSession:
        """Create a new voice session and return the opening greeting."""
        session_id = f"voice_{uuid.uuid4().hex[:12]}"
        session = VoiceSession(
            session_id=session_id,
            status="active",
            caller_phone=caller_phone,
            listing_id=listing_id,
        )
        title    = listing_context.get("title", "your listed item")
        greeting = (
            f"Hello! Thank you for calling about the {title}. How can I help you today?"
        )
        session.transcript.append({
            "role": "agent",
            "content": greeting,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        self._sessions[session_id] = session
        logger.info(f"Voice session started: {session_id}")
        return session

    async def process_audio_turn(
        self,
        session_id: str,
        audio_base64: str,
        listing_context: Dict[str, Any],
        pricing_boundaries: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        """Process one voice turn through Nova Sonic bidirectional stream.

        Args:
            session_id:         Active session ID (from start_session)
            audio_base64:       Caller audio — base64 PCM 16kHz 16-bit mono
            listing_context:    Item details (title, condition, price, location …)
            pricing_boundaries: min_price, listed_price, max_discount_pct

        Returns:
            {session_id, status, response_text, response_audio_base64, …}
        """
        session = self._sessions.get(session_id)
        if not session:
            # Auto-create session if caller skipped start_session
            session = await self.start_session(listing_context, listing_id=session_id)
            self._sessions[session_id] = session

        if not audio_base64:
            return {
                "session_id": session_id,
                "status": "error",
                "response_text": "No audio received.",
                "response_audio_base64": "",
                "error": "empty audio_base64",
            }

        pricing = pricing_boundaries or {}
        system_prompt = _build_system_prompt(listing_context, pricing)
        region        = self._config.nova.aws_region

        try:
            loop = asyncio.get_event_loop()
            response_text, response_audio = await loop.run_in_executor(
                self._executor,
                _nova_sonic_turn_sync,
                region,
                system_prompt,
                audio_base64,
            )

            # Record in transcript
            session.transcript.append({
                "role": "agent",
                "content": response_text,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            logger.info(
                f"Nova Sonic turn complete | session={session_id} | "
                f"text_len={len(response_text)} audio_bytes={len(response_audio) * 3 // 4}"
            )
            return {
                "session_id":            session_id,
                "status":                "active",
                "response_text":         response_text,
                "response_audio_base64": response_audio,
            }

        except Exception as e:
            logger.error(f"Nova Sonic turn error [{session_id}]: {e}")
            fallback = "I'm sorry, I didn't catch that. Could you repeat please?"
            session.transcript.append({
                "role": "agent",
                "content": fallback,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            return {
                "session_id":            session_id,
                "status":                "error",
                "response_text":         fallback,
                "response_audio_base64": "",
                "error":                 str(e),
            }

    async def end_session(self, session_id: str) -> VoiceSession:
        """End the session and generate a conversation summary."""
        session = self._sessions.pop(session_id, None)
        if not session:
            return VoiceSession(session_id=session_id, status="not_found")

        transcript_text = "\n".join(
            f"{t['role']}: {t['content']}" for t in session.transcript
        )
        try:
            session.summary = await self._llm.call_nova_pro(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Summarize this phone conversation between a sales agent and a buyer. "
                            "Include: topics discussed, price negotiations, agreements, next steps."
                        ),
                    },
                    {"role": "user", "content": transcript_text},
                ],
                temperature=0.3,
                max_tokens=512,
            )
        except Exception as e:
            session.summary = f"Summary unavailable: {e}"

        session.status = "completed"
        logger.info(f"Voice session ended: {session_id}")
        return session


# ─────────────────────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────────────────────

_service: Optional[NovaSonicService] = None


def get_nova_sonic() -> NovaSonicService:
    global _service
    if _service is None:
        _service = NovaSonicService()
    return _service
