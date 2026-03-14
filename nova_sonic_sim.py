#!/usr/bin/env python3
"""
Nova Sonic 2 Simulation Script
================================
Demonstrates a full real-time speech-to-speech turn with Amazon Nova Sonic.

Flow:
    Text question  ──[Polly TTS]──▶  PCM audio (16kHz 16-bit mono)
                                           │
                         invoke_model_with_bidirectional_stream
                                           │
    Response audio ◀──[Nova Sonic]──  text + audio chunks

Requirements:
    pip install "boto3>=1.35.0"

AWS permissions needed:
    bedrock:InvokeModelWithBidirectionalStream  (model: amazon.nova-sonic-v1:0)
    polly:SynthesizeSpeech                       (for TTS input generation)

Run:
    AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... AWS_REGION=us-east-1 python nova_sonic_sim.py
    -- or --
    export AWS_PROFILE=your-profile && python nova_sonic_sim.py
"""
from __future__ import annotations

import asyncio
import base64
import json
import math
import os
import struct
import sys
import uuid

import boto3

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

MODEL_ID = "amazon.nova-sonic-v1:0"
REGION   = os.getenv("AWS_REGION", "us-east-1")

SYSTEM_PROMPT = (
    "You are a friendly sales agent handling a phone call from a buyer on Shozon marketplace. "
    "The seller listed an iPhone 13 Pro Max 256GB in excellent condition for 2,800 AED. "
    "Minimum price you can accept is 2,500 AED. "
    "Keep every reply under 3 sentences. Speak naturally as if on a phone call."
)

# The buyer's question we'll synthesize and send
BUYER_QUESTION = (
    "Hi, I saw your iPhone listing. What is the condition? Can you do 2,400 AED?"
)

OUTPUT_AUDIO_PATH = "/tmp/nova_sonic_response.raw"  # 24kHz 16-bit mono PCM


# ─────────────────────────────────────────────────────────────────────────────
# Step 1 — Generate test audio via Polly (or fallback to synthetic tone)
# ─────────────────────────────────────────────────────────────────────────────

def generate_input_audio(text: str) -> bytes:
    """Return raw PCM bytes (16-bit signed, 16 kHz, mono) for the given text.

    Tries Amazon Polly first. Falls back to a 440 Hz sine wave if Polly
    is unavailable so the script still exercises the Nova Sonic path.
    """
    try:
        polly = boto3.client("polly", region_name=REGION)
        resp  = polly.synthesize_speech(
            Text=text,
            OutputFormat="pcm",
            VoiceId="Joanna",
            SampleRate="16000",
        )
        pcm = resp["AudioStream"].read()
        print(f"[Polly]  Generated {len(pcm):,} bytes  ({len(pcm)/32000:.2f}s)")
        return pcm
    except Exception as exc:
        print(f"[Polly]  Unavailable ({exc}) — using synthetic 440 Hz tone")
        sample_rate, duration = 16000, 2.5
        n = int(sample_rate * duration)
        samples = [int(32767 * 0.25 * math.sin(2 * math.pi * 440 * i / sample_rate)) for i in range(n)]
        return struct.pack(f"<{n}h", *samples)


# ─────────────────────────────────────────────────────────────────────────────
# Step 2 — Nova Sonic bidirectional stream session
# ─────────────────────────────────────────────────────────────────────────────

def run_nova_sonic(system_prompt: str, audio_pcm: bytes) -> str:
    """Send one turn to Nova Sonic and return the full response text.

    Event sequence sent to Nova Sonic
    ──────────────────────────────────
    sessionStart         ← inference config
    promptStart          ← voice + text output config
    contentBlockStart 0  ← SYSTEM / TEXT
    contentBlockDelta 0  ← system prompt text
    contentBlockStop  0
    contentBlockStart 1  ← USER / AUDIO
    contentBlockDelta 1  ← audio chunks (base64 PCM, 1 KB each)
      ...
    contentBlockStop  1
    promptStop
    sessionEnd
    """
    client      = boto3.client("bedrock-runtime", region_name=REGION)
    prompt_name = str(uuid.uuid4())
    audio_b64   = base64.b64encode(audio_pcm).decode()

    print(f"\n[Session] prompt_name = {prompt_name[:12]}…")
    print(f"[Session] audio input = {len(audio_pcm):,} bytes")

    # ── Build the ordered list of input events ──────────────────────────────
    def _event(payload: dict) -> dict:
        """Wrap a Nova Sonic event as a Bedrock EventStream chunk."""
        return {"chunk": {"bytes": json.dumps({"event": payload}).encode()}}

    def input_stream():
        # 1. Session start
        yield _event({"sessionStart": {
            "inferenceConfiguration": {"maxTokens": 1024, "topP": 0.9, "temperature": 0.7}
        }})

        # 2. Prompt start
        yield _event({"promptStart": {
            "promptName": prompt_name,
            "textOutputConfiguration": {"mediaType": "text/plain"},
            "audioOutputConfiguration": {
                "mediaType":       "audio/lpcm",
                "sampleRateHertz": 24000,
                "sampleSizeBits":  16,
                "channelCount":    1,
                "voiceId":         "tiffany",   # options: matthew, tiffany, amy …
                "encoding":        "base64",
                "audioType":       "SPEECH",
            },
        }})

        # 3. System prompt block
        yield _event({"contentBlockStart": {
            "promptName": prompt_name, "contentBlockIndex": 0,
            "role": "SYSTEM", "type": "TEXT",
        }})
        yield _event({"contentBlockDelta": {
            "promptName": prompt_name, "contentBlockIndex": 0,
            "delta": {"text": system_prompt},
        }})
        yield _event({"contentBlockStop": {
            "promptName": prompt_name, "contentBlockIndex": 0,
        }})

        # 4. User audio block
        yield _event({"contentBlockStart": {
            "promptName": prompt_name, "contentBlockIndex": 1,
            "role": "USER", "type": "AUDIO",
        }})

        chunk_size, sent = 1024, 0
        for i in range(0, len(audio_b64), chunk_size):
            yield _event({"contentBlockDelta": {
                "promptName": prompt_name, "contentBlockIndex": 1,
                "delta": {"audioChunk": audio_b64[i:i + chunk_size]},
            }})
            sent += 1
        print(f"[Stream]  {sent} audio chunks sent")

        yield _event({"contentBlockStop": {
            "promptName": prompt_name, "contentBlockIndex": 1,
        }})

        # 5. End prompt + session
        yield _event({"promptStop":  {"promptName": prompt_name}})
        yield _event({"sessionEnd":  {}})

    # ── Invoke model ─────────────────────────────────────────────────────────
    response = client.invoke_model_with_bidirectional_stream(
        modelId=MODEL_ID,
        body=input_stream(),
    )

    # ── Process output ────────────────────────────────────────────────────────
    print("\n[Nova Sonic] ── Response ──\n")
    full_text    = ""
    audio_chunks = []

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
                sys.stdout.write(delta["text"])
                sys.stdout.flush()
                full_text += delta["text"]
            elif "audioChunk" in delta:
                audio_chunks.append(base64.b64decode(delta["audioChunk"]))

        elif "contentBlockStop" in evt:
            idx = evt["contentBlockStop"].get("contentBlockIndex", "?")
            print(f"\n  [block {idx} done]")

        elif "promptStop" in evt:
            print("[prompt done]")

        elif "sessionEnd" in evt:
            print("[session ended]")
            break

    # ── Save response audio ───────────────────────────────────────────────────
    if audio_chunks:
        total = b"".join(audio_chunks)
        with open(OUTPUT_AUDIO_PATH, "wb") as f:
            f.write(total)
        secs = len(total) / (24000 * 2)
        print(f"\n[Audio]  Response saved: {OUTPUT_AUDIO_PATH}  ({secs:.1f}s, {len(total):,} bytes)")
        print(f"[Audio]  Play:  ffplay -f s16le -ar 24000 -ac 1 {OUTPUT_AUDIO_PATH}")
        print(f"[Audio]  Convert to WAV:  ffmpeg -f s16le -ar 24000 -ac 1 -i {OUTPUT_AUDIO_PATH} response.wav")
    else:
        print("\n[Audio]  No audio received (check model access / region)")

    return full_text


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print(" Nova Sonic 2  — Simulation")
    print("=" * 60)
    print(f" Region : {REGION}")
    print(f" Model  : {MODEL_ID}")
    print(f" Buyer  : {BUYER_QUESTION}")
    print("=" * 60)

    # Step 1: generate input audio
    audio = generate_input_audio(BUYER_QUESTION)

    # Step 2: run one Nova Sonic turn
    try:
        text = run_nova_sonic(SYSTEM_PROMPT, audio)
        print(f"\n\n[Result] Full text response:\n{text}")
    except client_error := Exception:
        # Common errors:
        #   AccessDeniedException  → enable Nova Sonic in Bedrock console
        #   ValidationException    → wrong event format or model not available in region
        #   ResourceNotFoundException → model ID typo
        print(f"\n[Error] {client_error}")
        print("\nTroubleshooting:")
        print("  1. Go to AWS Console → Bedrock → Model access → enable 'Nova Sonic'")
        print("  2. Make sure region is us-east-1 (Nova Sonic not in all regions)")
        print("  3. Check IAM has bedrock:InvokeModelWithBidirectionalStream permission")
        raise
